from decimal import ROUND_DOWN, ROUND_HALF_UP, ROUND_UP, Decimal

import pytest

from src.enums import Currency
from src.exceptions import CurrencyConversionError, InvalidOperationError
from src.models import CurrencyConverter


class TestCreation:
    def test_uses_default_rates(self):
        converter = CurrencyConverter()

        assert converter.rates[Currency.RUB] == Decimal("1")
        assert converter.rates[Currency.USD] == Decimal("90")

    def test_accepts_custom_rates(self):
        converter = CurrencyConverter({Currency.RUB: Decimal("1")})

        assert converter.rates == {Currency.RUB: Decimal("1")}

    def test_rates_getter_returns_a_copy(self):
        converter = CurrencyConverter()

        converter.rates[Currency.USD] = Decimal("1")

        assert converter.rates[Currency.USD] == Decimal("90")

    @pytest.mark.parametrize(
        "rates",
        [
            {"rub": Decimal("1")},
            {Currency.RUB: 90},
            {Currency.RUB: 90.0},
            {Currency.RUB: Decimal("0")},
            {Currency.RUB: Decimal("-1")},
            {Currency.RUB: Decimal("NaN")},
        ],
    )
    def test_rejects_invalid_rates(self, rates):
        with pytest.raises(InvalidOperationError):
            CurrencyConverter(rates)


class TestConversion:
    @pytest.fixture
    def converter(self):
        return CurrencyConverter()

    def test_same_currency_is_unchanged(self, converter):
        assert converter.convert(Decimal("100"), Currency.RUB, Currency.RUB) == Decimal(
            "100.00"
        )

    @pytest.mark.parametrize(
        "amount, source, target, expected",
        [
            ("900", Currency.RUB, Currency.USD, "10.00"),
            ("10", Currency.USD, Currency.RUB, "900.00"),
            ("100", Currency.USD, Currency.EUR, "90.00"),
            ("100", Currency.EUR, Currency.USD, "111.11"),
            ("1000", Currency.KZT, Currency.RUB, "190.00"),
        ],
    )
    def test_cross_rates(self, converter, amount, source, target, expected):
        assert converter.convert(Decimal(amount), source, target) == Decimal(expected)

    def test_result_is_rounded_to_kopecks(self, converter):
        assert converter.convert(Decimal("1"), Currency.RUB, Currency.USD) == Decimal(
            "0.01"
        )

    def test_missing_rate_fails(self):
        converter = CurrencyConverter({Currency.RUB: Decimal("1")})

        with pytest.raises(CurrencyConversionError):
            converter.convert(Decimal("100"), Currency.RUB, Currency.USD)


class TestRounding:
    @pytest.mark.parametrize(
        ("rounding", "expected"),
        [(ROUND_HALF_UP, "1.11"), (ROUND_DOWN, "1.11"), (ROUND_UP, "1.12")],
    )
    def test_rounding_mode_is_applied(self, rounding, expected):
        converter = CurrencyConverter()

        result = converter.convert(Decimal("100"), Currency.RUB, Currency.USD, rounding)

        assert result == Decimal(expected)

    def test_default_is_half_up(self):
        converter = CurrencyConverter()

        assert converter.convert(Decimal("50"), Currency.EUR, Currency.USD) == Decimal(
            "55.56"
        )
