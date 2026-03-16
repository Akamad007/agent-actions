"""Support operations example.

Shows:
  1. Required scopes for normal agent-driven support actions
  2. Approval for risky account operations
  3. Tenant-aware responses and audit-friendly context usage

Run:
    uvicorn examples.support_app:fastapi_app --reload

Try it:
    curl -s -X POST http://localhost:8000/actions/get_ticket/execute \
         -H "Content-Type: application/json" \
         -H "X-Actor-Id: support-alice" \
         -H "X-Roles: support,scope:support" \
         -H "X-Tenant-Id: acme" \
         -d '{"inputs": {"ticket_id": "T-100"}}' | jq

    curl -s -X POST http://localhost:8000/actions/issue_service_credit/execute \
         -H "Content-Type: application/json" \
         -H "X-Actor-Id: support-alice" \
         -H "X-Roles: support,scope:support" \
         -H "X-Tenant-Id: acme" \
         -d '{"inputs": {"account_id": "A-22", "amount": 25.0, "reason": "late delivery"}, "idempotency_key": "credit-A-22-25"}' | jq

    curl -s -X POST http://localhost:8000/actions/suspend_account/execute \
         -H "Content-Type: application/json" \
         -H "X-Actor-Id: support-alice" \
         -H "X-Roles: support,scope:support" \
         -H "X-Tenant-Id: acme" \
         -d '{"inputs": {"account_id": "A-22", "reason": "fraud review"}}' | jq
"""

from agent_actions import AgentActionApp, RequestContext, action
from agent_actions.policies import RoleBasedPolicy


@action(
    name="get_ticket",
    description="Fetch a support ticket for triage.",
    risk="low",
    required_scopes=["support"],
)
def get_ticket(ticket_id: str, ctx: RequestContext):
    return {
        "ticket_id": ticket_id,
        "status": "open",
        "priority": "high",
        "tenant_id": ctx.tenant_id,
        "viewer": ctx.actor_id,
    }


@action(
    name="issue_service_credit",
    description="Issue a small service credit to a customer account.",
    risk="medium",
    required_scopes=["support"],
    policy=RoleBasedPolicy(allowed_roles=["support", "support-lead", "admin"]),
)
def issue_service_credit(
    account_id: str,
    amount: float,
    reason: str,
    ctx: RequestContext,
):
    return {
        "account_id": account_id,
        "credited_amount": amount,
        "reason": reason,
        "tenant_id": ctx.tenant_id,
        "processed_by": ctx.actor_id,
        "status": "credited",
    }


@action(
    name="suspend_account",
    description="Suspend a customer account pending investigation.",
    risk="high",
    approval_required=True,
    required_scopes=["support"],
    policy=RoleBasedPolicy(allowed_roles=["support-lead", "admin"]),
)
def suspend_account(account_id: str, reason: str, ctx: RequestContext):
    return {
        "account_id": account_id,
        "reason": reason,
        "status": "suspended",
        "tenant_id": ctx.tenant_id,
        "requested_by": ctx.actor_id,
    }


app = AgentActionApp()
app.register(get_ticket)
app.register(issue_service_credit)
app.register(suspend_account)

fastapi_app = app.fastapi_app()
mcp = app.mcp_server(
    mcp_actor_id="support-agent",
    mcp_roles=["support", "scope:support"],
)
