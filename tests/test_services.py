from datetime import datetime
from decimal import Decimal

import pytest

from src.enums import AccountStatus, ClientStatus, Currency
from src.exceptions import InvalidOperationError
from src.models import CurrencyConverter
from src.models.accounts import BankAccount
from src.services import AuthService, BankAnalytics, FraudJournal

NIGHT = datetime(2026, 9, 4, 2, 0)
DAY = datetime(2026, 9, 4, 14, 0)


def at(moment):
    return lambda: moment


def make_account(balance="100", currency=Currency.RUB, status=AccountStatus.ACTIVE):
    account = BankAccount(
        user_id=1, owner_name="Max Petrov", balance=Decimal(balance), currency=currency
    )
    account.status = status
    return account


class TestFraudJournal:
    def test_records_an_action(self):
        journal = FraudJournal(time_provider=at(DAY))

        journal.record(1, "failed_login")

        assert journal.actions == [
            {
                "client_id": 1,
                "action": "failed_login",
                "time": "2026-09-04T14:00:00",
            }
        ]

    def test_starts_empty(self):
        assert FraudJournal().actions == []

    def test_actions_getter_returns_copies(self):
        journal = FraudJournal(time_provider=at(DAY))
        journal.record(1, "failed_login")

        journal.actions[0]["action"] = "hacked"

        assert journal.actions[0]["action"] == "failed_login"

    def test_flag_client_marks_and_records(self, make_client):
        journal = FraudJournal(time_provider=at(DAY))
        client = make_client()

        journal.flag_client(client, "failed_login")

        assert client.status is ClientStatus.SUSPICIOUS
        assert journal.actions[0]["action"] == "failed_login"

    @pytest.mark.parametrize("hour", [0, 1, 2, 3, 4])
    def test_night_hours_are_restricted(self, hour):
        journal = FraudJournal(time_provider=at(datetime(2026, 9, 4, hour, 0)))

        assert journal.is_restricted_time() is True

    @pytest.mark.parametrize("hour", [5, 9, 14, 23])
    def test_day_hours_are_free(self, hour):
        journal = FraudJournal(time_provider=at(datetime(2026, 9, 4, hour, 0)))

        assert journal.is_restricted_time() is False

    def test_night_attempt_raises_and_is_recorded(self):
        journal = FraudJournal(time_provider=at(NIGHT))

        with pytest.raises(InvalidOperationError):
            journal.ensure_allowed_time("open_account", client_id=7)

        assert journal.actions == [
            {
                "client_id": 7,
                "action": "restricted_time:open_account",
                "time": "2026-09-04T02:00:00",
            }
        ]

    def test_daytime_attempt_is_silent(self):
        journal = FraudJournal(time_provider=at(DAY))

        journal.ensure_allowed_time("open_account")

        assert journal.actions == []


class TestAuthService:
    @pytest.fixture
    def journal(self):
        return FraudJournal(time_provider=at(DAY))

    @pytest.fixture
    def auth(self, journal):
        return AuthService(journal)

    def test_correct_password(self, auth, make_client):
        assert auth.authenticate(make_client(), "secret-pass") is True

    def test_wrong_password_is_flagged(self, auth, journal, make_client):
        client = make_client()

        assert auth.authenticate(client, "wrong") is False
        assert client.failed_login_attempts == 1
        assert client.status is ClientStatus.SUSPICIOUS
        assert journal.actions[0]["action"] == "failed_login"

    def test_success_resets_the_counter(self, auth, make_client):
        client = make_client()
        auth.authenticate(client, "wrong")

        auth.authenticate(client, "secret-pass")

        assert client.failed_login_attempts == 0

    def test_third_failure_blocks_and_is_recorded(self, auth, journal, make_client):
        client = make_client()

        for _ in range(3):
            auth.authenticate(client, "wrong")

        assert client.status is ClientStatus.BLOCKED
        actions = [entry["action"] for entry in journal.actions]
        assert "client_blocked_after_failed_logins" in actions

    def test_blocked_client_cannot_authenticate(self, auth, journal, make_client):
        client = make_client()
        client.status = ClientStatus.BLOCKED

        with pytest.raises(InvalidOperationError):
            auth.authenticate(client, "secret-pass")

        assert journal.actions[0]["action"] == "blocked_client_login"

    def test_blocked_client_cannot_operate(self, auth, journal, make_client):
        client = make_client()
        client.status = ClientStatus.BLOCKED

        with pytest.raises(InvalidOperationError):
            auth.ensure_can_operate(client, "open_account")

        assert journal.actions[0]["action"] == "open_account"

    def test_active_client_may_operate(self, auth, make_client):
        auth.ensure_can_operate(make_client(), "open_account")


class TestBankAnalytics:
    @pytest.fixture
    def analytics(self):
        return BankAnalytics(CurrencyConverter())

    def test_sums_one_currency(self, analytics):
        accounts = [make_account("100"), make_account("250.50")]

        assert analytics.total_balance(accounts) == Decimal("350.50")

    def test_converts_other_currencies(self, analytics):
        accounts = [make_account("100"), make_account("1", Currency.USD)]

        assert analytics.total_balance(accounts) == Decimal("190.00")

    def test_reports_in_any_currency(self, analytics):
        assert analytics.total_balance([make_account("900")], Currency.USD) == Decimal(
            "10.00"
        )

    def test_ignores_closed_accounts(self, analytics):
        accounts = [
            make_account("100"),
            make_account("500", status=AccountStatus.CLOSED),
        ]

        assert analytics.total_balance(accounts) == Decimal("100.00")

    def test_empty_list(self, analytics):
        assert analytics.total_balance([]) == Decimal("0.00")

    def test_ranking_is_sorted_by_total(self, analytics, make_client):
        poor = make_client(client_id=1, full_name="Anna Ivanova")
        rich = make_client(client_id=2, full_name="Max Petrov")
        small = make_account("150")
        big = make_account("2", Currency.USD)
        poor.add_account_id(small.account_id)
        rich.add_account_id(big.account_id)
        accounts = {small.account_id: small, big.account_id: big}

        ranking = analytics.clients_ranking([poor, rich], accounts)

        assert [client.client_id for client, _ in ranking] == [2, 1]
        assert [total for _, total in ranking] == [Decimal("180.00"), Decimal("150.00")]

    def test_ranking_skips_unknown_account_ids(self, analytics, make_client):
        client = make_client()
        client.add_account_id("does-not-exist")

        ranking = analytics.clients_ranking([client], {})

        assert ranking == [(client, Decimal("0.00"))]
