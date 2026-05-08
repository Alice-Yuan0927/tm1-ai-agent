import json
import re

import anthropic

from .config import ANTHROPIC_API_KEY, CLAUDE_MODEL, MAX_DATA_ROWS


def _client() -> anthropic.Anthropic:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable not set")
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def select_view(question: str, views: list[dict]) -> dict:
    prompt = f"""You are an expert TM1 / IBM Planning Analytics consultant.

Available cubes and views:
{json.dumps(views, indent=2, ensure_ascii=False)}

User question: "{question}"

Choose the single most relevant cube and view.
Reply ONLY with valid JSON - no markdown, no extra text:
{{
  "cube": "<exact cube name from the list>",
  "view": "<exact view name from the list>",
  "reasoning": "<one concise sentence explaining your choice>"
}}"""

    try:
        response = _client().messages.create(
            model=CLAUDE_MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = re.sub(r"```json|```", "", response.content[0].text).strip()
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"AI selection parsing failed. Raw: {raw}") from exc
    except Exception as exc:
        raise RuntimeError(f"AI selection error: {exc}") from exc


def write_financial_analysis(
    question: str,
    chosen_cube: str,
    chosen_view: str,
    rows: list[dict],
) -> str:
    prompt = f"""You are a senior financial analyst reviewing IBM Planning Analytics data.

User question: "{question}"

Data source - Cube: "{chosen_cube}"  |  View: "{chosen_view}"
Total data rows: {len(rows)}

Data (first {min(len(rows), MAX_DATA_ROWS)} rows):
{json.dumps(rows, indent=2, ensure_ascii=False)}

Write a concise financial analysis that:
1. Directly answers the user question
2. Calls out key figures, trends, and variances
3. Flags anything unusual
4. Suggests one or two follow-up questions if relevant

Use specific numbers from the data. Keep it readable - no bullet-point spam."""

    try:
        response = _client().messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception as exc:
        raise RuntimeError(f"AI analysis error: {exc}") from exc
