from datetime import datetime
from decimal import Decimal

import pytest

from src.enums import AccountStatus, ClientStatus, Currency, TransactionType
from src.exceptions import AccountClosedError, InvalidOperationError
from src.models import Bank, CurrencyConverter, Transaction, TransactionProcessor
from src.models.accounts import (
    BankAccount,
    InvestmentAccount,
    PremiumAccount,
    SavingsAccount,
)


class TestBankCreation:
    def test_creates_with_name(self):
        bank = Bank("Test Bank")

        assert bank.name == "Test Bank"
        assert bank.clients == {}
        assert bank.accounts == {}
        assert bank.suspicious_actions == []

    @pytest.mark.parametrize("name", ["", "   ", None, 123])
    def test_rejects_invalid_name(self, name):
        with pytest.raises(InvalidOperationError):
            Bank(name)


class TestAddClient:
    def test_adds_client(self, bank, make_client):
        client = make_client()

        bank.add_client(client)

        assert bank.clients == {1: client}

    def test_rejects_duplicate_id(self, bank, make_client):
        bank.add_client(make_client(client_id=1))

        with pytest.raises(InvalidOperationError):
            bank.add_client(make_client(client_id=1, full_name="Someone Else"))

    def test_rejects_non_client(self, bank):
        with pytest.raises(InvalidOperationError):
            bank.add_client("not a client")

    def test_clients_getter_returns_a_copy(self, bank_with_client):
        bank_with_client.clients.clear()

        assert len(bank_with_client.clients) == 1


class TestOpenAccount:
    @pytest.mark.parametrize(
        "account_type, expected",
        [
            ("bank", BankAccount),
            ("savings", SavingsAccount),
            ("premium", PremiumAccount),
            ("investment", InvestmentAccount),
        ],
    )
    def test_opens_every_type(self, bank_with_client, account_type, expected):
        account = bank_with_client.open_account(1, account_type)

        assert type(account) is expected

    def test_account_is_linked_to_bank_and_client(self, bank_with_client):
        account = bank_with_client.open_account(1, "bank", balance=Decimal("100"))

        assert bank_with_client.accounts[account.account_id] is account
        assert bank_with_client.search_accounts(client_id=1) == [account]
        assert bank_with_client.clients[1].account_ids == [account.account_id]
        assert account.owner_name == "Max Petrov"

    def test_rejects_duplicate_account_id(self, transfer_bank):
        transfer_bank.open_account(1, "bank", account_id="ABC123")

        with pytest.raises(InvalidOperationError):
            transfer_bank.open_account(2, "bank", account_id="ABC123")

    def test_duplicate_attempt_changes_nothing(self, transfer_bank):
        original = transfer_bank.open_account(
            1, "bank", account_id="ABC123", balance=Decimal("50000")
        )
        accounts_before = len(transfer_bank.accounts)
        total_before = transfer_bank.get_total_balance()

        with pytest.raises(InvalidOperationError):
            transfer_bank.open_account(
                2, "bank", account_id="ABC123", balance=Decimal("10")
            )

        assert transfer_bank.accounts["ABC123"] is original
        assert original.balance == Decimal("50000.00")
        assert len(transfer_bank.accounts) == accounts_before
        assert transfer_bank.get_total_balance() == total_before
        client_accounts = transfer_bank.search_accounts(client_id=2)
        assert "ABC123" not in [account.account_id for account in client_accounts]
        assert "ABC123" not in transfer_bank.clients[2].account_ids

    def test_rejects_unknown_type(self, bank_with_client):
        with pytest.raises(InvalidOperationError):
            bank_with_client.open_account(1, "crypto")

    def test_rejects_unknown_client(self, bank):
        with pytest.raises(InvalidOperationError):
            bank.open_account(999, "bank")

    def test_blocked_client_cannot_open_account(self, bank_with_client):
        bank_with_client.clients[1].status = ClientStatus.BLOCKED

        with pytest.raises(InvalidOperationError):
            bank_with_client.open_account(1, "bank")

    def test_blocked_attempt_is_recorded(self, bank_with_client):
        bank_with_client.clients[1].status = ClientStatus.BLOCKED

        with pytest.raises(InvalidOperationError):
            bank_with_client.open_account(1, "bank")

        actions = [item["action"] for item in bank_with_client.suspicious_actions]
        assert "open_account" in actions


class TestAccountLifecycle:
    def test_closes_empty_account(self, bank_with_client):
        account = bank_with_client.open_account(1, "bank")

        bank_with_client.close_account(account.account_id)

        assert account.status is AccountStatus.CLOSED

    def test_cannot_close_account_with_money(self, bank_with_client):
        account = bank_with_client.open_account(1, "bank", balance=Decimal("500"))

        with pytest.raises(InvalidOperationError):
            bank_with_client.close_account(account.account_id)

        assert account.status is AccountStatus.ACTIVE

    def test_cannot_close_account_in_debt(self, bank_with_client):
        account = bank_with_client.open_account(
            1, "premium", balance=Decimal("100"), overdraft_limit=Decimal("500")
        )
        account.withdraw(Decimal("300"))

        with pytest.raises(InvalidOperationError):
            bank_with_client.close_account(account.account_id)

    def test_freeze_and_unfreeze(self, account, bank_with_client):
        bank_with_client.freeze_account(account.account_id)
        assert account.status is AccountStatus.FROZEN

        bank_with_client.unfreeze_account(account.account_id)
        assert account.status is AccountStatus.ACTIVE

    def test_closed_account_cannot_be_unfrozen(self, bank_with_client):
        account = bank_with_client.open_account(1, "bank")
        bank_with_client.close_account(account.account_id)

        with pytest.raises(InvalidOperationError):
            bank_with_client.unfreeze_account(account.account_id)

    def test_closed_account_cannot_be_frozen(self, bank_with_client):
        account = bank_with_client.open_account(1, "bank")
        bank_with_client.close_account(account.account_id)

        with pytest.raises(InvalidOperationError):
            bank_with_client.freeze_account(account.account_id)

        assert account.status is AccountStatus.CLOSED

    def test_closed_account_cannot_be_revived_through_freezing(self, bank_with_client):
        account = bank_with_client.open_account(1, "bank")
        bank_with_client.close_account(account.account_id)

        with pytest.raises(InvalidOperationError):
            bank_with_client.freeze_account(account.account_id)

        with pytest.raises(InvalidOperationError):
            bank_with_client.unfreeze_account(account.account_id)

        with pytest.raises(AccountClosedError):
            account.deposit(Decimal("1000"))

        assert account.status is AccountStatus.CLOSED
        assert account.balance == Decimal("0.00")

    def test_unknown_account_id(self, bank):
        with pytest.raises(InvalidOperationError):
            bank.freeze_account("nope")


class TestAuthentication:
    def test_correct_password(self, bank_with_client):
        assert bank_with_client.authenticate_client(1, "secret-pass") is True

    def test_wrong_password(self, bank_with_client):
        assert bank_with_client.authenticate_client(1, "wrong") is False
        assert bank_with_client.clients[1].failed_login_attempts == 1

    def test_success_resets_counter(self, bank_with_client):
        bank_with_client.authenticate_client(1, "wrong")

        bank_with_client.authenticate_client(1, "secret-pass")

        assert bank_with_client.clients[1].failed_login_attempts == 0

    def test_three_failures_block_client(self, bank_with_client):
        for _ in range(3):
            bank_with_client.authenticate_client(1, "wrong")

        assert bank_with_client.clients[1].status is ClientStatus.BLOCKED

    def test_blocked_client_cannot_authenticate(self, bank_with_client):
        for _ in range(3):
            bank_with_client.authenticate_client(1, "wrong")

        with pytest.raises(InvalidOperationError):
            bank_with_client.authenticate_client(1, "secret-pass")

    def test_failed_login_marks_client_suspicious(self, bank_with_client):
        bank_with_client.authenticate_client(1, "wrong")

        assert bank_with_client.clients[1].status is ClientStatus.SUSPICIOUS
        actions = [item["action"] for item in bank_with_client.suspicious_actions]
        assert "failed_login" in actions

    def test_blocking_is_recorded(self, bank_with_client):
        for _ in range(3):
            bank_with_client.authenticate_client(1, "wrong")

        actions = [item["action"] for item in bank_with_client.suspicious_actions]
        assert "client_blocked_after_failed_logins" in actions


class TestSearchAccounts:
    @pytest.fixture
    def filled_bank(self, bank_with_client):
        bank_with_client.open_account(1, "bank", balance=Decimal("100"))
        bank_with_client.open_account(1, "savings", balance=Decimal("200"))
        bank_with_client.open_account(1, "premium", balance=Decimal("300"))
        bank_with_client.open_account(
            1, "investment", balance=Decimal("400"), currency=Currency.USD
        )
        return bank_with_client

    @pytest.mark.parametrize(
        "account_type", ["bank", "savings", "premium", "investment"]
    )
    def test_matches_exact_type_only(self, filled_bank, account_type):
        found = filled_bank.search_accounts(account_type=account_type)

        assert len(found) == 1

    def test_filters_by_status(self, filled_bank):
        account = filled_bank.search_accounts(account_type="bank")[0]
        filled_bank.freeze_account(account.account_id)

        frozen = filled_bank.search_accounts(status=AccountStatus.FROZEN)

        assert frozen == [account]

    def test_filters_by_currency(self, filled_bank):
        found = filled_bank.search_accounts(currency=Currency.USD)

        assert len(found) == 1
        assert found[0].currency is Currency.USD

    def test_filters_by_client(self, filled_bank, make_client):
        filled_bank.add_client(make_client(client_id=2, full_name="Anna Ivanova"))
        filled_bank.open_account(2, "bank")

        assert len(filled_bank.search_accounts(client_id=1)) == 4
        assert len(filled_bank.search_accounts(client_id=2)) == 1

    def test_combined_filters(self, filled_bank):
        found = filled_bank.search_accounts(
            client_id=1, account_type="savings", status=AccountStatus.ACTIVE
        )

        assert len(found) == 1
        assert type(found[0]) is SavingsAccount

    def test_no_filters_returns_everything(self, filled_bank):
        assert len(filled_bank.search_accounts()) == 4

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"account_type": "crypto"},
            {"status": "active"},
            {"currency": "rub"},
        ],
    )
    def test_rejects_invalid_filters(self, filled_bank, kwargs):
        with pytest.raises(InvalidOperationError):
            filled_bank.search_accounts(**kwargs)


class TestReports:
    def test_total_balance_sums_open_accounts(self, bank_with_client):
        bank_with_client.open_account(1, "bank", balance=Decimal("100"))
        bank_with_client.open_account(1, "savings", balance=Decimal("250.50"))

        assert bank_with_client.get_total_balance() == Decimal("350.50")

    def test_total_balance_ignores_closed(self, bank_with_client):
        bank_with_client.open_account(1, "bank", balance=Decimal("100"))
        closed = bank_with_client.open_account(1, "savings")
        bank_with_client.close_account(closed.account_id)
        closed.refund(Decimal("50"))

        assert bank_with_client.get_total_balance() == Decimal("100.00")

    def test_total_balance_of_empty_bank(self, bank):
        assert bank.get_total_balance() == Decimal("0.00")

    def test_total_balance_converts_other_currencies(self, bank_with_client):
        bank_with_client.open_account(1, "bank", balance=Decimal("100"))
        bank_with_client.open_account(
            1, "bank", balance=Decimal("1"), currency=Currency.USD
        )

        assert bank_with_client.get_total_balance() == Decimal("190.00")

    def test_total_balance_can_be_reported_in_any_currency(self, bank_with_client):
        bank_with_client.open_account(1, "bank", balance=Decimal("900"))

        assert bank_with_client.get_total_balance(Currency.USD) == Decimal("10.00")

    def test_ranking_counts_foreign_accounts(self, bank, make_client):
        bank.add_client(make_client(client_id=1, full_name="Max Petrov"))
        bank.add_client(make_client(client_id=2, full_name="Anna Ivanova"))
        bank.open_account(1, "bank", balance=Decimal("100"))
        bank.open_account(1, "bank", balance=Decimal("1"), currency=Currency.USD)
        bank.open_account(2, "bank", balance=Decimal("150"))

        ranking = bank.get_clients_ranking()

        assert [client.client_id for client, _ in ranking] == [1, 2]
        assert [total for _, total in ranking] == [
            Decimal("190.00"),
            Decimal("150.00"),
        ]

    def test_reports_use_the_injected_converter(self, make_client, daytime):
        converter = CurrencyConverter(
            {Currency.RUB: Decimal("1"), Currency.USD: Decimal("2")}
        )
        bank = Bank("Custom", converter=converter, time_provider=daytime)
        bank.add_client(make_client())
        bank.open_account(1, "bank", balance=Decimal("1"), currency=Currency.USD)

        assert bank.converter is converter
        assert bank.get_total_balance() == Decimal("2.00")

    def test_ranking_is_sorted_by_balance(self, bank, make_client):
        bank.add_client(make_client(client_id=1, full_name="Max Petrov"))
        bank.add_client(make_client(client_id=2, full_name="Anna Ivanova"))
        bank.open_account(1, "bank", balance=Decimal("100"))
        bank.open_account(2, "bank", balance=Decimal("900"))

        ranking = bank.get_clients_ranking()

        assert [client.client_id for client, _ in ranking] == [2, 1]
        assert [total for _, total in ranking] == [Decimal("900.00"), Decimal("100.00")]

    def test_client_without_accounts_has_zero(self, bank_with_client):
        ranking = bank_with_client.get_clients_ranking()

        assert ranking[0][1] == Decimal("0.00")


class TestRestrictedTime:
    @pytest.mark.parametrize("hour", [0, 2, 4])
    def test_operations_blocked_at_night(self, make_client, hour):
        bank = Bank("Night", time_provider=lambda: datetime(2026, 7, 16, hour, 0, 0))

        with pytest.raises(InvalidOperationError):
            bank.add_client(make_client())

    @pytest.mark.parametrize("hour", [5, 12, 23])
    def test_operations_allowed_during_the_day(self, make_client, hour):
        bank = Bank("Day", time_provider=lambda: datetime(2026, 7, 16, hour, 0, 0))

        bank.add_client(make_client())

        assert len(bank.clients) == 1

    def test_night_attempt_is_recorded(self, night_bank, make_client):
        with pytest.raises(InvalidOperationError):
            night_bank.add_client(make_client())

        actions = [item["action"] for item in night_bank.suspicious_actions]
        assert "restricted_time:add_client" in actions

    def test_authentication_is_allowed_at_night(self, night_bank, make_client):
        night_bank._clients[1] = make_client()

        assert night_bank.authenticate_client(1, "secret-pass") is True


class TestClientHistoryAndStatistics:
    def test_history_of_an_unknown_client_is_an_error(self, bank):
        with pytest.raises(InvalidOperationError):
            bank.get_client_history(99)

    def test_new_bank_has_empty_statistics(self, bank):
        statistics = bank.get_transaction_statistics()

        assert statistics["total"] == 0

    def test_receiver_sees_a_processed_transfer(
        self, transfer_bank, source_account, target_account
    ):
        processor = TransactionProcessor(transfer_bank)
        processor.process(
            Transaction(
                TransactionType.TRANSFER,
                Decimal("250"),
                source_account_id=source_account.account_id,
                target_account_id=target_account.account_id,
            )
        )

        sender = transfer_bank.get_client_history(1)
        receiver = transfer_bank.get_client_history(2)

        assert [row["direction"] for row in sender] == ["out"]
        assert [row["direction"] for row in receiver] == ["in"]
        assert transfer_bank.get_transaction_statistics()["completed"] == 1


class TestAccountOwnership:
    def test_client_account_ids_match_the_account_owners(self, transfer_bank):
        transfer_bank.open_account(2, "savings")

        for client_id, client in transfer_bank.clients.items():
            owned = transfer_bank.search_accounts(client_id=client_id)
            assert client.account_ids == [account.account_id for account in owned]

    def test_client_accounts_follow_the_account_owner(self, transfer_bank):
        owners = {
            account.user_id for account in transfer_bank.search_accounts(client_id=2)
        }

        assert owners == {2}

    def test_ranking_sums_only_accounts_of_the_client(self, transfer_bank):
        ranking = dict(
            (client.client_id, total)
            for client, total in transfer_bank.get_clients_ranking()
        )

        assert ranking == {1: Decimal("10000.00"), 2: Decimal("1000.00")}

    def test_account_with_money_cannot_be_opened_closed(self, bank_with_client):
        with pytest.raises(InvalidOperationError):
            bank_with_client.open_account(
                1, "bank", balance=Decimal("500"), status=AccountStatus.CLOSED
            )

        assert bank_with_client.accounts == {}
