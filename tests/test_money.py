from decimal import Decimal

import pytest

from src.exceptions import InvalidOperationError
from src.money import MONEY_PRECISION, is_valid_money, to_money, to_rate


class TestIsValidMoney:
    @pytest.mark.parametrize("value", [100, 0, -5, Decimal("100.50"), Decimal("0")])
    def test_accepts_int_and_decimal(self, value):
        assert is_valid_money(value) is True

    @pytest.mark.parametrize(
        "value",
        [0.1, 100.0, True, False, "100", None, [], Decimal("NaN"), Decimal("Infinity")],
    )
    def test_rejects_everything_else(self, value):
        assert is_valid_money(value) is False


class TestToMoney:
    @pytest.mark.parametrize(
        "value, expected",
        [
            (100, "100.00"),
            (Decimal("10.005"), "10.01"),
            (Decimal("10.004"), "10.00"),
            (Decimal("-3"), "-3.00"),
        ],
    )
    def test_quantizes_to_kopecks(self, value, expected):
        assert to_money(value, "bad") == Decimal(expected)

    def test_result_has_two_places(self):
        assert (
            to_money(7, "bad").as_tuple().exponent
            == MONEY_PRECISION.as_tuple().exponent
        )

    @pytest.mark.parametrize("value", [0.1, True, "100", None, Decimal("NaN")])
    def test_rejects_invalid_value(self, value):
        with pytest.raises(InvalidOperationError, match="bad"):
            to_money(value, "bad")


class TestToRate:
    def test_keeps_full_precision(self):
        assert to_rate(Decimal("0.0375"), "bad") == Decimal("0.0375")

    @pytest.mark.parametrize("value", [0.1, True, "0.5", None])
    def test_rejects_invalid_value(self, value):
        with pytest.raises(InvalidOperationError, match="bad"):
            to_rate(value, "bad")
