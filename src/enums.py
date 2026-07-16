from enum import StrEnum


class AccountStatus(StrEnum):
    ACTIVE = 'active'
    FROZEN = "frozen"
    CLOSED = "closed"

class Currency(StrEnum):
    RUB = 'rub'
    USD = 'usd'
    EUR = 'eur'
    KZT = 'kzt'
    CNY = 'cny'