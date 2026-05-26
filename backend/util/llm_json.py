"""Parse JSON returned by an LLM, tolerating ```json fences and stray whitespace."""

import json
import re

_FENCE_RE = re.compile(r"```(?:json)?")


def parse_llm_json(raw: str) -> object:
    """Strip ``` fences and json.loads(). Raises json.JSONDecodeError on failure."""
    cleaned = _FENCE_RE.sub("", raw or "").strip()
    return json.loads(cleaned)
