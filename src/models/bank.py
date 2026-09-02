from datetime import datetime
from decimal import Decimal

from src.enums import AccountStatus, ClientStatus, Currency
from src.exceptions import InvalidOperationError
from src.models.accounts import (
    BankAccount,
    InvestmentAccount,
    PremiumAccount,
    SavingsAccount,
)
from src.models.client import Client


class Bank:
    _ACCOUNT_TYPES = {
        "bank": BankAccount,
        "savings": SavingsAccount,
        "premium": PremiumAccount,
        "investment": InvestmentAccount,
    }

    def __init__(self, name: str, time_provider=None):
        if not isinstance(name, str) or not name.strip():
            raise InvalidOperationError("Bank name is required")

        self._name = name
        self._clients = {}
        self._accounts = {}
        self._suspicious_actions = []
        self._time_provider = time_provider or datetime.now

    @property
    def name(self):
        return self._name

    @property
    def clients(self):
        return self._clients.copy()

    @property
    def accounts(self):
        return self._accounts.copy()

    @property
    def suspicious_actions(self):
        return self._suspicious_actions.copy()

    def add_client(self, client):
        self._check_restricted_time("add_client")

        if not isinstance(client, Client):
            raise InvalidOperationError("Invalid client")

        if client.client_id in self._clients:
            raise InvalidOperationError("Client already exists")

        self._clients[client.client_id] = client
        return client

    def open_account(self, client_id, account_type="bank", **account_data):
        self._check_restricted_time("open_account", client_id)
        client = self._get_client(client_id)
        self._ensure_client_can_operate(client, "open_account")

        account_class = self._ACCOUNT_TYPES.get(account_type)

        if account_class is None:
            raise InvalidOperationError("Unknown account type")

        account = account_class(
            user_id=client.client_id,
            owner_name=client.full_name,
            **account_data,
        )

        self._accounts[account.account_id] = account
        client.add_account_id(account.account_id)
        return account

    def close_account(self, account_id):
        self._check_restricted_time("close_account")
        account = self._get_account(account_id)

        if account.balance != 0:
            raise InvalidOperationError(
                "Account with non-zero balance cannot be closed"
            )

        account.status = AccountStatus.CLOSED
        return account

    def freeze_account(self, account_id):
        self._check_restricted_time("freeze_account")
        account = self._get_account(account_id)
        account.status = AccountStatus.FROZEN
        return account

    def unfreeze_account(self, account_id):
        self._check_restricted_time("unfreeze_account")
        account = self._get_account(account_id)

        if account.status is AccountStatus.CLOSED:
            raise InvalidOperationError("Closed account cannot be unfrozen")

        account.status = AccountStatus.ACTIVE
        return account

    def authenticate_client(self, client_id, password):
        client = self._get_client(client_id)

        if client.status is ClientStatus.BLOCKED:
            self._mark_suspicious(client, "blocked_client_login")
            raise InvalidOperationError("Client is blocked")

        if client.check_password(password):
            client.reset_failed_logins()
            return True

        client.register_failed_login()
        self._mark_suspicious(client, "failed_login")

        if client.status is ClientStatus.BLOCKED:
            self._record_suspicious_action(client, "client_blocked_after_failed_logins")

        return False

    def search_accounts(
        self,
        client_id=None,
        status=None,
        currency=None,
        account_type=None,
    ):
        accounts = list(self._accounts.values())

        if client_id is not None:
            client = self._get_client(client_id)
            account_ids = set(client.account_ids)
            accounts = [
                account for account in accounts if account.account_id in account_ids
            ]

        if status is not None:
            if not isinstance(status, AccountStatus):
                raise InvalidOperationError("Invalid account status")

            accounts = [account for account in accounts if account.status is status]

        if currency is not None:
            if not isinstance(currency, Currency):
                raise InvalidOperationError("Invalid currency")

            accounts = [account for account in accounts if account.currency is currency]

        if account_type is not None:
            account_class = self._ACCOUNT_TYPES.get(account_type)

            if account_class is None:
                raise InvalidOperationError("Unknown account type")

            accounts = [
                account for account in accounts if type(account) is account_class
            ]

        return accounts

    def get_total_balance(self):
        return sum(
            (
                account.balance
                for account in self._accounts.values()
                if account.status is not AccountStatus.CLOSED
            ),
            Decimal("0.00"),
        )

    def get_clients_ranking(self):
        ranking = []

        for client in self._clients.values():
            total_balance = Decimal("0.00")

            for account_id in client.account_ids:
                account = self._accounts.get(account_id)

                if account is not None and account.status is not AccountStatus.CLOSED:
                    total_balance += account.balance

            ranking.append((client, total_balance))

        return sorted(ranking, key=lambda item: item[1], reverse=True)

    def _get_client(self, client_id):
        client = self._clients.get(client_id)

        if client is None:
            raise InvalidOperationError("Client not found")

        return client

    def _get_account(self, account_id):
        account = self._accounts.get(account_id)

        if account is None:
            raise InvalidOperationError("Account not found")

        return account

    def _ensure_client_can_operate(self, client, action):
        if client.status is ClientStatus.BLOCKED:
            self._mark_suspicious(client, action)
            raise InvalidOperationError("Blocked client cannot operate")

    def _is_restricted_time(self):
        current_hour = self._time_provider().hour
        return 0 <= current_hour < 5

    def _check_restricted_time(self, action, client_id=None):
        if self._is_restricted_time():
            self._record_suspicious_action_by_id(client_id, f"restricted_time:{action}")
            raise InvalidOperationError("Operations are blocked from 00:00 to 05:00")

    def _mark_suspicious(self, client, action):
        client.mark_suspicious()
        self._record_suspicious_action(client, action)

    def _record_suspicious_action(self, client, action):
        self._suspicious_actions.append(
            {
                "client_id": client.client_id,
                "action": action,
                "time": self._time_provider().isoformat(timespec="seconds"),
            }
        )

    def _record_suspicious_action_by_id(self, client_id, action):
        self._suspicious_actions.append(
            {
                "client_id": client_id,
                "action": action,
                "time": self._time_provider().isoformat(timespec="seconds"),
            }
        )
