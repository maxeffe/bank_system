"""Отчёты по клиенту, банку и рискам. Всё берётся из публичного API банка."""

from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from src.enums import AccountStatus, ClientStatus, Currency
from src.reporting import charts, formats
from src.services import BankAnalytics

HISTORY_COLUMNS = (
    "time",
    "type",
    "direction",
    "amount",
    "currency",
    "account_id",
    "counterparty",
    "status",
    "risk_level",
    "reason",
)
ACCOUNT_COLUMNS = ("account_id", "owner", "type", "currency", "balance", "status")
SUSPICIOUS_COLUMNS = (
    "time",
    "severity",
    "event",
    "client_id",
    "account_id",
    "amount",
    "risk_level",
    "details",
)
TOP_CHART_CLIENTS = 10


@dataclass(frozen=True)
class Report:
    kind: str
    title: str
    generated_at: datetime
    summary: dict
    tables: dict
    main_table: str
    main_columns: tuple = field(default_factory=tuple)

    def to_dict(self):
        return {
            "kind": self.kind,
            "title": self.title,
            "generated_at": self.generated_at,
            "summary": self.summary,
            "tables": self.tables,
        }


class ReportBuilder:
    def __init__(self, bank, time_provider=None, currency=Currency.RUB):
        self._bank = bank
        self._time_provider = time_provider or bank.time_provider
        self._currency = currency

    def client_report(self, client_id):
        bank = self._bank
        history = bank.get_client_history(client_id)
        client = bank.clients[client_id]
        profile = bank.get_client_risk_profile(client_id)
        accounts = bank.search_accounts(client_id=client_id)

        summary = {
            "client_id": client_id,
            "full_name": client.full_name,
            "status": client.status,
            "accounts": len(accounts),
            f"total_balance_{self._currency}": BankAnalytics(
                bank.converter
            ).total_balance(accounts, self._currency),
            "operations": len(history),
            "risk_level": profile["level"],
            "blocked_operations": profile["blocked"],
            "suspicious_records": len(bank.get_suspicious_operations(client_id)),
        }
        tables = {
            "history": [
                {column: row[column] for column in HISTORY_COLUMNS} for row in history
            ],
            "accounts": [self._account_row(account) for account in accounts],
        }
        return self._report(
            "client",
            f"Отчёт по клиенту: {client.full_name}",
            summary,
            tables,
            "history",
            HISTORY_COLUMNS,
        )

    def bank_report(self):
        bank = self._bank
        statistics = bank.get_transaction_statistics()
        summary = {
            "bank": bank.name,
            "clients": len(bank.clients),
            "accounts": len(bank.accounts),
            f"total_balance_{self._currency}": bank.get_total_balance(self._currency),
            **{
                f"transactions_{key}": statistics[key]
                for key in (
                    "total",
                    "completed",
                    "rejected",
                    "success_rate",
                    "blocked_by_risk",
                )
            },
        }
        tables = {
            "accounts": [
                self._account_row(account) for account in bank.accounts.values()
            ],
            "clients_ranking": [
                {"place": place, "client": client.full_name, "balance": total}
                for place, (client, total) in enumerate(
                    bank.get_clients_ranking(self._currency), 1
                )
            ],
            "transactions_by_type": [
                {"type": kind, **counts}
                for kind, counts in statistics["by_type"].items()
            ],
            "balance_by_currency": self._balance_by_currency(),
        }
        return self._report(
            "bank",
            f"Отчёт по банку: {bank.name}",
            summary,
            tables,
            "accounts",
            ACCOUNT_COLUMNS,
        )

    def risk_report(self):
        bank = self._bank
        statistics = bank.get_transaction_statistics()
        profiles = [
            bank.get_client_risk_profile(client_id) for client_id in bank.clients
        ]
        suspicious = bank.get_suspicious_operations()

        summary = {
            "assessments": sum(statistics["by_risk_level"].values()),
            "blocked_by_risk": statistics["blocked_by_risk"],
            "suspicious_records": len(suspicious),
            "watched_clients": sum(
                client.status is not ClientStatus.ACTIVE
                for client in bank.clients.values()
            ),
        }
        tables = {
            "suspicious_operations": [
                self._suspicious_row(record) for record in suspicious
            ],
            "risk_levels": [
                {"level": level, "count": count}
                for level, count in statistics["by_risk_level"].items()
            ],
            "rules": [
                {"rule": rule, "count": count}
                for rule, count in statistics["by_rule"].items()
            ],
            "clients": [self._client_risk_row(profile) for profile in profiles],
        }
        return self._report(
            "risk",
            "Отчёт по рискам",
            summary,
            tables,
            "suspicious_operations",
            SUSPICIOUS_COLUMNS,
        )

    @staticmethod
    def to_text(report):
        return formats.render_text(report)

    @staticmethod
    def export_to_text(report, path):
        return formats.write_text(report, path)

    @staticmethod
    def export_to_json(report, path):
        return formats.write_json(report, path)

    @staticmethod
    def export_to_csv(report, path):
        return formats.write_csv(report, path)

    def save_charts(self, directory, client_id=None):
        """Сохраняет PNG-диаграммы банка и рисков, а для клиента - движение баланса."""
        directory = Path(directory)
        bank, risk = self.bank_report(), self.risk_report()
        currency_rows = bank.tables["balance_by_currency"]
        by_type = bank.tables["transactions_by_type"]
        ranking = bank.tables["clients_ranking"]
        paths = [
            charts.save_pie(
                directory / "bank_balance_by_currency.png",
                f"Доля валют в общем балансе, {self._label}",
                [row["currency"].upper() for row in currency_rows],
                [row[f"balance_{self._currency}"] for row in currency_rows],
            ),
            charts.save_bars(
                directory / "bank_top_clients.png",
                self._ranking_title(len(ranking)),
                [row["client"] for row in ranking[:TOP_CHART_CLIENTS]],
                [row["balance"] for row in ranking[:TOP_CHART_CLIENTS]],
            ),
            charts.save_stacked_bars(
                directory / "bank_transactions_by_type.png",
                "Транзакции по типам",
                [row["type"] for row in by_type],
                {
                    "исполнено": [row["completed"] for row in by_type],
                    "отклонено": [row["rejected"] for row in by_type],
                },
            ),
            charts.save_risk_levels(
                directory / "risk_levels.png",
                "Оценки риска по уровням",
                {row["level"]: row["count"] for row in risk.tables["risk_levels"]},
            ),
            charts.save_bars(
                directory / "risk_rules.png",
                "Сколько раз сработало правило риска",
                [row["rule"] for row in risk.tables["rules"]],
                [row["count"] for row in risk.tables["rules"]],
            ),
        ]

        if client_id is not None:
            paths.append(self._save_balance_chart(directory, client_id))

        return paths

    def _save_balance_chart(self, directory, client_id):
        bank = self._bank
        accounts = bank.accounts
        series = {
            self._account_label(accounts[account_id]): [
                (moment, self._to_base(balance, accounts[account_id].currency))
                for moment, balance in points
            ]
            for account_id, points in bank.get_balance_history(client_id).items()
        }
        client = bank.clients[client_id]
        return charts.save_balance_lines(
            directory / f"client_{client_id}_balance.png",
            f"Движение баланса: {client.full_name}, {self._label}",
            series,
        )

    def _ranking_title(self, count):
        title = f"Клиенты по балансу, {self._label}"

        if count > TOP_CHART_CLIENTS:
            title = f"{title}: топ-{TOP_CHART_CLIENTS} из {count}"

        return title

    @property
    def _label(self):
        return str(self._currency).upper()

    @staticmethod
    def _account_label(account):
        currency = str(account.currency).upper()
        return f"{type(account).__name__} {account.account_id} ({currency})"

    def _report(self, kind, title, summary, tables, main_table, columns):
        return Report(
            kind, title, self._time_provider(), summary, tables, main_table, columns
        )

    def _account_row(self, account):
        return {
            "account_id": account.account_id,
            "owner": account.owner_name,
            "type": type(account).__name__,
            "currency": account.currency,
            "balance": account.balance,
            "status": account.status,
        }

    def _balance_by_currency(self):
        """Каждый счёт переводится отдельно, как в общем балансе банка:
        иначе строки расходятся с итогом на копейки округления."""
        rows = {}

        for account in self._bank.accounts.values():
            if account.status is AccountStatus.CLOSED:
                continue

            row = rows.setdefault(
                account.currency,
                {
                    "currency": str(account.currency),
                    "balance": Decimal("0.00"),
                    f"balance_{self._currency}": Decimal("0.00"),
                },
            )
            row["balance"] += account.balance
            row[f"balance_{self._currency}"] += self._to_base(
                account.balance, account.currency
            )

        return list(rows.values())

    def _to_base(self, amount, currency):
        return self._bank.converter.convert(amount, currency, self._currency)

    @staticmethod
    def _suspicious_row(record):
        details = record["details"]
        return {
            "time": record["time"],
            "severity": record["severity"],
            "event": record["event"],
            "client_id": record["client_id"],
            "account_id": record["account_id"],
            "amount": details.get("amount"),
            "risk_level": details.get("risk_level"),
            "details": details.get("reasons") or details.get("action"),
        }

    def _client_risk_row(self, profile):
        client = self._bank.clients[profile["client_id"]]
        return {
            "client_id": profile["client_id"],
            "client": client.full_name,
            "status": client.status,
            "level": profile["level"],
            "operations": profile["operations"],
            "blocked": profile["blocked"],
        }
