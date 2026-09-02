"""Журнал подозрительных действий и ночное окно запрета."""

from datetime import datetime

from src.exceptions import InvalidOperationError

RESTRICTED_HOURS = range(0, 5)


class FraudJournal:
    def __init__(self, time_provider=None):
        self._actions = []
        self._time_provider = time_provider or datetime.now

    @property
    def actions(self):
        return [action.copy() for action in self._actions]

    def record(self, client_id, action):
        self._actions.append(
            {
                "client_id": client_id,
                "action": action,
                "time": self._time_provider().isoformat(timespec="seconds"),
            }
        )

    def flag_client(self, client, action):
        """Помечает клиента подозрительным и заносит действие в журнал."""
        client.mark_suspicious()
        self.record(client.client_id, action)

    def is_restricted_time(self):
        return self._time_provider().hour in RESTRICTED_HOURS

    def ensure_allowed_time(self, action, client_id=None):
        if not self.is_restricted_time():
            return

        self.record(client_id, f"restricted_time:{action}")
        raise InvalidOperationError("Operations are blocked from 00:00 to 05:00")
