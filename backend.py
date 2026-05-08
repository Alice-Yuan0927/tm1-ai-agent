"""
TM1 AI Financial Analyst — Backend
FastAPI + TM1py + Claude API

Usage:
    export ANTHROPIC_API_KEY=sk-ant-...
    pip install fastapi uvicorn TM1py anthropic
    uvicorn backend:app --reload --port 8000
"""

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from TM1py import TM1Service
import anthropic
from email.message import EmailMessage
import json
import os
import re
import smtplib

app = FastAPI(title="TM1 AI Analyst")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Config — edit these ───────────────────────────────────────────────────────
TM1_CONFIG = {
    "address": "localhost",
    "port": 9510,
    "user": "admin",
    "password": "apple",
    "ssl": False,
    "async_requests_mode": False,
    "verify": False,
}

CLAUDE_MODEL = "claude-opus-4-5"
MAX_DATA_ROWS = 150   # rows sent to Claude for analysis
APQ_CUBE     = "}APQ Cube Views"
APQ_VIEW     = "Default"

# ─── Request schema ────────────────────────────────────────────────────────────
class QuestionRequest(BaseModel):
    question: str


class EmailRequest(BaseModel):
    to: str
    question: str
    chosen_cube: str
    chosen_view: str
    reasoning: str
    data_row_count: int
    data_preview: list[dict] = []
    analysis: str

# ─── TM1 helpers ──────────────────────────────────────────────────────────────
def get_views_from_apq() -> list[dict]:
    """
    Read }APQ Cube Views → return list of dicts:
        { cube, view, description }
    Only includes rows where Description is filled in.
    System cubes (} prefix or Sys prefix) are excluded.
    """
    with TM1Service(**TM1_CONFIG) as tm1:
        try:
            cellset = tm1.cells.execute_view(
                cube_name=APQ_CUBE,
                view_name=APQ_VIEW,
                private=False,
            )
        except Exception as exc:
            raise RuntimeError(f"Cannot read {APQ_CUBE}: {exc}") from exc

        # cellset keys are tuples: (apq_view_element, measure_element)
        # e.g. ("Labor Base Pay:Default", "Cube Name") → "Labor Base Pay"
        view_data: dict[str, dict] = {}
        for coords, cell in cellset.items():
            if not isinstance(coords, (list, tuple)) or len(coords) < 2:
                continue
            view_key = str(coords[0])
            measure  = str(coords[-1])
            raw      = cell.get("Value") if isinstance(cell, dict) else cell
            value    = str(raw).strip() if raw is not None else ""
            view_data.setdefault(view_key, {})[measure] = value

        views = []
        for _, data in view_data.items():
            cube = data.get("Cube Name", "").strip()
            view = data.get("View Name", "").strip()
            desc = data.get("Description", "").strip()
            if not (cube and view and desc):
                continue
            if cube.startswith("}") or cube.startswith("Sys"):
                continue
            views.append({"cube": cube, "view": view, "description": desc})

        return views


def execute_view_safe(cube_name: str, view_name: str) -> list[dict]:
    """Execute a TM1 view; return non-zero rows formatted for Claude."""
    with TM1Service(**TM1_CONFIG) as tm1:
        cellset = tm1.cells.execute_view(
            cube_name=cube_name,
            view_name=view_name,
            private=False,
        )
        rows = []
        for coords, cell in cellset.items():
            value = cell.get("Value") if isinstance(cell, dict) else cell
            if value is None or value == 0:
                continue
            row = {f"dim{i}": elem for i, elem in enumerate(coords)}
            row["value"] = value
            rows.append(row)
            if len(rows) >= MAX_DATA_ROWS:
                break
        return rows

# ─── Endpoints ─────────────────────────────────────────────────────────────────
@app.get("/api/health")
def health():
    return {"status": "ok", "model": CLAUDE_MODEL}


@app.get("/api/views")
def list_views():
    """Preview all views available to AI (useful for debugging)."""
    views = get_views_from_apq()
    return {"count": len(views), "views": views}


@app.post("/api/analyze")
def analyze(req: QuestionRequest):
    """
    Pipeline:
        1. Read }APQ Cube Views  →  view catalogue
        2. Claude selects best cube + view
        3. TM1py executes the view
        4. Claude writes financial analysis
    """
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(500, "ANTHROPIC_API_KEY environment variable not set")
    client = anthropic.Anthropic(api_key=api_key)

    # ── Step 1: catalogue ────────────────────────────────────────────────────
    try:
        views = get_views_from_apq()
    except RuntimeError as exc:
        raise HTTPException(500, str(exc))

    if not views:
        raise HTTPException(
            404,
            "No views with descriptions found. "
            "Please fill in the Description column in }APQ Cube Views."
        )

    # ── Step 2: AI selects view ──────────────────────────────────────────────
    selection_prompt = f"""You are an expert TM1 / IBM Planning Analytics consultant.

Available cubes and views:
{json.dumps(views, indent=2, ensure_ascii=False)}

User question: "{req.question}"

Choose the single most relevant cube and view.
Reply ONLY with valid JSON — no markdown, no extra text:
{{
  "cube": "<exact cube name from the list>",
  "view": "<exact view name from the list>",
  "reasoning": "<one concise sentence explaining your choice>"
}}"""

    try:
        sel_resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": selection_prompt}],
        )
        raw = re.sub(r"```json|```", "", sel_resp.content[0].text).strip()
        selection = json.loads(raw)
    except json.JSONDecodeError:
        raise HTTPException(500, f"AI selection parsing failed. Raw: {raw}")
    except Exception as exc:
        raise HTTPException(500, f"AI selection error: {exc}")

    chosen_cube = selection.get("cube", "")
    chosen_view = selection.get("view", "")
    reasoning   = selection.get("reasoning", "")

    if not chosen_cube or not chosen_view:
        raise HTTPException(500, "AI did not return a valid cube/view")

    # ── Step 3: execute view ─────────────────────────────────────────────────
    try:
        rows = execute_view_safe(chosen_cube, chosen_view)
    except Exception as exc:
        raise HTTPException(500, f"TM1 view execution failed: {exc}")

    if not rows:
        raise HTTPException(404, f"View '{chosen_view}' in cube '{chosen_cube}' returned no data")

    # ── Step 4: AI analysis ──────────────────────────────────────────────────
    analysis_prompt = f"""You are a senior financial analyst reviewing IBM Planning Analytics data.

User question: "{req.question}"

Data source — Cube: "{chosen_cube}"  |  View: "{chosen_view}"
Total data rows: {len(rows)}

Data (first {min(len(rows), MAX_DATA_ROWS)} rows):
{json.dumps(rows, indent=2, ensure_ascii=False)}

Write a concise financial analysis that:
1. Directly answers the user question
2. Calls out key figures, trends, and variances
3. Flags anything unusual
4. Suggests one or two follow-up questions if relevant

Use specific numbers from the data. Keep it readable — no bullet-point spam."""

    try:
        analysis_resp = client.messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1500,
            messages=[{"role": "user", "content": analysis_prompt}],
        )
        analysis = analysis_resp.content[0].text.strip()
    except Exception as exc:
        raise HTTPException(500, f"AI analysis error: {exc}")

    return {
        "success":       True,
        "question":      req.question,
        "chosen_cube":   chosen_cube,
        "chosen_view":   chosen_view,
        "reasoning":     reasoning,
        "data_row_count": len(rows),
        "data_preview":  rows[:12],
        "analysis":      analysis,
    }


@app.post("/api/send-email")
def send_email(req: EmailRequest):
    smtp_host = os.environ.get("SMTP_HOST", "").strip()
    try:
        smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    except ValueError:
        raise HTTPException(500, "SMTP_PORT must be a number")

    smtp_user = os.environ.get("SMTP_USER", "").strip()
    smtp_password = os.environ.get("SMTP_PASSWORD", "")
    smtp_from = os.environ.get("SMTP_FROM", smtp_user).strip()
    smtp_tls = os.environ.get("SMTP_TLS", "true").lower() != "false"

    if not smtp_host or not smtp_from:
        raise HTTPException(
            500,
            "SMTP is not configured. Set SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, and SMTP_FROM."
        )

    if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", req.to):
        raise HTTPException(400, "Invalid recipient email address")

    body = f"""TM1 AI Analyst result

Question:
{req.question}

Selected view:
Cube: {req.chosen_cube}
View: {req.chosen_view}
Rows fetched: {req.data_row_count}

Reasoning:
{req.reasoning}

Data preview:
{json.dumps(req.data_preview, indent=2, ensure_ascii=False)}

Financial analysis:
{req.analysis}
"""

    message = EmailMessage()
    message["Subject"] = f"TM1 AI Analyst result - {req.chosen_cube} / {req.chosen_view}"
    message["From"] = smtp_from
    message["To"] = req.to
    message.set_content(body)

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as smtp:
            if smtp_tls:
                smtp.starttls()
            if smtp_user and smtp_password:
                smtp.login(smtp_user, smtp_password)
            smtp.send_message(message)
    except Exception as exc:
        raise HTTPException(500, f"Email send failed: {exc}") from exc

    return {"success": True}
