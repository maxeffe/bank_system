import json
import threading
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from src.enums import AuditSeverity
from src.exceptions import AuditWriteError, InvalidOperationError
from src.services import AuditLog

DAY = datetime(2026, 9, 4, 14, 0)


def at(moment):
    return lambda: moment


class TestRecording:
    def test_starts_empty(self):
        assert AuditLog().records == []

    def test_stores_all_fields(self):
        log = AuditLog(time_provider=at(DAY))

        log.record(
            "transfer",
            severity=AuditSeverity.WARNING,
            client_id=1,
            account_id="acc-1",
            amount=Decimal("100.00"),
        )

        assert log.records == [
            {
                "time": DAY,
                "event": "transfer",
                "severity": AuditSeverity.WARNING,
                "client_id": 1,
                "account_id": "acc-1",
                "details": {"amount": Decimal("100.00")},
            }
        ]

    def test_severity_defaults_to_info(self):
        log = AuditLog()

        log.record("login")

        assert log.records[0]["severity"] is AuditSeverity.INFO

    def test_event_is_stripped(self):
        log = AuditLog()

        log.record("  login  ")

        assert log.records[0]["event"] == "login"

    def test_returns_a_copy_of_the_entry(self):
        log = AuditLog()

        entry = log.record("login")
        entry["event"] = "hacked"

        assert log.records[0]["event"] == "login"

    def test_records_getter_returns_copies(self):
        log = AuditLog()
        log.record("login")

        log.records[0]["event"] = "hacked"

        assert log.records[0]["event"] == "login"

    def test_keeps_insertion_order(self):
        log = AuditLog()

        log.record("first")
        log.record("second")

        assert [record["event"] for record in log.records] == ["first", "second"]

    def test_explicit_time_wins_over_the_clock(self):
        log = AuditLog(time_provider=at(DAY))
        moment = DAY + timedelta(hours=3)

        log.record("login", now=moment)

        assert log.records[0]["time"] == moment

    def test_explicit_time_must_be_a_datetime(self):
        with pytest.raises(InvalidOperationError):
            AuditLog().record("login", now="2026-09-04")

    @pytest.mark.parametrize("event", ["", "   ", None, 42])
    def test_event_must_be_a_non_empty_string(self, event):
        with pytest.raises(InvalidOperationError):
            AuditLog().record(event)

    @pytest.mark.parametrize("severity", ["warning", None, 1])
    def test_severity_must_be_an_enum_member(self, severity):
        with pytest.raises(InvalidOperationError):
            AuditLog().record("login", severity=severity)


class TestFiltering:
    @pytest.fixture
    def log(self):
        log = AuditLog(time_provider=at(DAY))
        log.record("login", client_id=1, account_id="acc-1")
        log.record("transfer", severity=AuditSeverity.WARNING, client_id=1)
        log.record("transfer", severity=AuditSeverity.CRITICAL, client_id=2)
        return log

    def test_without_conditions_returns_everything(self, log):
        assert len(log.filter()) == 3

    def test_by_exact_severity(self, log):
        found = log.filter(severity=AuditSeverity.WARNING)

        assert [record["client_id"] for record in found] == [1]

    def test_by_minimum_severity(self, log):
        found = log.filter(min_severity=AuditSeverity.WARNING)

        assert len(found) == 2

    def test_minimum_severity_includes_everything_at_info(self, log):
        assert len(log.filter(min_severity=AuditSeverity.INFO)) == 3

    def test_by_event(self, log):
        assert len(log.filter(event="transfer")) == 2

    def test_by_client(self, log):
        assert len(log.filter(client_id=1)) == 2

    def test_by_account(self, log):
        assert len(log.filter(account_id="acc-1")) == 1

    def test_conditions_are_combined(self, log):
        found = log.filter(event="transfer", client_id=1)

        assert [record["severity"] for record in found] == [AuditSeverity.WARNING]

    def test_no_match_returns_empty_list(self, log):
        assert log.filter(client_id=99) == []

    def test_returns_copies(self, log):
        log.filter()[0]["event"] = "hacked"

        assert log.records[0]["event"] == "login"

    @pytest.mark.parametrize("field", ["severity", "min_severity"])
    @pytest.mark.parametrize("value", ["warning", 1])
    def test_invalid_severity_is_rejected(self, log, field, value):
        with pytest.raises(InvalidOperationError):
            log.filter(**{field: value})

    @pytest.mark.parametrize("field", ["since", "until"])
    @pytest.mark.parametrize("value", ["2026-09-04", date(2026, 9, 4), 0])
    def test_invalid_time_bound_is_rejected(self, log, field, value):
        with pytest.raises(InvalidOperationError):
            log.filter(**{field: value})

    def test_invalid_conditions_are_rejected_even_on_an_empty_log(self):
        with pytest.raises(InvalidOperationError):
            AuditLog().filter(since="x")


class TestTimeFiltering:
    @pytest.fixture
    def log(self):
        moments = iter(DAY + timedelta(minutes=index) for index in range(3))
        log = AuditLog(time_provider=lambda: next(moments))

        for index in range(3):
            log.record(f"event-{index}")

        return log

    def test_since_keeps_later_records(self, log):
        found = log.filter(since=DAY + timedelta(minutes=1))

        assert [record["event"] for record in found] == ["event-1", "event-2"]

    def test_until_keeps_earlier_records(self, log):
        found = log.filter(until=DAY + timedelta(minutes=1))

        assert [record["event"] for record in found] == ["event-0", "event-1"]

    def test_window_keeps_the_middle(self, log):
        found = log.filter(
            since=DAY + timedelta(minutes=1), until=DAY + timedelta(minutes=1)
        )

        assert [record["event"] for record in found] == ["event-1"]


class TestFileSink:
    def test_without_path_no_file_is_created(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        log = AuditLog()

        log.record("login")

        assert list(tmp_path.iterdir()) == []

    def test_writes_one_json_line_per_record(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        log = AuditLog(time_provider=at(DAY), file_path=path)

        log.record("login")
        log.record("transfer")

        assert len(path.read_text(encoding="utf-8").splitlines()) == 2

    def test_serializes_domain_types(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        log = AuditLog(time_provider=at(DAY), file_path=path)

        log.record(
            "transfer",
            severity=AuditSeverity.WARNING,
            client_id=1,
            amount=Decimal("100.50"),
        )

        entry = json.loads(path.read_text(encoding="utf-8"))
        assert entry == {
            "time": "2026-09-04T14:00:00",
            "event": "transfer",
            "severity": "warning",
            "client_id": 1,
            "account_id": None,
            "details": {"amount": "100.50"},
        }

    def test_keeps_non_ascii_readable(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        log = AuditLog(file_path=path)

        log.record("login", reason="ночная операция")

        assert "ночная операция" in path.read_text(encoding="utf-8")

    def test_creates_missing_directories(self, tmp_path):
        path = tmp_path / "logs" / "nested" / "audit.jsonl"

        AuditLog(file_path=path).record("login")

        assert path.exists()

    def test_appends_to_an_existing_file(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        AuditLog(file_path=path).record("first")

        AuditLog(file_path=path).record("second")

        events = [
            json.loads(line)["event"]
            for line in path.read_text(encoding="utf-8").splitlines()
        ]
        assert events == ["first", "second"]

    def test_memory_and_file_hold_the_same_count(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        log = AuditLog(file_path=path)

        for index in range(5):
            log.record(f"event-{index}")

        lines = path.read_text(encoding="utf-8").splitlines()
        assert len(lines) == len(log.records) == 5


class TestDeepCopies:
    """Изменить историю через то, что журнал отдал наружу, нельзя."""

    @pytest.fixture
    def log(self):
        log = AuditLog(time_provider=at(DAY))
        log.record("risk", client_id=1, reasons=["large_amount"], nested={"a": 1})
        return log

    def test_filter_result_details_are_detached(self, log):
        found = log.filter(event="risk")[0]

        found["details"]["reasons"].append("tampered")
        found["details"]["nested"]["a"] = 2

        assert log.records[0]["details"] == {
            "reasons": ["large_amount"],
            "nested": {"a": 1},
        }

    def test_records_details_are_detached(self, log):
        log.records[0]["details"]["reasons"].clear()

        assert log.records[0]["details"]["reasons"] == ["large_amount"]

    def test_returned_entry_is_detached(self):
        log = AuditLog()

        entry = log.record("risk", reasons=["large_amount"])
        entry["details"]["reasons"].clear()

        assert log.records[0]["details"]["reasons"] == ["large_amount"]

    def test_caller_list_is_not_aliased(self):
        log = AuditLog()
        reasons = ["large_amount"]

        log.record("risk", reasons=reasons)
        reasons.append("tampered")

        assert log.records[0]["details"]["reasons"] == ["large_amount"]


class TestWriteFailures:
    """Сначала файл, потом память: сбой записи не оставляет полу-события."""

    def test_directory_as_path_raises_a_domain_error(self, tmp_path):
        log = AuditLog(file_path=tmp_path)

        with pytest.raises(AuditWriteError):
            log.record("login")

        assert log.records == []

    def test_regular_file_as_parent_raises_a_domain_error(self, tmp_path):
        parent = tmp_path / "not-a-dir"
        parent.write_text("x", encoding="utf-8")
        log = AuditLog(file_path=parent / "audit.jsonl")

        with pytest.raises(AuditWriteError):
            log.record("login")

        assert log.records == []

    def test_error_message_does_not_leak_the_path(self, tmp_path):
        with pytest.raises(AuditWriteError) as error:
            AuditLog(file_path=tmp_path).record("login")

        assert str(tmp_path) not in str(error.value)

    def test_unserializable_details_are_rejected_before_storing(self):
        log = AuditLog()

        with pytest.raises(InvalidOperationError):
            log.record("x", by_currency={Decimal("1"): 1})

        assert log.records == []

    def test_unserializable_details_are_rejected_without_a_file_too(self):
        looped = {}
        looped["self"] = looped

        with pytest.raises(InvalidOperationError):
            AuditLog().record("x", looped=looped)

    def test_failed_write_leaves_the_file_untouched(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        log = AuditLog(file_path=path)
        log.record("first")

        with pytest.raises(InvalidOperationError):
            log.record("second", by_currency={Decimal("1"): 1})

        assert len(path.read_text(encoding="utf-8").splitlines()) == len(log.records)


class TestLoguruLevels:
    @pytest.mark.parametrize(
        ("severity", "level"),
        [
            (AuditSeverity.INFO, "INFO"),
            (AuditSeverity.WARNING, "WARNING"),
            (AuditSeverity.CRITICAL, "CRITICAL"),
        ],
    )
    def test_severity_becomes_the_log_level(self, log_events, severity, level):
        AuditLog().record("risk", severity=severity, client_id=7, account_id="acc-1")

        event = [record for record in log_events if record["message"] == "audit"][-1]
        assert event["level"].name == level
        assert event["extra"]["client_id"] == 7
        assert event["extra"]["account_id"] == "acc-1"


class TestRejectedBeforeWriting:
    """Всё, что не удаётся скопировать или закодировать, не попадает никуда."""

    def test_uncopyable_details_are_rejected(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        log = AuditLog(file_path=path)
        log.record("first")

        with pytest.raises(InvalidOperationError):
            log.record("second", lock=threading.Lock())

        assert len(log.records) == 1
        assert len(path.read_text(encoding="utf-8").splitlines()) == 1

    def test_text_that_cannot_be_encoded_is_rejected(self, tmp_path):
        path = tmp_path / "audit.jsonl"
        log = AuditLog(file_path=path)
        log.record("first")

        with pytest.raises(InvalidOperationError):
            log.record("second", reason="bad \udcff reply")

        assert len(log.records) == 1
        assert len(path.read_text(encoding="utf-8").splitlines()) == 1

    def test_invalid_path_becomes_a_domain_error(self, tmp_path):
        log = AuditLog(file_path=tmp_path / "bad\0name.jsonl")

        with pytest.raises(AuditWriteError):
            log.record("login")

        assert log.records == []

    def test_client_id_is_not_aliased(self):
        log = AuditLog()
        client_ids = [1]

        log.record("login", client_id=client_ids)
        client_ids.append(2)

        assert log.records[0]["client_id"] == [1]


class TestTimeZones:
    def test_aware_records_and_aware_bounds_work(self):
        moment = datetime(2026, 9, 4, 14, 0, tzinfo=UTC)
        log = AuditLog(time_provider=at(moment))
        log.record("login")

        assert len(log.filter(since=moment - timedelta(minutes=1))) == 1
