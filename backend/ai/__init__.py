"""AI subsystem: LLM prompts, MDX generation, narrative, semantic profile.

Each public function lives in a focused subpackage. This package surface
stays stable so upstream code can keep importing from ``backend.ai``.
"""

from .intent.attribute_intent import detect_attribute_intent
from .intent.clarification import find_clarifications, find_schema_clarification
from .intent.cube_selection import select_cubes, select_cubes_with_profile
from .intent.layout_followup import effective_question_from_history
from .intent.preflight import is_unclear_question
from .mdx.context import MdxContext
from .mdx.generation import repair_cube_mdx
from .mdx.normalize import normalize_mdx, validate_generated_mdx
from .mdx.planner import MdxPlan, SURFACE_THRESHOLD as PLAN_SURFACE_THRESHOLD, try_plan_mdx
from .output.narrative import (
    generate_homepage_suggestions,
    parse_suggestions as _parse_suggestions,
    stream_financial_analysis,
)
from .output.semantic_profile import generate_semantic_profile
from .retrieval.rag import init_db, retrieve_similar, save_query

__all__ = [
    "MdxContext",
    "MdxPlan",
    "PLAN_SURFACE_THRESHOLD",
    "_parse_suggestions",
    "detect_attribute_intent",
    "effective_question_from_history",
    "find_clarifications",
    "find_schema_clarification",
    "generate_homepage_suggestions",
    "generate_semantic_profile",
    "init_db",
    "is_unclear_question",
    "normalize_mdx",
    "repair_cube_mdx",
    "retrieve_similar",
    "save_query",
    "select_cubes",
    "select_cubes_with_profile",
    "stream_financial_analysis",
    "try_plan_mdx",
    "validate_generated_mdx",
]
