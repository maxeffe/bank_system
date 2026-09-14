"""Оценка риска операции по четырём правилам."""

from bisect import bisect_left, bisect_right, insort
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

from src.enums import (
    AuditSeverity,
    Currency,
    RiskLevel,
    TransactionStatus,
    TransactionType,
)
from src.exceptions import InvalidOperationError
from src.money import to_positive_money

ASSESSMENT_EVENT = "transaction_assessed"
COMPLETED_EVENT = "transaction_completed"
FAILED_EVENT = "transaction_failed"

LARGE_AMOUNT_RULE = "large_amount"
FREQUENT_OPERATIONS_RULE = "frequent_operations"
NEW_COUNTERPARTY_RULE = "new_counterparty"
NIGHT_TIME_RULE = "night_time"

RULE_WEIGHTS = {
    LARGE_AMOUNT_RULE: 2,
    FREQUENT_OPERATIONS_RULE: 2,
    NEW_COUNTERPARTY_RULE: 1,
    NIGHT_TIME_RULE: 1,
}

SEVERITY_BY_LEVEL = {
    RiskLevel.LOW: AuditSeverity.INFO,
    RiskLevel.MEDIUM: AuditSeverity.WARNING,
    RiskLevel.HIGH: AuditSeverity.CRITICAL,
}

HOURS_IN_DAY = 24
DEFAULT_LARGE_AMOUNT = Decimal("100000")
DEFAULT_MAX_OPERATIONS = 5
DEFAULT_WINDOW = timedelta(minutes=5)
DEFAULT_NIGHT_HOURS = range(0, 5)
DEFAULT_MEDIUM_SCORE = 2
DEFAULT_HIGH_SCORE = 4


@dataclass(frozen=True)
class RiskAssessment:
    """Результат проверки: уровень, сумма весов и сработавшие правила."""

    level: RiskLevel
    score: int
    reasons: tuple = field(default_factory=tuple)

    @property
    def is_blocked(self):
        return self.level is RiskLevel.HIGH

    @property
    def severity(self):
        return SEVERITY_BY_LEVEL[self.level]

    def __str__(self):
        reasons = ", ".join(self.reasons) if self.reasons else "none"
        return f"risk={self.level} score={self.score} reasons=[{reasons}]"


@dataclass
class ClientHistory:
    """Всё, что правилам нужно знать о клиенте, без перебора журнала."""

    operation_times: list = field(default_factory=list)
    counterparties: set = field(default_factory=set)


def _validate_night_hours(night_hours):
    try:
        hours = frozenset(night_hours)
    except TypeError:
        raise InvalidOperationError(
            "Night hours must be a collection of hours"
        ) from None

    for hour in hours:
        if isinstance(hour, bool) or not isinstance(hour, int):
            raise InvalidOperationError("Night hours must be integers")

        if not 0 <= hour < HOURS_IN_DAY:
            raise InvalidOperationError("Night hours must be between 0 and 23")

    return hours


class RiskAnalyzer:
    """Оценивает операции и пишет оценки и итоги в журнал аудита.

    Журнал - для отчётов и расследований. Для правил анализатор держит
    компактную историю клиента: иначе каждая оценка перебирала бы весь журнал,
    а подделка записи в журнале меняла бы решение о блокировке.
    """

    def __init__(
        self,
        audit_log,
        converter,
        large_amount=DEFAULT_LARGE_AMOUNT,
        max_operations: int = DEFAULT_MAX_OPERATIONS,
        window: timedelta = DEFAULT_WINDOW,
        night_hours=DEFAULT_NIGHT_HOURS,
        medium_score: int = DEFAULT_MEDIUM_SCORE,
        high_score: int = DEFAULT_HIGH_SCORE,
        base_currency=Currency.RUB,
        time_provider=None,
    ):
        self._large_amount = to_positive_money(
            large_amount, "Large amount threshold must be a positive number"
        )

        if not isinstance(max_operations, int) or max_operations < 1:
            raise InvalidOperationError("Max operations must be a positive integer")

        if not isinstance(window, timedelta) or window <= timedelta(0):
            raise InvalidOperationError("Window must be a positive timedelta")

        if not isinstance(medium_score, int) or not isinstance(high_score, int):
            raise InvalidOperationError("Score thresholds must be integers")

        if not 0 < medium_score <= high_score:
            raise InvalidOperationError(
                "Score thresholds must grow: 0 < medium <= high"
            )

        if not isinstance(base_currency, Currency):
            raise InvalidOperationError("Invalid currency")

        self._audit_log = audit_log
        self._converter = converter
        self._max_operations = max_operations
        self._window = window
        self._night_hours = _validate_night_hours(night_hours)
        self._medium_score = medium_score
        self._high_score = high_score
        self._base_currency = base_currency
        self._time_provider = time_provider or datetime.now
        self._histories = {}

    def assess(
        self, transaction, client_id=None, now: datetime = None
    ) -> RiskAssessment:
        """Считает риск, ничего не записывая."""
        moment = now or self._time_provider()
        reasons = []

        if self._is_large(transaction):
            reasons.append(LARGE_AMOUNT_RULE)

        if self._is_frequent(client_id, moment):
            reasons.append(FREQUENT_OPERATIONS_RULE)

        if self._is_new_counterparty(transaction, client_id):
            reasons.append(NEW_COUNTERPARTY_RULE)

        if moment.hour in self._night_hours:
            reasons.append(NIGHT_TIME_RULE)

        score = sum(RULE_WEIGHTS[reason] for reason in reasons)
        return RiskAssessment(self._level_for(score), score, tuple(reasons))

    def evaluate(self, transaction, client_id=None, now: datetime = None):
        """Считает риск, запоминает попытку и заносит оценку в аудит.

        Запись помечается тем же моментом, которым считали риск: иначе окно
        частоты смотрит в одни часы, а история живёт по другим.
        """
        moment = now or self._time_provider()
        assessment = self.assess(transaction, client_id, moment)

        self._audit_log.record(
            ASSESSMENT_EVENT,
            severity=assessment.severity,
            client_id=client_id,
            account_id=self._account_of(transaction),
            now=moment,
            transaction_id=transaction.transaction_id,
            transaction_type=str(transaction.transaction_type),
            amount=transaction.amount,
            currency=str(transaction.currency),
            target_account_id=transaction.target_account_id,
            risk_level=str(assessment.level),
            score=assessment.score,
            reasons=list(assessment.reasons),
        )

        if client_id is not None:
            insort(self._history(client_id).operation_times, moment)

        return assessment

    def record_outcome(self, transaction, client_id=None, error=None, balances=None):
        """Итог операции - в аудит. Знакомым получатель становится только тогда,
        когда перевод на него действительно прошёл.

        История обновляется до записи в журнал: деньги уже ушли, и сбой
        журнала не должен снова сделать получателя новым.
        """
        completed = transaction.status is TransactionStatus.COMPLETED

        if not completed and transaction.status is not TransactionStatus.FAILED:
            raise InvalidOperationError("Only a finished transaction has an outcome")

        is_transfer = transaction.transaction_type is TransactionType.TRANSFER

        if completed and is_transfer and client_id is not None:
            self._history(client_id).counterparties.add(transaction.target_account_id)

        self._audit_log.record(
            COMPLETED_EVENT if completed else FAILED_EVENT,
            client_id=client_id,
            account_id=self._account_of(transaction),
            now=transaction.updated_at,
            transaction_id=transaction.transaction_id,
            transaction_type=str(transaction.transaction_type),
            amount=transaction.amount,
            fee=transaction.fee,
            currency=str(transaction.currency),
            source_account_id=transaction.source_account_id,
            target_account_id=transaction.target_account_id,
            error=type(error).__name__ if error is not None else None,
            reason=transaction.failure_reason,
            balances=dict(balances or {}),
        )

    def _history(self, client_id):
        return self._histories.setdefault(client_id, ClientHistory())

    @staticmethod
    def _account_of(transaction):
        """Счёт владельца денег: отправитель, а для пополнения - получатель."""
        return transaction.source_account_id or transaction.target_account_id

    def _level_for(self, score):
        if score >= self._high_score:
            return RiskLevel.HIGH

        if score >= self._medium_score:
            return RiskLevel.MEDIUM

        return RiskLevel.LOW

    def _is_large(self, transaction):
        in_base = self._converter.convert(
            transaction.amount, transaction.currency, self._base_currency
        )
        return in_base > self._large_amount

    def _is_frequent(self, client_id, moment):
        if client_id is None or client_id not in self._histories:
            return False

        times = self._histories[client_id].operation_times
        recent = bisect_right(times, moment) - bisect_left(times, moment - self._window)
        return recent >= self._max_operations

    def _is_new_counterparty(self, transaction, client_id):
        if transaction.transaction_type is not TransactionType.TRANSFER:
            return False

        if client_id is None or client_id not in self._histories:
            return True

        known = self._histories[client_id].counterparties
        return transaction.target_account_id not in known
