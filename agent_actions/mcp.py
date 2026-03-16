"""MCP server integration.

Exposes registered actions as MCP tools using the ``mcp`` library (FastMCP).
Every tool invocation flows through the same ``ActionRuntime`` as HTTP
requests — auth, policy, approval, idempotency, and audit are all applied.

The MCP caller's identity is configured at server-build time via
``mcp_actor_id``, ``mcp_roles``, and ``mcp_tenant_id``.  Override these when
mounting the server so that MCP-originated calls carry a stable, auditable
identity rather than the generic ``mcp-agent`` default.

Usage::

    mcp_server = app.mcp_server(
        mcp_actor_id="my-ai-agent",
        mcp_roles=["agent", "finance"],
        mcp_tenant_id="acme-corp",
    )
    # Run standalone:  mcp run examples/basic_app.py
    # Or mount:        fastapi_app.mount("/mcp", mcp_server.get_asgi_app())
"""

from __future__ import annotations

import json
from typing import Any

from mcp.server.fastmcp import FastMCP

from agent_actions.registry import ActionRegistry
from agent_actions.runtime import ActionRuntime


def build_mcp_server(
    registry: ActionRegistry,
    runtime: ActionRuntime,
    name: str = "agent-actions",
    *,
    mcp_actor_id: str = "mcp-agent",
    mcp_roles: list[str] | None = None,
    mcp_tenant_id: str | None = None,
) -> FastMCP:
    """Create a FastMCP server with one tool per registered action.

    Args:
        mcp_actor_id: Identity injected into every MCP-originated invocation.
            Override this to give your AI agent a meaningful, auditable name.
        mcp_roles: Roles assigned to the MCP caller.  Defaults to
            ``["agent"]``.  Must satisfy any ``required_scopes`` / policy on
            the actions you want the agent to reach.
        mcp_tenant_id: Tenant context for MCP calls.  Set this when the server
            serves a single tenant so idempotency keys and audit records are
            correctly scoped.
    """
    mcp = FastMCP(name)

    for action_def in registry.list():
        _action_def = action_def
        _register_tool(
            mcp,
            _action_def,
            runtime,
            actor_id=mcp_actor_id,
            roles=mcp_roles or ["agent"],
            tenant_id=mcp_tenant_id,
        )

    return mcp


def _register_tool(
    mcp: FastMCP,
    action_def: Any,
    runtime: ActionRuntime,
    *,
    actor_id: str,
    roles: list[str],
    tenant_id: str | None,
) -> None:
    description = action_def.description

    @mcp.tool(name=action_def.name, description=description)
    def _tool_handler(**kwargs: Any) -> str:
        headers: dict[str, str] = {
            "x-actor-id": actor_id,
            "x-roles": ",".join(roles),
        }
        if tenant_id is not None:
            headers["x-tenant-id"] = tenant_id
        try:
            result = runtime.invoke(
                action_name=action_def.name,
                raw_inputs=kwargs,
                headers=headers,
            )
            return json.dumps(result.model_dump(), default=str)
        except PermissionError:
            return json.dumps({"status": "error", "message": "Authentication required."})
        except Exception:
            return json.dumps({"status": "error", "message": "Action execution failed."})

    _tool_handler.__doc__ = description
    _tool_handler.__name__ = action_def.name
