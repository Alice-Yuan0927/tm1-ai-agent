"""Stream the financial narrative + parse trailing SUGGESTIONS JSON."""

import json
import logging
from collections.abc import Iterator

from ...config import ANALYSIS_MAX_TOKENS, get_llm_temperature
from ...util.llm_json import parse_llm_json
from .conversation import conversation_context
from ..prompts.prompt_rules import GENERAL_AGENT_CONTRACT
from ..providers import complete_text, stream_text

_log = logging.getLogger(__name__)


def _analysis_prompt(
    question: str,
    sources: list[dict],
    skipped_sources: list[dict],
    history: list[dict] | None,
) -> str:
    return f"""You are a senior financial analyst reviewing IBM Planning Analytics data.

{GENERAL_AGENT_CONTRACT}

User question: "{question}"

Previous conversation:
{conversation_context(history)}

Data sources:
{json.dumps(sources, indent=2, ensure_ascii=False)}

Skipped sources with no data or errors:
{json.dumps(skipped_sources, indent=2, ensure_ascii=False)}

Write a concise financial analysis that:
1. Directly answers the user question using all useful sources
2. Calls out key figures, trends, and variances
3. Mentions when a selected source had no usable data only if relevant

Use specific numbers from the data. For monetary measures where the exact
currency is not specified, use a generic dollar-style symbol like $5,510 and do
not name a specific currency such as euro, AUD, or USD unless the source data,
filters, cube name, or measure name explicitly states it. Keep it readable -
no bullet-point spam.

After the analysis, on a new line output exactly this and nothing else:
SUGGESTIONS: ["<follow-up question 1>", "<follow-up question 2>", "<follow-up question 3>"]"""


def stream_financial_analysis(
    question: str,
    sources: list[dict],
    skipped_sources: list[dict] | None = None,
    history: list[dict] | None = None,
) -> Iterator[str]:
    """Sync generator yielding text chunks from the LLM streaming API."""
    prompt = _analysis_prompt(question, sources, skipped_sources or [], history)
    try:
        yield from stream_text(
            prompt,
            max_tokens=ANALYSIS_MAX_TOKENS,
            temperature=get_llm_temperature("analysis_temperature"),
        )
    except Exception as exc:
        raise RuntimeError(f"AI analysis error: {exc}") from exc


def parse_suggestions(text: str) -> tuple[str, list[str]]:
    """Split analysis text from the trailing SUGGESTIONS JSON array."""
    marker = "SUGGESTIONS:"
    if marker not in text:
        return text.strip(), []
    parts = text.rsplit(marker, 1)
    analysis = parts[0].strip()
    try:
        suggestions = json.loads(parts[1].strip())
        if isinstance(suggestions, list):
            return analysis, [str(s) for s in suggestions[:3]]
    except (json.JSONDecodeError, IndexError):
        pass
    return text.strip(), []


def generate_homepage_suggestions(cubes: list[dict]) -> list[str]:
    """Three short suggested questions tailored to the connected TM1 model."""
    cube_summary = json.dumps(
        [{"cube": c.get("cube", ""), "description": c.get("description", "")} for c in cubes[:20]],
        ensure_ascii=False,
    )
    prompt = f"""You are a financial analyst assistant connected to an IBM Planning Analytics (TM1) model.

Available cubes:
{cube_summary}

Generate exactly 3 short, specific suggested questions a business user would ask about this data.
Each question should reference concepts visible in the cube names or descriptions.
Keep each question under 10 words - concise enough to fit in a button label.

Reply with ONLY a JSON array of 3 strings, no markdown, no extra text:
["<question 1>", "<question 2>", "<question 3>"]"""

    try:
        raw = complete_text(
            prompt,
            max_tokens=200,
            temperature=get_llm_temperature("suggestions_temperature"),
        )
        result = parse_llm_json(raw)
        if isinstance(result, list) and len(result) >= 3:
            return [str(s) for s in result[:3]]
    except Exception as exc:
        _log.warning("homepage suggestion generation failed: %s", exc)
    names = [c.get("cube", "") for c in cubes[:3] if c.get("cube")]
    if len(names) >= 2:
        return [
            f"Show me data from {names[0]}",
            f"Summarize {names[1]}",
            f"What are the key figures in {names[min(2, len(names) - 1)]}?",
        ]
    return [
        "What data is available in this model?",
        "Show me an overview of the cubes",
        "What are the key metrics here?",
    ]
