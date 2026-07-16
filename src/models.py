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
            raise InvalidOperationError

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
            raise AccountFrozenError
        
        elif self._status is AccountStatus.CLOSED:
            raise AccountClosedError

    def withdraw(self, amount):
        if self._status is AccountStatus.ACTIVE:
            self._validate_amount(amount)

            if self._balance >= amount:
                self._balance -= amount
                print(f'Charged {amount}!')
            else:
                raise InsufficientFundsError

        elif self._status is AccountStatus.FROZEN:
            raise AccountFrozenError
        
        elif self._status is AccountStatus.CLOSED:
            raise AccountClosedError
            
    def get_account_info(self):
        return str(self)
    

class SavingsAccount(AbstractAccount):
    pass

class PremiumAccount(AbstractAccount):
    pass

class InvestmentAccount(AbstractAccount):
    pass
        

