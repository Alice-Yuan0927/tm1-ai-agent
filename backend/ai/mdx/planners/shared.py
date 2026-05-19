"""Shared types, constants, and helpers used by all MDX sub-planners."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

# ---------------------------------------------------------------------------
# Public constants (re-exported via __init__.py)
# ---------------------------------------------------------------------------

MIN_CONFIDENCE: float = 0.55
SURFACE_THRESHOLD: float = 0.85

# ---------------------------------------------------------------------------
# Shared patterns
# ---------------------------------------------------------------------------

_BY_MONTH_PATTERN = re.compile(r"\bby\s+(month|months|period|periods)\b", re.IGNORECASE)
_BY_QUARTER_PATTERN = re.compile(r"\bby\s+(quarter|quarters|q[1-4])\b", re.IGNORECASE)
_BY_SCENARIO_PATTERN = re.compile(r"\bby\s+(scenario|version)\b", re.IGNORECASE)
_YEAR_PATTERN = re.compile(r"\b(20\d{2})\b")
_YEAR_COMPARE_RE = re.compile(
    r"\b(compare|vs\.?|versus|prior\s+year|previous\s+year|last\s+year|"
    r"year.?over.?year|yoy|year.?on.?year)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Currency constants
# ---------------------------------------------------------------------------

_LOCAL_CURRENCY_DESC_TOKENS = ("local currency", "entity currency", "lcy", "as reported")
_PARENT_CURRENCY_DESC_TOKENS = (
    "parent currency", "translated", "consolidated total", "pct",
    "usd total", "group currency",
)
_PARTIAL_VIEW_TOKENS = (
    "adjustment", "override", "top side", "mgmt adj", "elimination",
)

# Keys in default_filters that are time/reserved and not generic dim overrides.
_RESERVED_DEFAULT_FILTER_KEYS = {
    "current_year", "current_month", "current_day", "current_week",
    "forecast_year", "source", "used_real_current_date",
    "Month", "Year",
}


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Basic helpers
# ---------------------------------------------------------------------------

def _mdx_member(dim_name: str, element: str) -> str:
    return f"[{dim_name}].[{dim_name}].[{element}]"


def _dedup_preserve(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _estimate_descendants(dim_name: str, element_name: str, limit: int) -> int:
    """BFS on element_edges; stops as soon as `limit` nodes are counted."""
    try:
        from ....tm1.cache.db import connect
        rows = connect().execute(
            "SELECT parent_name, child_name FROM element_edges WHERE dim_name = ?",
            (dim_name,),
        ).fetchall()
    except Exception:
        return 0

    children_by_parent: dict[str, list[str]] = {}
    for parent, child in rows:
        children_by_parent.setdefault(str(parent), []).append(str(child))

    seen: set[str] = set()
    queue = list(children_by_parent.get(element_name, []))
    while queue:
        node = queue.pop(0)
        if node in seen:
            continue
        seen.add(node)
        if len(seen) >= limit:
            return len(seen)
        queue.extend(children_by_parent.get(node, []))
    return len(seen)


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

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
    match = _YEAR_PATTERN.search(question)
    if match and match.group(1) in elements:
        return match.group(1), "question"
    default_filters = (model_profile or {}).get("default_filters", {})
    profile_year = str(default_filters.get("Year", "")).strip()
    if profile_year and profile_year in elements:
        return profile_year, "profile default"
    dim_default = str(year_dim.get("default_element", "")).strip()
    if dim_default:
        return dim_default, "dim default"
    today = str(date.today().year)
    if today in elements:
        return today, "current year"
    return (elements[0] if elements else ""), "first element"


def _best_all_period(candidates: list[str], dim_name: str = "") -> str:
    """Return the minimum-leaf all-periods aggregate from candidates."""
    agg_candidates = [
        c for c in candidates
        if re.search(r"(?i)^(all|total)\s", c)
    ]
    if not agg_candidates:
        return ""

    if dim_name and len(agg_candidates) > 1:
        scores: list[tuple[int, str]] = []
        for c in agg_candidates:
            n = _estimate_descendants(dim_name, c, limit=200)
            scores.append((n if n > 0 else 9999, c))
        scores.sort(key=lambda x: x[0])
        best_n, best_name = scores[0]
        if best_n < 9999:
            return best_name

    return agg_candidates[0]


def _pick_period(
    question: str,
    model_profile: dict | None,
    period_dim: dict | None,
    year_on_columns: bool = False,
    element_matches: list[tuple[str, str, str]] | None = None,
) -> str:
    """Semantically select the period/month element for a WHERE filter."""
    if not period_dim:
        return ""

    dim_name = period_dim["name"]
    elements: list[str] = [str(e) for e in period_dim.get("elements", [])]
    consolidations: list[str] = [str(e) for e in period_dim.get("consolidations", [])]
    known = set(elements) | set(consolidations)

    for _phrase, matched_dim, matched_elem in (element_matches or []):
        if matched_dim == dim_name and matched_elem in known:
            return matched_elem

    wants_full_year = (
        year_on_columns
        or bool(_YEAR_COMPARE_RE.search(question or ""))
    )
    aggregate = (
        _best_all_period(consolidations, dim_name)
        or _best_all_period(elements, dim_name)
    )
    if wants_full_year and aggregate:
        return aggregate
    if aggregate:
        return aggregate
    return str(period_dim.get("default_element", ""))


# ---------------------------------------------------------------------------
# WHERE / element-match helpers
# ---------------------------------------------------------------------------

def _matches_by_dim(
    matches: list[tuple[str, str, str]],
    schema_dims: list[dict],
    profile_roles: dict[str, str] | None = None,
) -> dict[str, str]:
    """Map element matches from the question onto dims (entity_subject dims only)."""
    from ...schema.dim_roles import accepts_entity_name, get_dim_role

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


def _profile_dim_defaults(model_profile: dict | None) -> dict[str, str]:
    """Per-dimension WHERE overrides from default_filters in the profile."""
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


# ---------------------------------------------------------------------------
# Currency helpers
# ---------------------------------------------------------------------------

def _detect_currency_view(question: str) -> str:
    from ...intent.query_intent import _detect_currency_view as upstream
    return upstream(question)


def _pick_currency_code_element(dim: dict, currency_view: str, question: str = "") -> str:
    """Pick from a currency_code role dim (elements are ISO codes like USD/EUR/HKD)."""
    from ....config import PARENT_CURRENCY_FALLBACKS
    elements = [str(e) for e in (dim.get("elements") or []) if e]
    if not elements:
        return ""

    upper_map = {str(e).upper(): str(e) for e in elements}
    explicit_codes = re.findall(r"\b[A-Za-z]{3}\b", question or "")
    for code in explicit_codes:
        upper = code.upper()
        if upper in upper_map:
            return upper_map[upper]

    if currency_view == "parent":
        for candidate in PARENT_CURRENCY_FALLBACKS:
            if candidate.upper() in upper_map:
                return upper_map[candidate.upper()]
        return ""
    return ""


def _pick_currency_view_element(dim: dict, currency_view: str) -> str:
    """Pick from a currency_view role dim (elements describe translation methodology)."""
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
        for tok in target_tokens:
            if tok in text:
                score += 5
        for tok in _PARTIAL_VIEW_TOKENS:
            if tok in text:
                score -= 8
        for tok in avoid_tokens:
            if tok in text:
                score -= 4
        if currency_view == "local":
            if name_low == "ec":
                score += 6
            elif re.match(r"^ec[at]?(\s|\()|^ec_", name_low):
                score += 1
        else:
            if name_low == "pct":
                score += 8
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


# ---------------------------------------------------------------------------
# Finance / measure helpers (shared by pnl and consolidation planners)
# ---------------------------------------------------------------------------

def _get_finance_concept(model_profile: dict | None, concept: str) -> dict | None:
    if not model_profile:
        return None
    semantics = model_profile.get("finance_semantics") or {}
    concepts = semantics.get("concepts") or {}
    info = concepts.get(concept) or {}
    return info if info else None


def _pick_measure(
    measure_dim: dict | None,
    finance_semantics: dict | None,
) -> tuple[str, str]:
    """Pick the best measure element: profile-preferred > dim default."""
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
