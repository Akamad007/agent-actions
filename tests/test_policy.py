"""Tests for the policy engine and built-in policy rules."""

from __future__ import annotations

from django_agent_actions.context import RequestContext
from django_agent_actions.decorators import action
from django_agent_actions.policies import (
    Decision,
    DefaultPolicy,
    DenyPolicy,
    PolicyEngine,
    RequireApprovalPolicy,
    RiskBasedPolicy,
    RoleBasedPolicy,
)


def make_action(name="test", risk="low", policy=None, approval_required=False):
    @action(
        name=name,
        description="test action",
        risk=risk,
        approval_required=approval_required,
        policy=policy,
    )
    def fn(x: str):
        return x

    return fn._action_def


def make_ctx(roles=None):
    return RequestContext(actor_id="alice", roles=roles or ["user"])


class TestDefaultPolicy:
    def test_always_allows(self):
        policy = DefaultPolicy()
        assert policy.evaluate(make_action(), make_ctx()) == Decision.ALLOW


class TestDenyPolicy:
    def test_always_denies(self):
        policy = DenyPolicy()
        assert policy.evaluate(make_action(), make_ctx()) == Decision.DENY


class TestRequireApprovalPolicy:
    def test_always_requires_approval(self):
        policy = RequireApprovalPolicy()
        assert policy.evaluate(make_action(), make_ctx()) == Decision.REQUIRE_APPROVAL


class TestRoleBasedPolicy:
    def test_allows_when_role_present(self):
        policy = RoleBasedPolicy(allowed_roles=["admin", "finance"])
        ctx = make_ctx(roles=["admin"])
        assert policy.evaluate(make_action(), ctx) == Decision.ALLOW

    def test_denies_when_role_missing(self):
        policy = RoleBasedPolicy(allowed_roles=["admin"])
        ctx = make_ctx(roles=["user"])
        assert policy.evaluate(make_action(), ctx) == Decision.DENY

    def test_allows_with_multiple_matching_roles(self):
        policy = RoleBasedPolicy(allowed_roles=["admin", "finance"])
        ctx = make_ctx(roles=["finance", "user"])
        assert policy.evaluate(make_action(), ctx) == Decision.ALLOW


class TestRiskBasedPolicy:
    def test_low_risk_allows(self):
        policy = RiskBasedPolicy()
        assert policy.evaluate(make_action(risk="low"), make_ctx()) == Decision.ALLOW

    def test_medium_risk_allows(self):
        policy = RiskBasedPolicy()
        assert policy.evaluate(make_action(risk="medium"), make_ctx()) == Decision.ALLOW

    def test_high_risk_requires_approval(self):
        policy = RiskBasedPolicy()
        assert policy.evaluate(make_action(risk="high"), make_ctx()) == Decision.REQUIRE_APPROVAL

    def test_custom_risk_map(self):
        policy = RiskBasedPolicy(
            risk_map={
                "low": Decision.DENY,
                "medium": Decision.DENY,
                "high": Decision.DENY,
            }
        )
        assert policy.evaluate(make_action(risk="low"), make_ctx()) == Decision.DENY


class TestPolicyEngine:
    def test_uses_default_policy_when_no_action_policy(self):
        engine = PolicyEngine(DenyPolicy())
        action_def = make_action(policy=None)
        assert engine.evaluate(action_def, make_ctx()) == Decision.DENY

    def test_prefers_action_policy_over_default(self):
        engine = PolicyEngine(DenyPolicy())
        action_def = make_action(policy=DefaultPolicy())
        assert engine.evaluate(action_def, make_ctx()) == Decision.ALLOW

    def test_default_is_allow_when_none_provided(self):
        engine = PolicyEngine()
        action_def = make_action()
        assert engine.evaluate(action_def, make_ctx()) == Decision.ALLOW
