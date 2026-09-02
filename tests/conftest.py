from datetime import datetime
from decimal import Decimal

import pytest

import src.models.client as client_module
from src.models import Bank, Client

REAL_PASSWORD_ITERATIONS = client_module.PASSWORD_ITERATIONS
TEST_PASSWORD_ITERATIONS = 1_000


@pytest.fixture(autouse=True)
def fast_password_hashing(monkeypatch):
    """Хеширование пароля намеренно медленное. В тестах снижаем цену."""
    monkeypatch.setattr(client_module, "PASSWORD_ITERATIONS", TEST_PASSWORD_ITERATIONS)


@pytest.fixture(scope="session")
def real_password_iterations():
    return REAL_PASSWORD_ITERATIONS


@pytest.fixture
def make_client():
    def factory(client_id=1, full_name="Max Petrov", age=30, **kwargs):
        kwargs.setdefault("password", "secret-pass")
        return Client(client_id=client_id, full_name=full_name, age=age, **kwargs)

    return factory


@pytest.fixture
def bank():
    return Bank("Test Bank")


@pytest.fixture
def night_bank():
    return Bank("Night Bank", time_provider=lambda: datetime(2026, 7, 16, 2, 0, 0))


@pytest.fixture
def bank_with_client(bank, make_client):
    bank.add_client(make_client())
    return bank


@pytest.fixture
def account(bank_with_client):
    return bank_with_client.open_account(1, "bank", balance=Decimal("1000"))
