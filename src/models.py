from src.enums import AccountStatus, Currency
from abc import ABC, abstractmethod
from src.exceptions import AccountClosedError, AccountFrozenError, InvalidOperationError, InsufficientFundsError
import uuid



class AbstractAccount(ABC):

    def __init__(self, account_id:str=None, user_id:str=None, owner_name:str=None, balance:int | float=0, status=AccountStatus.ACTIVE, currency=Currency.RUB):
        if account_id:
            self._account_id = str(account_id)
        else:
            self._account_id = str(uuid.uuid4())[:8]

        self._user_id = user_id
        self._owner_name = owner_name
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

    def __init__(self, account_id:str=None, user_id:str=None, owner_name:str=None, balance:int | float=0, status=AccountStatus.ACTIVE, currency=Currency.RUB):
        if user_id is None:
            raise InvalidOperationError("User id is required")

        if not isinstance(owner_name, str) or not owner_name.strip():
            raise InvalidOperationError("Owner name is required")

        if not self._is_valid_money(balance) or balance < 0:
            raise InvalidOperationError("Balance cannot be negative")

        if not isinstance(status, AccountStatus):
            raise InvalidOperationError("Invalid account status")

        if not isinstance(currency, Currency):
            raise InvalidOperationError("Invalid currency")

        super().__init__(account_id, user_id, owner_name, balance, status, currency)

    @staticmethod
    def _is_valid_money(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool)

    def _validate_amount(self, amount):
        if not self._is_valid_money(amount) or amount <= 0:
            raise InvalidOperationError("Amount must be a positive number")

    def __str__(self):
        return f'''
            account_type: BANK\n
            owner_name: {self._owner_name}\n
            account_id: {self._account_id[-4::]}\n  
            user_id: {self._user_id}\n
            balance: {self._balance} {self._currency}\n
            status: {self._status}\n
            '''

    def deposit(self, amount):
        if self._status is AccountStatus.ACTIVE:
            self._validate_amount(amount)
            self._balance += amount
            print(f'Account topped up {amount}!')

        elif self._status is AccountStatus.FROZEN:
            raise AccountFrozenError("Frozen account cannot accept deposits")
        
        elif self._status is AccountStatus.CLOSED:
            raise AccountClosedError("Closed account cannot accept deposits")

    def withdraw(self, amount):
        if self._status is AccountStatus.ACTIVE:
            self._validate_amount(amount)

            if self._balance >= amount:
                self._balance -= amount
                print(f'Charged {amount}!')
            else:
                raise InsufficientFundsError("Not enough money to withdraw")

        elif self._status is AccountStatus.FROZEN:
            raise AccountFrozenError("Frozen account cannot withdraw money")
        
        elif self._status is AccountStatus.CLOSED:
            raise AccountClosedError("Closed account cannot withdraw money")
            
    def get_account_info(self):
        return str(self)
    

class SavingsAccount(BankAccount):

    def __init__(
        self,
        account_id: str = None,
        user_id: str = None,
        owner_name: str = None,
        balance: int | float = 0,
        status=AccountStatus.ACTIVE,
        currency=Currency.RUB,
        min_balance: int | float = 0,
        monthly_interest_rate: int | float = 0.01,
    ):
        super().__init__(account_id, user_id, owner_name, balance, status, currency)

        if not self._is_valid_money(min_balance) or min_balance < 0:
            raise InvalidOperationError("Min balance cannot be negative")

        if not self._is_valid_money(monthly_interest_rate) or monthly_interest_rate < 0:
            raise InvalidOperationError("Monthly interest rate cannot be negative")

        if balance < min_balance:
            raise InvalidOperationError("Balance cannot be less than min balance")

        self._min_balance = min_balance
        self._monthly_interest_rate = monthly_interest_rate

    def withdraw(self, amount):
        self._validate_amount(amount)

        if self._balance - amount < self._min_balance:
            raise InsufficientFundsError("Withdrawal would break minimum balance")

        super().withdraw(amount)

    def apply_monthly_interest(self):
        if self._status is AccountStatus.ACTIVE:
            interest = self._balance * self._monthly_interest_rate
            self._balance += interest
            return interest

        elif self._status is AccountStatus.FROZEN:
            raise AccountFrozenError("Frozen account cannot receive interest")

        elif self._status is AccountStatus.CLOSED:
            raise AccountClosedError("Closed account cannot receive interest")

    def get_account_info(self):
        return str(self)

    def __str__(self):
        return f'''
            account_type: SAVINGS\n
            owner_name: {self._owner_name}\n
            account_id: {self._account_id[-4::]}\n
            user_id: {self._user_id}\n
            balance: {self._balance} {self._currency}\n
            status: {self._status}\n
            min_balance: {self._min_balance}\n
            monthly_interest_rate: {self._monthly_interest_rate}\n
            '''


class PremiumAccount(BankAccount):

    def __init__(
        self,
        account_id: str = None,
        user_id: str = None,
        owner_name: str = None,
        balance: int | float = 0,
        status=AccountStatus.ACTIVE,
        currency=Currency.RUB,
        overdraft_limit: int | float = 0,
        withdraw_limit: int | float = 100000,
        fixed_fee: int | float = 0,
    ):
        super().__init__(account_id, user_id, owner_name, balance, status, currency)

        if not self._is_valid_money(overdraft_limit) or overdraft_limit < 0:
            raise InvalidOperationError("Overdraft limit cannot be negative")

        if not self._is_valid_money(withdraw_limit) or withdraw_limit <= 0:
            raise InvalidOperationError("Withdraw limit must be positive")

        if not self._is_valid_money(fixed_fee) or fixed_fee < 0:
            raise InvalidOperationError("Fixed fee cannot be negative")

        self._overdraft_limit = overdraft_limit
        self._withdraw_limit = withdraw_limit
        self._fixed_fee = fixed_fee

    def withdraw(self, amount):
        if self._status is AccountStatus.ACTIVE:
            self._validate_amount(amount)

            if amount > self._withdraw_limit:
                raise InvalidOperationError("Withdraw limit exceeded")

            total_amount = amount + self._fixed_fee

            if self._balance - total_amount >= -self._overdraft_limit:
                self._balance -= total_amount
                print(f'Charged {amount}! Fee: {self._fixed_fee}')
            else:
                raise InsufficientFundsError("Overdraft limit exceeded")

        elif self._status is AccountStatus.FROZEN:
            raise AccountFrozenError("Frozen account cannot withdraw money")

        elif self._status is AccountStatus.CLOSED:
            raise AccountClosedError("Closed account cannot withdraw money")

    def get_account_info(self):
        return str(self)

    def __str__(self):
        return f'''
            account_type: PREMIUM\n
            owner_name: {self._owner_name}\n
            account_id: {self._account_id[-4::]}\n
            user_id: {self._user_id}\n
            balance: {self._balance} {self._currency}\n
            status: {self._status}\n
            overdraft_limit: {self._overdraft_limit}\n
            withdraw_limit: {self._withdraw_limit}\n
            fixed_fee: {self._fixed_fee}\n
            '''


class InvestmentAccount(BankAccount):

    def __init__(
        self,
        account_id: str = None,
        user_id: str = None,
        owner_name: str = None,
        balance: int | float = 0,
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

            if not self._is_valid_money(value) or value < 0:
                raise InvalidOperationError("Portfolio asset value cannot be negative")

            validated_portfolio[asset] = value

        for asset in allowed_assets:
            validated_portfolio.setdefault(asset, 0)

        return validated_portfolio

    def withdraw(self, amount):
        super().withdraw(amount)

    def project_yearly_growth(self):
        growth_rates = {
            "stocks": 0.12,
            "bonds": 0.05,
            "etf": 0.08,
        }

        projected_growth = 0

        for asset, value in self._portfolio.items():
            projected_growth += value * growth_rates[asset]

        return projected_growth

    def get_account_info(self):
        return str(self)

    def __str__(self):
        return f'''
            account_type: INVESTMENT\n
            owner_name: {self._owner_name}\n
            account_id: {self._account_id[-4::]}\n
            user_id: {self._user_id}\n
            balance: {self._balance} {self._currency}\n
            status: {self._status}\n
            portfolio: {self._portfolio}\n
            projected_yearly_growth: {self.project_yearly_growth()}\n
            '''
