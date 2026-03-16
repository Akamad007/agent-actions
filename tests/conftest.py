"""Shared pytest fixtures — tests use isolated SQLite database files."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from agent_actions.approvals import ApprovalService
from agent_actions.audit import AuditLogger
from agent_actions.context import RequestContext
from agent_actions.db import create_db_engine, init_db, make_session_factory
from agent_actions.idempotency import IdempotencyService
from agent_actions.policies import DefaultPolicy, PolicyEngine
from agent_actions.registry import ActionRegistry
from agent_actions.runtime import ActionRuntime
from agent_actions.server import AgentActionApp


@pytest.fixture
def session_factory(tmp_path):
    engine = create_db_engine(f"sqlite:///{tmp_path}/test.db")
    init_db(engine)
    return make_session_factory(engine)


@pytest.fixture
def registry():
    return ActionRegistry()


@pytest.fixture
def policy_engine():
    return PolicyEngine(DefaultPolicy())


@pytest.fixture
def audit_logger(session_factory):
    return AuditLogger(session_factory)


@pytest.fixture
def idempotency_service(session_factory):
    return IdempotencyService(session_factory)


@pytest.fixture
def approval_service(session_factory):
    return ApprovalService(session_factory)


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


@pytest.fixture
def app_client(tmp_path):
    """Full AgentActionApp wired to a temp SQLite file, returns a TestClient."""
    db_url = f"sqlite:///{tmp_path}/test.db"
    app = AgentActionApp(db_url=db_url)
    return app, TestClient(app.fastapi_app())
