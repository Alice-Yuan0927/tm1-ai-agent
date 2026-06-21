"""LLM-assisted rule drafting.

LLM only generates RULE DRAFTS at configuration time; it never decides
whether something is anomalous at runtime. The user reviews and edits the
draft, then saves it as YAML.

We reuse the existing provider abstraction (``ai.providers``) so this
honours the same key, model, and temperature controls as the rest of the
app — no second LLM client.
"""

from __future__ import annotations

import json
import logging

from ...util.llm_json import parse_llm_json
from .schema import RuleSet

_log = logging.getLogger(__name__)


_SUGGEST_SYSTEM = """\
You draft anomaly-detection rule SETS for IBM Planning Analytics (TM1) cubes.

Your output is a starting point for a human FP&A reviewer to edit. Be
concrete and conservative:
- Only suggest rules whose required columns plausibly exist in the cube
  schema you are given.
- Pick materiality thresholds (rel_pct AND abs_value) appropriate to the
  measure's typical magnitude. Err on the side of LESS noise.
- Provide a one-sentence description for every variance rule.

Output a single JSON object matching the supplied schema. No prose.
"""


def build_suggest_prompt(cube: str, schema_compact: dict,
                         sample_rows: list[dict] | None = None) -> dict:
    """Build a (system, user) prompt to ask the LLM for a draft RuleSet."""
    user = (
        f"Cube: {cube}\n\n"
        f"Schema (dimensions + measures):\n"
        f"{json.dumps(schema_compact, indent=2, ensure_ascii=False)}\n\n"
        f"Sample rows (optional):\n"
        f"{json.dumps(sample_rows or [], indent=2, ensure_ascii=False)}\n\n"
        f"Draft a JSON object with this shape:\n"
        f"{json.dumps(_TARGET_SHAPE, indent=2)}\n"
    )
    return {"system": _SUGGEST_SYSTEM.strip(), "user": user}


_TARGET_SHAPE = {
    "cube": "<echo cube name>",
    "version": 1,
    "description": "<1 sentence>",
    "integrity": [
        {"name": "<negative_values|headcount_no_salary|fte_gt_headcount|alloc_not_100pct>",
         "enabled": True}
    ],
    "variance": [
        {
            "name": "<short_id>",
            "description": "<1 sentence>",
            "measure": "<column name in the cube>",
            "reference": "<budget|forecast|prior_week|prior_year|custom>",
            "rel_pct": 0.10,
            "abs_value": 5000,
            "group_by": ["<dim 1>"],
        }
    ],
}


def parse_suggestion(raw: str, cube: str) -> RuleSet:
    """Parse LLM output and coerce into a validated RuleSet."""
    data = parse_llm_json(raw)
    if not isinstance(data, dict):
        raise ValueError("LLM output was not a JSON object")
    data.setdefault("cube", cube)
    return RuleSet.model_validate(data)
