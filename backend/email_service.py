import json
import re

import requests

from .config import RESEND_API_KEY, RESEND_FROM
from .schemas import EmailRequest


EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
RESEND_EMAILS_URL = "https://api.resend.com/emails"


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
        "subject": f"TM1 AI Analyst result - {req.chosen_cube} / {req.chosen_view}",
        "text": _build_email_body(req),
    }
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


def _build_email_body(req: EmailRequest) -> str:
    return f"""TM1 AI Analyst result

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
