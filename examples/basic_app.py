"""Basic example — three invoice actions demonstrating the core features.

Run:
    pip install -e ".[dev]"
    uvicorn examples.basic_app:fastapi_app --reload

Or from the repo root:
    uvicorn examples.basic_app:fastapi_app --reload

MCP (stdio transport):
    mcp run examples/basic_app.py

Try it:
    # Read action (always allowed)
    curl -s -X POST http://localhost:8000/actions/get_invoice/execute \
         -H "Content-Type: application/json" \
         -H "X-Actor-Id: alice" \
         -d '{"inputs": {"invoice_id": "INV-001"}}' | jq

    # High-risk action — requires approval
    curl -s -X POST http://localhost:8000/actions/approve_invoice/execute \
         -H "Content-Type: application/json" \
         -H "X-Actor-Id: alice" \
         -d '{"inputs": {"invoice_id": "INV-001", "reason": "Budget approved"}}' | jq

    # Approve it (replace <id> with approval_id from previous response)
    curl -s -X POST http://localhost:8000/approvals/<id>/approve | jq

    # Idempotent refund — second call returns cached result
    curl -s -X POST http://localhost:8000/actions/issue_refund/execute \
         -H "Content-Type: application/json" \
         -H "X-Actor-Id: alice" \
         -d '{"inputs": {"invoice_id": "INV-001", "amount": 49.99}, "idempotency_key": "refund-INV-001"}' | jq
"""

from agent_actions import AgentActionApp, RequestContext, action

# ---------------------------------------------------------------------------
# Action definitions
# ---------------------------------------------------------------------------


@action(
    name="get_invoice",
    description="Fetch a single invoice by ID. Safe read — no side effects.",
    risk="low",
    approval_required=False,
)
def get_invoice(invoice_id: str, ctx: RequestContext):
    # In a real app this would query your database.
    return {
        "invoice_id": invoice_id,
        "status": "open",
        "amount": 149.99,
        "currency": "USD",
        "actor": ctx.actor_id,
    }


@action(
    name="approve_invoice",
    description="Approve an invoice for payment. High-risk — requires human approval.",
    risk="high",
    approval_required=True,
)
def approve_invoice(invoice_id: str, reason: str, ctx: RequestContext):
    return {
        "invoice_id": invoice_id,
        "status": "approved",
        "reason": reason,
        "approved_by": ctx.actor_id,
    }


@action(
    name="issue_refund",
    description="Issue a refund for an invoice. Use an idempotency key to avoid double-refunds.",
    risk="medium",
    approval_required=False,
)
def issue_refund(invoice_id: str, amount: float, ctx: RequestContext):
    return {
        "invoice_id": invoice_id,
        "refund_amount": amount,
        "currency": "USD",
        "status": "refunded",
        "processed_by": ctx.actor_id,
    }


# ---------------------------------------------------------------------------
# Wire up the app
# ---------------------------------------------------------------------------

app = AgentActionApp()
app.register(get_invoice)
app.register(approve_invoice)
app.register(issue_refund)

# FastAPI ASGI app — used by uvicorn
fastapi_app = app.fastapi_app()

# MCP server — used by `mcp run examples/basic_app.py`
mcp = app.mcp_server()
