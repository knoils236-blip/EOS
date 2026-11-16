class EosFileDbError(Exception):
    pass


class EosControlLockError(Exception):
    """Raised when this orchestrator cannot take, or has lost, exclusive control of the lab state in its database."""


class DbConnectionError(Exception):
    """Raised when database connection fails (e.g., timeout, connection refused)."""
