"""Policy engine for agent-actions.

Policies decide whether an action invocation should be:
  - ALLOW          : proceed with execution
  - DENY           : refuse immediately
  - REQUIRE_APPROVAL: queue for human approval

A PolicyRule is any object with an `evaluate(action, ctx) -> Decision` method.
Implement the Protocol to plug in your own rules (RBAC, OPA, etc.).
"""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from agent_actions.context import RequestContext
    from agent_actions.registry import ActionDef


class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


@runtime_checkable
class PolicyRule(Protocol):
    def evaluate(self, action: "ActionDef", ctx: "RequestContext") -> Decision:
        ...


# ---------------------------------------------------------------------------
# Built-in policy rules
# ---------------------------------------------------------------------------


class DefaultPolicy:
    """Always allow. Use as the fallback when no per-action policy is set."""

    def evaluate(self, action: "ActionDef", ctx: "RequestContext") -> Decision:
        return Decision.ALLOW


class DenyPolicy:
    """Always deny. Useful for disabled actions or maintenance windows."""

    def evaluate(self, action: "ActionDef", ctx: "RequestContext") -> Decision:
        return Decision.DENY


class RequireApprovalPolicy:
    """Always require approval regardless of other settings."""

    def evaluate(self, action: "ActionDef", ctx: "RequestContext") -> Decision:
        return Decision.REQUIRE_APPROVAL


class RoleBasedPolicy:
    """Allow only if the actor has one of the required roles; otherwise deny."""

    def __init__(self, allowed_roles: list[str]) -> None:
        self.allowed_roles = set(allowed_roles)

    def evaluate(self, action: "ActionDef", ctx: "RequestContext") -> Decision:
        if self.allowed_roles.intersection(ctx.roles):
            return Decision.ALLOW
        return Decision.DENY


class RiskBasedPolicy:
    """Map action risk level to a policy decision.

    Defaults:
      low    -> ALLOW
      medium -> ALLOW
      high   -> REQUIRE_APPROVAL
    """

    _DEFAULT_MAP = {
        "low": Decision.ALLOW,
        "medium": Decision.ALLOW,
        "high": Decision.REQUIRE_APPROVAL,
    }

    def __init__(self, risk_map: dict[str, Decision] | None = None) -> None:
        self.risk_map = risk_map or self._DEFAULT_MAP

    def evaluate(self, action: "ActionDef", ctx: "RequestContext") -> Decision:
        return self.risk_map.get(action.risk, Decision.ALLOW)


# ---------------------------------------------------------------------------
# Policy engine — applies per-action override or the app-level default
# ---------------------------------------------------------------------------


class PolicyEngine:
    def __init__(self, default_policy: PolicyRule | None = None) -> None:
        self.default_policy: PolicyRule = default_policy or DefaultPolicy()

    def evaluate(self, action: "ActionDef", ctx: "RequestContext") -> Decision:
        rule: PolicyRule = action.policy if action.policy is not None else self.default_policy
        return rule.evaluate(action, ctx)
