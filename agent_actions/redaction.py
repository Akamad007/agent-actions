"""Centralized redaction utility for sensitive field names.

Prevents passwords, tokens, and other secrets from being written to
audit logs or returned in API responses.

All services that store or return user-supplied data should call
``redact_dict`` before persisting or serialising.
"""

from __future__ import annotations

from typing import Any

# Field names (lower-cased) whose values are always replaced with a sentinel.
# Add application-specific names here rather than at each call site.
SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "token",
        "api_key",
        "apikey",
        "access_token",
        "refresh_token",
        "id_token",
        "authorization",
        "x-api-key",
        "x-auth-token",
        "x-authorization",
        "client_secret",
        "private_key",
        "credential",
        "credentials",
        "ssn",
        "credit_card",
        "card_number",
        "cvv",
    }
)

_REDACTED = "**REDACTED**"


def redact_dict(data: Any, *, _depth: int = 0) -> Any:
    """Recursively redact sensitive keys in a dict, list, or scalar.

    - Dict keys matching ``SENSITIVE_KEYS`` (case-insensitive) have their
      values replaced with ``**REDACTED**``.
    - Lists are recursed into element-by-element.
    - Scalars are returned unchanged.
    - Depth is capped at 10 to prevent stack overflow on pathological inputs.
    """
    if _depth > 10:
        return data
    if isinstance(data, dict):
        return {
            k: (
                _REDACTED
                if k.lower() in SENSITIVE_KEYS
                else redact_dict(v, _depth=_depth + 1)
            )
            for k, v in data.items()
        }
    if isinstance(data, list):
        return [redact_dict(item, _depth=_depth + 1) for item in data]
    return data


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Return a copy of *headers* with auth/sensitive values replaced."""
    return {
        k: (_REDACTED if k.lower() in SENSITIVE_KEYS else v)
        for k, v in headers.items()
    }
