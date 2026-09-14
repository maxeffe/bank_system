"""Отчёты аудита: подозрительные операции, риск-профиль, история клиента,
статистика транзакций и ошибок."""

from collections import Counter
from decimal import Decimal

from src.enums import AuditSeverity, RiskLevel, TransactionType
from src.exceptions import RiskBlockedError
from src.services.audit import ACCOUNT_OPENED_EVENT
from src.services.risk import ASSESSMENT_EVENT, COMPLETED_EVENT, FAILED_EVENT

PERCENT = Decimal("100")
PERCENT_PRECISION = Decimal("0.1")


class AuditReporter:
    def __init__(self, audit_log):
        self._audit_log = audit_log

    def suspicious_operations(self, client_id=None):
        """Всё, что журнал счёл как минимум предупреждением."""
        return self._audit_log.filter(
            min_severity=AuditSeverity.WARNING, client_id=client_id
        )

    def client_risk_profile(self, client_id):
        """Сводка по клиенту: сколько операций, какие уровни, какие правила."""
        records = self._audit_log.filter(event=ASSESSMENT_EVENT, client_id=client_id)
        by_level, reasons = self._risk_counts(records)

        return {
            "client_id": client_id,
            "operations": len(records),
            "level": self._worst_level(by_level),
            "blocked": by_level[str(RiskLevel.HIGH)],
            "by_level": by_level,
            "reasons": reasons,
            "total_score": sum(record["details"].get("score", 0) for record in records),
        }

    def error_statistics(self, errors):
        """Считает журнал ошибок процессора по типам ошибок и типам операций."""
        errors = list(errors)

        return {
            "total": len(errors),
            "by_error": dict(Counter(error["error"] for error in errors).most_common()),
            "by_transaction_type": dict(
                Counter(error["transaction_type"] for error in errors).most_common()
            ),
            "affected_transactions": len({error["transaction_id"] for error in errors}),
        }

    def client_history(self, client_id, account_ids=()):
        """Операции клиента по итогам из аудита: свои и завершённые входящие.

        Упавший входящий перевод получателю не показывается: денег он не видел.
        Уровень риска виден только у своих операций.
        """
        own_accounts = set(account_ids)
        risk_levels = {
            record["details"].get("transaction_id"): record["details"].get("risk_level")
            for record in self._audit_log.filter(
                event=ASSESSMENT_EVENT, client_id=client_id
            )
        }
        rows = [
            self._history_row(record, client_id, risk_levels)
            for record in self._outcomes()
            if record["client_id"] == client_id
            or (
                record["event"] == COMPLETED_EVENT
                and record["details"].get("target_account_id") in own_accounts
            )
        ]
        return sorted(rows, key=lambda row: row["time"])

    def balance_history(self, account_ids):
        """Движение баланса: остаток при открытии и после каждой исполненной операции."""
        series = {account_id: [] for account_id in account_ids}

        for record in self._audit_log.filter():
            if record["event"] == ACCOUNT_OPENED_EVENT:
                points = {record["account_id"]: record["details"].get("balance")}
            elif record["event"] == COMPLETED_EVENT:
                points = record["details"].get("balances", {})
            else:
                continue

            for account_id, balance in points.items():
                if account_id in series:
                    series[account_id].append((record["time"], balance))

        return series

    def transaction_statistics(self):
        """Итоги обработки из аудита: сколько прошло, сколько отклонено и почему."""
        outcomes = self._outcomes()
        completed = [
            record for record in outcomes if record["event"] == COMPLETED_EVENT
        ]
        by_type = {}

        for record in outcomes:
            counts = by_type.setdefault(
                record["details"].get("transaction_type"),
                {"completed": 0, "rejected": 0},
            )
            counts[
                "completed" if record["event"] == COMPLETED_EVENT else "rejected"
            ] += 1

        by_level, by_rule = self._risk_counts(
            self._audit_log.filter(event=ASSESSMENT_EVENT)
        )

        return {
            "total": len(outcomes),
            "completed": len(completed),
            "rejected": len(outcomes) - len(completed),
            "success_rate": self._percent(len(completed), len(outcomes)),
            "by_type": by_type,
            "by_risk_level": by_level,
            "by_rule": by_rule,
            "blocked_by_risk": sum(
                1
                for record in outcomes
                if record["details"].get("error") == RiskBlockedError.__name__
            ),
            "volume": self._sum_by_currency(completed, "amount"),
            "fees": self._sum_by_currency(completed, "fee"),
        }

    def _outcomes(self):
        return [
            record
            for record in self._audit_log.filter()
            if record["event"] in (COMPLETED_EVENT, FAILED_EVENT)
        ]

    @staticmethod
    def _history_row(record, client_id, risk_levels):
        details = record["details"]
        transaction_type = details.get("transaction_type")
        outgoing = record["client_id"] == client_id and transaction_type != str(
            TransactionType.DEPOSIT
        )
        own_account, counterparty = (
            (details.get("source_account_id"), details.get("target_account_id"))
            if outgoing
            else (details.get("target_account_id"), details.get("source_account_id"))
        )

        return {
            "time": record["time"],
            "transaction_id": details.get("transaction_id"),
            "type": transaction_type,
            "direction": "out" if outgoing else "in",
            "amount": details.get("amount"),
            "fee": details.get("fee"),
            "currency": details.get("currency"),
            "account_id": own_account,
            "counterparty": counterparty,
            "status": "completed" if record["event"] == COMPLETED_EVENT else "rejected",
            "reason": details.get("reason"),
            "risk_level": risk_levels.get(details.get("transaction_id")),
        }

    @staticmethod
    def _sum_by_currency(records, field):
        totals = {}

        for record in records:
            currency = record["details"].get("currency")
            value = record["details"].get(field) or Decimal("0.00")
            totals[currency] = totals.get(currency, Decimal("0.00")) + value

        return totals

    @staticmethod
    def _percent(part, whole):
        if whole == 0:
            return Decimal("0.0")

        return (Decimal(part) * PERCENT / whole).quantize(PERCENT_PRECISION)

    @staticmethod
    def _risk_counts(assessments):
        """Оценки по уровням (все уровни, по порядку) и сработавшие правила."""
        levels = Counter(record["details"].get("risk_level") for record in assessments)
        rules = Counter(
            reason
            for record in assessments
            for reason in record["details"].get("reasons", [])
        )
        by_level = {str(level): levels.get(str(level), 0) for level in RiskLevel}
        return by_level, dict(rules.most_common())

    @staticmethod
    def _worst_level(by_level):
        for level in reversed(RiskLevel):
            if by_level[str(level)]:
                return level

        return RiskLevel.LOW
