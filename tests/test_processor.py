from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from src.enums import (
    Currency,
    TransactionPriority,
    TransactionStatus,
    TransactionType,
)
from src.exceptions import (
    InvalidOperationError,
    TransientTransactionError,
)
from src.models import Transaction, TransactionProcessor
from src.models.processor import DEFAULT_MAX_ATTEMPTS

NOW = datetime(2026, 9, 3, 12, 0)


class FlakyGateway:
    """Внешний шлюз, который падает первые N раз, а затем срабатывает."""

    def __init__(self, failures):
        self._failures = failures
        self.calls = 0

    def __call__(self, transaction):
        self.calls += 1

        if self.calls <= self._failures:
            raise TransientTransactionError("External gateway is unavailable")


def transfer(source, target, amount="500", **kwargs):
    return Transaction(
        TransactionType.TRANSFER,
        Decimal(amount),
        source_account_id=source.account_id,
        target_account_id=target.account_id,
        **kwargs,
    )


def mixed_transactions(source, target, savings, frozen):
    """Десять транзакций: успешные, ошибочные, отменённая и отложенная."""
    cancelled = transfer(source, target, "300")
    scheduled = transfer(source, target, "400", scheduled_at=NOW + timedelta(days=1))

    transactions = [
        transfer(
            source,
            target,
            "500",
            priority=TransactionPriority.HIGH,
        ),
        Transaction(
            TransactionType.DEPOSIT,
            Decimal("250"),
            target_account_id=target.account_id,
        ),
        Transaction(
            TransactionType.WITHDRAWAL,
            Decimal("100"),
            source_account_id=source.account_id,
        ),
        Transaction(
            TransactionType.EXTERNAL_TRANSFER,
            Decimal("1000"),
            source_account_id=source.account_id,
        ),
        transfer(source, savings, "200"),
        transfer(source, frozen, "150"),
        transfer(savings, target, "1500"),
        transfer(source, target, "999999"),
        cancelled,
        scheduled,
    ]
    return transactions, cancelled, scheduled


class TestFees:
    @pytest.mark.parametrize(
        "transaction_type, kwargs",
        [
            (TransactionType.DEPOSIT, {"target_account_id": "dst"}),
            (TransactionType.WITHDRAWAL, {"source_account_id": "src"}),
            (
                TransactionType.TRANSFER,
                {"source_account_id": "src", "target_account_id": "dst"},
            ),
        ],
    )
    def test_internal_operations_are_free(self, processor, transaction_type, kwargs):
        transaction = Transaction(transaction_type, Decimal("1000"), **kwargs)

        assert processor.calculate_fee(transaction) == Decimal("0.00")

    def test_external_transfer_uses_percent(self, processor):
        transaction = Transaction(
            TransactionType.EXTERNAL_TRANSFER,
            Decimal("100000"),
            source_account_id="src",
        )

        assert processor.calculate_fee(transaction) == Decimal("1000.00")

    def test_external_transfer_uses_minimum(self, processor):
        transaction = Transaction(
            TransactionType.EXTERNAL_TRANSFER, Decimal("1000"), source_account_id="src"
        )

        assert processor.calculate_fee(transaction) == Decimal("50.00")

    def test_minimum_fee_is_converted_to_transaction_currency(self, processor):
        transaction = Transaction(
            TransactionType.EXTERNAL_TRANSFER,
            Decimal("10"),
            currency=Currency.USD,
            source_account_id="src",
        )

        assert processor.calculate_fee(transaction) == Decimal("0.56")


class TestExecution:
    def test_deposit(self, processor, target_account):
        transaction = Transaction(
            TransactionType.DEPOSIT,
            Decimal("250"),
            target_account_id=target_account.account_id,
        )

        assert processor.process(transaction) is True
        assert transaction.status is TransactionStatus.COMPLETED
        assert target_account.balance == Decimal("1250.00")

    def test_withdrawal(self, processor, source_account):
        transaction = Transaction(
            TransactionType.WITHDRAWAL,
            Decimal("400"),
            source_account_id=source_account.account_id,
        )

        assert processor.process(transaction) is True
        assert source_account.balance == Decimal("9600.00")

    def test_transfer_moves_money(self, processor, source_account, target_account):
        transaction = transfer(source_account, target_account, "500")

        assert processor.process(transaction) is True
        assert source_account.balance == Decimal("9500.00")
        assert target_account.balance == Decimal("1500.00")

    def test_transfer_with_conversion(self, processor, source_account, usd_account):
        transaction = transfer(source_account, usd_account, "900")

        assert processor.process(transaction) is True
        assert source_account.balance == Decimal("9100.00")
        assert usd_account.balance == Decimal("110.00")

    def test_external_transfer_charges_fee(self, processor, source_account):
        transaction = Transaction(
            TransactionType.EXTERNAL_TRANSFER,
            Decimal("1000"),
            source_account_id=source_account.account_id,
        )

        assert processor.process(transaction) is True
        assert transaction.fee == Decimal("50.00")
        assert transaction.total_amount == Decimal("1050.00")
        assert source_account.balance == Decimal("8950.00")

    def test_external_transfer_rejects_target(self, source_account, target_account):
        with pytest.raises(InvalidOperationError):
            Transaction(
                TransactionType.EXTERNAL_TRANSFER,
                Decimal("100"),
                source_account_id=source_account.account_id,
                target_account_id=target_account.account_id,
            )

    def test_unknown_account_fails(self, processor, source_account):
        transaction = Transaction(
            TransactionType.TRANSFER,
            Decimal("100"),
            source_account_id=source_account.account_id,
            target_account_id="nope",
        )

        assert processor.process(transaction) is False
        assert transaction.failure_reason == "Account not found"
        assert source_account.balance == Decimal("10000.00")


class TestRules:
    def test_frozen_source_blocks_transfer(
        self, processor, transfer_bank, source_account, target_account
    ):
        transfer_bank.freeze_account(source_account.account_id)
        transaction = transfer(source_account, target_account)

        assert processor.process(transaction) is False
        assert transaction.status is TransactionStatus.FAILED
        assert "Frozen" in transaction.failure_reason
        assert source_account.balance == Decimal("10000.00")
        assert target_account.balance == Decimal("1000.00")

    def test_frozen_target_blocks_transfer_without_losing_money(
        self, processor, transfer_bank, source_account, target_account
    ):
        transfer_bank.freeze_account(target_account.account_id)
        transaction = transfer(source_account, target_account)

        assert processor.process(transaction) is False
        assert source_account.balance == Decimal("10000.00")
        assert target_account.balance == Decimal("1000.00")

    def test_closed_account_blocks_transfer(
        self, processor, transfer_bank, source_account, target_account
    ):
        closed = transfer_bank.open_account(2, "bank")
        transfer_bank.close_account(closed.account_id)
        transaction = transfer(source_account, closed)

        assert processor.process(transaction) is False
        assert "Closed" in transaction.failure_reason

    def test_plain_account_cannot_go_negative(
        self, processor, source_account, target_account
    ):
        transaction = transfer(source_account, target_account, "10001")

        assert processor.process(transaction) is False
        assert transaction.failure_reason == "Not enough money to withdraw"
        assert source_account.balance == Decimal("10000.00")

    def test_savings_keeps_its_minimum_on_transfer(self, transfer_bank, target_account):
        savings = transfer_bank.open_account(
            1, "savings", balance=Decimal("5000"), min_balance=Decimal("1000")
        )
        processor = TransactionProcessor(transfer_bank)
        transaction = transfer(savings, target_account, "4500")

        assert processor.process(transaction) is False
        assert savings.balance == Decimal("5000.00")

    def test_premium_fee_counts_against_the_limit(self, transfer_bank, target_account):
        premium = transfer_bank.open_account(
            1,
            "premium",
            balance=Decimal("100"),
            overdraft_limit=Decimal("400"),
            fixed_fee=Decimal("25"),
        )
        processor = TransactionProcessor(transfer_bank)

        assert processor.process(transfer(premium, target_account, "500")) is False
        assert premium.balance == Decimal("100.00")

        assert processor.process(transfer(premium, target_account, "475")) is True
        assert premium.balance == Decimal("-400.00")

    def test_premium_may_use_overdraft(self, transfer_bank, target_account):
        premium = transfer_bank.open_account(
            1, "premium", balance=Decimal("100"), overdraft_limit=Decimal("1000")
        )
        processor = TransactionProcessor(transfer_bank)
        transaction = transfer(premium, target_account, "500")

        assert processor.process(transaction) is True
        assert premium.balance == Decimal("-400.00")
        assert target_account.balance == Decimal("1500.00")

    def test_premium_still_respects_its_overdraft_limit(
        self, transfer_bank, target_account
    ):
        premium = transfer_bank.open_account(
            1, "premium", balance=Decimal("100"), overdraft_limit=Decimal("200")
        )
        processor = TransactionProcessor(transfer_bank)
        transaction = transfer(premium, target_account, "500")

        assert processor.process(transaction) is False
        assert premium.balance == Decimal("100.00")


class TestRetries:
    def test_succeeds_after_transient_failures(self, transfer_bank, source_account):
        gateway = FlakyGateway(failures=2)
        processor = TransactionProcessor(transfer_bank, external_gateway=gateway)
        transaction = Transaction(
            TransactionType.EXTERNAL_TRANSFER,
            Decimal("1000"),
            source_account_id=source_account.account_id,
        )

        assert processor.process(transaction) is True
        assert transaction.attempts == 3
        assert gateway.calls == 3
        assert source_account.balance == Decimal("8950.00")

    def test_gives_up_after_max_attempts(self, transfer_bank, source_account):
        gateway = FlakyGateway(failures=99)
        processor = TransactionProcessor(transfer_bank, external_gateway=gateway)
        transaction = Transaction(
            TransactionType.EXTERNAL_TRANSFER,
            Decimal("1000"),
            source_account_id=source_account.account_id,
        )

        assert processor.process(transaction) is False
        assert transaction.attempts == 3
        assert transaction.status is TransactionStatus.FAILED
        assert source_account.balance == Decimal("10000.00")

    def test_business_error_is_not_retried(
        self, processor, source_account, target_account
    ):
        transaction = transfer(source_account, target_account, "10001")

        processor.process(transaction)

        assert transaction.attempts == 1

    def test_max_attempts_must_be_positive(self, transfer_bank):
        with pytest.raises(InvalidOperationError):
            TransactionProcessor(transfer_bank, max_attempts=0)


class TestRollback:
    def test_failed_external_transfer_returns_account_fee_too(
        self, transfer_bank, target_account
    ):
        premium = transfer_bank.open_account(
            1,
            "premium",
            balance=Decimal("10000"),
            fixed_fee=Decimal("25"),
            overdraft_limit=Decimal("5000"),
        )
        processor = TransactionProcessor(
            transfer_bank, external_gateway=FlakyGateway(failures=99)
        )
        transaction = Transaction(
            TransactionType.EXTERNAL_TRANSFER,
            Decimal("1000"),
            source_account_id=premium.account_id,
        )

        assert processor.process(transaction) is False
        assert premium.balance == Decimal("10000.00")

    def test_failed_transfer_returns_account_fee_too(self, transfer_bank, usd_account):
        premium = transfer_bank.open_account(
            1, "premium", balance=Decimal("10000"), fixed_fee=Decimal("25")
        )
        processor = TransactionProcessor(transfer_bank)
        transaction = transfer(premium, usd_account, "0.01")

        assert processor.process(transaction) is False
        assert premium.balance == Decimal("10000.00")
        assert usd_account.balance == Decimal("100.00")


class TestUnexpectedErrors:
    def test_unknown_error_does_not_escape(self, transfer_bank, source_account):
        def broken_gateway(transaction):
            raise RuntimeError("network is down")

        processor = TransactionProcessor(transfer_bank, external_gateway=broken_gateway)
        transaction = Transaction(
            TransactionType.EXTERNAL_TRANSFER,
            Decimal("1000"),
            source_account_id=source_account.account_id,
        )

        assert processor.process(transaction) is False
        assert transaction.status is TransactionStatus.FAILED
        assert transaction.failure_reason == "network is down"
        assert source_account.balance == Decimal("10000.00")

    def test_unknown_error_is_recorded(self, transfer_bank, source_account):
        def broken_gateway(transaction):
            raise RuntimeError("network is down")

        processor = TransactionProcessor(transfer_bank, external_gateway=broken_gateway)
        transaction = Transaction(
            TransactionType.EXTERNAL_TRANSFER,
            Decimal("1000"),
            source_account_id=source_account.account_id,
        )

        processor.process(transaction)

        assert len(processor.errors) == 1
        assert processor.errors[0]["error"] == "RuntimeError"

    def test_unknown_error_is_not_retried(self, transfer_bank, source_account):
        def broken_gateway(transaction):
            raise RuntimeError("network is down")

        processor = TransactionProcessor(transfer_bank, external_gateway=broken_gateway)
        transaction = Transaction(
            TransactionType.EXTERNAL_TRANSFER,
            Decimal("1000"),
            source_account_id=source_account.account_id,
        )

        processor.process(transaction)

        assert transaction.attempts == 1

    def test_queue_keeps_going_after_unknown_error(
        self, transfer_bank, queue, source_account, target_account
    ):
        def broken_gateway(transaction):
            raise RuntimeError("network is down")

        processor = TransactionProcessor(transfer_bank, external_gateway=broken_gateway)
        queue.add(
            Transaction(
                TransactionType.EXTERNAL_TRANSFER,
                Decimal("1000"),
                source_account_id=source_account.account_id,
                priority=TransactionPriority.HIGH,
            )
        )
        queue.add(transfer(source_account, target_account, "500"))

        summary = processor.process_queue(queue, NOW)

        assert summary == {"processed": 2, "completed": 1, "failed": 1}
        assert target_account.balance == Decimal("1500.00")


class TestErrorLog:
    def test_failure_is_recorded(self, processor, source_account, target_account):
        transaction = transfer(source_account, target_account, "10001")

        processor.process(transaction)

        assert len(processor.errors) == 1
        entry = processor.errors[0]
        assert entry["transaction_id"] == transaction.transaction_id
        assert entry["error"] == "InsufficientFundsError"
        assert entry["attempt"] == 1

    def test_every_retry_is_recorded(self, transfer_bank, source_account):
        processor = TransactionProcessor(
            transfer_bank, external_gateway=FlakyGateway(failures=99)
        )
        transaction = Transaction(
            TransactionType.EXTERNAL_TRANSFER,
            Decimal("1000"),
            source_account_id=source_account.account_id,
        )

        processor.process(transaction)

        assert len(processor.errors) == 3
        assert [entry["attempt"] for entry in processor.errors] == [1, 2, 3]

    def test_success_writes_nothing(self, processor, source_account, target_account):
        processor.process(transfer(source_account, target_account, "100"))

        assert processor.errors == []

    def test_log_getter_returns_copies(self, processor, source_account, target_account):
        processor.process(transfer(source_account, target_account, "10001"))

        processor.errors[0]["message"] = "hacked"

        assert processor.errors[0]["message"] != "hacked"


class TestProcessQueue:
    def test_rejects_non_queue(self, processor):
        with pytest.raises(InvalidOperationError):
            processor.process_queue([])

    def test_empty_queue(self, processor, queue):
        assert processor.process_queue(queue, NOW) == {
            "processed": 0,
            "completed": 0,
            "failed": 0,
        }

    def test_scheduled_stays_in_queue(
        self, processor, queue, source_account, target_account
    ):
        queue.add(
            transfer(
                source_account,
                target_account,
                "100",
                scheduled_at=NOW + timedelta(days=1),
            )
        )

        summary = processor.process_queue(queue, NOW)

        assert summary["processed"] == 0
        assert len(queue) == 1

    def test_ten_transactions_end_to_end(
        self, processor, queue, transfer_bank, source_account, target_account
    ):
        savings = transfer_bank.open_account(
            2, "savings", balance=Decimal("2000"), min_balance=Decimal("1000")
        )
        frozen = transfer_bank.open_account(2, "bank", balance=Decimal("500"))
        transfer_bank.freeze_account(frozen.account_id)

        transactions, cancelled, scheduled = mixed_transactions(
            source_account, target_account, savings, frozen
        )

        for transaction in transactions:
            queue.add(transaction)

        assert len(queue) == 10

        queue.cancel(cancelled.transaction_id)
        summary = processor.process_queue(queue, NOW)

        assert summary == {"processed": 8, "completed": 5, "failed": 3}
        assert len(queue) == 1
        assert queue.scheduled == [scheduled]
        assert cancelled.status is TransactionStatus.CANCELLED
        assert len(processor.errors) == 3
        assert source_account.balance == Decimal("10000") - Decimal("1850")
        assert target_account.balance == Decimal("1000") + Decimal("750")
        assert savings.balance == Decimal("2200.00")
        assert frozen.balance == Decimal("500.00")


class TestProcessorClock:
    """Часы процессора по умолчанию - часы банка; один момент на весь process()."""

    def test_default_clock_is_the_bank_clock(
        self, transfer_bank, source_account, target_account
    ):
        processor = TransactionProcessor(transfer_bank)
        transaction = transfer(source_account, target_account)

        processor.process(transaction)

        assert transaction.completed_at == transfer_bank.time_provider()

    def test_explicit_moment_is_used_for_every_step(
        self, transfer_bank, source_account, target_account
    ):
        later = transfer_bank.time_provider() + timedelta(hours=1)
        processor = TransactionProcessor(transfer_bank)
        transaction = transfer(source_account, target_account, "999999")

        processor.process(transaction, now=later)

        assert transaction.updated_at == later
        assert processor.errors[0]["time"] == later.isoformat(timespec="seconds")

    def test_retries_keep_the_same_moment(self, transfer_bank, source_account):
        later = transfer_bank.time_provider() + timedelta(hours=1)
        processor = TransactionProcessor(
            transfer_bank, external_gateway=FlakyGateway(failures=99)
        )
        transaction = Transaction(
            TransactionType.EXTERNAL_TRANSFER,
            Decimal("1000"),
            source_account_id=source_account.account_id,
        )

        processor.process(transaction, now=later)

        times = [error["time"] for error in processor.errors]
        assert times == [later.isoformat(timespec="seconds")] * DEFAULT_MAX_ATTEMPTS
        assert transaction.updated_at == later
