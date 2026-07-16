from src.models import BankAccount, InvestmentAccount, PremiumAccount, SavingsAccount
from src.enums import AccountStatus
from src.exceptions import AccountFrozenError, InsufficientFundsError, InvalidOperationError


account_active = BankAccount(user_id=1, owner_name='Max')
account_frozen = BankAccount(user_id=2, owner_name='Ivan', status=AccountStatus.FROZEN)
savings_account = SavingsAccount(
    user_id=3,
    owner_name='Anna',
    balance=1000,
    min_balance=300,
    monthly_interest_rate=0.02,
)
premium_account = PremiumAccount(
    user_id=4,
    owner_name='Olga',
    balance=500,
    overdraft_limit=1000,
    withdraw_limit=2000,
    fixed_fee=25,
)
investment_account = InvestmentAccount(
    user_id=5,
    owner_name='Pavel',
    balance=700,
    portfolio={
        "stocks": 1000,
        "bonds": 500,
        "etf": 800,
    },
)



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

savings_account.withdraw(200)
interest = savings_account.apply_monthly_interest()
print(f"Savings interest: {interest}")

try:
    savings_account.withdraw(600)
except InsufficientFundsError:
    print("Savings account min balance protected")

premium_account.withdraw(1200)

try:
    premium_account.withdraw(400)
except (InsufficientFundsError, InvalidOperationError):
    print("Premium account cannot withdraw this amount")

investment_account.withdraw(100)
growth = investment_account.project_yearly_growth()
print(f"Investment projected yearly growth: {growth}")

print(savings_account.get_account_info())
print(premium_account.get_account_info())
print(investment_account.get_account_info())

