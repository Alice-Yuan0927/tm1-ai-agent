"""Tool registry for the MDX generation agent.

ToolSpec holds a tool's name, description, JSON-Schema parameters, and handler.
ToolRegistry stores specs, exports provider-ready schemas, validates args, and
dispatches execution — so adding a new tool is a single register() call.

Validation covers the subset of JSON Schema used by agent tools:
  - required field presence
  - primitive type coercion (non-string → str for "string" fields)
  - array type checking
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Callable

_log = logging.getLogger(__name__)


class _ValidationError(Exception):
    pass


def _validate(args: dict, schema: dict) -> dict:
    """Validate and lightly coerce args against a JSON Schema object.

    Only enforces required fields and top-level primitive types — enough to
    catch malformed LLM output before it reaches the handler.
    """
    props = schema.get("properties", {})
    required = set(schema.get("required", []))

    missing = required - {k for k, v in args.items() if v is not None}
    if missing:
        raise _ValidationError(f"missing required fields: {sorted(missing)}")

    result: dict = {}
    errors: list[str] = []

    for key, val in args.items():
        if key not in props:
            continue
        expected = props[key].get("type")
        if expected == "string" and not isinstance(val, str):
            result[key] = str(val)
        elif expected == "array" and not isinstance(val, list):
            errors.append(f"'{key}' must be an array, got {type(val).__name__}")
        else:
            result[key] = val

    if errors:
        raise _ValidationError("; ".join(errors))

    return {**args, **result}


@dataclass
class ToolSpec:
    """A single tool the LLM agent can call.

    handler signature: (validated_args: dict, cube_schema: dict) -> str (JSON)
    """

    name: str
    description: str
    parameters: dict  # JSON Schema {"type": "object", "properties": {...}, "required": [...]}
    handler: Callable[[dict, dict], str]


class ToolRegistry:
    """Registry that stores ToolSpecs, validates args, and dispatches calls."""

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> "ToolRegistry":
        """Register a tool. Returns self so calls can be chained."""
        self._specs[spec.name] = spec
        return self

    def schemas(self) -> list[dict]:
        """Return tool list in the format expected by LLM providers."""
        return [
            {
                "name": s.name,
                "description": s.description,
                "parameters": s.parameters,
            }
            for s in self._specs.values()
        ]

    def execute(self, name: str, args: dict, cube_schema: dict) -> str:
        """Validate args and call the registered handler. Always returns a JSON string."""
        spec = self._specs.get(name)
        if spec is None:
            return json.dumps({"error": f"Unknown tool: {name}"})
        try:
            validated = _validate(args, spec.parameters)
            return spec.handler(validated, cube_schema)
        except _ValidationError as exc:
            _log.warning("[registry] %s validation error: %s", name, exc)
            return json.dumps({"error": f"invalid args: {exc}"})
        except Exception as exc:
            _log.warning("[registry] %s failed: %s", name, exc)
            return json.dumps({"error": str(exc)})

    def __contains__(self, name: str) -> bool:
        return name in self._specs

    def __len__(self) -> int:
        return len(self._specs)
