import uuid
from datetime import datetime
from decimal import Decimal

from src.enums import Currency, TransactionPriority, TransactionStatus, TransactionType
from src.exceptions import InvalidOperationError
from src.money import to_non_negative_money, to_positive_money

PRIORITY_RANK = {
    TransactionPriority.HIGH: 0,
    TransactionPriority.NORMAL: 1,
    TransactionPriority.LOW: 2,
}

OPEN_STATUSES = (TransactionStatus.PENDING, TransactionStatus.SCHEDULED)

# тип операции -> (нужен отправитель, нужен получатель)
PARTICIPANTS = {
    TransactionType.DEPOSIT: (False, True),
    TransactionType.WITHDRAWAL: (True, False),
    TransactionType.TRANSFER: (True, True),
    TransactionType.EXTERNAL_TRANSFER: (True, False),
}


class Transaction:
    def __init__(
        self,
        transaction_type,
        amount: int | Decimal,
        currency=Currency.RUB,
        source_account_id: str = None,
        target_account_id: str = None,
        priority=TransactionPriority.NORMAL,
        scheduled_at: datetime = None,
        transaction_id: str = None,
        created_at: datetime = None,
        time_provider=None,
    ):
        if not isinstance(transaction_type, TransactionType):
            raise InvalidOperationError("Invalid transaction type")

        if not isinstance(currency, Currency):
            raise InvalidOperationError("Invalid currency")

        if not isinstance(priority, TransactionPriority):
            raise InvalidOperationError("Invalid transaction priority")

        amount = to_positive_money(amount, "Amount must be a positive number")

        if scheduled_at is not None and not isinstance(scheduled_at, datetime):
            raise InvalidOperationError("Scheduled time must be a datetime")

        self._validate_participants(
            transaction_type, source_account_id, target_account_id
        )

        self._time_provider = time_provider or datetime.now
        self._transaction_id = str(transaction_id or uuid.uuid4())
        self._transaction_type = transaction_type
        self._amount = amount
        self._currency = currency
        self._fee = Decimal("0.00")
        self._source_account_id = source_account_id
        self._target_account_id = target_account_id
        self._priority = priority
        self._scheduled_at = scheduled_at
        self._status = (
            TransactionStatus.SCHEDULED
            if scheduled_at is not None
            else TransactionStatus.PENDING
        )
        self._failure_reason = None
        self._attempts = 0
        self._created_at = created_at or self._time_provider()
        self._updated_at = self._created_at
        self._completed_at = None

    @staticmethod
    def _validate_participants(transaction_type, source_account_id, target_account_id):
        needs_source, needs_target = PARTICIPANTS[transaction_type]

        if needs_source and not source_account_id:
            raise InvalidOperationError("Source account is required")

        if not needs_source and source_account_id:
            raise InvalidOperationError(
                f"{transaction_type} cannot have a source account"
            )

        if needs_target and not target_account_id:
            raise InvalidOperationError("Target account is required")

        if not needs_target and target_account_id:
            raise InvalidOperationError(
                f"{transaction_type} cannot have a target account"
            )

        if needs_source and needs_target and source_account_id == target_account_id:
            raise InvalidOperationError("Transfer requires two different accounts")

    @property
    def transaction_id(self):
        return self._transaction_id

    @property
    def transaction_type(self):
        return self._transaction_type

    @property
    def amount(self):
        return self._amount

    @property
    def currency(self):
        return self._currency

    @property
    def fee(self):
        return self._fee

    @property
    def total_amount(self):
        return self._amount + self._fee

    @property
    def source_account_id(self):
        return self._source_account_id

    @property
    def target_account_id(self):
        return self._target_account_id

    @property
    def priority(self):
        return self._priority

    @property
    def priority_rank(self):
        return PRIORITY_RANK[self._priority]

    @property
    def scheduled_at(self):
        return self._scheduled_at

    @property
    def status(self):
        return self._status

    @property
    def failure_reason(self):
        return self._failure_reason

    @property
    def attempts(self):
        return self._attempts

    @property
    def created_at(self):
        return self._created_at

    @property
    def updated_at(self):
        return self._updated_at

    @property
    def completed_at(self):
        return self._completed_at

    @property
    def is_open(self):
        return self._status in OPEN_STATUSES

    def is_ready(self, now: datetime):
        if self._status not in OPEN_STATUSES:
            return False

        return self._scheduled_at is None or self._scheduled_at <= now

    def apply_fee(self, fee: int | Decimal):
        if self._status is not TransactionStatus.PROCESSING:
            raise InvalidOperationError("Fee can only be applied while processing")

        self._fee = to_non_negative_money(fee, "Fee cannot be negative")
        self._touch()

    def register_attempt(self, now: datetime = None):
        self._attempts += 1
        self._touch(now)

    def mark_processing(self, now: datetime = None):
        if not self.is_open:
            raise InvalidOperationError("Only an open transaction can be processed")

        self._status = TransactionStatus.PROCESSING
        self._touch(now)

    def mark_completed(self, now: datetime = None):
        if self._status is not TransactionStatus.PROCESSING:
            raise InvalidOperationError("Only a processing transaction can complete")

        self._status = TransactionStatus.COMPLETED
        self._failure_reason = None
        self._completed_at = now or self._time_provider()
        self._touch(self._completed_at)

    def mark_failed(self, reason: str, now: datetime = None):
        if not isinstance(reason, str) or not reason.strip():
            raise InvalidOperationError("Failure reason is required")

        if self._status in (TransactionStatus.COMPLETED, TransactionStatus.CANCELLED):
            raise InvalidOperationError("Finished transaction cannot fail")

        self._status = TransactionStatus.FAILED
        self._failure_reason = reason.strip()
        self._touch(now)

    def cancel(self, reason: str = "Cancelled by user", now: datetime = None):
        if not self.is_open:
            raise InvalidOperationError("Only an open transaction can be cancelled")

        self._status = TransactionStatus.CANCELLED
        self._failure_reason = reason
        self._touch(now)

    def _touch(self, now: datetime = None):
        self._updated_at = now or self._time_provider()

    def __str__(self):
        return (
            f"Transaction {self._transaction_id[:8]} "
            f"{self._transaction_type} {self._amount} {self._currency} "
            f"fee={self._fee} status={self._status}"
        )
