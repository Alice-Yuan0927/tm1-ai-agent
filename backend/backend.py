"""
TM1 AI Financial Analyst backend.

Usage:
    pip install -r backend/requirements.txt
    uvicorn backend.backend:app --reload --port 8000
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .ai import (
    find_clarifications,
    generate_cube_mdx,
    init_db,
    is_unclear_question,
    retrieve_similar,
    save_query,
    select_cubes,
    write_multi_source_financial_analysis,
)
from .config import CLAUDE_MODEL, FRONTEND_DIR
from .email_service import send_analysis_email
from .schemas import EmailRequest, QuestionRequest
from .tm1 import (
    build_structured_preview,
    execute_generated_mdx,
    get_cube_schema,
    get_cubes_with_descriptions,
    init_schema_db,
    sync_schema,
)
from .tm1 import is_empty as _schema_is_empty


app = FastAPI(title="TM1 AI Analyst")


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
    allowed_assets = {"frontend.js", "frontend.tailwind.js", "logo.svg"}
    if asset_name not in allowed_assets:
        raise HTTPException(404, "Not found")
    return FileResponse(
        FRONTEND_DIR / asset_name,
        headers={"Cache-Control": "no-store, max-age=0"},
    )


@app.get("/api/health")
def health():
    return {"status": "ok", "model": CLAUDE_MODEL}


@app.post("/api/sync-schema")
def sync_schema_endpoint():
    """Re-sync the TM1 schema cache (cubes, dimensions, elements)."""
    try:
        summary = sync_schema()
    except Exception as exc:
        raise HTTPException(500, f"Schema sync failed: {exc}") from exc
    return {"success": True, **summary}


@app.get("/api/views")
def list_views():
    try:
        cubes = get_cubes_with_descriptions()
    except RuntimeError as exc:
        raise HTTPException(500, str(exc)) from exc
    return {"count": len(cubes), "cubes": cubes}


@app.post("/api/analyze")
def analyze(req: QuestionRequest):
    question = req.question.strip()
    if not req.history and is_unclear_question(question):
        return {
            "success": True,
            "type": "clarification",
            "question": question,
            "analysis": (
                "Looks like this may be a test message or an incomplete question. "
                "Ask me something specific about your TM1 data, such as **labor cost trends**, "
                "**headcount movement**, **salary variance**, **actuals vs budget**, "
                "or a specific cube, cost center, month, or scenario."
            ),
        }
    clarification = find_clarifications(question, req.history)
    if clarification:
        return {
            "success": True,
            "type": "clarification",
            "question": question,
            "analysis": clarification,
        }

    try:
        cubes = get_cubes_with_descriptions()
    except RuntimeError as exc:
        raise HTTPException(500, str(exc)) from exc

    # Reconstruct the full effective question from all user turns so that
    # cube selection has complete context, not just the latest follow-up answer.
    if req.history:
        prior = [msg.get("question", "") for msg in req.history]
        effective_question = " ".join(q for q in prior + [question] if q.strip())
    else:
        effective_question = question

    try:
        selection = select_cubes(effective_question, cubes, req.history)
    except RuntimeError as exc:
        raise HTTPException(500, str(exc)) from exc

    selected_cubes = selection.get("cubes") or []
    reasoning = selection.get("reasoning", "")

    if not selected_cubes:
        raise HTTPException(500, "AI did not return a valid cube selection")

    sources = []
    skipped_sources = []
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
            skipped_sources.append({
                "cube": cube, "view": "", "reasoning": source_reasoning,
                "status": f"schema error: {exc}",
            })
            continue

        similar = retrieve_similar(effective_question, cube=cube)

        try:
            mdx = generate_cube_mdx(effective_question, schema, req.history, similar_queries=similar)
        except RuntimeError as exc:
            skipped_sources.append({
                "cube": cube, "view": "", "reasoning": source_reasoning,
                "status": f"MDX generation error: {exc}",
            })
            continue

        try:
            rows, layout = execute_generated_mdx(cube, mdx)
        except RuntimeError as exc:
            skipped_sources.append({
                "cube": cube, "view": "", "reasoning": source_reasoning,
                "status": f"error: {exc}",
                "generated_mdx": mdx,
            })
            continue

        if not rows:
            skipped_sources.append({
                "cube": cube, "view": "", "reasoning": source_reasoning,
                "status": "no data",
                "generated_mdx": mdx,
            })
            continue

        # Persist this successful query for future RAG retrieval
        save_query(effective_question, cube, mdx, row_count=len(rows))

        sources.append({
            "cube": cube,
            "view": "",
            "reasoning": source_reasoning,
            "data_row_count": len(rows),
            "data_preview": rows[:15],
            "structured_preview": build_structured_preview(rows, layout, limit=15),
            "applied_filters": layout.get("applied_filters", []),
            "analysis_rows": rows,
        })

    if not sources:
        skipped_text = "; ".join(
            f"{item['cube']} / {item['view']} ({item['status']})"
            for item in skipped_sources
        )
        raise HTTPException(404, f"No selected TM1 cubes returned usable data. Tried: {skipped_text}")

    try:
        analysis = write_multi_source_financial_analysis(question, sources, skipped_sources, req.history)
    except RuntimeError as exc:
        raise HTTPException(500, str(exc)) from exc

    first_source = sources[0]
    response_sources = [
        {key: value for key, value in source.items() if key != "analysis_rows"}
        for source in sources
    ]

    return {
        "success": True,
        "type": "analysis",
        "question": question,
        "chosen_cube": first_source["cube"],
        "chosen_view": first_source["view"],
        "reasoning": reasoning,
        "data_row_count": sum(source["data_row_count"] for source in sources),
        "data_preview": first_source["data_preview"],
        "data_sources": response_sources,
        "skipped_sources": skipped_sources,
        "analysis": analysis,
    }


@app.post("/api/send-email")
def send_email(req: EmailRequest):
    try:
        send_analysis_email(req)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(500, str(exc)) from exc

    return {"success": True}
