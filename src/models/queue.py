import heapq
from datetime import datetime

from loguru import logger

from src.enums import TransactionStatus
from src.exceptions import InvalidOperationError
from src.models.transactions import Transaction


class TransactionQueue:
    """Очередь транзакций: приоритет, отложенный запуск, отмена."""

    def __init__(self, time_provider=None):
        self._heap = []
        self._by_id = {}
        self._counter = 0
        self._time_provider = time_provider or datetime.now

    @property
    def pending(self):
        return self._sorted_transactions()

    @property
    def scheduled(self):
        return [
            transaction
            for transaction in self._sorted_transactions()
            if transaction.status is TransactionStatus.SCHEDULED
        ]

    def add(self, transaction):
        if not isinstance(transaction, Transaction):
            raise InvalidOperationError("Invalid transaction")

        if not transaction.is_open:
            raise InvalidOperationError("Only an open transaction can be queued")

        if transaction.transaction_id in self._by_id:
            raise InvalidOperationError("Transaction is already queued")

        self._counter += 1
        self._by_id[transaction.transaction_id] = transaction
        heapq.heappush(
            self._heap,
            (transaction.priority_rank, self._counter, transaction.transaction_id),
        )
        logger.info(
            "transaction_queued",
            transaction_id=transaction.transaction_id,
            transaction_type=transaction.transaction_type,
            amount=transaction.amount,
            currency=transaction.currency,
            priority=transaction.priority,
            scheduled_at=transaction.scheduled_at,
        )
        return transaction

    def cancel(self, transaction_id, reason="Cancelled by user"):
        transaction = self._by_id.get(transaction_id)

        if transaction is None:
            raise InvalidOperationError("Transaction not found in queue")

        # Сначала меняем статус: если переход запрещён, запись остаётся в очереди.
        transaction.cancel(reason, now=self._time_provider())
        del self._by_id[transaction_id]
        logger.info(
            "transaction_cancelled",
            transaction_id=transaction_id,
            reason=transaction.failure_reason,
        )
        return transaction

    def pop_ready(self, now: datetime = None):
        now = now or self._time_provider()
        deferred = []
        found = None

        while self._heap:
            entry = heapq.heappop(self._heap)
            transaction = self._by_id.get(entry[2])

            if transaction is None:
                continue

            if not transaction.is_open:
                del self._by_id[transaction.transaction_id]
                continue

            if not transaction.is_ready(now):
                deferred.append(entry)
                continue

            found = transaction
            break

        for entry in deferred:
            heapq.heappush(self._heap, entry)

        if found is not None:
            del self._by_id[found.transaction_id]

        return found

    def _sorted_transactions(self):
        return [
            self._by_id[transaction_id]
            for _, _, transaction_id in sorted(self._heap)
            if transaction_id in self._by_id and self._by_id[transaction_id].is_open
        ]

    def __len__(self):
        return sum(1 for t in self._by_id.values() if t.is_open)

    def __contains__(self, transaction_id):
        return transaction_id in self._by_id
