from abc import ABC, abstractmethod
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import uuid

from src.enums import AccountStatus, Currency
from src.exceptions import (
    AccountClosedError,
    AccountFrozenError,
    InsufficientFundsError,
    InvalidOperationError,
)

MONEY_PRECISION = Decimal("0.01")


class AbstractAccount(ABC):
    def __init__(
        self,
        account_id: str = None,
        user_id: str = None,
        owner_name: str = None,
        balance: int | Decimal = Decimal("0"),
        status=AccountStatus.ACTIVE,
        currency=Currency.RUB,
    ):
        if account_id:
            self._account_id = str(account_id)
        else:
            self._account_id = str(uuid.uuid4())[:8]

        self._user_id = user_id
        self.owner_name = owner_name
        self._balance = balance
        self._status = status
        self._currency = currency

    @abstractmethod
    def deposit(self, amount):
        pass

    @abstractmethod
    def withdraw(self, amount):
        pass

    @abstractmethod
    def get_account_info(self):
        pass


class BankAccount(AbstractAccount):
    def __init__(
        self,
        account_id: str = None,
        user_id: str = None,
        owner_name: str = None,
        balance: int | Decimal = Decimal("0"),
        status=AccountStatus.ACTIVE,
        currency=Currency.RUB,
    ):
        if user_id is None:
            raise InvalidOperationError("User id is required")

        balance = self._to_money(balance, "Balance cannot be negative")

        if balance < 0:
            raise InvalidOperationError("Balance cannot be negative")

        if not isinstance(status, AccountStatus):
            raise InvalidOperationError("Invalid account status")

        if not isinstance(currency, Currency):
            raise InvalidOperationError("Invalid currency")

        super().__init__(account_id, user_id, owner_name, balance, status, currency)

    @staticmethod
    def _is_valid_money(value):
        if isinstance(value, bool):
            return False

        if isinstance(value, int):
            return True

        if isinstance(value, Decimal):
            return value.is_finite()

        return False

    @classmethod
    def _to_money(cls, value, error_message):
        if not cls._is_valid_money(value):
            raise InvalidOperationError(error_message)

        try:
            return Decimal(value).quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)
        except InvalidOperation:
            raise InvalidOperationError(error_message) from None

    @classmethod
    def _to_rate(cls, value, error_message):
        if not cls._is_valid_money(value):
            raise InvalidOperationError(error_message)

        return Decimal(value)

    @property
    def account_id(self):
        return self._account_id

    @property
    def user_id(self):
        return self._user_id

    @property
    def owner_name(self):
        return self._owner_name

    @owner_name.setter
    def owner_name(self, new_owner_name):
        if not isinstance(new_owner_name, str) or not new_owner_name.strip():
            raise InvalidOperationError("Owner name is required")
        self._owner_name = new_owner_name

    @property
    def balance(self):
        return self._balance

    @property
    def currency(self):
        return self._currency

    @property
    def status(self):
        return self._status

    @status.setter
    def status(self, new_status):
        if not isinstance(new_status, AccountStatus):
            raise InvalidOperationError("Invalid account status")

        self._status = new_status

    def _validate_amount(self, amount):
        amount = self._to_money(amount, "Amount must be a positive number")

        if amount <= 0:
            raise InvalidOperationError("Amount must be a positive number")

        return amount

    def _ensure_operational(self, action):
        if self._status is AccountStatus.ACTIVE:
            return

        if self._status is AccountStatus.FROZEN:
            raise AccountFrozenError(f"Frozen account cannot {action}")

        if self._status is AccountStatus.CLOSED:
            raise AccountClosedError(f"Closed account cannot {action}")

        raise InvalidOperationError(f"Account status does not allow to {action}")

    def __str__(self):
        return f"""
            account_type: BANK\n
            owner_name: {self._owner_name}\n
            account_id: {self._account_id[-4::]}\n  
            user_id: {self._user_id}\n
            balance: {self._balance} {self._currency}\n
            status: {self._status}\n
            """

    def deposit(self, amount):
        self._ensure_operational("accept deposits")
        amount = self._validate_amount(amount)
        self._balance += amount
        print(f"Account topped up {amount}!")

    def withdraw(self, amount):
        self._ensure_operational("withdraw money")
        amount = self._validate_amount(amount)

        if self._balance < amount:
            raise InsufficientFundsError("Not enough money to withdraw")

        self._balance -= amount
        print(f"Charged {amount}!")

    def get_account_info(self):
        return str(self)


class SavingsAccount(BankAccount):
    def __init__(
        self,
        account_id: str = None,
        user_id: str = None,
        owner_name: str = None,
        balance: int | Decimal = Decimal("0"),
        status=AccountStatus.ACTIVE,
        currency=Currency.RUB,
        min_balance: int | Decimal = Decimal("0"),
        monthly_interest_rate: int | Decimal = Decimal("0.01"),
    ):
        super().__init__(account_id, user_id, owner_name, balance, status, currency)

        min_balance = self._to_money(min_balance, "Min balance cannot be negative")

        if min_balance < 0:
            raise InvalidOperationError("Min balance cannot be negative")

        monthly_interest_rate = self._to_rate(
            monthly_interest_rate, "Monthly interest rate cannot be negative"
        )

        if monthly_interest_rate < 0:
            raise InvalidOperationError("Monthly interest rate cannot be negative")

        if self._balance < min_balance:
            raise InvalidOperationError("Balance cannot be less than min balance")

        self._min_balance = min_balance
        self._monthly_interest_rate = monthly_interest_rate

    def withdraw(self, amount):
        self._ensure_operational("withdraw money")
        amount = self._validate_amount(amount)

        if self._balance - amount < self._min_balance:
            raise InsufficientFundsError("Withdrawal would break minimum balance")

        super().withdraw(amount)

    def apply_monthly_interest(self):
        self._ensure_operational("receive interest")
        interest = (self._balance * self._monthly_interest_rate).quantize(
            MONEY_PRECISION, rounding=ROUND_HALF_UP
        )
        self._balance += interest
        return interest

    def __str__(self):
        return f"""
            account_type: SAVINGS\n
            owner_name: {self._owner_name}\n
            account_id: {self._account_id[-4::]}\n
            user_id: {self._user_id}\n
            balance: {self._balance} {self._currency}\n
            status: {self._status}\n
            min_balance: {self._min_balance}\n
            monthly_interest_rate: {self._monthly_interest_rate}\n
            """


class PremiumAccount(BankAccount):
    def __init__(
        self,
        account_id: str = None,
        user_id: str = None,
        owner_name: str = None,
        balance: int | Decimal = Decimal("0"),
        status=AccountStatus.ACTIVE,
        currency=Currency.RUB,
        overdraft_limit: int | Decimal = Decimal("0"),
        withdraw_limit: int | Decimal = Decimal("100000"),
        fixed_fee: int | Decimal = Decimal("0"),
    ):
        super().__init__(account_id, user_id, owner_name, balance, status, currency)

        overdraft_limit = self._to_money(
            overdraft_limit, "Overdraft limit cannot be negative"
        )

        if overdraft_limit < 0:
            raise InvalidOperationError("Overdraft limit cannot be negative")

        withdraw_limit = self._to_money(
            withdraw_limit, "Withdraw limit must be positive"
        )

        if withdraw_limit <= 0:
            raise InvalidOperationError("Withdraw limit must be positive")

        fixed_fee = self._to_money(fixed_fee, "Fixed fee cannot be negative")

        if fixed_fee < 0:
            raise InvalidOperationError("Fixed fee cannot be negative")

        self._overdraft_limit = overdraft_limit
        self._withdraw_limit = withdraw_limit
        self._fixed_fee = fixed_fee

    def withdraw(self, amount):
        self._ensure_operational("withdraw money")
        amount = self._validate_amount(amount)

        if amount > self._withdraw_limit:
            raise InvalidOperationError("Withdraw limit exceeded")

        total_amount = amount + self._fixed_fee

        if self._balance - total_amount < -self._overdraft_limit:
            raise InsufficientFundsError("Overdraft limit exceeded")

        self._balance -= total_amount
        print(f"Charged {amount}! Fee: {self._fixed_fee}")

    def __str__(self):
        return f"""
            account_type: PREMIUM\n
            owner_name: {self._owner_name}\n
            account_id: {self._account_id[-4::]}\n
            user_id: {self._user_id}\n
            balance: {self._balance} {self._currency}\n
            status: {self._status}\n
            overdraft_limit: {self._overdraft_limit}\n
            withdraw_limit: {self._withdraw_limit}\n
            fixed_fee: {self._fixed_fee}\n
            """


class InvestmentAccount(BankAccount):
    def __init__(
        self,
        account_id: str = None,
        user_id: str = None,
        owner_name: str = None,
        balance: int | Decimal = Decimal("0"),
        status=AccountStatus.ACTIVE,
        currency=Currency.RUB,
        portfolio: dict = None,
    ):
        super().__init__(account_id, user_id, owner_name, balance, status, currency)

        self._portfolio = self._validate_portfolio(portfolio or {})

    def _validate_portfolio(self, portfolio):
        if not isinstance(portfolio, dict):
            raise InvalidOperationError("Portfolio must be a dictionary")

        allowed_assets = ("stocks", "bonds", "etf")
        validated_portfolio = {}

        for asset, value in portfolio.items():
            if asset not in allowed_assets:
                raise InvalidOperationError("Unknown portfolio asset")

            value = self._to_money(value, "Portfolio asset value cannot be negative")

            if value < 0:
                raise InvalidOperationError("Portfolio asset value cannot be negative")

            validated_portfolio[asset] = value

        for asset in allowed_assets:
            validated_portfolio.setdefault(asset, Decimal("0.00"))

        return validated_portfolio

    def project_yearly_growth(self):
        growth_rates = {
            "stocks": Decimal("0.12"),
            "bonds": Decimal("0.05"),
            "etf": Decimal("0.08"),
        }

        projected_growth = Decimal("0")

        for asset, value in self._portfolio.items():
            projected_growth += value * growth_rates[asset]

        return projected_growth.quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)

    def __str__(self):
        return f"""
            account_type: INVESTMENT\n
            owner_name: {self._owner_name}\n
            account_id: {self._account_id[-4::]}\n
            user_id: {self._user_id}\n
            balance: {self._balance} {self._currency}\n
            status: {self._status}\n
            portfolio: {self._portfolio}\n
            projected_yearly_growth: {self.project_yearly_growth()}\n
            """
