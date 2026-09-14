from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from src.enums import AuditSeverity, Currency, RiskLevel, TransactionType
from src.models import CurrencyConverter, Transaction
from src.services import AuditLog, AuditReporter, RiskAnalyzer
from src.exceptions import RiskBlockedError

DAY = datetime(2026, 9, 4, 14, 0)
NIGHT = datetime(2026, 9, 4, 2, 0)


def at(moment):
    return lambda: moment


def make_transfer(amount="100", target="acc-2"):
    return Transaction(
        TransactionType.TRANSFER,
        Decimal(amount),
        currency=Currency.RUB,
        source_account_id="acc-1",
        target_account_id=target,
        time_provider=at(DAY),
    )


def make_error(error="InsufficientFundsError", kind="transfer", transaction_id="tx-1"):
    return {
        "transaction_id": transaction_id,
        "transaction_type": kind,
        "attempt": 1,
        "error": error,
        "message": "boom",
        "time": DAY.isoformat(timespec="seconds"),
    }


@pytest.fixture
def audit():
    return AuditLog(time_provider=at(DAY))


@pytest.fixture
def analyzer(audit):
    return RiskAnalyzer(audit, CurrencyConverter(), time_provider=at(DAY))


@pytest.fixture
def reporter(audit):
    return AuditReporter(audit)


class TestSuspiciousOperations:
    def test_empty_log_has_nothing_suspicious(self, reporter):
        assert reporter.suspicious_operations() == []

    def test_low_risk_operations_are_not_listed(self, analyzer, reporter):
        analyzer.evaluate(make_transfer(target="acc-2"), client_id=1)

        assert reporter.suspicious_operations() == []

    def test_medium_risk_operation_is_listed(self, analyzer, reporter):
        analyzer.evaluate(make_transfer("200000"), client_id=1)

        assert len(reporter.suspicious_operations()) == 1

    def test_high_risk_operation_is_listed(self, analyzer, reporter):
        analyzer.evaluate(make_transfer("200000"), client_id=1, now=NIGHT)

        found = reporter.suspicious_operations()
        assert found[0]["severity"] is AuditSeverity.CRITICAL

    def test_can_be_narrowed_to_one_client(self, analyzer, reporter):
        analyzer.evaluate(make_transfer("200000"), client_id=1)
        analyzer.evaluate(make_transfer("200000"), client_id=2)

        found = reporter.suspicious_operations(client_id=2)

        assert [record["client_id"] for record in found] == [2]


class TestClientRiskProfile:
    def test_unknown_client_has_an_empty_profile(self, reporter):
        profile = reporter.client_risk_profile(99)

        assert profile == {
            "client_id": 99,
            "operations": 0,
            "level": RiskLevel.LOW,
            "blocked": 0,
            "by_level": {"low": 0, "medium": 0, "high": 0},
            "reasons": {},
            "total_score": 0,
        }

    def test_counts_only_the_given_client(self, analyzer, reporter):
        analyzer.evaluate(make_transfer(), client_id=1)
        analyzer.evaluate(make_transfer(), client_id=2)

        assert reporter.client_risk_profile(1)["operations"] == 1

    def test_level_is_the_worst_one_seen(self, analyzer, reporter):
        analyzer.evaluate(make_transfer(target="acc-2"), client_id=1)
        analyzer.evaluate(
            make_transfer("200000", target="acc-9"), client_id=1, now=NIGHT
        )
        analyzer.evaluate(make_transfer(target="acc-2"), client_id=1)

        assert reporter.client_risk_profile(1)["level"] is RiskLevel.HIGH

    def test_counts_blocked_operations(self, analyzer, reporter):
        analyzer.evaluate(make_transfer("200000"), client_id=1, now=NIGHT)
        analyzer.evaluate(make_transfer("300000", target="acc-3"), client_id=1)

        assert reporter.client_risk_profile(1)["blocked"] == 1

    def test_groups_operations_by_level(self, analyzer, reporter):
        analyzer.evaluate(make_transfer(target="acc-2"), client_id=1)
        analyzer.evaluate(
            make_transfer("200000", target="acc-9"), client_id=1, now=NIGHT
        )

        assert reporter.client_risk_profile(1)["by_level"] == {
            "low": 1,
            "medium": 0,
            "high": 1,
        }

    def test_counts_every_triggered_rule(self, analyzer, reporter):
        analyzer.evaluate(make_transfer("200000"), client_id=1)
        analyzer.evaluate(make_transfer("300000", target="acc-3"), client_id=1)

        reasons = reporter.client_risk_profile(1)["reasons"]

        assert reasons["large_amount"] == 2
        assert reasons["new_counterparty"] == 2

    def test_sums_the_scores(self, analyzer, reporter):
        analyzer.evaluate(make_transfer("200000"), client_id=1)

        assert reporter.client_risk_profile(1)["total_score"] == 3


class TestErrorStatistics:
    def test_empty_journal(self, reporter):
        assert reporter.error_statistics([]) == {
            "total": 0,
            "by_error": {},
            "by_transaction_type": {},
            "affected_transactions": 0,
        }

    def test_counts_errors_by_type(self, reporter):
        errors = [
            make_error("InsufficientFundsError"),
            make_error("InsufficientFundsError", transaction_id="tx-2"),
            make_error("AccountFrozenError", transaction_id="tx-3"),
        ]

        statistics = reporter.error_statistics(errors)

        assert statistics["total"] == 3
        assert statistics["by_error"] == {
            "InsufficientFundsError": 2,
            "AccountFrozenError": 1,
        }

    def test_counts_errors_by_transaction_type(self, reporter):
        errors = [make_error(kind="transfer"), make_error(kind="deposit")]

        statistics = reporter.error_statistics(errors)

        assert statistics["by_transaction_type"] == {"transfer": 1, "deposit": 1}

    def test_retries_of_one_transaction_count_once(self, reporter):
        errors = [make_error(transaction_id="tx-1") for _ in range(3)]

        statistics = reporter.error_statistics(errors)

        assert statistics["total"] == 3
        assert statistics["affected_transactions"] == 1

    def test_most_frequent_error_goes_first(self, reporter):
        errors = [
            make_error("AccountFrozenError"),
            make_error("InsufficientFundsError"),
            make_error("InsufficientFundsError"),
        ]

        statistics = reporter.error_statistics(errors)

        assert next(iter(statistics["by_error"])) == "InsufficientFundsError"

    def test_accepts_any_iterable(self, reporter):
        statistics = reporter.error_statistics(iter([make_error()]))

        assert statistics["total"] == 1


def finish(
    analyzer, transaction, client_id, completed=True, error=None, now=DAY, fee=None
):
    analyzer.evaluate(transaction, client_id=client_id, now=now)
    transaction.mark_processing(now=now)

    if fee is not None:
        transaction.apply_fee(Decimal(fee))

    if completed:
        transaction.mark_completed(now=now)
    else:
        transaction.mark_failed("boom", now=now)

    analyzer.record_outcome(transaction, client_id, error)
    return transaction


def make_deposit(amount="100", target="acc-1"):
    return Transaction(
        TransactionType.DEPOSIT,
        Decimal(amount),
        target_account_id=target,
        time_provider=at(DAY),
    )


class TestClientHistory:
    def test_unknown_client_has_no_history(self, reporter):
        assert reporter.client_history(99, ["acc-9"]) == []

    def test_outgoing_completed_transfer(self, analyzer, reporter):
        transaction = finish(analyzer, make_transfer("300"), client_id=1)

        rows = reporter.client_history(1, ["acc-1"])

        assert rows == [
            {
                "time": DAY,
                "transaction_id": transaction.transaction_id,
                "type": "transfer",
                "direction": "out",
                "amount": Decimal("300.00"),
                "fee": Decimal("0.00"),
                "currency": "rub",
                "account_id": "acc-1",
                "counterparty": "acc-2",
                "status": "completed",
                "reason": None,
                "risk_level": "low",
            }
        ]

    def test_incoming_completed_transfer_is_seen_by_the_receiver(
        self, analyzer, reporter
    ):
        finish(analyzer, make_transfer("300"), client_id=1)

        rows = reporter.client_history(2, ["acc-2"])

        assert [
            (row["direction"], row["account_id"], row["counterparty"]) for row in rows
        ] == [("in", "acc-2", "acc-1")]
        assert rows[0]["risk_level"] is None

    def test_incoming_failed_transfer_is_hidden_from_the_receiver(
        self, analyzer, reporter
    ):
        finish(analyzer, make_transfer("300"), client_id=1, completed=False)

        assert reporter.client_history(2, ["acc-2"]) == []

    def test_own_failed_operation_shows_the_reason(self, analyzer, reporter):
        finish(analyzer, make_transfer("300"), client_id=1, completed=False)

        row = reporter.client_history(1, ["acc-1"])[0]

        assert row["status"] == "rejected"
        assert row["reason"] == "boom"

    def test_deposit_is_incoming_with_its_risk_level(self, analyzer, reporter):
        finish(analyzer, make_deposit("200000"), client_id=1)

        row = reporter.client_history(1, ["acc-1"])[0]

        assert row["direction"] == "in"
        assert row["account_id"] == "acc-1"
        assert row["counterparty"] is None
        assert row["risk_level"] == "medium"

    def test_rows_are_sorted_by_time(self, analyzer, reporter):
        later = finish(
            analyzer, make_transfer("1"), client_id=1, now=DAY + timedelta(hours=1)
        )
        earlier = finish(analyzer, make_transfer("2"), client_id=1, now=DAY)

        rows = reporter.client_history(1, ["acc-1"])

        assert [row["transaction_id"] for row in rows] == [
            earlier.transaction_id,
            later.transaction_id,
        ]

    def test_operations_of_other_clients_are_excluded(self, analyzer, reporter):
        finish(analyzer, make_transfer("300", target="acc-7"), client_id=5)

        assert reporter.client_history(1, ["acc-1"]) == []


class TestTransactionStatistics:
    def test_empty_log(self, reporter):
        assert reporter.transaction_statistics() == {
            "total": 0,
            "completed": 0,
            "rejected": 0,
            "success_rate": Decimal("0.0"),
            "by_type": {},
            "by_risk_level": {"low": 0, "medium": 0, "high": 0},
            "by_rule": {},
            "blocked_by_risk": 0,
            "volume": {},
            "fees": {},
        }

    def test_counts_outcomes_by_status_and_type(self, analyzer, reporter):
        finish(analyzer, make_transfer("100"), client_id=1)
        finish(analyzer, make_deposit("50"), client_id=1)
        finish(analyzer, make_transfer("70"), client_id=1, completed=False)

        statistics = reporter.transaction_statistics()

        assert statistics["total"] == 3
        assert statistics["completed"] == 2
        assert statistics["rejected"] == 1
        assert statistics["success_rate"] == Decimal("66.7")
        assert statistics["by_type"] == {
            "transfer": {"completed": 1, "rejected": 1},
            "deposit": {"completed": 1, "rejected": 0},
        }

    def test_volume_counts_only_completed_operations(self, analyzer, reporter):
        finish(analyzer, make_transfer("100"), client_id=1)
        finish(analyzer, make_transfer("70"), client_id=1, completed=False)

        statistics = reporter.transaction_statistics()

        assert statistics["volume"] == {"rub": Decimal("100.00")}
        assert statistics["fees"] == {"rub": Decimal("0.00")}

    def test_counts_risk_blocks_and_levels(self, analyzer, reporter):
        finish(analyzer, make_transfer("100"), client_id=1)
        finish(
            analyzer,
            make_transfer("200000", target="acc-9"),
            client_id=1,
            completed=False,
            error=RiskBlockedError("blocked"),
            now=NIGHT,
        )

        statistics = reporter.transaction_statistics()

        assert statistics["blocked_by_risk"] == 1
        assert statistics["by_risk_level"] == {"low": 1, "medium": 0, "high": 1}


class TestTransactionMoney:
    def test_fees_come_only_from_completed_operations(self, analyzer, reporter):
        external = TransactionType.EXTERNAL_TRANSFER
        finish(
            analyzer,
            Transaction(external, Decimal("5000"), source_account_id="acc-1"),
            client_id=1,
            fee="50",
        )
        finish(
            analyzer,
            Transaction(external, Decimal("9000"), source_account_id="acc-1"),
            client_id=1,
            completed=False,
            fee="90",
        )

        statistics = reporter.transaction_statistics()

        assert statistics["fees"] == {"rub": Decimal("50.00")}
        assert statistics["volume"] == {"rub": Decimal("5000.00")}

    def test_volume_is_kept_per_currency(self, analyzer, reporter):
        finish(analyzer, make_transfer("100"), client_id=1)
        finish(
            analyzer,
            Transaction(
                TransactionType.DEPOSIT,
                Decimal("7"),
                currency=Currency.USD,
                target_account_id="acc-3",
            ),
            client_id=1,
        )

        statistics = reporter.transaction_statistics()

        assert statistics["volume"] == {
            "rub": Decimal("100.00"),
            "usd": Decimal("7.00"),
        }
