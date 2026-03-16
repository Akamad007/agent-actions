"""Embedded runtime example for existing apps, workers, or queues.

This pattern is useful when you do not want to expose the generic HTTP API and
instead want to call the action runtime directly from your own application
code, message consumer, or job runner.

Run:
    python -m examples.embedded_runtime
"""

from __future__ import annotations

from agent_actions import AgentActionApp, RequestContext, action
from agent_actions.policies import RoleBasedPolicy


@action(
    name="create_vendor_payout",
    description="Create a payout request for a marketplace vendor.",
    risk="high",
    approval_required=True,
    required_scopes=["finance"],
    policy=RoleBasedPolicy(allowed_roles=["finance", "finance-manager"]),
)
def create_vendor_payout(
    vendor_id: str,
    amount: float,
    currency: str,
    ctx: RequestContext,
):
    return {
        "vendor_id": vendor_id,
        "amount": amount,
        "currency": currency,
        "requested_by": ctx.actor_id,
        "tenant_id": ctx.tenant_id,
        "status": "queued_for_payout",
    }


@action(
    name="get_vendor_balance",
    description="Read a vendor's available balance.",
    risk="low",
    required_scopes=["finance"],
)
def get_vendor_balance(vendor_id: str, ctx: RequestContext):
    return {
        "vendor_id": vendor_id,
        "available_balance": 1320.55,
        "currency": "USD",
        "tenant_id": ctx.tenant_id,
        "viewer": ctx.actor_id,
    }


app = AgentActionApp()
app.register(create_vendor_payout)
app.register(get_vendor_balance)


def handle_job(event: dict) -> dict:
    """Example queue/worker entry point."""
    headers = {
        "x-actor-id": event["actor_id"],
        "x-roles": ",".join(event["roles"]),
        "x-tenant-id": event["tenant_id"],
    }
    result = app.runtime.invoke(
        action_name=event["action_name"],
        raw_inputs=event["inputs"],
        headers=headers,
        idempotency_key=event.get("idempotency_key"),
    )
    return result.model_dump()


if __name__ == "__main__":
    read_event = {
        "action_name": "get_vendor_balance",
        "inputs": {"vendor_id": "vendor-7"},
        "actor_id": "finance-bot",
        "roles": ["finance", "scope:finance"],
        "tenant_id": "marketplace-us",
    }
    print(handle_job(read_event))

    payout_event = {
        "action_name": "create_vendor_payout",
        "inputs": {
            "vendor_id": "vendor-7",
            "amount": 250.00,
            "currency": "USD",
        },
        "actor_id": "finance-bot",
        "roles": ["finance", "scope:finance"],
        "tenant_id": "marketplace-us",
        "idempotency_key": "payout-vendor-7-250",
    }
    print(handle_job(payout_event))
