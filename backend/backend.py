"""
TM1 AI Financial Analyst backend.

Usage:
    pip install -r backend/requirements.txt
    uvicorn backend.backend:app --reload --port 8000
"""

import json as _json
import re
import threading
import time as _time
from pathlib import Path
from typing import Any, cast

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse

from .ai import (
    PLAN_SURFACE_THRESHOLD,
    _parse_suggestions,
    detect_attribute_intent,
    find_clarifications,
    find_schema_clarification,
    generate_cube_mdx,
    generate_homepage_suggestions,
    generate_semantic_profile,
    init_db,
    is_unclear_question,
    retrieve_similar,
    repair_cube_mdx,
    save_query,
    select_cubes_with_profile,
    stream_financial_analysis,
    try_plan_mdx,
)
from .tm1.cache import find_question_element_matches
from .ai.service import validate_generated_mdx
from .config import AI_MAX_ROWS, get_llm_model, FRONTEND_DIR, TM1_CONFIG
from .config import public_tm1_config, update_tm1_config
from .llm_models import load_llm_model_catalog, refresh_llm_model_catalog, validate_llm_selection
from .ai.result_validators import (
    result_shape_issue as validate_result_shape,
    specific_focus_issue as validate_specific_focus,
    statement_line_item_issue as validate_statement_line_items,
    default_filter_issue as validate_default_filter,
    account_dim_choice_issue as validate_account_dim_choice,
)
from .ai.finance_semantics import build_finance_semantic_profile
from .email_service import send_analysis_email
from .schemas import EmailRequest, QuestionRequest, TM1ConfigRequest
from .response_messages import no_usable_data_message
from .tm1 import (
    build_structured_preview,
    execute_generated_mdx,
    get_alias_attribute_names,
    get_alias_maps,
    get_current_period_defaults,
    get_cube_schema,
    get_cubes_with_descriptions,
    get_dim_hierarchy_edges,
    get_dim_metadata,
    get_last_synced_at,
    get_named_attribute_map,
    lookup_element_dim,
    init_schema_db,
    sync_schema,
)
from .tm1 import is_empty as _schema_is_empty


app = FastAPI(title="TM1 AI Analyst")
_PROFILE_DIR = Path(__file__).parent / "model_profiles"
_MAX_MDX_ATTEMPTS = 3
_CONNECTION_ERROR_PATTERNS = (
    "connection refused",
    "failed to establish",
    "max retries exceeded",
    "name or service not known",
    "nodename nor servname",
    "timed out",
    "timeout",
    "unauthorized",
    "authentication",
    "certificate",
    "ssl",
)

# ── Suggested questions cache ─────────────────────────────────────────────────
# Generated once by the AI provider on first request and held in memory.
# Cleared whenever sync-schema runs so suggestions stay current after a model change.
_suggestions_cache: list[str] | None = None

# ── TM1 health state ──────────────────────────────────────────────────────────
# Status is updated passively whenever a real TM1 API call succeeds or fails.
# The server name is probed on first health check and whenever the connection
# settings change, so the UI follows the active TM1 instance.
_tm1_health_lock = threading.Lock()
_tm1_health: dict = {"status": "unknown", "name": "", "ts": 0.0}


def _tm1_fallback_name() -> str:
    address = TM1_CONFIG.get("address", "")
    port = TM1_CONFIG.get("port", "")
    return f"{address}:{port}" if port else str(address)


def _probe_tm1_name() -> tuple[str, str]:
    """Probe the active TM1 connection and return status plus display name."""
    from TM1py import TM1Service
    fallback = _tm1_fallback_name()
    try:
        with TM1Service(**TM1_CONFIG) as tm1:
            name = tm1.server.get_server_name() or fallback
        return "up", name
    except Exception:
        return "down", fallback


def _update_tm1_health(status: str, name: str | None = None) -> None:
    """Passively update TM1 status, preserving the display name unless given."""
    global _tm1_health
    with _tm1_health_lock:
        next_health = {**_tm1_health, "status": status, "ts": _time.monotonic()}
        if name is not None:
            next_health["name"] = name
        elif not next_health.get("name"):
            next_health["name"] = _tm1_fallback_name()
        _tm1_health = next_health


@app.on_event("startup")
def _startup() -> None:
    init_db()
    init_schema_db()
    if _schema_is_empty():
        try:
            sync_schema()
        except Exception:
            pass  # TM1 might not be reachable yet; cache will fill on first /api/sync-schema

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def index():
    return FileResponse(
        FRONTEND_DIR / "frontend.html",
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/{asset_name}")
def frontend_asset(asset_name: str):
    allowed_assets = {
        "frontend.tailwind.js",
        "config.js", "markdown.js", "charts.js", "table.js",
        "store.js", "share.js", "ui.js", "sidebar.js",
        "cubes.js", "render.js", "api.js", "main.js",
    }
    if asset_name not in allowed_assets:
        raise HTTPException(404, "Not found")
    return FileResponse(
        FRONTEND_DIR / "js" / asset_name,
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/js/{asset_name}")
def frontend_js_asset(asset_name: str):
    allowed_assets = {
        "frontend.tailwind.js",
        "config.js", "markdown.js", "charts.js", "table.js",
        "store.js", "share.js", "ui.js", "sidebar.js",
        "cubes.js", "render.js", "api.js", "main.js",
    }
    if asset_name not in allowed_assets:
        raise HTTPException(404, "Not found")
    return FileResponse(
        FRONTEND_DIR / "js" / asset_name,
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/js/vendor/{asset_name}")
def frontend_vendor_js_asset(asset_name: str):
    allowed_assets = {"chartjs-plugin-datalabels.min.js"}
    if asset_name not in allowed_assets:
        raise HTTPException(404, "Not found")
    return FileResponse(
        FRONTEND_DIR / "js" / "vendor" / asset_name,
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/assets/{asset_name}")
def frontend_image_asset(asset_name: str):
    allowed_assets = {"logo.svg", "block.png", "block2.png", "blockchain.png"}
    if asset_name not in allowed_assets:
        raise HTTPException(404, "Not found")
    return FileResponse(
        FRONTEND_DIR / "assets" / asset_name,
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/api/health")
def health():
    global _tm1_health
    with _tm1_health_lock:
        needs_probe = _tm1_health["status"] == "unknown"
        if needs_probe:
            # Mark as probing so concurrent requests skip the probe
            _tm1_health = {**_tm1_health, "status": "checking"}

    if needs_probe:
        status, name = _probe_tm1_name()
        with _tm1_health_lock:
            _tm1_health = {"status": status, "name": name, "ts": _time.monotonic()}

    with _tm1_health_lock:
        cached = dict(_tm1_health)

    return {
        "status": "ok",
        "model": get_llm_model(),
        "llm_warnings": validate_llm_selection(),
        "tm1_status":    cached["status"],
        "tm1_name":      cached["name"],
        "last_synced_at": get_last_synced_at(),
    }


@app.post("/api/sync-schema")
def sync_schema_endpoint():
    """Re-sync the TM1 schema cache (cubes, dimensions, elements)."""
    global _suggestions_cache
    model_catalog, llm_warnings = refresh_llm_model_catalog()
    try:
        summary = sync_schema()
    except Exception as exc:
        _update_tm1_health("down")
        raise HTTPException(500, f"Schema sync failed: {exc}") from exc
    _update_tm1_health("up")
    _suggestions_cache = None  # invalidate so next /api/suggestions regenerates
    return {
        "success": True,
        "last_synced_at": get_last_synced_at(),
        "llm_model_catalog": model_catalog,
        "llm_warnings": llm_warnings,
        **summary,
    }


@app.get("/api/tm1-config")
def get_tm1_config():
    return {
        **public_tm1_config(),
        "llm_model_catalog": load_llm_model_catalog(),
        "llm_warnings": validate_llm_selection(),
    }


@app.post("/api/llm-models/refresh")
def refresh_llm_models(payload: dict[str, Any] = Body(default_factory=dict)):
    """Re-pull the provider's model list without touching TM1 config / cube sync."""
    provider = str(payload.get("provider", "")).strip() or None
    model = payload.get("model")
    model = str(model).strip() if model is not None else None
    try:
        catalog, warnings = refresh_llm_model_catalog(provider, model)
    except Exception as exc:
        raise HTTPException(500, f"Model refresh failed: {exc}") from exc
    provider_key = (provider or "").lower() or None
    provider_entry = catalog.get(provider_key) if provider_key else None
    return {
        "success": True,
        "provider": provider_key,
        "models": list((provider_entry or {}).get("models") or []),
        "source_type": (provider_entry or {}).get("source_type", ""),
        "refreshed_at": (provider_entry or {}).get("refreshed_at", ""),
        "llm_model_catalog": catalog,
        "llm_warnings": warnings,
    }


@app.post("/api/tm1-config")
def save_tm1_config(req: TM1ConfigRequest):
    global _suggestions_cache, _tm1_health
    try:
        config = update_tm1_config(req.dict())
        model_catalog, llm_warnings = refresh_llm_model_catalog(
            req.llm_provider,
            req.llm_model,
        )
        with _tm1_health_lock:
            _tm1_health = {
                "status": "checking",
                "name": _tm1_fallback_name(),
                "ts": _time.monotonic(),
            }
        summary = sync_schema()
        tm1_status, tm1_name = _probe_tm1_name()
    except Exception as exc:
        _update_tm1_health("down")
        raise HTTPException(500, f"TM1 configuration save failed: {exc}") from exc

    _update_tm1_health(tm1_status, tm1_name)
    _suggestions_cache = None
    return {
        "success": True,
        "config": config,
        "tm1_status": tm1_status,
        "tm1_name": tm1_name,
        "last_synced_at": get_last_synced_at(),
        "llm_model_catalog": model_catalog,
        "llm_warnings": llm_warnings,
        **summary,
    }


@app.post("/api/model-profile/generate")
def generate_model_profile_endpoint():
    try:
        schema_summary = _schema_summary_for_profile()
        try:
            profile = generate_semantic_profile(schema_summary)
        except Exception as profile_exc:
            profile = _fallback_semantic_profile(schema_summary, str(profile_exc))
        profile["finance_semantics"] = build_finance_semantic_profile(schema_summary)
        # Preserve user-pinned default_filters across regeneration. We layer
        # in this order so user pins win over fresh auto-detection:
        #   1. auto-detected from the new schema (newly-generated profile)
        #   2. current period defaults from TM1 sys parameter
        #   3. existing profile's user-pinned dim-level filters (most important)
        existing_profile = _load_current_model_profile() or {}
        existing_filters = dict(existing_profile.get("default_filters") or {})
        profile["default_filters"] = {
            **dict(profile.get("default_filters") or {}),
            **get_current_period_defaults(),
            **existing_filters,
        }
        # dim_roles: similarly preserve any hand-corrected role overrides.
        auto_roles = _build_dim_roles_map(schema_summary)
        existing_roles = dict(existing_profile.get("dim_roles") or {})
        profile["dim_roles"] = {**auto_roles, **existing_roles}
        guidance = list(profile.get("selection_guidance") or [])
        guidance.append(
            "For financial statement questions, use finance_semantics to choose "
            "the matched cube and put the mapped line_item_dimension on ROWS."
        )
        profile["selection_guidance"] = guidance
        path = _save_current_model_profile(profile)
    except Exception as exc:
        raise HTTPException(500, f"Semantic profile generation failed: {exc}") from exc

    return {
        "success": True,
        "profile": profile,
        "profile_file": path.name,
        "warning": profile.get("profile_generation_warning", ""),
        "fallback": profile.get("source") == "deterministic_fallback_after_ai_profile_error",
    }


def _fallback_semantic_profile(schema_summary: dict, error_message: str) -> dict:
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


def _build_dim_roles_map(schema_summary: dict) -> dict[str, str]:
    """Classify every distinct dim in the connected model by semantic role
    (entity_subject / counterparty / business_classifier / line_item /
    scenario / time / measure / data_source / metadata / unclassified).
    The map is consumed by the planner and validators to decide which dims
    accept the user's entity-name mentions."""
    from .ai.dim_roles import classify_dim
    out: dict[str, str] = {}
    for cube in schema_summary.get("cubes") or []:
        for dim in cube.get("dimensions") or []:
            if isinstance(dim, str):
                # Schema summary sometimes flattens dims to just their names.
                # Fall back to a name-only classify.
                synthetic = {"name": dim}
                out.setdefault(dim, classify_dim(synthetic))
                continue
            name = str(dim.get("name", "")).strip() if isinstance(dim, dict) else ""
            if not name or name in out:
                continue
            out[name] = classify_dim(dim)
    return out


@app.get("/api/suggestions")
def get_suggestions():
    """Return 3 AI-generated suggested questions based on the connected TM1 cubes."""
    global _suggestions_cache
    if _suggestions_cache is not None:
        return {"suggestions": _suggestions_cache}
    try:
        cubes = get_cubes_with_descriptions()
    except RuntimeError as exc:
        _update_tm1_health("down")
        raise HTTPException(500, str(exc)) from exc
    _update_tm1_health("up")
    _suggestions_cache = generate_homepage_suggestions(cubes)
    return {"suggestions": _suggestions_cache}


@app.get("/api/views")
def list_views():
    try:
        cubes = get_cubes_with_descriptions()
    except RuntimeError as exc:
        _update_tm1_health("down")
        raise HTTPException(500, str(exc)) from exc
    _update_tm1_health("up")
    return {"count": len(cubes), "cubes": cubes}




def _source_for_ai(s: dict) -> dict:
    """Return a token-efficient representation of a source for the AI prompt.

    Uses the full pivoted preview (_full_preview) rather than raw analysis_rows.
    Raw rows include a _dimensions dict that duplicates every field value and
    can be 5–10× larger than the pivoted equivalent.
    """
    fp: dict = s.get("_full_preview") or s.get("structured_preview") or {}
    return {
        "cube":            s["cube"],
        "reasoning":       s.get("reasoning", ""),
        "data_row_count":  s["data_row_count"],
        "applied_filters": s.get("applied_filters", []),
        "data":            {**fp, "rows": fp.get("rows", [])[:AI_MAX_ROWS]},
    }


def _cube_context_for_selection(cubes: list[dict]) -> list[dict]:
    """Add compact dimension/measure context so cube selection can honor breakdowns."""
    enriched: list[dict] = []
    for cube in cubes:
        item = dict(cube)
        try:
            schema = get_cube_schema(str(cube.get("cube", "")))
            dimensions = schema.get("dimensions", [])
            item["dimensions"] = [
                d.get("name", "")
                for d in dimensions
                if d.get("name") and not d.get("is_measure")
            ]
            item["dimension_elements"] = {
                d.get("name", ""): list(d.get("elements", []))[:12]
                for d in dimensions
                if d.get("name") and not d.get("is_measure")
            }
            measure_dim = next((d for d in dimensions if d.get("is_measure")), None)
            if measure_dim:
                item["measure_dimension"] = measure_dim.get("name", "")
                item["measures"] = list(measure_dim.get("elements", []))[:25]
            attr_names = sorted({
                a.get("name", "")
                for d in dimensions
                for a in d.get("attributes", [])
                if a.get("name")
            })
            if attr_names:
                item["attributes"] = attr_names[:25]
        except Exception:
            pass
        enriched.append(item)
    return enriched


def _model_profile_id() -> str:
    address = str(TM1_CONFIG.get("address", "tm1")).strip() or "tm1"
    port = str(TM1_CONFIG.get("port", "")).strip()
    raw = f"{address}_{port}" if port else address
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("_") or "default"


def _model_profile_path() -> Path:
    return _PROFILE_DIR / f"{_model_profile_id()}.json"


def _load_current_model_profile() -> dict | None:
    for path in (_model_profile_path(), _PROFILE_DIR / "default.json"):
        if not path.exists():
            continue
        try:
            return _json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
    return None


def _save_current_model_profile(profile: dict) -> Path:
    _PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    profile = {
        **profile,
        "model_id": _model_profile_id(),
        "tm1_connection": {
            "address": TM1_CONFIG.get("address", ""),
            "port": TM1_CONFIG.get("port", ""),
        },
    }
    path = _model_profile_path()
    path.write_text(_json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return path


def _schema_summary_for_profile() -> dict:
    cubes = get_cubes_with_descriptions()
    return {
        "tm1_connection": {
            "address": TM1_CONFIG.get("address", ""),
            "port": TM1_CONFIG.get("port", ""),
        },
        "current_period_defaults": get_current_period_defaults(),
        "cubes": _cube_context_for_selection(cubes),
    }


def _explicit_employee_ids(question: str) -> list[str]:
    """Return explicit Employee element IDs referenced by number in the question."""
    if not re.search(r"\bemployees?\b", question, flags=re.IGNORECASE):
        return []

    ids: list[str] = []
    for match in re.finditer(
        r"(?:\bno\.?\s*|#\s*|\bnumber\s+)(\d+)\b",
        question,
        flags=re.IGNORECASE,
    ):
        ids.append(match.group(1))

    for match in re.finditer(
        r"\bemployees?\s+(?:no\.?\s*|#\s*|number\s+)?(\d+)\b",
        question,
        flags=re.IGNORECASE,
    ):
        ids.append(match.group(1))

    return list(dict.fromkeys(ids))


def _schema_with_explicit_employee_ids(schema: dict, question: str) -> dict:
    """
    Add explicitly requested Employee IDs to the capped schema sample.

    The full element list remains in SQLite; this only ensures the MDX prompt
    sees specific Employee elements the user named, such as "employee no.2".
    """
    requested_ids = _explicit_employee_ids(question)
    if not requested_ids:
        return schema

    dimensions = list(schema.get("dimensions", []))
    employee_dim = next((d for d in dimensions if d.get("name") == "Employee"), None)
    if not employee_dim:
        return schema

    existing = {str(e) for e in employee_dim.get("elements", [])}
    verified = [
        employee_id for employee_id in requested_ids
        if employee_id not in existing
        and lookup_element_dim(employee_id, candidate_dims=["Employee"]) == "Employee"
    ]
    if not verified:
        return schema

    patched_dims = []
    for dimension in dimensions:
        if dimension is employee_dim:
            patched_dims.append({
                **dimension,
                "elements": [*dimension.get("elements", []), *verified],
            })
        else:
            patched_dims.append(dimension)
    return {**schema, "dimensions": patched_dims}


def _execute_mdx_with_repair(
    question: str,
    cube: str,
    schema: dict,
    mdx: str,
    history: list[dict],
    similar_queries: list[dict],
    model_profile: dict | None,
) -> tuple[list[dict[str, object]], dict[str, object], str, list[str]]:
    """Execute generated MDX, asking the AI provider to repair it after TM1 errors."""
    attempts: list[str] = []
    current_mdx = mdx
    last_error = ""

    for attempt in range(1, _MAX_MDX_ATTEMPTS + 1):
        try:
            validate_generated_mdx(current_mdx, cube)
            default_issue = validate_default_filter(question, schema, current_mdx, model_profile=model_profile)
            if default_issue:
                raise RuntimeError(default_issue)
            account_choice_issue = validate_account_dim_choice(question, schema, current_mdx)
            if account_choice_issue:
                raise RuntimeError(account_choice_issue)
            rows, layout = execute_generated_mdx(cube, current_mdx)
            shape_issue = _result_shape_issue(rows, layout)
            if shape_issue:
                raise RuntimeError(shape_issue)
            focus_issue = _specific_focus_issue(question, schema, rows, layout)
            if focus_issue:
                raise RuntimeError(focus_issue)
            statement_issue = _statement_line_item_issue(question, schema, layout, model_profile=model_profile)
            if statement_issue:
                raise RuntimeError(statement_issue)
            return rows, layout, current_mdx, attempts
        except RuntimeError as exc:
            last_error = str(exc)
            attempts.append(f"attempt {attempt}: {last_error}")
            if _looks_like_connection_error(last_error):
                raise
            if attempt >= _MAX_MDX_ATTEMPTS:
                break
            try:
                current_mdx = repair_cube_mdx(
                    question,
                    schema,
                    current_mdx,
                    last_error,
                    history,
                    similar_queries=similar_queries,
                    model_profile=model_profile,
                )
            except RuntimeError as repair_exc:
                attempts.append(f"repair {attempt}: {repair_exc}")
                break

    raise RuntimeError(last_error or "MDX execution failed")


def _result_shape_issue(rows: list[dict[str, object]], layout: dict[str, object]) -> str | None:
    if not rows:
        return None

    column_dims = cast(list[str], layout.get("column_dimensions") or [])
    if not column_dims:
        return None

    try:
        dim_meta = get_dim_metadata(column_dims)
    except Exception:
        dim_meta = {}
    try:
        hierarchy_edges = get_dim_hierarchy_edges(column_dims)
    except Exception:
        hierarchy_edges = {}

    return validate_result_shape(rows, layout, dim_meta, hierarchy_edges)

def _specific_focus_issue(
    question: str,
    schema: dict,
    rows: list[dict[str, object]],
    layout: dict[str, object],
) -> str | None:
    return validate_specific_focus(question, schema, rows, layout)


def _statement_line_item_issue(
    question: str,
    schema: dict,
    layout: dict[str, object],
    model_profile: dict | None = None,
) -> str | None:
    return validate_statement_line_items(question, schema, layout, model_profile=model_profile)


def _looks_like_connection_error(message: str) -> bool:
    text = message.lower()
    return any(pattern in text for pattern in _CONNECTION_ERROR_PATTERNS)


def _analyze_sse_gen(req: QuestionRequest):
    """Sync SSE generator for /api/analyze. Yields `data: {...}\n\n` strings."""

    def evt(data: dict) -> str:
        return f"data: {_json.dumps(data, ensure_ascii=False)}\n\n"

    question = req.question.strip()

    # Fast-path: clarification (no TM1 needed)
    if not req.history and is_unclear_question(question):
        yield evt({"type": "done", "data": {
            "success": True, "type": "clarification", "question": question,
            "analysis": (
                "Looks like this may be a test message or an incomplete question. "
                "Ask me something specific about your TM1 data, such as **labor cost trends**, "
                "**headcount movement**, **salary variance**, **actuals vs budget**, "
                "or a specific cube, cost center, month, or scenario."
            ),
        }})
        return

    model_profile = _load_current_model_profile()
    clarification = find_clarifications(question, req.history, model_profile)
    if clarification:
        yield evt({"type": "done", "data": {
            "success": True, "type": "clarification",
            "question": question, "analysis": clarification,
        }})
        return

    # Pre-check: detect attribute display intent from follow-up questions.
    # Runs before cube selection so the attribute column is applied regardless
    # of which cube the AI selects.
    forced_apply_attributes: dict[str, str] = {}
    if req.history:
        prev_cube = str(req.history[-1].get("chosen_cube", "")).strip()
        if prev_cube:
            try:
                prev_schema = get_cube_schema(prev_cube)
                dim_attrs: dict[str, list[str]] = {
                    d["name"]: [
                        a["name"] for a in d.get("attributes", [])
                        if a.get("type") in ("Alias", "String")
                    ]
                    for d in prev_schema.get("dimensions", [])
                    if not d.get("is_measure") and d.get("attributes")
                }
                if dim_attrs:
                    intent = detect_attribute_intent(question, req.history, dim_attrs)
                    if intent:
                        forced_apply_attributes = {
                            intent["dim_name"]: intent["attr_name"]
                        }
            except Exception:
                pass

    # Step 1: select cubes
    yield evt({"type": "step", "step": 1})
    try:
        cubes = get_cubes_with_descriptions()
    except RuntimeError as exc:
        yield evt({"type": "error", "message": str(exc)}); return

    if req.history:
        prior = [m.get("question", "") for m in req.history]
        effective_question = " ".join(q for q in prior + [question] if q.strip())
    else:
        effective_question = question

    user_scoped = bool(req.selected_cubes)
    if user_scoped:
        wanted = {c.strip().lower() for c in req.selected_cubes if c.strip()}
        manual_cubes = [c for c in cubes if str(c.get("cube", "")).lower() in wanted]
        if not manual_cubes:
            yield evt({
                "type": "error",
                "message": "Selected cubes are no longer available in the connected TM1 model.",
                "scope_filtered": True,
            }); return
        selected_cubes = [
            {"cube": c["cube"], "reasoning": "User-selected scope"}
            for c in manual_cubes
        ]
        reasoning = f"User restricted scope to {len(selected_cubes)} cube(s)"
    else:
        cube_context = _cube_context_for_selection(cubes)

        schema_clarification = find_schema_clarification(
            question,
            cube_context,
            req.history,
            model_profile=model_profile,
        )
        if schema_clarification:
            yield evt({"type": "done", "data": {
                "success": True,
                "type": "clarification",
                "question": question,
                "analysis": schema_clarification,
            }})
            return

        try:
            selection = select_cubes_with_profile(
                question,
                cube_context,
                req.history,
                model_profile=model_profile,
            )
        except RuntimeError as exc:
            yield evt({"type": "error", "message": str(exc)}); return

        selected_cubes = selection.get("cubes") or []
        reasoning = selection.get("reasoning", "")
        if not selected_cubes:
            yield evt({"type": "error", "message": "AI did not return a valid cube selection"}); return

    # Step 2: fetch data from each cube
    yield evt({"type": "step", "step": 2})
    sources: list[dict[str, Any]] = []
    skipped_sources: list[dict[str, Any]] = []
    seen_cubes: set[str] = set()

    cube_limit = 10 if user_scoped else 3
    for selected in selected_cubes[:cube_limit]:
        cube = str(selected.get("cube", "")).strip()
        source_reasoning = str(selected.get("reasoning", "")).strip()
        if not cube or cube in seen_cubes:
            continue
        seen_cubes.add(cube)

        try:
            schema = get_cube_schema(cube)
        except RuntimeError as exc:
            skipped_sources.append({"cube": cube, "reasoning": source_reasoning, "status": f"schema error: {exc}"}); continue
        schema = _schema_with_explicit_employee_ids(schema, effective_question)

        # Use effective_question (= prior history + current) so planner and
        # MDX generation see the FULL intent. Without this, after a
        # clarification round the current question is just the answer
        # ("Actual full year") which loses the "P&L statement" keyword from
        # the original turn.
        mdx_question = effective_question
        similar = retrieve_similar(mdx_question, cube=cube)

        # Rule-based intent detection narrows each dim's top_consolidations to
        # the elements actually relevant to this question, so the planner and
        # the LLM only see a focused candidate list per dim instead of dozens
        # of unrelated category rollups.
        from .ai.query_intent import detect_query_intent, prepare_schema_for_query
        intent = detect_query_intent(mdx_question)
        focused_schema = prepare_schema_for_query(
            schema, mdx_question, intent=intent, model_profile=model_profile
        )
        print(f"[query-intent] {cube} primary={intent['primary']} sec={intent['secondaries']} conf={intent['confidence']:.2f}", flush=True)

        plan = None
        try:
            element_matches = find_question_element_matches(mdx_question)
            plan = try_plan_mdx(
                mdx_question,
                focused_schema,
                model_profile=model_profile,
                element_matches=element_matches,
            )
        except Exception as exc:  # noqa: BLE001 - planner must never break the request
            print(f"[mdx-planner] error: {exc}", flush=True)

        if plan is not None:
            mdx = plan.mdx
            print(f"[mdx-planner] {cube} pattern={plan.pattern} conf={plan.confidence:.2f}", flush=True)
        else:
            try:
                mdx = generate_cube_mdx(
                    mdx_question,
                    focused_schema,
                    req.history,
                    similar_queries=similar,
                    model_profile=model_profile,
                )
            except RuntimeError as exc:
                skipped_sources.append({
                    "cube": cube,
                    "reasoning": source_reasoning,
                    "status": f"MDX error: {exc}",
                    "generated_mdx": "",
                    "mdx_attempts": [],
                }); continue

        mdx_attempts: list[str] = []
        try:
            rows, layout, mdx, mdx_attempts = _execute_mdx_with_repair(
                mdx_question,
                cube,
                schema,
                mdx,
                req.history,
                similar,
                model_profile,
            )
        except RuntimeError as exc:
            # "Cannot execute MDX at host:port" → connection failure
            # MDX syntax errors also surface here; mark down only on clear connect failures
            if _looks_like_connection_error(str(exc)):
                _update_tm1_health("down")
            skipped_sources.append({
                "cube": cube,
                "reasoning": source_reasoning,
                "status": f"error: {exc}",
                "generated_mdx": mdx,
                "mdx_attempts": mdx_attempts,
            }); continue

        _update_tm1_health("up")

        if not rows:
            skipped_sources.append({"cube": cube, "reasoning": source_reasoning, "status": "no data", "generated_mdx": mdx}); continue

        # Look up element types / time flags for all dimensions that appear as
        # columns in this query so the frontend can draw the right chart type
        # and exclude consolidated (rollup) elements from chart axes.
        col_dims: list[str] = cast(list[str], layout.get("column_dimensions") or [])
        row_dims: list[str] = cast(list[str], layout.get("row_dimensions") or [])
        all_dims = list({d for d in (col_dims + row_dims) if d})
        try:
            dm = get_dim_metadata(all_dims) if all_dims else {}
        except Exception:
            dm = {}
        try:
            if forced_apply_attributes:
                # Attribute intent detected: insert the requested attribute as a new column
                am = {}
                col_names: dict[str, str] = {}
                for dim_name, attr_name in forced_apply_attributes.items():
                    attr_map = get_named_attribute_map(dim_name, attr_name)
                    if attr_map:
                        am[dim_name] = attr_map
                        col_names[dim_name] = attr_name
            else:
                # Default: add alias column if the dimension has one
                am = get_alias_maps(row_dims) if row_dims else {}
                attr_names = get_alias_attribute_names(list(am.keys())) if am else {}
                col_names = {d: attr_names.get(d, f"{d} Name") for d in am}
        except Exception:
            am = {}
            col_names = {}

        full_preview = build_structured_preview(
            rows, layout, limit=len(rows), dim_metadata=dm,
            alias_maps=am, apply_attributes=col_names or None,
        )
        # In normal mode: rows are the limiting axis (15 rows shown as table rows).
        # In transposed mode: columns become table rows — limit those too.
        _preview_rows = full_preview["rows"][:15]
        _preview_cols = (
            full_preview["columns"][:15]
            if full_preview.get("transpose")
            else full_preview["columns"]
        )
        limited_preview = {**full_preview, "rows": _preview_rows, "columns": _preview_cols}
        # When transposed, columns become the displayed rows — report that count.
        effective_row_count = (
            len(full_preview["columns"])
            if full_preview.get("transpose")
            else len(full_preview["rows"])
        )
        save_query(effective_question, cube, mdx, row_count=effective_row_count)
        plan_dict = None
        if plan is not None and plan.confidence < PLAN_SURFACE_THRESHOLD:
            plan_dict = plan.to_dict()
        sources.append({
            "cube": cube, "reasoning": source_reasoning,
            "data_row_count": effective_row_count,
            "data_preview": rows[:15],
            "structured_preview": limited_preview,
            "applied_filters": layout.get("applied_filters", []),
            "generated_mdx": mdx,
            "mdx_attempts": mdx_attempts,
            "analysis_rows": rows,
            "plan": plan_dict,
            "_full_preview": full_preview,   # clean pivoted data for AI; stripped before sending to frontend
        })

    if not sources:
        message, detail = no_usable_data_message(skipped_sources)
        payload = {
            "type": "error",
            "message": message,
            "detail": detail,
            "skipped": skipped_sources,
        }
        if user_scoped:
            payload["scope_filtered"] = True
            payload["scoped_cubes"] = [str(c.get("cube", "")) for c in selected_cubes]
        yield evt(payload); return

    # Step 3: signal data ready, send sources to frontend
    yield evt({"type": "step", "step": 3})
    _internal = {"analysis_rows", "_full_preview"}
    response_sources = [{k: v for k, v in s.items() if k not in _internal} for s in sources]
    yield evt({"type": "sources", "sources": response_sources, "skipped": skipped_sources, "reasoning": reasoning})

    # Build compact sources for AI analysis: pivoted structured data only, no raw cells.
    # Raw analysis_rows contain _dimensions duplicates and can be 10× larger than
    # the pivoted form; sending them to the model causes prompt-too-long errors.
    ai_sources = [_source_for_ai(s) for s in sources]

    # Stream analysis text chunk by chunk
    full_text: str = ""
    try:
        for chunk in stream_financial_analysis(question, ai_sources, skipped_sources, req.history):
            full_text += chunk
            yield evt({"type": "chunk", "text": chunk})
    except RuntimeError as exc:
        yield evt({"type": "error", "message": str(exc)}); return

    analysis, suggestions = _parse_suggestions(full_text)
    first = sources[0]
    # response_sources already excludes analysis_rows; re-add them so the
    # frontend can offer a full CSV export. store.js strips before localStorage.
    for rs, s in zip(response_sources, sources):
        rs["analysis_rows"] = s["analysis_rows"]
    yield evt({"type": "done", "data": {
        "success": True, "type": "analysis", "question": question,
        "chosen_cube": first["cube"],
        "reasoning": reasoning,
        "data_row_count": sum(s["data_row_count"] for s in sources),
        "data_preview": first["data_preview"],
        "data_sources": response_sources,
        "skipped_sources": skipped_sources,
        "analysis": analysis,
        "suggestions": suggestions,
    }})


@app.post("/api/analyze")
def analyze(req: QuestionRequest):
    return StreamingResponse(
        _analyze_sse_gen(req),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/export-excel")
def export_excel(payload: dict[str, Any] = Body(...)):
    try:
        from .excel_service import build_excel
        xlsx = build_excel(
            cube=str(payload.get("cube", "")),
            question=str(payload.get("question", "")),
            analysis_rows=list(payload.get("analysis_rows", [])),
            structured_meta=dict(payload.get("structured_preview", {})),
        )
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    filename = str(payload.get("cube", "export")).replace("/", "-") + ".xlsx"
    return Response(
        content=xlsx,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/send-email")
def send_email(req: EmailRequest):
    try:
        send_analysis_email(req)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(500, str(exc)) from exc

    return {"success": True}
