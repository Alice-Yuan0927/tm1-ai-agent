import json
import logging
import re
from pathlib import Path

from fastapi import HTTPException

_log = logging.getLogger(__name__)

from ..ai.output.semantic_profile import generate_semantic_profile
from ..ai.schema.dim_roles import build_dim_roles_map
from ..ai.schema.finance_semantics import build_finance_semantic_profile
from ..config import (
    CUBE_CONTEXT_ATTR_LIMIT,
    CUBE_CONTEXT_ELEMENTS_PER_DIM,
    CUBE_CONTEXT_MEASURE_LIMIT,
    get_tm1_config,
)
from ..semantic.store import load_all_summaries
from ..tm1.cache import get_current_period_defaults
from ..tm1.service import get_cube_schema, get_cubes_with_descriptions
from ..util.io import atomic_write_json

_PROFILE_DIR = Path(__file__).parent.parent / "model_profiles"


def model_profile_id() -> str:
    config = get_tm1_config()
    address = str(config.get("address", "tm1")).strip() or "tm1"
    port = str(config.get("port", "")).strip()
    raw = f"{address}_{port}" if port else address
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("_") or "default"


def model_profile_path() -> Path:
    return _PROFILE_DIR / f"{model_profile_id()}.json"


def load_current_model_profile() -> dict | None:
    for path in (model_profile_path(), _PROFILE_DIR / "default.json"):
        if not path.exists():
            continue
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
    return None


def save_current_model_profile(profile: dict) -> Path:
    _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    config = get_tm1_config()
    profile = {
        **profile,
        "model_id": model_profile_id(),
        "tm1_connection": {
            "address": config.get("address", ""),
            "port": config.get("port", ""),
        },
    }
    path = model_profile_path()
    atomic_write_json(path, profile)
    return path


def cube_context_for_selection(cubes: list[dict], model_profile: dict | None = None) -> list[dict]:
    """Return compact cube context for cube selection.

    Fast path: serves pre-computed summaries from cube_summaries (one SQLite
    query for all cubes).  Falls back to live schema enrichment for any cube
    not yet summarised, preserving the old behaviour during the migration period.
    """
    stored_summaries = load_all_summaries()
    cube_roles: dict = (model_profile or {}).get("cube_roles", {})
    enriched: list[dict] = []

    for cube in cubes:
        cube_name = str(cube.get("cube", "")).strip()
        item = dict(cube)

        summary = stored_summaries.get(cube_name)
        if summary:
            # Serve from pre-computed summary — no schema read needed.
            item["business_purpose"] = summary.get("business_purpose", "")
            item["grain"] = summary.get("grain", [])
            # Expose dimension names + important elements in the same shape the
            # LLM prompt expects (list of names + element map).
            dim_list = summary.get("dimensions", [])
            item["dimensions"] = [d["name"] for d in dim_list if d.get("name")]
            item["dimension_elements"] = {
                d["name"]: d.get("important_elements", [])
                for d in dim_list
                if d.get("name")
            }
            item["measures"] = summary.get("measures", [])
            item["best_for"] = summary.get("best_for", [])
            item["avoid_for"] = summary.get("avoid_for", [])
            if summary.get("default_filters"):
                item["default_filters"] = summary["default_filters"]
        else:
            # Fallback: enrich from live schema (old behaviour).
            _log.debug("[cube-context] no summary for %s, reading schema", cube_name)
            try:
                schema = get_cube_schema(cube_name)
                dimensions = schema.get("dimensions", [])
                item["dimensions"] = [
                    d.get("name", "")
                    for d in dimensions
                    if d.get("name") and not d.get("is_measure")
                ]
                item["dimension_elements"] = {
                    d.get("name", ""): list(d.get("elements", []))[:CUBE_CONTEXT_ELEMENTS_PER_DIM]
                    for d in dimensions
                    if d.get("name") and not d.get("is_measure")
                }
                measure_dim = next((d for d in dimensions if d.get("is_measure")), None)
                if measure_dim:
                    item["measure_dimension"] = measure_dim.get("name", "")
                    item["measures"] = list(measure_dim.get("elements", []))[:CUBE_CONTEXT_MEASURE_LIMIT]
                attr_names = sorted({
                    a.get("name", "")
                    for d in dimensions
                    for a in d.get("attributes", [])
                    if a.get("name")
                })
                if attr_names:
                    item["attributes"] = attr_names[:CUBE_CONTEXT_ATTR_LIMIT]
            except Exception as exc:
                _log.debug("cube_context enrichment failed for %s: %s", cube_name, exc)

        # Profile overrides always win (hand-tuned values take precedence).
        if cube_name in cube_roles:
            role_info = cube_roles[cube_name]
            if role_info.get("role"):
                item["business_purpose"] = role_info["role"]
            if role_info.get("best_for"):
                item["best_for"] = role_info["best_for"]
            if role_info.get("avoid_for"):
                item["avoid_for"] = role_info["avoid_for"]

        enriched.append(item)
    return enriched


def schema_summary_for_profile() -> dict:
    cubes = get_cubes_with_descriptions()
    config = get_tm1_config()
    return {
        "tm1_connection": {
            "address": config.get("address", ""),
            "port": config.get("port", ""),
        },
        "current_period_defaults": get_current_period_defaults(),
        "cubes": cube_context_for_selection(cubes),
    }


def fallback_semantic_profile(schema_summary: dict, error_message: str) -> dict:
    cubes = list(schema_summary.get("cubes") or [])
    return {
        "profile_version": 1,
        "model_name": "generated_fallback",
        "source": "deterministic_fallback_after_ai_profile_error",
        "profile_generation_warning": error_message[:800],
        "business_terms": {},
        "metric_mappings": {},
        "cube_roles": {
            str(cube.get("cube", "")): {
                "role": str(cube.get("description", "")) or str(cube.get("cube", "")),
                "best_for": [],
                "avoid_for": [],
            }
            for cube in cubes[:20]
            if cube.get("cube")
        },
        "default_filters": {},
        "selection_guidance": [
            "Fallback profile generated because AI semantic profile generation failed.",
            "Use cube names, dimension names, and finance_semantics for routing.",
        ],
        "chart_guidance": {
            "percentage_terms": ["percent", "percentage", "pct", "share", "ratio", "rate"],
            "split_percentage_measures": True,
            "prefer_share_chart": "doughnut",
            "prefer_absolute_chart": "bar",
        },
    }


def generate_current_model_profile_response() -> dict:
    try:
        schema_summary = schema_summary_for_profile()
        try:
            profile = generate_semantic_profile(schema_summary)
        except Exception as profile_exc:
            profile = fallback_semantic_profile(schema_summary, str(profile_exc))
        profile["finance_semantics"] = build_finance_semantic_profile(schema_summary)

        existing_profile = load_current_model_profile() or {}
        existing_filters = dict(existing_profile.get("default_filters") or {})
        profile["default_filters"] = {
            **dict(profile.get("default_filters") or {}),
            **get_current_period_defaults(),
            **existing_filters,
        }
        auto_roles = build_dim_roles_map(schema_summary)
        existing_roles = dict(existing_profile.get("dim_roles") or {})
        profile["dim_roles"] = {**auto_roles, **existing_roles}
        guidance = list(profile.get("selection_guidance") or [])
        guidance.append(
            "For financial statement questions, use finance_semantics to choose "
            "the matched cube and put the mapped line_item_dimension on ROWS."
        )
        profile["selection_guidance"] = guidance
        path = save_current_model_profile(profile)
    except Exception as exc:
        raise HTTPException(500, f"Semantic profile generation failed: {exc}") from exc

    return {
        "success": True,
        "profile": profile,
        "profile_file": path.name,
        "warning": profile.get("profile_generation_warning", ""),
        "fallback": profile.get("source") == "deterministic_fallback_after_ai_profile_error",
    }
