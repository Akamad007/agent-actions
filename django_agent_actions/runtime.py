"""Central action runtime — the single place where every invocation is processed.

All Django views call through here so behaviour is always consistent.

Execution order for ``invoke()``:
  1.  Resolve action from registry
  2.  Parse + validate inputs
  3.  Build RequestContext via ContextResolver (may raise PermissionError)
  4.  Check required scopes — deny immediately if any are missing
  5.  Run policy engine → ALLOW | REQUIRE_APPROVAL | DENY
  6.  DENY             → audit, return denied result
  7.  REQUIRE_APPROVAL → create Approval, audit, return approval_required result
  8.  ALLOW            → execute (with optional idempotency), audit, return result

Security guarantees:
  - Auth errors surface as PermissionError; views map these to HTTP 401.
  - Scope failures are audited as "denied" before returning.
  - Execution errors are audited as "error" before re-raising.
  - Approver identity is recorded on both the Approval row and the audit log.
  - Idempotency keys are scoped per (action, key, tenant).
"""

from __future__ import annotations

import threading
from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from django_agent_actions.approvals import ApprovalNotFound, ApprovalService
from django_agent_actions.audit import AuditLogger
from django_agent_actions.context import ContextResolver, RequestContext
from django_agent_actions.idempotency import IdempotencyService
from django_agent_actions.policies import Decision, PolicyEngine
from django_agent_actions.registry import ActionDef, ActionRegistry


class InvokeResult(BaseModel):
    status: Literal["success", "denied", "approval_required", "duplicate", "error"]
    result: Any = None
    approval_id: str | None = None
    message: str | None = None


class ActionRuntime:
    def __init__(
        self,
        registry: ActionRegistry,
        policy_engine: PolicyEngine,
        audit_logger: AuditLogger,
        idempotency_service: IdempotencyService,
        approval_service: ApprovalService,
        context_resolver: ContextResolver | None = None,
    ) -> None:
        self.registry = registry
        self.policy_engine = policy_engine
        self.audit_logger = audit_logger
        self.idempotency = idempotency_service
        self.approvals = approval_service
        self._context_resolver = context_resolver or ContextResolver()

    # ------------------------------------------------------------------
    # Primary entry points
    # ------------------------------------------------------------------

    def invoke(
        self,
        action_name: str,
        raw_inputs: dict,
        *,
        request=None,
        headers: dict | None = None,
        idempotency_key: str | None = None,
    ) -> InvokeResult:
        """Invoke an action.

        Pass either a Django ``request`` (preferred) or a plain ``headers``
        dict.  If both are provided, ``request`` takes precedence.
        """
        # 1. Resolve action
        action = self.registry.get(action_name)

        # 2. Validate inputs
        try:
            validated = action.input_model.model_validate(raw_inputs)
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc

        inputs_dict = validated.model_dump()

        # 3. Build context — PermissionError propagates to caller (→ HTTP 401)
        if request is not None:
            ctx = self._context_resolver.resolve_request(request)
        else:
            ctx = self._context_resolver.resolve(headers or {})

        # 4. Scope check — fast deny before any DB access
        missing = [s for s in action.required_scopes if not ctx.has_scope(s)]
        if missing:
            self.audit_logger.log(
                action_name=action_name,
                actor_id=ctx.actor_id,
                tenant_id=ctx.tenant_id,
                inputs=inputs_dict,
                policy_decision="deny",
                status="denied",
                idempotency_key=idempotency_key,
            )
            return InvokeResult(
                status="denied",
                message=f"Missing required scopes: {', '.join(missing)}",
            )

        # 5. Policy decision
        decision = self.policy_engine.evaluate(action, ctx)

        # 6. DENY
        if decision == Decision.DENY:
            self.audit_logger.log(
                action_name=action_name,
                actor_id=ctx.actor_id,
                tenant_id=ctx.tenant_id,
                inputs=inputs_dict,
                policy_decision=decision.value,
                status="denied",
                idempotency_key=idempotency_key,
            )
            return InvokeResult(status="denied", message="Action denied by policy.")

        # 7. REQUIRE_APPROVAL (from policy OR action flag)
        needs_approval = decision == Decision.REQUIRE_APPROVAL or action.approval_required
        if needs_approval:
            approval = self.approvals.create(
                action_name=action_name,
                actor_id=ctx.actor_id,
                tenant_id=ctx.tenant_id,
                inputs=inputs_dict,
            )
            self.audit_logger.log(
                action_name=action_name,
                actor_id=ctx.actor_id,
                tenant_id=ctx.tenant_id,
                inputs=inputs_dict,
                policy_decision=decision.value,
                status="approval_required",
                approval_id=str(approval.id),
                idempotency_key=idempotency_key,
            )
            return InvokeResult(
                status="approval_required",
                approval_id=str(approval.id),
                message="Action requires human approval before execution.",
            )

        # 8. ALLOW — execute
        execution_status = "success"
        try:
            if idempotency_key:
                execution = self.idempotency.execute_once(
                    action_name,
                    idempotency_key,
                    lambda: self._execute(action, inputs_dict, ctx),
                    tenant_id=ctx.tenant_id,
                )
                execution_status = execution.status
                result = execution.result
            else:
                result = self._execute(action, inputs_dict, ctx)
        except Exception:
            self.audit_logger.log(
                action_name=action_name,
                actor_id=ctx.actor_id,
                tenant_id=ctx.tenant_id,
                inputs=inputs_dict,
                policy_decision=decision.value,
                status="error",
                idempotency_key=idempotency_key,
            )
            raise

        self.audit_logger.log(
            action_name=action_name,
            actor_id=ctx.actor_id,
            tenant_id=ctx.tenant_id,
            inputs=inputs_dict,
            policy_decision=decision.value,
            status=execution_status,
            result=result,
            idempotency_key=idempotency_key,
        )
        if execution_status == "duplicate":
            return InvokeResult(
                status="duplicate",
                result=result,
                message="Returned cached result for idempotency key.",
            )
        return InvokeResult(status="success", result=result)

    # ------------------------------------------------------------------
    # Approval resumption
    # ------------------------------------------------------------------

    def invoke_approved(
        self,
        approval_id: str,
        *,
        approver_id: str | None = None,
    ) -> InvokeResult:
        """Execute an action whose approval has been granted."""
        try:
            approval = self.approvals.get(approval_id)
        except ApprovalNotFound:
            raise

        action = self.registry.get(approval.action_name)
        inputs_dict = approval.get_inputs()
        ctx = RequestContext(
            actor_id=approval.actor_id,
            tenant_id=approval.tenant_id,
            authenticated=True,
        )

        updated_approval = self.approvals.approve(
            approval_id,
            lambda: self._execute(action, inputs_dict, ctx),
            approver_id=approver_id,
        )
        result = updated_approval.get_result()

        self.audit_logger.log(
            action_name=approval.action_name,
            actor_id=approval.actor_id,
            tenant_id=approval.tenant_id,
            inputs=inputs_dict,
            policy_decision=Decision.REQUIRE_APPROVAL.value,
            status="success",
            result=result,
            approval_id=approval_id,
            approver_id=approver_id,
        )
        return InvokeResult(status="success", result=result, approval_id=approval_id)

    def invoke_rejected(
        self,
        approval_id: str,
        *,
        approver_id: str | None = None,
    ) -> InvokeResult:
        try:
            approval = self.approvals.get(approval_id)
        except ApprovalNotFound:
            raise

        self.approvals.reject(approval_id, approver_id=approver_id)

        self.audit_logger.log(
            action_name=approval.action_name,
            actor_id=approval.actor_id,
            tenant_id=approval.tenant_id,
            inputs=approval.get_inputs(),
            policy_decision=Decision.REQUIRE_APPROVAL.value,
            status="denied",
            approval_id=approval_id,
            approver_id=approver_id,
        )
        return InvokeResult(
            status="denied",
            approval_id=approval_id,
            message="Approval rejected.",
        )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _execute(self, action: ActionDef, inputs_dict: dict, ctx: RequestContext) -> Any:
        import inspect

        sig = inspect.signature(action.fn)
        if "ctx" in sig.parameters:
            return action.fn(**inputs_dict, ctx=ctx)
        return action.fn(**inputs_dict)


# ---------------------------------------------------------------------------
# Module-level singletons — used by views and the @action decorator ecosystem
# ---------------------------------------------------------------------------

# Global registry — users register their actions here.

registry = ActionRegistry()

_runtime: ActionRuntime | None = None
_runtime_lock = threading.Lock()


def get_runtime() -> ActionRuntime:
    """Return the lazily-initialised module-level runtime.

    The runtime is constructed the first time this function is called, reading
    optional configuration from ``settings.AGENT_ACTIONS``.

    Supported settings keys::

        AGENT_ACTIONS = {
            "DEFAULT_POLICY": RiskBasedPolicy(),   # default: allow all
            "AUTH_BACKEND": MyAuthBackend(),        # default: trust X-* headers
        }
    """
    global _runtime
    if _runtime is None:
        with _runtime_lock:
            if _runtime is None:
                _runtime = _build_runtime()
    return _runtime


def _build_runtime() -> ActionRuntime:
    from django.conf import settings

    from django_agent_actions.policies import DefaultPolicy, PolicyEngine

    agent_settings: dict = getattr(settings, "AGENT_ACTIONS", {})
    default_policy = agent_settings.get("DEFAULT_POLICY", DefaultPolicy())
    auth_backend = agent_settings.get("AUTH_BACKEND", None)

    return ActionRuntime(
        registry=registry,
        policy_engine=PolicyEngine(default_policy),
        audit_logger=AuditLogger(),
        idempotency_service=IdempotencyService(),
        approval_service=ApprovalService(),
        context_resolver=ContextResolver(auth_backend=auth_backend),
    )
