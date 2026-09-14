import uuid
from abc import ABC, abstractmethod
from decimal import ROUND_HALF_UP, Decimal

from loguru import logger

from src.enums import AccountStatus, Currency
from src.money import (
    MAX_MONEY,
    MONEY_LIMIT_MESSAGE,
    MONEY_PRECISION,
    to_non_negative_money,
    to_non_negative_rate,
    to_positive_money,
)
from src.exceptions import (
    AccountClosedError,
    AccountFrozenError,
    InsufficientFundsError,
    InvalidOperationError,
)


# Годовая доходность активов инвестиционного счёта; ключи - допустимые активы.
PORTFOLIO_GROWTH_RATES = {
    "stocks": Decimal("0.12"),
    "bonds": Decimal("0.05"),
    "etf": Decimal("0.08"),
}


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
    INSUFFICIENT_FUNDS_MESSAGE = "Not enough money to withdraw"

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

        balance = to_non_negative_money(balance, "Balance cannot be negative")

        if not isinstance(currency, Currency):
            raise InvalidOperationError("Invalid currency")

        super().__init__(
            account_id, user_id, owner_name, balance, AccountStatus.ACTIVE, currency
        )
        # Через сеттер: правила статуса одни и те же при создании и потом.
        self.status = status

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

        if (
            self._status is AccountStatus.CLOSED
            and new_status is not AccountStatus.CLOSED
        ):
            raise InvalidOperationError("Closed account cannot change status")

        if new_status is AccountStatus.CLOSED and self._balance != 0:
            raise InvalidOperationError(
                "Account with non-zero balance cannot be closed"
            )

        self._status = new_status

    def _validate_amount(self, amount):
        return to_positive_money(amount, "Amount must be a positive number")

    def _check_withdraw_limit(self, amount):
        """Лимит одного снятия. У обычного счёта его нет."""

    def withdrawal_fee(self, amount) -> Decimal:
        """Своя комиссия счёта за снятие. Базовый счёт не берёт ничего."""
        return Decimal("0.00")

    @property
    def min_allowed_balance(self) -> Decimal:
        """Ниже какого баланса счёт опускаться не имеет права."""
        return Decimal("0.00")

    def ensure_operational(self, action):
        if self._status is AccountStatus.ACTIVE:
            return

        if self._status is AccountStatus.FROZEN:
            raise AccountFrozenError(f"Frozen account cannot {action}")

        if self._status is AccountStatus.CLOSED:
            raise AccountClosedError(f"Closed account cannot {action}")

        raise InvalidOperationError(f"Account status does not allow to {action}")

    def _format_info(self, account_type: str, **extra) -> str:
        """Общая часть карточки счёта плюс поля конкретного типа."""
        lines = [
            f"account_type: {account_type}",
            f"owner_name: {self._owner_name}",
            f"account_id: {self._account_id[-4:]}",
            f"user_id: {self._user_id}",
            f"balance: {self._balance} {self._currency}",
            f"status: {self._status}",
        ]
        lines.extend(f"{name}: {value}" for name, value in extra.items())
        return "\n".join(lines)

    def __str__(self):
        return self._format_info("BANK")

    def deposit(self, amount):
        self.ensure_operational("accept deposits")
        amount = self._validate_amount(amount)
        self._change_balance(amount)
        self._log_balance_change("deposit", amount)

    def withdraw(self, amount):
        """Одно правило снятия для всех типов счёта.

        Тип счёта меняет только хуки: лимит снятия, комиссию и нижнюю границу
        баланса. Возвращает, сколько реально ушло со счёта вместе с комиссией.
        """
        self.ensure_operational("withdraw money")
        amount = self._validate_amount(amount)
        self._check_withdraw_limit(amount)
        fee = self.withdrawal_fee(amount)

        if self._balance - amount - fee < self.min_allowed_balance:
            raise InsufficientFundsError(self.INSUFFICIENT_FUNDS_MESSAGE)

        self._change_balance(-(amount + fee))
        self._log_balance_change("withdrawal", amount, fee=fee)
        return amount + fee

    def refund(self, amount):
        """Возврат ранее списанного.

        Статус счёта не проверяется намеренно: это не пополнение от клиента,
        а отмена собственного списания банка. Если операция не удалась,
        деньги обязаны вернуться даже на замороженный счёт.
        """
        amount = self._validate_amount(amount)
        self._change_balance(amount)
        self._log_balance_change("refund", amount)

    def _change_balance(self, delta):
        """Единственное место, где меняется баланс: он не выходит за MAX_MONEY."""
        new_balance = self._balance + delta

        if abs(new_balance) > MAX_MONEY:
            raise InvalidOperationError(MONEY_LIMIT_MESSAGE)

        self._balance = new_balance

    def _log_balance_change(self, event, amount, **extra):
        logger.info(
            event,
            account_id=self._account_id,
            amount=amount,
            currency=self._currency,
            balance=self._balance,
            **extra,
        )

    def get_account_info(self):
        return str(self)


class SavingsAccount(BankAccount):
    INSUFFICIENT_FUNDS_MESSAGE = "Withdrawal would break minimum balance"

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

        min_balance = to_non_negative_money(
            min_balance, "Min balance cannot be negative"
        )
        monthly_interest_rate = to_non_negative_rate(
            monthly_interest_rate, "Monthly interest rate cannot be negative"
        )

        if self._balance < min_balance:
            raise InvalidOperationError("Balance cannot be less than min balance")

        self._min_balance = min_balance
        self._monthly_interest_rate = monthly_interest_rate

    def withdraw(self, amount):
        """Общее правило; нижняя граница - неснижаемый остаток."""
        return super().withdraw(amount)

    @property
    def min_allowed_balance(self) -> Decimal:
        return self._min_balance

    def apply_monthly_interest(self):
        self.ensure_operational("receive interest")
        interest = (self._balance * self._monthly_interest_rate).quantize(
            MONEY_PRECISION, rounding=ROUND_HALF_UP
        )
        self._change_balance(interest)
        self._log_balance_change("interest_applied", interest)
        return interest

    def get_account_info(self):
        return str(self)

    def __str__(self):
        return self._format_info(
            "SAVINGS",
            min_balance=self._min_balance,
            monthly_interest_rate=self._monthly_interest_rate,
        )


class PremiumAccount(BankAccount):
    INSUFFICIENT_FUNDS_MESSAGE = "Overdraft limit exceeded"

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

        overdraft_limit = to_non_negative_money(
            overdraft_limit, "Overdraft limit cannot be negative"
        )
        withdraw_limit = to_positive_money(
            withdraw_limit, "Withdraw limit must be positive"
        )
        fixed_fee = to_non_negative_money(fixed_fee, "Fixed fee cannot be negative")

        self._overdraft_limit = overdraft_limit
        self._withdraw_limit = withdraw_limit
        self._fixed_fee = fixed_fee

    def withdraw(self, amount):
        """Общее правило; лимит снятия, фиксированная комиссия и овердрафт."""
        return super().withdraw(amount)

    def _check_withdraw_limit(self, amount):
        if amount > self._withdraw_limit:
            raise InvalidOperationError("Withdraw limit exceeded")

    def withdrawal_fee(self, amount) -> Decimal:
        return self._fixed_fee

    @property
    def min_allowed_balance(self) -> Decimal:
        return -self._overdraft_limit

    def get_account_info(self):
        return str(self)

    def __str__(self):
        return self._format_info(
            "PREMIUM",
            overdraft_limit=self._overdraft_limit,
            withdraw_limit=self._withdraw_limit,
            fixed_fee=self._fixed_fee,
        )


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

        validated_portfolio = {}

        for asset, value in portfolio.items():
            if asset not in PORTFOLIO_GROWTH_RATES:
                raise InvalidOperationError("Unknown portfolio asset")

            validated_portfolio[asset] = to_non_negative_money(
                value, "Portfolio asset value cannot be negative"
            )

        for asset in PORTFOLIO_GROWTH_RATES:
            validated_portfolio.setdefault(asset, Decimal("0.00"))

        return validated_portfolio

    def project_yearly_growth(self):
        projected_growth = Decimal("0")

        for asset, value in self._portfolio.items():
            projected_growth += value * PORTFOLIO_GROWTH_RATES[asset]

        return projected_growth.quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)

    def withdraw(self, amount):
        """Общее правило без особенностей: портфель на снятие не влияет."""
        return super().withdraw(amount)

    def get_account_info(self):
        return str(self)

    def __str__(self):
        return self._format_info(
            "INVESTMENT",
            portfolio=self._portfolio,
            projected_yearly_growth=self.project_yearly_growth(),
        )
