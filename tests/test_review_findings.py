"""Регрессии, найденные коллегией ревьюеров."""

from datetime import datetime, timedelta
from decimal import ROUND_DOWN, Decimal

import pytest

from src.enums import AccountStatus, Currency, TransactionStatus, TransactionType
from src.exceptions import InvalidOperationError, TransientTransactionError
from src.models import Bank, Client, Transaction, TransactionProcessor, TransactionQueue

NOW = datetime(2026, 9, 10, 12, 0)
LATER = NOW + timedelta(days=1)


def at_now():
    return NOW


@pytest.fixture
def bank():
    bank = Bank("Review", time_provider=at_now)
    bank.add_client(Client(1, "Ivan Ivanov", 30, password="pw"))
    return bank


@pytest.fixture
def processor(bank):
    return TransactionProcessor(bank, time_provider=at_now)


def transfer(source, target, amount, currency=Currency.RUB):
    return Transaction(
        TransactionType.TRANSFER,
        Decimal(amount),
        currency,
        source_account_id=source.account_id,
        target_account_id=target.account_id,
    )


class TestCrossCurrencyConservesMoney:
    """Раньше округление списания и зачисления шло врозь и создавало деньги."""

    @pytest.fixture
    def rub(self, bank):
        return bank.open_account(1, "bank", balance=Decimal("1000"))

    @pytest.mark.parametrize("currency", [Currency.EUR, Currency.USD, Currency.KZT])
    @pytest.mark.parametrize("amount", ["0.50", "4", "5", "333.33", "100"])
    def test_single_transfer_keeps_the_total(
        self, bank, processor, rub, currency, amount
    ):
        target = bank.open_account(1, "bank", balance=Decimal("0"), currency=currency)
        before = bank.get_total_balance()

        processor.process(transfer(rub, target, amount))

        assert bank.get_total_balance() == before

    def test_a_thousand_transfers_keep_the_total(self, bank, processor, rub):
        eur = bank.open_account(1, "bank", balance=Decimal("0"), currency=Currency.EUR)
        before = bank.get_total_balance()

        for _ in range(1000):
            if not processor.process(transfer(rub, eur, "0.50")):
                break

        assert bank.get_total_balance() == before

    def test_sender_pays_what_the_receiver_gets(self, bank, processor, rub):
        eur = bank.open_account(1, "bank", balance=Decimal("0"), currency=Currency.EUR)

        processor.process(transfer(rub, eur, "1.50"))

        # 1.50 RUB = 0.015 EUR, зачисление вниз: 0.01 EUR, а стоит она ровно 1.00 RUB
        assert eur.balance == Decimal("0.01")
        assert rub.balance == Decimal("999.00")

    def test_amount_too_small_for_target_currency(self, bank, processor, rub):
        eur = bank.open_account(1, "bank", balance=Decimal("0"), currency=Currency.EUR)
        # 0.01 RUB это 0.0001 EUR - меньше копейки целевой валюты
        transaction = transfer(rub, eur, "0.01")

        assert processor.process(transaction) is False
        assert transaction.status is TransactionStatus.FAILED
        assert rub.balance == Decimal("1000.00")
        assert eur.balance == Decimal("0.00")

    def test_same_currency_is_unaffected(self, bank, processor, rub):
        other = bank.open_account(1, "bank", balance=Decimal("0"))
        before = bank.get_total_balance()

        processor.process(transfer(rub, other, "333.33"))

        assert bank.get_total_balance() == before
        assert rub.balance == Decimal("666.67")
        assert other.balance == Decimal("333.33")


class TestQueueAndProcessorShareOneClock:
    """Раньше очередь и процессор смотрели на разные часы и теряли транзакцию."""

    def test_caller_time_drives_both(self, bank, processor):
        account = bank.open_account(1, "bank", balance=Decimal("1000"))
        queue = TransactionQueue(time_provider=at_now)
        scheduled = Transaction(
            TransactionType.DEPOSIT,
            Decimal("100"),
            target_account_id=account.account_id,
            scheduled_at=LATER,
        )
        queue.add(scheduled)
        queue.add(
            Transaction(
                TransactionType.DEPOSIT,
                Decimal("50"),
                target_account_id=account.account_id,
            )
        )

        summary = processor.process_queue(queue, now=LATER)

        assert summary == {"processed": 2, "completed": 2, "failed": 0}
        assert account.balance == Decimal("1150.00")
        assert len(queue) == 0

    def test_scheduled_stays_when_its_time_has_not_come(self, bank, processor):
        account = bank.open_account(1, "bank", balance=Decimal("1000"))
        queue = TransactionQueue(time_provider=at_now)
        queue.add(
            Transaction(
                TransactionType.DEPOSIT,
                Decimal("100"),
                target_account_id=account.account_id,
                scheduled_at=LATER,
            )
        )

        summary = processor.process_queue(queue)

        assert summary == {"processed": 0, "completed": 0, "failed": 0}
        assert len(queue) == 1
        assert account.balance == Decimal("1000.00")


class TestRefundSurvivesAFreeze:
    """Раньше возврат упирался в статус, деньги пропадали, ошибка затиралась."""

    def test_money_comes_back_from_a_frozen_account(self, bank):
        source = bank.open_account(1, "bank", balance=Decimal("10000"))

        def freezing_gateway(transaction):
            source.status = AccountStatus.FROZEN
            raise TransientTransactionError("gateway rejected, nothing sent")

        processor = TransactionProcessor(
            bank, external_gateway=freezing_gateway, time_provider=at_now
        )
        transaction = Transaction(
            TransactionType.EXTERNAL_TRANSFER,
            Decimal("1000"),
            source_account_id=source.account_id,
        )

        assert processor.process(transaction) is False
        assert source.balance == Decimal("10000.00")

    def test_refund_ignores_account_status(self, bank):
        account = bank.open_account(1, "bank", balance=Decimal("100"))
        account.status = AccountStatus.FROZEN

        account.refund(Decimal("50"))

        assert account.balance == Decimal("150.00")

    def test_refund_still_validates_the_amount(self, bank):
        account = bank.open_account(1, "bank", balance=Decimal("100"))

        for bad in (Decimal("0"), Decimal("-1"), 0.5, "50"):
            with pytest.raises(InvalidOperationError):
                account.refund(bad)

        assert account.balance == Decimal("100.00")


class TestQueueDropsClosedTransactions:
    """Раньше отменённая транзакция оставалась в очереди навсегда."""

    @pytest.fixture
    def account(self, bank):
        return bank.open_account(1, "bank", balance=Decimal("1000"))

    def test_cancelled_leaves_the_queue(self, account, processor):
        queue = TransactionQueue(time_provider=at_now)
        zombie = Transaction(
            TransactionType.DEPOSIT,
            Decimal("100"),
            target_account_id=account.account_id,
        )
        alive = Transaction(
            TransactionType.DEPOSIT, Decimal("5"), target_account_id=account.account_id
        )
        queue.add(zombie)
        queue.add(alive)

        zombie.cancel("client changed mind")

        assert len(queue) == 1
        assert queue.pending == [alive]

        processor.process_queue(queue)

        assert len(queue) == 0
        assert account.balance == Decimal("1005.00")

    def test_failed_cancel_keeps_the_entry(self, account):
        queue = TransactionQueue(time_provider=at_now)
        transaction = Transaction(
            TransactionType.DEPOSIT, Decimal("5"), target_account_id=account.account_id
        )
        queue.add(transaction)
        transaction.mark_processing(now=NOW)
        transaction.mark_completed(now=NOW)

        with pytest.raises(InvalidOperationError):
            queue.cancel(transaction.transaction_id)

        assert transaction.transaction_id in queue

    def test_cancel_through_the_queue_still_works(self, account):
        queue = TransactionQueue(time_provider=at_now)
        transaction = Transaction(
            TransactionType.DEPOSIT, Decimal("5"), target_account_id=account.account_id
        )
        queue.add(transaction)

        queue.cancel(transaction.transaction_id)

        assert transaction.status is TransactionStatus.CANCELLED
        assert len(queue) == 0
        assert transaction.transaction_id not in queue


class TestConversionRounding:
    """Зачисление округляется вниз, списание вверх: банк не создаёт деньги."""

    @pytest.fixture
    def accounts(self, bank):
        def factory(source_currency, target_currency):
            source = bank.open_account(
                1, "bank", balance=Decimal("1000"), currency=source_currency
            )
            target = bank.open_account(
                1, "bank", balance=Decimal("0"), currency=target_currency
            )
            return source, target

        return factory

    def test_eur_to_usd_credits_down_and_debits_the_value(self, processor, accounts):
        eur, usd = accounts(Currency.EUR, Currency.USD)
        transaction_in_eur = Transaction(
            TransactionType.TRANSFER,
            Decimal("50"),
            currency=Currency.EUR,
            source_account_id=eur.account_id,
            target_account_id=usd.account_id,
        )

        processor.process(transaction_in_eur)

        # 50 EUR = 55.555 USD -> 55.55; они стоят 49.995 EUR -> списано 50.00
        assert usd.balance == Decimal("55.55")
        assert eur.balance == Decimal("950.00")

    @pytest.mark.parametrize(
        ("source_currency", "target_currency"),
        [
            (Currency.EUR, Currency.USD),
            (Currency.USD, Currency.EUR),
            (Currency.KZT, Currency.CNY),
            (Currency.USD, Currency.KZT),
            (Currency.CNY, Currency.RUB),
        ],
    )
    @pytest.mark.parametrize("amount", ["0.07", "1", "33.33", "50", "123.45"])
    def test_foreign_transfer_never_creates_money(
        self, bank, processor, accounts, source_currency, target_currency, amount
    ):
        source, target = accounts(source_currency, target_currency)
        before = bank.get_total_balance()
        transaction = Transaction(
            TransactionType.TRANSFER,
            Decimal(amount),
            currency=source_currency,
            source_account_id=source.account_id,
            target_account_id=target.account_id,
        )

        processor.process(transaction)

        paid = Decimal("1000") - source.balance
        received_value = bank.converter.convert(
            target.balance, target_currency, source_currency, rounding=ROUND_DOWN
        )
        assert paid <= Decimal(amount)
        assert received_value <= paid
        assert bank.get_total_balance() <= before

    def test_withdrawal_in_another_currency_rounds_the_debit_up(self, bank, processor):
        usd = bank.open_account(
            1, "bank", balance=Decimal("100"), currency=Currency.USD
        )
        withdrawal = Transaction(
            TransactionType.WITHDRAWAL, Decimal("100"), source_account_id=usd.account_id
        )

        processor.process(withdrawal)

        # 100 RUB = 1.111 USD -> списано 1.12
        assert usd.balance == Decimal("98.88")

    def test_deposit_in_another_currency_rounds_the_credit_down(self, bank, processor):
        usd = bank.open_account(1, "bank", balance=Decimal("0"), currency=Currency.USD)
        deposit = Transaction(
            TransactionType.DEPOSIT, Decimal("100"), target_account_id=usd.account_id
        )

        processor.process(deposit)

        # 100 RUB = 1.111 USD -> зачислено 1.11
        assert usd.balance == Decimal("1.11")
