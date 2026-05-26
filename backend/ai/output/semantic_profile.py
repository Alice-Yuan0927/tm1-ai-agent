"""Generate a JSON semantic profile of the connected TM1 model."""

import json

from ...config import SEMANTIC_PROFILE_MAX_TOKENS, get_llm_temperature
from ...util.llm_json import parse_llm_json
from ..providers import complete_text


def generate_semantic_profile(schema_summary: dict) -> dict:
    prompt = f"""You are building a semantic profile for a TM1 / IBM Planning Analytics model.

Schema summary:
{json.dumps(schema_summary, indent=2, ensure_ascii=False)}

Generate a concise JSON semantic profile that helps an AI analyst map natural
business language to this model's cubes, dimensions, measures, and attributes.

Rules:
- Infer business terms from cube names, dimension names, measure names, attributes,
  and sample dimension elements.
- Prefer general mappings over one-off hardcoded rules.
- Include only mappings that are supported by the schema evidence.
- Use exact cube, dimension, measure, and attribute names from the schema.
- Keep the JSON compact: at most 12 business_terms, 12 metric_mappings, and
  20 cube_roles.
- Each array must contain at most 5 strings. Do not enumerate every scenario,
  month, forecast version, employee, account, or element variant.
- Prefer broad semantic terms over long synonym lists.
- Do not include markdown or commentary.

Return ONLY valid JSON with this shape:
{{
  "profile_version": 1,
  "model_name": "<short inferred model name>",
  "source": "generated_from_schema_cache",
  "business_terms": {{
    "<natural language term>": ["<dimension/attribute/measure/cube name>", "..."]
  }},
  "metric_mappings": {{
    "<metric term>": {{
      "preferred_cubes": ["<cube>", "..."],
      "preferred_measures": ["<measure>", "..."],
      "preferred_dimensions": ["<dimension>", "..."]
    }}
  }},
  "cube_roles": {{
    "<cube>": {{
      "role": "<what this cube is for>",
      "best_for": ["<question type>", "..."],
      "avoid_for": ["<question type>", "..."]
    }}
  }},
  "default_filters": {{}},
  "selection_guidance": ["<general guidance>", "..."],
  "chart_guidance": {{
    "percentage_terms": ["percent", "percentage", "pct", "share", "ratio", "rate"],
    "split_percentage_measures": true,
    "prefer_share_chart": "doughnut",
    "prefer_absolute_chart": "bar"
  }}
}}"""

    raw = ""
    try:
        raw = complete_text(
            prompt,
            max_tokens=SEMANTIC_PROFILE_MAX_TOKENS,
            temperature=get_llm_temperature("semantic_profile_temperature"),
        )
        result = parse_llm_json(raw)
        if not isinstance(result, dict):
            raise RuntimeError("AI semantic profile must be a JSON object")
        return result
    except json.JSONDecodeError as exc:
        repaired = _repair_truncated_profile_json(raw)
        if isinstance(repaired, dict):
            return repaired
        raise RuntimeError(f"AI semantic profile parsing failed. Raw: {raw}") from exc
    except Exception as exc:
        raise RuntimeError(f"AI semantic profile generation error: {exc}") from exc


def _repair_truncated_profile_json(raw: str) -> dict | None:
    """Ask the LLM to complete and fix a truncated/malformed profile JSON.

    Returns a parsed dict on success, or None if repair also failed.
    """
    if not raw or not raw.strip():
        return None

    repair_prompt = f"""The following semantic profile JSON was truncated or
malformed. Return a single VALID compact JSON object with the same shape as
the input, preserving every field already produced. Do not add commentary,
markdown, or extra fields. If a string was cut off, finish it sensibly; if
an array or object was open, close it cleanly. Drop the final partial entry
only if completing it would change the meaning.

Original (possibly truncated) JSON:
{raw}

Required top-level keys: profile_version, model_name, source, business_terms,
metric_mappings, cube_roles, default_filters, selection_guidance, chart_guidance.

Return ONLY valid JSON."""

    try:
        fixed = complete_text(
            repair_prompt,
            max_tokens=max(SEMANTIC_PROFILE_MAX_TOKENS, 14000),
            temperature=0,
        )
        result = parse_llm_json(fixed)
        return result if isinstance(result, dict) else None
    except Exception:
        return None
