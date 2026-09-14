"""Демо дня 6 проверяется как программа: требования задания и числа итога."""

import io
import json
import re
from contextlib import redirect_stdout
from decimal import Decimal

import pytest

import src.models.client as client_module
from src import main as demo_module
from src.enums import TransactionStatus

# Ожидания заданы здесь, а не взяты из src.main: иначе тест проверял бы сам себя.
LIFECYCLE_EVENTS = (
    "transaction_queued",
    "transaction_cancelled",
    "transaction_completed",
    "transaction_rejected",
)

FINISHED = (
    TransactionStatus.COMPLETED,
    TransactionStatus.FAILED,
    TransactionStatus.CANCELLED,
)


def run_demo(log_dir, export=True):
    """export=False пропускает рисование графиков там, где они не проверяются."""
    output = io.StringIO()

    with pytest.MonkeyPatch.context() as patch, redirect_stdout(output):
        patch.setattr(client_module, "PASSWORD_ITERATIONS", 1_000)

        if not export:
            patch.setattr(demo_module, "export_reports", lambda demo, report_dir: None)

        demo = demo_module.main(log_dir, log_dir / "reports")

    return demo, output.getvalue()


@pytest.fixture(scope="module")
def demo_run(tmp_path_factory):
    log_dir = tmp_path_factory.mktemp("demo-logs")
    demo, output = run_demo(log_dir)
    return demo, output, log_dir


@pytest.fixture(scope="module")
def demo(demo_run):
    return demo_run[0]


@pytest.fixture(scope="module")
def statistics(demo):
    return demo.bank.get_transaction_statistics()


class TestRequirements:
    def test_has_five_to_ten_clients(self, demo):
        assert 5 <= len(demo.bank.clients) <= 10

    def test_has_ten_to_fifteen_accounts(self, demo):
        assert 10 <= len(demo.bank.accounts) <= 15

    def test_runs_thirty_to_fifty_transactions(self, demo):
        assert 30 <= len(demo.transactions) + demo.refused_at_intake <= 50

    def test_some_transactions_are_rejected(self, statistics, demo):
        assert statistics["rejected"] > 0
        assert demo.refused_at_intake > 0

    def test_some_transactions_are_suspicious(self, statistics, demo):
        assert statistics["by_risk_level"]["medium"] > 0
        assert statistics["blocked_by_risk"] > 0
        assert demo.bank.get_suspicious_operations()

    def test_top_three_clients_are_ranked(self, demo):
        ranking = demo.bank.get_clients_ranking()[: demo_module.TOP_CLIENTS]

        assert len(ranking) == 3
        assert ranking[0][1] >= ranking[1][1] >= ranking[2][1]

    def test_spotlight_client_has_history(self, demo):
        history = demo.bank.get_client_history(demo_module.SPOTLIGHT_CLIENT_ID)

        assert {row["direction"] for row in history} == {"in", "out"}
        assert {row["status"] for row in history} == {"completed", "rejected"}


class TestSimulationOutcome:
    def test_queue_is_drained(self, demo):
        assert len(demo.queue) == 0

    def test_every_transaction_is_finished(self, demo):
        assert all(transaction.status in FINISHED for transaction in demo.transactions)

    def test_numbers_match_the_scenario(self, statistics, demo):
        assert len(demo.transactions) == 36
        assert demo.refused_at_intake == 2
        assert statistics["completed"] == 23
        assert statistics["rejected"] == 12
        assert statistics["blocked_by_risk"] == 2
        assert statistics["volume"] == {
            "rub": Decimal("340400.00"),
            "usd": Decimal("100.00"),
            "eur": Decimal("50.00"),
        }
        assert statistics["fees"]["rub"] == Decimal("50.00")

    def test_total_balance_matches_the_scenario(self, demo):
        assert demo.bank.get_total_balance() == Decimal("2110624.50")

    def test_console_events_match_the_demo_setting(self):
        assert set(demo_module.LIFECYCLE_EVENTS) == set(LIFECYCLE_EVENTS)

    def test_second_run_in_the_same_folder_starts_fresh_logs(self, tmp_path):
        run_demo(tmp_path, export=False)
        first = len((tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines())

        run_demo(tmp_path, export=False)
        second = len(
            (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        )

        assert second == first

    def test_demo_is_deterministic(self, demo, statistics, tmp_path):
        again, _ = run_demo(tmp_path, export=False)

        assert again.bank.get_transaction_statistics() == statistics
        assert again.bank.get_total_balance() == demo.bank.get_total_balance()


class TestOutput:
    @pytest.mark.parametrize(
        "title",
        ["1. ИНИЦИАЛИЗАЦИЯ", "2. СИМУЛЯЦИЯ", "3. СЦЕНАРИИ", "4. ОТЧЁТЫ", "5. ЭКСПОРТ"],
    )
    def test_prints_every_section(self, demo_run, title):
        assert title in demo_run[1]

    @pytest.mark.parametrize("event", LIFECYCLE_EVENTS)
    def test_console_shows_lifecycle_events(self, demo_run, event):
        assert event in demo_run[1]

    def test_console_hides_account_level_events(self, demo_run):
        assert "  INFO    deposit " not in demo_run[1]
        assert "  INFO    audit " not in demo_run[1]

    @pytest.mark.parametrize("event", LIFECYCLE_EVENTS)
    def test_log_file_holds_lifecycle_events(self, demo_run, event):
        text = (demo_run[2] / "demo.log").read_text(encoding="utf-8")

        assert re.search(rf"\| (INFO|WARNING) +\| {event} +\|", text)

    def test_audit_file_holds_every_kind_of_event(self, demo_run):
        lines = (demo_run[2] / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        events = {json.loads(line)["event"] for line in lines}

        assert events == {
            "account_opened",
            "transaction_assessed",
            "transaction_completed",
            "transaction_failed",
            "fraud_action",
        }


class TestExport:
    """День 7: отчёты по банку, рискам и клиенту в трёх форматах и шесть графиков."""

    EXPECTED_FILES = {
        *(
            f"{kind}_report.{suffix}"
            for kind in ("bank", "risk", "client")
            for suffix in ("txt", "json", "csv")
        ),
        "charts/bank_balance_by_currency.png",
        "charts/bank_top_clients.png",
        "charts/bank_transactions_by_type.png",
        "charts/risk_levels.png",
        "charts/risk_rules.png",
        f"charts/client_{demo_module.SPOTLIGHT_CLIENT_ID}_balance.png",
    }

    def test_writes_every_report_and_chart(self, demo_run):
        report_dir = demo_run[2] / "reports"

        written = {
            path.relative_to(report_dir).as_posix()
            for path in report_dir.rglob("*")
            if path.is_file()
        }

        assert written == self.EXPECTED_FILES

    def test_returns_the_written_paths(self, demo):
        assert len(demo.report_files) == len(self.EXPECTED_FILES)
        assert all(path.exists() for path in demo.report_files)

    def test_bank_json_matches_the_scenario(self, demo_run):
        data = json.loads(
            (demo_run[2] / "reports" / "bank_report.json").read_text(encoding="utf-8")
        )

        assert data["summary"]["transactions_completed"] == 23
        assert data["summary"]["total_balance_rub"] == "2110624.50"

    def test_client_csv_lists_the_history(self, demo):
        path = next(p for p in demo.report_files if p.name == "client_report.csv")

        lines = path.read_text(encoding="utf-8").splitlines()

        assert len(lines) - 1 == len(
            demo.bank.get_client_history(demo_module.SPOTLIGHT_CLIENT_ID)
        )
