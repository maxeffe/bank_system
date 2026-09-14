import pytest

from src.enums import ClientStatus
from src.exceptions import InvalidOperationError
from src.models import Client


class TestClientCreation:
    def test_creates_with_valid_data(self, make_client):
        client = make_client(contacts={"email": "max@example.com"})

        assert client.client_id == 1
        assert client.full_name == "Max Petrov"
        assert client.age == 30
        assert client.account_ids == []
        assert client.status is ClientStatus.ACTIVE
        assert client.failed_login_attempts == 0

    @pytest.mark.parametrize("age", [0, 17, -5])
    def test_rejects_underage(self, make_client, age):
        with pytest.raises(InvalidOperationError):
            make_client(age=age)

    def test_accepts_exactly_eighteen(self, make_client):
        client = make_client(age=18)

        assert client.age == 18

    @pytest.mark.parametrize("age", ["30", 30.5, True, None])
    def test_rejects_non_integer_age(self, make_client, age):
        with pytest.raises(InvalidOperationError):
            make_client(age=age)

    @pytest.mark.parametrize(
        "field, value",
        [
            ("client_id", None),
            ("full_name", ""),
            ("full_name", "   "),
            ("full_name", 123),
        ],
    )
    def test_rejects_invalid_identity(self, make_client, field, value):
        with pytest.raises(InvalidOperationError):
            make_client(**{field: value})

    @pytest.mark.parametrize("password", [None, "", 123])
    def test_rejects_invalid_password(self, password):
        with pytest.raises(InvalidOperationError):
            Client(client_id=1, full_name="Max", age=30, password=password)

    def test_rejects_invalid_status(self, make_client):
        with pytest.raises(InvalidOperationError):
            make_client(status="active")


class TestContacts:
    def test_accepts_email_and_phone(self, make_client):
        client = make_client(
            contacts={"email": "max@example.com", "phone": "+79990000001"}
        )

        assert client.contacts == {
            "email": "max@example.com",
            "phone": "+79990000001",
        }

    def test_defaults_to_empty(self, make_client):
        client = make_client()

        assert client.contacts == {}

    def test_strips_whitespace(self, make_client):
        client = make_client(contacts={"email": "  max@example.com  "})

        assert client.contacts["email"] == "max@example.com"

    def test_rejects_unknown_contact_type(self, make_client):
        with pytest.raises(InvalidOperationError):
            make_client(contacts={"telegram": "@max"})

    @pytest.mark.parametrize(
        "email", ["max", "max@", "@example.com", "max@example", "a b@c.com"]
    )
    def test_rejects_invalid_email(self, make_client, email):
        with pytest.raises(InvalidOperationError):
            make_client(contacts={"email": email})

    @pytest.mark.parametrize("phone", ["abc", "12", "+", "phone: 79990000001"])
    def test_rejects_invalid_phone(self, make_client, phone):
        with pytest.raises(InvalidOperationError):
            make_client(contacts={"phone": phone})

    @pytest.mark.parametrize("value", [None, 123, ["+79990000001"], ""])
    def test_rejects_non_string_value(self, make_client, value):
        with pytest.raises(InvalidOperationError):
            make_client(contacts={"phone": value})

    def test_rejects_non_dict(self, make_client):
        with pytest.raises(InvalidOperationError):
            make_client(contacts=[("email", "max@example.com")])

    def test_caller_dict_cannot_be_changed_from_outside(self, make_client):
        source = {"email": "max@example.com"}
        client = make_client(contacts=source)

        source["email"] = "hacker@evil.com"

        assert client.contacts["email"] == "max@example.com"

    def test_getter_returns_a_copy(self, make_client):
        client = make_client(contacts={"email": "max@example.com"})

        client.contacts["email"] = "hacker@evil.com"

        assert client.contacts["email"] == "max@example.com"


class TestPassword:
    def test_plain_password_is_not_stored(self, make_client):
        client = make_client(password="super-secret")

        stored = [
            value
            for value in vars(client).values()
            if isinstance(value, str) and "super-secret" in value
        ]

        assert stored == []
        assert not hasattr(client, "_password")

    def test_correct_password_accepted(self, make_client):
        client = make_client(password="super-secret")

        assert client.check_password("super-secret") is True

    @pytest.mark.parametrize(
        "wrong", ["super-secre", "Super-Secret", "", "x", None, 123]
    )
    def test_wrong_password_rejected(self, make_client, wrong):
        client = make_client(password="super-secret")

        assert client.check_password(wrong) is False

    def test_hashing_cost_is_high_in_production(self, real_password_iterations):
        assert real_password_iterations >= 200_000

    def test_same_password_gives_different_hashes(self, make_client):
        first = make_client(client_id=1, password="same-pass")
        second = make_client(client_id=2, password="same-pass")

        assert first._password_hash != second._password_hash
        assert first._password_salt != second._password_salt


class TestLoginAttempts:
    def test_blocks_after_three_failures(self, make_client):
        client = make_client()

        for _ in range(3):
            client.register_failed_login()

        assert client.failed_login_attempts == 3
        assert client.status is ClientStatus.BLOCKED

    def test_two_failures_do_not_block(self, make_client):
        client = make_client()

        client.register_failed_login()
        client.register_failed_login()

        assert client.status is ClientStatus.ACTIVE

    def test_reset_clears_counter(self, make_client):
        client = make_client()
        client.register_failed_login()

        client.reset_failed_logins()

        assert client.failed_login_attempts == 0


class TestAccountIds:
    def test_adds_account_id(self, make_client):
        client = make_client()

        client.add_account_id("abc123")

        assert client.account_ids == ["abc123"]

    def test_same_id_is_added_once(self, make_client):
        client = make_client()

        client.add_account_id("abc123")
        client.add_account_id("abc123")

        assert client.account_ids == ["abc123"]

    def test_list_is_a_copy(self, make_client):
        client = make_client()
        client.add_account_id("abc123")

        client.account_ids.append("hacked")

        assert client.account_ids == ["abc123"]


class TestStatusChanges:
    def test_mark_suspicious_from_active(self, make_client):
        client = make_client()

        client.mark_suspicious()

        assert client.status is ClientStatus.SUSPICIOUS

    def test_mark_suspicious_does_not_override_blocked(self, make_client):
        client = make_client()
        client.status = ClientStatus.BLOCKED

        client.mark_suspicious()

        assert client.status is ClientStatus.BLOCKED

    def test_status_setter_rejects_non_enum(self, make_client):
        client = make_client()

        with pytest.raises(InvalidOperationError):
            client.status = "blocked"
