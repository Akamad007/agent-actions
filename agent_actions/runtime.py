"""Central action runtime — the single place where every invocation is processed.

Both the HTTP server and the MCP server call through here so behaviour is
always consistent regardless of transport.

Execution order for ``invoke()``:
  1.  Resolve action from registry
  2.  Parse + validate inputs
  3.  Build RequestContext via ContextResolver (may raise PermissionError)
  4.  Check required scopes — deny immediately if any are missing
  5.  Check idempotency — short-circuit with cached result if seen before
  6.  Run policy engine → ALLOW | REQUIRE_APPROVAL | DENY
  7.  DENY             → audit, return denied result
  8.  REQUIRE_APPROVAL → create Approval, audit, return approval_required result
  9.  ALLOW            → execute (errors are audited), store idempotency, audit,
                         return success result

Security guarantees:
  - Auth errors surface as PermissionError; caller maps to HTTP 401.
  - Scope failures are audited as "denied" before returning.
  - Execution errors are audited as "error" before re-raising; internal
    exception messages do not reach the HTTP response layer.
  - Approver identity is recorded on both the Approval row and the audit log.
  - Idempotency keys are scoped per (action, key, tenant) to prevent
    cross-tenant collisions.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ValidationError

from agent_actions.approvals import ApprovalAlreadyResolved, ApprovalNotFound, ApprovalService
from agent_actions.audit import AuditLogger
from agent_actions.context import ContextResolver, RequestContext
from agent_actions.idempotency import IdempotencyService
from agent_actions.policies import Decision, PolicyEngine
from agent_actions.registry import ActionDef, ActionRegistry


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
    # Primary entry point
    # ------------------------------------------------------------------

    def invoke(
        self,
        action_name: str,
        raw_inputs: dict,
        headers: dict,
        idempotency_key: str | None = None,
    ) -> InvokeResult:
        # 1. Resolve action
        action = self.registry.get(action_name)

        # 2. Validate inputs
        try:
            validated = action.input_model.model_validate(raw_inputs)
        except ValidationError as exc:
            raise ValueError(str(exc)) from exc

        inputs_dict = validated.model_dump()

        # 3. Build context — PermissionError propagates to caller (→ HTTP 401)
        ctx = self._context_resolver.resolve(headers)

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
        needs_approval = (
            decision == Decision.REQUIRE_APPROVAL or action.approval_required
        )
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
                approval_id=approval.id,
                idempotency_key=idempotency_key,
            )
            return InvokeResult(
                status="approval_required",
                approval_id=approval.id,
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
            # Always audit execution failures; do not surface internal details.
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
        """Execute an action whose approval has been granted.

        The original actor's context is reconstructed from the stored approval.
        The policy is *not* re-evaluated — the approval decision itself is the
        authorisation gate.  *approver_id* is persisted on the Approval row and
        on the audit log.

        Note: the original actor's roles are not stored on the Approval, so the
        reconstructed context has an empty roles list.  This is a known
        limitation; scope/role checks during resumed execution are skipped.
        """
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

        def execute_fn():
            return self._execute(action, inputs_dict, ctx)

        updated_approval = self.approvals.approve(
            approval_id, execute_fn, approver_id=approver_id
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
