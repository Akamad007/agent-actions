"""Auth example — API key and JWT-style authentication with role-based policies.

This example shows how to wire real auth into agent-actions using FastAPI
middleware. The framework's RequestContext is intentionally auth-agnostic;
this middleware pattern is the recommended integration point.

Two auth strategies are shown side by side:
  1. API key — a static key looked up in a registry (simplest)
  2. Bearer token — a fake JWT decoder (swap for PyJWT / python-jose in prod)

Run:
    uvicorn examples.auth_app:fastapi_app --reload

Try it:

    # Valid API key, finance role — allowed
    curl -s -X POST http://localhost:8000/actions/get_invoice/execute \
         -H "Content-Type: application/json" \
         -H "Authorization: ApiKey key-finance-001" \
         -d '{"inputs": {"invoice_id": "INV-100"}}' | jq

    # Valid API key, viewer role — denied on finance-only action
    curl -s -X POST http://localhost:8000/actions/issue_refund/execute \
         -H "Content-Type: application/json" \
         -H "Authorization: ApiKey key-viewer-001" \
         -d '{"inputs": {"invoice_id": "INV-100", "amount": 50.0}}' | jq

    # Missing auth — 401
    curl -s -X POST http://localhost:8000/actions/get_invoice/execute \
         -H "Content-Type: application/json" \
         -d '{"inputs": {"invoice_id": "INV-100"}}' | jq

    # Bearer token (fake JWT)
    curl -s -X POST http://localhost:8000/actions/get_invoice/execute \
         -H "Content-Type: application/json" \
         -H "Authorization: Bearer alice:finance,admin:acme-corp" \
         -d '{"inputs": {"invoice_id": "INV-200"}}' | jq
"""

from __future__ import annotations

import base64
from typing import Optional

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from agent_actions import AgentActionApp, RequestContext, action
from agent_actions.policies import RoleBasedPolicy

# ---------------------------------------------------------------------------
# Fake credential store — replace with your DB / secrets manager
# ---------------------------------------------------------------------------

# Maps API key -> (actor_id, roles, tenant_id)
API_KEY_STORE: dict[str, tuple[str, list[str], str]] = {
    "key-finance-001": ("svc-finance", ["finance", "viewer"], "acme"),
    "key-admin-001":   ("svc-admin",   ["admin", "finance", "viewer"], "acme"),
    "key-viewer-001":  ("svc-viewer",  ["viewer"], "acme"),
}


def resolve_api_key(key: str) -> Optional[tuple[str, list[str], str]]:
    return API_KEY_STORE.get(key)


def resolve_bearer_token(token: str) -> Optional[tuple[str, list[str], str]]:
    """Minimal fake decoder: token format is 'actor_id:role1,role2:tenant_id'.

    In production replace this with PyJWT:
        import jwt
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    """
    try:
        parts = token.split(":")
        if len(parts) != 3:
            return None
        actor_id, roles_str, tenant_id = parts
        roles = [r.strip() for r in roles_str.split(",") if r.strip()]
        return actor_id, roles, tenant_id
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Auth middleware
# ---------------------------------------------------------------------------

UNPROTECTED_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}


class AuthMiddleware(BaseHTTPMiddleware):
    """Validates the Authorization header and injects identity headers.

    After this middleware runs, downstream code (including agent-actions'
    RequestContext.from_headers) sees X-Actor-Id, X-Roles, X-Tenant-Id —
    the same headers it always expects. Auth is fully decoupled from actions.
    """

    async def dispatch(self, request: Request, call_next):
        if request.url.path in UNPROTECTED_PATHS:
            return await call_next(request)

        auth_header = request.headers.get("Authorization", "")
        identity = None

        if auth_header.startswith("ApiKey "):
            api_key = auth_header[len("ApiKey "):]
            identity = resolve_api_key(api_key)

        elif auth_header.startswith("Bearer "):
            token = auth_header[len("Bearer "):]
            identity = resolve_bearer_token(token)

        if identity is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or missing credentials."},
            )

        actor_id, roles, tenant_id = identity

        # Inject identity into request state so the action runtime picks it up
        # via RequestContext.from_headers. We rebuild the scope headers.
        request.state.actor_id = actor_id
        request.state.roles = roles
        request.state.tenant_id = tenant_id

        # Starlette headers are immutable; we patch by subclassing the scope.
        # The cleanest approach: override the headers passed to the ASGI scope.
        additional = {
            b"x-actor-id": actor_id.encode(),
            b"x-roles": ",".join(roles).encode(),
            b"x-tenant-id": tenant_id.encode(),
        }
        original_headers = list(request.scope["headers"])
        # Remove any existing identity headers to prevent spoofing
        stripped = [
            (k, v) for k, v in original_headers
            if k.lower() not in {b"x-actor-id", b"x-roles", b"x-tenant-id"}
        ]
        request.scope["headers"] = stripped + list(additional.items())

        return await call_next(request)


# ---------------------------------------------------------------------------
# Actions with role-based per-action policies
# ---------------------------------------------------------------------------


@action(
    name="get_invoice",
    description="Fetch an invoice. Allowed for any authenticated user.",
    risk="low",
    policy=RoleBasedPolicy(allowed_roles=["viewer", "finance", "admin"]),
)
def get_invoice(invoice_id: str, ctx: RequestContext):
    return {
        "invoice_id": invoice_id,
        "status": "open",
        "amount": 299.00,
        "actor": ctx.actor_id,
        "tenant": ctx.tenant_id,
        "roles": ctx.roles,
    }


@action(
    name="issue_refund",
    description="Issue a refund. Requires the 'finance' or 'admin' role.",
    risk="medium",
    policy=RoleBasedPolicy(allowed_roles=["finance", "admin"]),
)
def issue_refund(invoice_id: str, amount: float, ctx: RequestContext):
    return {
        "invoice_id": invoice_id,
        "refund_amount": amount,
        "status": "refunded",
        "processed_by": ctx.actor_id,
    }


@action(
    name="delete_invoice",
    description="Permanently delete an invoice. Admin only.",
    risk="high",
    approval_required=True,
    policy=RoleBasedPolicy(allowed_roles=["admin"]),
)
def delete_invoice(invoice_id: str, ctx: RequestContext):
    return {
        "invoice_id": invoice_id,
        "status": "deleted",
        "deleted_by": ctx.actor_id,
    }


# ---------------------------------------------------------------------------
# Wire everything together
# ---------------------------------------------------------------------------

app = AgentActionApp(db_url="sqlite:///./auth_example.db")
app.register(get_invoice)
app.register(issue_refund)
app.register(delete_invoice)

fastapi_app = app.fastapi_app()

# Add the auth middleware — it runs before every request
fastapi_app.add_middleware(AuthMiddleware)

mcp = app.mcp_server()
