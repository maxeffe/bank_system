class AccountFrozenError(Exception):
    pass


class AccountClosedError(Exception):
    pass


class InvalidOperationError(Exception):
    pass


class InsufficientFundsError(Exception):
    pass


class TransactionError(Exception):
    pass


class TransientTransactionError(TransactionError):
    pass


class CurrencyConversionError(TransactionError):
    pass
