from enum import StrEnum


class AccountStatus(StrEnum):
    ACTIVE = "active"
    FROZEN = "frozen"
    CLOSED = "closed"


class Currency(StrEnum):
    RUB = "rub"
    USD = "usd"
    EUR = "eur"
    KZT = "kzt"
    CNY = "cny"


class ClientStatus(StrEnum):
    ACTIVE = "active"
    BLOCKED = "blocked"
    SUSPICIOUS = "suspicious"


class TransactionType(StrEnum):
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    TRANSFER = "transfer"
    EXTERNAL_TRANSFER = "external_transfer"


class TransactionStatus(StrEnum):
    PENDING = "pending"
    SCHEDULED = "scheduled"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TransactionPriority(StrEnum):
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"
