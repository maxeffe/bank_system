from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from src.exceptions import InvalidOperationError

MONEY_PRECISION = Decimal("0.01")


def is_valid_money(value) -> bool:
    if isinstance(value, bool):
        return False

    if isinstance(value, int):
        return True

    if isinstance(value, Decimal):
        return value.is_finite()

    return False


def to_money(value, error_message: str) -> Decimal:
    """Приводит сумму к Decimal с копейками или бросает понятную ошибку."""
    if not is_valid_money(value):
        raise InvalidOperationError(error_message)

    try:
        return Decimal(value).quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)
    except InvalidOperation:
        raise InvalidOperationError(error_message) from None


def to_rate(value, error_message: str) -> Decimal:
    """Ставка или курс: тот же Decimal, но без округления до копеек."""
    if not is_valid_money(value):
        raise InvalidOperationError(error_message)

    return Decimal(value)


def to_non_negative_money(value, error_message: str) -> Decimal:
    amount = to_money(value, error_message)

    if amount < 0:
        raise InvalidOperationError(error_message)

    return amount


def to_positive_money(value, error_message: str) -> Decimal:
    amount = to_money(value, error_message)

    if amount <= 0:
        raise InvalidOperationError(error_message)

    return amount


def to_non_negative_rate(value, error_message: str) -> Decimal:
    rate = to_rate(value, error_message)

    if rate < 0:
        raise InvalidOperationError(error_message)

    return rate
