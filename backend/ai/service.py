import json
import re
from collections.abc import Iterator
from datetime import date

_TIME_PERIOD_RE = re.compile(
    r"""
    \b(?:
        jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|
        jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|
        oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|
        q[1-4]|h[12]|
        f?ytd|[fq]?mtd|qtd|
        full[\s\-]?year|whole[\s\-]?year|all[\s\-]?year|
        year[\s\-]to[\s\-]date|all[\s\-]months?|full[\s\-]period
    )\b
    """,
    re.VERBOSE | re.IGNORECASE,
)


def _has_time_period(text: str) -> bool:
    """Return True if text already specifies a month, quarter, or period."""
    return bool(_TIME_PERIOD_RE.search(text))

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - depends on installed runtime deps
    OpenAI = None

try:
    import anthropic as _anthropic_sdk
except ImportError:  # pragma: no cover
    _anthropic_sdk = None

from ..config import (
    ANALYSIS_MAX_TOKENS,
    get_llm_api_key,
    get_llm_model,
    get_llm_provider,
    get_llm_temperature,
    CUBE_SELECT_MAX_TOKENS,
    MDX_MAX_TOKENS,
    SEMANTIC_PROFILE_MAX_TOKENS,
)
from ..tm1.cache import find_question_element_matches
from .prompt_rules import GENERAL_AGENT_CONTRACT


# ── OpenAI ────────────────────────────────────────────────────────────────────

def _openai_client() -> OpenAI:
    api_key = get_llm_api_key()
    if not api_key:
        raise RuntimeError("LLM_API_KEY environment variable not set")
    if OpenAI is None:
        raise RuntimeError("openai package is not installed")
    return OpenAI(api_key=api_key)


def _needs_openai_min_token_budget(model: str) -> bool:
    name = model.strip().lower()
    return bool(re.match(r"o\d", name) or re.match(r"gpt-5(?:[.\-]|$)", name))


def _openai_token_budget(max_tokens: int, model: str) -> int:
    if _needs_openai_min_token_budget(model):
        return max(max_tokens, 4096)
    return max_tokens


def _openai_output_text(response) -> str:
    text = (getattr(response, "output_text", "") or "").strip()
    status = getattr(response, "status", None)
    details = getattr(response, "incomplete_details", None)
    reason = getattr(details, "reason", None)
    if not text and status == "incomplete" and reason == "max_output_tokens":
        raise RuntimeError("OpenAI response used the output token budget before producing visible text; increase MDX_MAX_TOKENS")
    return text


def _complete_text_openai(prompt: str, *, max_tokens: int, temperature: float) -> str:
    model = get_llm_model()
    budget = _openai_token_budget(max_tokens, model)
    kwargs = {"model": model, "input": prompt, "max_output_tokens": budget, "temperature": temperature}
    try:
        response = _openai_client().responses.create(**kwargs)
    except Exception as exc:
        if not _is_unsupported_temperature_error(exc):
            raise
        kwargs.pop("temperature", None)
        response = _openai_client().responses.create(**kwargs)
    return _openai_output_text(response)


def _stream_text_openai(prompt: str, *, max_tokens: int, temperature: float) -> Iterator[str]:
    model = get_llm_model()
    budget = _openai_token_budget(max_tokens, model)
    kwargs = {"model": model, "input": prompt, "max_output_tokens": budget, "temperature": temperature}
    try:
        yield from _stream_openai_kwargs(kwargs)
    except Exception as exc:
        if not _is_unsupported_temperature_error(exc):
            raise
        kwargs.pop("temperature", None)
        yield from _stream_openai_kwargs(kwargs)


def _stream_openai_kwargs(kwargs: dict) -> Iterator[str]:
    with _openai_client().responses.stream(**kwargs) as stream:
        for event in stream:
            if event.type == "response.output_text.delta":
                yield event.delta


def _is_unsupported_temperature_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "unsupported parameter" in message and "temperature" in message


# ── Anthropic ─────────────────────────────────────────────────────────────────

def _anthropic_client():
    if _anthropic_sdk is None:
        raise RuntimeError("anthropic package is not installed")
    api_key = get_llm_api_key()
    if not api_key:
        raise RuntimeError("LLM_API_KEY environment variable not set")
    return _anthropic_sdk.Anthropic(api_key=api_key)


def _complete_text_anthropic(prompt: str, *, max_tokens: int) -> str:
    response = _anthropic_client().messages.create(
        model=get_llm_model(),
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(b.text for b in response.content if b.type == "text").strip()


def _stream_text_anthropic(prompt: str, *, max_tokens: int) -> Iterator[str]:
    with _anthropic_client().messages.stream(
        model=get_llm_model(),
        max_tokens=max_tokens,
        messages=[{"role": "user", "content": prompt}],
    ) as stream:
        yield from stream.text_stream


# ── Provider router ───────────────────────────────────────────────────────────

def _complete_text(prompt: str, *, max_tokens: int, temperature: float) -> str:
    provider = get_llm_provider()
    if provider == "anthropic":
        return _complete_text_anthropic(prompt, max_tokens=max_tokens)
    return _complete_text_openai(prompt, max_tokens=max_tokens, temperature=temperature)


def _stream_text(prompt: str, *, max_tokens: int, temperature: float) -> Iterator[str]:
    provider = get_llm_provider()
    if provider == "anthropic":
        yield from _stream_text_anthropic(prompt, max_tokens=max_tokens)
    else:
        yield from _stream_text_openai(prompt, max_tokens=max_tokens, temperature=temperature)


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
    """Fix WHERE appearing before FROM by swapping them to the correct order."""
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


def normalize_mdx(mdx: str, question: str, model_profile: dict | None) -> str:
    mdx = re.sub(r"```(?:mdx|sql|)\n?", "", mdx or "").strip("` \n")
    mdx = _fix_mdx_structure(mdx)
    mdx = _fix_unrequested_time_rollup(mdx, question, model_profile)
    return " ".join(mdx.split())


def validate_generated_mdx(mdx: str, cube_name: str) -> None:
    text = (mdx or "").strip()
    if not text:
        raise RuntimeError("AI returned empty MDX")
    if not re.search(r"(?i)^\s*SELECT\b", text):
        raise RuntimeError("AI did not return an MDX SELECT statement")
    if not re.search(rf"(?i)\bFROM\s+\[{re.escape(str(cube_name))}\]", text):
        raise RuntimeError(f"AI returned MDX without FROM [{cube_name}]")


_STATEMENT_QUESTION_RE = re.compile(
    r"\b(p\s*&\s*l|p\s*and\s*l|pnl|profit\s+and\s+loss|income\s+statement|"
    r"balance\s+sheet|cash\s+flow|trial\s+balance)\b",
    re.IGNORECASE,
)


def _profile_defaults_directive(cube_schema: dict, model_profile: dict | None) -> str:
    """Emit a hard directive listing per-dim WHERE overrides from
    `default_filters` in the model profile. These pinned values come from the
    user (or from probes that found known-good data-bearing leaves) and must be
    used whenever the user didn't name something different for that dim. This
    prevents the LLM from picking the schema's default_element when that
    consolidation has no data feeding into it."""
    from .mdx_planner import _profile_dim_defaults
    overrides = _profile_dim_defaults(model_profile)
    if not overrides:
        return ""
    schema_dims = {str(d.get("name", "")): d for d in (cube_schema.get("dimensions") or [])}
    relevant = {k: v for k, v in overrides.items() if k in schema_dims}
    if not relevant:
        return ""
    lines = "\n".join(
        f"  - [{name}].[{name}].[{element}]"
        for name, element in relevant.items()
    )
    return (
        "\nProfile default-filter overrides (HARD RULE: use these in WHERE "
        "for the named dimensions unless the user explicitly mentioned a "
        "different element for the same dimension - they are the cube's "
        "known data-bearing defaults, NOT the schema's mechanical "
        "default_element):\n"
        f"{lines}\n"
    )


def _line_item_directive(question: str, cube_schema: dict) -> str:
    """When the question looks like a financial statement, find the cube's
    line-item dim by scanning ELEMENT CONTENT (revenue / cost / expense / ...
    inside leaves + consolidations) and lock it onto ROWS. This stays in sync
    with the planner's element-scoring picker."""
    if not _STATEMENT_QUESTION_RE.search(question or ""):
        return ""
    from .mdx_planner import _resolve_line_item_dim  # avoid circular import at module load
    dim = _resolve_line_item_dim(cube_schema.get("dimensions", []) or [], None)
    if not dim:
        return ""
    name = str(dim.get("name", ""))
    top = (
        (dim.get("top_consolidations") or [None])[0]
        or dim.get("default_element")
        or ""
    )
    if not name or not top:
        return ""
    return (
        f"\nFinancial-statement line-item directive (HARD RULE for this question):\n"
        f"- Put dimension [{name}] on ROWS using "
        f"Descendants([{name}].[{name}].[{top}], 99, LEAVES).\n"
        f"- This dim was chosen because its elements/consolidations contain the most "
        f"P&L-style names (revenue, cost, expense, depreciation, etc.).\n"
        f"- Do NOT put any other similarly-named dimension on ROWS (e.g. a separate "
        f"reporting / disclosure dim whose elements are just codes or layout labels). "
        f"Filter those in WHERE to their default_element.\n"
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
        examples_section = f"\nPast successful queries for structural reference (reuse axis layout and CrossJoin patterns only - always verify element names against the schema above):\n{examples}\n"

    profile_section = (
        f"\nSemantic model profile defaults and guidance:\n"
        f"{json.dumps(model_profile, indent=2, ensure_ascii=False)}\n"
        if model_profile else ""
    )

    statement_directive = _line_item_directive(question, cube_schema)
    defaults_directive = _profile_defaults_directive(cube_schema, model_profile)

    prompt = f"""You are an expert TM1 / IBM Planning Analytics MDX query writer.

{GENERAL_AGENT_CONTRACT}

Cube: {cube_name}

Dimensions and their available elements:
{dims_json}
{statement_directive}{defaults_directive}{examples_section}
{profile_section}
User question: "{question}"

Previous conversation:
{_conversation_context(history)}

Write ONE MDX SELECT statement that answers the question.

TM1 focus examples:
- "why Pension contributions are largest, show by month and grade" means filter the benefit/category/measure dimension to Pension contributions, then break that item down by month and grade.
- A follow-up like "show by month" keeps the business topic from the previous turn, but the current requested breakdown still controls the new axes.
- "show the P&L statement", "profit and loss statement", or "income statement" means display financial statement line items: put the Account/Line Item/P&L account dimension on ROWS if such a dimension exists, rather than filtering it to one total element.
- If the semantic model profile has finance_semantics for income_statement, use its line_item_dimension as the preferred ROWS dimension for P&L/income statement questions.

REQUIRED structure - follow exactly. Prefer set functions (left) over enumeration (right) when a parent is available:
  SELECT {{[Period].[Period].[2025].Children}} ON COLUMNS,
         {{Descendants([Account].[Account].[Net Income], 99, LEAVES)}} ON ROWS
  FROM [{cube_name}]
  WHERE ([Year].[Year].[2025], [Scenario].[Scenario].[Actual], [Measure].[Measure].[Amount])

Hand-picked enumeration is fine only for a small subset:
  SELECT {{[Period].[Period].[Jan], [Period].[Period].[Feb], [Period].[Period].[Mar]}} ON COLUMNS, ...

Each dimension in the schema above lists three element groups:
  - "elements": representative element names (leaves and parents mixed)
  - "consolidations": parent / rollup elements - valid targets for .Children and Descendants
  - "top_consolidations": root parents - prefer these for full-statement queries
  - "default_element": the safest total/root element for this dimension when the user did not specify one

Hard rules for valid TM1 MDX:
1. FROM [{cube_name}] must come immediately after the axes - always BEFORE WHERE
2. WHERE accepts ONLY single members [Dim].[Dim].[Element]
   - never .Members, never {{set expressions}}, never .Children, never Descendants(...)
3. A dimension must appear on exactly ONE of: COLUMNS, ROWS, or WHERE - never in two places, never omitted
4. ALL dimensions not on COLUMNS or ROWS MUST appear in WHERE - include every remaining dimension with one element
5. A 4-digit year (e.g. 2025) ALWAYS goes in the dimension whose name contains "Year" or "Period" - NEVER Employee, Scenario, etc.
6. For unfiltered WHERE dimensions, choose a default from the model profile when available; otherwise use that dimension's "default_element". Do not choose an arbitrary leaf when default_element exists.
7. Put dimensions needed for the answer on COLUMNS or ROWS; put all remaining dimensions in WHERE.
8. PREFER set functions over enumeration when the axis would list more than 5 sibling elements that share a parent:
   - All immediate children of a parent: [Dim].[Dim].[Parent].Children
   - All leaf descendants under a parent: Descendants([Dim].[Dim].[Parent], 99, LEAVES)
   - Descendants at one level: Descendants([Dim].[Dim].[Parent], 1)
   The Parent MUST come from the dimension's "consolidations" or "top_consolidations" list.
9. NEVER call .Children, Descendants, or .Members on a leaf element (an element not in "consolidations").
10. For full financial statements (P&L, income statement, balance sheet, cash flow) on the Account/Line Item dimension, use
    Descendants([Dim].[Dim].[TopConsolidation], 99, LEAVES) with a name from "top_consolidations" -
    do NOT enumerate every line item by hand.
11. For "by month" on a Period/Time dimension that has a yearly parent: prefer [Period].[Period].[<year>].Children
    over enumerating 12 month names.
12. Avoid [Dim].[Dim].Members unless the user explicitly asks for every element in that dimension.
13. For dimensions with mixed hierarchy levels, prefer either a set function rooted at a clean parent or an explicit small list -
    do NOT mix rollups and leaves on the same axis.
14. When the question names one specific non-time element, use that element in WHERE unless the user asks to compare it against siblings.
15. For financial statement requests (P&L, profit and loss statement, income statement), show statement line items by putting the Account/Line Item/P&L account dimension on ROWS. Do not hide the account dimension in WHERE as a single total unless the user asked for a single total.
16. Single ROWS dimension: {{<set-function-or-enumeration>}} ON ROWS
17. Multiple ROWS dimensions: {{<setA>}} * {{<setB>}} ON ROWS
    - use the * operator for cross product; NEVER use CrossJoin() function (causes rte 45 in TM1)
18. Use ONLY element names from the lists above; match case-insensitively to the exact entry
19. CRITICAL: verify which dimension each element belongs to before writing it - wrong dimension = hard error
20. TM1 element display names and other attributes (e.g. "Employee Name", "Grade") are stored as
    dimension attributes - do NOT query a different cube to look up names or labels
21. "Employee no.2", "employee #2", or "employee 2" means the Employee element named exactly "2";
    do not substitute a nearby visible ID like "10" and do not use the Full Name attribute as the MDX element
22. Time dimensions are semantic, not generic rollups: do not choose "All YTD", "All FYTD",
    "All YTG", "All FYTG", "All QTD", "All MTD", or similar cumulative elements unless
    the user explicitly asks for YTD/FYTD/YTG/QTD/MTD/full-year/all-periods, or the model profile default explicitly names that element.
23. If the user gives a specific month, use that exact month element. If no month is
    specified and the model profile provides a Month default, use that default.
24. Reply with ONLY the raw MDX - no markdown, no comments, nothing else"""

    try:
        mdx = _complete_text(
            prompt,
            max_tokens=MDX_MAX_TOKENS,
            temperature=get_llm_temperature("mdx_temperature"),
        )
        mdx = normalize_mdx(mdx, question, model_profile)
        validate_generated_mdx(mdx, cube_name)
        print(f"[MDX] {cube_schema.get('cube')} | {mdx}", flush=True)
        return mdx
    except Exception as exc:
        raise RuntimeError(f"MDX generation error: {exc}") from exc


def repair_cube_mdx(
    question: str,
    cube_schema: dict,
    failed_mdx: str,
    error_message: str,
    history: list[dict] | None = None,
    similar_queries: list[dict] | None = None,
    model_profile: dict | None = None,
) -> str:
    """Repair an MDX statement that TM1 rejected."""
    cube_name = cube_schema.get("cube", "")
    dims_json = json.dumps(cube_schema.get("dimensions", []), indent=2, ensure_ascii=False)

    examples_section = ""
    if similar_queries:
        examples = "\n\n".join(
            f'Question: "{sq["question"]}"\nCube: {sq["cube"]}\nMDX:\n{sq["mdx"]}'
            for sq in similar_queries
        )
        examples_section = f"\nPast successful queries for structural reference:\n{examples}\n"

    profile_section = (
        f"\nSemantic model profile defaults and guidance:\n"
        f"{json.dumps(model_profile, indent=2, ensure_ascii=False)}\n"
        if model_profile else ""
    )

    statement_directive = _line_item_directive(question, cube_schema)
    defaults_directive = _profile_defaults_directive(cube_schema, model_profile)

    prompt = f"""You are an expert TM1 / IBM Planning Analytics MDX debugger.

{GENERAL_AGENT_CONTRACT}

Cube: {cube_name}

Dimensions and available elements:
{dims_json}
{statement_directive}{defaults_directive}{examples_section}
{profile_section}
User question: "{question}"

Previous conversation:
{_conversation_context(history)}

TM1 rejected this MDX:
{failed_mdx}

TM1 error:
{error_message}

Return a corrected MDX SELECT statement for the same question.

Hard rules:
1. Use ONLY cube [{cube_name}]
2. Use ONLY dimensions and elements listed in the schema above
3. A dimension must appear on exactly ONE of COLUMNS, ROWS, or WHERE
4. WHERE accepts ONLY single members [Dim].[Dim].[Element], never sets or .Members
5. Put 4-digit years only in a Year/Period/Time dimension
6. For unfiltered dimensions, use model profile defaults where available; otherwise use that dimension's default_element. Do not choose an arbitrary leaf when default_element exists
7. PREFER set functions over enumeration on COLUMNS/ROWS when an axis would list >5 siblings under one parent:
   - [Dim].[Dim].[Parent].Children for immediate children
   - Descendants([Dim].[Dim].[Parent], 99, LEAVES) for all leaves under a parent
   The Parent MUST be in that dimension's "consolidations" or "top_consolidations" list. Never call set functions on a leaf.
8. For full P&L / income statement / balance sheet rows, use Descendants on a name from "top_consolidations" - do NOT enumerate line items by hand.
9. Avoid [Dim].[Dim].Members when the previous result mixed rollups and leaf elements on the same axis; rewrite that axis with a set function rooted at a clean parent or an explicit small list
10. Preserve any current-question narrowing: if the question names one specific measure/account/category/benefit/etc., keep that element filtered instead of expanding siblings
11. If the error says an element cannot be found, replace it with the closest exact element from the correct dimension
12. Reply with ONLY raw MDX, no markdown, no comments"""

    try:
        mdx = _complete_text(
            prompt,
            max_tokens=MDX_MAX_TOKENS,
            temperature=get_llm_temperature("mdx_temperature"),
        )
        mdx = normalize_mdx(mdx, question, model_profile)
        validate_generated_mdx(mdx, cube_name)
        print(f"[MDX repair] {cube_name} | {mdx}", flush=True)
        return mdx
    except Exception as exc:
        raise RuntimeError(f"MDX repair error: {exc}") from exc


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

Reply with ONLY valid JSON - no markdown, no extra text:
{{"intent": true, "dim_name": "<dimension name>", "attr_name": "<attribute name>"}}
or
{{"intent": false}}"""

    try:
        raw = _complete_text(
            prompt,
            max_tokens=80,
            temperature=get_llm_temperature("attribute_intent_temperature"),
        )
        raw = re.sub(r"```json|```", "", raw).strip()
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

{GENERAL_AGENT_CONTRACT}

Available cubes:
{json.dumps(cubes, indent=2, ensure_ascii=False)}
{profile_section}

User question: "{question}"

Previous conversation:
{_conversation_context(history)}

Select 2 to 3 cubes: the best-matching primary source first, then 1-2 alternatives as fallbacks.
Alternatives are essential - if the primary cube returns no data, the system will automatically try the next one.
Choose alternatives that cover the same topic from a different angle.

Use the previous conversation to resolve follow-up references such as "this",
"that category", or "show by month". Do not let previous broad questions override
a narrower current question.

Critical: if the user requests a breakdown such as "by X", the primary cube
MUST expose X or a clear business equivalent as a dimension, attribute, or
dimension element pattern in the cube metadata above. Use semantic business
matching for natural-language equivalents (for example role/person/entity,
organization/unit/location/category concepts), but do not pick a cube that lacks
a plausible equivalent breakdown as the primary source.

Use metric and cube preferences from the semantic model profile when available.
If the profile has no guidance for a business term, choose the cube whose own
dimensions, attributes, measures, and description best support the requested
metric and breakdown.

For financial statement questions, use finance_semantics from the semantic
model profile when available. For example, an income_statement/P&L question
should prefer the mapped primary_cube and require the mapped line_item_dimension
to be available for statement rows.

Critical: if the user is asking to display names, labels, or descriptions of entities (employees, cost centres, etc.) that appeared in a previous result, select THE SAME CUBE as the previous turn (shown as [cube: ...] in conversation above). TM1 element display names come from dimension attributes and are applied automatically - there is no separate "names" cube to query.

Reply ONLY with valid JSON - no markdown, no extra text:
{{
  "cubes": [
    {{
      "cube": "<exact cube name from the list>",
      "reasoning": "<max 12 words>"
    }}
  ],
  "reasoning": "<max 12 words>"
}}"""

    try:
        raw = _complete_text(prompt, max_tokens=CUBE_SELECT_MAX_TOKENS, temperature=get_llm_temperature("cube_select_temperature"))
        return _parse_cube_selection_json(raw)
    except json.JSONDecodeError as exc:
        try:
            repair_prompt = f"""Return ONLY valid compact JSON for this cube selection result.
No markdown. No extra text. Keep every reasoning under 8 words.

Original invalid JSON:
{raw}

Required shape:
{{"cubes":[{{"cube":"<exact cube name>","reasoning":"<short>"}}],"reasoning":"<short>"}}"""
            repaired = _complete_text(
                repair_prompt,
                max_tokens=max(CUBE_SELECT_MAX_TOKENS, 900),
                temperature=0,
            )
            return _parse_cube_selection_json(repaired)
        except Exception:
            raise RuntimeError(f"AI cube selection parsing failed. Raw: {raw}") from exc
    except Exception as exc:
        raise RuntimeError(f"AI cube selection error: {exc}") from exc


def _parse_cube_selection_json(raw: str) -> dict:
    cleaned = re.sub(r"```json|```", "", raw).strip()
    return json.loads(cleaned)


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
- Keep the JSON compact: at most 12 business_terms, 12 metric_mappings, and
  20 cube_roles.
- Each array must contain at most 5 strings. Do not enumerate every scenario,
  month, forecast version, employee, account, or element variant.
- Prefer broad semantic terms over long synonym lists.
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
        raw = _complete_text(
            prompt,
            max_tokens=SEMANTIC_PROFILE_MAX_TOKENS,
            temperature=get_llm_temperature("semantic_profile_temperature"),
        )
        raw = re.sub(r"```json|```", "", raw).strip()
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


def find_clarifications(
    question: str,
    history: list[dict] | None = None,
    model_profile: dict | None = None,
) -> str | None:
    """
    Decide whether to ask the user for clarification before running a TM1 query.

    Detection is deterministic and cheap (no LLM call when nothing is missing).
    When something IS missing, the LLM phrases a single context-aware follow-up.
    Schema and business-term ambiguity is handled separately by
    find_schema_clarification() after cube metadata is available.
    """
    prior_questions = [str(msg.get("question", "")) for msg in (history or [])]
    all_user_input = " ".join(prior_questions + [question]).strip()
    combined = all_user_input.lower()
    original = question.lower()

    missing: list[str] = []

    asks_for_trend = any(term in original for term in (
        "trend", "trends", "over time", "movement", "variance over time",
    ))
    has_time_grain = any(term in combined for term in (
        "by month", "monthly", "month", "mtd",
        "by year", "yearly", "annual", "annually", "year over year", "yoy",
        "by quarter", "quarterly", "quarter", "qtr",
        "week", "weekly", "day", "daily",
    ))
    if asks_for_trend and not has_time_grain:
        missing.append("time_breakdown")

    has_year = bool(re.search(r"\b(20\d{2}|19\d{2})\b", all_user_input))
    has_yearly_breakdown = any(term in combined for term in (
        "by year", "yearly", "annual", "annually", "year over year", "yoy",
    ))
    has_current_period_hint = any(term in combined for term in (
        "current", "this year", "this month", "latest", "today",
    ))
    if not has_year and not has_yearly_breakdown and not has_current_period_hint:
        missing.append("year")

    scenario_terms = (
        "actual", "actuals", "act",
        "budget", "bud", "bdg",
        "forecast", "fc", "fcst", "fcast",
        "plan", "planned", "estimate", "target",
    )
    has_scenario = any(re.search(rf"\b{re.escape(term)}\b", combined) for term in scenario_terms)
    if not has_scenario:
        missing.append("scenario")

    has_any_month = _has_time_period(combined)
    asks_monthly = any(term in combined for term in ("by month", "monthly"))
    asks_snapshot = any(term in combined for term in (
        "p&l", "p & l", "profit and loss", "income statement", "profit & loss",
        "balance sheet", "cash flow",
    ))
    if has_year and not has_any_month and (asks_monthly or asks_snapshot):
        missing.append("month_range" if asks_monthly else "month_or_period")

    if not missing:
        return None

    return _compose_clarification_message(question, missing, history, model_profile)


_FIELD_HINTS = {
    "time_breakdown": "user asked for a trend/over-time view but did not say by month, quarter, or year",
    "year": "no specific year mentioned (e.g., 2025 or a range)",
    "scenario": "no scenario specified (Actual, Budget, Forecast, Plan, etc.)",
    "month_range": "user asked for a monthly breakdown but did not say which months (a range like Jan-Jun, full year, YTD)",
    "month_or_period": "snapshot/statement query without a specific month or period (single month like Apr, full year, YTD, or a quarter)",
}

_FIELD_FALLBACK_LABELS = {
    "time_breakdown": "**Time breakdown**: by month, by quarter, or by year?",
    "year": "**Which year?** (for example, 2025 or a range like 2024-2026)",
    "scenario": "**Which scenario?** (Actual, Budget, Forecast, or Actual vs Budget)",
    "month_range": "**Which months?** (for example, Jan-Jun, full year, or YTD)",
    "month_or_period": "**Which month or period?** (for example, Apr, full year, or YTD)",
}


def _compose_clarification_message(
    question: str,
    missing: list[str],
    history: list[dict] | None,
    model_profile: dict | None,
) -> str:
    """LLM-phrased clarification that references the user's actual question."""
    defaults = (model_profile or {}).get("default_filters") or {}
    defaults_json = json.dumps(defaults, ensure_ascii=False) if defaults else "{}"
    needs = "\n".join(f"- {field}: {_FIELD_HINTS.get(field, field)}" for field in missing)

    prompt = f"""You help a TM1 / IBM Planning Analytics assistant ask the user for missing details.

User question: "{question}"

Previous conversation:
{_conversation_context(history)}

Model default filters (already applied automatically if the user does not override):
{defaults_json}

Detected missing details:
{needs}

Write ONE short clarification message (1-3 sentences, markdown) that:
- Opens with brief context referencing what the user is actually asking about (e.g., the entity, statement, or metric they mentioned)
- Asks for the detected missing fields above. Do not silently treat defaults as user confirmation for scenario, month, period, or full-year choices.
- Uses **bold** for each field name and gives 2-4 concrete example choices tied to the user's question (e.g., "Apr", "full year YTD", "Q1")
- Friendly, conversational tone - combine into one natural sentence when possible instead of bullet spam

Reply with ONLY the markdown message, no JSON, no extra text."""

    try:
        message = _complete_text(prompt, max_tokens=240, temperature=0.3).strip()
        if message.upper().startswith("NONE"):
            return _fallback_clarification(missing)
        return message
    except Exception:
        return _fallback_clarification(missing)


def _fallback_clarification(missing: list[str]) -> str:
    prefix = (
        "One more detail before I fetch the TM1 data:"
        if len(missing) == 1
        else "Before I pull the TM1 data, I need a few more details:"
    )
    bullets = "\n".join(
        f"- {_FIELD_FALLBACK_LABELS[f]}" for f in missing if f in _FIELD_FALLBACK_LABELS
    )
    return prefix + "\n\n" + bullets


def find_schema_clarification(
    question: str,
    cubes: list[dict],
    history: list[dict] | None = None,
    model_profile: dict | None = None,
) -> str | None:
    """
    Ask for clarification when business terms can map to multiple plausible
    TM1 concepts. This catches cases like "staff" meaning Employee, Headcount,
    FTE, or cost, while avoiding hard-coded model-specific rules.
    """
    matches = find_question_element_matches(question)
    if matches:
        sample = ", ".join(f"{m[0]} -> {m[1]}.{m[2]}" for m in matches[:5])
        print(f"[clarify-skip] question references real elements: {sample}", flush=True)
        return None
    cube_context = json.dumps(cubes[:12], indent=2, ensure_ascii=False)
    profile_section = (
        f"\nSemantic model profile:\n{json.dumps(model_profile, indent=2, ensure_ascii=False)}\n"
        if model_profile else ""
    )
    prompt = f"""You are a TM1 / IBM Planning Analytics query router.

{GENERAL_AGENT_CONTRACT}

Available cube and dimension context:
{cube_context}
{profile_section}

Current user question: "{question}"

Previous conversation:
{_conversation_context(history)}

Decide whether the question has HIGH-RISK schema ambiguity that should be
clarified before running a TM1 query. Only ask when multiple plausible mappings
would materially change the data retrieved.

Examples of high-risk ambiguity:
- a term like staff could mean Employee detail, Headcount, FTE, or labor cost
- a term like forecast could mean a Scenario element or a version/year-specific label
- a number could be an employee ID rather than a year, unless it is a clear 4-digit year
- requested breakdown is not clearly available in the chosen business area

Do NOT ask if the existing conversation already resolves the ambiguity.
Do NOT ask generic questions about year/scenario/month; another checker handles those.

Reply ONLY with valid JSON:
{{"clarify": false}}
or
{{"clarify": true, "message": "<short markdown question with 2-4 concrete choices>"}}"""

    try:
        raw = _complete_text(
            prompt,
            max_tokens=240,
            temperature=0,
        )
        raw = re.sub(r"```json|```", "", raw).strip()
        parsed = json.loads(raw)
        if parsed.get("clarify") and parsed.get("message"):
            return str(parsed["message"]).strip()
    except Exception:
        pass
    return None



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
filters, cube name, or measure name explicitly states it. Keep it readable -
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
Keep each question under 10 words - concise enough to fit in a button label.

Reply with ONLY a JSON array of 3 strings, no markdown, no extra text:
["<question 1>", "<question 2>", "<question 3>"]"""

    try:
        raw = _complete_text(
            prompt,
            max_tokens=200,
            temperature=get_llm_temperature("suggestions_temperature"),
        )
        raw = re.sub(r"```json|```", "", raw).strip()
        result = json.loads(raw)
        if isinstance(result, list) and len(result) >= 3:
            return [str(s) for s in result[:3]]
    except Exception:
        pass
    # Fallback to generic questions if the AI provider fails
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
    """Sync generator: yields text chunks from the LLM streaming API."""
    prompt = _analysis_prompt(question, sources, skipped_sources or [], history)
    try:
        yield from _stream_text(
            prompt,
            max_tokens=ANALYSIS_MAX_TOKENS,
            temperature=get_llm_temperature("analysis_temperature"),
        )
    except Exception as exc:
        raise RuntimeError(f"AI analysis error: {exc}") from exc
