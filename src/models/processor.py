from contextlib import contextmanager
from datetime import datetime
from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal

from loguru import logger

from src.enums import Currency, TransactionType
from src.exceptions import (
    AuditWriteError,
    InvalidOperationError,
    TransientTransactionError,
)
from src.money import MONEY_PRECISION
from src.models.queue import TransactionQueue

EXTERNAL_TRANSFER_FEE_RATE = Decimal("0.01")
MIN_EXTERNAL_TRANSFER_FEE = Decimal("50")
DEFAULT_MAX_ATTEMPTS = 3


class TransactionProcessor:
    """Исполняет транзакции: комиссии, конвертация, повторы, журнал ошибок.

    Курсы берутся у банка. При пересчёте валют зачисление округляется вниз,
    списание вверх: доля копейки остаётся у банка, а не возникает из воздуха.
    """

    def __init__(
        self,
        bank,
        external_gateway=None,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        time_provider=None,
    ):
        if not isinstance(max_attempts, int) or max_attempts < 1:
            raise InvalidOperationError("Max attempts must be a positive integer")

        self._bank = bank
        self._converter = bank.converter
        self._external_gateway = external_gateway or (lambda transaction: None)
        self._max_attempts = max_attempts
        self._time_provider = time_provider or bank.time_provider
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
        minimum_fee = self._converter.convert(
            MIN_EXTERNAL_TRANSFER_FEE, Currency.RUB, transaction.currency
        )
        return max(percent_fee, minimum_fee).quantize(
            MONEY_PRECISION, rounding=ROUND_HALF_UP
        )

    def process(self, transaction, now: datetime = None):
        moment = now or self._time_provider()
        transaction.mark_processing(now=moment)
        last_error = None

        try:
            # Риск считается один раз на транзакцию, а не на каждую попытку:
            # иначе повторы сами раздували бы частоту операций клиента.
            self._bank.assess_transaction(transaction, now=moment)
        except Exception as error:
            self._record_error(transaction, error, moment)
            self._fail(transaction, error, moment)
            return False

        for _ in range(self._max_attempts):
            transaction.register_attempt(now=moment)

            try:
                self._execute(transaction, moment)
            except TransientTransactionError as error:
                last_error = error
                self._record_error(transaction, error, moment)
                continue
            except Exception as error:
                # Никакая ошибка не должна утащить транзакцию из очереди
                # в вечный PROCESSING. Ловим всё, пишем в журнал, помечаем failed.
                self._record_error(transaction, error, moment)
                self._fail(transaction, error, moment)
                return False
            else:
                transaction.mark_completed(now=moment)
                logger.info(
                    "transaction_completed",
                    transaction_id=transaction.transaction_id,
                    transaction_type=transaction.transaction_type,
                    amount=transaction.amount,
                    fee=transaction.fee,
                    currency=transaction.currency,
                )
                self._record_outcome(transaction)
                return True

        self._fail(transaction, last_error, moment)
        return False

    def process_queue(self, queue, now: datetime = None):
        if not isinstance(queue, TransactionQueue):
            raise InvalidOperationError("Invalid transaction queue")

        moment = now or self._time_provider()
        summary = {"processed": 0, "completed": 0, "failed": 0}

        while True:
            transaction = queue.pop_ready(moment)

            if transaction is None:
                break

            summary["processed"] += 1

            if self.process(transaction, now=moment):
                summary["completed"] += 1
            else:
                summary["failed"] += 1

        return summary

    def _execute(self, transaction, moment):
        self._bank.ensure_can_process(transaction, now=moment)
        transaction.apply_fee(self.calculate_fee(transaction))
        self._handlers[transaction.transaction_type](transaction)

    def _execute_deposit(self, transaction):
        target = self._bank.get_account(transaction.target_account_id)
        target.deposit(self._credited(transaction.amount, transaction, target))

    def _execute_withdrawal(self, transaction):
        source = self._bank.get_account(transaction.source_account_id)
        source.withdraw(self._debited(transaction.total_amount, transaction, source))

    def _execute_transfer(self, transaction):
        source = self._bank.get_account(transaction.source_account_id)
        target = self._bank.get_account(transaction.target_account_id)

        source.ensure_operational("send money")
        target.ensure_operational("receive money")

        credited = self._credited(transaction.amount, transaction, target)

        if credited <= 0:
            raise InvalidOperationError("Amount is too small for the target currency")

        # Получатель принимает целые копейки своей валюты (округление вниз),
        # отправитель платит их стоимость (вверх): не больше запрошенной суммы
        # и не меньше того, что получил получатель.
        sent = self._converter.convert(
            credited, target.currency, source.currency, rounding=ROUND_UP
        )
        fee = self._debited(transaction.fee, transaction, source)

        with self._debit(source, sent + fee):
            target.deposit(credited)

    def _execute_external_transfer(self, transaction):
        source = self._bank.get_account(transaction.source_account_id)
        total = self._debited(transaction.total_amount, transaction, source)

        with self._debit(source, total):
            self._external_gateway(transaction)

    @contextmanager
    def _debit(self, source, total):
        """Списывает деньги и возвращает их, если дальнейший шаг не удался.

        Возвращается ровно то, что реально ушло со счёта: у премиум-счёта
        сверх суммы списывается ещё и его собственная комиссия.
        """
        debited = source.withdraw(total)

        try:
            yield
        except Exception:
            try:
                source.refund(debited)
            except Exception as refund_error:
                logger.error(
                    "refund_failed",
                    account_id=source.account_id,
                    amount=debited,
                    reason=str(refund_error),
                )

            raise

    def _credited(self, amount, transaction, account):
        return self._converter.convert(
            amount, transaction.currency, account.currency, rounding=ROUND_DOWN
        )

    def _debited(self, amount, transaction, account):
        return self._converter.convert(
            amount, transaction.currency, account.currency, rounding=ROUND_UP
        )

    def _fail(self, transaction, error, moment):
        transaction.mark_failed(self._reason(error), now=moment)
        logger.warning(
            "transaction_rejected",
            transaction_id=transaction.transaction_id,
            transaction_type=transaction.transaction_type,
            amount=transaction.amount,
            currency=transaction.currency,
            error=type(error).__name__,
            reason=transaction.failure_reason,
        )

        self._record_outcome(transaction, error)

    def _record_outcome(self, transaction, error=None):
        """Итог уже наступил: сбой журнала не должен ронять очередь."""
        try:
            self._bank.record_outcome(transaction, error)
        except (AuditWriteError, InvalidOperationError) as audit_error:
            logger.error(
                "audit_outcome_lost",
                transaction_id=transaction.transaction_id,
                status=str(transaction.status),
                reason=str(audit_error),
            )

    @staticmethod
    def _reason(error):
        """Причина отказа не бывает пустой: у ConnectionError() нет текста."""
        return str(error).strip() or type(error).__name__

    def _record_error(self, transaction, error, moment):
        self._errors.append(
            {
                "transaction_id": transaction.transaction_id,
                "transaction_type": str(transaction.transaction_type),
                "attempt": transaction.attempts,
                "error": type(error).__name__,
                "message": self._reason(error),
                "time": moment.isoformat(timespec="seconds"),
            }
        )
