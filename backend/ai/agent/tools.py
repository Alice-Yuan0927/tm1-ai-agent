"""Tool registry for the top-level TM1 financial analysis agent.

Extends the MDX-generation tool set with three discovery tools that replace
the old fixed-pipeline Stage 2 (cube selection):

  rag_search         — search historical query knowledge base (MUST be first call)
  list_model_cubes   — enumerate available TM1 cubes
  get_cube_schema    — load a cube's schema (loop intercepts to update state)

All other tools (search_elements, execute_mdx, etc.) are imported from
the existing ELEMENT_REGISTRY in element_tools.py.

The EXECUTE_MDX_TOOL schema is defined here but dispatched directly by
loop.py — same pattern as the old mdx/agent.py.
"""

import json
import logging

from ...tm1.service import get_cubes_with_descriptions
from ..retrieval.rag import retrieve_similar
from ..tools.element_tools import ELEMENT_REGISTRY
from ..tools.registry import ToolRegistry, ToolSpec

_log = logging.getLogger(__name__)


# ── New tool handlers ──────────────────────────────────────────────────────────

def _handle_rag_search(args: dict, _cube_schema: dict) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        return json.dumps({"error": "query is required"})
    try:
        hits = retrieve_similar(query, cube=None, limit=5)
    except Exception as exc:
        _log.debug("rag_search failed: %s", exc)
        return json.dumps({"hits": [], "note": f"RAG lookup failed: {exc}"})

    if not hits:
        return json.dumps({"hits": [], "note": "No similar past queries found."})

    return json.dumps({
        "hits": [
            {
                "rank": idx + 1,
                "cube": h.get("cube", ""),
                "question": h.get("question", ""),
                "mdx": h.get("mdx", ""),
            }
            for idx, h in enumerate(hits)
        ],
        "note": (
            "Results ranked by relevance (rank 1 = best match). "
            "If rank-1 question closely matches the user's intent (same metric, "
            "same dimension breakdown), reuse its MDX directly via execute_mdx "
            "after substituting any [?] placeholders with actual element names. "
            "Otherwise proceed to list_model_cubes."
        ),
    })


def _handle_list_model_cubes(args: dict, _cube_schema: dict) -> str:
    filter_term = str(args.get("filter_term", "") or "").strip().lower()
    try:
        cubes = get_cubes_with_descriptions()
    except Exception as exc:
        return json.dumps({"error": f"Could not fetch cube list: {exc}"})

    visible = [c for c in cubes if not c.get("is_system_cube")]
    if filter_term:
        visible = [
            c for c in visible
            if filter_term in str(c.get("cube", "")).lower()
            or filter_term in str(c.get("description", "")).lower()
        ]

    return json.dumps({
        "count": len(visible),
        "cubes": [
            {"cube": c.get("cube", ""), "description": c.get("description", "")}
            for c in visible
        ],
    })


def _handle_get_cube_schema_sentinel(_args: dict, _cube_schema: dict) -> str:
    # This handler is never called — the loop intercepts get_cube_schema directly
    # to update its internal cube_schema state before appending the result to
    # the LLM message history.
    return json.dumps({"error": "internal: dispatched by loop, not registry"})


# ── Build combined registry ────────────────────────────────────────────────────

AGENT_REGISTRY: ToolRegistry = ToolRegistry()

# 1. Discovery tools (new — run before any MDX work)
AGENT_REGISTRY.register(ToolSpec(
    name="rag_search",
    description=(
        "Search the historical TM1 query knowledge base for similar past questions. "
        "ALWAYS call this first, before any cube exploration or MDX writing. "
        "Returns ranked past queries with reusable MDX templates. "
        "If the top result closely matches the user's question, reuse its MDX."
    ),
    parameters={
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "The user's question verbatim.",
            },
        },
        "required": ["query"],
    },
    handler=_handle_rag_search,
))

AGENT_REGISTRY.register(ToolSpec(
    name="list_model_cubes",
    description=(
        "List all available TM1 cubes with their business descriptions. "
        "Call this to understand the model structure and select the right cube "
        "before loading its schema."
    ),
    parameters={
        "type": "object",
        "properties": {
            "filter_term": {
                "type": "string",
                "description": "Optional keyword to narrow cube names or descriptions.",
            },
        },
        "required": [],
    },
    handler=_handle_list_model_cubes,
))

AGENT_REGISTRY.register(ToolSpec(
    name="get_cube_schema",
    description=(
        "Load the full schema for a TM1 cube: dimensions, elements, consolidations, "
        "and measures. Call this after choosing a cube from list_model_cubes — "
        "the schema is required before you can write or validate MDX."
    ),
    parameters={
        "type": "object",
        "properties": {
            "cube": {
                "type": "string",
                "description": "Exact TM1 cube name as returned by list_model_cubes.",
            },
        },
        "required": ["cube"],
    },
    handler=_handle_get_cube_schema_sentinel,
))

# 2. MDX-generation tools (imported from the existing element registry)
for _spec in ELEMENT_REGISTRY._specs.values():
    AGENT_REGISTRY.register(_spec)

# 3. execute_mdx — schema only; dispatched directly by the loop.
EXECUTE_MDX_TOOL: dict = {
    "name": "execute_mdx",
    "description": (
        "Execute a TM1 MDX SELECT statement and return data rows. "
        "Call this once you have a well-formed MDX query. "
        "If it returns 0 rows, verify element names with search_elements and retry. "
        "Once it returns row_count > 0, STOP — your answer is ready."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "mdx": {
                "type": "string",
                "description": "A complete TM1 MDX SELECT statement.",
            },
        },
        "required": ["mdx"],
    },
}

# Full tool list passed to call_with_tools — registry tools + execute_mdx.
ALL_AGENT_TOOLS: list[dict] = AGENT_REGISTRY.schemas() + [EXECUTE_MDX_TOOL]
