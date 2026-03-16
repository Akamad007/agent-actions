"""Advanced FastAPI integration patterns.

Covers:
  1. FastAPI lifespan (startup/shutdown) instead of global singletons
  2. Dependency injection — expose RequestContext as a FastAPI dependency
  3. Custom response envelope middleware
  4. Mounting the action API as a sub-application under a prefix
  5. Per-tenant app routing (multiple AgentActionApp instances)
  6. Calling actions directly from your own FastAPI routes (not via /execute)

Run:
    uvicorn examples.fastapi_advanced:app --reload

Try it:
    # Via the mounted sub-app prefix
    curl -s -X POST http://localhost:8000/agent/actions/get_order/execute \
         -H "Content-Type: application/json" \
         -H "X-Actor-Id: alice" \
         -H "X-Roles: support" \
         -d '{"inputs": {"order_id": "ORD-42"}}' | jq

    # Via your own custom route (bypasses /execute, calls runtime directly)
    curl -s -X GET "http://localhost:8000/orders/ORD-42?actor=alice" | jq

    # Tenant A actions
    curl -s http://localhost:8000/tenants/acme/actions | jq

    # Tenant B actions
    curl -s http://localhost:8000/tenants/beta/actions | jq
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request

from agent_actions import AgentActionApp, RequestContext, action
from agent_actions.policies import RiskBasedPolicy, RoleBasedPolicy
from agent_actions.runtime import InvokeResult

# ---------------------------------------------------------------------------
# 1. Define actions
# ---------------------------------------------------------------------------


@action(
    name="get_order",
    description="Fetch an order by ID.",
    risk="low",
    policy=RoleBasedPolicy(allowed_roles=["support", "admin", "finance"]),
)
def get_order(order_id: str, ctx: RequestContext):
    return {"order_id": order_id, "status": "shipped", "actor": ctx.actor_id}


@action(
    name="cancel_order",
    description="Cancel an in-flight order. Finance or admin only.",
    risk="medium",
    policy=RoleBasedPolicy(allowed_roles=["finance", "admin"]),
)
def cancel_order(order_id: str, reason: str, ctx: RequestContext):
    return {"order_id": order_id, "status": "cancelled", "reason": reason}


@action(
    name="refund_order",
    description="Refund an order. High-risk — uses risk-based approval.",
    risk="high",
    policy=RiskBasedPolicy(),
)
def refund_order(order_id: str, amount: float, ctx: RequestContext):
    return {"order_id": order_id, "refund": amount, "status": "refunded"}


# ---------------------------------------------------------------------------
# 2. Per-tenant action apps (tenant isolation pattern)
# ---------------------------------------------------------------------------

TENANT_ACTIONS: dict[str, list] = {
    "acme": [get_order, cancel_order, refund_order],
    "beta": [get_order],          # beta tenant only gets read access
}

_tenant_apps: dict[str, AgentActionApp] = {}


def build_tenant_app(tenant_id: str) -> AgentActionApp:
    tenant_app = AgentActionApp(db_url=f"sqlite:///./tenant_{tenant_id}.db")
    for fn in TENANT_ACTIONS.get(tenant_id, []):
        tenant_app.register(fn)
    return tenant_app


# ---------------------------------------------------------------------------
# 3. Main app wired with lifespan
# ---------------------------------------------------------------------------

_main_agent_app: AgentActionApp | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize resources at startup; clean up at shutdown."""
    global _main_agent_app, _tenant_apps

    # Main action app
    _main_agent_app = AgentActionApp(db_url="sqlite:///./advanced_example.db")
    _main_agent_app.register(get_order)
    _main_agent_app.register(cancel_order)
    _main_agent_app.register(refund_order)

    # Per-tenant apps
    for tenant_id in TENANT_ACTIONS:
        _tenant_apps[tenant_id] = build_tenant_app(tenant_id)

    # Mount the agent-actions HTTP sub-app under /agent
    agent_sub_app = _main_agent_app.fastapi_app()
    app.mount("/agent", agent_sub_app)

    yield
    # Shutdown: nothing to close for SQLite, but DB connections / pools go here


app = FastAPI(title="advanced agent-actions", lifespan=lifespan)


# ---------------------------------------------------------------------------
# 4. FastAPI dependency — RequestContext from request headers
#    Useful when you want ctx in your own route handlers
# ---------------------------------------------------------------------------


def get_ctx(request: Request) -> RequestContext:
    return RequestContext.from_headers(dict(request.headers))


ContextDep = Annotated[RequestContext, Depends(get_ctx)]


# ---------------------------------------------------------------------------
# 5. Custom route that calls the runtime directly (no /execute needed)
# ---------------------------------------------------------------------------


@app.get("/orders/{order_id}")
def get_order_route(
    order_id: str,
    ctx: ContextDep,
    actor: str = Query(default="anonymous"),
):
    """Your own route that invokes the action runtime directly.

    This is useful when you want to expose a clean REST API alongside the
    generic /actions/{name}/execute endpoint.
    """
    if _main_agent_app is None:
        raise HTTPException(status_code=503, detail="App not initialised")

    # Override actor from query param for demo purposes
    import dataclasses
    ctx = dataclasses.replace(ctx, actor_id=actor)

    result: InvokeResult = _main_agent_app.runtime.invoke(
        action_name="get_order",
        raw_inputs={"order_id": order_id},
        headers={"x-actor-id": ctx.actor_id, "x-roles": ",".join(ctx.roles)},
    )
    if result.status == "denied":
        raise HTTPException(status_code=403, detail="Access denied.")
    return result.result


# ---------------------------------------------------------------------------
# 6. Per-tenant routing
# ---------------------------------------------------------------------------


@app.get("/tenants/{tenant_id}/actions")
def list_tenant_actions(tenant_id: str):
    tenant_app = _tenant_apps.get(tenant_id)
    if tenant_app is None:
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found.")
    return [a.to_schema_dict() for a in tenant_app.registry.list()]


@app.post("/tenants/{tenant_id}/actions/{action_name}/execute")
def execute_tenant_action(
    tenant_id: str,
    action_name: str,
    body: dict[str, Any],
    ctx: ContextDep,
):
    tenant_app = _tenant_apps.get(tenant_id)
    if tenant_app is None:
        raise HTTPException(status_code=404, detail=f"Tenant '{tenant_id}' not found.")

    try:
        result = tenant_app.runtime.invoke(
            action_name=action_name,
            raw_inputs=body.get("inputs", {}),
            headers={
                "x-actor-id": ctx.actor_id,
                "x-roles": ",".join(ctx.roles),
                "x-tenant-id": tenant_id,
            },
            idempotency_key=body.get("idempotency_key"),
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    return result


# ---------------------------------------------------------------------------
# 7. Response envelope middleware (wrap every response in {data: ..., ok: bool})
# ---------------------------------------------------------------------------


class EnvelopeMiddleware:
    """Optionally wrap JSON responses in a standard envelope.

    Enable by passing ?envelope=true to any request.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        from starlette.requests import Request as StarletteRequest
        from starlette.responses import Response

        request = StarletteRequest(scope, receive)
        if request.query_params.get("envelope") != "true":
            await self.app(scope, receive, send)
            return

        # Buffer the response
        body_parts: list[bytes] = []
        status_code = 200
        headers_list: list[tuple[bytes, bytes]] = []

        async def capture_send(message):
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers_list.extend(message.get("headers", []))
            elif message["type"] == "http.response.body":
                body_parts.append(message.get("body", b""))

        await self.app(scope, receive, capture_send)

        import json
        raw = b"".join(body_parts)
        try:
            data = json.loads(raw)
        except Exception:
            data = raw.decode()

        envelope = json.dumps({"ok": status_code < 400, "data": data}).encode()
        response = Response(
            content=envelope,
            status_code=status_code,
            media_type="application/json",
        )
        await response(scope, receive, send)


app.add_middleware(EnvelopeMiddleware)
