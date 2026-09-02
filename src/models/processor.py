from contextlib import contextmanager
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

from src.enums import Currency, TransactionType
from src.exceptions import (
    InsufficientFundsError,
    InvalidOperationError,
    TransientTransactionError,
)
from src.money import MONEY_PRECISION
from src.models.exchange import CurrencyConverter
from src.models.queue import TransactionQueue

EXTERNAL_TRANSFER_FEE_RATE = Decimal("0.01")
MIN_EXTERNAL_TRANSFER_FEE = Decimal("50")
DEFAULT_MAX_ATTEMPTS = 3


class TransactionProcessor:
    """Исполняет транзакции: комиссии, конвертация, повторы, журнал ошибок."""

    def __init__(
        self,
        bank,
        exchange_rates: dict = None,
        external_gateway=None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        time_provider=None,
    ):
        if not isinstance(max_attempts, int) or max_attempts < 1:
            raise InvalidOperationError("Max attempts must be a positive integer")

        self._bank = bank
        self._converter = (
            CurrencyConverter(exchange_rates)
            if exchange_rates is not None
            else bank.converter
        )
        self._external_gateway = external_gateway or (lambda transaction: None)
        self._max_attempts = max_attempts
        self._time_provider = time_provider or datetime.now
        self._errors = []
        self._handlers = {
            TransactionType.DEPOSIT: self._execute_deposit,
            TransactionType.WITHDRAWAL: self._execute_withdrawal,
            TransactionType.TRANSFER: self._execute_transfer,
            TransactionType.EXTERNAL_TRANSFER: self._execute_external_transfer,
        }

    @property
    def errors(self):
        return [error.copy() for error in self._errors]

    def calculate_fee(self, transaction):
        if transaction.transaction_type is not TransactionType.EXTERNAL_TRANSFER:
            return Decimal("0.00")

        percent_fee = transaction.amount * EXTERNAL_TRANSFER_FEE_RATE
        minimum_fee = self.convert(
            MIN_EXTERNAL_TRANSFER_FEE, Currency.RUB, transaction.currency
        )
        return max(percent_fee, minimum_fee).quantize(
            MONEY_PRECISION, rounding=ROUND_HALF_UP
        )

    def convert(self, amount, from_currency, to_currency):
        return self._converter.convert(amount, from_currency, to_currency)

    def process(self, transaction):
        transaction.mark_processing(now=self._time_provider())
        last_error = None

        for _ in range(self._max_attempts):
            transaction.register_attempt(now=self._time_provider())

            try:
                self._execute(transaction)
            except TransientTransactionError as error:
                last_error = error
                self._record_error(transaction, error)
                continue
            except Exception as error:
                # Никакая ошибка не должна утащить транзакцию из очереди
                # в вечный PROCESSING. Ловим всё, пишем в журнал, помечаем failed.
                self._fail(transaction, error)
                return False
            else:
                transaction.mark_completed(now=self._time_provider())
                return True

        self._fail(transaction, last_error, already_logged=True)
        return False

    def process_queue(self, queue, now: datetime = None):
        if not isinstance(queue, TransactionQueue):
            raise InvalidOperationError("Invalid transaction queue")

        summary = {"processed": 0, "completed": 0, "failed": 0}

        while True:
            transaction = queue.pop_ready(now)

            if transaction is None:
                break

            summary["processed"] += 1

            if self.process(transaction):
                summary["completed"] += 1
            else:
                summary["failed"] += 1

        return summary

    def _execute(self, transaction):
        transaction.apply_fee(self.calculate_fee(transaction))
        self._handlers[transaction.transaction_type](transaction)

    def _execute_deposit(self, transaction):
        target = self._get_account(transaction.target_account_id)
        target.deposit(self._to_account_money(transaction.amount, transaction, target))

    def _execute_withdrawal(self, transaction):
        source = self._get_account(transaction.source_account_id)
        total = self._to_account_money(transaction.total_amount, transaction, source)

        self._ensure_can_send(source, total)
        source.withdraw(total)

    def _execute_transfer(self, transaction):
        source = self._get_account(transaction.source_account_id)
        target = self._get_account(transaction.target_account_id)

        source.ensure_operational("send money")
        target.ensure_operational("receive money")

        total = self._to_account_money(transaction.total_amount, transaction, source)
        credited = self._to_account_money(transaction.amount, transaction, target)

        with self._debit(source, total):
            target.deposit(credited)

    def _execute_external_transfer(self, transaction):
        source = self._get_account(transaction.source_account_id)
        total = self._to_account_money(transaction.total_amount, transaction, source)

        with self._debit(source, total):
            self._external_gateway(transaction)

    @contextmanager
    def _debit(self, source, total):
        """Списывает деньги и возвращает их, если дальнейший шаг не удался.

        Возвращается ровно то, что реально ушло со счёта: у премиум-счёта
        сверх суммы списывается ещё и его собственная комиссия.
        """
        self._ensure_can_send(source, total)
        debited = total + source.withdrawal_fee(total)
        source.withdraw(total)

        try:
            yield
        except Exception:
            source.deposit(debited)
            raise

    def _to_account_money(self, amount, transaction, account):
        return self.convert(amount, transaction.currency, account.currency)

    @staticmethod
    def _ensure_can_send(account, total):
        debited = total + account.withdrawal_fee(total)

        if account.balance - debited < account.min_allowed_balance:
            raise InsufficientFundsError("Transfer would break the balance limit")

    def _get_account(self, account_id):
        return self._bank.get_account(account_id)

    def _fail(self, transaction, error, already_logged=False):
        reason = str(error) if error else "Unknown processing error"
        transaction.mark_failed(reason, now=self._time_provider())

        if not already_logged:
            self._record_error(transaction, error)

    def _record_error(self, transaction, error):
        self._errors.append(
            {
                "transaction_id": transaction.transaction_id,
                "transaction_type": str(transaction.transaction_type),
                "attempt": transaction.attempts,
                "error": type(error).__name__ if error else "UnknownError",
                "message": str(error) if error else "Unknown processing error",
                "time": self._time_provider().isoformat(timespec="seconds"),
            }
        )
