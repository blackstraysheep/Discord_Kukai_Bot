class ServiceError(Exception):
    """Base for all domain errors raised by service layer."""


class NotFoundError(ServiceError):
    """Requested resource does not exist."""


class PermissionError(ServiceError):
    """Caller lacks required permission."""


class InvalidStateError(ServiceError):
    """Operation is not allowed in the current kukai state."""


class ValidationError(ServiceError):
    """Input data fails business rules."""


class DeadlineConflictError(ServiceError):
    """Deadline ordering is inconsistent (e.g. close before open)."""
