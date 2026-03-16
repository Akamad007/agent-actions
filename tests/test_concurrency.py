"""Concurrency-focused tests for context isolation and audit correctness."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from agent_actions.approvals import ApprovalService
from agent_actions.audit import AuditLogger
from agent_actions.decorators import action
from agent_actions.idempotency import IdempotencyService
from agent_actions.policies import DefaultPolicy, PolicyEngine
from agent_actions.runtime import ActionRuntime


def setup_runtime(registry, session_factory):
    return ActionRuntime(
        registry=registry,
        policy_engine=PolicyEngine(DefaultPolicy()),
        audit_logger=AuditLogger(session_factory),
        idempotency_service=IdempotencyService(session_factory),
        approval_service=ApprovalService(session_factory),
    )


class TestConcurrencyIsolation:
    def test_request_context_does_not_leak_across_threads(self, registry, session_factory):
        @action(name="whoami", description="Echo request context", risk="low")
        def whoami(value: str, ctx):
            return {
                "value": value,
                "actor_id": ctx.actor_id,
                "tenant_id": ctx.tenant_id,
            }

        registry.register(whoami._action_def)
        runtime = setup_runtime(registry, session_factory)

        requests = [
            ("alice", "tenant-a"),
            ("bob", "tenant-b"),
            ("carol", "tenant-c"),
            ("dave", "tenant-d"),
        ]

        def invoke(actor_id: str, tenant_id: str):
            return runtime.invoke(
                action_name="whoami",
                raw_inputs={"value": actor_id},
                headers={"x-actor-id": actor_id, "x-tenant-id": tenant_id},
            )

        with ThreadPoolExecutor(max_workers=len(requests)) as executor:
            results = list(executor.map(lambda args: invoke(*args), requests))

        seen = {(result.result["actor_id"], result.result["tenant_id"]) for result in results}
        assert seen == set(requests)

    def test_audit_logs_keep_per_request_actor_and_tenant_under_concurrency(
        self, registry, session_factory
    ):
        @action(name="audit_echo", description="Audit context", risk="low")
        def audit_echo(value: str, ctx):
            return {"actor_id": ctx.actor_id, "tenant_id": ctx.tenant_id, "value": value}

        registry.register(audit_echo._action_def)
        runtime = setup_runtime(registry, session_factory)
        audit_logger = AuditLogger(session_factory)

        requests = [
            ("alice", "tenant-a"),
            ("bob", "tenant-b"),
            ("carol", "tenant-c"),
            ("dave", "tenant-d"),
        ]

        def invoke(actor_id: str, tenant_id: str):
            return runtime.invoke(
                action_name="audit_echo",
                raw_inputs={"value": actor_id},
                headers={"x-actor-id": actor_id, "x-tenant-id": tenant_id},
            )

        with ThreadPoolExecutor(max_workers=len(requests)) as executor:
            results = list(executor.map(lambda args: invoke(*args), requests))

        assert all(result.status == "success" for result in results)

        logs = audit_logger.list_logs(action_name="audit_echo", limit=20)
        observed = {(log.actor_id, log.tenant_id) for log in logs}
        assert observed == set(requests)
