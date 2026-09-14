from decimal import Decimal
from pathlib import Path

import pytest

from loguru import logger

from src.enums import Currency, TransactionType
from src.logging_setup import configure_logging
from src.models import Transaction
from src.models.accounts import BankAccount, PremiumAccount, SavingsAccount

MODELS_DIR = Path(__file__).resolve().parent.parent / "src" / "models"


def make_account(**kwargs):
    kwargs.setdefault("user_id", 1)
    kwargs.setdefault("owner_name", "Max Petrov")
    kwargs.setdefault("balance", Decimal("1000"))
    return BankAccount(**kwargs)


class TestModelsDoNotPrint:
    @pytest.mark.parametrize(
        "path", sorted(MODELS_DIR.glob("*.py")), ids=lambda p: p.name
    )
    def test_no_print_in_domain_models(self, path):
        assert "print(" not in path.read_text(encoding="utf-8")

    def test_nothing_goes_to_stdout(self, capsys):
        account = make_account()

        account.deposit(Decimal("100"))
        account.withdraw(Decimal("50"))

        assert capsys.readouterr().out == ""


class TestStructuredEvents:
    def test_deposit_event_carries_fields(self, log_events):
        account = make_account(balance=Decimal("0"))

        account.deposit(Decimal("100"))

        event = log_events[-1]
        assert event["message"] == "deposit"
        assert event["extra"] == {
            "account_id": account.account_id,
            "amount": Decimal("100.00"),
            "currency": Currency.RUB,
            "balance": Decimal("100.00"),
        }

    def test_withdrawal_event_carries_fields(self, log_events):
        account = make_account(balance=Decimal("500"))

        account.withdraw(Decimal("200"))

        event = log_events[-1]
        assert event["message"] == "withdrawal"
        assert event["extra"]["amount"] == Decimal("200.00")
        assert event["extra"]["balance"] == Decimal("300.00")

    def test_premium_event_carries_the_fee(self, log_events):
        account = PremiumAccount(
            user_id=1,
            owner_name="Anna",
            balance=Decimal("1000"),
            fixed_fee=Decimal("25"),
        )

        account.withdraw(Decimal("100"))

        assert log_events[-1]["extra"]["fee"] == Decimal("25.00")

    def test_failed_operation_writes_nothing(self, log_events):
        account = make_account(balance=Decimal("10"))

        with pytest.raises(Exception):
            account.withdraw(Decimal("100"))

        assert log_events == []


def lifecycle(log_events, name):
    return [event for event in log_events if event["message"] == name]


def queued_transfer(queue, source_account, target_account, amount="100"):
    transaction = Transaction(
        TransactionType.TRANSFER,
        Decimal(amount),
        source_account_id=source_account.account_id,
        target_account_id=target_account.account_id,
    )
    queue.add(transaction)
    return transaction


class TestLifecycleEvents:
    def test_queued_event_carries_fields(
        self, log_events, queue, source_account, target_account
    ):
        transaction = queued_transfer(queue, source_account, target_account)

        event = lifecycle(log_events, "transaction_queued")[0]
        assert event["extra"]["transaction_id"] == transaction.transaction_id
        assert event["extra"]["amount"] == Decimal("100.00")
        assert event["extra"]["priority"] == transaction.priority
        assert event["extra"]["scheduled_at"] is None

    def test_cancelled_event_carries_the_reason(
        self, log_events, queue, source_account, target_account
    ):
        transaction = queued_transfer(queue, source_account, target_account)

        queue.cancel(transaction.transaction_id, "Клиент передумал")

        event = lifecycle(log_events, "transaction_cancelled")[0]
        assert event["extra"] == {
            "transaction_id": transaction.transaction_id,
            "reason": "Клиент передумал",
        }

    def test_completed_event_carries_the_fee(
        self, log_events, queue, processor, source_account, target_account
    ):
        transaction = queued_transfer(queue, source_account, target_account)

        processor.process_queue(queue)

        event = lifecycle(log_events, "transaction_completed")[0]
        assert event["level"].name == "INFO"
        assert event["extra"]["transaction_id"] == transaction.transaction_id
        assert event["extra"]["fee"] == Decimal("0.00")

    def test_rejected_event_is_a_warning_with_the_error(
        self, log_events, queue, processor, source_account, target_account
    ):
        transaction = queued_transfer(queue, source_account, target_account, "999999")

        processor.process_queue(queue)

        event = lifecycle(log_events, "transaction_rejected")[0]
        assert event["level"].name == "WARNING"
        assert event["extra"]["transaction_id"] == transaction.transaction_id
        assert event["extra"]["error"] == "InsufficientFundsError"
        assert event["extra"]["reason"] == transaction.failure_reason

    def test_every_processed_transaction_has_exactly_one_outcome_event(
        self, log_events, queue, processor, source_account, target_account
    ):
        queued_transfer(queue, source_account, target_account)
        queued_transfer(queue, source_account, target_account, "999999")

        processor.process_queue(queue)

        outcomes = lifecycle(log_events, "transaction_completed") + lifecycle(
            log_events, "transaction_rejected"
        )
        assert len(outcomes) == 2


class TestConfigureLogging:
    def test_sink_gets_only_the_selected_events(self, tmp_path):
        shown = []

        try:
            configure_logging(
                sink=lambda message: shown.append(message.record["message"]),
                events=("wanted",),
                log_file=tmp_path / "logs" / "all.log",
            )
            logger.info("wanted")
            logger.info("noise")
        finally:
            logger.remove()

        assert shown == ["wanted"]

    def test_log_file_gets_every_event(self, tmp_path):
        path = tmp_path / "logs" / "all.log"

        try:
            configure_logging(sink=lambda message: None, events=(), log_file=path)
            logger.info("wanted")
            logger.info("noise")
        finally:
            logger.remove()

        text = path.read_text(encoding="utf-8")
        assert "wanted" in text
        assert "noise" in text

    def test_empty_event_list_shows_nothing(self):
        shown = []

        try:
            configure_logging(sink=lambda message: shown.append(message), events=())
            logger.info("anything")
        finally:
            logger.remove()

        assert shown == []


class TestInterestEvent:
    def test_monthly_interest_is_logged(self, log_events):
        account = SavingsAccount(
            user_id=1,
            owner_name="Max",
            balance=Decimal("1000"),
            monthly_interest_rate=Decimal("0.02"),
        )

        account.apply_monthly_interest()

        event = log_events[-1]
        assert event["message"] == "interest_applied"
        assert event["extra"]["amount"] == Decimal("20.00")
        assert event["extra"]["balance"] == Decimal("1020.00")
