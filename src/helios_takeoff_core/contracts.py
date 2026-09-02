"""Small, dependency-free validation helpers for public HELIOS P0 contracts."""

from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation
from typing import Any

from .errors import ValidationError


DECIMAL_PATTERN = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")


def require_text(value: Any, field_name: str) -> str:
    """Return a non-blank string or raise a stable validation error."""
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field_name} is required")
    return value.strip()


def require_decimal_string(
    value: Any,
    field_name: str,
    *,
    minimum: Decimal | None = None,
    maximum: Decimal | None = None,
) -> str:
    """Validate a non-exponent decimal string without accepting binary floats."""
    if not isinstance(value, str) or not DECIMAL_PATTERN.fullmatch(value):
        raise ValidationError(f"{field_name} must be a plain decimal string")
    try:
        decimal_value = Decimal(value)
    except InvalidOperation as error:  # Defensive: regex already narrows the input.
        raise ValidationError(f"{field_name} must be a valid decimal string") from error
    if minimum is not None and decimal_value < minimum:
        raise ValidationError(f"{field_name} must be at least {minimum}")
    if maximum is not None and decimal_value > maximum:
        raise ValidationError(f"{field_name} must be at most {maximum}")
    return value
