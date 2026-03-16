"""Tests for idempotency behaviour."""

from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

from agent_actions.decorators import action
from agent_actions.policies import DefaultPolicy, PolicyEngine
from agent_actions.runtime import ActionRuntime


def setup_runtime(registry, session_factory):
    from agent_actions.approvals import ApprovalService
    from agent_actions.audit import AuditLogger
    from agent_actions.idempotency import IdempotencyService

    return ActionRuntime(
        registry=registry,
        policy_engine=PolicyEngine(DefaultPolicy()),
        audit_logger=AuditLogger(session_factory),
        idempotency_service=IdempotencyService(session_factory),
        approval_service=ApprovalService(session_factory),
    )


class TestIdempotency:
    def test_second_call_with_same_key_returns_cached(self, registry, session_factory):
        call_count = {"n": 0}

        @action(name="refund", description="Issue refund", risk="low")
        def issue_refund(invoice_id: str, amount: float):
            call_count["n"] += 1
            return {"invoice_id": invoice_id, "amount": amount, "call": call_count["n"]}

        registry.register(issue_refund._action_def)
        runtime = setup_runtime(registry, session_factory)

        # First call
        r1 = runtime.invoke(
            action_name="refund",
            raw_inputs={"invoice_id": "INV-1", "amount": 50.0},
            headers={"x-actor-id": "alice"},
            idempotency_key="refund-INV-1",
        )
        assert r1.status == "success"
        assert call_count["n"] == 1

        # Second call — same key
        r2 = runtime.invoke(
            action_name="refund",
            raw_inputs={"invoice_id": "INV-1", "amount": 50.0},
            headers={"x-actor-id": "alice"},
            idempotency_key="refund-INV-1",
        )
        assert r2.status == "duplicate"
        assert r2.result == r1.result
        assert call_count["n"] == 1  # function not called again

    def test_different_keys_execute_independently(self, registry, session_factory):
        call_count = {"n": 0}

        @action(name="send_email", description="Send email", risk="low")
        def send_email(to: str):
            call_count["n"] += 1
            return {"to": to, "call": call_count["n"]}

        registry.register(send_email._action_def)
        runtime = setup_runtime(registry, session_factory)

        r1 = runtime.invoke(
            action_name="send_email",
            raw_inputs={"to": "a@example.com"},
            headers={"x-actor-id": "system"},
            idempotency_key="email-1",
        )
        r2 = runtime.invoke(
            action_name="send_email",
            raw_inputs={"to": "b@example.com"},
            headers={"x-actor-id": "system"},
            idempotency_key="email-2",
        )

        assert r1.status == "success"
        assert r2.status == "success"
        assert call_count["n"] == 2

    def test_no_idempotency_key_always_executes(self, registry, session_factory):
        call_count = {"n": 0}

        @action(name="no_key_action", description="No key", risk="low")
        def no_key_action(x: str):
            call_count["n"] += 1
            return {"x": x}

        registry.register(no_key_action._action_def)
        runtime = setup_runtime(registry, session_factory)

        for _ in range(3):
            runtime.invoke(
                action_name="no_key_action",
                raw_inputs={"x": "hello"},
                headers={"x-actor-id": "alice"},
            )

        assert call_count["n"] == 3

    def test_same_key_different_actions_execute_independently(self, registry, session_factory):
        calls = {"a": 0, "b": 0}

        @action(name="action_a", description="Action A", risk="low")
        def action_a(v: str):
            calls["a"] += 1
            return {"action": "a"}

        @action(name="action_b", description="Action B", risk="low")
        def action_b(v: str):
            calls["b"] += 1
            return {"action": "b"}

        registry.register(action_a._action_def)
        registry.register(action_b._action_def)
        runtime = setup_runtime(registry, session_factory)

        runtime.invoke(
            action_name="action_a",
            raw_inputs={"v": "x"},
            headers={"x-actor-id": "alice"},
            idempotency_key="shared-key",
        )
        runtime.invoke(
            action_name="action_b",
            raw_inputs={"v": "x"},
            headers={"x-actor-id": "alice"},
            idempotency_key="shared-key",
        )

        assert calls["a"] == 1
        assert calls["b"] == 1  # different action_name namespace

    def test_concurrent_same_key_executes_only_once(self, registry, session_factory):
        call_count = {"n": 0}
        entered_execution = threading.Event()
        release_execution = threading.Event()

        @action(name="race_refund", description="Issue refund", risk="low")
        def race_refund(invoice_id: str):
            entered_execution.set()
            release_execution.wait(timeout=1)
            call_count["n"] += 1
            return {"invoice_id": invoice_id, "call": call_count["n"]}

        registry.register(race_refund._action_def)
        runtime = setup_runtime(registry, session_factory)

        def invoke():
            return runtime.invoke(
                action_name="race_refund",
                raw_inputs={"invoice_id": "INV-9"},
                headers={"x-actor-id": "alice", "x-tenant-id": "tenant-1"},
                idempotency_key="refund-INV-9",
            )

        with ThreadPoolExecutor(max_workers=2) as executor:
            future_1 = executor.submit(invoke)
            assert entered_execution.wait(timeout=1)
            future_2 = executor.submit(invoke)
            release_execution.set()
            r1 = future_1.result()
            r2 = future_2.result()

        assert sorted([r1.status, r2.status]) == ["duplicate", "success"]
        assert r1.result == r2.result
        assert call_count["n"] == 1
