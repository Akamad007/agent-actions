"""AgentActionApp — top-level entry point that wires everything together.

    app = AgentActionApp()
    app.register(get_invoice)
    app.register(approve_invoice)

    fastapi_app = app.fastapi_app()   # mount under uvicorn
    mcp_server  = app.mcp_server()    # run with `mcp run`

Database modes (see ``db.py`` for full details):

  Standalone (default):
      app = AgentActionApp()
      # Uses ./agent_actions.db (SQLite)

  Explicit URL:
      app = AgentActionApp(db_url="postgresql+psycopg2://user:pass@host/db")

  Inject host session factory (FastAPI / SQLAlchemy host mode):
      app = AgentActionApp(session_factory=my_existing_session_factory)
      # Call init_db(engine) once at startup to create framework tables.

  Django auto-detection:
      # Just run — if Django settings are configured the default DB is used.
      app = AgentActionApp()
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request
from pydantic import BaseModel

from agent_actions.approvals import ApprovalAlreadyResolved, ApprovalNotFound, ApprovalService
from agent_actions.audit import AuditLogger
from agent_actions.context import ContextResolver
from agent_actions.db import resolve_session_factory
from agent_actions.idempotency import IdempotencyService
from agent_actions.mcp import build_mcp_server
from agent_actions.models import Approval, AuditLog
from agent_actions.policies import DefaultPolicy, PolicyEngine, PolicyRule
from agent_actions.registry import ActionDef, ActionRegistry
from agent_actions.runtime import ActionRuntime, InvokeResult

# ---------------------------------------------------------------------------
# Request / response schemas for HTTP endpoints
# ---------------------------------------------------------------------------


class ExecuteRequest(BaseModel):
    inputs: dict[str, Any] = {}
    idempotency_key: str | None = None


class ApprovalResponse(BaseModel):
    id: str
    action_name: str
    actor_id: str
    tenant_id: str | None
    status: str
    approver_id: str | None
    created_at: str
    resolved_at: str | None
    result: Any | None = None

    @classmethod
    def from_orm(cls, a: Approval) -> "ApprovalResponse":
        return cls(
            id=a.id,
            action_name=a.action_name,
            actor_id=a.actor_id,
            tenant_id=a.tenant_id,
            status=a.status,
            approver_id=a.approver_id,
            created_at=a.created_at.isoformat(),
            resolved_at=a.resolved_at.isoformat() if a.resolved_at else None,
            result=a.get_result(),
        )


class AuditLogResponse(BaseModel):
    id: int
    action_name: str
    actor_id: str
    tenant_id: str | None
    # input_payload is already redacted at write time (see AuditLogger).
    input_payload: Any | None
    policy_decision: str
    status: str
    approval_id: str | None
    approver_id: str | None
    idempotency_key: str | None
    result: Any | None
    created_at: str

    @classmethod
    def from_orm(cls, log: AuditLog) -> "AuditLogResponse":
        return cls(
            id=log.id,
            action_name=log.action_name,
            actor_id=log.actor_id,
            tenant_id=log.tenant_id,
            input_payload=log.get_inputs(),
            policy_decision=log.policy_decision,
            status=log.status,
            approval_id=log.approval_id,
            approver_id=log.approver_id,
            idempotency_key=log.idempotency_key,
            result=log.get_result(),
            created_at=log.created_at.isoformat(),
        )


# ---------------------------------------------------------------------------
# AgentActionApp
# ---------------------------------------------------------------------------


class AgentActionApp:
    def __init__(
        self,
        db_url: str | None = None,
        session_factory=None,
        default_policy: PolicyRule | None = None,
        context_resolver: ContextResolver | None = None,
    ) -> None:
        """Create an AgentActionApp.

        Args:
            db_url: SQLAlchemy database URL.  If omitted, Django auto-detection
                is attempted, then ``sqlite:///./agent_actions.db`` is used.
            session_factory: Inject an existing SQLAlchemy ``sessionmaker`` from
                the host application.  When provided, ``db_url`` is ignored and
                the caller is responsible for ensuring framework tables exist
                (call ``init_db(engine)`` once at startup).
            default_policy: App-level ``PolicyRule`` applied to actions that
                have no per-action policy override.  Defaults to ``DefaultPolicy``
                (allow all).  Consider ``RoleBasedPolicy`` or ``RiskBasedPolicy``
                for production deployments.
            context_resolver: Controls how ``RequestContext`` is built from
                incoming headers.  Attach an ``AuthBackend`` to validate tokens
                inline, or leave ``None`` to trust ``X-*`` identity headers set
                by upstream middleware.
        """
        sf = resolve_session_factory(db_url=db_url, session_factory=session_factory)

        self.registry = ActionRegistry()
        policy_engine = PolicyEngine(default_policy or DefaultPolicy())
        audit_logger = AuditLogger(sf)
        idempotency_service = IdempotencyService(sf)
        approval_service = ApprovalService(sf)
        _context_resolver = context_resolver or ContextResolver()

        self.runtime = ActionRuntime(
            registry=self.registry,
            policy_engine=policy_engine,
            audit_logger=audit_logger,
            idempotency_service=idempotency_service,
            approval_service=approval_service,
            context_resolver=_context_resolver,
        )
        self._approval_service = approval_service
        self._audit_logger = audit_logger
        self._context_resolver = _context_resolver
        self._fastapi_app: FastAPI | None = None

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(self, fn_or_def: Any) -> None:
        """Accept a decorated function (with ``_action_def``) or a bare ``ActionDef``."""
        if isinstance(fn_or_def, ActionDef):
            self.registry.register(fn_or_def)
        elif hasattr(fn_or_def, "_action_def"):
            self.registry.register(fn_or_def._action_def)
        else:
            raise TypeError(
                "register() expects a function decorated with @action or an ActionDef instance."
            )

    # ------------------------------------------------------------------
    # FastAPI app
    # ------------------------------------------------------------------

    def fastapi_app(self) -> FastAPI:
        if self._fastapi_app is not None:
            return self._fastapi_app

        api = FastAPI(
            title="agent-actions",
            description="Expose safe backend actions to LLM agents",
            version="0.1.0",
        )

        runtime = self.runtime
        registry = self.registry
        approval_service = self._approval_service
        audit_logger = self._audit_logger
        context_resolver = self._context_resolver

        # ---- Health ----

        @api.get("/health", tags=["meta"])
        def health():
            return {"status": "ok"}

        # ---- Actions ----

        @api.get("/actions", tags=["actions"])
        def list_actions():
            return [a.to_schema_dict() for a in registry.list()]

        @api.post("/actions/{action_name}/execute", response_model=InvokeResult, tags=["actions"])
        def execute_action(action_name: str, body: ExecuteRequest, request: Request):
            headers = dict(request.headers)
            try:
                result = runtime.invoke(
                    action_name=action_name,
                    raw_inputs=body.inputs,
                    headers=headers,
                    idempotency_key=body.idempotency_key,
                )
            except PermissionError:
                raise HTTPException(status_code=401, detail="Authentication required.")
            except KeyError as exc:
                raise HTTPException(status_code=404, detail=str(exc))
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc))
            except Exception:
                raise HTTPException(status_code=500, detail="Action execution failed.")
            return result

        # ---- Approvals ----

        @api.get("/approvals", tags=["approvals"])
        def list_approvals(
            status: str | None = Query(default=None),
            limit: int = Query(default=50, ge=1, le=500),
            offset: int = Query(default=0, ge=0),
        ):
            items = approval_service.list(status=status, limit=limit, offset=offset)
            return [ApprovalResponse.from_orm(a) for a in items]

        @api.post(
            "/approvals/{approval_id}/approve",
            response_model=InvokeResult,
            tags=["approvals"],
        )
        def approve_action(approval_id: str, request: Request):
            # Extract approver identity from the request context.
            approver_ctx = context_resolver.resolve(dict(request.headers))
            approver_id = approver_ctx.actor_id
            try:
                result = runtime.invoke_approved(approval_id, approver_id=approver_id)
            except ApprovalNotFound as exc:
                raise HTTPException(status_code=404, detail=str(exc))
            except ApprovalAlreadyResolved as exc:
                raise HTTPException(status_code=409, detail=str(exc))
            except Exception:
                raise HTTPException(status_code=500, detail="Approval execution failed.")
            return result

        @api.post(
            "/approvals/{approval_id}/reject",
            response_model=InvokeResult,
            tags=["approvals"],
        )
        def reject_action(approval_id: str, request: Request):
            approver_ctx = context_resolver.resolve(dict(request.headers))
            approver_id = approver_ctx.actor_id
            try:
                result = runtime.invoke_rejected(approval_id, approver_id=approver_id)
            except ApprovalNotFound as exc:
                raise HTTPException(status_code=404, detail=str(exc))
            except ApprovalAlreadyResolved as exc:
                raise HTTPException(status_code=409, detail=str(exc))
            return result

        # ---- Audit Logs ----

        @api.get("/audit-logs", tags=["audit"])
        def list_audit_logs(
            action_name: str | None = Query(default=None),
            actor_id: str | None = Query(default=None),
            tenant_id: str | None = Query(default=None),
            limit: int = Query(default=50, ge=1, le=500),
            offset: int = Query(default=0, ge=0),
        ):
            logs = audit_logger.list_logs(
                action_name=action_name,
                actor_id=actor_id,
                tenant_id=tenant_id,
                limit=limit,
                offset=offset,
            )
            return [AuditLogResponse.from_orm(log) for log in logs]

        self._fastapi_app = api
        return api

    # ------------------------------------------------------------------
    # MCP server
    # ------------------------------------------------------------------

    def mcp_server(
        self,
        *,
        mcp_actor_id: str = "mcp-agent",
        mcp_roles: list[str] | None = None,
        mcp_tenant_id: str | None = None,
    ):
        """Return a FastMCP server.

        Args:
            mcp_actor_id: Stable identity for all MCP-originated calls.
            mcp_roles: Roles assigned to the MCP caller.
            mcp_tenant_id: Tenant context for MCP calls.
        """
        return build_mcp_server(
            self.registry,
            self.runtime,
            mcp_actor_id=mcp_actor_id,
            mcp_roles=mcp_roles,
            mcp_tenant_id=mcp_tenant_id,
        )
