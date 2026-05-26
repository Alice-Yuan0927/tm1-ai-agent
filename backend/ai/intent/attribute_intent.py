"""Detect when a follow-up question is asking to display a dimension attribute
rather than to query new data."""

import json
import logging

from ...config import get_llm_temperature
from ...util.llm_json import parse_llm_json
from ..output.conversation import conversation_context
from ..providers import complete_text

_log = logging.getLogger(__name__)


def detect_attribute_intent(
    question: str,
    history: list[dict] | None,
    available_attributes: dict[str, list[str]],
) -> dict | None:
    """Return {"dim_name": ..., "attr_name": ...} or None.

    available_attributes: {dim_name: [attr_name, ...]} for the previous cube's
    non-measure dimensions.
    """
    if not available_attributes:
        return None

    prompt = f"""Previous conversation:
{conversation_context(history)}

Current question: "{question}"

Available dimension attributes from the previous query result:
{json.dumps(available_attributes, ensure_ascii=False)}

Is the user asking to display a dimension attribute (e.g. a name, grade, category,
description) of entities that already appeared in the previous result?

Reply with ONLY valid JSON - no markdown, no extra text:
{{"intent": true, "dim_name": "<dimension name>", "attr_name": "<attribute name>"}}
or
{{"intent": false}}"""

    try:
        raw = complete_text(
            prompt,
            max_tokens=80,
            temperature=get_llm_temperature("attribute_intent_temperature"),
        )
        parsed = parse_llm_json(raw)
        if (
            isinstance(parsed, dict)
            and parsed.get("intent")
            and parsed.get("dim_name")
            and parsed.get("attr_name")
        ):
            _log.info("[attr-intent] dim=%s attr=%s", parsed["dim_name"], parsed["attr_name"])
            return {"dim_name": str(parsed["dim_name"]), "attr_name": str(parsed["attr_name"])}
    except Exception as exc:
        _log.debug("attribute intent check failed: %s", exc)
    return None
