"""Shared pytest fixtures for the Django-backed test suite."""

from __future__ import annotations

import pytest

from django_agent_actions.approvals import ApprovalService
from django_agent_actions.audit import AuditLogger
from django_agent_actions.context import RequestContext
from django_agent_actions.idempotency import IdempotencyService
from django_agent_actions.policies import DefaultPolicy, PolicyEngine
from django_agent_actions.registry import ActionRegistry
from django_agent_actions.runtime import ActionRuntime


@pytest.fixture
def registry():
    return ActionRegistry()


@pytest.fixture
def policy_engine():
    return PolicyEngine(DefaultPolicy())


@pytest.fixture
def audit_logger():
    return AuditLogger()


@pytest.fixture
def idempotency_service():
    return IdempotencyService()


@pytest.fixture
def approval_service():
    return ApprovalService()


@pytest.fixture
def runtime(registry, policy_engine, audit_logger, idempotency_service, approval_service):
    return ActionRuntime(
        registry=registry,
        policy_engine=policy_engine,
        audit_logger=audit_logger,
        idempotency_service=idempotency_service,
        approval_service=approval_service,
    )


@pytest.fixture
def default_headers():
    return {"x-actor-id": "test-user", "x-roles": "user", "x-tenant-id": "tenant-1"}


@pytest.fixture
def ctx():
    return RequestContext(actor_id="test-user", roles=["user"], tenant_id="tenant-1")
