from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from src.enums import Currency, TransactionPriority, TransactionStatus, TransactionType
from src.exceptions import InvalidOperationError
from src.models import Transaction


def make_transfer(**kwargs):
    kwargs.setdefault("transaction_type", TransactionType.TRANSFER)
    kwargs.setdefault("amount", Decimal("100"))
    kwargs.setdefault("source_account_id", "src-1")
    kwargs.setdefault("target_account_id", "dst-1")
    return Transaction(**kwargs)


class TestCreation:
    def test_defaults(self):
        transaction = make_transfer()

        assert transaction.status is TransactionStatus.PENDING
        assert transaction.amount == Decimal("100.00")
        assert transaction.fee == Decimal("0.00")
        assert transaction.currency is Currency.RUB
        assert transaction.priority is TransactionPriority.NORMAL
        assert transaction.attempts == 0
        assert transaction.failure_reason is None
        assert transaction.completed_at is None
        assert transaction.created_at == transaction.updated_at

    def test_ids_are_unique(self):
        assert make_transfer().transaction_id != make_transfer().transaction_id

    def test_scheduled_transaction_starts_scheduled(self):
        transaction = make_transfer(scheduled_at=datetime(2026, 9, 3, 10, 0))

        assert transaction.status is TransactionStatus.SCHEDULED

    @pytest.mark.parametrize(
        "field, value",
        [
            ("transaction_type", "transfer"),
            ("currency", "rub"),
            ("priority", "high"),
            ("amount", Decimal("0")),
            ("amount", Decimal("-10")),
            ("amount", 0.5),
            ("amount", True),
            ("scheduled_at", "2026-09-03"),
        ],
    )
    def test_rejects_invalid_field(self, field, value):
        with pytest.raises(InvalidOperationError):
            make_transfer(**{field: value})

    def test_amount_is_quantized(self):
        assert make_transfer(amount=Decimal("10.005")).amount == Decimal("10.01")


class TestParticipants:
    def test_deposit_requires_target_only(self):
        transaction = Transaction(
            TransactionType.DEPOSIT, Decimal("10"), target_account_id="dst-1"
        )

        assert transaction.source_account_id is None

    def test_deposit_without_target_fails(self):
        with pytest.raises(InvalidOperationError):
            Transaction(TransactionType.DEPOSIT, Decimal("10"))

    def test_deposit_with_source_fails(self):
        with pytest.raises(InvalidOperationError):
            Transaction(
                TransactionType.DEPOSIT,
                Decimal("10"),
                source_account_id="src-1",
                target_account_id="dst-1",
            )

    def test_withdrawal_requires_source_only(self):
        transaction = Transaction(
            TransactionType.WITHDRAWAL, Decimal("10"), source_account_id="src-1"
        )

        assert transaction.target_account_id is None

    def test_withdrawal_with_target_fails(self):
        with pytest.raises(InvalidOperationError):
            Transaction(
                TransactionType.WITHDRAWAL,
                Decimal("10"),
                source_account_id="src-1",
                target_account_id="dst-1",
            )

    @pytest.mark.parametrize(
        "source, target", [(None, "dst-1"), ("src-1", None), (None, None)]
    )
    def test_transfer_requires_both_sides(self, source, target):
        with pytest.raises(InvalidOperationError):
            Transaction(
                TransactionType.TRANSFER,
                Decimal("10"),
                source_account_id=source,
                target_account_id=target,
            )

    def test_transfer_to_itself_fails(self):
        with pytest.raises(InvalidOperationError):
            make_transfer(source_account_id="same", target_account_id="same")

    def test_external_transfer_needs_no_target(self):
        transaction = Transaction(
            TransactionType.EXTERNAL_TRANSFER, Decimal("10"), source_account_id="src-1"
        )

        assert transaction.target_account_id is None


class TestLifecycle:
    def test_full_success_path(self):
        transaction = make_transfer()

        transaction.mark_processing()
        assert transaction.status is TransactionStatus.PROCESSING

        transaction.mark_completed()
        assert transaction.status is TransactionStatus.COMPLETED
        assert transaction.completed_at is not None
        assert transaction.failure_reason is None

    def test_failure_path(self):
        transaction = make_transfer()
        transaction.mark_processing()

        transaction.mark_failed("Frozen account cannot send money")

        assert transaction.status is TransactionStatus.FAILED
        assert transaction.failure_reason == "Frozen account cannot send money"

    def test_cancel_from_pending(self):
        transaction = make_transfer()

        transaction.cancel("Клиент передумал")

        assert transaction.status is TransactionStatus.CANCELLED
        assert transaction.failure_reason == "Клиент передумал"

    def test_cannot_cancel_completed(self):
        transaction = make_transfer()
        transaction.mark_processing()
        transaction.mark_completed()

        with pytest.raises(InvalidOperationError):
            transaction.cancel()

    def test_cannot_complete_without_processing(self):
        with pytest.raises(InvalidOperationError):
            make_transfer().mark_completed()

    def test_cannot_process_cancelled(self):
        transaction = make_transfer()
        transaction.cancel()

        with pytest.raises(InvalidOperationError):
            transaction.mark_processing()

    def test_cannot_fail_completed(self):
        transaction = make_transfer()
        transaction.mark_processing()
        transaction.mark_completed()

        with pytest.raises(InvalidOperationError):
            transaction.mark_failed("too late")

    @pytest.mark.parametrize("reason", ["", "   ", None, 123])
    def test_failure_reason_is_required(self, reason):
        transaction = make_transfer()
        transaction.mark_processing()

        with pytest.raises(InvalidOperationError):
            transaction.mark_failed(reason)

    def test_attempts_are_counted(self):
        transaction = make_transfer()

        transaction.register_attempt()
        transaction.register_attempt()

        assert transaction.attempts == 2

    def test_updated_at_moves_forward(self):
        created = datetime(2026, 9, 3, 10, 0)
        transaction = make_transfer(created_at=created)

        transaction.mark_processing(now=datetime(2026, 9, 3, 11, 0))

        assert transaction.updated_at > created


class TestFee:
    def test_fee_added_to_total(self):
        transaction = make_transfer(amount=Decimal("100"))
        transaction.mark_processing()

        transaction.apply_fee(Decimal("25"))

        assert transaction.fee == Decimal("25.00")
        assert transaction.total_amount == Decimal("125.00")

    def test_fee_only_while_processing(self):
        with pytest.raises(InvalidOperationError):
            make_transfer().apply_fee(Decimal("25"))

    @pytest.mark.parametrize("fee", [Decimal("-1"), 0.5, "25"])
    def test_rejects_invalid_fee(self, fee):
        transaction = make_transfer()
        transaction.mark_processing()

        with pytest.raises(InvalidOperationError):
            transaction.apply_fee(fee)


class TestReadiness:
    def test_immediate_transaction_is_ready(self):
        assert make_transfer().is_ready(datetime(2026, 9, 3, 10, 0)) is True

    def test_scheduled_is_not_ready_before_time(self):
        transaction = make_transfer(scheduled_at=datetime(2026, 9, 3, 12, 0))

        assert transaction.is_ready(datetime(2026, 9, 3, 10, 0)) is False

    def test_scheduled_is_ready_at_time(self):
        moment = datetime(2026, 9, 3, 12, 0)
        transaction = make_transfer(scheduled_at=moment)

        assert transaction.is_ready(moment) is True

    def test_cancelled_is_never_ready(self):
        transaction = make_transfer()
        transaction.cancel()

        assert transaction.is_ready(datetime.now() + timedelta(days=1)) is False

    @pytest.mark.parametrize(
        "priority, rank",
        [
            (TransactionPriority.HIGH, 0),
            (TransactionPriority.NORMAL, 1),
            (TransactionPriority.LOW, 2),
        ],
    )
    def test_priority_rank(self, priority, rank):
        assert make_transfer(priority=priority).priority_rank == rank
