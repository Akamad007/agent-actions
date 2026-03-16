"""agent-actions — expose safe backend actions to LLM agents as part of a Django app."""

__version__ = "0.2.0"

from django_agent_actions.context import AuthBackend, ContextResolver, RequestContext
from django_agent_actions.decorators import action
from django_agent_actions.policies import (
    Decision,
    DefaultPolicy,
    DenyPolicy,
    PolicyEngine,
    PolicyRule,
    RequireApprovalPolicy,
    RiskBasedPolicy,
    RoleBasedPolicy,
)
from django_agent_actions.redaction import redact_dict
from django_agent_actions.registry import ActionDef, ActionRegistry
from django_agent_actions.runtime import ActionRuntime, InvokeResult, get_runtime, registry

__all__ = [
    "__version__",
    "action",
    "ActionDef",
    "ActionRegistry",
    "ActionRuntime",
    "AuthBackend",
    "ContextResolver",
    "Decision",
    "DefaultPolicy",
    "DenyPolicy",
    "get_runtime",
    "InvokeResult",
    "PolicyEngine",
    "PolicyRule",
    "redact_dict",
    "registry",
    "RequestContext",
    "RequireApprovalPolicy",
    "RiskBasedPolicy",
    "RoleBasedPolicy",
]
