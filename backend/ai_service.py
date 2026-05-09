import json
import re

import anthropic

from .config import ANTHROPIC_API_KEY, CLAUDE_MODEL, MAX_DATA_ROWS


def _client() -> anthropic.Anthropic:
    if not ANTHROPIC_API_KEY:
        raise RuntimeError("ANTHROPIC_API_KEY environment variable not set")
    return anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)


def _conversation_context(history: list[dict] | None) -> str:
    if not history:
        return "No previous messages."

    items = []
    for message in history[-4:]:
        question = str(message.get("question", "")).strip()
        analysis = str(message.get("analysis", "")).strip()
        if analysis:
            analysis = analysis[:800]
        items.append(f"User: {question}\nAssistant: {analysis}")
    return "\n\n".join(items)


def select_views(question: str, views: list[dict], history: list[dict] | None = None) -> dict:
    prompt = f"""You are an expert TM1 / IBM Planning Analytics consultant.

Available cubes and views:
{json.dumps(views, indent=2, ensure_ascii=False)}

User question: "{question}"

Previous conversation:
{_conversation_context(history)}

Always select 2 to 3 cube views: the best-matching primary source first, then 1–2 alternatives as fallbacks.
Alternatives are essential — if the primary view returns no data, the system will automatically try the next one.
Choose alternatives that cover the same topic from a different angle (e.g. summary vs detail, different scenario, or a related cube).
Reply ONLY with valid JSON - no markdown, no extra text:
{{
  "views": [
    {{
      "cube": "<exact cube name from the list>",
      "view": "<exact view name from the list>",
      "reasoning": "<one concise sentence explaining why this source is useful>"
    }}
  ],
  "reasoning": "<one concise sentence explaining the overall source strategy>"
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


def select_view(question: str, views: list[dict], history: list[dict] | None = None) -> dict:
    selection = select_views(question, views, history)
    selected = selection.get("views") or []
    if not selected:
        return {}
    first = selected[0]
    return {
        "cube": first.get("cube", ""),
        "view": first.get("view", ""),
        "reasoning": first.get("reasoning") or selection.get("reasoning", ""),
    }


def is_unclear_question(question: str) -> bool:
    text = question.strip()
    if len(text) < 4:
        return True

    words = re.findall(r"[A-Za-z0-9]+", text)
    if len(words) <= 1 and len(text) < 12:
        return True

    meaningful_words = [
        word for word in words
        if len(word) >= 3 or word.isdigit()
    ]
    return not meaningful_words


def find_clarifications(question: str, history: list[dict] | None = None) -> str | None:
    """
    Checks whether the conversation is missing key TM1 query parameters.
    Accumulates all user input across history so answers from follow-up turns
    are recognised and the agent only asks for what is still missing.
    Returns a markdown clarification message or None if no clarification needed.
    """
    # Combine every user question in this conversation (oldest first, current last)
    prior_questions = [msg.get("question", "") for msg in (history or [])]
    all_user_input = " ".join(prior_questions + [question])
    combined = all_user_input.lower()

    # The original question drives the "trend" detection
    original = (prior_questions[0] if prior_questions else question).lower()

    missing = []

    # 1. Trend without time grain
    asks_for_trend = any(term in original for term in [
        "trend", "trends", "over time", "movement", "变化", "趋势",
    ])
    has_time_grain = any(term in combined for term in [
        "by month", "monthly", "month", "mtd",
        "by year", "yearly", "annual", "annually",
        "by quarter", "quarterly", "quarter", "qtr",
        "week", "weekly", "day", "daily",
        "按月", "按年", "按季度", "季度", "月份", "年度",
    ])
    if asks_for_trend and not has_time_grain:
        missing.append("**Time breakdown**: by month, by quarter, or by year?")

    # 2. Year — required unless the user asked for a year-over-year breakdown
    has_year = bool(re.search(r"\b(20\d{2}|19\d{2})\b", all_user_input))
    has_yearly_breakdown = any(term in combined for term in [
        "by year", "yearly", "annual", "annually", "year over year", "yoy", "按年", "年度",
    ])
    if not has_year and not has_yearly_breakdown:
        missing.append("**Which year?** (e.g., 2024, 2025, or a range like 2023–2025)")

    # 3. Scenario — always required for financial planning data
    scenario_terms = [
        "actual", "actuals", "act",
        "budget", "bud", "bdg",
        "forecast", "fc", "fcst", "fcast",
        "plan", "estimate",
        "实际", "预算", "预测", "计划",
    ]
    has_scenario = any(re.search(rf"\b{re.escape(term)}\b", combined) for term in scenario_terms)
    if not has_scenario:
        missing.append("**Which scenario?** (Actual, Budget, Forecast, or e.g. Actual vs Budget)")

    # 4. Month range — only ask once year is known and "by month" is confirmed
    asks_monthly = any(term in combined for term in ["by month", "monthly", "按月"])
    if asks_monthly and has_year:
        month_range_terms = [
            "q1", "q2", "q3", "q4", "h1", "h2", "ytd", "full year", "all",
            "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
            "january", "february", "march", "april", "june", "july",
            "august", "september", "october", "november", "december",
            "一月", "二月", "三月", "四月", "五月", "六月",
            "七月", "八月", "九月", "十月", "十一月", "十二月", "全年",
        ]
        has_month_range = any(term in combined for term in month_range_terms)
        if not has_month_range:
            missing.append("**Which months?** (e.g., Jan–Jun, full year, or YTD)")

    if not missing:
        return None

    # On the very first question, append optional dimension filters as a hint
    if not history:
        missing.append(
            "*(Optional)* **Filter by** cost center, grade, or employee category — "
            "leave blank to include all"
        )

    prefix = (
        "One more detail before I fetch the TM1 data:"
        if len(missing) == 1
        else "Before I pull the TM1 data, I need a few more details:"
    )
    return prefix + "\n\n" + "\n".join(f"- {part}" for part in missing)


def write_financial_analysis(
    question: str,
    chosen_cube: str,
    chosen_view: str,
    rows: list[dict],
    history: list[dict] | None = None,
) -> str:
    prompt = f"""You are a senior financial analyst reviewing IBM Planning Analytics data.

User question: "{question}"

Previous conversation:
{_conversation_context(history)}

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


def write_multi_source_financial_analysis(
    question: str,
    sources: list[dict],
    skipped_sources: list[dict] | None = None,
    history: list[dict] | None = None,
) -> str:
    skipped_sources = skipped_sources or []
    prompt = f"""You are a senior financial analyst reviewing IBM Planning Analytics data.

User question: "{question}"

Previous conversation:
{_conversation_context(history)}

Data sources:
{json.dumps(sources, indent=2, ensure_ascii=False)}

Skipped sources with no data or errors:
{json.dumps(skipped_sources, indent=2, ensure_ascii=False)}

Write a concise financial analysis that:
1. Directly answers the user question using all useful sources
2. Calls out key figures, trends, and variances
3. Mentions when a selected source had no usable data only if relevant
4. Suggests one or two follow-up questions if relevant

Use specific numbers from the data. Keep it readable - no bullet-point spam."""

    try:
        response = _client().messages.create(
            model=CLAUDE_MODEL,
            max_tokens=1800,
            messages=[{"role": "user", "content": prompt}],
        )
        return response.content[0].text.strip()
    except Exception as exc:
        raise RuntimeError(f"AI analysis error: {exc}") from exc
