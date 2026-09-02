"""Реестр клиентов и счетов.

Банк хранит участников и владеет жизненным циклом счетов. Всё остальное -
вход, журнал подозрительных действий, отчёты - делают сервисы из src/services.
"""

from src.enums import AccountStatus, Currency
from src.exceptions import InvalidOperationError
from src.models.accounts import (
    BankAccount,
    InvestmentAccount,
    PremiumAccount,
    SavingsAccount,
)
from src.models.client import Client
from src.models.exchange import CurrencyConverter
from src.services import AuthService, BankAnalytics, FraudJournal


class Bank:
    _ACCOUNT_TYPES = {
        "bank": BankAccount,
        "savings": SavingsAccount,
        "premium": PremiumAccount,
        "investment": InvestmentAccount,
    }

    def __init__(
        self,
        name: str,
        time_provider=None,
        converter=None,
        journal=None,
        auth_service=None,
        analytics=None,
    ):
        if not isinstance(name, str) or not name.strip():
            raise InvalidOperationError("Bank name is required")

        self._name = name
        self._clients = {}
        self._accounts = {}

        self._converter = converter or CurrencyConverter()
        self._journal = journal or FraudJournal(time_provider)
        self._auth_service = auth_service or AuthService(self._journal)
        self._analytics = analytics or BankAnalytics(self._converter)

    @property
    def name(self):
        return self._name

    @property
    def converter(self):
        return self._converter

    @property
    def journal(self):
        return self._journal

    @property
    def clients(self):
        return self._clients.copy()

    @property
    def accounts(self):
        return self._accounts.copy()

    @property
    def suspicious_actions(self):
        return self._journal.actions

    def add_client(self, client):
        self._journal.ensure_allowed_time("add_client")

        if not isinstance(client, Client):
            raise InvalidOperationError("Invalid client")

        if client.client_id in self._clients:
            raise InvalidOperationError("Client already exists")

        self._clients[client.client_id] = client
        return client

    def open_account(self, client_id, account_type="bank", **account_data):
        self._journal.ensure_allowed_time("open_account", client_id)
        client = self._get_client(client_id)
        self._auth_service.ensure_can_operate(client, "open_account")

        account_class = self._ACCOUNT_TYPES.get(account_type)

        if account_class is None:
            raise InvalidOperationError("Unknown account type")

        account = account_class(
            user_id=client.client_id,
            owner_name=client.full_name,
            **account_data,
        )

        if account.account_id in self._accounts:
            raise InvalidOperationError("Account id already exists")

        self._accounts[account.account_id] = account
        client.add_account_id(account.account_id)
        return account

    def close_account(self, account_id):
        self._journal.ensure_allowed_time("close_account")
        account = self._get_account(account_id)

        if account.balance != 0:
            raise InvalidOperationError(
                "Account with non-zero balance cannot be closed"
            )

        account.status = AccountStatus.CLOSED
        return account

    def freeze_account(self, account_id):
        self._journal.ensure_allowed_time("freeze_account")
        account = self._get_account(account_id)
        account.status = AccountStatus.FROZEN
        return account

    def unfreeze_account(self, account_id):
        self._journal.ensure_allowed_time("unfreeze_account")
        account = self._get_account(account_id)

        if account.status is AccountStatus.CLOSED:
            raise InvalidOperationError("Closed account cannot be unfrozen")

        account.status = AccountStatus.ACTIVE
        return account

    def authenticate_client(self, client_id, password):
        return self._auth_service.authenticate(self._get_client(client_id), password)

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

    def get_total_balance(self, currency=Currency.RUB):
        return self._analytics.total_balance(self._accounts.values(), currency)

    def get_clients_ranking(self, currency=Currency.RUB):
        return self._analytics.clients_ranking(
            self._clients.values(), self._accounts, currency
        )

    def get_account(self, account_id):
        return self._get_account(account_id)

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
