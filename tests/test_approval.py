"""Tests for the approval workflow end-to-end."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from agent_actions.approvals import ApprovalAlreadyResolved, ApprovalNotFound
from agent_actions.context import RequestContext
from agent_actions.decorators import action
from agent_actions.policies import DefaultPolicy, PolicyEngine
from agent_actions.runtime import ActionRuntime


def setup_runtime_with_action(registry, session_factory, action_fn):
    from agent_actions.approvals import ApprovalService
    from agent_actions.audit import AuditLogger
    from agent_actions.idempotency import IdempotencyService

    registry.register(action_fn._action_def)
    return ActionRuntime(
        registry=registry,
        policy_engine=PolicyEngine(DefaultPolicy()),
        audit_logger=AuditLogger(session_factory),
        idempotency_service=IdempotencyService(session_factory),
        approval_service=ApprovalService(session_factory),
    )


class TestApprovalWorkflow:
    def test_approval_required_action_returns_pending(self, registry, session_factory):
        @action(
            name="approve_invoice",
            description="Approve an invoice",
            risk="high",
            approval_required=True,
        )
        def approve_invoice(invoice_id: str):
            return {"invoice_id": invoice_id, "status": "approved"}

        runtime = setup_runtime_with_action(registry, session_factory, approve_invoice)
        result = runtime.invoke(
            action_name="approve_invoice",
            raw_inputs={"invoice_id": "INV-1"},
            headers={"x-actor-id": "alice"},
        )

        assert result.status == "approval_required"
        assert result.approval_id is not None

    def test_approved_action_executes_and_returns_result(self, registry, session_factory):
        call_count = {"n": 0}

        @action(
            name="risky_op",
            description="Risky operation",
            risk="high",
            approval_required=True,
        )
        def risky_op(payload: str):
            call_count["n"] += 1
            return {"payload": payload, "done": True}

        runtime = setup_runtime_with_action(registry, session_factory, risky_op)

        # First call: returns approval_required
        result = runtime.invoke(
            action_name="risky_op",
            raw_inputs={"payload": "test"},
            headers={"x-actor-id": "alice"},
        )
        assert result.status == "approval_required"
        approval_id = result.approval_id

        # Function has not been called yet
        assert call_count["n"] == 0

        # Approve and execute
        approved_result = runtime.invoke_approved(approval_id)
        assert approved_result.status == "success"
        assert approved_result.result["done"] is True
        assert call_count["n"] == 1

    def test_rejected_action_returns_denied(self, registry, session_factory):
        @action(
            name="reject_test",
            description="Will be rejected",
            risk="high",
            approval_required=True,
        )
        def reject_test(x: str):
            return {"x": x}

        runtime = setup_runtime_with_action(registry, session_factory, reject_test)

        result = runtime.invoke(
            action_name="reject_test",
            raw_inputs={"x": "hello"},
            headers={"x-actor-id": "alice"},
        )
        approval_id = result.approval_id

        rejected = runtime.invoke_rejected(approval_id)
        assert rejected.status == "denied"

    def test_double_approve_raises(self, registry, session_factory):
        @action(
            name="double_approve",
            description="Test double approve",
            risk="high",
            approval_required=True,
        )
        def double_approve(x: str):
            return {"x": x}

        runtime = setup_runtime_with_action(registry, session_factory, double_approve)

        result = runtime.invoke(
            action_name="double_approve",
            raw_inputs={"x": "test"},
            headers={"x-actor-id": "alice"},
        )
        approval_id = result.approval_id

        runtime.invoke_approved(approval_id)

        from agent_actions.approvals import ApprovalAlreadyResolved

        with pytest.raises(ApprovalAlreadyResolved):
            runtime.invoke_approved(approval_id)

    def test_approve_nonexistent_raises(self, registry, session_factory, runtime):
        with pytest.raises(ApprovalNotFound):
            runtime.invoke_approved("00000000-0000-0000-0000-000000000000")

    def test_concurrent_approve_executes_only_once(self, registry, session_factory):
        call_count = {"n": 0}
        entered_execution = threading.Event()
        release_execution = threading.Event()

        @action(
            name="approve_race",
            description="Concurrent approval race",
            risk="high",
            approval_required=True,
        )
        def approve_race(x: str):
            entered_execution.set()
            release_execution.wait(timeout=1)
            call_count["n"] += 1
            return {"x": x, "call": call_count["n"]}

        runtime = setup_runtime_with_action(registry, session_factory, approve_race)
        pending = runtime.invoke(
            action_name="approve_race",
            raw_inputs={"x": "test"},
            headers={"x-actor-id": "alice"},
        )
        approval_id = pending.approval_id

        def approve_once():
            try:
                return runtime.invoke_approved(approval_id, approver_id="manager")
            except ApprovalAlreadyResolved:
                return "already_resolved"

        with ThreadPoolExecutor(max_workers=2) as executor:
            future_1 = executor.submit(approve_once)
            assert entered_execution.wait(timeout=1)
            future_2 = executor.submit(approve_once)
            release_execution.set()
            result_1 = future_1.result()
            result_2 = future_2.result()

        outcomes = sorted(
            ["already_resolved" if item == "already_resolved" else item.status for item in [result_1, result_2]]
        )
        assert outcomes == ["already_resolved", "success"]
        assert call_count["n"] == 1

    def test_concurrent_approve_and_reject_only_one_terminal_state_wins(
        self, registry, session_factory
    ):
        call_count = {"n": 0}
        entered_execution = threading.Event()
        release_execution = threading.Event()

        @action(
            name="approve_reject_race",
            description="Concurrent approval and rejection race",
            risk="high",
            approval_required=True,
        )
        def approve_reject_race(x: str):
            entered_execution.set()
            release_execution.wait(timeout=1)
            call_count["n"] += 1
            return {"x": x}

        runtime = setup_runtime_with_action(registry, session_factory, approve_reject_race)
        pending = runtime.invoke(
            action_name="approve_reject_race",
            raw_inputs={"x": "test"},
            headers={"x-actor-id": "alice"},
        )
        approval_id = pending.approval_id

        def approve_once():
            try:
                return runtime.invoke_approved(approval_id, approver_id="manager")
            except ApprovalAlreadyResolved:
                return "already_resolved"

        def reject_once():
            try:
                return runtime.invoke_rejected(approval_id, approver_id="manager")
            except ApprovalAlreadyResolved:
                return "already_resolved"

        with ThreadPoolExecutor(max_workers=2) as executor:
            future_1 = executor.submit(approve_once)
            assert entered_execution.wait(timeout=1)
            future_2 = executor.submit(reject_once)
            release_execution.set()
            result_1 = future_1.result()
            result_2 = future_2.result()

        terminal_results = [item for item in [result_1, result_2] if item != "already_resolved"]
        assert len(terminal_results) == 1
        assert terminal_results[0].status == "success"
        assert result_1 == "already_resolved" or result_2 == "already_resolved"
        assert call_count["n"] == 1


class TestApprovalHTTPEndpoints:
    def test_full_approval_flow_via_http(self, app_client):
        from agent_actions import action as action_decorator
        from agent_actions.context import RequestContext

        app, client = app_client

        @action_decorator(
            name="http_approval_test",
            description="HTTP approval test",
            risk="high",
            approval_required=True,
        )
        def http_approval_test(amount: float, ctx: RequestContext):
            return {"amount": amount, "actor": ctx.actor_id}

        app.register(http_approval_test)

        # Invoke — expect approval_required
        resp = client.post(
            "/actions/http_approval_test/execute",
            json={"inputs": {"amount": 99.99}},
            headers={"X-Actor-Id": "bob"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "approval_required"
        approval_id = data["approval_id"]

        # List pending approvals
        resp2 = client.get("/approvals?status=pending")
        assert resp2.status_code == 200
        pending = resp2.json()
        assert any(a["id"] == approval_id for a in pending)

        # Approve
        resp3 = client.post(f"/approvals/{approval_id}/approve")
        assert resp3.status_code == 200
        final = resp3.json()
        assert final["status"] == "success"
        assert final["result"]["amount"] == 99.99
