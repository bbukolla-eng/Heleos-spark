"""Domain errors exposed by HELIOS Takeoff Core."""


class HeliosTakeoffError(Exception):
    """Base class for safe, user-actionable domain failures."""


class ValidationError(HeliosTakeoffError):
    """Raised when caller input is malformed or incomplete."""


class NotFoundError(HeliosTakeoffError):
    """Raised when a referenced resource does not exist."""


class ImmutableStateError(HeliosTakeoffError):
    """Raised when a caller tries to alter an immutable or frozen record."""


class PreconditionError(HeliosTakeoffError):
    """Raised when a lifecycle action lacks a required prior state."""


class ConflictError(HeliosTakeoffError):
    """Raised when a valid request conflicts with an immutable prior operation."""


class AuthorizationError(HeliosTakeoffError):
    """Raised when an actor does not hold the required role."""
