"""Element lookup tools for the MDX generation agent.

These tools let the LLM verify element names and explore dimension structure
before committing to an MDX query, reducing first-attempt errors.
"""

import json
import logging

from ..retrieval.embeddings import search_by_embedding
from ...tm1.cache import find_question_element_matches

_log = logging.getLogger(__name__)

# ── Tool schemas (provider-agnostic) ─────────────────────────────────────────

ELEMENT_TOOLS: list[dict] = [
    {
        "name": "search_elements",
        "description": (
            "Search for TM1 dimension elements matching a term. "
            "Use when uncertain whether an element name exists or what its exact "
            "spelling is (entity names, account labels, scenario names, etc.)."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "dimension": {
                    "type": "string",
                    "description": "TM1 dimension name to search within.",
                },
                "term": {
                    "type": "string",
                    "description": "Search term (e.g. 'Singapore', 'labor cost', 'actual').",
                },
            },
            "required": ["dimension", "term"],
        },
    },
    {
        "name": "get_dimension_members",
        "description": (
            "Get elements and consolidations for a specific dimension. "
            "Use when you need to see available members or find the right "
            "consolidation parent for a Descendants/Children call."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "dimension": {
                    "type": "string",
                    "description": "TM1 dimension name.",
                },
            },
            "required": ["dimension"],
        },
    },
]


# ── Tool executor ─────────────────────────────────────────────────────────────

def execute_element_tool(name: str, args: dict, cube_schema: dict) -> str:
    """Execute one tool call and return result as a JSON string."""
    try:
        if name == "search_elements":
            return _search_elements(args, cube_schema)
        if name == "get_dimension_members":
            return _get_dimension_members(args, cube_schema)
        return json.dumps({"error": f"Unknown tool: {name}"})
    except Exception as exc:
        _log.warning("[element-tool] %s failed: %s", name, exc)
        return json.dumps({"error": str(exc)})


def _search_elements(args: dict, cube_schema: dict) -> str:
    dim = args.get("dimension", "")
    term = args.get("term", "")

    # Embedding search (semantic, handles aliases and synonyms)
    candidate_dims = [dim] if dim else None
    matches = search_by_embedding(term, candidate_dims=candidate_dims)
    if matches:
        results = [m.get("element") for m in matches[:10] if m.get("element")]
        if results:
            return json.dumps({"dimension": dim, "matches": results})

    # FTS fallback (exact / near-exact token match)
    fts_matches = find_question_element_matches(term)
    if dim:
        fts_matches = [m for m in fts_matches if m.get("dimension") == dim]
    results = [m.get("element") for m in fts_matches[:10] if m.get("element")]
    if results:
        return json.dumps({"dimension": dim, "matches": results})

    return json.dumps({"dimension": dim, "matches": [], "note": "No matches found"})


def _get_dimension_members(args: dict, cube_schema: dict) -> str:
    dim_name = args.get("dimension", "")
    for d in cube_schema.get("dimensions", []):
        if d.get("name") == dim_name:
            return json.dumps({
                "dimension": dim_name,
                "elements": d.get("elements", [])[:40],
                "consolidations": d.get("consolidations", [])[:20],
                "top_consolidations": d.get("top_consolidations", [])[:5],
                "default_element": d.get("default_element", ""),
            })
    return json.dumps({"error": f"Dimension '{dim_name}' not found in cube schema"})
