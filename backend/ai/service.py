import json
import re
from collections.abc import Iterator
from datetime import date

import anthropic

from ..config import (
    ANALYSIS_TEMPERATURE,
    ANALYSIS_MAX_TOKENS,
    ANTHROPIC_API_KEY,
    CLAUDE_MODEL,
    CUBE_SELECT_MAX_TOKENS,
    CUBE_SELECT_TEMPERATURE,
    ATTRIBUTE_INTENT_TEMPERATURE,
    MDX_MAX_TOKENS,
    MDX_TEMPERATURE,
    SEMANTIC_PROFILE_TEMPERATURE,
    SUGGESTIONS_TEMPERATURE,
)


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
        cube = str(message.get("chosen_cube", "")).strip()
        if analysis:
            analysis = analysis[:800]
        cube_tag = f" [cube: {cube}]" if cube else ""
        items.append(f"User:{cube_tag} {question}\nAssistant: {analysis}")
    return "\n\n".join(items)


def _fix_mdx_structure(mdx: str) -> str:
    """Fix WHERE appearing before FROM Ã¢â‚¬â€ swap them to the correct order."""
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


def _fix_unrequested_time_rollup(mdx: str, question: str, model_profile: dict | None) -> str:
    if re.search(r"\b(ytd|fytd|ytg|fytg|qtd|mtd|full year|all periods)\b", question, re.I):
        return mdx
    default_month = str(
        (model_profile or {}).get("default_filters", {}).get("Month")
        or f"{date.today().month:02d}"
    )
    if not default_month:
        return mdx

    cumulative = r"(?:All\s+)?(?:FYTD|YTD|FYT?G|YTG|QTD|MTD|FYTG)"

    def repl(match: re.Match) -> str:
        dim, hier, _element = match.groups()
        return f"[{dim}].[{hier}].[{default_month}]"

    return re.sub(
        rf"\[(Month|[^\]]*Period[^\]]*|[^\]]*Time[^\]]*)\]\.\[([^\]]+)\]\.\[({cumulative})\]",
        repl,
        mdx,
        flags=re.I,
    )



def generate_cube_mdx(
    question: str,
    cube_schema: dict,
    history: list[dict] | None = None,
    similar_queries: list[dict] | None = None,
    model_profile: dict | None = None,
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
        examples_section = f"\nPast successful queries for structural reference (reuse axis layout and CrossJoin patterns only Ã¢â‚¬â€ always verify element names against the schema above):\n{examples}\n"

    profile_section = (
        f"\nSemantic model profile defaults and guidance:\n"
        f"{json.dumps(model_profile, indent=2, ensure_ascii=False)}\n"
        if model_profile else ""
    )

    prompt = f"""You are an expert TM1 / IBM Planning Analytics MDX query writer.

Cube: {cube_name}

Dimensions and their available elements:
{dims_json}
{examples_section}
{profile_section}
User question: "{question}"

Previous conversation:
{_conversation_context(history)}

Write ONE MDX SELECT statement that answers the question.

REQUIRED structure Ã¢â‚¬â€ follow exactly:
  SELECT {{[TimeDim].[TimeDim].Members}} ON COLUMNS,
         {{[Dim1].[Dim1].Members}} * {{[Dim2].[Dim2].Members}} ON ROWS
  FROM [{cube_name}]
  WHERE ([Year].[Year].[2025], [Scenario].[Scenario].[ACT], [Measure].[Measure].[Total])

Hard rules:
1. FROM [{cube_name}] must come immediately after the axes Ã¢â‚¬â€ always BEFORE WHERE
2. WHERE accepts ONLY single members [Dim].[Dim].[Element]
   Ã¢â‚¬â€ never .Members, never {{set expressions}}
3. A dimension must appear on exactly ONE of: COLUMNS, ROWS, or WHERE Ã¢â‚¬â€ never in two places, never omitted
4. ALL dimensions not on COLUMNS or ROWS MUST appear in WHERE Ã¢â‚¬â€ include every remaining dimension with one element
5. A 4-digit year (e.g. 2025) ALWAYS goes in the dimension whose name contains "Year" or "Period" Ã¢â‚¬â€ NEVER Employee, Scenario, etc.
6. For each WHERE dimension the user did NOT specifically filter, use the consolidated element (e.g. "All Employees", "All Cost Centers", "Total") Ã¢â‚¬â€ NEVER a leaf or numeric ID like "1"
7. Trend/time questions  Ã¢â€ â€™ time/month dimension on COLUMNS; breakdown dims on ROWS; Year + Scenario + measure in WHERE
8. Snapshot questions    Ã¢â€ â€™ measure dimension (is_measure: true) on COLUMNS; breakdown on ROWS; all other dims in WHERE
9. Single ROWS dimension : {{[Dim].[Dim].Members}} ON ROWS
10. Multiple ROWS dimensions: {{[Dim1].[Dim1].Members}} * {{[Dim2].[Dim2].Members}} ON ROWS
    Ã¢â‚¬â€ use the * operator for cross product; NEVER use CrossJoin() function (causes rte 45 in TM1)
11. Use ONLY element names from the lists above; match case-insensitively to the exact entry
12. CRITICAL: verify which dimension each element belongs to before writing it Ã¢â‚¬â€ wrong dimension = hard error
13. TM1 element display names and other attributes (e.g. "Employee Name", "Grade") are stored as
    dimension attributes Ã¢â‚¬â€ do NOT query a different cube to look up names or labels
14. "Employee no.2", "employee #2", or "employee 2" means the Employee element named exactly "2";
    do not substitute a nearby visible ID like "10" and do not use the Full Name attribute as the MDX element
15. Time dimensions are semantic, not generic rollups: do not choose "All YTD", "All FYTD",
    "All YTG", "All FYTG", "All QTD", "All MTD", or similar cumulative elements unless
    the user explicitly asks for YTD/FYTD/YTG/QTD/MTD/full-year/all-periods, or the model profile default explicitly names that element.
16. If the user gives a specific month, use that exact month element. If no month is
    specified and the model profile provides a Month default, use that default. If no
    profile default exists for Month, prefer "All MTD" as the current-period default over broader cumulative rollups like "All YTD".
17. Reply with ONLY the raw MDX Ã¢â‚¬â€ no markdown, no comments, nothing else"""

    try:
        response = _client().messages.create(
            model=CLAUDE_MODEL,
            max_tokens=MDX_MAX_TOKENS,
            temperature=MDX_TEMPERATURE,
            messages=[{"role": "user", "content": prompt}],
        )
        mdx = response.content[0].text.strip()
        mdx = re.sub(r"```(?:mdx|sql|)\n?", "", mdx).strip("` \n")
        mdx = _fix_mdx_structure(mdx)
        mdx = _fix_unrequested_time_rollup(mdx, question, model_profile)
        mdx = " ".join(mdx.split())
        print(f"[MDX] {cube_schema.get('cube')} | {mdx}", flush=True)
        return mdx
    except Exception as exc:
        raise RuntimeError(f"MDX generation error: {exc}") from exc


def detect_attribute_intent(
    question: str,
    history: list[dict] | None,
    available_attributes: "dict[str, list[str]]",
) -> "dict | None":
    """
    Determine whether a follow-up question is asking to display a dimension
    attribute (name, grade, department, etc.) from the previous result rather
    than querying new data.

    available_attributes: {dim_name: [attr_name, ...]} for the previous cube's
    non-measure dimensions.

    Returns {"dim_name": ..., "attr_name": ...} or None.
    """
    if not available_attributes:
        return None

    prompt = f"""Previous conversation:
{_conversation_context(history)}

Current question: "{question}"

Available dimension attributes from the previous query result:
{json.dumps(available_attributes, ensure_ascii=False)}

Is the user asking to display a dimension attribute (e.g. a name, grade, category,
description) of entities that already appeared in the previous result?

Reply with ONLY valid JSON Ã¢â‚¬â€ no markdown, no extra text:
{{"intent": true, "dim_name": "<dimension name>", "attr_name": "<attribute name>"}}
or
{{"intent": false}}"""

    try:
        response = _client().messages.create(
            model=CLAUDE_MODEL,
            max_tokens=80,
            temperature=ATTRIBUTE_INTENT_TEMPERATURE,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = re.sub(r"```json|```", "", response.content[0].text).strip()
        parsed = json.loads(raw)
        if parsed.get("intent") and parsed.get("dim_name") and parsed.get("attr_name"):
            print(
                f"[attr-intent] dim={parsed['dim_name']} attr={parsed['attr_name']}",
                flush=True,
            )
            return {"dim_name": str(parsed["dim_name"]), "attr_name": str(parsed["attr_name"])}
    except Exception:
        pass
    return None


def select_cubes(question: str, cubes: list[dict], history: list[dict] | None = None) -> dict:
    return select_cubes_with_profile(question, cubes, history, model_profile=None)


def select_cubes_with_profile(
    question: str,
    cubes: list[dict],
    history: list[dict] | None = None,
    model_profile: dict | None = None,
) -> dict:
    profile_section = (
        f"\nSemantic model profile for business-term interpretation:\n"
        f"{json.dumps(model_profile, indent=2, ensure_ascii=False)}\n"
        if model_profile else ""
    )
    prompt = f"""You are an expert TM1 / IBM Planning Analytics consultant.

Available cubes:
{json.dumps(cubes, indent=2, ensure_ascii=False)}
{profile_section}

User question: "{question}"

Previous conversation:
{_conversation_context(history)}

Select 2 to 3 cubes: the best-matching primary source first, then 1-2 alternatives as fallbacks.
Alternatives are essential Ã¢â‚¬â€ if the primary cube returns no data, the system will automatically try the next one.
Choose alternatives that cover the same topic from a different angle.

Critical: if the user requests a breakdown such as "by X", the primary cube
MUST expose X or a clear business equivalent as a dimension, attribute, or
dimension element pattern in the cube metadata above. Use semantic business
matching for natural-language equivalents (for example role/person/entity,
organization/unit/location/category concepts), but do not pick a cube that lacks
a plausible equivalent breakdown as the primary source.

Critical: for "total labor cost", prefer cubes with total labor cost/account
measures such as "Account Labor", "Total Employment Cost", or "Amount". Do not
use a narrow salary-only cube such as gross salary when a broader labor summary
or employment-cost cube is available.

Critical: if the user is asking to display names, labels, or descriptions of entities (employees, cost centres, etc.) that appeared in a previous result, select THE SAME CUBE as the previous turn (shown as [cube: ...] in conversation above). TM1 element display names come from dimension attributes and are applied automatically Ã¢â‚¬â€ there is no separate "names" cube to query.

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
            max_tokens=CUBE_SELECT_MAX_TOKENS,
            temperature=CUBE_SELECT_TEMPERATURE,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = re.sub(r"```json|```", "", response.content[0].text).strip()
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"AI cube selection parsing failed. Raw: {raw}") from exc
    except Exception as exc:
        raise RuntimeError(f"AI cube selection error: {exc}") from exc


def generate_semantic_profile(schema_summary: dict) -> dict:
    prompt = f"""You are building a semantic profile for a TM1 / IBM Planning Analytics model.

Schema summary:
{json.dumps(schema_summary, indent=2, ensure_ascii=False)}

Generate a concise JSON semantic profile that helps an AI analyst map natural
business language to this model's cubes, dimensions, measures, and attributes.

Rules:
- Infer business terms from cube names, dimension names, measure names, attributes,
  and sample dimension elements.
- Prefer general mappings over one-off hardcoded rules.
- Include only mappings that are supported by the schema evidence.
- Use exact cube, dimension, measure, and attribute names from the schema.
- Do not include markdown or commentary.

Return ONLY valid JSON with this shape:
{{
  "profile_version": 1,
  "model_name": "<short inferred model name>",
  "source": "generated_from_schema_cache",
  "business_terms": {{
    "<natural language term>": ["<dimension/attribute/measure/cube name>", "..."]
  }},
  "metric_mappings": {{
    "<metric term>": {{
      "preferred_cubes": ["<cube>", "..."],
      "preferred_measures": ["<measure>", "..."],
      "preferred_dimensions": ["<dimension>", "..."]
    }}
  }},
  "cube_roles": {{
    "<cube>": {{
      "role": "<what this cube is for>",
      "best_for": ["<question type>", "..."],
      "avoid_for": ["<question type>", "..."]
    }}
  }},
  "default_filters": {{}},
  "selection_guidance": ["<general guidance>", "..."],
  "chart_guidance": {{
    "percentage_terms": ["percent", "percentage", "pct", "share", "ratio", "rate"],
    "split_percentage_measures": true,
    "prefer_share_chart": "doughnut",
    "prefer_absolute_chart": "bar"
  }}
}}"""

    try:
        response = _client().messages.create(
            model=CLAUDE_MODEL,
            max_tokens=4000,
            temperature=SEMANTIC_PROFILE_TEMPERATURE,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = re.sub(r"```json|```", "", response.content[0].text).strip()
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"AI semantic profile parsing failed. Raw: {raw}") from exc
    except Exception as exc:
        raise RuntimeError(f"AI semantic profile generation error: {exc}") from exc


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
        "trend", "trends", "over time", "movement", "Ã¥ÂËœÃ¥Å’â€“", "Ã¨Â¶â€¹Ã¥Å Â¿",
    ])
    has_time_grain = any(term in combined for term in [
        "by month", "monthly", "month", "mtd",
        "by year", "yearly", "annual", "annually",
        "by quarter", "quarterly", "quarter", "qtr",
        "week", "weekly", "day", "daily",
        "Ã¦Å’â€°Ã¦Å“Ë†", "Ã¦Å’â€°Ã¥Â¹Â´", "Ã¦Å’â€°Ã¥Â­Â£Ã¥ÂºÂ¦", "Ã¥Â­Â£Ã¥ÂºÂ¦", "Ã¦Å“Ë†Ã¤Â»Â½", "Ã¥Â¹Â´Ã¥ÂºÂ¦",
    ])
    if asks_for_trend and not has_time_grain:
        missing.append("**Time breakdown**: by month, by quarter, or by year?")

    # 2. Year Ã¢â‚¬â€ required unless the user asked for a year-over-year breakdown
    has_year = bool(re.search(r"\b(20\d{2}|19\d{2})\b", all_user_input))
    has_yearly_breakdown = any(term in combined for term in [
        "by year", "yearly", "annual", "annually", "year over year", "yoy", "Ã¦Å’â€°Ã¥Â¹Â´", "Ã¥Â¹Â´Ã¥ÂºÂ¦",
    ])
    if not has_year and not has_yearly_breakdown:
        missing.append("**Which year?** (e.g., 2024, 2025, or a range like 2023Ã¢â‚¬â€œ2025)")

    # 3. Scenario Ã¢â‚¬â€ always required for financial planning data
    scenario_terms = [
        "actual", "actuals", "act",
        "budget", "bud", "bdg",
        "forecast", "fc", "fcst", "fcast",
        "plan", "estimate",
        "Ã¥Â®Å¾Ã©â„¢â€¦", "Ã©Â¢â€žÃ§Â®â€”", "Ã©Â¢â€žÃ¦Âµâ€¹", "Ã¨Â®Â¡Ã¥Ë†â€™",
    ]
    has_scenario = any(re.search(rf"\b{re.escape(term)}\b", combined) for term in scenario_terms)
    if not has_scenario:
        missing.append("**Which scenario?** (Actual, Budget, Forecast, or e.g. Actual vs Budget)")

    # 4. Month range Ã¢â‚¬â€ only ask once year is known and "by month" is confirmed
    asks_monthly = any(term in combined for term in ["by month", "monthly", "Ã¦Å’â€°Ã¦Å“Ë†"])
    if asks_monthly and has_year:
        month_range_terms = [
            "q1", "q2", "q3", "q4", "h1", "h2", "ytd", "full year", "all",
            "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
            "january", "february", "march", "april", "june", "july",
            "august", "september", "october", "november", "december",
            "Ã¤Â¸â‚¬Ã¦Å“Ë†", "Ã¤ÂºÅ’Ã¦Å“Ë†", "Ã¤Â¸â€°Ã¦Å“Ë†", "Ã¥â€ºâ€ºÃ¦Å“Ë†", "Ã¤Âºâ€Ã¦Å“Ë†", "Ã¥â€¦Â­Ã¦Å“Ë†",
            "Ã¤Â¸Æ’Ã¦Å“Ë†", "Ã¥â€¦Â«Ã¦Å“Ë†", "Ã¤Â¹ÂÃ¦Å“Ë†", "Ã¥ÂÂÃ¦Å“Ë†", "Ã¥ÂÂÃ¤Â¸â‚¬Ã¦Å“Ë†", "Ã¥ÂÂÃ¤ÂºÅ’Ã¦Å“Ë†", "Ã¥â€¦Â¨Ã¥Â¹Â´",
        ]
        has_month_range = any(term in combined for term in month_range_terms)
        if not has_month_range:
            missing.append("**Which months?** (e.g., JanÃ¢â‚¬â€œJun, full year, or YTD)")

    if not missing:
        return None

    # On the very first question, append optional dimension filters as a hint
    if not history:
        missing.append(
            "*(Optional)* **Filter by** cost center, grade, or employee category Ã¢â‚¬â€ "
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

Use specific numbers from the data. For monetary measures where the exact
currency is not specified, use a generic dollar-style symbol like $5,510 and do
not name a specific currency such as euro, AUD, or USD unless the source data,
filters, cube name, or measure name explicitly states it. Keep it readable Ã¢â‚¬â€
no bullet-point spam.

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


def generate_homepage_suggestions(cubes: list[dict]) -> list[str]:
    """Generate 3 suggested questions tailored to the connected TM1 model."""
    cube_summary = json.dumps(
        [{"cube": c.get("cube", ""), "description": c.get("description", "")} for c in cubes[:20]],
        ensure_ascii=False,
    )
    prompt = f"""You are a financial analyst assistant connected to an IBM Planning Analytics (TM1) model.

Available cubes:
{cube_summary}

Generate exactly 3 short, specific suggested questions a business user would ask about this data.
Each question should reference concepts visible in the cube names or descriptions.
Keep each question under 10 words Ã¢â‚¬â€ concise enough to fit in a button label.

Reply with ONLY a JSON array of 3 strings, no markdown, no extra text:
["<question 1>", "<question 2>", "<question 3>"]"""

    try:
        response = _client().messages.create(
            model=CLAUDE_MODEL,
            max_tokens=200,
            temperature=SUGGESTIONS_TEMPERATURE,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = re.sub(r"```json|```", "", response.content[0].text).strip()
        result = json.loads(raw)
        if isinstance(result, list) and len(result) >= 3:
            return [str(s) for s in result[:3]]
    except Exception:
        pass
    # Fallback to generic questions if Claude fails
    return [
        "Show me a cost summary by department",
        "What is the headcount movement this quarter?",
        "Compare actuals vs budget by cost center",
    ]


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
            max_tokens=ANALYSIS_MAX_TOKENS,
            temperature=ANALYSIS_TEMPERATURE,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            for text in stream.text_stream:
                yield text
    except Exception as exc:
        raise RuntimeError(f"AI analysis error: {exc}") from exc
