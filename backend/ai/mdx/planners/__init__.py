"""Sub-planners for deterministic MDX generation."""

from __future__ import annotations

from .consolidation import _try_consolidation_expansion_plan
from .pnl import _try_pnl_plan
from .shared import MIN_CONFIDENCE, SURFACE_THRESHOLD, MdxPlan


def try_plan_mdx(
    question: str,
    schema: dict,
    model_profile: dict | None = None,
    element_matches: list[tuple[str, str, str]] | None = None,
) -> MdxPlan | None:
    """Return an MDX plan when the question matches a known pattern."""
    matches = element_matches or []
    plan = _try_pnl_plan(question, schema, model_profile, matches)
    if plan and plan.confidence >= MIN_CONFIDENCE:
        return plan
    plan = _try_consolidation_expansion_plan(question, schema, model_profile, matches)
    if plan and plan.confidence >= MIN_CONFIDENCE:
        return plan
    return None


__all__ = ["try_plan_mdx", "MdxPlan", "MIN_CONFIDENCE", "SURFACE_THRESHOLD"]
