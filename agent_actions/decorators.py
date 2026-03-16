"""@action decorator — the primary developer-facing API.

Usage::

    @action(
        name="get_invoice",
        description="Fetch a single invoice",
        risk="low",
        approval_required=False,
        required_scopes=["finance"],   # actor must hold this scope
    )
    def get_invoice(invoice_id: str, ctx: RequestContext):
        return {"invoice_id": invoice_id, "status": "open"}

The decorator introspects the function signature, skips the ``ctx`` parameter,
and builds a Pydantic input model automatically.  The decorated function is
returned unchanged; the ``ActionDef`` is stored as ``fn._action_def`` for later
registration via ``app.register(fn)``.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, Literal, get_type_hints

from pydantic import BaseModel, create_model

from agent_actions.registry import ActionDef


def _build_input_model(name: str, fn: Callable) -> type[BaseModel]:
    """Derive a Pydantic model from a function's type-annotated parameters.

    Rules:
    - Parameters named ``ctx`` are skipped (injected at runtime).
    - Parameters annotated as ``RequestContext`` (by name check) are skipped.
    - If a single non-ctx parameter is itself a ``BaseModel`` subclass, use it
      directly rather than wrapping it.
    - Otherwise build a model from scalar type hints + defaults.
    """
    sig = inspect.signature(fn)
    try:
        hints = get_type_hints(fn)
    except Exception:
        hints = {}

    fields: dict[str, Any] = {}
    non_ctx_params = []

    for param_name, param in sig.parameters.items():
        if param_name == "ctx":
            continue
        annotation = hints.get(param_name, param.annotation)
        if annotation is not inspect.Parameter.empty:
            ann_name = getattr(annotation, "__name__", "") or getattr(annotation, "_name", "")
            if ann_name == "RequestContext":
                continue
        non_ctx_params.append((param_name, param, annotation))

    # If the sole parameter is already a Pydantic model, use it directly.
    if len(non_ctx_params) == 1:
        _, _, annotation = non_ctx_params[0]
        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            return annotation

    for param_name, param, annotation in non_ctx_params:
        if annotation is inspect.Parameter.empty:
            annotation = Any
        if param.default is inspect.Parameter.empty:
            fields[param_name] = (annotation, ...)
        else:
            fields[param_name] = (annotation, param.default)

    model_name = f"{name.replace('_', ' ').title().replace(' ', '')}Input"
    return create_model(model_name, **fields)


def action(
    name: str,
    description: str,
    risk: Literal["low", "medium", "high"] = "low",
    approval_required: bool = False,
    policy: Any = None,
    required_scopes: list[str] | None = None,
    tags: list[str] | None = None,
) -> Callable:
    """Decorator that attaches ``ActionDef`` metadata to a function.

    Args:
        name: Unique action identifier used in HTTP paths and MCP tool names.
        description: Human-readable description shown in ``/actions`` and MCP.
        risk: ``"low"`` | ``"medium"`` | ``"high"`` — informs policy decisions.
        approval_required: Always require human approval regardless of policy.
        policy: Per-action ``PolicyRule`` override.  ``None`` uses the app-level
            default policy.
        required_scopes: Actor must hold *all* listed scopes (or matching roles)
            for the action to be reachable.  Checked before policy evaluation.
        tags: Arbitrary string labels for documentation / filtering.
    """

    def decorator(fn: Callable) -> Callable:
        input_model = _build_input_model(name, fn)
        action_def = ActionDef(
            name=name,
            description=description,
            fn=fn,
            risk=risk,
            approval_required=approval_required,
            input_model=input_model,
            policy=policy,
            required_scopes=required_scopes or [],
            tags=tags or [],
        )
        fn._action_def = action_def  # type: ignore[attr-defined]
        return fn

    return decorator
