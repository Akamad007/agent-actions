"""Tests for ActionRegistry and ActionDef."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

import pytest

from django_agent_actions.decorators import action
from django_agent_actions.registry import ActionDef, ActionRegistry


def make_simple_action(name="test_action"):
    @action(name=name, description="A test action", risk="low")
    def fn(value: str):
        return {"value": value}

    return fn


class TestActionRegistry:
    def test_register_and_get(self):
        registry = ActionRegistry()
        fn = make_simple_action("my_action")
        registry.register(fn._action_def)
        retrieved = registry.get("my_action")
        assert retrieved.name == "my_action"
        assert retrieved.description == "A test action"

    def test_list_returns_all(self):
        registry = ActionRegistry()
        registry.register(make_simple_action("a")._action_def)
        registry.register(make_simple_action("b")._action_def)
        registry.register(make_simple_action("c")._action_def)
        names = {a.name for a in registry.list()}
        assert names == {"a", "b", "c"}

    def test_duplicate_name_raises(self):
        registry = ActionRegistry()
        fn = make_simple_action("dup")
        registry.register(fn._action_def)
        with pytest.raises(ValueError, match="already registered"):
            registry.register(fn._action_def)

    def test_get_missing_raises(self):
        registry = ActionRegistry()
        with pytest.raises(KeyError, match="No action registered"):
            registry.get("nonexistent")

    def test_contains(self):
        registry = ActionRegistry()
        fn = make_simple_action("exists")
        registry.register(fn._action_def)
        assert "exists" in registry
        assert "missing" not in registry

    def test_concurrent_register_same_action_is_atomic(self):
        registry = ActionRegistry()
        fn = make_simple_action("shared")

        def register_once():
            try:
                registry.register(fn._action_def)
                return "registered"
            except ValueError:
                return "duplicate"

        with ThreadPoolExecutor(max_workers=8) as executor:
            results = list(executor.map(lambda _: register_once(), range(8)))

        assert results.count("registered") == 1
        assert results.count("duplicate") == 7
        assert len(registry.list()) == 1


class TestActionDefSchema:
    def test_input_schema_from_type_hints(self):
        @action(name="typed_action", description="desc", risk="low")
        def fn(invoice_id: str, amount: float):
            return {}

        defn: ActionDef = fn._action_def
        schema = defn.input_model.model_json_schema()
        props = schema["properties"]
        assert "invoice_id" in props
        assert "amount" in props

    def test_ctx_excluded_from_schema(self):
        from django_agent_actions.context import RequestContext

        @action(name="ctx_action", description="desc", risk="low")
        def fn(value: str, ctx: RequestContext):
            return {}

        schema = fn._action_def.input_model.model_json_schema()
        assert "ctx" not in schema.get("properties", {})

    def test_to_schema_dict(self):
        fn = make_simple_action("schema_test")
        d = fn._action_def.to_schema_dict()
        assert d["name"] == "schema_test"
        assert "input_schema" in d
        assert d["risk"] == "low"
        assert d["approval_required"] is False
