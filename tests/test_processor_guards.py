"""Проверки, которые процессор обязан сделать до движения денег."""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from src.enums import ClientStatus, TransactionStatus, TransactionType
from src.exceptions import InvalidOperationError
from src.models import Bank, Transaction, TransactionProcessor
from src.services.fraud import FRAUD_EVENT

DAYTIME = datetime(2026, 9, 3, 12, 0)
NIGHT = datetime(2026, 9, 3, 2, 0)


class Clock:
    """Часы, которые можно перевести посреди теста."""

    def __init__(self, moment):
        self.moment = moment

    def __call__(self):
        return self.moment


@pytest.fixture
def clock():
    return Clock(DAYTIME)


@pytest.fixture
def guarded_bank(clock, make_client):
    bank = Bank("Guarded", time_provider=clock)
    bank.add_client(make_client(client_id=1, full_name="Max Petrov"))
    bank.add_client(make_client(client_id=2, full_name="Anna Ivanova"))
    return bank


@pytest.fixture
def source(guarded_bank):
    return guarded_bank.open_account(1, "bank", balance=Decimal("10000"))


@pytest.fixture
def target(guarded_bank):
    return guarded_bank.open_account(2, "bank", balance=Decimal("0"))


@pytest.fixture
def processor(guarded_bank, clock):
    return TransactionProcessor(guarded_bank, time_provider=clock)


def transfer(source, target, amount="5000", **kwargs):
    return Transaction(
        TransactionType.TRANSFER,
        Decimal(amount),
        source_account_id=source.account_id,
        target_account_id=target.account_id,
        **kwargs,
    )


class TestBlockedClient:
    def test_blocked_sender_record_uses_the_process_moment(
        self, processor, guarded_bank, source, target
    ):
        guarded_bank.clients[1].status = ClientStatus.BLOCKED
        later = DAYTIME + timedelta(hours=1)

        processor.process(transfer(source, target), now=later)

        times = [record["time"] for record in guarded_bank.audit_log.records]
        assert guarded_bank.audit_log.filter(event=FRAUD_EVENT)[-1]["time"] == later
        assert times == sorted(times)

    def test_blocked_owner_cannot_send(self, processor, guarded_bank, source, target):
        guarded_bank.clients[1].status = ClientStatus.BLOCKED
        transaction = transfer(source, target)

        assert processor.process(transaction) is False
        assert transaction.status is TransactionStatus.FAILED
        assert source.balance == Decimal("10000.00")
        assert target.balance == Decimal("0.00")

    def test_blocked_owner_cannot_withdraw(self, processor, guarded_bank, source):
        guarded_bank.clients[1].status = ClientStatus.BLOCKED
        transaction = Transaction(
            TransactionType.WITHDRAWAL,
            Decimal("100"),
            source_account_id=source.account_id,
        )

        assert processor.process(transaction) is False
        assert source.balance == Decimal("10000.00")

    def test_blocked_owner_cannot_send_abroad(self, processor, guarded_bank, source):
        guarded_bank.clients[1].status = ClientStatus.BLOCKED
        transaction = Transaction(
            TransactionType.EXTERNAL_TRANSFER,
            Decimal("100"),
            source_account_id=source.account_id,
        )

        assert processor.process(transaction) is False
        assert source.balance == Decimal("10000.00")

    def test_blocked_recipient_still_receives(
        self, processor, guarded_bank, source, target
    ):
        guarded_bank.clients[2].status = ClientStatus.BLOCKED

        assert processor.process(transfer(source, target)) is True
        assert target.balance == Decimal("5000.00")

    def test_active_owner_may_send(self, processor, source, target):
        assert processor.process(transfer(source, target)) is True
        assert source.balance == Decimal("5000.00")


class TestRestrictedTime:
    def test_ban_follows_the_process_moment(self, processor, source, target):
        transaction = transfer(source, target)

        assert processor.process(transaction, now=NIGHT) is False
        assert (
            transaction.failure_reason == "Operations are blocked from 00:00 to 05:00"
        )
        assert source.balance == Decimal("10000.00")

    def test_daytime_moment_is_not_banned_by_a_night_clock(
        self, processor, clock, source, target
    ):
        clock.moment = NIGHT

        assert processor.process(transfer(source, target), now=DAYTIME) is True

    def test_night_record_names_the_client_and_the_moment(
        self, processor, guarded_bank, source, target
    ):
        processor.process(transfer(source, target), now=NIGHT)

        record = guarded_bank.audit_log.filter(event=FRAUD_EVENT)[-1]
        assert record["time"] == NIGHT
        assert record in guarded_bank.get_suspicious_operations(1)

    def test_night_transfer_is_refused(self, processor, clock, source, target):
        clock.moment = NIGHT
        transaction = transfer(source, target)

        assert processor.process(transaction) is False
        assert transaction.status is TransactionStatus.FAILED
        assert source.balance == Decimal("10000.00")
        assert target.balance == Decimal("0.00")

    def test_night_attempt_is_recorded(
        self, processor, guarded_bank, clock, source, target
    ):
        clock.moment = NIGHT

        processor.process(transfer(source, target))

        actions = [entry["action"] for entry in guarded_bank.suspicious_actions]
        assert "restricted_time:process_transaction" in actions

    @pytest.mark.parametrize("hour", [0, 4])
    def test_every_restricted_hour(self, processor, clock, source, target, hour):
        clock.moment = datetime(2026, 9, 3, hour, 30)

        assert processor.process(transfer(source, target)) is False

    @pytest.mark.parametrize("hour", [5, 12, 23])
    def test_daytime_hours_are_fine(self, processor, clock, source, target, hour):
        clock.moment = datetime(2026, 9, 3, hour, 30)

        assert processor.process(transfer(source, target)) is True


class TestScheduledTransactions:
    def test_future_transaction_cannot_be_processed(
        self, processor, clock, source, target
    ):
        transaction = transfer(
            source, target, scheduled_at=clock.moment + timedelta(days=30)
        )

        with pytest.raises(InvalidOperationError, match="not due yet"):
            processor.process(transaction)

        assert transaction.status is TransactionStatus.SCHEDULED
        assert source.balance == Decimal("10000.00")
        assert target.balance == Decimal("0.00")

    def test_transaction_runs_once_its_time_comes(
        self, processor, clock, source, target
    ):
        due = clock.moment + timedelta(days=30)
        transaction = transfer(source, target, scheduled_at=due)

        clock.moment = due

        assert processor.process(transaction) is True
        assert transaction.status is TransactionStatus.COMPLETED
        assert target.balance == Decimal("5000.00")

    def test_mark_processing_itself_refuses_early(self, clock, source, target):
        transaction = transfer(
            source, target, scheduled_at=clock.moment + timedelta(hours=1)
        )

        with pytest.raises(InvalidOperationError, match="not due yet"):
            transaction.mark_processing(now=clock.moment)

    def test_plain_transaction_is_always_due(self, processor, source, target):
        assert processor.process(transfer(source, target)) is True
