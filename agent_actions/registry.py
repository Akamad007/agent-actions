"""Action registry — stores and retrieves ActionDef objects."""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from typing import TYPE_CHECKING, Any, Callable, Literal

from pydantic import BaseModel

if TYPE_CHECKING:
    from agent_actions.policies import PolicyRule


@dataclass
class ActionDef:
    name: str
    description: str
    fn: Callable[..., Any]
    risk: Literal["low", "medium", "high"]
    approval_required: bool
    input_model: type[BaseModel]
    policy: "PolicyRule | None" = None
    # Scopes the actor must hold to invoke this action.
    # Checked before the policy engine runs; denial is immediate if any are missing.
    required_scopes: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def to_schema_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "risk": self.risk,
            "approval_required": self.approval_required,
            "required_scopes": self.required_scopes,
            "tags": self.tags,
            "input_schema": self.input_model.model_json_schema(),
        }


class ActionRegistry:
    def __init__(self) -> None:
        self._actions: dict[str, ActionDef] = {}
        self._lock = RLock()

    def register(self, action_def: ActionDef) -> None:
        with self._lock:
            if action_def.name in self._actions:
                raise ValueError(f"Action '{action_def.name}' is already registered.")
            self._actions[action_def.name] = action_def

    def get(self, name: str) -> ActionDef:
        with self._lock:
            try:
                return self._actions[name]
            except KeyError:
                raise KeyError(f"No action registered with name '{name}'.")

    def list(self) -> list[ActionDef]:
        with self._lock:
            return list(self._actions.values())

    def __contains__(self, name: str) -> bool:
        with self._lock:
            return name in self._actions
