from datetime import datetime

from decimal import Decimal

from src.enums import AccountStatus
from src.exceptions import (
    AccountFrozenError,
    InsufficientFundsError,
    InvalidOperationError,
)
from src.models import Bank, Client


bank = Bank("ItGrind Bank")

client_max = Client(
    client_id=1,
    full_name="Max Petrov",
    age=25,
    contacts={"phone": "+79990000001", "email": "max@example.com"},
    password="max-pass",
)
client_anna = Client(
    client_id=2,
    full_name="Anna Ivanova",
    age=31,
    contacts={"phone": "+79990000002", "email": "anna@example.com"},
    password="anna-pass",
)
client_olga = Client(
    client_id=3,
    full_name="Olga Sidorova",
    age=42,
    contacts={"phone": "+79990000003"},
    password="olga-pass",
)

bank.add_client(client_max)
bank.add_client(client_anna)
bank.add_client(client_olga)

max_base_account = bank.open_account(
    client_id=1, account_type="bank", balance=Decimal("1000")
)
max_savings_account = bank.open_account(
    client_id=1,
    account_type="savings",
    balance=Decimal("5000"),
    min_balance=Decimal("1000"),
    monthly_interest_rate=Decimal("0.02"),
)
anna_premium_account = bank.open_account(
    client_id=2,
    account_type="premium",
    balance=Decimal("1200"),
    overdraft_limit=Decimal("1000"),
    withdraw_limit=Decimal("3000"),
    fixed_fee=Decimal("25"),
)
olga_investment_account = bank.open_account(
    client_id=3,
    account_type="investment",
    balance=Decimal("700"),
    portfolio={
        "stocks": Decimal("1000"),
        "bonds": Decimal("500"),
        "etf": Decimal("800"),
    },
)
temporary_account = bank.open_account(
    client_id=2, account_type="bank", balance=Decimal("300")
)
temporary_account.withdraw(temporary_account.balance)
bank.close_account(temporary_account.account_id)

try:
    bank.close_account(max_base_account.account_id)
except InvalidOperationError as error:
    print(error)

max_base_account.deposit(Decimal("500"))
max_savings_account.withdraw(Decimal("1000"))
interest = max_savings_account.apply_monthly_interest()
print(f"Savings interest: {interest}")

anna_premium_account.withdraw(Decimal("2000"))
growth = olga_investment_account.project_yearly_growth()
print(f"Investment projected yearly growth: {growth}")

try:
    max_savings_account.withdraw(Decimal("3500"))
except InsufficientFundsError as exc:
    print(exc)

bank.freeze_account(max_base_account.account_id)

try:
    max_base_account.deposit(Decimal("100"))
except AccountFrozenError as exc:
    print(exc)

bank.unfreeze_account(max_base_account.account_id)
max_base_account.deposit(Decimal("100"))

print(f"Anna auth failed: {bank.authenticate_client(2, 'bad-password')}")
print(f"Anna auth success: {bank.authenticate_client(2, 'anna-pass')}")

for _ in range(3):
    try:
        result = bank.authenticate_client(3, "bad-password")
        print(f"Olga auth failed: {result}")
    except InvalidOperationError as exc:
        print(exc)

try:
    bank.open_account(client_id=3, account_type="bank", balance=Decimal("100"))
except InvalidOperationError as exc:
    print(exc)

active_accounts = bank.search_accounts(status=AccountStatus.ACTIVE)
closed_accounts = bank.search_accounts(status=AccountStatus.CLOSED)
savings_accounts = bank.search_accounts(account_type="savings")
print(f"Active accounts: {len(active_accounts)}")
print(f"Closed accounts: {len(closed_accounts)}")
print(f"Savings accounts: {len(savings_accounts)}")
print(f"Total balance: {bank.get_total_balance()}")

for client, total_balance in bank.get_clients_ranking():
    print(f"{client.full_name}: {total_balance}")

night_bank = Bank(
    "Night Bank",
    time_provider=lambda: datetime(2026, 7, 16, 2, 0, 0),
)

try:
    night_bank.add_client(
        Client(
            client_id=10,
            full_name="Night Client",
            age=22,
            contacts={"phone": "+79990000010"},
            password="night-pass",
        )
    )
except InvalidOperationError as exc:
    print(exc)

print(f"Suspicious actions: {bank.suspicious_actions}")
print(f"Night suspicious actions: {night_bank.suspicious_actions}")
