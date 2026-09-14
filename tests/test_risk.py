import json
from datetime import datetime, timedelta
from decimal import Decimal

import pytest

from src.enums import (
    AuditSeverity,
    ClientStatus,
    Currency,
    RiskLevel,
    TransactionStatus,
    TransactionType,
)
from src.exceptions import (
    AuditWriteError,
    InvalidOperationError,
    RiskBlockedError,
    TransientTransactionError,
)
from src.models import (
    Bank,
    CurrencyConverter,
    Transaction,
    TransactionProcessor,
    TransactionQueue,
)
from src.services import AuditLog, RiskAnalyzer, RiskAssessment
from src.services.risk import (
    ASSESSMENT_EVENT,
    COMPLETED_EVENT,
    DEFAULT_LARGE_AMOUNT,
    FAILED_EVENT,
    FREQUENT_OPERATIONS_RULE,
    LARGE_AMOUNT_RULE,
    NEW_COUNTERPARTY_RULE,
    NIGHT_TIME_RULE,
)

DAY = datetime(2026, 9, 4, 14, 0)
NIGHT = datetime(2026, 9, 4, 2, 0)


def at(moment):
    return lambda: moment


def make_transfer(amount="100", target="acc-2", currency=Currency.RUB):
    return Transaction(
        TransactionType.TRANSFER,
        Decimal(amount),
        currency=currency,
        source_account_id="acc-1",
        target_account_id=target,
        time_provider=at(DAY),
    )


class OutcomeLosingAuditLog(AuditLog):
    """Журнал, у которого падает запись итога, как при сбое диска."""

    def record(self, event, *args, **kwargs):
        if event == COMPLETED_EVENT:
            raise AuditWriteError("Audit file is not writable")

        return super().record(event, *args, **kwargs)


def complete(analyzer, transaction, client_id):
    """Оценка и успешное исполнение: только так получатель становится знакомым."""
    analyzer.evaluate(transaction, client_id=client_id)
    transaction.mark_processing(now=DAY)
    transaction.mark_completed(now=DAY)
    analyzer.record_outcome(transaction, client_id)


def fail(analyzer, transaction, client_id, error=None):
    analyzer.evaluate(transaction, client_id=client_id)
    transaction.mark_processing(now=DAY)
    transaction.mark_failed("boom", now=DAY)
    analyzer.record_outcome(transaction, client_id, error)


def make_deposit(amount="100"):
    return Transaction(
        TransactionType.DEPOSIT,
        Decimal(amount),
        target_account_id="acc-2",
        time_provider=at(DAY),
    )


@pytest.fixture
def audit():
    return AuditLog(time_provider=at(DAY))


@pytest.fixture
def analyzer(audit):
    return RiskAnalyzer(audit, CurrencyConverter(), time_provider=at(DAY))


class TestLargeAmount:
    def test_amount_below_threshold_is_quiet(self, analyzer):
        assessment = analyzer.assess(make_transfer("100"), client_id=1)

        assert LARGE_AMOUNT_RULE not in assessment.reasons

    def test_amount_above_threshold_triggers(self, analyzer):
        assessment = analyzer.assess(make_transfer("100001"), client_id=1)

        assert LARGE_AMOUNT_RULE in assessment.reasons

    def test_exact_threshold_is_not_large(self, analyzer):
        assessment = analyzer.assess(make_transfer("100000"), client_id=1)

        assert LARGE_AMOUNT_RULE not in assessment.reasons

    def test_foreign_currency_is_converted_first(self, analyzer):
        assessment = analyzer.assess(
            make_transfer("2000", currency=Currency.USD), client_id=1
        )

        assert LARGE_AMOUNT_RULE in assessment.reasons

    def test_threshold_is_configurable(self, audit):
        analyzer = RiskAnalyzer(
            audit,
            CurrencyConverter(),
            large_amount=Decimal("50"),
            time_provider=at(DAY),
        )

        assessment = analyzer.assess(make_transfer("60"), client_id=1)

        assert LARGE_AMOUNT_RULE in assessment.reasons


class TestFrequentOperations:
    @pytest.fixture
    def analyzer(self, audit):
        return RiskAnalyzer(
            audit,
            CurrencyConverter(),
            max_operations=3,
            window=timedelta(minutes=5),
            time_provider=at(DAY),
        )

    def test_first_operations_are_quiet(self, analyzer):
        for _ in range(2):
            analyzer.evaluate(make_transfer(), client_id=1)

        assessment = analyzer.assess(make_transfer(), client_id=1)

        assert FREQUENT_OPERATIONS_RULE not in assessment.reasons

    def test_operation_over_the_limit_triggers(self, analyzer):
        for _ in range(3):
            analyzer.evaluate(make_transfer(), client_id=1)

        assessment = analyzer.assess(make_transfer(), client_id=1)

        assert FREQUENT_OPERATIONS_RULE in assessment.reasons

    def test_old_operations_fall_out_of_the_window(self, audit):
        analyzer = RiskAnalyzer(
            audit,
            CurrencyConverter(),
            max_operations=2,
            window=timedelta(minutes=5),
            time_provider=at(DAY),
        )
        for _ in range(3):
            analyzer.evaluate(make_transfer(), client_id=1)

        assessment = analyzer.assess(
            make_transfer(), client_id=1, now=DAY + timedelta(minutes=6)
        )

        assert FREQUENT_OPERATIONS_RULE not in assessment.reasons

    def test_another_client_history_does_not_count(self, analyzer):
        for _ in range(5):
            analyzer.evaluate(make_transfer(), client_id=2)

        assessment = analyzer.assess(make_transfer(), client_id=1)

        assert FREQUENT_OPERATIONS_RULE not in assessment.reasons

    def test_unknown_client_has_no_history(self, analyzer):
        for _ in range(5):
            analyzer.evaluate(make_transfer(), client_id=None)

        assessment = analyzer.assess(make_transfer(), client_id=None)

        assert FREQUENT_OPERATIONS_RULE not in assessment.reasons


class TestNewCounterparty:
    def test_first_transfer_to_an_account_is_new(self, analyzer):
        assessment = analyzer.assess(make_transfer(target="acc-9"), client_id=1)

        assert NEW_COUNTERPARTY_RULE in assessment.reasons

    def test_second_transfer_to_the_same_account_is_known(self, analyzer):
        complete(analyzer, make_transfer(target="acc-9"), client_id=1)

        assessment = analyzer.assess(make_transfer(target="acc-9"), client_id=1)

        assert NEW_COUNTERPARTY_RULE not in assessment.reasons

    def test_blocked_attempt_does_not_make_the_account_known(self, analyzer):
        analyzer.evaluate(
            make_transfer("200000", target="acc-9"), client_id=1, now=NIGHT
        )

        assessment = analyzer.assess(make_transfer(target="acc-9"), client_id=1)

        assert NEW_COUNTERPARTY_RULE in assessment.reasons

    def test_assessed_but_unfinished_transfer_is_not_known(self, analyzer):
        analyzer.evaluate(make_transfer(target="acc-9"), client_id=1)

        assessment = analyzer.assess(make_transfer(target="acc-9"), client_id=1)

        assert NEW_COUNTERPARTY_RULE in assessment.reasons

    def test_failed_transfer_does_not_make_the_account_known(self, analyzer):
        fail(analyzer, make_transfer(target="acc-9"), client_id=1)

        assessment = analyzer.assess(make_transfer(target="acc-9"), client_id=1)

        assert NEW_COUNTERPARTY_RULE in assessment.reasons

    def test_lost_outcome_record_still_makes_the_account_known(self):
        analyzer = RiskAnalyzer(OutcomeLosingAuditLog(), CurrencyConverter())

        with pytest.raises(AuditWriteError):
            complete(analyzer, make_transfer(target="acc-9"), client_id=1)

        assessment = analyzer.assess(make_transfer(target="acc-9"), client_id=1)

        assert NEW_COUNTERPARTY_RULE not in assessment.reasons

    def test_forged_audit_records_do_not_change_the_decision(self, analyzer, audit):
        audit.record(
            COMPLETED_EVENT,
            client_id=1,
            transaction_type="transfer",
            target_account_id="acc-9",
        )

        assessment = analyzer.assess(make_transfer(target="acc-9"), client_id=1)

        assert NEW_COUNTERPARTY_RULE in assessment.reasons

    def test_history_of_another_client_does_not_help(self, analyzer):
        analyzer.evaluate(make_transfer(target="acc-9"), client_id=2)

        assessment = analyzer.assess(make_transfer(target="acc-9"), client_id=1)

        assert NEW_COUNTERPARTY_RULE in assessment.reasons

    def test_deposit_has_no_counterparty_rule(self, analyzer):
        assessment = analyzer.assess(make_deposit(), client_id=1)

        assert NEW_COUNTERPARTY_RULE not in assessment.reasons

    def test_unknown_client_is_always_new(self, analyzer):
        assessment = analyzer.assess(make_transfer(), client_id=None)

        assert NEW_COUNTERPARTY_RULE in assessment.reasons


class TestNightTime:
    @pytest.mark.parametrize("hour", [0, 2, 4])
    def test_night_hours_trigger(self, audit, hour):
        moment = datetime(2026, 9, 4, hour, 30)
        analyzer = RiskAnalyzer(audit, CurrencyConverter(), time_provider=at(moment))

        assessment = analyzer.assess(make_transfer(), client_id=1)

        assert NIGHT_TIME_RULE in assessment.reasons

    @pytest.mark.parametrize("hour", [5, 12, 23])
    def test_day_hours_are_quiet(self, audit, hour):
        moment = datetime(2026, 9, 4, hour, 30)
        analyzer = RiskAnalyzer(audit, CurrencyConverter(), time_provider=at(moment))

        assessment = analyzer.assess(make_transfer(), client_id=1)

        assert NIGHT_TIME_RULE not in assessment.reasons

    def test_explicit_moment_wins_over_the_clock(self, analyzer):
        assessment = analyzer.assess(make_transfer(), client_id=1, now=NIGHT)

        assert NIGHT_TIME_RULE in assessment.reasons

    @pytest.mark.parametrize("hour", [23, 0, 1])
    def test_window_across_midnight_is_a_set_of_hours(self, audit, hour):
        analyzer = RiskAnalyzer(audit, CurrencyConverter(), night_hours={23, 0, 1})

        assessment = analyzer.assess(
            make_transfer(), client_id=1, now=datetime(2026, 9, 4, hour, 30)
        )

        assert NIGHT_TIME_RULE in assessment.reasons

    def test_empty_night_hours_turn_the_rule_off(self, audit):
        analyzer = RiskAnalyzer(audit, CurrencyConverter(), night_hours=())

        assessment = analyzer.assess(make_transfer(), client_id=1, now=NIGHT)

        assert NIGHT_TIME_RULE not in assessment.reasons


class TestScoring:
    def test_no_rules_means_low(self, analyzer):
        complete(analyzer, make_transfer(target="acc-2"), client_id=1)

        assessment = analyzer.assess(make_transfer(target="acc-2"), client_id=1)

        assert assessment.score == 0
        assert assessment.level is RiskLevel.LOW

    def test_new_counterparty_alone_stays_low(self, analyzer):
        assessment = analyzer.assess(make_transfer(), client_id=1)

        assert assessment.score == 1
        assert assessment.level is RiskLevel.LOW

    def test_large_amount_alone_is_medium(self, analyzer):
        complete(analyzer, make_transfer(target="acc-2"), client_id=1)

        assessment = analyzer.assess(make_transfer("200000"), client_id=1)

        assert assessment.score == 2
        assert assessment.level is RiskLevel.MEDIUM

    def test_large_new_and_night_is_high(self, analyzer):
        assessment = analyzer.assess(make_transfer("200000"), client_id=1, now=NIGHT)

        assert assessment.score == 4
        assert assessment.level is RiskLevel.HIGH

    def test_high_level_is_blocked(self, analyzer):
        assessment = analyzer.assess(make_transfer("200000"), client_id=1, now=NIGHT)

        assert assessment.is_blocked is True

    def test_medium_level_is_not_blocked(self, analyzer):
        assessment = analyzer.assess(make_transfer("200000"), client_id=1)

        assert assessment.is_blocked is False

    @pytest.mark.parametrize(
        ("level", "severity"),
        [
            (RiskLevel.LOW, AuditSeverity.INFO),
            (RiskLevel.MEDIUM, AuditSeverity.WARNING),
            (RiskLevel.HIGH, AuditSeverity.CRITICAL),
        ],
    )
    def test_severity_follows_the_level(self, level, severity):
        assert RiskAssessment(level, 0).severity is severity

    def test_string_form_lists_the_reasons(self, analyzer):
        assessment = analyzer.assess(make_transfer("200000"), client_id=1)

        assert "large_amount" in str(assessment)
        assert "score=3" in str(assessment)

    def test_string_form_without_reasons(self):
        assert "none" in str(RiskAssessment(RiskLevel.LOW, 0))

    def test_thresholds_are_configurable(self, audit):
        analyzer = RiskAnalyzer(
            audit,
            CurrencyConverter(),
            medium_score=1,
            high_score=1,
            time_provider=at(DAY),
        )

        assessment = analyzer.assess(make_transfer(), client_id=1)

        assert assessment.level is RiskLevel.HIGH


class TestEvaluate:
    def test_writes_one_audit_record(self, analyzer, audit):
        analyzer.evaluate(make_transfer(), client_id=1)

        assert len(audit.filter(event=ASSESSMENT_EVENT)) == 1

    def test_record_carries_the_verdict(self, analyzer, audit):
        transaction = make_transfer("200000")

        assessment = analyzer.evaluate(transaction, client_id=1, now=NIGHT)

        record = audit.filter(event=ASSESSMENT_EVENT)[0]
        assert record["severity"] is AuditSeverity.CRITICAL
        assert record["client_id"] == 1
        assert record["details"]["transaction_id"] == transaction.transaction_id
        assert record["details"]["risk_level"] == str(RiskLevel.HIGH)
        assert record["details"]["score"] == assessment.score

    def test_record_time_matches_the_assessed_moment(self, analyzer, audit):
        """Часы вызывающего важнее часов журнала: иначе окно частоты пустует."""
        analyzer.evaluate(make_transfer(), client_id=1, now=NIGHT)

        assert audit.records[0]["time"] == NIGHT

    def test_history_stays_inside_the_window_of_a_foreign_clock(self, analyzer):
        for _ in range(6):
            analyzer.evaluate(make_transfer(), client_id=1, now=NIGHT)

        assessment = analyzer.assess(make_transfer(), client_id=1, now=NIGHT)

        assert FREQUENT_OPERATIONS_RULE in assessment.reasons

    def test_assess_alone_writes_nothing(self, analyzer, audit):
        analyzer.assess(make_transfer(), client_id=1)

        assert audit.records == []


class TestValidation:
    @pytest.mark.parametrize("value", [0, -1, "many", None])
    def test_max_operations_must_be_positive_integer(self, audit, value):
        with pytest.raises(InvalidOperationError):
            RiskAnalyzer(audit, CurrencyConverter(), max_operations=value)

    @pytest.mark.parametrize("value", [timedelta(0), timedelta(minutes=-1), 300])
    def test_window_must_be_a_positive_timedelta(self, audit, value):
        with pytest.raises(InvalidOperationError):
            RiskAnalyzer(audit, CurrencyConverter(), window=value)

    @pytest.mark.parametrize("value", [Decimal("0"), Decimal("-1"), 1.5, "100"])
    def test_large_amount_must_be_positive_money(self, audit, value):
        with pytest.raises(InvalidOperationError):
            RiskAnalyzer(audit, CurrencyConverter(), large_amount=value)

    def test_medium_score_cannot_exceed_high(self, audit):
        with pytest.raises(InvalidOperationError):
            RiskAnalyzer(audit, CurrencyConverter(), medium_score=5, high_score=4)

    def test_scores_must_be_integers(self, audit):
        with pytest.raises(InvalidOperationError):
            RiskAnalyzer(audit, CurrencyConverter(), medium_score=1.5)

    @pytest.mark.parametrize("value", [None, 5, "0123", [24], [-1], [True], [1.5]])
    def test_night_hours_must_be_hours_of_the_day(self, audit, value):
        with pytest.raises(InvalidOperationError):
            RiskAnalyzer(audit, CurrencyConverter(), night_hours=value)

    def test_base_currency_must_be_an_enum_member(self, audit):
        with pytest.raises(InvalidOperationError):
            RiskAnalyzer(audit, CurrencyConverter(), base_currency="rub")


class TestBankRiskWiring:
    def test_bank_analyzer_uses_the_bank_clock_and_rates(self):
        rates = CurrencyConverter().rates
        rates[Currency.USD] = DEFAULT_LARGE_AMOUNT
        bank = Bank(
            "Wired", time_provider=at(NIGHT), converter=CurrencyConverter(rates)
        )
        deposit = Transaction(
            TransactionType.DEPOSIT,
            Decimal("2"),
            target_account_id="acc-1",
            currency=Currency.USD,
        )

        assessment = bank.assess_transaction(deposit)

        assert set(assessment.reasons) == {LARGE_AMOUNT_RULE, NIGHT_TIME_RULE}


class TestBankBlocking:
    """Банк и процессор: высокий риск не должен дойти до денег."""

    @pytest.fixture
    def strict_bank(self, make_client):
        bank = Bank(
            "Strict Bank",
            time_provider=at(DAY),
            risk_settings={
                "large_amount": Decimal("1000"),
                "medium_score": 2,
                "high_score": 3,
            },
        )
        bank.add_client(make_client(client_id=1, full_name="Max Petrov"))
        bank.add_client(make_client(client_id=2, full_name="Anna Ivanova"))
        bank.open_account(1, "bank", balance=Decimal("10000"))
        bank.open_account(2, "bank", balance=Decimal("1000"))
        return bank

    @pytest.fixture
    def accounts(self, strict_bank):
        return (
            strict_bank.search_accounts(client_id=1)[0],
            strict_bank.search_accounts(client_id=2)[0],
        )

    @pytest.fixture
    def processor(self, strict_bank):
        return TransactionProcessor(strict_bank, time_provider=at(DAY))

    def transfer(self, accounts, amount):
        source, target = accounts
        return Transaction(
            TransactionType.TRANSFER,
            Decimal(amount),
            source_account_id=source.account_id,
            target_account_id=target.account_id,
            time_provider=at(DAY),
        )

    def test_high_risk_transfer_is_refused(self, strict_bank, accounts):
        with pytest.raises(RiskBlockedError):
            strict_bank.assess_transaction(self.transfer(accounts, "5000"))

    def test_blocked_transfer_fails_without_moving_money(self, processor, accounts):
        source, target = accounts
        transaction = self.transfer(accounts, "5000")

        assert processor.process(transaction) is False
        assert transaction.status is TransactionStatus.FAILED
        assert source.balance == Decimal("10000.00")
        assert target.balance == Decimal("1000.00")

    def test_blocked_transfer_is_written_to_the_error_journal(
        self, processor, accounts
    ):
        processor.process(self.transfer(accounts, "5000"))

        assert processor.errors[0]["error"] == "RiskBlockedError"

    def test_blocked_transfer_marks_the_client(self, strict_bank, processor, accounts):
        processor.process(self.transfer(accounts, "5000"))

        assert strict_bank.clients[1].status is ClientStatus.SUSPICIOUS

    def test_blocked_transfer_leaves_a_critical_record(
        self, strict_bank, processor, accounts
    ):
        processor.process(self.transfer(accounts, "5000"))

        records = strict_bank.audit_log.filter(
            event=ASSESSMENT_EVENT, min_severity=AuditSeverity.CRITICAL
        )
        assert len(records) == 1

    def test_low_risk_transfer_goes_through(self, strict_bank, processor, accounts):
        source, target = accounts
        transaction = self.transfer(accounts, "100")

        assert processor.process(transaction) is True
        assert source.balance == Decimal("9900.00")
        assert target.balance == Decimal("1100.00")
        record = strict_bank.audit_log.filter(event=ASSESSMENT_EVENT)[0]
        assert record["details"]["risk_level"] == str(RiskLevel.LOW)

    def test_medium_risk_transfer_is_only_warned_about(
        self, strict_bank, processor, accounts
    ):
        # Первый перевод делает счёт получателя знакомым, остаётся одна крупная сумма.
        processor.process(self.transfer(accounts, "100"))
        transaction = self.transfer(accounts, "2000")

        assert processor.process(transaction) is True

        record = strict_bank.audit_log.filter(event=ASSESSMENT_EVENT)[-1]
        assert record["severity"] is AuditSeverity.WARNING
        assert record["details"]["risk_level"] == str(RiskLevel.MEDIUM)

    def test_risk_is_assessed_once_per_transaction(self, strict_bank, accounts):
        def broken_gateway(transaction):
            raise TransientTransactionError("gateway is down")

        processor = TransactionProcessor(
            strict_bank, external_gateway=broken_gateway, time_provider=at(DAY)
        )
        source, _ = accounts
        transaction = Transaction(
            TransactionType.EXTERNAL_TRANSFER,
            Decimal("100"),
            source_account_id=source.account_id,
            time_provider=at(DAY),
        )

        processor.process(transaction)

        assert transaction.attempts == 3
        assert len(strict_bank.audit_log.filter(event=ASSESSMENT_EVENT)) == 1

    def test_suspicious_operations_report_sees_the_block(
        self, strict_bank, processor, accounts
    ):
        processor.process(self.transfer(accounts, "5000"))

        found = strict_bank.get_suspicious_operations(client_id=1)

        assert [record["event"] for record in found] == [ASSESSMENT_EVENT]
        assert found[0]["severity"] is AuditSeverity.CRITICAL

    def test_risk_profile_counts_the_block(self, strict_bank, processor, accounts):
        processor.process(self.transfer(accounts, "5000"))

        profile = strict_bank.get_client_risk_profile(1)
        assert profile["blocked"] == 1
        assert profile["level"] is RiskLevel.HIGH

    def test_error_statistics_come_from_the_processor(
        self, strict_bank, processor, accounts
    ):
        processor.process(self.transfer(accounts, "5000"))

        statistics = strict_bank.get_error_statistics(processor.errors)
        assert statistics["by_error"] == {"RiskBlockedError": 1}

    def test_blocked_transfer_leaves_no_duplicate_fraud_record(
        self, strict_bank, processor, accounts
    ):
        processor.process(self.transfer(accounts, "5000"))

        assert strict_bank.suspicious_actions == []

    def test_failed_transfer_does_not_make_the_target_known(
        self, strict_bank, processor, accounts
    ):
        _, target = accounts
        strict_bank.freeze_account(target.account_id)
        processor.process(self.transfer(accounts, "10"))
        strict_bank.unfreeze_account(target.account_id)

        transaction = self.transfer(accounts, "5000")

        assert processor.process(transaction) is False
        assert "new_counterparty" in transaction.failure_reason

    def test_outcomes_reach_the_audit_log(self, strict_bank, processor, accounts):
        processor.process(self.transfer(accounts, "100"))
        processor.process(self.transfer(accounts, "999999"))

        completed = strict_bank.audit_log.filter(event=COMPLETED_EVENT)
        failed = strict_bank.audit_log.filter(event=FAILED_EVENT)
        assert len(completed) == 1
        assert failed[0]["details"]["error"] == "InsufficientFundsError"

    def test_process_moment_reaches_the_risk_rules(
        self, strict_bank, processor, accounts
    ):
        transaction = self.transfer(accounts, "10")

        processor.process(transaction, now=NIGHT)

        record = strict_bank.audit_log.filter(event=ASSESSMENT_EVENT)[0]
        assert record["time"] == NIGHT
        assert "night_time" in record["details"]["reasons"]

    def test_queue_fails_only_the_blocked_item(self, strict_bank, processor, accounts):
        queue = TransactionQueue()
        queue.add(self.transfer(accounts, "5000"))
        queue.add(self.transfer(accounts, "100"))
        queue.add(self.transfer(accounts, "200"))

        summary = processor.process_queue(queue, now=DAY)

        assert summary == {"processed": 3, "completed": 2, "failed": 1}

    def test_deposit_is_assessed_for_the_receiving_client(
        self, strict_bank, processor, accounts
    ):
        _, target = accounts
        deposit = Transaction(
            TransactionType.DEPOSIT,
            Decimal("100"),
            target_account_id=target.account_id,
            time_provider=at(DAY),
        )

        processor.process(deposit)

        record = strict_bank.audit_log.filter(event=ASSESSMENT_EVENT)[0]
        assert record["client_id"] == 2
        assert record["account_id"] == target.account_id

    def test_deposit_can_be_found_by_its_account(
        self, strict_bank, processor, accounts
    ):
        _, target = accounts
        processor.process(
            Transaction(
                TransactionType.DEPOSIT,
                Decimal("5000"),
                target_account_id=target.account_id,
                time_provider=at(DAY),
            )
        )

        found = strict_bank.audit_log.filter(
            event=ASSESSMENT_EVENT, account_id=target.account_id
        )

        assert len(found) == 1

    def test_unknown_source_account_is_assessed_without_a_client(
        self, strict_bank, processor, accounts
    ):
        _, target = accounts
        transaction = Transaction(
            TransactionType.TRANSFER,
            Decimal("100"),
            source_account_id="missing",
            target_account_id=target.account_id,
            time_provider=at(DAY),
        )

        assert processor.process(transaction) is False

        record = strict_bank.audit_log.filter(event=ASSESSMENT_EVENT)[0]
        assert record["client_id"] is None
        assert transaction.failure_reason == "Account not found"
        assert all(
            client.status is ClientStatus.ACTIVE
            for client in strict_bank.clients.values()
        )

    def test_bank_fraud_journal_writes_into_the_bank_audit_log(self, strict_bank):
        strict_bank.authenticate_client(1, "wrong-password")

        found = strict_bank.audit_log.filter(event="fraud_action", client_id=1)

        assert [record["details"]["action"] for record in found] == ["failed_login"]


class TestAuditOutcomeRecording:
    def test_completed_transfer_is_recorded(self, analyzer, audit):
        complete(analyzer, make_transfer(), client_id=1)

        record = audit.filter(event=COMPLETED_EVENT)[0]
        assert record["client_id"] == 1
        assert record["details"]["error"] is None

    def test_failed_transfer_keeps_the_error_name(self, analyzer, audit):
        fail(analyzer, make_transfer(), client_id=1, error=TransientTransactionError())

        record = audit.filter(event=FAILED_EVENT)[0]
        assert record["details"]["error"] == "TransientTransactionError"
        assert record["details"]["reason"] == "boom"

    def test_unfinished_transaction_has_no_outcome(self, analyzer):
        with pytest.raises(InvalidOperationError):
            analyzer.record_outcome(make_transfer(), client_id=1)

    def test_completed_deposit_adds_no_counterparty(self, analyzer):
        deposit = make_deposit()
        complete(analyzer, deposit, client_id=1)

        assessment = analyzer.assess(make_transfer(target="acc-2"), client_id=1)

        assert NEW_COUNTERPARTY_RULE in assessment.reasons


class TestDefaultThresholds:
    """Договорённые значения: 100 000 руб., 5 операций за 5 минут, веса 2-2-1-1."""

    def test_five_recent_operations_make_the_next_frequent(self, analyzer):
        for minute in range(5):
            analyzer.evaluate(
                make_deposit(), client_id=1, now=DAY + timedelta(minutes=minute)
            )

        assessment = analyzer.assess(
            make_deposit(), client_id=1, now=DAY + timedelta(minutes=4, seconds=59)
        )

        assert FREQUENT_OPERATIONS_RULE in assessment.reasons

    def test_four_recent_operations_are_not_frequent(self, analyzer):
        for minute in range(4):
            analyzer.evaluate(
                make_deposit(), client_id=1, now=DAY + timedelta(minutes=minute)
            )

        assessment = analyzer.assess(
            make_deposit(), client_id=1, now=DAY + timedelta(minutes=4)
        )

        assert FREQUENT_OPERATIONS_RULE not in assessment.reasons

    def test_operation_older_than_five_minutes_does_not_count(self, analyzer):
        for minute in range(5):
            analyzer.evaluate(
                make_deposit(), client_id=1, now=DAY + timedelta(minutes=minute)
            )

        assessment = analyzer.assess(
            make_deposit(), client_id=1, now=DAY + timedelta(minutes=5, seconds=1)
        )

        assert FREQUENT_OPERATIONS_RULE not in assessment.reasons

    def test_large_amount_threshold_is_one_hundred_thousand(self, analyzer):
        assert LARGE_AMOUNT_RULE not in analyzer.assess(make_deposit("100000")).reasons
        assert LARGE_AMOUNT_RULE in analyzer.assess(make_deposit("100000.01")).reasons

    def test_out_of_order_moments_are_counted_correctly(self, analyzer):
        # Минута 0 вне окна: без сортировки бинарный поиск ошибся бы.
        for minute in (10, 0, 11, 12, 13, 14):
            analyzer.evaluate(
                make_deposit(), client_id=1, now=DAY + timedelta(minutes=minute)
            )

        assessment = analyzer.assess(
            make_deposit(), client_id=1, now=DAY + timedelta(minutes=14)
        )

        assert FREQUENT_OPERATIONS_RULE in assessment.reasons


class TestDefaultBankBlocking:
    """Банк без подменённого анализатора, договорённые пороги, не только переводы."""

    @pytest.fixture
    def bank(self, make_client):
        bank = Bank("Default Bank", time_provider=at(DAY))
        bank.add_client(make_client(client_id=1, full_name="Max Petrov"))
        bank.open_account(1, "bank", balance=Decimal("500000"))
        return bank

    @pytest.fixture
    def account(self, bank):
        return bank.search_accounts(client_id=1)[0]

    def warm_up(self, processor, account, operations=5):
        for _ in range(operations):
            processor.process(
                Transaction(
                    TransactionType.DEPOSIT,
                    Decimal("1"),
                    target_account_id=account.account_id,
                    time_provider=at(DAY),
                )
            )

    @pytest.mark.parametrize(
        "transaction_type",
        [TransactionType.WITHDRAWAL, TransactionType.EXTERNAL_TRANSFER],
    )
    def test_large_frequent_outflow_is_blocked(self, bank, account, transaction_type):
        processor = TransactionProcessor(bank, time_provider=at(DAY))
        self.warm_up(processor, account)
        balance = account.balance
        transaction = Transaction(
            transaction_type,
            Decimal("150000"),
            source_account_id=account.account_id,
            time_provider=at(DAY),
        )

        assert processor.process(transaction) is False
        assert processor.errors[-1]["error"] == "RiskBlockedError"
        assert account.balance == balance

    def test_large_outflow_without_a_burst_goes_through(self, bank, account):
        processor = TransactionProcessor(bank, time_provider=at(DAY))
        self.warm_up(processor, account, operations=4)
        transaction = Transaction(
            TransactionType.WITHDRAWAL,
            Decimal("150000"),
            source_account_id=account.account_id,
            time_provider=at(DAY),
        )

        assert processor.process(transaction) is True

    def test_audit_file_holds_assessments_outcomes_and_fraud_actions(
        self, make_client, tmp_path
    ):
        path = tmp_path / "audit.jsonl"
        bank = Bank(
            "File Bank",
            time_provider=at(DAY),
            audit_log=AuditLog(time_provider=at(DAY), file_path=path),
        )
        bank.add_client(make_client(client_id=1, full_name="Max Petrov"))
        account = bank.open_account(1, "bank", balance=Decimal("100"))
        processor = TransactionProcessor(bank, time_provider=at(DAY))

        bank.authenticate_client(1, "wrong-password")
        processor.process(
            Transaction(
                TransactionType.DEPOSIT,
                Decimal("10"),
                target_account_id=account.account_id,
                time_provider=at(DAY),
            )
        )

        events = [
            json.loads(line)["event"]
            for line in path.read_text(encoding="utf-8").splitlines()
        ]
        assert events == [
            "account_opened",
            "fraud_action",
            ASSESSMENT_EVENT,
            COMPLETED_EVENT,
        ]

    def test_unwritable_audit_blocks_the_operation_and_keeps_the_queue_alive(
        self, make_client, tmp_path
    ):
        path = tmp_path / "audit.jsonl"
        bank = Bank(
            "Broken Audit Bank",
            time_provider=at(DAY),
            audit_log=AuditLog(time_provider=at(DAY), file_path=path),
        )
        bank.add_client(make_client(client_id=1, full_name="Max Petrov"))
        account = bank.open_account(1, "bank", balance=Decimal("100"))
        # Журнал ломается уже после открытия счёта: на месте файла - папка.
        path.unlink()
        path.mkdir()
        records_before = bank.audit_log.records
        processor = TransactionProcessor(bank, time_provider=at(DAY))
        deposit = Transaction(
            TransactionType.DEPOSIT,
            Decimal("10"),
            target_account_id=account.account_id,
            time_provider=at(DAY),
        )

        assert processor.process(deposit) is False
        assert processor.errors[0]["error"] == AuditWriteError.__name__
        assert account.balance == Decimal("100.00")
        assert bank.audit_log.records == records_before


class TestAuditFailuresInProcessing:
    """Итог уже наступил: сбои журнала не должны ронять очередь и путать риск."""

    @pytest.fixture
    def make_bank(self, make_client):
        def factory(audit_log):
            bank = Bank("Flaky Bank", time_provider=at(DAY), audit_log=audit_log)
            bank.add_client(make_client(client_id=1, full_name="Max Petrov"))
            bank.add_client(make_client(client_id=2, full_name="Anna Ivanova"))
            source = bank.open_account(1, "bank", balance=Decimal("10000"))
            target = bank.open_account(2, "bank", balance=Decimal("0"))
            return bank, source, target

        return factory

    @staticmethod
    def transfer(source, target, amount="100"):
        return Transaction(
            TransactionType.TRANSFER,
            Decimal(amount),
            source_account_id=source.account_id,
            target_account_id=target.account_id,
            time_provider=at(DAY),
        )

    def test_lost_outcome_is_logged_and_the_target_stays_known(
        self, make_bank, log_events
    ):
        bank, source, target = make_bank(OutcomeLosingAuditLog(time_provider=at(DAY)))
        processor = TransactionProcessor(bank, time_provider=at(DAY))
        transaction = self.transfer(source, target)

        assert processor.process(transaction) is True

        lost = [
            event for event in log_events if event["message"] == "audit_outcome_lost"
        ]
        assert lost[0]["extra"]["transaction_id"] == transaction.transaction_id
        assessment = bank.risk_analyzer.assess(
            self.transfer(source, target), client_id=1
        )
        assert NEW_COUNTERPARTY_RULE not in assessment.reasons

    def test_text_that_cannot_be_written_does_not_stop_the_queue(
        self, make_bank, tmp_path, log_events
    ):
        path = tmp_path / "audit.jsonl"
        bank, source, target = make_bank(
            AuditLog(time_provider=at(DAY), file_path=path)
        )

        def gateway(transaction):
            raise RuntimeError(b"gateway said \xff".decode("utf-8", "surrogateescape"))

        processor = TransactionProcessor(
            bank, external_gateway=gateway, time_provider=at(DAY)
        )
        queue = TransactionQueue()
        queue.add(
            Transaction(
                TransactionType.EXTERNAL_TRANSFER,
                Decimal("10"),
                source_account_id=source.account_id,
                time_provider=at(DAY),
            )
        )
        queue.add(self.transfer(source, target))

        summary = processor.process_queue(queue, now=DAY)

        assert summary == {"processed": 2, "completed": 1, "failed": 1}
        assert any(event["message"] == "audit_outcome_lost" for event in log_events)
        events = [
            json.loads(line)["event"]
            for line in path.read_text(encoding="utf-8").splitlines()
        ]
        operations = [event for event in events if event != "account_opened"]
        assert operations == [ASSESSMENT_EVENT, ASSESSMENT_EVENT, COMPLETED_EVENT]

    def test_exception_without_message_fails_with_its_class_name(self, make_bank):
        bank, source, _ = make_bank(AuditLog(time_provider=at(DAY)))

        def gateway(transaction):
            raise ConnectionError()

        processor = TransactionProcessor(
            bank, external_gateway=gateway, time_provider=at(DAY)
        )
        transaction = Transaction(
            TransactionType.EXTERNAL_TRANSFER,
            Decimal("10"),
            source_account_id=source.account_id,
            time_provider=at(DAY),
        )

        assert processor.process(transaction) is False
        assert transaction.status is TransactionStatus.FAILED
        assert transaction.failure_reason == "ConnectionError"
        assert processor.errors[0]["message"] == "ConnectionError"
        assert len(bank.audit_log.filter(event=FAILED_EVENT)) == 1

    def test_outcome_time_follows_the_processor_clock(self, make_bank):
        bank, source, target = make_bank(AuditLog(time_provider=at(DAY)))
        later = DAY + timedelta(hours=1)
        processor = TransactionProcessor(bank, time_provider=at(later))
        transaction = self.transfer(source, target)

        processor.process(transaction)

        record = bank.audit_log.filter(event=COMPLETED_EVENT)[0]
        assert record["time"] == transaction.updated_at == later
