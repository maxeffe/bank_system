"""Журнал аудита: запись событий в память и в файл, фильтрация."""

import copy
import json
from datetime import datetime
from pathlib import Path

from loguru import logger

from src.enums import AuditSeverity
from src.exceptions import AuditWriteError, InvalidOperationError

SEVERITY_RANK = {
    AuditSeverity.INFO: 0,
    AuditSeverity.WARNING: 1,
    AuditSeverity.CRITICAL: 2,
}

LOG_LEVEL_BY_SEVERITY = {
    AuditSeverity.INFO: "INFO",
    AuditSeverity.WARNING: "WARNING",
    AuditSeverity.CRITICAL: "CRITICAL",
}

FILE_ENCODING = "utf-8"
ACCOUNT_OPENED_EVENT = "account_opened"


def _to_jsonable(value):
    """Время - в ISO, остальное (Decimal, Enum) - строкой без потери точности."""
    if isinstance(value, datetime):
        return value.isoformat(timespec="seconds")

    return str(value)


class AuditLog:
    """Хранит события в памяти и, если задан путь, дописывает их в JSONL.

    Наружу отдаются только глубокие копии: изменить историю через результат
    filter() или records нельзя.
    """

    def __init__(self, time_provider=None, file_path: str | Path = None):
        self._records = []
        self._time_provider = time_provider or datetime.now
        self._file_path = Path(file_path) if file_path is not None else None

    @property
    def records(self):
        return copy.deepcopy(self._records)

    def record(
        self,
        event: str,
        severity=AuditSeverity.INFO,
        client_id=None,
        account_id=None,
        now: datetime = None,
        **details,
    ):
        """Всё, что может упасть, случается до записи. Потом файл, потом память."""
        entry = self._build_entry(event, severity, client_id, account_id, now, details)
        line = self._serialize(entry)
        result = copy.deepcopy(entry)

        self._write_to_file(line)
        self._records.append(entry)
        logger.log(
            LOG_LEVEL_BY_SEVERITY[severity],
            "audit",
            event=entry["event"],
            severity=str(severity),
            client_id=entry["client_id"],
            account_id=entry["account_id"],
        )
        return result

    def filter(
        self,
        severity=None,
        min_severity=None,
        event=None,
        client_id=None,
        account_id=None,
        since: datetime = None,
        until: datetime = None,
    ):
        """Возвращает копии записей, подходящих сразу под все заданные условия."""
        self._validate_filter(severity, min_severity, since, until)
        conditions = {
            "severity": severity,
            "event": event,
            "client_id": client_id,
            "account_id": account_id,
        }
        wanted = {key: value for key, value in conditions.items() if value is not None}
        threshold = SEVERITY_RANK[min_severity] if min_severity is not None else None

        found = [
            record
            for record in self._records
            if all(record[key] == value for key, value in wanted.items())
            and (threshold is None or SEVERITY_RANK[record["severity"]] >= threshold)
            and (since is None or record["time"] >= since)
            and (until is None or record["time"] <= until)
        ]
        return copy.deepcopy(found)

    def _build_entry(self, event, severity, client_id, account_id, now, details):
        if not isinstance(event, str) or not event.strip():
            raise InvalidOperationError("Audit event is required")

        if not isinstance(severity, AuditSeverity):
            raise InvalidOperationError("Invalid audit severity")

        if now is not None and not isinstance(now, datetime):
            raise InvalidOperationError("Audit time must be a datetime")

        entry = {
            "time": now or self._time_provider(),
            "event": event.strip(),
            "severity": severity,
            "client_id": client_id,
            "account_id": account_id,
            "details": details,
        }

        try:
            return copy.deepcopy(entry)
        except (TypeError, copy.Error) as error:
            raise InvalidOperationError("Audit details must be copyable") from error

    @staticmethod
    def _validate_filter(severity, min_severity, since, until):
        for level in (severity, min_severity):
            if level is not None and not isinstance(level, AuditSeverity):
                raise InvalidOperationError("Invalid audit severity")

        for moment in (since, until):
            if moment is not None and not isinstance(moment, datetime):
                raise InvalidOperationError("Audit time must be a datetime")

    @staticmethod
    def _serialize(entry):
        """JSON-строка, которую точно можно записать в файл в utf-8."""
        try:
            line = json.dumps(entry, ensure_ascii=False, default=_to_jsonable)
            line.encode(FILE_ENCODING)
        except (TypeError, ValueError) as error:
            raise InvalidOperationError("Audit details must be serializable") from error

        return line

    def _write_to_file(self, line):
        """Дозапись одной строки. Файл открывается и закрывается на каждое событие."""
        if self._file_path is None:
            return

        try:
            self._file_path.parent.mkdir(parents=True, exist_ok=True)

            with self._file_path.open("a", encoding=FILE_ENCODING) as audit_file:
                audit_file.write(f"{line}\n")
        except (OSError, ValueError) as error:
            raise AuditWriteError("Audit file is not writable") from error
