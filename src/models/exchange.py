from decimal import ROUND_HALF_UP, Decimal

from src.enums import Currency
from src.exceptions import CurrencyConversionError, InvalidOperationError
from src.money import MONEY_PRECISION

DEFAULT_EXCHANGE_RATES = {
    Currency.RUB: Decimal("1"),
    Currency.USD: Decimal("90"),
    Currency.EUR: Decimal("100"),
    Currency.KZT: Decimal("0.19"),
    Currency.CNY: Decimal("12.5"),
}


class CurrencyConverter:
    """Пересчитывает суммы между валютами. Все курсы заданы в рублях."""

    def __init__(self, rates: dict = None):
        rates = dict(rates if rates is not None else DEFAULT_EXCHANGE_RATES)

        for currency, rate in rates.items():
            if not isinstance(currency, Currency):
                raise InvalidOperationError("Invalid currency")

            if not isinstance(rate, Decimal) or not rate.is_finite() or rate <= 0:
                raise InvalidOperationError("Exchange rate must be a positive Decimal")

        self._rates = rates

    @property
    def rates(self):
        return self._rates.copy()

    def convert(self, amount, from_currency, to_currency):
        if from_currency is to_currency:
            return Decimal(amount).quantize(MONEY_PRECISION, rounding=ROUND_HALF_UP)

        for currency in (from_currency, to_currency):
            if currency not in self._rates:
                raise CurrencyConversionError(f"No exchange rate for {currency}")

        in_rubles = Decimal(amount) * self._rates[from_currency]
        return (in_rubles / self._rates[to_currency]).quantize(
            MONEY_PRECISION, rounding=ROUND_HALF_UP
        )
