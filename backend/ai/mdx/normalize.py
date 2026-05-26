"""Post-process and validate MDX returned by the LLM."""

import re
from datetime import date

_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_SPECIFIC_SUBYEAR_RE = re.compile(
    r"\b("
    r"jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|"
    r"jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|"
    r"oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|"
    r"q[1-4]|quarter|h[12]|half|"
    r"month\s+\d{1,2}|\d{1,2}\s*month"
    r")\b",
    re.I,
)
_PRIOR_YEAR_COMPARE_RE = re.compile(
    r"\b(prior|previous|last)\s+year\b|\byear\s+over\s+year\b|\byoy\b",
    re.I,
)


def _implies_full_period_year_comparison(question: str) -> bool:
    """Year-only comparisons should use the all-period member, not one month."""
    q = question or ""
    if _SPECIFIC_SUBYEAR_RE.search(q):
        return False
    years = list(dict.fromkeys(_YEAR_RE.findall(q)))
    if len(years) >= 2:
        return True
    return bool(_PRIOR_YEAR_COMPARE_RE.search(q) and years)


def _fix_mdx_structure(mdx: str) -> str:
    """Fix WHERE appearing before FROM by swapping them to the correct order."""
    from_m = re.search(r"(?i)\bFROM\s+\[", mdx)
    where_m = re.search(r"(?i)\bWHERE\s*\(", mdx)
    if not from_m or not where_m or where_m.start() > from_m.start():
        return mdx
    from_clause = re.search(r"(?i)(FROM\s+\[[^\]]+\])", mdx)
    if not from_clause:
        return mdx
    fc = from_clause.group(1)
    mdx_no_from = mdx[: from_clause.start()] + mdx[from_clause.end():]
    where_in_stripped = re.search(r"(?i)\bWHERE\s*\(", mdx_no_from)
    if not where_in_stripped:
        return mdx
    pos = where_in_stripped.start()
    return mdx_no_from[:pos].rstrip() + "\n" + fc + "\n" + mdx_no_from[pos:]


def _fix_unrequested_time_rollup(mdx: str, question: str, model_profile: dict | None) -> str:
    """Replace YTD/MTD/etc. rollup picks with the profile default month when
    the user did not explicitly ask for a cumulative period."""
    if re.search(r"\b(ytd|fytd|ytg|fytg|qtd|mtd|full year|all periods)\b", question, re.I):
        return mdx
    if _implies_full_period_year_comparison(question):
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
