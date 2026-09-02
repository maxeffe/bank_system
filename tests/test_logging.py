from decimal import Decimal
from pathlib import Path

import pytest

from src.enums import Currency
from src.models.accounts import BankAccount, PremiumAccount

MODELS_DIR = Path(__file__).resolve().parent.parent / "src" / "models"


def make_account(**kwargs):
    kwargs.setdefault("user_id", 1)
    kwargs.setdefault("owner_name", "Max Petrov")
    kwargs.setdefault("balance", Decimal("1000"))
    return BankAccount(**kwargs)


class TestModelsDoNotPrint:
    @pytest.mark.parametrize(
        "path", sorted(MODELS_DIR.glob("*.py")), ids=lambda p: p.name
    )
    def test_no_print_in_domain_models(self, path):
        assert "print(" not in path.read_text(encoding="utf-8")

    def test_nothing_goes_to_stdout(self, capsys):
        account = make_account()

        account.deposit(Decimal("100"))
        account.withdraw(Decimal("50"))

        assert capsys.readouterr().out == ""


class TestStructuredEvents:
    def test_deposit_event_carries_fields(self, log_events):
        account = make_account(balance=Decimal("0"))

        account.deposit(Decimal("100"))

        event = log_events[-1]
        assert event["message"] == "deposit"
        assert event["extra"] == {
            "account_id": account.account_id,
            "amount": Decimal("100.00"),
            "currency": Currency.RUB,
            "balance": Decimal("100.00"),
        }

    def test_withdrawal_event_carries_fields(self, log_events):
        account = make_account(balance=Decimal("500"))

        account.withdraw(Decimal("200"))

        event = log_events[-1]
        assert event["message"] == "withdrawal"
        assert event["extra"]["amount"] == Decimal("200.00")
        assert event["extra"]["balance"] == Decimal("300.00")

    def test_premium_event_carries_the_fee(self, log_events):
        account = PremiumAccount(
            user_id=1,
            owner_name="Anna",
            balance=Decimal("1000"),
            fixed_fee=Decimal("25"),
        )

        account.withdraw(Decimal("100"))

        assert log_events[-1]["extra"]["fee"] == Decimal("25.00")

    def test_failed_operation_writes_nothing(self, log_events):
        account = make_account(balance=Decimal("10"))

        with pytest.raises(Exception):
            account.withdraw(Decimal("100"))

        assert log_events == []
