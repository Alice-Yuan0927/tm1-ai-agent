"""
TM1 AI Financial Analyst backend.

Usage:
    pip install -r backend/requirements.txt
    uvicorn backend.backend:app --reload --port 8000
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .ai_service import select_view, write_financial_analysis
from .config import CLAUDE_MODEL, FRONTEND_DIR
from .email_service import send_analysis_email
from .schemas import EmailRequest, QuestionRequest
from .tm1_service import execute_view_safe, get_views_from_apq

app = FastAPI(title="TM1 AI Analyst")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def index():
    return FileResponse(FRONTEND_DIR / "frontend.html")


@app.get("/{asset_name}")
def frontend_asset(asset_name: str):
    allowed_assets = {"frontend.js", "frontend.tailwind.js", "logo.svg"}
    if asset_name not in allowed_assets:
        raise HTTPException(404, "Not found")
    return FileResponse(FRONTEND_DIR / asset_name)


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
        selection = select_view(req.question, views)
    except RuntimeError as exc:
        raise HTTPException(500, str(exc)) from exc

    chosen_cube = selection.get("cube", "")
    chosen_view = selection.get("view", "")
    reasoning = selection.get("reasoning", "")

    if not chosen_cube or not chosen_view:
        raise HTTPException(500, "AI did not return a valid cube/view")

    try:
        rows = execute_view_safe(chosen_cube, chosen_view)
    except Exception as exc:
        raise HTTPException(500, f"TM1 view execution failed: {exc}") from exc

    if not rows:
        raise HTTPException(404, f"View '{chosen_view}' in cube '{chosen_cube}' returned no data")

    try:
        analysis = write_financial_analysis(req.question, chosen_cube, chosen_view, rows)
    except RuntimeError as exc:
        raise HTTPException(500, str(exc)) from exc

    return {
        "success": True,
        "question": req.question,
        "chosen_cube": chosen_cube,
        "chosen_view": chosen_view,
        "reasoning": reasoning,
        "data_row_count": len(rows),
        "data_preview": rows[:12],
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
