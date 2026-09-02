"""Отчёты банка. Все суммы приводятся к одной валюте."""

from decimal import Decimal

from src.enums import AccountStatus, Currency


class BankAnalytics:
    def __init__(self, converter):
        self._converter = converter

    def total_balance(self, accounts, currency=Currency.RUB):
        return sum(
            (
                self._converter.convert(account.balance, account.currency, currency)
                for account in accounts
                if account.status is not AccountStatus.CLOSED
            ),
            Decimal("0.00"),
        )

    def clients_ranking(self, clients, accounts_by_id, currency=Currency.RUB):
        ranking = [
            (client, self._client_total(client, accounts_by_id, currency))
            for client in clients
        ]
        return sorted(ranking, key=lambda item: item[1], reverse=True)

    def _client_total(self, client, accounts_by_id, currency):
        accounts = (accounts_by_id.get(account_id) for account_id in client.account_ids)
        return self.total_balance(
            [account for account in accounts if account is not None], currency
        )
