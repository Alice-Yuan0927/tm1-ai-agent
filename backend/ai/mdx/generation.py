"""MDX repair helpers and shared prompt-building utilities.

The old text-generation MDX entrypoints and MDX-specific agent were removed.
The agentic loop in backend/ai/agent/loop.py owns MDX generation now.
repair_cube_mdx is still used by the legacy services.mdx_execution repair loop.
"""

import json
import logging

from ...config import MDX_MAX_TOKENS, get_llm_temperature
from ..output.conversation import conversation_context
from .context import MdxContext
from .normalize import normalize_mdx, validate_generated_mdx
from ..prompts import MDX_HARD_RULES
from ..prompts.prompt_rules import GENERAL_AGENT_CONTRACT
from ..providers import complete_text
from .directives import (
    consolidated_member_directive,
    grounded_members_section,
    line_item_directive,
    profile_defaults_directive,
    schema_dimensions_for_prompt,
)

_log = logging.getLogger(__name__)


def _examples_section(similar_queries: list[dict] | None, *, mention_axis: bool = True) -> str:
    if not similar_queries:
        return ""
    examples = "\n\n".join(
        f'Question: "{sq["question"]}"\nCube: {sq["cube"]}\nMDX:\n{sq["mdx"]}'
        for sq in similar_queries
    )
    suffix = (
        " (reuse axis layout and CrossJoin patterns only - always verify element "
        "names against the schema above)"
        if mention_axis
        else ""
    )
    return f"\nPast successful queries for structural reference{suffix}:\n{examples}\n"


def _profile_section(model_profile: dict | None) -> str:
    if not model_profile:
        return ""
    return (
        f"\nSemantic model profile defaults and guidance:\n"
        f"{json.dumps(model_profile, indent=2, ensure_ascii=False)}\n"
    )


def _build_dims_section(ctx: MdxContext) -> tuple[str, str, str, str, str]:
    """Return prompt schema JSON and directive sections."""
    dims_json = json.dumps(
        schema_dimensions_for_prompt(
            ctx.cube_schema.get("dimensions", []),
            ctx.grounded_members,
        ),
        indent=2,
        ensure_ascii=False,
    )
    return (
        dims_json,
        line_item_directive(ctx.question, ctx.cube_schema, ctx.model_profile),
        consolidated_member_directive(ctx.question, ctx.grounded_members, ctx.cube_schema),
        profile_defaults_directive(ctx.cube_schema, ctx.model_profile, ctx.question),
        grounded_members_section(ctx.grounded_members, ctx.cube_schema, ctx.model_profile),
    )


def _ai_complete_mdx(prompt: str, ctx: MdxContext, *, log_tag: str) -> str:
    mdx = complete_text(
        prompt,
        max_tokens=MDX_MAX_TOKENS,
        temperature=get_llm_temperature("mdx_temperature"),
    )
    mdx = normalize_mdx(mdx, ctx.question, ctx.model_profile)
    validate_generated_mdx(mdx, ctx.cube_name)
    _log.info("[%s] %s | %s", log_tag, ctx.cube_name, mdx)
    return mdx


def repair_cube_mdx(ctx: MdxContext, failed_mdx: str, error_message: str) -> str:
    """Ask the LLM to repair MDX rejected by TM1 for the legacy repair loop."""
    cube_name = ctx.cube_name
    dims_json, statement_directive, consolidated_directive, defaults_directive, grounded_section = _build_dims_section(ctx)
    examples_section = _examples_section(ctx.similar_queries, mention_axis=False)
    profile_section = _profile_section(ctx.model_profile)

    prompt = f"""You are an expert TM1 / IBM Planning Analytics MDX debugger.

{GENERAL_AGENT_CONTRACT}

Cube: {cube_name}

Dimensions and available elements:
{dims_json}
{grounded_section}{statement_directive}{consolidated_directive}{defaults_directive}{examples_section}
{profile_section}
User question: "{ctx.question}"

Previous conversation:
{conversation_context(ctx.history)}

TM1 rejected this MDX:
{failed_mdx}

TM1 error:
{error_message}

Return a corrected MDX SELECT statement for the same question.

Hard rules for valid TM1 MDX:
1. FROM [{cube_name}] must come immediately after the axes - always BEFORE WHERE. Use ONLY cube [{cube_name}].
{MDX_HARD_RULES}
28. If the error says an element cannot be found, replace it with the closest exact element from the correct dimension.
29. Reply with ONLY the raw MDX - no markdown, no comments, nothing else"""

    try:
        return _ai_complete_mdx(prompt, ctx, log_tag="MDX repair")
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"MDX repair error: {exc}") from exc
