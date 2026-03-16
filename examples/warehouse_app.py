"""Warehouse and operations example.

Shows:
  1. Inventory reads
  2. Idempotent reservation calls for agent retries
  3. High-risk stock adjustments that require approval

Run:
    uvicorn examples.warehouse_app:fastapi_app --reload

Try it:
    curl -s -X POST http://localhost:8000/actions/get_stock_item/execute \
         -H "Content-Type: application/json" \
         -H "X-Actor-Id: warehouse-bot" \
         -H "X-Roles: ops,inventory,scope:inventory" \
         -H "X-Tenant-Id: west-coast" \
         -d '{"inputs": {"sku": "SKU-100"}}' | jq

    curl -s -X POST http://localhost:8000/actions/reserve_inventory/execute \
         -H "Content-Type: application/json" \
         -H "X-Actor-Id: warehouse-bot" \
         -H "X-Roles: ops,inventory,scope:inventory" \
         -H "X-Tenant-Id: west-coast" \
         -d '{"inputs": {"sku": "SKU-100", "quantity": 3, "order_id": "ORD-99"}, "idempotency_key": "reserve-ORD-99"}' | jq

    curl -s -X POST http://localhost:8000/actions/adjust_inventory/execute \
         -H "Content-Type: application/json" \
         -H "X-Actor-Id: ops-manager" \
         -H "X-Roles: ops-manager,inventory-admin,scope:inventory" \
         -H "X-Tenant-Id: west-coast" \
         -d '{"inputs": {"sku": "SKU-100", "delta": -25, "reason": "damaged pallet"}}' | jq
"""

from agent_actions import AgentActionApp, RequestContext, action
from agent_actions.policies import RiskBasedPolicy, RoleBasedPolicy


@action(
    name="get_stock_item",
    description="Return current stock information for a SKU.",
    risk="low",
    required_scopes=["inventory"],
)
def get_stock_item(sku: str, ctx: RequestContext):
    return {
        "sku": sku,
        "available": 87,
        "reserved": 12,
        "warehouse": ctx.tenant_id or "default",
        "viewer": ctx.actor_id,
    }


@action(
    name="reserve_inventory",
    description="Reserve units for an order. Safe to retry with the same idempotency key.",
    risk="medium",
    required_scopes=["inventory"],
    policy=RoleBasedPolicy(allowed_roles=["ops", "inventory", "inventory-admin"]),
)
def reserve_inventory(sku: str, quantity: int, order_id: str, ctx: RequestContext):
    return {
        "sku": sku,
        "quantity": quantity,
        "order_id": order_id,
        "warehouse": ctx.tenant_id,
        "reserved_by": ctx.actor_id,
        "status": "reserved",
    }


@action(
    name="adjust_inventory",
    description="Force-adjust inventory after shrinkage, damage, or recount.",
    risk="high",
    required_scopes=["inventory"],
    policy=RiskBasedPolicy(),
)
def adjust_inventory(sku: str, delta: int, reason: str, ctx: RequestContext):
    return {
        "sku": sku,
        "delta": delta,
        "reason": reason,
        "warehouse": ctx.tenant_id,
        "requested_by": ctx.actor_id,
        "status": "pending_adjustment",
    }


app = AgentActionApp()
app.register(get_stock_item)
app.register(reserve_inventory)
app.register(adjust_inventory)

fastapi_app = app.fastapi_app()
mcp = app.mcp_server(
    mcp_actor_id="warehouse-agent",
    mcp_roles=["ops", "inventory", "scope:inventory"],
)
