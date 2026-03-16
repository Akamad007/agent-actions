"""
Billing actions — decorated with @action and registered into the global registry.

Import this module from AppConfig.ready() so actions are registered once at
startup (see apps.py).
"""

from __future__ import annotations

from django_agent_actions import action, registry
from django_agent_actions.context import RequestContext

# ---------------------------------------------------------------------------
# Fake in-memory data store
# ---------------------------------------------------------------------------

INVOICES: dict[str, dict] = {
    "INV-001": {"id": "INV-001", "amount": 500.00, "status": "open", "customer": "Acme Corp"},
    "INV-002": {"id": "INV-002", "amount": 1200.00, "status": "open", "customer": "Globex"},
    "INV-003": {"id": "INV-003", "amount": 75.50, "status": "paid", "customer": "Initech"},
}


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


@action(
    name="get_invoice",
    description="Retrieve an invoice by ID.",
    risk="low",
    tags=["billing", "read"],
)
def get_invoice(invoice_id: str, ctx: RequestContext):
    invoice = INVOICES.get(invoice_id)
    if invoice is None:
        raise KeyError(f"Invoice '{invoice_id}' not found.")
    return {"invoice": invoice, "requested_by": ctx.actor_id}


@action(
    name="list_invoices",
    description="List all invoices, optionally filtered by status.",
    risk="low",
    tags=["billing", "read"],
)
def list_invoices(status: str = ""):
    results = list(INVOICES.values())
    if status:
        results = [inv for inv in results if inv["status"] == status]
    return {"invoices": results, "count": len(results)}


@action(
    name="mark_invoice_paid",
    description="Mark an open invoice as paid.",
    risk="medium",
    tags=["billing", "write"],
    required_scopes=["finance"],
)
def mark_invoice_paid(invoice_id: str, ctx: RequestContext):
    invoice = INVOICES.get(invoice_id)
    if invoice is None:
        raise KeyError(f"Invoice '{invoice_id}' not found.")
    if invoice["status"] == "paid":
        return {"invoice_id": invoice_id, "message": "Already paid."}
    INVOICES[invoice_id]["status"] = "paid"
    return {"invoice_id": invoice_id, "status": "paid", "marked_by": ctx.actor_id}


@action(
    name="issue_refund",
    description="Issue a full refund for a paid invoice. Requires approval.",
    risk="high",
    approval_required=True,
    tags=["billing", "write"],
    required_scopes=["finance"],
)
def issue_refund(invoice_id: str, reason: str, ctx: RequestContext):
    invoice = INVOICES.get(invoice_id)
    if invoice is None:
        raise KeyError(f"Invoice '{invoice_id}' not found.")
    INVOICES[invoice_id]["status"] = "refunded"
    return {
        "invoice_id": invoice_id,
        "refunded_amount": invoice["amount"],
        "reason": reason,
        "processed_by": ctx.actor_id,
    }


# ---------------------------------------------------------------------------
# Register all actions
# ---------------------------------------------------------------------------

registry.register(get_invoice)
registry.register(list_invoices)
registry.register(mark_invoice_paid)
registry.register(issue_refund)
