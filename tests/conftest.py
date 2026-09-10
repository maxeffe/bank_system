from datetime import datetime
from decimal import Decimal

import pytest
from loguru import logger

import src.models.client as client_module
from src.enums import Currency
from src.models import Bank, Client, TransactionProcessor, TransactionQueue

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


DAYTIME = datetime(2026, 9, 3, 12, 0)


@pytest.fixture
def daytime():
    """Часы, застывшие в полдень: тесты не должны зависеть от времени суток."""
    return lambda: DAYTIME


@pytest.fixture
def bank(daytime):
    return Bank("Test Bank", time_provider=daytime)


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


@pytest.fixture
def transfer_bank(bank, make_client):
    bank.add_client(make_client(client_id=1, full_name="Max Petrov"))
    bank.add_client(make_client(client_id=2, full_name="Anna Ivanova"))
    bank.open_account(1, "bank", balance=Decimal("10000"))
    bank.open_account(2, "bank", balance=Decimal("1000"))
    return bank


@pytest.fixture
def source_account(transfer_bank):
    return transfer_bank.search_accounts(client_id=1)[0]


@pytest.fixture
def target_account(transfer_bank):
    return transfer_bank.search_accounts(client_id=2)[0]


@pytest.fixture
def usd_account(transfer_bank):
    return transfer_bank.open_account(
        2, "bank", balance=Decimal("100"), currency=Currency.USD
    )


@pytest.fixture
def queue():
    return TransactionQueue()


@pytest.fixture
def processor(transfer_bank):
    return TransactionProcessor(transfer_bank)


@pytest.fixture
def log_events():
    """Собирает структурные события, которые пишут модели."""
    events = []
    handler_id = logger.add(lambda message: events.append(message.record), level="INFO")
    yield events
    logger.remove(handler_id)
