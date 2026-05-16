"""Rule-based MDX planner.

Returns a deterministic MDX statement when the user's question matches a
known reporting pattern (e.g. P&L / income statement). Used as a fast path
that skips the LLM MDX-generation call; if the planner can't reach high
enough confidence it returns None and the caller falls back to the LLM.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

from .tm1_lexicon import SCENARIO_ALIASES, SCENARIO_TERMS, SCENARIO_TERM_TO_CANON, STATEMENT_PATTERN

# Confidence below MIN_CONFIDENCE → defer to LLM completely.
# Confidence below SURFACE_THRESHOLD → still execute the plan but show the
# user the assumptions we made, since we're not sure they're right.
MIN_CONFIDENCE: float = 0.55
SURFACE_THRESHOLD: float = 0.85

_PNL_PATTERN = STATEMENT_PATTERN
_BY_MONTH_PATTERN = re.compile(r"\bby\s+(month|months|period|periods)\b", re.IGNORECASE)
_BY_QUARTER_PATTERN = re.compile(r"\bby\s+(quarter|quarters|q[1-4])\b", re.IGNORECASE)
_BY_SCENARIO_PATTERN = re.compile(r"\bby\s+(scenario|version)\b", re.IGNORECASE)
_YEAR_PATTERN = re.compile(r"\b(20\d{2})\b")

# Words/phrases that pick out specific scenario elements. Order matters
# for the "X vs Y" parser - longer/more specific terms come first.
_SCENARIO_TERMS = SCENARIO_TERMS
_TERM_TO_CANON = SCENARIO_TERM_TO_CANON
_SCENARIO_ALL_ALIASES = SCENARIO_ALIASES
_SCENARIO_PAIR_RE = re.compile(
    rf"\b({'|'.join(re.escape(a) for a in _SCENARIO_ALL_ALIASES)})\b"
    rf"\s+(?:vs\.?|versus|compared\s+to|against|and)\s+"
    rf"\b({'|'.join(re.escape(a) for a in _SCENARIO_ALL_ALIASES)})\b",
    re.IGNORECASE,
)
_SCENARIO_SINGLE_RE = re.compile(
    rf"\b({'|'.join(re.escape(a) for a in _SCENARIO_ALL_ALIASES)})\b",
    re.IGNORECASE,
)
_VARIANCE_HINTS = ("variance", "var", "delta", "vs", "v.s", "diff")


@dataclass
class MdxPlan:
    mdx: str
    confidence: float
    pattern: str
    assumptions: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "pattern": self.pattern,
            "confidence": round(self.confidence, 2),
            "assumptions": list(self.assumptions),
        }


def try_plan_mdx(
    question: str,
    schema: dict,
    model_profile: dict | None = None,
    element_matches: list[tuple[str, str, str]] | None = None,
) -> MdxPlan | None:
    """Return an MDX plan when the question matches a known pattern."""
    plan = _try_pnl_plan(question, schema, model_profile, element_matches or [])
    if plan and plan.confidence >= MIN_CONFIDENCE:
        return plan
    return None


# ── P&L / Income statement planner ────────────────────────────────────────────


def _try_pnl_plan(
    question: str,
    schema: dict,
    model_profile: dict | None,
    element_matches: list[tuple[str, str, str]],
) -> MdxPlan | None:
    if not _PNL_PATTERN.search(question):
        return None

    cube_name = str(schema.get("cube", ""))
    dimensions = list(schema.get("dimensions", []))
    if not cube_name or not dimensions:
        return None

    confidence = 1.0
    assumptions: list[str] = []

    finance_semantics = _get_finance_concept(model_profile, "income_statement")
    if not finance_semantics or finance_semantics.get("confidence", 0) < 0.4:
        confidence -= 0.25

    # Locate line-item dim
    line_dim = _resolve_line_item_dim(dimensions, finance_semantics)
    if not line_dim:
        return None
    top_consolidation, picked_by = _pick_pnl_top_consolidation(line_dim, question)
    if not top_consolidation:
        return None
    if picked_by == "qualifier_missing":
        confidence -= 0.20
        qualifier_label = (_detect_pnl_qualifier(question) or (set(), ""))[1]
        assumptions.append(
            f"Line items: expanding **{line_dim['name']}.{top_consolidation}** "
            f"(you asked for **{qualifier_label}**, but this cube has no consolidation "
            f"with that name — fell back to the default P&L bottom line)"
        )
    elif picked_by == "fuzzy":
        confidence -= 0.10
        assumptions.append(
            f"Line items: expanding **{line_dim['name']}.{top_consolidation}** "
            f"(picked the top consolidation; reply with a different parent if you meant another section)"
        )
    else:
        assumptions.append(
            f"Line items: expanding **{line_dim['name']}.{top_consolidation}**"
        )

    # Locate measure dim
    measure_dim = next((d for d in dimensions if d.get("is_measure")), None)
    measure_element, measure_picked = _pick_pnl_measure(measure_dim, finance_semantics)
    if measure_dim and not measure_element:
        return None
    if measure_dim and measure_picked == "default":
        confidence -= 0.05
        assumptions.append(f"Measure: **{measure_element}** (default for cube)")
    elif measure_dim and measure_picked == "profile":
        assumptions.append(f"Measure: **{measure_element}**")

    # Time dimensions: year + period
    year_dim, period_dim = _split_time_dims(dimensions)
    year_element, year_source = _pick_year(question, model_profile, year_dim)
    if year_dim and not year_element:
        return None
    # Single-year assumption is added later, once we know if a multi-year set
    # is going on columns (in which case the single-year line is misleading).

    # Scenario - look for "budget vs actual" style phrases or standalone scenario
    # terms before deferring to LLM. If the question references scenarios but we
    # can't resolve them to actual elements, then defer.
    scenario_dim, scenario_elements, scenario_status = _resolve_scenario_axis(
        question, dimensions
    )
    if scenario_status == "unresolved":
        return None  # User mentioned scenarios, but they don't exist as elements.

    wants_breakdown_by_month = bool(_BY_MONTH_PATTERN.search(question))
    wants_breakdown_by_quarter = bool(_BY_QUARTER_PATTERN.search(question))
    wants_breakdown_by_scenario = bool(_BY_SCENARIO_PATTERN.search(question))

    # Year pair / set: "2024 vs 2025" or "compare 2024 and 2025" -> put both years
    # on cols and skip the WHERE year filter.
    year_set: list[str] = []
    if year_dim:
        years_in_question = list(dict.fromkeys(_YEAR_PATTERN.findall(question)))
        elements_lower = {str(e).lower(): str(e) for e in year_dim.get("elements", [])}
        resolved = [elements_lower[y.lower()] for y in years_in_question if y.lower() in elements_lower]
        if len(resolved) >= 2:
            year_set = resolved[:4]

    period_on_columns = bool(period_dim) and (wants_breakdown_by_month or wants_breakdown_by_quarter)
    scenario_on_columns = (
        scenario_dim is not None
        and (len(scenario_elements) >= 2 or wants_breakdown_by_scenario)
    )
    year_on_columns = bool(year_set)

    # Now that we know if year is on cols, surface the single-year assumption
    # only when the year ends up as a filter (WHERE or single-year COL).
    if year_dim and not year_on_columns:
        if year_source != "question":
            confidence -= 0.10
            assumptions.append(f"Year: **{year_element}** ({year_source})")
        else:
            assumptions.append(f"Year: **{year_element}**")

    if period_on_columns:
        # When years are on cols too, the period parent is the dim's neutral
        # default (e.g. "All Year"), not a specific year.
        period_parent = period_dim.get("default_element") if year_on_columns else (year_element or period_dim.get("default_element"))
        assumptions.append(
            f"Columns: **{period_dim['name']}.{period_parent}.Children**"
            if period_parent else
            f"Columns: **{period_dim['name']}** members"
        )
    if year_on_columns:
        assumptions.append(
            "Year set on columns: " + " + ".join(f"**{y}**" for y in year_set)
        )
    if scenario_on_columns:
        if scenario_elements:
            assumptions.append(
                "Scenario set on columns: "
                + " + ".join(f"**{e}**" for e in scenario_elements)
            )
        else:
            assumptions.append(f"Columns: **{scenario_dim['name']}** members")

    # Build COLS as one or more sets separated by `*` (cross-product).
    col_sets: list[str] = []
    if year_on_columns and year_dim:
        col_sets.append(
            ", ".join(_mdx_member(year_dim["name"], y) for y in year_set)
        )
    if period_on_columns and period_dim:
        parent = period_dim.get("default_element") if year_on_columns else (year_element or period_dim.get("default_element"))
        if not parent:
            return None
        col_sets.append(f"{_mdx_member(period_dim['name'], parent)}.Children")
    if scenario_on_columns and scenario_dim is not None:
        if scenario_elements:
            col_sets.append(
                ", ".join(_mdx_member(scenario_dim["name"], e) for e in scenario_elements)
            )
        else:
            parent = scenario_dim.get("default_element")
            if not parent:
                return None
            col_sets.append(f"{_mdx_member(scenario_dim['name'], parent)}.Children")
    if not col_sets:
        # Default: put the year on cols so the user sees a labelled value column.
        if year_dim and year_element:
            col_sets.append(_mdx_member(year_dim["name"], year_element))
        else:
            col_sets.append("")  # empty cols - we'll skip cols entirely below
    cols = " * ".join(f"{{{s}}}" for s in col_sets if s)
    # Track dims-on-columns by name (dicts aren't hashable, so no set of dicts).
    cols_dim_names: set[str] = set()
    if year_on_columns and year_dim:
        cols_dim_names.add(year_dim["name"])
    if period_on_columns and period_dim:
        cols_dim_names.add(period_dim["name"])
    if scenario_on_columns and scenario_dim is not None:
        cols_dim_names.add(scenario_dim["name"])
    if (not col_sets[0] == "") and (not period_on_columns) and (not scenario_on_columns) and (not year_on_columns) and year_dim:
        cols_dim_names.add(year_dim["name"])

    # WHERE: every dim not on rows/cols gets a single element.
    profile_roles = (model_profile or {}).get("dim_roles") or {}
    matched_by_dim = _matches_by_dim(element_matches, dimensions, profile_roles)
    where_parts: list[str] = []

    if year_dim and year_dim["name"] not in cols_dim_names and year_element:
        where_parts.append(_mdx_member(year_dim["name"], year_element))
    if period_dim and period_dim["name"] not in cols_dim_names:
        period_filter = period_dim.get("default_element")
        if not period_filter:
            return None
        assumptions.append(f"Period filter: **{period_filter}**")
        where_parts.append(_mdx_member(period_dim["name"], period_filter))
    if measure_dim and measure_element:
        where_parts.append(_mdx_member(measure_dim["name"], measure_element))
    if scenario_dim and scenario_dim["name"] not in cols_dim_names:
        if len(scenario_elements) == 1:
            assumptions.append(f"Scenario: **{scenario_elements[0]}**")
            where_parts.append(_mdx_member(scenario_dim["name"], scenario_elements[0]))
        else:
            default = scenario_dim.get("default_element")
            if not default:
                return None
            confidence -= 0.05
            where_parts.append(_mdx_member(scenario_dim["name"], default))

    explicit_names = {d["name"] for d in (line_dim, measure_dim, year_dim, period_dim, scenario_dim) if d}
    other_dims = [
        d for d in dimensions
        if d.get("name")
        and d["name"] not in explicit_names
        and d["name"] not in cols_dim_names
    ]
    profile_overrides = _profile_dim_defaults(model_profile)
    profile_roles = (model_profile or {}).get("dim_roles") or {}
    currency_view = _detect_currency_view(question)
    for dim in other_dims:
        name = dim.get("name", "")
        if not name:
            continue
        chosen = matched_by_dim.get(name)
        if chosen:
            assumptions.append(f"{name}: **{chosen}** (from question)")
            where_parts.append(_mdx_member(name, chosen))
            continue
        # Currency-aware pick. Different models put currency translation in
        # different dims — content-classified roles route to the right picker:
        #   currency_view  → element attribute describes Local/Parent/Translated
        #   currency_code  → element is an ISO code (USD/EUR/HKD/...)
        # The currency intent wins over a profile pin because the user's
        # explicit "in USD" / "consolidated" intent should override a static
        # default.
        from .dim_roles import get_dim_role
        role = get_dim_role(dim, profile_roles)
        if role == "currency_view":
            cv_element = _pick_currency_view_element(dim, currency_view)
            if cv_element:
                assumptions.append(
                    f"{name}: **{cv_element}** ({currency_view} currency view)"
                )
                where_parts.append(_mdx_member(name, cv_element))
                continue
        elif role == "currency_code":
            cc_element = _pick_currency_code_element(dim, currency_view)
            if cc_element:
                assumptions.append(
                    f"{name}: **{cc_element}** ({currency_view} currency)"
                )
                where_parts.append(_mdx_member(name, cc_element))
                continue
        override = profile_overrides.get(name)
        if override:
            assumptions.append(f"{name}: **{override}** (profile default)")
            where_parts.append(_mdx_member(name, override))
            continue
        default = dim.get("default_element")
        if not default:
            return None
        confidence -= 0.04
        where_parts.append(_mdx_member(name, default))

    rows = f"{{Descendants({_mdx_member(line_dim['name'], top_consolidation)}, 99, LEAVES)}}"
    mdx = (
        f"SELECT {cols} ON COLUMNS, {rows} ON ROWS "
        f"FROM [{cube_name}] "
        f"WHERE ({', '.join(where_parts)})"
    )

    confidence = max(0.0, min(1.0, confidence))
    return MdxPlan(
        mdx=mdx,
        confidence=confidence,
        pattern="income_statement",
        assumptions=assumptions,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────


def _get_finance_concept(model_profile: dict | None, concept: str) -> dict | None:
    if not model_profile:
        return None
    semantics = model_profile.get("finance_semantics") or {}
    concepts = semantics.get("concepts") or {}
    info = concepts.get(concept) or {}
    return info if info else None


_LINE_ITEM_NAME_HINTS = ("account", "line item", "chart of accounts", "p&l account", "gl")

# P&L-flavoured vocabulary that we look for inside element / consolidation names.
# Hits on these tokens are what tell us a dimension actually carries financial
# statement line items, vs. being a reporting layout / disclosure dim whose
# elements are just codes or labels.
_PNL_ELEMENT_TOKENS = (
    "revenue", "sales", "income", "cost", "expense", "expenses",
    "gross profit", "operating profit", "operating expense", "operating income",
    "net income", "net profit", "ebitda", "ebit", "earnings", "margin",
    "depreciation", "amortization", "amortisation", "impairment", "impairm",
    "interest", "tax", "taxes", "deferred",
    "cogs", "cost of goods", "cost of sales",
    "salary", "salaries", "wages", "compensation", "benefits", "bonus",
    "rent", "utilities", "marketing", "advertising",
    "loss", "profit", "fee", "fees", "charge", "charges",
)


def _score_line_item_dim(dim: dict) -> int:
    """Count P&L-flavoured tokens across the dim's elements + consolidations."""
    bag: list[str] = []
    bag.extend(str(e) for e in dim.get("elements", []) or [])
    bag.extend(str(e) for e in dim.get("consolidations", []) or [])
    bag.extend(str(e) for e in dim.get("top_consolidations", []) or [])
    text = " ".join(b.lower() for b in bag)
    return sum(1 for token in _PNL_ELEMENT_TOKENS if token in text)


def _resolve_line_item_dim(dimensions: list[dict], finance_semantics: dict | None) -> dict | None:
    """
    Pick the line-item dimension by inspecting ELEMENT CONTENT - whichever non-
    measure dim contains the most P&L-flavoured element names (revenue, cost,
    expense, depreciation, ...) wins. This finds the cube's true source-of-
    truth account dim even when a sibling "Account Report" / "Account
    Disclosure" dim is named similarly but only contains codes/layout labels.

    Schema position is used only as a tie-breaker (earlier dim wins on ties),
    and the dim must contain at least one P&L token OR an account-flavoured
    name to be eligible.
    """
    candidates: list[tuple[int, int, dict]] = []
    for index, dim in enumerate(dimensions):
        if dim.get("is_measure"):
            continue
        name = str(dim.get("name", "")).lower()
        name_match = any(hint in name for hint in _LINE_ITEM_NAME_HINTS)
        element_score = _score_line_item_dim(dim)
        if not name_match and element_score == 0:
            continue
        # Name-flavoured dims still count, but element score dominates so the
        # right dim wins even when several have account-y names.
        score = element_score * 3 + (1 if name_match else 0)
        candidates.append((score, -index, dim))  # higher score wins; ties → earlier dim
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][2]


# P&L bottom-line hints, ordered by how "final" the line is in the income-
# statement flow. After-tax > before-tax > operating > gross > all accounts.
# Each hint is a token set; we match a candidate when its words contain all
# of the hint's words, so "Profit / (Loss) after Tax" still matches the hint
# "profit after tax" even though there's a slash + (Loss) in the middle.
_PNL_PARENT_HINTS = [
    # most-final bottom lines
    "net income",
    "net profit",
    "net earnings",
    "profit after tax",
    "loss after tax",
    "earnings after tax",
    "result for year",
    "result for period",
    "total comprehensive income",
    "comprehensive income",
    # pre-tax
    "profit before tax",
    "loss before tax",
    "earnings before tax",
    "ebit",
    "ebitda",
    # operating
    "operating profit",
    "operating income",
    "operating result",
    # gross
    "gross profit",
    "gross margin",
    # explicit statement names
    "income statement",
    "profit and loss",
    "profit loss statement",
    "p and l",         # "P&L" / "P & L" after & → and normalisation
    "pnl",             # "PnL" style
    "p l statement",
    # universal aggregate (last-resort - only when no specific P&L line found)
    "all account",
]


def _pnl_hint_score(candidate: str, hint: str) -> int:
    """Return the hint's specificity score if all of its words appear in the
    candidate name; 0 if it doesn't apply. Higher = more specific P&L bottom
    line. Normalises punctuation so "Profit / (Loss) after Tax" matches the
    hint "profit after tax", and "&" is expanded to "and" so "Profit & Loss"
    matches the hint "profit and loss"."""
    normalised = re.sub(r"\s*&\s*", " and ", candidate.lower())
    cand_words = set(re.findall(r"[a-z]+", normalised))
    hint_words = set(re.findall(r"[a-z]+", hint.lower()))
    if not hint_words or not hint_words.issubset(cand_words):
        return 0
    return len(hint_words)


# Question-side qualifiers. If the user explicitly named one of these in the
# question, the picker must select a candidate whose name contains BOTH
# tokens, overriding the default after-tax preference. Patterns are anchored
# enough that "before tax" and "after tax" are distinguished, and "EBIT" is
# distinguished from "EBITDA". The label is also returned so we can surface
# in assumptions when the qualifier couldn't be honoured.
_PNL_QUALIFIER_RULES: list[tuple[str, set[str], str]] = [
    (r"\b(before|pre)[\s\-]*tax\b|\bpbt\b",            {"before", "tax"},     "before tax"),
    (r"\b(after|post)[\s\-]*tax\b|\bpat\b",            {"after", "tax"},      "after tax"),
    (r"\bebitda\b",                                    {"ebitda"},            "EBITDA"),
    (r"\bebit\b(?!da)",                                {"ebit"},              "EBIT"),
    (r"\boperating\s+profit\b",                        {"operating", "profit"}, "operating profit"),
    (r"\boperating\s+income\b",                        {"operating", "income"}, "operating income"),
    (r"\boperating\s+result\b",                        {"operating", "result"}, "operating result"),
    (r"\bgross\s+profit\b",                            {"gross", "profit"},   "gross profit"),
    (r"\bgross\s+margin\b",                            {"gross", "margin"},   "gross margin"),
    (r"\b(total\s+)?comprehensive\s+income\b",         {"comprehensive", "income"}, "comprehensive income"),
    (r"\bnet\s+income\b",                              {"net", "income"},     "net income"),
    (r"\bnet\s+profit\b",                              {"net", "profit"},     "net profit"),
    (r"\bnet\s+earnings\b",                            {"net", "earnings"},   "net earnings"),
]


def _detect_pnl_qualifier(question: str) -> tuple[set[str], str] | None:
    """If the user's question contains an explicit P&L line qualifier,
    return (token_set, label). The picker then forces a candidate that
    contains all those tokens."""
    text = (question or "").lower()
    for pattern, must_match, label in _PNL_QUALIFIER_RULES:
        if re.search(pattern, text):
            return must_match, label
    return None


def _pick_pnl_top_consolidation(line_dim: dict, question: str = "") -> tuple[str, str]:
    """Return (chosen_element, status). Status is one of:
      - "exact"             : matched a hint or the user's qualifier
      - "qualifier_missing" : qualifier present but no candidate matches; fell
                              back to default (caller may want to warn user)
      - "fuzzy"             : no hint matched; took first available
    """
    tops = list(line_dim.get("top_consolidations") or [])
    if not tops:
        cons = list(line_dim.get("consolidations") or [])
        if not cons:
            return "", ""
        return cons[0], "fuzzy"
    if len(tops) == 1:
        return tops[0], "exact"

    qualifier = _detect_pnl_qualifier(question)
    if qualifier is not None:
        qualifier_tokens, _label = qualifier
        for candidate in tops:
            cand_words = set(re.findall(r"[a-z]+", candidate.lower()))
            if qualifier_tokens.issubset(cand_words):
                return candidate, "exact"
        # Qualifier present but no match -> let the default flow pick, but
        # report status so the planner can surface a warning to the user.
        fallback_status = "qualifier_missing"
    else:
        fallback_status = "fuzzy"

    for hint in _PNL_PARENT_HINTS:
        best: tuple[int, str] = (0, "")
        for candidate in tops:
            score = _pnl_hint_score(candidate, hint)
            if score > best[0]:
                best = (score, candidate)
        if best[0]:
            # If user asked for a specific qualifier but we couldn't honour it,
            # mark it so the planner records the discrepancy.
            return best[1], fallback_status if fallback_status == "qualifier_missing" else "exact"

    default = str(line_dim.get("default_element", "")).strip()
    if default and default in tops:
        return default, "exact"
    return tops[0], "fuzzy"


def _pick_pnl_measure(
    measure_dim: dict | None,
    finance_semantics: dict | None,
) -> tuple[str, str]:
    if not measure_dim:
        return "", ""
    elements = [str(e) for e in measure_dim.get("elements", [])]
    if finance_semantics:
        for preferred in finance_semantics.get("preferred_measures", []):
            for element in elements:
                if str(element).lower() == str(preferred).lower():
                    return element, "profile"
    default = measure_dim.get("default_element") or (elements[0] if elements else "")
    if default:
        return default, "default"
    return "", ""


def _split_time_dims(dimensions: list[dict]) -> tuple[dict | None, dict | None]:
    """Return (year_dim, period_dim). Period is the finer time dim (months)."""
    time_dims = [d for d in dimensions if d.get("is_time_dim")]
    year_dim: dict | None = None
    period_dim: dict | None = None
    for dim in time_dims:
        name = str(dim.get("name", "")).lower()
        if "year" in name and year_dim is None:
            year_dim = dim
        elif period_dim is None and (
            "period" in name or "month" in name or "time" in name or "date" in name
        ):
            period_dim = dim
    # Fallback: if the model has only one time dim and it's called "Period",
    # treat it as the period dim with year_dim left empty.
    if not year_dim and not period_dim and time_dims:
        period_dim = time_dims[0]
    return year_dim, period_dim


def _pick_year(
    question: str,
    model_profile: dict | None,
    year_dim: dict | None,
) -> tuple[str, str]:
    if not year_dim:
        return "", ""
    elements = [str(e) for e in year_dim.get("elements", [])]
    # 1. From question
    match = _YEAR_PATTERN.search(question)
    if match and match.group(1) in elements:
        return match.group(1), "question"
    # 2. From profile default
    default_filters = (model_profile or {}).get("default_filters", {})
    profile_year = str(default_filters.get("Year", "")).strip()
    if profile_year and profile_year in elements:
        return profile_year, "profile default"
    # 3. From dim default
    dim_default = str(year_dim.get("default_element", "")).strip()
    if dim_default:
        return dim_default, "dim default"
    # 4. Current year
    today = str(date.today().year)
    if today in elements:
        return today, "current year"
    return (elements[0] if elements else ""), "first element"


def _matches_by_dim(
    matches: list[tuple[str, str, str]],
    schema_dims: list[dict],
    profile_roles: dict[str, str] | None = None,
) -> dict[str, str]:
    """Map element matches from the question onto dims.

    Two filters keep us out of trouble:
      1. ROLE filter - only `entity_subject` dims (Company, Cost Center, ...)
         accept entity-name matches like "SLIM-HK". Counterparty / classifier /
         data_source / time / scenario dims keep their default_filters even
         if they happen to contain an element of the same name.
      2. Token-level dedup - same phrase (punctuation-normalised) maps to at
         most one dim, so a single mention can't cascade.
    """
    from .dim_roles import accepts_entity_name, get_dim_role

    dim_by_name = {str(d.get("name", "")): d for d in schema_dims if d.get("name")}
    out: dict[str, str] = {}
    used_phrases: set[str] = set()
    for phrase, dim_name, element_name in matches:
        if dim_name not in dim_by_name or dim_name in out:
            continue
        role = get_dim_role(dim_by_name[dim_name], profile_roles)
        if not accepts_entity_name(role):
            continue
        key = re.sub(r"[^a-z0-9]+", "", str(phrase).lower())
        if not key or key in used_phrases:
            continue
        used_phrases.add(key)
        out[dim_name] = element_name
    return out


def _mdx_member(dim_name: str, element: str) -> str:
    return f"[{dim_name}].[{dim_name}].[{element}]"


def _detect_currency_view(question: str) -> str:
    """Thin wrapper that reuses query_intent's detector so planner stays in
    sync with the upstream intent classifier."""
    from .query_intent import _detect_currency_view as upstream
    return upstream(question)


# Default parent / reporting currency code if a model has a `currency_code`
# role dim and the user asked for "parent / consolidated / USD" without
# naming a specific currency. USD covers the vast majority of TM1 models.
_DEFAULT_PARENT_CURRENCY_CODE = "USD"


def _pick_currency_code_element(dim: dict, currency_view: str) -> str:
    """Pick from a `currency_code` role dim (elements are ISO codes like
    USD/EUR/HKD/...). For 'parent' intent, default to USD if it exists;
    for 'local' intent, leave it to the dim's natural default unless the
    user explicitly named a currency in the question (handled upstream by
    element-name matching).

    Note: this picker only fires when no element name was matched from the
    question. If user said "in EUR", upstream element matching will already
    have set EUR as the WHERE filter, so we wouldn't be called.
    """
    elements = [str(e) for e in (dim.get("elements") or []) if e]
    if not elements:
        return ""

    upper_map = {str(e).upper(): str(e) for e in elements}

    if currency_view == "parent":
        # Prefer the model's parent / reporting currency code if present.
        for candidate in (_DEFAULT_PARENT_CURRENCY_CODE, "EUR", "GBP"):
            if candidate in upper_map:
                return upper_map[candidate]
        # No common parent code matched — leave to other logic.
        return ""

    # currency_view == "local": don't auto-pick; the planner upstream will
    # fall through to profile pin / default_element.
    return ""


# Description / label tokens that mark an element as a specific currency view.
_LOCAL_CURRENCY_DESC_TOKENS = ("local currency", "entity currency", "lcy", "as reported")
_PARENT_CURRENCY_DESC_TOKENS = (
    "parent currency", "translated", "consolidated total", "pct",
    "usd total", "group currency",
)
# Tokens that DISQUALIFY (these are partial views, not the whole picture).
_PARTIAL_VIEW_TOKENS = (
    "adjustment", "override", "top side", "mgmt adj", "elimination",
)


def _pick_currency_view_element(dim: dict, currency_view: str) -> str:
    """Pick the right element from a `currency_view` role dim (where elements
    describe translation methodology — EC / PC / PCT / ECT / etc.).

      - local:  prefer Description="Local Currency" / "Entity Currency" / label "LCY",
                avoid adjustments / translations / GAAP variants → typically EC
      - parent: prefer Description containing "Parent Currency"/"Translated"
                or name matching PCT / PC* / USD Total → the consolidated
                view including adjustments
    """
    attrs = dim.get("element_attr_values") or {}
    weights = dim.get("child_weights") or {}
    if not attrs:
        return ""

    target_tokens = (
        _LOCAL_CURRENCY_DESC_TOKENS if currency_view == "local"
        else _PARENT_CURRENCY_DESC_TOKENS
    )
    avoid_tokens = (
        _PARENT_CURRENCY_DESC_TOKENS + ("usd", "gaap")
        if currency_view == "local"
        else _LOCAL_CURRENCY_DESC_TOKENS
    )

    best: tuple[int, float, str] = (0, 0.0, "")
    for elem, info in attrs.items():
        if weights.get(elem, 1.0) <= 0:
            continue
        desc = str(info.get("Description", "")).lower()
        label = str(info.get("Label", "")).lower()
        alias = str(info.get("Alias", "")).lower()
        name_low = str(elem).lower()
        text = f"{desc} {label} {alias}"

        score = 0
        # Strong positive signals
        for tok in target_tokens:
            if tok in text:
                score += 5
        # Partial-view penalty (adjustment / GAAP-only variants are NEVER the
        # right "single source" pick).
        for tok in _PARTIAL_VIEW_TOKENS:
            if tok in text:
                score -= 8
        # Negative for the OPPOSITE currency view
        for tok in avoid_tokens:
            if tok in text:
                score -= 4
        # Name pattern bonuses (TM1 conventions)
        if currency_view == "local":
            if name_low == "ec":
                score += 6  # canonical entity currency code
            elif re.match(r"^ec[at]?(\s|\()|^ec_", name_low):
                score += 1  # ECT / ECA / ECGAAP — related but partial
        else:  # parent
            if name_low == "pct":
                score += 8  # Parent Currency Total — exactly what user wants
            elif name_low == "pc" or name_low.startswith("pc_"):
                score += 6
            elif name_low == "usd":
                score += 3
            elif "usd total" in text:
                score += 4

        if score <= 0:
            continue
        elem_weight = weights.get(elem, 1.0)
        if (score, elem_weight) > (best[0], best[1]):
            best = (score, elem_weight, elem)
    return best[2]


# Known time-related keys that mean a date/year/month, not a literal dim name.
_RESERVED_DEFAULT_FILTER_KEYS = {
    "current_year", "current_month", "current_day", "current_week",
    "forecast_year", "source", "used_real_current_date",
    "Month", "Year",  # picked via _pick_year / time-dim logic, not generic overrides
}


def _profile_dim_defaults(model_profile: dict | None) -> dict[str, str]:
    """Per-dimension WHERE overrides from `default_filters` in the profile.

    Lets the user pin a known-good filter element for any dim whose
    schema-derived default_element returns empty cells (common for data-source,
    segment, or other dimensions where the cube has no rule feeding the
    'All ...' consolidation). Reserved keys like 'Year' / 'current_year' /
    'source' are excluded - those are handled separately."""
    raw = (model_profile or {}).get("default_filters") or {}
    if not isinstance(raw, dict):
        return {}
    out: dict[str, str] = {}
    for key, value in raw.items():
        if key in _RESERVED_DEFAULT_FILTER_KEYS:
            continue
        if not isinstance(value, str) or not value.strip():
            continue
        out[str(key)] = value.strip()
    return out


# ── Scenario resolver ─────────────────────────────────────────────────────────


def _resolve_scenario_axis(
    question: str,
    dimensions: list[dict],
) -> tuple[dict | None, list[str], str]:
    """
    Inspect the question for scenario references and resolve them to actual
    elements in a Scenario/Version dimension.

    Returns (scenario_dim, [elements], status):
      - ("none"):  no scenario terms in question -> caller uses default element
      - ("pair"):  exactly two scenario elements found -> put set on COLS
      - ("single"): one scenario element found -> filter via WHERE
      - ("unresolved"): question mentions scenario terms but no element matches
        -> caller should defer to LLM
    """
    scenario_dim = _find_scenario_dim(dimensions)
    if scenario_dim is None:
        if _SCENARIO_SINGLE_RE.search(question) or re.search(
            r"\b(vs\.?|versus|variance|forecast vs|budget vs)\b", question, re.IGNORECASE
        ):
            return None, [], "unresolved"
        return None, [], "none"

    elements = [str(e) for e in scenario_dim.get("elements", [])]
    consolidations = [str(e) for e in scenario_dim.get("consolidations", [])]

    pair_match = _SCENARIO_PAIR_RE.search(question)
    if pair_match:
        a_term = _TERM_TO_CANON.get(pair_match.group(1).lower(), pair_match.group(1).lower())
        b_term = _TERM_TO_CANON.get(pair_match.group(2).lower(), pair_match.group(2).lower())
        a_elem = _find_scenario_element(a_term, elements)
        b_elem = _find_scenario_element(b_term, elements)
        if a_elem and b_elem and a_elem != b_elem:
            return scenario_dim, [a_elem, b_elem], "pair"
        # Try a Variance / "actual vs budget" consolidation when both bare terms
        # don't exist as elements (some TM1 models only expose the variance).
        variance_elem = _find_variance_element(consolidations + elements)
        if variance_elem:
            return scenario_dim, [variance_elem], "single"
        return scenario_dim, [], "unresolved"

    # No explicit pair, but maybe a single scenario term ("for actual", "budget P&L")
    singles = list(_SCENARIO_SINGLE_RE.finditer(question))
    if singles:
        # If the question contains "variance"/"var"/"delta" → look for a
        # variance-like consolidation rather than the bare "variance" element.
        if any(re.search(r"\b(?:variance|var|delta|diff)\b", m.group(0), re.IGNORECASE) for m in singles):
            v = _find_variance_element(consolidations + elements)
            if v:
                return scenario_dim, [v], "single"
        for m in singles:
            term = _TERM_TO_CANON.get(m.group(1).lower(), m.group(1).lower())
            elem = _find_scenario_element(term, elements)
            if elem:
                return scenario_dim, [elem], "single"
        return scenario_dim, [], "unresolved"

    return scenario_dim, [], "none"


def _find_scenario_dim(dimensions: list[dict]) -> dict | None:
    # Prefer dim whose name contains "scenario" or "version".
    for dim in dimensions:
        if dim.get("is_measure"):
            continue
        name = str(dim.get("name", "")).lower()
        if "scenario" in name or "version" in name:
            return dim
    # Fallback: any dim that exposes both an actual-like and budget-like element.
    for dim in dimensions:
        if dim.get("is_measure"):
            continue
        lower_elements = [str(e).lower() for e in dim.get("elements", [])]
        has_actual = any("actual" in e for e in lower_elements)
        has_budget = any("budget" in e or "plan" in e or "forecast" in e for e in lower_elements)
        if has_actual and has_budget:
            return dim
    return None


# Known abbreviations TM1 models use for scenario elements. Mapping from
# canonical term -> common code-form names.
_SCENARIO_ABBREVS = {
    "actual": ["act", "actl", "actuals", "ac"],
    "budget": ["bud", "bgt", "bdg", "budg"],
    "forecast": ["fcst", "fc", "fcs", "for"],
    "plan": ["pln", "planned"],
    "target": ["tgt", "tg"],
}


def _find_scenario_element(term: str, elements: list[str]) -> str:
    """Return the element whose name best matches `term`. Tries the canonical
    term first, then common TM1 abbreviations (so "actual" finds an "ACT"
    element)."""
    candidates = [term.lower()] + _SCENARIO_ABBREVS.get(term.lower(), [])
    for needle in candidates:
        # Exact (case-insensitive)
        for e in elements:
            if e.lower() == needle:
                return e
        # Word-boundary
        for e in elements:
            if re.search(rf"\b{re.escape(needle)}\b", e.lower()):
                return e
        # Substring
        for e in elements:
            if needle in e.lower():
                return e
    return ""


def _find_variance_element(candidates: list[str]) -> str:
    """Return an element that looks like a pre-computed variance/diff scenario."""
    for hint in _VARIANCE_HINTS:
        for e in candidates:
            if hint in e.lower():
                return e
    return ""
