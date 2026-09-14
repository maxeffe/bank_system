"""Демонстрация банковской системы целиком (дни 6 и 7).

Инициализация -> симуляция очереди транзакций -> сценарии клиента -> отчёты
-> экспорт отчётов (текст, JSON, CSV) и графиков.
Часы демо двигаются вручную, поэтому итоги одинаковые при каждом запуске;
меняются только случайные идентификаторы транзакций и счетов.
"""

import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from loguru import logger

from src.enums import ClientStatus, Currency, TransactionPriority, TransactionType
from src.exceptions import InvalidOperationError, TransientTransactionError
from src.logging_setup import configure_logging
from src.models import Bank, Client, Transaction, TransactionProcessor, TransactionQueue
from src.models.client import MAX_FAILED_LOGINS
from src.reporting import ReportBuilder
from src.services import AuditLog
from src.services.risk import DEFAULT_MAX_OPERATIONS

LOG_DIR = Path("logs")
REPORT_DIR = Path("reports")
SPOTLIGHT_CLIENT_ID = 1
BLOCKED_CLIENT_ID = 8
TOP_CLIENTS = 3

DAY_START = datetime(2026, 9, 7, 12, 0)
ERRORS_TIME = DAY_START + timedelta(minutes=10)
SUSPICIOUS_TIME = DAY_START + timedelta(minutes=30)
NIGHT_TIME = datetime(2026, 9, 8, 2, 30)
MORNING_TIME = datetime(2026, 9, 8, 9, 0)

LIFECYCLE_EVENTS = (
    "transaction_queued",
    "transaction_cancelled",
    "transaction_completed",
    "transaction_rejected",
)

# client_id, ФИО, возраст, телефон
CLIENTS = (
    (1, "Max Petrov", 25, "+79990000001"),
    (2, "Anna Ivanova", 31, "+79990000002"),
    (3, "Olga Sidorova", 42, "+79990000003"),
    (4, "Ivan Smirnov", 37, "+79990000004"),
    (5, "Elena Kuznetsova", 29, "+79990000005"),
    (6, "Dmitry Volkov", 51, "+79990000006"),
    (7, "Sofia Morozova", 23, "+79990000007"),
    (8, "Pavel Lebedev", 45, "+79990000008"),
)

# метка, client_id, тип счёта, параметры счёта
ACCOUNTS = (
    ("max_rub", 1, "bank", {"balance": Decimal("250000")}),
    ("max_usd", 1, "bank", {"balance": Decimal("3000"), "currency": Currency.USD}),
    (
        "anna_premium",
        2,
        "premium",
        {
            "balance": Decimal("20000"),
            "overdraft_limit": Decimal("5000"),
            "withdraw_limit": Decimal("25000"),
            "fixed_fee": Decimal("25"),
        },
    ),
    (
        "anna_savings",
        2,
        "savings",
        {"balance": Decimal("100000"), "min_balance": Decimal("50000")},
    ),
    ("olga_rub", 3, "bank", {"balance": Decimal("40000")}),
    (
        "olga_invest",
        3,
        "investment",
        {"balance": Decimal("15000"), "portfolio": {"stocks": Decimal("30000")}},
    ),
    ("ivan_rub", 4, "bank", {"balance": Decimal("60000")}),
    ("ivan_eur", 4, "bank", {"balance": Decimal("2500"), "currency": Currency.EUR}),
    ("elena_rub", 5, "bank", {"balance": Decimal("900000")}),
    ("dmitry_rub", 6, "bank", {"balance": Decimal("5000")}),
    ("sofia_rub", 7, "bank", {"balance": Decimal("1500")}),
    ("pavel_rub", 8, "bank", {"balance": Decimal("70000")}),
)


class DemoClock:
    """Часы демо: время двигается только вручную."""

    def __init__(self, moment):
        self.moment = moment

    def __call__(self):
        return self.moment


@dataclass
class Demo:
    bank: Bank
    clock: DemoClock
    queue: TransactionQueue
    processor: TransactionProcessor
    accounts: dict
    gateway_online: bool = True
    transactions: list = field(default_factory=list)
    refused_at_intake: int = 0
    report_files: list = field(default_factory=list)


def print_section(title):
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def print_lifecycle_event(message):
    """Консольный вывод событий очереди: коротко, суммы строкой."""
    record = message.record
    extra = record["extra"]
    details = ", ".join(
        f"{key}={value}" for key, value in extra.items() if key != "transaction_id"
    )
    print(
        f"  {record['level'].name:<7} {record['message']:<22} "
        f"{str(extra.get('transaction_id', ''))[:8]}  {details}"
    )


def build_demo(log_dir):
    clock = DemoClock(DAY_START)
    bank = Bank(
        "ItGrind Bank",
        time_provider=clock,
        audit_log=AuditLog(time_provider=clock, file_path=log_dir / "audit.jsonl"),
    )
    demo = Demo(
        bank=bank,
        clock=clock,
        queue=TransactionQueue(time_provider=clock),
        processor=None,
        accounts={},
    )
    demo.processor = TransactionProcessor(
        bank,
        external_gateway=lambda tx: external_gateway(demo, tx),
        time_provider=clock,
    )

    for client_id, full_name, age, phone in CLIENTS:
        bank.add_client(
            Client(
                client_id=client_id,
                full_name=full_name,
                age=age,
                contacts={"phone": phone},
                password=f"pass-{client_id}",
            )
        )

    for label, client_id, account_type, params in ACCOUNTS:
        demo.accounts[label] = bank.open_account(client_id, account_type, **params)

    return demo


def external_gateway(demo, transaction):
    if not demo.gateway_online:
        raise TransientTransactionError("External gateway is offline")


def show_initialization(demo):
    print_section("1. ИНИЦИАЛИЗАЦИЯ")
    print(f"Банк: {demo.bank.name}")
    print(f"\nКлиенты ({len(demo.bank.clients)}):")

    for client in demo.bank.clients.values():
        print(f"  #{client.client_id:<2} {client.full_name:<18} {client.age} лет")

    print(f"\nСчета ({len(demo.accounts)}):")

    for label, account in demo.accounts.items():
        owner = demo.bank.clients[account.user_id].full_name
        print(
            f"  {label:<13} {type(account).__name__:<18} {owner:<18} "
            f"{account.balance:>12} {account.currency}"
        )


def make(demo, kind, amount, source=None, target=None, **options):
    """Транзакция по меткам счетов. Неверные данные отсекаются ещё до очереди."""
    accounts = demo.accounts
    try:
        transaction = Transaction(
            kind,
            amount,
            source_account_id=accounts[source].account_id
            if source in accounts
            else source,
            target_account_id=accounts[target].account_id
            if target in accounts
            else target,
            time_provider=demo.clock,
            **options,
        )
    except InvalidOperationError as error:
        demo.refused_at_intake += 1
        print(f"  ОТКАЗ   не принята в очередь: {kind} {amount} - {error}")
        return None

    demo.transactions.append(transaction)
    demo.queue.add(transaction)
    return transaction


def run_batch(demo, title, moment, build):
    demo.clock.moment = moment
    print(f"\n--- {title} [{moment:%Y-%m-%d %H:%M}] ---")
    build(demo)
    summary = demo.processor.process_queue(demo.queue, now=moment)
    print(f"  Итог пакета: {summary}, в очереди осталось: {len(demo.queue)}")


def normal_operations(demo):
    deposit, transfer = TransactionType.DEPOSIT, TransactionType.TRANSFER
    make(demo, deposit, Decimal("5000"), target="olga_rub")
    make(demo, deposit, Decimal("12000"), target="ivan_rub")
    make(
        demo,
        transfer,
        Decimal("1500"),
        "max_rub",
        "anna_premium",
        priority=TransactionPriority.HIGH,
    )
    make(demo, transfer, Decimal("3000"), "ivan_rub", "olga_rub")
    make(demo, TransactionType.WITHDRAWAL, Decimal("2000"), "anna_premium")
    make(demo, TransactionType.WITHDRAWAL, Decimal("1000"), "pavel_rub")
    make(demo, TransactionType.EXTERNAL_TRANSFER, Decimal("4000"), "olga_rub")
    make(demo, transfer, Decimal("100"), "max_usd", "ivan_eur", currency=Currency.USD)
    make(demo, transfer, Decimal("50"), "ivan_eur", "max_usd", currency=Currency.EUR)
    make(demo, deposit, Decimal("700"), target="sofia_rub")
    make(demo, transfer, Decimal("10000"), "anna_savings", "anna_premium")
    make(
        demo,
        transfer,
        Decimal("20000"),
        "elena_rub",
        "max_rub",
        priority=TransactionPriority.LOW,
    )
    make(
        demo,
        transfer,
        Decimal("2500"),
        "ivan_rub",
        "pavel_rub",
        scheduled_at=MORNING_TIME,
    )
    cancelled = make(demo, transfer, Decimal("800"), "olga_rub", "max_rub")
    demo.queue.cancel(cancelled.transaction_id, "Клиент передумал")


def erroneous_operations(demo):
    transfer, withdrawal = TransactionType.TRANSFER, TransactionType.WITHDRAWAL
    make(demo, transfer, Decimal("-100"), "max_rub", "anna_premium")
    make(demo, transfer, Decimal("100"), "olga_rub", "olga_rub")

    demo.bank.freeze_account(demo.accounts["dmitry_rub"].account_id)
    for _ in range(MAX_FAILED_LOGINS):
        demo.bank.authenticate_client(BLOCKED_CLIENT_ID, "wrong-password")
    print(
        "  Счёт Дмитрия заморожен, Павел заблокирован после "
        f"{MAX_FAILED_LOGINS} неверных паролей"
    )

    make(demo, withdrawal, Decimal("5000"), "sofia_rub")
    make(demo, transfer, Decimal("1000"), "ivan_rub", "dmitry_rub")
    make(demo, TransactionType.DEPOSIT, Decimal("500"), target="dmitry_rub")
    make(demo, withdrawal, Decimal("60000"), "anna_savings")
    make(demo, withdrawal, Decimal("26000"), "anna_premium")
    make(demo, transfer, Decimal("3000"), "pavel_rub", "olga_rub")
    make(demo, transfer, Decimal("700"), "olga_rub", "ACC-404")
    make(demo, transfer, Decimal("5000"), "max_usd", "max_rub", currency=Currency.USD)

    demo.gateway_online = False
    make(demo, TransactionType.EXTERNAL_TRANSFER, Decimal("777"), "olga_rub")


def suspicious_operations(demo):
    demo.gateway_online = True
    transfer = TransactionType.TRANSFER
    make(demo, transfer, Decimal("150000"), "elena_rub", "ivan_rub")

    # Серия длиной в порог частоты: последний перевод уже частый.
    for _ in range(DEFAULT_MAX_OPERATIONS):
        make(demo, transfer, Decimal("1000"), "elena_rub", "max_rub")

    make(demo, transfer, Decimal("200000"), "elena_rub", "pavel_rub")
    make(demo, TransactionType.DEPOSIT, Decimal("120000"), target="max_rub")


def night_operations(demo):
    make(demo, TransactionType.TRANSFER, Decimal("300"), "ivan_rub", "sofia_rub")
    make(demo, TransactionType.TRANSFER, Decimal("180000"), "max_rub", "elena_rub")


def morning_operations(demo):
    demo.bank.unfreeze_account(demo.accounts["dmitry_rub"].account_id)
    print("  Счёт Дмитрия разморожен; отложенный перевод Ивана созрел")
    make(demo, TransactionType.DEPOSIT, Decimal("500"), target="dmitry_rub")
    make(demo, TransactionType.TRANSFER, Decimal("1200"), "olga_rub", "sofia_rub")
    make(demo, TransactionType.WITHDRAWAL, Decimal("2000"), "ivan_rub")


def simulate(demo):
    print_section("2. СИМУЛЯЦИЯ ТРАНЗАКЦИЙ")
    run_batch(demo, "Обычные операции", DAY_START, normal_operations)
    run_batch(demo, "Ошибочные операции", ERRORS_TIME, erroneous_operations)
    run_batch(demo, "Подозрительные операции", SUSPICIOUS_TIME, suspicious_operations)
    run_batch(demo, "Ночь: запрет и риск-контроль", NIGHT_TIME, night_operations)
    run_batch(demo, "Утро", MORNING_TIME, morning_operations)
    print(
        f"\nВсего транзакций: {len(demo.transactions)} в очереди "
        f"+ {demo.refused_at_intake} не принято"
    )


def show_client(demo, client_id):
    bank = demo.bank
    client = bank.clients[client_id]
    print_section(f"3. СЦЕНАРИИ КЛИЕНТА: {client.full_name} (#{client_id})")

    print("Счета:")
    for account in bank.search_accounts(client_id=client_id):
        print(
            f"  {account.account_id}  {type(account).__name__:<18} "
            f"{account.balance:>12} {account.currency}  {account.status}"
        )

    print("\nИстория операций:")
    for row in bank.get_client_history(client_id):
        print(
            f"  {row['time']:%m-%d %H:%M}  {row['direction']:<3} {row['type']:<17} "
            f"{row['amount']:>10} {row['currency']:<3}  {row['status']:<9} "
            f"risk={row['risk_level'] or '-':<6} {row['reason'] or ''}"
        )

    print("\nПодозрительные операции:")
    for record in bank.get_suspicious_operations(client_id=client_id):
        details = record["details"]
        summary = details.get("reasons") or details.get("action")
        print(
            f"  {record['time']:%m-%d %H:%M}  {record['severity']:<8} "
            f"{record['event']:<21} {details.get('amount', '')} {summary}"
        )


def show_reports(demo):
    bank = demo.bank
    print_section("4. ОТЧЁТЫ")

    print(f"Топ-{TOP_CLIENTS} клиентов по балансу (RUB):")
    for place, (client, total) in enumerate(
        bank.get_clients_ranking()[:TOP_CLIENTS], 1
    ):
        print(f"  {place}. {client.full_name:<18} {total:>14}")

    show_transaction_statistics(bank.get_transaction_statistics())

    errors = bank.get_error_statistics(demo.processor.errors)
    print("\nСтатистика ошибок (каждая попытка процессора):")
    print(f"  всего попыток с ошибкой: {errors['total']}")
    for error, count in errors["by_error"].items():
        print(f"  {error:<26} {count}")

    watched = [
        f"{client.full_name} ({client.status})"
        for client in bank.clients.values()
        if client.status is not ClientStatus.ACTIVE
    ]
    print(f"\nКлиенты под наблюдением: {', '.join(watched)}")
    print(f"\nОбщий баланс банка: {bank.get_total_balance()} RUB")


def show_transaction_statistics(statistics):
    print("\nСтатистика транзакций:")
    print(
        f"  обработано {statistics['total']}: исполнено {statistics['completed']}, "
        f"отклонено {statistics['rejected']} "
        f"(успешных {statistics['success_rate']}%)"
    )
    print(f"  заблокировано риск-контролем: {statistics['blocked_by_risk']}")
    print(f"  уровни риска: {statistics['by_risk_level']}")

    for transaction_type, counts in statistics["by_type"].items():
        print(
            f"  {transaction_type:<18} исполнено {counts['completed']:>2}, "
            f"отклонено {counts['rejected']:>2}"
        )

    for currency, volume in statistics["volume"].items():
        fee = statistics["fees"].get(currency)
        print(f"  оборот {currency}: {volume:>12}, комиссии: {fee}")


def export_reports(demo, report_dir):
    print_section("5. ЭКСПОРТ ОТЧЁТОВ И ГРАФИКОВ")
    builder = ReportBuilder(demo.bank, time_provider=demo.clock)
    reports = (
        builder.bank_report(),
        builder.risk_report(),
        builder.client_report(SPOTLIGHT_CLIENT_ID),
    )

    for report in reports:
        base = report_dir / f"{report.kind}_report"
        demo.report_files += [
            builder.export_to_text(report, base.with_suffix(".txt")),
            builder.export_to_json(report, base.with_suffix(".json")),
            builder.export_to_csv(report, base.with_suffix(".csv")),
        ]

    demo.report_files += builder.save_charts(
        report_dir / "charts", client_id=SPOTLIGHT_CLIENT_ID
    )

    for path in demo.report_files:
        print(f"  {path}")


def reset_logs(log_dir):
    for name in ("audit.jsonl", "demo.log"):
        (log_dir / name).unlink(missing_ok=True)


def main(log_dir: Path = LOG_DIR, report_dir: Path = REPORT_DIR) -> Demo:
    # В конвейере Windows пишет в cp1251: кириллица превращается в мусор.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    reset_logs(log_dir)
    configure_logging(
        sink=print_lifecycle_event,
        events=LIFECYCLE_EVENTS,
        log_file=log_dir / "demo.log",
    )

    try:
        demo = build_demo(log_dir)
        show_initialization(demo)
        simulate(demo)
        show_client(demo, SPOTLIGHT_CLIENT_ID)
        show_reports(demo)
        export_reports(demo, report_dir)
        print(f"\nЛоги: {log_dir / 'demo.log'}, аудит: {log_dir / 'audit.jsonl'}")
        return demo
    finally:
        logger.remove()


if __name__ == "__main__":
    main()
