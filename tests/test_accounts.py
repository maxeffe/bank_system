from decimal import Decimal

import pytest

from src.enums import AccountStatus, Currency
from src.exceptions import (
    AccountClosedError,
    AccountFrozenError,
    InsufficientFundsError,
    InvalidOperationError,
)
from src.money import MAX_MONEY, MONEY_LIMIT_MESSAGE
from src.models.accounts import (
    BankAccount,
    InvestmentAccount,
    PremiumAccount,
    SavingsAccount,
)


def make_bank_account(**kwargs):
    kwargs.setdefault("user_id", 1)
    kwargs.setdefault("owner_name", "Max Petrov")
    kwargs.setdefault("balance", Decimal("1000"))
    return BankAccount(**kwargs)


class TestMoneyOnAccounts:
    def test_kopecks_are_not_lost(self):
        account = make_bank_account(balance=Decimal("0"))

        for _ in range(10):
            account.deposit(Decimal("0.10"))

        assert account.balance == Decimal("1.00")

    def test_amount_is_quantized_to_two_places(self):
        account = make_bank_account(balance=Decimal("0"))

        account.deposit(Decimal("10.005"))

        assert account.balance == Decimal("10.01")

    def test_float_is_rejected(self):
        account = make_bank_account()

        with pytest.raises(InvalidOperationError):
            account.deposit(0.1)


class TestBankAccountCreation:
    def test_creates_with_valid_data(self):
        account = make_bank_account(balance=Decimal("500"))

        assert account.balance == Decimal("500.00")
        assert account.status is AccountStatus.ACTIVE
        assert account.currency is Currency.RUB
        assert len(account.account_id) > 0

    @pytest.mark.parametrize(
        "field, value",
        [
            ("user_id", None),
            ("owner_name", ""),
            ("owner_name", "   "),
            ("owner_name", 123),
            ("balance", Decimal("-1")),
            ("balance", 0.5),
            ("status", "active"),
            ("currency", "rub"),
        ],
    )
    def test_rejects_invalid_field(self, field, value):
        with pytest.raises(InvalidOperationError):
            make_bank_account(**{field: value})

    def test_owner_name_setter_validates(self):
        account = make_bank_account()

        account.owner_name = "Anna Ivanova"
        assert account.owner_name == "Anna Ivanova"

        with pytest.raises(InvalidOperationError):
            account.owner_name = "  "

    def test_balance_has_no_setter(self):
        account = make_bank_account()

        with pytest.raises(AttributeError):
            account.balance = Decimal("999999")


class TestBankAccountOperations:
    def test_deposit_increases_balance(self):
        account = make_bank_account(balance=Decimal("100"))

        account.deposit(Decimal("50"))

        assert account.balance == Decimal("150.00")

    def test_withdraw_decreases_balance(self):
        account = make_bank_account(balance=Decimal("100"))

        account.withdraw(Decimal("30"))

        assert account.balance == Decimal("70.00")

    def test_withdraw_more_than_balance_fails(self):
        account = make_bank_account(balance=Decimal("100"))

        with pytest.raises(InsufficientFundsError):
            account.withdraw(Decimal("101"))

        assert account.balance == Decimal("100.00")

    @pytest.mark.parametrize("operation", ["deposit", "refund"])
    def test_balance_cannot_pass_the_money_limit(self, operation):
        account = make_bank_account(balance=MAX_MONEY)

        with pytest.raises(InvalidOperationError, match=MONEY_LIMIT_MESSAGE):
            getattr(account, operation)(Decimal("0.01"))

        assert account.balance == MAX_MONEY

    def test_interest_cannot_pass_the_money_limit(self):
        account = SavingsAccount(user_id=1, owner_name="Anna", balance=MAX_MONEY)

        with pytest.raises(InvalidOperationError, match=MONEY_LIMIT_MESSAGE):
            account.apply_monthly_interest()

        assert account.balance == MAX_MONEY

    @pytest.mark.parametrize("amount", [Decimal("0"), Decimal("-10")])
    def test_non_positive_amount_fails(self, amount):
        account = make_bank_account()

        with pytest.raises(InvalidOperationError):
            account.deposit(amount)


class TestAccountStatusGuard:
    def test_frozen_blocks_deposit_and_withdraw(self):
        account = make_bank_account()
        account.status = AccountStatus.FROZEN

        with pytest.raises(AccountFrozenError):
            account.deposit(Decimal("10"))

        with pytest.raises(AccountFrozenError):
            account.withdraw(Decimal("10"))

    def test_closed_blocks_deposit_and_withdraw(self):
        account = make_bank_account(balance=Decimal("0"))
        account.status = AccountStatus.CLOSED

        with pytest.raises(AccountClosedError):
            account.deposit(Decimal("10"))

        with pytest.raises(AccountClosedError):
            account.withdraw(Decimal("10"))

    @pytest.mark.parametrize("new_status", [AccountStatus.ACTIVE, AccountStatus.FROZEN])
    def test_closed_is_terminal(self, new_status):
        account = make_bank_account(balance=Decimal("0"))
        account.status = AccountStatus.CLOSED

        with pytest.raises(InvalidOperationError):
            account.status = new_status

        assert account.status is AccountStatus.CLOSED

    def test_closing_twice_is_allowed(self):
        account = make_bank_account(balance=Decimal("0"))
        account.status = AccountStatus.CLOSED

        account.status = AccountStatus.CLOSED

        assert account.status is AccountStatus.CLOSED

    def test_account_with_money_cannot_be_closed(self):
        account = make_bank_account()

        with pytest.raises(InvalidOperationError):
            account.status = AccountStatus.CLOSED

        assert account.status is AccountStatus.ACTIVE

    def test_account_with_money_cannot_be_created_closed(self):
        with pytest.raises(InvalidOperationError):
            make_bank_account(status=AccountStatus.CLOSED)

    def test_empty_account_can_be_created_closed(self):
        account = make_bank_account(balance=Decimal("0"), status=AccountStatus.CLOSED)

        assert account.status is AccountStatus.CLOSED

    def test_status_setter_rejects_non_enum(self):
        account = make_bank_account()

        with pytest.raises(InvalidOperationError):
            account.status = "frozen"


class TestWithdrawalRules:
    """Правила снятия спрашиваются у счёта, а не угадываются по типу."""

    def test_plain_account_has_no_fee_and_no_overdraft(self):
        account = make_bank_account()

        assert account.withdrawal_fee(Decimal("100")) == Decimal("0.00")
        assert account.min_allowed_balance == Decimal("0.00")

    def test_savings_reports_its_minimum(self):
        account = SavingsAccount(
            user_id=1,
            owner_name="Max",
            balance=Decimal("5000"),
            min_balance=Decimal("1000"),
        )

        assert account.withdrawal_fee(Decimal("100")) == Decimal("0.00")
        assert account.min_allowed_balance == Decimal("1000.00")

    def test_premium_reports_fee_and_overdraft(self):
        account = PremiumAccount(
            user_id=1,
            owner_name="Anna",
            balance=Decimal("1000"),
            fixed_fee=Decimal("25"),
            overdraft_limit=Decimal("500"),
        )

        assert account.withdrawal_fee(Decimal("100")) == Decimal("25.00")
        assert account.min_allowed_balance == Decimal("-500.00")

    def test_investment_uses_base_rules(self):
        account = InvestmentAccount(user_id=1, owner_name="Olga")

        assert account.withdrawal_fee(Decimal("100")) == Decimal("0.00")
        assert account.min_allowed_balance == Decimal("0.00")


class TestSavingsAccount:
    def test_min_balance_blocks_withdrawal(self):
        account = SavingsAccount(
            user_id=1,
            owner_name="Max",
            balance=Decimal("5000"),
            min_balance=Decimal("1000"),
        )

        with pytest.raises(InsufficientFundsError):
            account.withdraw(Decimal("4500"))

        assert account.balance == Decimal("5000.00")

    def test_withdrawal_down_to_min_balance_is_allowed(self):
        account = SavingsAccount(
            user_id=1,
            owner_name="Max",
            balance=Decimal("5000"),
            min_balance=Decimal("1000"),
        )

        account.withdraw(Decimal("4000"))

        assert account.balance == Decimal("1000.00")

    @pytest.mark.parametrize(
        "status, error",
        [(AccountStatus.FROZEN, AccountFrozenError)],
    )
    def test_status_is_checked_before_min_balance(self, status, error):
        account = SavingsAccount(
            user_id=1,
            owner_name="Max",
            balance=Decimal("5000"),
            min_balance=Decimal("1000"),
        )
        account.status = status

        with pytest.raises(error):
            account.withdraw(Decimal("4500"))

    def test_interest_is_rounded_to_kopecks(self):
        account = SavingsAccount(
            user_id=1,
            owner_name="Max",
            balance=Decimal("333.33"),
            monthly_interest_rate=Decimal("0.037"),
        )

        interest = account.apply_monthly_interest()

        assert interest == Decimal("12.33")
        assert account.balance == Decimal("345.66")

    def test_interest_blocked_on_frozen_account(self):
        account = SavingsAccount(user_id=1, owner_name="Max", balance=Decimal("100"))
        account.status = AccountStatus.FROZEN

        with pytest.raises(AccountFrozenError):
            account.apply_monthly_interest()

    def test_balance_below_min_balance_rejected(self):
        with pytest.raises(InvalidOperationError):
            SavingsAccount(
                user_id=1,
                owner_name="Max",
                balance=Decimal("100"),
                min_balance=Decimal("500"),
            )


class TestPremiumAccount:
    def test_fee_is_added_to_withdrawal(self):
        account = PremiumAccount(
            user_id=1,
            owner_name="Anna",
            balance=Decimal("1000"),
            fixed_fee=Decimal("25"),
        )

        account.withdraw(Decimal("100"))

        assert account.balance == Decimal("875.00")

    def test_overdraft_allows_negative_balance(self):
        account = PremiumAccount(
            user_id=1,
            owner_name="Anna",
            balance=Decimal("100"),
            overdraft_limit=Decimal("500"),
        )

        account.withdraw(Decimal("400"))

        assert account.balance == Decimal("-300.00")

    def test_overdraft_limit_is_enforced(self):
        account = PremiumAccount(
            user_id=1,
            owner_name="Anna",
            balance=Decimal("100"),
            overdraft_limit=Decimal("500"),
        )

        with pytest.raises(InsufficientFundsError):
            account.withdraw(Decimal("601"))

    def test_withdraw_limit_is_enforced(self):
        account = PremiumAccount(
            user_id=1,
            owner_name="Anna",
            balance=Decimal("100000"),
            withdraw_limit=Decimal("3000"),
        )

        with pytest.raises(InvalidOperationError):
            account.withdraw(Decimal("3001"))

    def test_frozen_premium_raises_status_error(self):
        account = PremiumAccount(user_id=1, owner_name="Anna", balance=Decimal("100"))
        account.status = AccountStatus.FROZEN

        with pytest.raises(AccountFrozenError):
            account.withdraw(Decimal("10"))


class TestInvestmentAccount:
    def test_missing_assets_default_to_zero(self):
        account = InvestmentAccount(
            user_id=1, owner_name="Olga", portfolio={"stocks": Decimal("1000")}
        )

        assert account.project_yearly_growth() == Decimal("120.00")

    def test_unknown_asset_rejected(self):
        with pytest.raises(InvalidOperationError):
            InvestmentAccount(
                user_id=1, owner_name="Olga", portfolio={"crypto": Decimal("100")}
            )

    def test_negative_asset_rejected(self):
        with pytest.raises(InvalidOperationError):
            InvestmentAccount(
                user_id=1, owner_name="Olga", portfolio={"stocks": Decimal("-1")}
            )

    def test_projected_growth(self):
        account = InvestmentAccount(
            user_id=1,
            owner_name="Olga",
            portfolio={
                "stocks": Decimal("1000"),
                "bonds": Decimal("500"),
                "etf": Decimal("800"),
            },
        )

        assert account.project_yearly_growth() == Decimal("209.00")


class TestPolymorphism:
    @pytest.mark.parametrize(
        "account_class",
        [BankAccount, SavingsAccount, PremiumAccount, InvestmentAccount],
    )
    @pytest.mark.parametrize("method", ["withdraw", "get_account_info", "__str__"])
    def test_every_type_defines_its_own_method(self, account_class, method):
        """День 2: каждый тип счёта переопределяет эти три метода."""
        assert method in account_class.__dict__

    def test_investment_withdraw_follows_base_rules(self):
        account = InvestmentAccount(
            user_id=1, owner_name="Olga", balance=Decimal("1000")
        )

        account.withdraw(Decimal("400"))

        assert account.balance == Decimal("600.00")

        with pytest.raises(InsufficientFundsError):
            account.withdraw(Decimal("601"))

    def test_each_type_reports_its_own_info(self):
        accounts = [
            BankAccount(user_id=1, owner_name="A", balance=Decimal("100")),
            SavingsAccount(user_id=2, owner_name="B", balance=Decimal("100")),
            PremiumAccount(user_id=3, owner_name="C", balance=Decimal("100")),
            InvestmentAccount(user_id=4, owner_name="D", balance=Decimal("100")),
        ]

        markers = [account.get_account_info() for account in accounts]

        assert "account_type: BANK" in markers[0]
        assert "account_type: SAVINGS" in markers[1]
        assert "account_type: PREMIUM" in markers[2]
        assert "account_type: INVESTMENT" in markers[3]

    def test_withdraw_behaves_differently_per_type(self):
        plain = BankAccount(user_id=1, owner_name="A", balance=Decimal("1000"))
        premium = PremiumAccount(
            user_id=2, owner_name="C", balance=Decimal("1000"), fixed_fee=Decimal("25")
        )

        plain.withdraw(Decimal("100"))
        premium.withdraw(Decimal("100"))

        assert plain.balance == Decimal("900.00")
        assert premium.balance == Decimal("875.00")


class TestWithdrawTemplate:
    """Одно правило снятия: тип счёта меняет только хуки."""

    def test_withdraw_returns_what_left_the_account(self):
        account = PremiumAccount(
            user_id=1,
            owner_name="Anna",
            balance=Decimal("1000"),
            fixed_fee=Decimal("25"),
        )

        assert account.withdraw(Decimal("100")) == Decimal("125.00")
        assert account.balance == Decimal("875.00")

    def test_frozen_premium_reports_status_before_limit(self):
        account = PremiumAccount(
            user_id=1,
            owner_name="Anna",
            balance=Decimal("1000"),
            withdraw_limit=Decimal("10"),
        )
        account.status = AccountStatus.FROZEN

        with pytest.raises(AccountFrozenError):
            account.withdraw(Decimal("500"))

    def test_fee_counts_against_the_balance(self):
        account = PremiumAccount(
            user_id=1, owner_name="Anna", balance=Decimal("100"), fixed_fee=Decimal("1")
        )

        with pytest.raises(InsufficientFundsError):
            account.withdraw(Decimal("100"))

        assert account.balance == Decimal("100.00")
