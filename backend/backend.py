"""
TM1 AI Financial Analyst backend.

Usage:
    pip install -r backend/requirements.txt
    uvicorn backend.backend:app --reload --port 8000
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .ai_service import (
    find_clarifications,
    is_unclear_question,
    select_views,
    write_multi_source_financial_analysis,
)
from .config import CLAUDE_MODEL, FRONTEND_DIR
from .email_service import send_analysis_email
from .schemas import EmailRequest, QuestionRequest
from .tm1_service import build_structured_preview, execute_view_safe, get_views_from_apq

app = FastAPI(title="TM1 AI Analyst")

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


@app.get("/api/views")
def list_views():
    try:
        views = get_views_from_apq()
    except RuntimeError as exc:
        raise HTTPException(500, str(exc)) from exc
    return {"count": len(views), "views": views}


@app.post("/api/analyze")
def analyze(req: QuestionRequest):
    question = req.question.strip()
    if is_unclear_question(question):
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
        views = get_views_from_apq()
    except RuntimeError as exc:
        raise HTTPException(500, str(exc)) from exc

    if not views:
        raise HTTPException(
            404,
            "No views with descriptions found. "
            "Please fill in the Description column in }APQ Cube Views.",
        )

    try:
        selection = select_views(question, views, req.history)
    except RuntimeError as exc:
        raise HTTPException(500, str(exc)) from exc

    selected_views = selection.get("views") or []
    reasoning = selection.get("reasoning", "")

    if not selected_views:
        raise HTTPException(500, "AI did not return a valid cube/view")

    sources = []
    skipped_sources = []
    seen = set()
    for selected in selected_views[:3]:
        cube = str(selected.get("cube", "")).strip()
        view = str(selected.get("view", "")).strip()
        source_reasoning = str(selected.get("reasoning", "")).strip()
        if not cube or not view or (cube, view) in seen:
            continue
        seen.add((cube, view))

        try:
            rows, layout = execute_view_safe(cube, view, question)
        except Exception as exc:
            skipped_sources.append({
                "cube": cube,
                "view": view,
                "reasoning": source_reasoning,
                "status": f"error: {exc}",
            })
            continue

        if not rows:
            skipped_sources.append({
                "cube": cube,
                "view": view,
                "reasoning": source_reasoning,
                "status": "no data",
            })
            continue

        sources.append({
            "cube": cube,
            "view": view,
            "reasoning": source_reasoning,
            "data_row_count": len(rows),
            "data_preview": rows[:12],
            "structured_preview": build_structured_preview(rows, layout),
            "applied_filters": layout.get("applied_filters", []),
            "analysis_rows": rows,
        })

    if not sources:
        skipped_text = "; ".join(
            f"{item['cube']} / {item['view']} ({item['status']})"
            for item in skipped_sources
        )
        raise HTTPException(404, f"No selected TM1 views returned usable data. Tried: {skipped_text}")

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
