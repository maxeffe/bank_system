from src.models import BankAccount
from src.enums import AccountStatus
from src.exceptions import AccountFrozenError, InsufficientFundsError


account_active = BankAccount(user_id=1, owner_name='Max')
account_frozen = BankAccount(user_id=2, owner_name='Ivan', status=AccountStatus.FROZEN)



account_active.deposit(100)
account_active.withdraw(50)

try:
    account_active.withdraw(60)
except InsufficientFundsError:
    print("Not enough money to withdraw 60")

try:
    account_frozen.deposit(80)
except AccountFrozenError:
    print("Frozen account cannot accept deposits")

