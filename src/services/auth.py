"""Проверка пароля клиента и реакция на неудачные попытки."""

from src.enums import ClientStatus
from src.exceptions import InvalidOperationError


class AuthService:
    def __init__(self, journal):
        self._journal = journal

    def authenticate(self, client, password):
        if client.status is ClientStatus.BLOCKED:
            self._journal.flag_client(client, "blocked_client_login")
            raise InvalidOperationError("Client is blocked")

        if client.check_password(password):
            client.reset_failed_logins()
            return True

        client.register_failed_login()
        self._journal.flag_client(client, "failed_login")

        if client.status is ClientStatus.BLOCKED:
            self._journal.record(client.client_id, "client_blocked_after_failed_logins")

        return False

    def ensure_can_operate(self, client, action):
        if client.status is ClientStatus.BLOCKED:
            self._journal.flag_client(client, action)
            raise InvalidOperationError("Blocked client cannot operate")
