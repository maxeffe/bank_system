import csv
import json
import re
import sys
from datetime import datetime, timedelta
from decimal import Decimal

import matplotlib
import pytest

from src.enums import Currency, TransactionType
from src.exceptions import AuditWriteError, InvalidOperationError
from src.models import Bank, Transaction, TransactionProcessor
from src.reporting import Report, ReportBuilder, charts
from src.services import ACCOUNT_OPENED_EVENT, AuditLog
from src.services.risk import COMPLETED_EVENT, FAILED_EVENT

DAY = datetime(2026, 9, 3, 12, 0)
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
UTF8_BOM = b"\xef\xbb\xbf"
MAX_BAR_PX = 24
MIN_PNG_BYTES = 1_000
BANK_CHARTS = {
    "bank_balance_by_currency.png",
    "bank_top_clients.png",
    "bank_transactions_by_type.png",
    "risk_levels.png",
    "risk_rules.png",
}


def at(moment):
    return lambda: moment


@pytest.fixture
def busy_bank(transfer_bank, source_account, target_account):
    """Банк с исполненными, отклонёнными и подозрительными операциями."""
    processor = TransactionProcessor(transfer_bank, time_provider=at(DAY))
    transfer_bank.open_account(2, "bank", balance=Decimal("50"), currency=Currency.USD)
    operations = [
        Transaction(
            TransactionType.TRANSFER,
            Decimal("500"),
            source_account_id=source_account.account_id,
            target_account_id=target_account.account_id,
        ),
        Transaction(
            TransactionType.DEPOSIT,
            Decimal("150000"),
            target_account_id=target_account.account_id,
        ),
        Transaction(
            TransactionType.WITHDRAWAL,
            Decimal("999999"),
            source_account_id=source_account.account_id,
        ),
    ]

    for transaction in operations:
        processor.process(transaction)

    return transfer_bank


@pytest.fixture
def builder(busy_bank):
    return ReportBuilder(busy_bank, time_provider=at(DAY))


class TestBalanceHistory:
    def test_opening_an_account_is_recorded_with_its_balance(self, transfer_bank):
        opened = transfer_bank.audit_log.filter(event=ACCOUNT_OPENED_EVENT)

        assert [record["details"]["balance"] for record in opened] == [
            Decimal("10000.00"),
            Decimal("1000.00"),
        ]

    def test_completed_operation_records_balances_of_both_accounts(
        self, busy_bank, source_account, target_account
    ):
        record = busy_bank.audit_log.filter(event=COMPLETED_EVENT)[0]

        assert record["details"]["balances"] == {
            source_account.account_id: Decimal("9500.00"),
            target_account.account_id: Decimal("1500.00"),
        }

    def test_failed_operation_records_no_balances(self, busy_bank):
        record = busy_bank.audit_log.filter(event=FAILED_EVENT)[0]

        assert record["details"]["balances"] == {}

    def test_history_goes_from_opening_through_every_change(
        self, busy_bank, target_account
    ):
        history = busy_bank.get_balance_history(2)

        assert [balance for _, balance in history[target_account.account_id]] == [
            Decimal("1000.00"),
            Decimal("1500.00"),
            Decimal("151500.00"),
        ]

    def test_every_client_account_has_a_series(self, busy_bank):
        assert len(busy_bank.get_balance_history(2)) == 2

    def test_unknown_client_is_an_error(self, busy_bank):
        with pytest.raises(InvalidOperationError):
            busy_bank.get_balance_history(99)

    def test_failed_audit_write_leaves_no_account(self, make_client):
        class BrokenAuditLog(AuditLog):
            def record(self, event, *args, **kwargs):
                if event == ACCOUNT_OPENED_EVENT:
                    raise AuditWriteError("Audit file is not writable")

                return super().record(event, *args, **kwargs)

        bank = Bank("Broken", time_provider=at(DAY), audit_log=BrokenAuditLog())
        client = bank.add_client(make_client())

        with pytest.raises(AuditWriteError):
            bank.open_account(1, "bank", balance=Decimal("100"))

        assert bank.accounts == {}
        assert bank.search_accounts(client_id=client.client_id) == []


class TestClientReport:
    def test_summary(self, builder, busy_bank):
        report = builder.client_report(2)

        assert report.kind == "client"
        assert report.generated_at == DAY
        assert report.summary["full_name"] == "Anna Ivanova"
        assert report.summary["accounts"] == 2
        assert report.summary["total_balance_rub"] == Decimal("156000.00")
        assert report.summary["risk_level"] == "medium"

    def test_history_is_the_main_table(self, builder, busy_bank):
        report = builder.client_report(2)

        assert report.main_table == "history"
        assert len(report.tables["history"]) == len(busy_bank.get_client_history(2))

    def test_accounts_table(self, builder):
        rows = builder.client_report(2).tables["accounts"]

        assert {row["currency"] for row in rows} == {Currency.RUB, Currency.USD}

    def test_unknown_client_is_an_error(self, builder):
        with pytest.raises(InvalidOperationError):
            builder.client_report(99)


class TestBankReport:
    def test_summary_matches_the_bank(self, builder, busy_bank):
        report = builder.bank_report()

        assert report.summary["clients"] == 2
        assert report.summary["accounts"] == 3
        assert report.summary["total_balance_rub"] == busy_bank.get_total_balance()
        assert report.summary["transactions_completed"] == 2
        assert report.summary["transactions_rejected"] == 1

    def test_accounts_are_the_main_table(self, builder):
        report = builder.bank_report()

        assert report.main_table == "accounts"
        assert len(report.tables["accounts"]) == 3

    def test_ranking_goes_from_richest(self, builder):
        ranking = builder.bank_report().tables["clients_ranking"]

        assert [row["place"] for row in ranking] == [1, 2]
        assert ranking[0]["balance"] >= ranking[1]["balance"]

    def test_balance_by_currency_adds_up_to_the_total(self, builder, busy_bank):
        rows = builder.bank_report().tables["balance_by_currency"]

        assert sum(row["balance_rub"] for row in rows) == busy_bank.get_total_balance()
        assert {row["currency"] for row in rows} == {"rub", "usd"}

    def test_closed_account_is_not_in_balance_by_currency(self, transfer_bank):
        closed = transfer_bank.open_account(1, "bank", currency=Currency.EUR)
        transfer_bank.close_account(closed.account_id)
        closed.refund(Decimal("50"))
        builder = ReportBuilder(transfer_bank, time_provider=at(DAY))

        rows = builder.bank_report().tables["balance_by_currency"]

        assert [row["currency"] for row in rows] == ["rub"]


class TestRiskReport:
    def test_levels_add_up_to_the_assessments(self, builder):
        report = builder.risk_report()

        assert (
            sum(row["count"] for row in report.tables["risk_levels"])
            == (report.summary["assessments"])
        )

    def test_suspicious_operations_are_the_main_table(self, builder, busy_bank):
        report = builder.risk_report()

        assert report.main_table == "suspicious_operations"
        assert len(report.tables["suspicious_operations"]) == len(
            busy_bank.get_suspicious_operations()
        )

    def test_counts_triggered_rules(self, builder):
        rules = {
            row["rule"]: row["count"] for row in builder.risk_report().tables["rules"]
        }

        assert rules["large_amount"] == 2
        assert rules["new_counterparty"] == 1

    def test_lists_every_client(self, builder):
        assert len(builder.risk_report().tables["clients"]) == 2


class TestFormats:
    def test_text_marks_an_empty_table(self, transfer_bank):
        report = ReportBuilder(transfer_bank, time_provider=at(DAY)).risk_report()

        assert "(нет данных)" in ReportBuilder.to_text(report)

    def test_text_export_writes_the_rendered_text(self, builder, tmp_path):
        report = builder.risk_report()

        path = builder.export_to_text(report, tmp_path / "risk.txt")

        assert path.read_text(encoding="utf-8") == builder.to_text(report)


class TestCharts:
    def test_bank_charts_draw_the_report_data(self, builder, figures, tmp_path):
        paths = builder.save_charts(tmp_path / "charts", client_id=2)

        assert {path.name for path in paths} == BANK_CHARTS | {"client_2_balance.png"}
        assert all(path.read_bytes().startswith(PNG_SIGNATURE) for path in paths)
        assert all(len(path.read_bytes()) > MIN_PNG_BYTES for path in paths)
        ranking = builder.bank_report().tables["clients_ranking"]
        bars = figures["bank_top_clients.png"].axes[0].patches
        assert [bar.get_width() for bar in bars] == [
            float(row["balance"]) for row in ranking
        ]
        assert len(figures["bank_balance_by_currency.png"].axes[0].patches) == 2

    def test_bank_without_data_gets_every_chart_untouched_settings(
        self, figures, tmp_path
    ):
        with matplotlib.rc_context():
            matplotlib.rcdefaults()
            before = {key: matplotlib.rcParams[key] for key in charts.STYLE}

            paths = ReportBuilder(Bank("Empty"), time_provider=at(DAY)).save_charts(
                tmp_path
            )

            after = {key: matplotlib.rcParams[key] for key in charts.STYLE}

        assert {path.name for path in paths} == BANK_CHARTS
        assert all(charts.EMPTY_TEXT in texts(figure) for figure in figures.values())
        assert after == before
        assert "matplotlib.pyplot" not in sys.modules

    def test_too_many_accounts_are_named_in_the_title(self, tmp_path, monkeypatch):
        titles = []
        real_figure = charts._figure
        monkeypatch.setattr(
            charts, "_figure", lambda title: titles.append(title) or real_figure(title)
        )
        series = {f"acc-{index}": [(DAY, Decimal("1"))] for index in range(10)}

        charts.save_balance_lines(tmp_path / "many.png", "Баланс", series)

        assert titles == ["Баланс (первые 8 счетов из 10)"]

    def test_money_uses_spaces_between_thousands(self):
        assert charts.money(Decimal("1234567.891")) == "1 234 567.89"


@pytest.fixture
def figures(monkeypatch):
    """Перехватывает Figure перед сохранением: проверяем, что нарисовано, а не байты."""
    saved = {}
    real_save = charts._save

    def spy(figure, path):
        saved[path.name if hasattr(path, "name") else str(path)] = figure
        return real_save(figure, path)

    monkeypatch.setattr(charts, "_save", spy)
    return saved


def read_csv(path):
    with path.open(encoding="utf-8-sig", newline="") as csv_file:
        return list(csv.reader(csv_file, delimiter=";"))


def texts(figure):
    return [text.get_text() for axes in figure.axes for text in axes.texts]


class TestFormatDetails:
    def test_text_has_title_summary_and_rows(self, builder, busy_bank, source_account):
        text = builder.to_text(builder.bank_report())

        assert "Отчёт по банку: Test Bank" in text
        assert re.search(r"^clients +2$", text, flags=re.MULTILINE)
        assert "--- accounts (3) ---" in text
        assert source_account.account_id in text

    def test_json_holds_the_whole_report(self, builder, tmp_path):
        report = builder.bank_report()

        path = builder.export_to_json(report, tmp_path / "out" / "bank.json")

        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["title"] == report.title
        assert set(data["tables"]) == set(report.tables)
        assert data["generated_at"] == "2026-09-03T12:00:00"
        assert data["tables"]["accounts"][0]["balance"] == "9500.00"

    def test_csv_opens_in_excel_with_bom_and_semicolons(self, builder, tmp_path):
        path = builder.export_to_csv(builder.bank_report(), tmp_path / "bank.csv")

        assert path.read_bytes().startswith(UTF8_BOM)
        assert read_csv(path)[0] == list(builder.bank_report().main_columns)

    def test_csv_cells_hold_the_values(self, builder, busy_bank, tmp_path):
        report = builder.client_report(2)

        rows = read_csv(builder.export_to_csv(report, tmp_path / "client.csv"))

        header, first = rows[0], dict(zip(rows[0], rows[1]))
        assert header == list(report.main_columns)
        assert len(rows) == len(report.tables["history"]) + 1
        assert first["time"] == "2026-09-03 12:00:00"
        assert first["type"] == "transfer"
        assert first["direction"] == "in"
        assert first["amount"] == "500.00"
        assert first["status"] == "completed"

    def test_csv_of_an_empty_table_still_has_a_header(self, transfer_bank, tmp_path):
        report = ReportBuilder(transfer_bank, time_provider=at(DAY)).risk_report()

        rows = read_csv(ReportBuilder.export_to_csv(report, tmp_path / "risk.csv"))

        assert rows == [list(report.main_columns)]

    def test_csv_escapes_text_that_looks_like_a_formula(self, tmp_path):
        report = Report(
            "bank",
            "t",
            DAY,
            {},
            {
                "rows": [
                    {
                        "name": '=HYPERLINK("x")',
                        "amount": Decimal("-5.00"),
                        "note": "-bad",
                    }
                ]
            },
            "rows",
        )

        rows = read_csv(ReportBuilder.export_to_csv(report, tmp_path / "x.csv"))

        assert rows[1] == ['\'=HYPERLINK("x")', "-5.00", "'-bad"]

    def test_seconds_are_kept_in_every_format(self, tmp_path):
        moment = datetime(2026, 9, 3, 12, 0, 5)
        report = Report("bank", "t", moment, {}, {"rows": [{"time": moment}]}, "rows")

        rows = read_csv(ReportBuilder.export_to_csv(report, tmp_path / "x.csv"))

        assert rows[1] == ["2026-09-03 12:00:05"]
        assert "2026-09-03 12:00:05" in ReportBuilder.to_text(report)

    @pytest.mark.parametrize(
        "export", ["export_to_text", "export_to_json", "export_to_csv"]
    )
    def test_unencodable_report_keeps_the_old_file(self, builder, tmp_path, export):
        path = tmp_path / "report.out"
        getattr(builder, export)(builder.bank_report(), path)
        before = path.read_bytes()
        bad = "bad \udcff"
        broken = Report("bank", bad, DAY, {}, {"rows": [{"name": bad}]}, "rows")

        with pytest.raises(InvalidOperationError):
            getattr(builder, export)(broken, path)

        assert path.read_bytes() == before


class TestReviewFixes:
    def test_rules_count_assessments_without_a_known_client(
        self, transfer_bank, target_account
    ):
        processor = TransactionProcessor(transfer_bank, time_provider=at(DAY))
        processor.process(
            Transaction(
                TransactionType.DEPOSIT, Decimal("150000"), target_account_id="ACC-404"
            )
        )
        processor.process(
            Transaction(
                TransactionType.TRANSFER,
                Decimal("10"),
                source_account_id="ACC-405",
                target_account_id=target_account.account_id,
            )
        )

        report = ReportBuilder(transfer_bank, time_provider=at(DAY)).risk_report()

        rules = {row["rule"]: row["count"] for row in report.tables["rules"]}
        assert report.summary["assessments"] == 2
        assert rules == {"large_amount": 1, "new_counterparty": 1}

    @pytest.mark.parametrize("currency", [Currency.RUB, Currency.USD])
    def test_balance_by_currency_matches_the_total_with_fractional_rates(
        self, bank, make_client, currency
    ):
        bank.add_client(make_client())
        for amount in ("0.01", "0.01", "0.03"):
            bank.open_account(1, "bank", balance=Decimal(amount), currency=Currency.CNY)
        bank.open_account(1, "bank", balance=Decimal("50"))
        bank.open_account(1, "bank", balance=Decimal("50"))

        rows = (
            ReportBuilder(bank, currency=currency)
            .bank_report()
            .tables["balance_by_currency"]
        )

        key = f"balance_{currency}"
        assert sum(row[key] for row in rows) == bank.get_total_balance(currency)

    def test_account_opening_uses_the_bank_clock(self, make_client):
        audit = AuditLog(time_provider=at(DAY + timedelta(days=30)))
        bank = Bank("Clocks", time_provider=at(DAY), audit_log=audit)
        bank.add_client(make_client())

        bank.open_account(1, "bank", balance=Decimal("100"))

        assert audit.filter(event=ACCOUNT_OPENED_EVENT)[0]["time"] == DAY


class TestChartContent:
    def test_dollar_signs_in_a_name_are_plain_text(
        self, bank, make_client, figures, tmp_path
    ):
        name = "Anna $\\foo$"
        bank.add_client(make_client(client_id=1, full_name=name))
        bank.open_account(1, "bank", balance=Decimal("100"))

        paths = ReportBuilder(bank).save_charts(tmp_path, client_id=1)

        axes = figures["bank_top_clients.png"].axes[0]
        assert name in [label.get_text() for label in axes.get_yticklabels()]
        assert all(path.stat().st_size > MIN_PNG_BYTES for path in paths)

    def test_top_clients_chart_keeps_ten_and_says_so(
        self, bank, make_client, figures, tmp_path
    ):
        for client_id in range(1, 13):
            bank.add_client(
                make_client(client_id=client_id, full_name=f"Client {client_id}")
            )
            bank.open_account(client_id, "bank", balance=Decimal(client_id))

        ReportBuilder(bank).save_charts(tmp_path)

        axes = figures["bank_top_clients.png"].axes[0]
        assert len(axes.patches) == 10
        assert "топ-10 из 12" in axes.get_title(loc="left")

    @pytest.mark.parametrize("count", [1, 3, 8])
    def test_bars_are_never_thicker_than_24px(self, figures, tmp_path, count):
        charts.save_bars(
            tmp_path / "bars.png", "t", list(range(count)), [Decimal("5")] * count
        )

        axes = figures["bars.png"].axes[0]
        for patch in axes.patches:
            bottom = axes.transData.transform((0, patch.get_y()))[1]
            top = axes.transData.transform((0, patch.get_y() + patch.get_height()))[1]
            assert abs(top - bottom) <= MAX_BAR_PX + 1

    def test_negative_share_is_named_in_the_pie_title(self, figures, tmp_path):
        charts.save_pie(
            tmp_path / "pie.png",
            "Доли",
            ["RUB", "EUR"],
            [Decimal("1000"), Decimal("-500")],
        )

        axes = figures["pie.png"].axes[0]
        assert "без отрицательных: EUR -500.00" in axes.get_title(loc="left")
        assert len(axes.patches) == 1

    def test_small_pie_slices_get_no_percent_label(self, figures, tmp_path):
        charts.save_risk_levels(
            tmp_path / "risk.png", "t", {"low": 1000, "medium": 3, "high": 2}
        )

        labels = [text for text in texts(figures["risk.png"]) if text.endswith("%")]
        assert labels == ["99.5%"]

    def test_single_moment_gets_a_readable_time_axis(self, figures, tmp_path):
        series = {"a": [(DAY, Decimal("100"))], "b": [(DAY, Decimal("200"))]}

        charts.save_balance_lines(tmp_path / "line.png", "t", series)

        low, high = figures["line.png"].axes[0].get_xlim()
        assert (high - low) * 24 == pytest.approx(2)

    def test_tiny_value_range_has_no_negative_zero(self, figures, tmp_path):
        charts.save_bars(
            tmp_path / "zero.png", "t", ["a", "b"], [Decimal("0"), Decimal("0")]
        )

        axes = figures["zero.png"].axes[0]
        assert axes.get_xlim() == (0, 1)
        assert "-0" not in [label.get_text() for label in axes.get_xticklabels()]

    def test_only_lonely_line_ends_are_labeled(self, figures, tmp_path):
        later = DAY + timedelta(hours=1)
        series = {
            "a": [(DAY, Decimal("0")), (later, Decimal("5000"))],
            "b": [(DAY, Decimal("0")), (later, Decimal("5010"))],
            "c": [(DAY, Decimal("0")), (later, Decimal("-19000"))],
        }

        charts.save_balance_lines(tmp_path / "ends.png", "t", series)

        assert texts(figures["ends.png"]) == ["-19 000.00"]

    def test_single_bar_leaves_room_for_its_label(self, figures, tmp_path):
        charts.save_bars(tmp_path / "one.png", "t", ["rule"], [3])

        assert figures["one.png"].axes[0].get_xlim()[1] > 3

    @pytest.mark.parametrize("value", [-0.0, -1e-12, -0.4])
    def test_axis_label_never_shows_negative_zero(self, value):
        assert charts._whole(value) == "0"
