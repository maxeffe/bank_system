from datetime import datetime, timedelta

from decimal import Decimal

from src.logging_setup import configure_logging
from src.enums import AccountStatus, Currency, TransactionPriority, TransactionType
from src.exceptions import (
    AccountFrozenError,
    InsufficientFundsError,
    InvalidOperationError,
)
from src.models import (
    Bank,
    Client,
    Transaction,
    TransactionProcessor,
    TransactionQueue,
)


configure_logging()

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


print("\n--- Day 4: transactions ---")

transaction_bank = Bank("ItGrind Bank")
transaction_bank.add_client(
    Client(
        client_id=1,
        full_name="Max Petrov",
        age=25,
        contacts={"email": "max@example.com"},
        password="max-pass",
    )
)
transaction_bank.add_client(
    Client(
        client_id=2,
        full_name="Anna Ivanova",
        age=31,
        contacts={"phone": "+79990000002"},
        password="anna-pass",
    )
)

payer = transaction_bank.open_account(1, "bank", balance=Decimal("10000"))
payee = transaction_bank.open_account(2, "bank", balance=Decimal("1000"))
savings = transaction_bank.open_account(
    2, "savings", balance=Decimal("2000"), min_balance=Decimal("1000")
)
dollars = transaction_bank.open_account(
    2, "bank", balance=Decimal("100"), currency=Currency.USD
)
frozen = transaction_bank.open_account(2, "bank", balance=Decimal("500"))
transaction_bank.freeze_account(frozen.account_id)

queue = TransactionQueue()
processor = TransactionProcessor(transaction_bank)

cancelled_transaction = Transaction(
    TransactionType.TRANSFER,
    Decimal("300"),
    source_account_id=payer.account_id,
    target_account_id=payee.account_id,
)
scheduled_transaction = Transaction(
    TransactionType.TRANSFER,
    Decimal("400"),
    source_account_id=payer.account_id,
    target_account_id=payee.account_id,
    scheduled_at=datetime.now() + timedelta(days=1),
)

planned_transactions = [
    Transaction(
        TransactionType.TRANSFER,
        Decimal("500"),
        source_account_id=payer.account_id,
        target_account_id=payee.account_id,
        priority=TransactionPriority.HIGH,
    ),
    Transaction(
        TransactionType.DEPOSIT, Decimal("250"), target_account_id=payee.account_id
    ),
    Transaction(
        TransactionType.WITHDRAWAL, Decimal("100"), source_account_id=payer.account_id
    ),
    Transaction(
        TransactionType.EXTERNAL_TRANSFER,
        Decimal("1000"),
        source_account_id=payer.account_id,
    ),
    Transaction(
        TransactionType.TRANSFER,
        Decimal("900"),
        source_account_id=payer.account_id,
        target_account_id=dollars.account_id,
    ),
    Transaction(
        TransactionType.TRANSFER,
        Decimal("200"),
        source_account_id=payer.account_id,
        target_account_id=savings.account_id,
        priority=TransactionPriority.LOW,
    ),
    Transaction(
        TransactionType.TRANSFER,
        Decimal("150"),
        source_account_id=payer.account_id,
        target_account_id=frozen.account_id,
    ),
    Transaction(
        TransactionType.TRANSFER,
        Decimal("999999"),
        source_account_id=payer.account_id,
        target_account_id=payee.account_id,
    ),
    cancelled_transaction,
    scheduled_transaction,
]

for planned_transaction in planned_transactions:
    queue.add(planned_transaction)

print(f"Queued: {len(queue)}, scheduled: {len(queue.scheduled)}")

queue.cancel(cancelled_transaction.transaction_id, "Клиент передумал")
print(f"Cancelled: {cancelled_transaction.status}")

summary = processor.process_queue(queue)
print(f"Summary: {summary}")
print(f"Left in queue: {len(queue)}")

for planned_transaction in planned_transactions:
    print(f"  {planned_transaction}")

print(f"Payer balance: {payer.balance}")
print(f"Payee balance: {payee.balance}")
print(f"Savings balance: {savings.balance}")
print(f"Dollars balance: {dollars.balance}")
print(f"Frozen balance: {frozen.balance}")

for error in processor.errors:
    print(f"  error: {error['transaction_type']} {error['error']} {error['message']}")
