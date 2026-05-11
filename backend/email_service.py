import base64
import re
from pathlib import Path
from string import Template

import markdown
import requests

from .config import RESEND_API_KEY, RESEND_FROM
from .excel_service import build_excel
from .schemas import EmailRequest

EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
RESEND_EMAILS_URL = "https://api.resend.com/emails"

_TEMPLATES_DIR = Path(__file__).parent / "templates"


def _load_template(name: str) -> Template:
    return Template((_TEMPLATES_DIR / name).read_text(encoding="utf-8"))


def send_analysis_email(req: EmailRequest) -> None:
    if not RESEND_API_KEY:
        raise RuntimeError("Resend is not configured. Set RESEND_API_KEY.")
    if not RESEND_FROM:
        raise RuntimeError("Resend is not configured. Set RESEND_FROM.")

    if not EMAIL_PATTERN.match(req.to):
        raise ValueError("Invalid recipient email address")

    payload = {
        "from": RESEND_FROM,
        "to": [req.to],
        "subject": f"TM1 AI Analyst - {req.chosen_cube}",
        "html": _build_html_email(req),
    }
    attachments = _build_excel_attachments(req)
    if attachments:
        payload["attachments"] = attachments

    headers = {
        "Authorization": f"Bearer {RESEND_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        response = requests.post(
            RESEND_EMAILS_URL,
            headers=headers,
            json=payload,
            timeout=20,
        )
    except requests.RequestException as exc:
        raise RuntimeError(f"Resend API request failed: {exc}") from exc

    if not response.ok:
        raise RuntimeError(f"Resend API error ({response.status_code}): {response.text}")


def _safe_filename(name: str, used: set[str]) -> str:
    stem = re.sub(r'[/\\?%*:|"<>]+', "-", name).strip(" .") or "tm1-export"
    stem = stem[:80]
    filename = f"{stem}.xlsx"
    counter = 2
    while filename.lower() in used:
        filename = f"{stem}-{counter}.xlsx"
        counter += 1
    used.add(filename.lower())
    return filename


def _build_excel_attachments(req: EmailRequest) -> list[dict]:
    attachments: list[dict] = []
    used: set[str] = set()

    for source in req.excel_sources:
        cube = str(source.get("cube") or req.chosen_cube or "TM1 Export")
        rows = list(source.get("analysis_rows") or source.get("data_preview") or [])
        if not rows:
            continue

        xlsx = build_excel(
            cube=cube,
            question=str(source.get("question") or req.question or ""),
            analysis_rows=rows,
            structured_meta=dict(source.get("structured_preview") or {}),
        )
        attachments.append({
            "filename": _safe_filename(cube, used),
            "content": base64.b64encode(xlsx).decode("ascii"),
        })

    return attachments


def _build_html_email(req: EmailRequest) -> str:
    def _md(text: str) -> str:
        return markdown.markdown(text, extensions=["tables", "nl2br"])

    messages = req.history if req.history else [{"question": req.question, "analysis": req.analysis}]

    turns_html = ""
    for msg in messages:
        q = msg.get("question", "")
        a = msg.get("analysis", "")
        if not q and not a:
            continue
        turns_html += f"""
        <div class="turn">
          <div class="question">
            <span class="label">Question</span>
            <p>{q}</p>
          </div>
          <div class="answer">{_md(a)}</div>
        </div>"""

    return _load_template("email.html").substitute(
        chosen_cube=req.chosen_cube,
        data_row_count=req.data_row_count,
        turns_html=turns_html,
    )
