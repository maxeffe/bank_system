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

    def clients_ranking(self, clients, accounts, currency=Currency.RUB):
        accounts = list(accounts)
        ranking = [
            (
                client,
                self.total_balance(
                    [a for a in accounts if a.user_id == client.client_id], currency
                ),
            )
            for client in clients
        ]
        return sorted(ranking, key=lambda item: item[1], reverse=True)
