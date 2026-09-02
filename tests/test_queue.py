from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from src.enums import TransactionPriority, TransactionStatus, TransactionType
from src.exceptions import InvalidOperationError
from src.models import Transaction, TransactionQueue

NOW = datetime(2026, 9, 3, 12, 0)


def make_deposit(amount="100", **kwargs):
    kwargs.setdefault("target_account_id", "dst-1")
    return Transaction(TransactionType.DEPOSIT, Decimal(amount), **kwargs)


class TestAdd:
    def test_adds_transaction(self, queue):
        transaction = make_deposit()

        queue.add(transaction)

        assert len(queue) == 1
        assert transaction.transaction_id in queue

    def test_rejects_non_transaction(self, queue):
        with pytest.raises(InvalidOperationError):
            queue.add("not a transaction")

    def test_rejects_duplicate(self, queue):
        transaction = make_deposit()
        queue.add(transaction)

        with pytest.raises(InvalidOperationError):
            queue.add(transaction)

    def test_rejects_cancelled(self, queue):
        transaction = make_deposit()
        transaction.cancel()

        with pytest.raises(InvalidOperationError):
            queue.add(transaction)

    def test_empty_queue(self, queue):
        assert len(queue) == 0
        assert queue.pending == []
        assert queue.pop_ready(NOW) is None


class TestPriority:
    def test_high_goes_first(self, queue):
        low = make_deposit(priority=TransactionPriority.LOW)
        high = make_deposit(priority=TransactionPriority.HIGH)
        normal = make_deposit(priority=TransactionPriority.NORMAL)

        queue.add(low)
        queue.add(high)
        queue.add(normal)

        order = [queue.pop_ready(NOW) for _ in range(3)]
        assert order == [high, normal, low]

    def test_same_priority_keeps_insertion_order(self, queue):
        first = make_deposit()
        second = make_deposit()
        third = make_deposit()

        for transaction in (first, second, third):
            queue.add(transaction)

        order = [queue.pop_ready(NOW) for _ in range(3)]
        assert order == [first, second, third]

    def test_pending_is_sorted_by_priority(self, queue):
        low = make_deposit(priority=TransactionPriority.LOW)
        high = make_deposit(priority=TransactionPriority.HIGH)
        queue.add(low)
        queue.add(high)

        assert queue.pending == [high, low]


class TestScheduled:
    def test_not_released_before_time(self, queue):
        queue.add(make_deposit(scheduled_at=NOW + timedelta(hours=1)))

        assert queue.pop_ready(NOW) is None
        assert len(queue) == 1

    def test_released_at_time(self, queue):
        scheduled = make_deposit(scheduled_at=NOW)
        queue.add(scheduled)

        assert queue.pop_ready(NOW) is scheduled

    def test_ready_one_is_taken_before_scheduled(self, queue):
        scheduled = make_deposit(
            priority=TransactionPriority.HIGH, scheduled_at=NOW + timedelta(hours=1)
        )
        immediate = make_deposit(priority=TransactionPriority.LOW)
        queue.add(scheduled)
        queue.add(immediate)

        assert queue.pop_ready(NOW) is immediate
        assert queue.pop_ready(NOW) is None
        assert queue.pop_ready(NOW + timedelta(hours=2)) is scheduled

    def test_scheduled_property(self, queue):
        scheduled = make_deposit(scheduled_at=NOW + timedelta(hours=1))
        queue.add(make_deposit())
        queue.add(scheduled)

        assert queue.scheduled == [scheduled]


class TestCancel:
    def test_cancel_removes_from_queue(self, queue):
        transaction = make_deposit()
        queue.add(transaction)

        queue.cancel(transaction.transaction_id)

        assert len(queue) == 0
        assert transaction.status is TransactionStatus.CANCELLED
        assert queue.pop_ready(NOW) is None

    def test_cancel_keeps_reason(self, queue):
        transaction = make_deposit()
        queue.add(transaction)

        queue.cancel(transaction.transaction_id, "Ошибка оператора")

        assert transaction.failure_reason == "Ошибка оператора"

    def test_cancel_scheduled(self, queue):
        transaction = make_deposit(scheduled_at=NOW + timedelta(hours=1))
        queue.add(transaction)

        queue.cancel(transaction.transaction_id)

        assert queue.scheduled == []

    def test_cancel_unknown_id(self, queue):
        with pytest.raises(InvalidOperationError):
            queue.cancel("nope")

    def test_cancel_does_not_disturb_others(self, queue):
        first = make_deposit()
        second = make_deposit()
        third = make_deposit()
        for transaction in (first, second, third):
            queue.add(transaction)

        queue.cancel(second.transaction_id)

        assert [queue.pop_ready(NOW) for _ in range(2)] == [first, third]
        assert len(queue) == 0


class TestPop:
    def test_popped_transaction_leaves_queue(self, queue):
        transaction = make_deposit()
        queue.add(transaction)

        queue.pop_ready(NOW)

        assert len(queue) == 0
        assert transaction.transaction_id not in queue

    def test_popped_can_be_requeued_while_open(self, queue):
        transaction = make_deposit()
        queue.add(transaction)
        queue.pop_ready(NOW)

        queue.add(transaction)

        assert len(queue) == 1

    def test_uses_own_clock_when_now_is_omitted(self):
        queue = TransactionQueue(time_provider=lambda: NOW)
        queue.add(make_deposit(scheduled_at=NOW + timedelta(hours=1)))

        assert queue.pop_ready() is None
