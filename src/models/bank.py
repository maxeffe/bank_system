"""Реестр клиентов и счетов.

Банк хранит участников и владеет жизненным циклом счетов. Всё остальное -
вход, журнал подозрительных действий, отчёты - делают сервисы из src/services.
"""

from datetime import datetime

from src.enums import AccountStatus, Currency, TransactionStatus
from src.exceptions import InvalidOperationError, RiskBlockedError
from src.models.accounts import (
    BankAccount,
    InvestmentAccount,
    PremiumAccount,
    SavingsAccount,
)
from src.models.client import Client
from src.models.exchange import CurrencyConverter
from src.services import (
    ACCOUNT_OPENED_EVENT,
    AuditLog,
    AuditReporter,
    AuthService,
    BankAnalytics,
    FraudJournal,
    RiskAnalyzer,
)


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
        audit_log=None,
        risk_settings: dict = None,
    ):
        if not isinstance(name, str) or not name.strip():
            raise InvalidOperationError("Bank name is required")

        self._name = name
        self._clients = {}
        self._accounts = {}

        self._time_provider = time_provider or datetime.now
        self._converter = converter or CurrencyConverter()
        self._audit_log = audit_log or AuditLog(time_provider=self._time_provider)
        self._journal = FraudJournal(self._time_provider, self._audit_log)
        self._auth_service = AuthService(self._journal)
        self._analytics = BankAnalytics(self._converter)
        # Анализатор всегда пишет в журнал банка и живёт по его часам и курсам;
        # снаружи настраиваются только пороги.
        self._risk_analyzer = RiskAnalyzer(
            self._audit_log,
            self._converter,
            time_provider=self._time_provider,
            **(risk_settings or {}),
        )
        self._reporter = AuditReporter(self._audit_log)

    @property
    def name(self):
        return self._name

    @property
    def converter(self):
        return self._converter

    @property
    def time_provider(self):
        return self._time_provider

    @property
    def audit_log(self):
        return self._audit_log

    @property
    def risk_analyzer(self):
        return self._risk_analyzer

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

        # Сначала журнал: если запись не удалась, счёта не должно появиться.
        self._audit_log.record(
            ACCOUNT_OPENED_EVENT,
            client_id=client.client_id,
            account_id=account.account_id,
            now=self._time_provider(),
            account_type=account_type,
            currency=str(account.currency),
            balance=account.balance,
        )
        self._accounts[account.account_id] = account
        return account

    def close_account(self, account_id):
        self._journal.ensure_allowed_time("close_account")
        account = self.get_account(account_id)
        account.status = AccountStatus.CLOSED
        return account

    def freeze_account(self, account_id):
        self._journal.ensure_allowed_time("freeze_account")
        account = self.get_account(account_id)
        account.status = AccountStatus.FROZEN
        return account

    def unfreeze_account(self, account_id):
        self._journal.ensure_allowed_time("unfreeze_account")
        account = self.get_account(account_id)
        account.status = AccountStatus.ACTIVE
        return account

    def authenticate_client(self, client_id, password):
        return self._auth_service.authenticate(self._get_client(client_id), password)

    def ensure_can_process(self, transaction, now: datetime = None):
        """Проверки в момент исполнения: ночной запрет и блокировка отправителя.

        Время берётся из того же момента, что и оценка риска и итог в аудите.
        """
        client_id = self._transaction_client_id(transaction)
        self._journal.ensure_allowed_time("process_transaction", client_id, now)

        if transaction.source_account_id is None:
            return

        owner_id = self.get_account(transaction.source_account_id).user_id
        self._auth_service.ensure_can_operate(
            self._get_client(owner_id), "send money", now
        )

    def search_accounts(
        self,
        client_id=None,
        status=None,
        currency=None,
        account_type=None,
    ):
        accounts = (
            self._accounts_of(client_id)
            if client_id is not None
            else list(self._accounts.values())
        )

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
            self._clients.values(), self._accounts.values(), currency
        )

    def assess_transaction(self, transaction, now: datetime = None):
        """Оценивает риск операции и блокирует её, если уровень высокий.

        Сама блокировка уже записана в аудит как оценка CRITICAL, поэтому
        клиент только помечается, без второй записи о том же событии.
        """
        client_id = self._transaction_client_id(transaction)
        assessment = self._risk_analyzer.evaluate(transaction, client_id, now)

        if not assessment.is_blocked:
            return assessment

        client = self._clients.get(client_id)

        if client is not None:
            client.mark_suspicious()

        raise RiskBlockedError(f"Transaction blocked by risk control: {assessment}")

    def record_outcome(self, transaction, error=None):
        """Итог операции в аудит; у исполненной - остатки затронутых счетов."""
        client_id = self._transaction_client_id(transaction)
        balances = {}

        if transaction.status is TransactionStatus.COMPLETED:
            touched = (transaction.source_account_id, transaction.target_account_id)
            balances = {
                account_id: self._accounts[account_id].balance
                for account_id in touched
                if account_id in self._accounts
            }

        self._risk_analyzer.record_outcome(transaction, client_id, error, balances)

    def get_suspicious_operations(self, client_id=None):
        return self._reporter.suspicious_operations(client_id)

    def get_client_risk_profile(self, client_id):
        return self._reporter.client_risk_profile(client_id)

    def get_error_statistics(self, errors):
        return self._reporter.error_statistics(errors)

    def get_client_history(self, client_id):
        return self._reporter.client_history(client_id, self._account_ids_of(client_id))

    def get_balance_history(self, client_id):
        return self._reporter.balance_history(self._account_ids_of(client_id))

    def get_transaction_statistics(self):
        return self._reporter.transaction_statistics()

    def _transaction_client_id(self, transaction):
        """Владелец денег: отправитель, а для пополнения - получатель."""
        account = self._accounts.get(
            transaction.source_account_id or transaction.target_account_id
        )
        return account.user_id if account is not None else None

    def _accounts_of(self, client_id):
        """Счета клиента. Владелец записан только в самом счёте."""
        self._get_client(client_id)
        return [
            account
            for account in self._accounts.values()
            if account.user_id == client_id
        ]

    def _account_ids_of(self, client_id):
        return [account.account_id for account in self._accounts_of(client_id)]

    def _get_client(self, client_id):
        client = self._clients.get(client_id)

        if client is None:
            raise InvalidOperationError("Client not found")

        return client

    def get_account(self, account_id):
        account = self._accounts.get(account_id)

        if account is None:
            raise InvalidOperationError("Account not found")

        return account
