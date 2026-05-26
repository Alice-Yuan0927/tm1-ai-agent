"""Unit tests for ToolRegistry — no LLM or DB required."""

import json

import pytest

from backend.ai.tools.registry import ToolRegistry, ToolSpec, _validate, _ValidationError


# ── _validate ────────────────────────────────────────────────────────────────

_SCHEMA = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "count": {"type": "integer"},
        "tags": {"type": "array"},
    },
    "required": ["name"],
}


def test_validate_passes_with_required_field():
    result = _validate({"name": "foo"}, _SCHEMA)
    assert result["name"] == "foo"


def test_validate_raises_on_missing_required():
    with pytest.raises(_ValidationError, match="missing required fields"):
        _validate({}, _SCHEMA)


def test_validate_coerces_non_string_to_string():
    result = _validate({"name": 42}, _SCHEMA)
    assert result["name"] == "42"


def test_validate_raises_on_non_list_for_array_field():
    with pytest.raises(_ValidationError, match="must be an array"):
        _validate({"name": "ok", "tags": "not-a-list"}, _SCHEMA)


def test_validate_passes_list_for_array_field():
    result = _validate({"name": "ok", "tags": ["a", "b"]}, _SCHEMA)
    assert result["tags"] == ["a", "b"]


def test_validate_preserves_unknown_keys():
    # _validate returns {**args, **result} so unknown keys from args are kept
    result = _validate({"name": "ok", "unknown_key": "x"}, _SCHEMA)
    assert "unknown_key" in result


def test_validate_allows_none_for_optional_field():
    result = _validate({"name": "ok", "count": None}, _SCHEMA)
    assert "name" in result


# ── ToolRegistry ─────────────────────────────────────────────────────────────

def _make_registry() -> tuple[ToolRegistry, list]:
    """Return a registry with one echo tool and a call-log list."""
    registry = ToolRegistry()
    calls: list[tuple[dict, dict]] = []

    def echo_handler(args: dict, cube_schema: dict) -> str:
        calls.append((args, cube_schema))
        return json.dumps({"echo": args.get("text", "")})

    registry.register(ToolSpec(
        name="echo",
        description="Echo tool for tests",
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        handler=echo_handler,
    ))
    return registry, calls


def test_registry_len():
    registry, _ = _make_registry()
    assert len(registry) == 1


def test_registry_contains():
    registry, _ = _make_registry()
    assert "echo" in registry
    assert "nonexistent" not in registry


def test_registry_schemas_format():
    registry, _ = _make_registry()
    schemas = registry.schemas()
    assert len(schemas) == 1
    schema = schemas[0]
    assert schema["name"] == "echo"
    assert "description" in schema
    assert schema["parameters"]["type"] == "object"


def test_registry_execute_success():
    registry, calls = _make_registry()
    result_str = registry.execute("echo", {"text": "hello"}, {})
    result = json.loads(result_str)
    assert result["echo"] == "hello"
    assert len(calls) == 1


def test_registry_execute_unknown_tool():
    registry, _ = _make_registry()
    result_str = registry.execute("unknown_tool", {}, {})
    result = json.loads(result_str)
    assert "error" in result
    assert "Unknown tool" in result["error"]


def test_registry_execute_missing_required_arg():
    registry, calls = _make_registry()
    result_str = registry.execute("echo", {}, {})
    result = json.loads(result_str)
    assert "error" in result
    assert len(calls) == 0  # handler was NOT called


def test_registry_execute_handler_exception_returns_error_json():
    registry = ToolRegistry()

    def boom(args: dict, cube_schema: dict) -> str:
        raise RuntimeError("handler exploded")

    registry.register(ToolSpec(
        name="boom",
        description="Exploding tool",
        parameters={"type": "object", "properties": {}, "required": []},
        handler=boom,
    ))
    result_str = registry.execute("boom", {}, {})
    result = json.loads(result_str)
    assert "error" in result
    assert "handler exploded" in result["error"]


def test_registry_register_returns_self_for_chaining():
    registry = ToolRegistry()
    returned = registry.register(ToolSpec(
        name="t", description="", parameters={"type": "object", "properties": {}, "required": []},
        handler=lambda a, b: "{}",
    ))
    assert returned is registry


def test_registry_coerces_arg_before_handler_call():
    registry = ToolRegistry()
    received: list[dict] = []

    def capture(args: dict, cube_schema: dict) -> str:
        received.append(args)
        return "{}"

    registry.register(ToolSpec(
        name="capture",
        description="",
        parameters={
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
        handler=capture,
    ))
    registry.execute("capture", {"name": 99}, {})
    assert received[0]["name"] == "99"
