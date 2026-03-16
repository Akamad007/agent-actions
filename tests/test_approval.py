"""Tests for the approval workflow end-to-end."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from django_agent_actions.approvals import ApprovalAlreadyResolved, ApprovalNotFound
from django_agent_actions.context import RequestContext
from django_agent_actions.decorators import action
from django_agent_actions.policies import DefaultPolicy, PolicyEngine
from django_agent_actions.runtime import ActionRuntime
from django_agent_actions.approvals import ApprovalService
from django_agent_actions.audit import AuditLogger
from django_agent_actions.idempotency import IdempotencyService


def setup_runtime_with_action(registry, action_fn):
    registry.register(action_fn._action_def)
    return ActionRuntime(
        registry=registry,
        policy_engine=PolicyEngine(DefaultPolicy()),
        audit_logger=AuditLogger(),
        idempotency_service=IdempotencyService(),
        approval_service=ApprovalService(),
    )


@pytest.mark.django_db(transaction=True)
class TestApprovalWorkflow:
    def test_approval_required_action_returns_pending(self, registry):
        @action(
            name="approve_invoice",
            description="Approve an invoice",
            risk="high",
            approval_required=True,
        )
        def approve_invoice(invoice_id: str):
            return {"invoice_id": invoice_id, "status": "approved"}

        runtime = setup_runtime_with_action(registry, approve_invoice)
        result = runtime.invoke(
            action_name="approve_invoice",
            raw_inputs={"invoice_id": "INV-1"},
            headers={"x-actor-id": "alice"},
        )

        assert result.status == "approval_required"
        assert result.approval_id is not None

    def test_approved_action_executes_and_returns_result(self, registry):
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

        runtime = setup_runtime_with_action(registry, risky_op)

        result = runtime.invoke(
            action_name="risky_op",
            raw_inputs={"payload": "test"},
            headers={"x-actor-id": "alice"},
        )
        assert result.status == "approval_required"
        approval_id = result.approval_id

        assert call_count["n"] == 0

        approved_result = runtime.invoke_approved(approval_id)
        assert approved_result.status == "success"
        assert approved_result.result["done"] is True
        assert call_count["n"] == 1

    def test_rejected_action_returns_denied(self, registry):
        @action(
            name="reject_test",
            description="Will be rejected",
            risk="high",
            approval_required=True,
        )
        def reject_test(x: str):
            return {"x": x}

        runtime = setup_runtime_with_action(registry, reject_test)

        result = runtime.invoke(
            action_name="reject_test",
            raw_inputs={"x": "hello"},
            headers={"x-actor-id": "alice"},
        )
        approval_id = result.approval_id

        rejected = runtime.invoke_rejected(approval_id)
        assert rejected.status == "denied"

    def test_double_approve_raises(self, registry):
        @action(
            name="double_approve",
            description="Test double approve",
            risk="high",
            approval_required=True,
        )
        def double_approve(x: str):
            return {"x": x}

        runtime = setup_runtime_with_action(registry, double_approve)

        result = runtime.invoke(
            action_name="double_approve",
            raw_inputs={"x": "test"},
            headers={"x-actor-id": "alice"},
        )
        approval_id = result.approval_id

        runtime.invoke_approved(approval_id)

        with pytest.raises(ApprovalAlreadyResolved):
            runtime.invoke_approved(approval_id)

    def test_approve_nonexistent_raises(self, runtime):
        with pytest.raises(ApprovalNotFound):
            runtime.invoke_approved("00000000-0000-0000-0000-000000000000")

    def test_concurrent_approve_executes_only_once(self, registry):
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

        runtime = setup_runtime_with_action(registry, approve_race)
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
            [
                "already_resolved" if item == "already_resolved" else item.status
                for item in [result_1, result_2]
            ]
        )
        assert outcomes == ["already_resolved", "success"]
        assert call_count["n"] == 1

    def test_concurrent_approve_and_reject_only_one_terminal_state_wins(self, registry):
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

        runtime = setup_runtime_with_action(registry, approve_reject_race)
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
