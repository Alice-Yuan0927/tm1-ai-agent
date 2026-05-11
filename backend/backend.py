"""
TM1 AI Financial Analyst backend.

Usage:
    pip install -r backend/requirements.txt
    uvicorn backend.backend:app --reload --port 8000
"""

import json as _json
import threading
import time as _time
from typing import Any, cast

from fastapi import Body, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response, StreamingResponse

from .ai import (
    _parse_suggestions,
    find_clarifications,
    generate_cube_mdx,
    generate_homepage_suggestions,
    init_db,
    is_unclear_question,
    retrieve_similar,
    save_query,
    select_cubes,
    stream_financial_analysis,
)
from .config import AI_MAX_ROWS, CLAUDE_MODEL, FRONTEND_DIR, TM1_CONFIG
from .email_service import send_analysis_email
from .schemas import EmailRequest, QuestionRequest
from .tm1 import (
    build_structured_preview,
    execute_generated_mdx,
    get_alias_maps,
    get_cube_schema,
    get_cubes_with_descriptions,
    get_dim_metadata,
    get_last_synced_at,
    init_schema_db,
    sync_schema,
)
from .tm1 import is_empty as _schema_is_empty


app = FastAPI(title="TM1 AI Analyst")

# ── Suggested questions cache ─────────────────────────────────────────────────
# Generated once by Claude on first request and held in memory.
# Cleared whenever sync-schema runs so suggestions stay current after a model change.
_suggestions_cache: list[str] | None = None

# ── TM1 health state ──────────────────────────────────────────────────────────
# Status is updated passively whenever a real TM1 API call succeeds or fails,
# so there is zero monitoring overhead between user requests.
# A single probe fires on the first GET /api/health to get the server name and
# set an initial status; it never fires again after that.
_tm1_health_lock = threading.Lock()
_tm1_health: dict = {"status": "unknown", "name": "", "ts": 0.0}


def _probe_tm1_once() -> tuple[str, str]:
    """Called exactly once (when status is still 'unknown') to get server name."""
    from TM1py import TM1Service
    address  = TM1_CONFIG.get("address", "")
    port     = TM1_CONFIG.get("port", "")
    fallback = f"{address}:{port}"
    try:
        with TM1Service(**TM1_CONFIG) as tm1:
            name = tm1.server.get_server_name() or fallback
        return "up", name
    except Exception:
        return "down", fallback


def _update_tm1_health(status: str) -> None:
    """Passively update TM1 status. Name is preserved from the initial probe."""
    global _tm1_health
    with _tm1_health_lock:
        _tm1_health = {**_tm1_health, "status": status, "ts": _time.monotonic()}


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
        "frontend.tailwind.js", "logo.svg",
        "config.js", "markdown.js", "charts.js", "table.js",
        "store.js", "share.js", "ui.js", "sidebar.js",
        "render.js", "api.js", "main.js",
    }
    if asset_name not in allowed_assets:
        raise HTTPException(404, "Not found")
    return FileResponse(
        FRONTEND_DIR / asset_name,
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
        status, name = _probe_tm1_once()
        with _tm1_health_lock:
            _tm1_health = {"status": status, "name": name, "ts": _time.monotonic()}

    with _tm1_health_lock:
        cached = dict(_tm1_health)

    return {
        "status": "ok",
        "model": CLAUDE_MODEL,
        "tm1_status":    cached["status"],
        "tm1_name":      cached["name"],
        "last_synced_at": get_last_synced_at(),
    }


@app.post("/api/sync-schema")
def sync_schema_endpoint():
    """Re-sync the TM1 schema cache (cubes, dimensions, elements)."""
    global _suggestions_cache
    try:
        summary = sync_schema()
    except Exception as exc:
        _update_tm1_health("down")
        raise HTTPException(500, f"Schema sync failed: {exc}") from exc
    _update_tm1_health("up")
    _suggestions_cache = None  # invalidate so next /api/suggestions regenerates
    return {"success": True, "last_synced_at": get_last_synced_at(), **summary}


@app.get("/api/suggestions")
def get_suggestions():
    """Return 3 Claude-generated suggested questions based on the connected TM1 cubes."""
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

    clarification = find_clarifications(question, req.history)
    if clarification:
        yield evt({"type": "done", "data": {
            "success": True, "type": "clarification",
            "question": question, "analysis": clarification,
        }})
        return

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

    try:
        selection = select_cubes(effective_question, cubes, req.history)
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

    for selected in selected_cubes[:3]:
        cube = str(selected.get("cube", "")).strip()
        source_reasoning = str(selected.get("reasoning", "")).strip()
        if not cube or cube in seen_cubes:
            continue
        seen_cubes.add(cube)

        try:
            schema = get_cube_schema(cube)
        except RuntimeError as exc:
            skipped_sources.append({"cube": cube, "reasoning": source_reasoning, "status": f"schema error: {exc}"}); continue

        similar = retrieve_similar(effective_question, cube=cube)

        try:
            mdx = generate_cube_mdx(effective_question, schema, req.history, similar_queries=similar)
        except RuntimeError as exc:
            skipped_sources.append({"cube": cube, "reasoning": source_reasoning, "status": f"MDX error: {exc}"}); continue

        try:
            rows, layout = execute_generated_mdx(cube, mdx)
        except RuntimeError as exc:
            # "Cannot execute MDX at host:port" → connection failure
            # MDX syntax errors also surface here; mark down only on clear connect failures
            if "Cannot execute MDX at" in str(exc):
                _update_tm1_health("down")
            skipped_sources.append({"cube": cube, "reasoning": source_reasoning, "status": f"error: {exc}", "generated_mdx": mdx}); continue

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
            am = get_alias_maps(row_dims) if row_dims else {}
        except Exception:
            am = {}

        full_preview = build_structured_preview(rows, layout, limit=len(rows), dim_metadata=dm, alias_maps=am)
        # In normal mode: rows are the limiting axis (15 rows shown as table rows).
        # In transposed mode: columns become table rows — limit those too.
        _preview_rows = full_preview["rows"][:15]
        _preview_cols = (
            full_preview["columns"][:15]
            if full_preview.get("transpose")
            else full_preview["columns"]
        )
        limited_preview = {**full_preview, "rows": _preview_rows, "columns": _preview_cols}
        save_query(effective_question, cube, mdx, row_count=len(full_preview["rows"]))
        sources.append({
            "cube": cube, "reasoning": source_reasoning,
            "data_row_count": len(full_preview["rows"]),
            "data_preview": rows[:15],
            "structured_preview": limited_preview,
            "applied_filters": layout.get("applied_filters", []),
            "analysis_rows": rows,
            "_full_preview": full_preview,   # clean pivoted data for AI; stripped before sending to frontend
        })

    if not sources:
        skipped_text = "; ".join(f"{s['cube']} ({s['status']})" for s in skipped_sources)
        yield evt({"type": "error", "message": f"No usable data. Tried: {skipped_text}"}); return

    # Step 3: signal data ready, send sources to frontend
    yield evt({"type": "step", "step": 3})
    _internal = {"analysis_rows", "_full_preview"}
    response_sources = [{k: v for k, v in s.items() if k not in _internal} for s in sources]
    yield evt({"type": "sources", "sources": response_sources, "skipped": skipped_sources, "reasoning": reasoning})

    # Build compact sources for Claude — pivoted structured data only, no raw cells.
    # Raw analysis_rows contain _dimensions duplicates and can be 10× larger than
    # the pivoted form; sending them to Claude causes prompt-too-long errors.
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
