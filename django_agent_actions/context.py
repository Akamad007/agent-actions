"""Request context abstraction.

RequestContext carries identity information for every action invocation.

Usage in a Django view::

    ctx = RequestContext.from_request(request)
    # or with a custom auth backend:
    ctx = RequestContext.from_request(request, auth_backend=MyBackend())

To add real authentication implement the ``AuthBackend`` protocol and pass it
to ``ContextResolver``.  Without an ``AuthBackend`` the framework falls back
to trusting the ``X-*`` identity headers — suitable when upstream middleware
has already authenticated the caller.

Key design decisions:
- Raw ``Authorization`` values are *never* stored on the context object.
  ``headers`` only contains a redacted copy.
- The ``authenticated`` flag distinguishes explicit identity from anonymous.
- ``has_scope`` lets action definitions declare fine-grained access
  requirements without forcing a full RBAC system on the host application.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

from django_agent_actions.redaction import redact_headers

# ---------------------------------------------------------------------------
# AuthBackend — plug-in interface for credential validation
# ---------------------------------------------------------------------------


@runtime_checkable
class AuthBackend(Protocol):
    """Validates a raw credential string and returns identity fields.

    Implement this protocol to add JWT / OAuth / API-key validation without
    modifying the framework core.

    Raise ``PermissionError`` (or a subclass) if the credential is invalid.
    Return a ``dict`` with at least ``actor_id`` and optionally ``roles``
    (``list[str]``) and ``tenant_id`` (``str``).
    """

    def authenticate(self, credential: str) -> dict:
        ...


# ---------------------------------------------------------------------------
# RequestContext
# ---------------------------------------------------------------------------


@dataclass
class RequestContext:
    actor_id: str
    roles: list[str] = field(default_factory=list)
    tenant_id: str | None = None
    authenticated: bool = False
    # Redacted copy of the incoming headers — auth values are never stored raw.
    headers: dict = field(default_factory=dict)

    @classmethod
    def from_request(
        cls,
        request,
        *,
        auth_backend: "AuthBackend | None" = None,
    ) -> "RequestContext":
        """Build a ``RequestContext`` from a Django ``HttpRequest``.

        Django stores HTTP headers in ``request.META`` as ``HTTP_*`` keys
        (uppercase, underscores).  This method normalises them to lowercase
        hyphen-separated names (e.g. ``HTTP_X_ACTOR_ID`` → ``x-actor-id``)
        and then delegates to ``from_headers``.
        """
        headers: dict[str, str] = {}
        for key, value in request.META.items():
            if key.startswith("HTTP_"):
                header_name = key[5:].lower().replace("_", "-")
                headers[header_name] = value
            elif key == "CONTENT_TYPE":
                headers["content-type"] = value
        return cls.from_headers(headers, auth_backend=auth_backend)

    @classmethod
    def from_headers(
        cls,
        headers: dict,
        *,
        auth_backend: "AuthBackend | None" = None,
    ) -> "RequestContext":
        """Build a ``RequestContext`` from a plain headers dict.

        Keys should be lowercase (e.g. ``x-actor-id``).  If *auth_backend* is
        provided the ``authorization`` header is validated through it and
        identity is populated from the returned payload.  Without a backend,
        identity is read from the ``X-*`` headers set by upstream middleware.

        The stored ``headers`` dict is always redacted — no raw auth material
        is ever persisted on the context object.
        """
        lower = {k.lower(): v for k, v in headers.items()}
        safe_headers = redact_headers(lower)

        if auth_backend is not None:
            raw_credential = lower.get("authorization", "")
            if not raw_credential:
                raise PermissionError("Authorization header is required.")
            try:
                identity = auth_backend.authenticate(raw_credential)
            except PermissionError:
                raise
            except Exception as exc:
                raise PermissionError("Authentication failed.") from exc
            return cls(
                actor_id=identity.get("actor_id", "anonymous"),
                roles=list(identity.get("roles", [])),
                tenant_id=identity.get("tenant_id") or None,
                authenticated=True,
                headers=safe_headers,
            )

        # No auth backend — trust the X-* headers set by upstream middleware.
        actor_id = lower.get("x-actor-id", "anonymous")
        roles_raw = lower.get("x-roles", "")
        roles = [r.strip() for r in roles_raw.split(",") if r.strip()]
        tenant_id = lower.get("x-tenant-id") or None
        return cls(
            actor_id=actor_id,
            roles=roles,
            tenant_id=tenant_id,
            authenticated=actor_id != "anonymous",
            headers=safe_headers,
        )

    def has_role(self, role: str) -> bool:
        return role in self.roles

    def has_scope(self, scope: str) -> bool:
        """Return True if the actor holds *scope*.

        Scopes may be represented as plain role strings (e.g. ``"finance"``) or
        with a ``scope:`` prefix (e.g. ``"scope:finance"``).  Both forms match.
        """
        return scope in self.roles or f"scope:{scope}" in self.roles


# ---------------------------------------------------------------------------
# ContextResolver
# ---------------------------------------------------------------------------


class ContextResolver:
    """Builds a ``RequestContext`` from an incoming Django request or headers.

    Attach an *auth_backend* to add real credential validation.  Without one,
    context is built directly from the ``X-*`` identity headers.

    Usage::

        resolver = ContextResolver()
        ctx = resolver.resolve_request(request)   # Django HttpRequest
        ctx = resolver.resolve(dict(request.META)) # raw headers dict
    """

    def __init__(self, auth_backend: "AuthBackend | None" = None) -> None:
        self.auth_backend = auth_backend

    def resolve_request(self, request) -> RequestContext:
        """Build context from a Django ``HttpRequest``."""
        return RequestContext.from_request(request, auth_backend=self.auth_backend)

    def resolve(self, headers: dict) -> RequestContext:
        """Build context from a plain lowercased headers dict."""
        return RequestContext.from_headers(headers, auth_backend=self.auth_backend)
