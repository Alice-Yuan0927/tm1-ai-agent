"""Rule-based MDX planner — thin facade over backend/ai/mdx/planners/.

Preserves the public API (MdxPlan, MIN_CONFIDENCE, SURFACE_THRESHOLD,
try_plan_mdx) so callers don't need to change their imports.
"""

from __future__ import annotations

from .planners import MIN_CONFIDENCE, SURFACE_THRESHOLD, MdxPlan, try_plan_mdx

__all__ = ["MdxPlan", "MIN_CONFIDENCE", "SURFACE_THRESHOLD", "try_plan_mdx"]
