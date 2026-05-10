import json
import re
from collections.abc import Iterator

import anthropic

from ..config import ANTHROPIC_API_KEY, CLAUDE_MODEL


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


def _fix_mdx_structure(mdx: str) -> str:
    """Fix WHERE appearing before FROM — swap them to the correct order."""
    from_m = re.search(r"(?i)\bFROM\s+\[", mdx)
    where_m = re.search(r"(?i)\bWHERE\s*\(", mdx)
    if not from_m or not where_m or where_m.start() > from_m.start():
        return mdx
    # Extract FROM [CubeName]
    from_clause = re.search(r"(?i)(FROM\s+\[[^\]]+\])", mdx)
    if not from_clause:
        return mdx
    fc = from_clause.group(1)
    # Remove FROM clause from its current position, insert it before WHERE
    mdx_no_from = mdx[: from_clause.start()] + mdx[from_clause.end():]
    where_in_stripped = re.search(r"(?i)\bWHERE\s*\(", mdx_no_from)
    if not where_in_stripped:
        return mdx
    pos = where_in_stripped.start()
    return mdx_no_from[:pos].rstrip() + "\n" + fc + "\n" + mdx_no_from[pos:]



def generate_cube_mdx(
    question: str,
    cube_schema: dict,
    history: list[dict] | None = None,
    similar_queries: list[dict] | None = None,
) -> str:
    """Generate an MDX SELECT statement for a cube using the cube schema and user question."""
    cube_name = cube_schema.get("cube", "")
    dims_json = json.dumps(cube_schema.get("dimensions", []), indent=2, ensure_ascii=False)

    # Few-shot examples from RAG history
    examples_section = ""
    if similar_queries:
        examples = "\n\n".join(
            f'Question: "{sq["question"]}"\nCube: {sq["cube"]}\nMDX:\n{sq["mdx"]}'
            for sq in similar_queries
        )
        examples_section = f"\nPast successful queries for structural reference (reuse axis layout and CrossJoin patterns only — always verify element names against the schema above):\n{examples}\n"

    prompt = f"""You are an expert TM1 / IBM Planning Analytics MDX query writer.

Cube: {cube_name}

Dimensions and their available elements:
{dims_json}
{examples_section}
User question: "{question}"

Previous conversation:
{_conversation_context(history)}

Write ONE MDX SELECT statement that answers the question.

REQUIRED structure — follow exactly:
  SELECT {{[TimeDim].[TimeDim].Members}} ON COLUMNS,
         {{[Dim1].[Dim1].Members}} * {{[Dim2].[Dim2].Members}} ON ROWS
  FROM [{cube_name}]
  WHERE ([Year].[Year].[2025], [Scenario].[Scenario].[ACT], [Measure].[Measure].[Total])

Hard rules:
1. FROM [{cube_name}] must come immediately after the axes — always BEFORE WHERE
2. WHERE accepts ONLY single members [Dim].[Dim].[Element]
   — never .Members, never {{set expressions}}
3. A dimension must appear on exactly ONE of: COLUMNS, ROWS, or WHERE — never in two places, never omitted
4. ALL dimensions not on COLUMNS or ROWS MUST appear in WHERE — include every remaining dimension with one element
5. A 4-digit year (e.g. 2025) ALWAYS goes in the dimension whose name contains "Year" or "Period" — NEVER Employee, Scenario, etc.
6. For each WHERE dimension the user did NOT specifically filter, use the consolidated element (e.g. "All Employees", "All Cost Centers", "Total") — NEVER a leaf or numeric ID like "1"
7. Trend/time questions  → time/month dimension on COLUMNS; breakdown dims on ROWS; Year + Scenario + measure in WHERE
8. Snapshot questions    → measure dimension (is_measure: true) on COLUMNS; breakdown on ROWS; all other dims in WHERE
9. Single ROWS dimension : {{[Dim].[Dim].Members}} ON ROWS
10. Multiple ROWS dimensions: {{[Dim1].[Dim1].Members}} * {{[Dim2].[Dim2].Members}} ON ROWS
    — use the * operator for cross product; NEVER use CrossJoin() function (causes rte 45 in TM1)
11. Use ONLY element names from the lists above; match case-insensitively to the exact entry
12. CRITICAL: verify which dimension each element belongs to before writing it — wrong dimension = hard error
13. Reply with ONLY the raw MDX — no markdown, no comments, nothing else"""

    try:
        response = _client().messages.create(
            model=CLAUDE_MODEL,
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}],
        )
        mdx = response.content[0].text.strip()
        mdx = re.sub(r"```(?:mdx|sql|)\n?", "", mdx).strip("` \n")
        mdx = _fix_mdx_structure(mdx)
        mdx = " ".join(mdx.split())  # collapse all whitespace / newlines to single spaces
        print(f"[MDX] {cube_schema.get('cube')} | {mdx}", flush=True)
        return mdx
    except Exception as exc:
        raise RuntimeError(f"MDX generation error: {exc}") from exc


def select_cubes(question: str, cubes: list[dict], history: list[dict] | None = None) -> dict:
    prompt = f"""You are an expert TM1 / IBM Planning Analytics consultant.

Available cubes:
{json.dumps(cubes, indent=2, ensure_ascii=False)}

User question: "{question}"

Previous conversation:
{_conversation_context(history)}

Select 2 to 3 cubes: the best-matching primary source first, then 1-2 alternatives as fallbacks.
Alternatives are essential — if the primary cube returns no data, the system will automatically try the next one.
Choose alternatives that cover the same topic from a different angle.
Reply ONLY with valid JSON - no markdown, no extra text:
{{
  "cubes": [
    {{
      "cube": "<exact cube name from the list>",
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
        raise RuntimeError(f"AI cube selection parsing failed. Raw: {raw}") from exc
    except Exception as exc:
        raise RuntimeError(f"AI cube selection error: {exc}") from exc


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


def _analysis_prompt(
    question: str,
    sources: list[dict],
    skipped_sources: list[dict],
    history: list[dict] | None,
) -> str:
    return f"""You are a senior financial analyst reviewing IBM Planning Analytics data.

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

Use specific numbers from the data. Keep it readable — no bullet-point spam.

After the analysis, on a new line output exactly this and nothing else:
SUGGESTIONS: ["<follow-up question 1>", "<follow-up question 2>", "<follow-up question 3>"]"""


def _parse_suggestions(text: str) -> tuple[str, list[str]]:
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


def stream_financial_analysis(
    question: str,
    sources: list[dict],
    skipped_sources: list[dict] | None = None,
    history: list[dict] | None = None,
) -> Iterator[str]:
    """Sync generator: yields text chunks from the Claude streaming API."""
    prompt = _analysis_prompt(question, sources, skipped_sources or [], history)
    try:
        with _client().messages.stream(
            model=CLAUDE_MODEL,
            max_tokens=1800,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            for text in stream.text_stream:
                yield text
    except Exception as exc:
        raise RuntimeError(f"AI analysis error: {exc}") from exc
