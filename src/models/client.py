import hashlib
import hmac
import re
import secrets

from src.enums import ClientStatus
from src.exceptions import InvalidOperationError

MIN_CLIENT_AGE = 18
MAX_FAILED_LOGINS = 3

ALLOWED_CONTACTS = ("email", "phone")
EMAIL_PATTERN = re.compile(r"[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}")
PHONE_PATTERN = re.compile(r"\+?\d[\d\s()-]{4,19}")

PASSWORD_SALT_BYTES = 16
PASSWORD_ITERATIONS = 200_000


class Client:
    def __init__(
        self,
        client_id,
        full_name: str,
        age: int,
        contacts: dict = None,
        password: str = None,
        status=ClientStatus.ACTIVE,
    ):
        if client_id is None:
            raise InvalidOperationError("Client id is required")

        if not isinstance(full_name, str) or not full_name.strip():
            raise InvalidOperationError("Full name is required")

        if not isinstance(age, int) or isinstance(age, bool):
            raise InvalidOperationError("Age must be an integer")

        if age < MIN_CLIENT_AGE:
            raise InvalidOperationError(
                f"Client must be at least {MIN_CLIENT_AGE} years old"
            )

        if not isinstance(status, ClientStatus):
            raise InvalidOperationError("Invalid client status")

        if not isinstance(password, str) or not password:
            raise InvalidOperationError("Password is required")

        self._client_id = client_id
        self._full_name = full_name
        self._age = age
        self._contacts = self._validate_contacts(contacts)
        self._password_salt = secrets.token_bytes(PASSWORD_SALT_BYTES)
        self._password_hash = self._hash_password(password, self._password_salt)
        self._status = status
        self._account_ids = []
        self._failed_login_attempts = 0

    @property
    def client_id(self):
        return self._client_id

    @property
    def full_name(self):
        return self._full_name

    @property
    def age(self):
        return self._age

    @property
    def contacts(self):
        return self._contacts.copy()

    @property
    def account_ids(self):
        return self._account_ids.copy()

    @property
    def failed_login_attempts(self):
        return self._failed_login_attempts

    @property
    def status(self):
        return self._status

    @status.setter
    def status(self, new_status):
        if not isinstance(new_status, ClientStatus):
            raise InvalidOperationError("Invalid client status")

        self._status = new_status

    @staticmethod
    def _validate_contacts(contacts):
        if contacts is None:
            return {}

        if not isinstance(contacts, dict):
            raise InvalidOperationError("Contacts must be a dictionary")

        validated_contacts = {}

        for contact_type, value in contacts.items():
            if contact_type not in ALLOWED_CONTACTS:
                raise InvalidOperationError("Unknown contact type")

            if not isinstance(value, str) or not value.strip():
                raise InvalidOperationError("Contact value must be a non-empty string")

            value = value.strip()
            pattern = EMAIL_PATTERN if contact_type == "email" else PHONE_PATTERN

            if not pattern.fullmatch(value):
                raise InvalidOperationError(f"Invalid {contact_type}")

            validated_contacts[contact_type] = value

        return validated_contacts

    @staticmethod
    def _hash_password(password, salt):
        return hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
        )

    def check_password(self, password):
        if not isinstance(password, str):
            return False

        candidate = self._hash_password(password, self._password_salt)
        return hmac.compare_digest(candidate, self._password_hash)

    def add_account_id(self, account_id):
        if account_id not in self._account_ids:
            self._account_ids.append(account_id)

    def register_failed_login(self):
        self._failed_login_attempts += 1

        if self._failed_login_attempts >= MAX_FAILED_LOGINS:
            self.status = ClientStatus.BLOCKED

    def reset_failed_logins(self):
        self._failed_login_attempts = 0

    def mark_suspicious(self):
        if self._status is ClientStatus.ACTIVE:
            self._status = ClientStatus.SUSPICIOUS

    def __str__(self):
        return f"""
            client_id: {self._client_id}\n
            full_name: {self._full_name}\n
            age: {self._age}\n
            status: {self._status}\n
            accounts: {self._account_ids}\n
            contacts: {self._contacts}\n
            """
