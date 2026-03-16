"""agent-actions — expose safe backend actions to LLM agents via MCP and HTTP."""

__version__ = "0.1.0"

from agent_actions.context import AuthBackend, ContextResolver, RequestContext
from agent_actions.db import init_db
from agent_actions.decorators import action
from agent_actions.policies import (
    Decision,
    DefaultPolicy,
    DenyPolicy,
    PolicyEngine,
    PolicyRule,
    RequireApprovalPolicy,
    RiskBasedPolicy,
    RoleBasedPolicy,
)
from agent_actions.redaction import redact_dict
from agent_actions.registry import ActionDef, ActionRegistry
from agent_actions.runtime import ActionRuntime, InvokeResult
from agent_actions.server import AgentActionApp

__all__ = [
    "__version__",
    "action",
    "AgentActionApp",
    "ActionDef",
    "ActionRegistry",
    "ActionRuntime",
    "AuthBackend",
    "ContextResolver",
    "Decision",
    "DefaultPolicy",
    "DenyPolicy",
    "init_db",
    "InvokeResult",
    "PolicyEngine",
    "PolicyRule",
    "redact_dict",
    "RequestContext",
    "RequireApprovalPolicy",
    "RiskBasedPolicy",
    "RoleBasedPolicy",
]
