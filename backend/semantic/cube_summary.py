"""Generate and refresh structured cube summaries.

A summary is a compact, stable description of what a cube is for.
It is built deterministically from schema_cache data + dim_roles —
no LLM call needed.  The summary is stored in cube_summaries (SQLite)
and used by cube_context_for_selection() instead of the full schema dump.

Public API:
  generate_summary(cube_name, schema, model_profile) -> dict
  refresh_all_summaries(model_profile)               -> dict[cube_name, summary]
  get_or_generate(cube_name, model_profile)          -> dict
"""

from __future__ import annotations

import logging
from typing import Any

from ..ai.schema.dim_roles import get_dim_role
from ..config import CUBE_CONTEXT_ELEMENTS_PER_DIM, CUBE_CONTEXT_MEASURE_LIMIT
from ..tm1.service import get_cube_schema, get_cubes_with_descriptions
from .store import load_all_summaries, load_cube_summary, save_cube_summary

_log = logging.getLogger(__name__)

# How many "important elements" to surface per non-measure dim.
_IMPORTANT_ELEM_LIMIT = 8
# Roles whose elements are business-meaningful enough to surface.
_SURFACE_ROLES = {"line_item", "scenario", "entity_subject", "time"}


def generate_summary(
    cube_name: str,
    schema: dict,
    model_profile: dict | None = None,
) -> dict:
    """Build a structured summary from a full cube schema dict.

    No network calls.  Pure function — safe to call in a tight loop.
    """
    profile_roles: dict[str, str] = (model_profile or {}).get("dim_roles", {})
    cube_roles: dict[str, Any] = (model_profile or {}).get("cube_roles", {})

    dims = schema.get("dimensions", [])

    grain: list[str] = []
    dim_summaries: list[dict] = []
    measures: list[str] = []

    for dim in dims:
        name = str(dim.get("name", "")).strip()
        if not name:
            continue
        role = get_dim_role(dim, profile_roles=profile_roles)
        elements = [str(e) for e in (dim.get("elements") or []) if e]

        if role == "measure":
            measures = elements[:CUBE_CONTEXT_MEASURE_LIMIT]
            continue

        grain.append(name)

        important: list[str] = []
        if role in _SURFACE_ROLES:
            # Prefer leaf (non-consolidated) elements when available.
            leaf = [
                str(e) for e, t in zip(
                    dim.get("elements", []),
                    _elem_types(dim),
                ) if t != "Consolidated"
            ]
            important = (leaf or elements)[:_IMPORTANT_ELEM_LIMIT]

        dim_summaries.append({
            "name": name,
            "role": role,
            "important_elements": important,
        })

    # Pull business metadata from model_profile cube_roles if present.
    cube_role_info: dict = cube_roles.get(cube_name, {})
    business_purpose: str = (
        cube_role_info.get("role", "")
        or str(schema.get("description", ""))
        or cube_name
    )
    best_for: list[str] = list(cube_role_info.get("best_for", []))
    avoid_for: list[str] = list(cube_role_info.get("avoid_for", []))

    # Default filters from profile.
    raw_defaults: dict = (model_profile or {}).get("default_filters", {})
    default_filters: dict[str, str] = {}
    # Only keep filters whose dim actually exists in this cube.
    cube_dim_names = {str(d.get("name", "")) for d in dims}
    for dim_name, value in raw_defaults.items():
        if dim_name in cube_dim_names:
            default_filters[dim_name] = str(value)

    # Infer best_for from finance_semantics if empty.
    if not best_for:
        best_for = _infer_best_for(cube_name, dim_summaries, measures)

    summary = {
        "cube": cube_name,
        "business_purpose": business_purpose,
        "grain": grain,
        "dimensions": dim_summaries,
        "measures": measures,
        "best_for": best_for,
        "avoid_for": avoid_for,
        "default_filters": default_filters,
    }
    return summary


def refresh_all_summaries(model_profile: dict | None = None) -> dict[str, dict]:
    """Regenerate and persist summaries for every cube in the schema cache.

    Returns the full {cube_name: summary} map.
    Called after schema sync completes.
    """
    try:
        cubes = get_cubes_with_descriptions()
    except Exception as exc:
        _log.warning("[cube-summary] could not list cubes: %s", exc)
        return {}

    results: dict[str, dict] = {}
    for cube_info in cubes:
        cube_name = str(cube_info.get("cube", "")).strip()
        if not cube_name:
            continue
        try:
            schema = get_cube_schema(cube_name)
            summary = generate_summary(cube_name, schema, model_profile=model_profile)
            save_cube_summary(cube_name, summary)
            results[cube_name] = summary
        except Exception as exc:
            _log.debug("[cube-summary] skipped %s: %s", cube_name, exc)

    _log.info("[cube-summary] refreshed %d cube summaries", len(results))
    return results


def get_or_generate(
    cube_name: str,
    model_profile: dict | None = None,
) -> dict:
    """Return the stored summary, generating and persisting it on cache miss."""
    stored = load_cube_summary(cube_name)
    if stored:
        return stored
    try:
        schema = get_cube_schema(cube_name)
        summary = generate_summary(cube_name, schema, model_profile=model_profile)
        save_cube_summary(cube_name, summary)
        return summary
    except Exception as exc:
        _log.debug("[cube-summary] on-demand generation failed for %s: %s", cube_name, exc)
        return {"cube": cube_name, "business_purpose": cube_name, "grain": [],
                "dimensions": [], "measures": [], "best_for": [], "avoid_for": [],
                "default_filters": {}}


# ── Internal helpers ──────────────────────────────────────────────────────────

def _elem_types(dim: dict) -> list[str]:
    """Return parallel element type list for dim['elements']."""
    raw = dim.get("element_types") or dim.get("elements_with_types") or []
    if raw and len(raw) == len(dim.get("elements", [])):
        return [str(t) for t in raw]
    # Fallback: all treated as leaf (we can't distinguish without the data).
    return ["Numeric"] * len(dim.get("elements", []))


_PNL_HINTS = {"p&l", "profit", "income", "revenue", "pnl", "financial"}
_BS_HINTS = {"balance", "sheet", "asset", "liability", "position"}
_HC_HINTS = {"headcount", "employee", "hr", "people", "workforce", "fte"}
_COST_HINTS = {"cost", "expense", "labor", "labour", "salary", "compensation"}


def _infer_best_for(
    cube_name: str,
    dim_summaries: list[dict],
    measures: list[str],
) -> list[str]:
    """Rough heuristic: infer best_for tags from cube name + measure names."""
    probe = (cube_name + " " + " ".join(measures)).lower()
    tags: list[str] = []
    if any(h in probe for h in _PNL_HINTS):
        tags.append("P&L analysis")
    if any(h in probe for h in _BS_HINTS):
        tags.append("balance sheet")
    if any(h in probe for h in _HC_HINTS):
        tags.append("headcount analysis")
    if any(h in probe for h in _COST_HINTS):
        tags.append("cost analysis")
    return tags
