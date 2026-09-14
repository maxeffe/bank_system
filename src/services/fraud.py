"""Журнал подозрительных действий и ночное окно запрета.

Своего хранилища не держит: все записи уходят в AuditLog, а `actions`
собирается из него обратно в прежнем виде.
"""

from datetime import datetime

from src.enums import AuditSeverity
from src.exceptions import InvalidOperationError
from src.services.audit import AuditLog

RESTRICTED_HOURS = range(0, 5)
FRAUD_EVENT = "fraud_action"


class FraudJournal:
    def __init__(self, time_provider=None, audit_log=None):
        self._time_provider = time_provider or datetime.now
        self._audit_log = audit_log or AuditLog(time_provider=self._time_provider)

    @property
    def audit_log(self):
        return self._audit_log

    @property
    def actions(self):
        return [
            {
                "client_id": record["client_id"],
                "action": record["details"]["action"],
                "time": record["time"].isoformat(timespec="seconds"),
            }
            for record in self._audit_log.filter(event=FRAUD_EVENT)
        ]

    def record(self, client_id, action, severity=AuditSeverity.WARNING, now=None):
        self._audit_log.record(
            FRAUD_EVENT,
            severity=severity,
            client_id=client_id,
            now=now or self._time_provider(),
            action=action,
        )

    def flag_client(self, client, action, now=None):
        """Помечает клиента подозрительным и заносит действие в журнал."""
        client.mark_suspicious()
        self.record(client.client_id, action, now=now)

    def is_restricted_time(self, now=None):
        return (now or self._time_provider()).hour in RESTRICTED_HOURS

    def ensure_allowed_time(self, action, client_id=None, now=None):
        moment = now or self._time_provider()

        if not self.is_restricted_time(moment):
            return

        self.record(
            client_id,
            f"restricted_time:{action}",
            severity=AuditSeverity.CRITICAL,
            now=moment,
        )
        raise InvalidOperationError("Operations are blocked from 00:00 to 05:00")
