"""Classify TM1 dimensions by their semantic role in financial reporting.

The role tag drives planner / validator behaviour: an entity-name mention in
the user's question ("SLIM-HK") should only resolve onto an `entity_subject`
dim (Company, Cost Center, Employee, ...), NOT onto a `counterparty`
(Intercompany) or `business_classifier` (Segment, Category, Channel, ...)
dim that happens to contain the same string as one of its elements.

Roles are deterministic from dim names + the schema-cache `is_measure` /
`is_time_dim` flags. The map is stored in the model profile under
`dim_roles` so it survives across requests and can be hand-tuned per model.
LLM augmentation for cryptic dim names is left as a phase-2 refinement -
unknown dims default to `unclassified` (safe: filter only, no entity matches).
"""

from __future__ import annotations

import re


# Common ISO 4217 currency codes - used to detect a "currency_code" dim by
# inspecting whether its elements look like real-world currency abbreviations.
_COMMON_ISO_CURRENCIES = frozenset({
    "USD", "EUR", "GBP", "JPY", "CNY", "HKD", "CHF", "AUD", "CAD", "SGD",
    "INR", "KRW", "TWD", "MYR", "THB", "PHP", "IDR", "VND", "NZD", "NOK",
    "SEK", "DKK", "PLN", "TRY", "RUB", "BRL", "MXN", "ZAR", "ILS", "AED",
    "SAR", "QAR", "KWD", "ARS", "CLP", "COP", "PEN", "EGP", "PKR", "BDT",
})


# Phrases that indicate "this dim describes a currency translation methodology"
# (Local vs Parent vs Translated). Matched against element Description / Alias.
_CURRENCY_VIEW_SIGNAL_TOKENS = (
    "local currency", "parent currency", "entity currency",
    "translated", "translation",
    "reporting currency",
)


def classify_dim_by_content(dim: dict) -> str | None:
    """Inspect a dim's elements + their attribute values and return a role
    inferred from CONTENT. This trumps name-based classification because dim
    names are conventions ("S Consol GL Company") but elements are facts.

    Returns one of:
      - "currency_code"  - elements look like ISO currency abbreviations
      - "currency_view"  - elements describe a currency translation view
                           (Description="Local Currency" / "Parent Currency"
                           / "Translated" / similar)
      - None             - no strong content signal; caller should fall back
                           to name-based classification.
    """
    elements = [str(e) for e in (dim.get("elements") or []) if e]
    if not elements:
        return None

    # 1. ISO currency code dim — most or many leaf elements are ISO codes.
    upper_elems = [e.upper() for e in elements if re.match(r"^[A-Za-z]{3}$", e)]
    iso_hits = sum(1 for e in upper_elems if e in _COMMON_ISO_CURRENCIES)
    if iso_hits >= 3 and iso_hits >= len(elements) * 0.25:
        return "currency_code"

    # 2. Currency-view dim — attribute descriptions describe translation
    # methodology in the local/parent/translated sense.
    attrs = dim.get("element_attr_values") or {}
    desc_blob = " ".join(
        " ".join(str(v) for v in (info or {}).values())
        for info in attrs.values()
    ).lower()
    if not desc_blob:
        return None
    cv_hits = sum(1 for tok in _CURRENCY_VIEW_SIGNAL_TOKENS if tok in desc_blob)
    if cv_hits >= 1:
        return "currency_view"

    return None


# Ordered most-specific-first so the regex chain stops at the right role.
# Patterns match on the lower-cased dim name with word boundaries.
_ROLE_PATTERNS: list[tuple[str, list[str]]] = [
    # Scenario / version
    ("scenario", [
        r"\bscenario\b", r"\bversion\b",
    ]),
    # Line-item dims (account-like, source-of-truth GL)
    ("line_item", [
        r"\bchart\s+of\s+accounts\b",
        r"\bp\s*&\s*l\s+account\b",
        r"\bgl\s+account\b",
        # "Line Item" only when it's not a generic metadata dim
        r"^line\s+item$",
    ]),
    # Counterparty / partner dims.
    # "Intercompany Category" is a business_classifier (Trade/Dividend/Loan…),
    # not individual IC counterparties — the negative lookahead prevents the
    # "intercompany" token from stealing it before business_classifier can match.
    ("counterparty", [
        r"\bintercompany\b(?!\s+category\b)", r"\bintercos?\b",
        r"\bcounter[\s_-]?party\b", r"\bcpty\b",
        r"\bic\s+partner\b", r"\bpartner\b",
    ]),
    # Data-source / system dims (often need a specific leaf, not All_XYZ)
    ("data_source", [
        r"\bdata\s+source\b", r"\bsource\s+system\b",
        r"\bs[\s_]+consol\b",          # "S Consol GL Company" etc.
        r"\bconsol\s+source\b",
    ]),
    # Reporting / disclosure dims (secondary account dims)
    ("business_classifier", [
        r"\baccount\s+(report|disclosure|type|class|group|category)\b",
        r"\bsegment\b", r"\bcategory\b", r"\bchannel\b", r"\bclass\b",
        r"\bdisclosure\b",
        r"\bregion\b", r"\bdivision\b", r"\bdepartment\b",
        r"\bbusiness\s+unit\b", r"\bbu\b",
        r"\bproject\b", r"\binitiative\b",
        r"\bperiod\s+type\b",          # "Period Type" - flow/balance flag
        r"\bgroup\b",                  # Group structure dims
    ]),
    # Entity subject dims (the report's "subject" — what we filter to a real value)
    ("entity_subject", [
        r"\bcompany\b", r"\bentity\b", r"\bcorp\b", r"\blegal\s+entity\b",
        r"\bcost\s*cent(er|re)\b", r"\bprofit\s*cent(er|re)\b",
        r"\bcustomer\b", r"\bemployee\b", r"\bvendor\b", r"\bsupplier\b",
        r"\bproduct\b", r"\bstore\b",
    ]),
    # Generic account-flavoured dims fall through here AFTER the
    # business_classifier match has had its chance on "account report" etc.
    ("line_item", [
        r"\baccount\b",
    ]),
    # Currency / FX
    ("currency", [
        r"\bcurrency\b", r"\bfx\s+rate\b", r"\bexchange\s+rate\b",
    ]),
    # Entry / journal metadata
    ("metadata", [
        r"\bentry\s+id\b", r"\bjournal\s+id\b",
        r"\bline\s+item\b",
        r"\b%\s*holding\b", r"\bownership\b",
    ]),
]


def classify_dim(dim: dict) -> str:
    """Return the semantic role for one dim (schema-cache style dict)."""
    if dim.get("is_measure"):
        return "measure"
    if dim.get("is_time_dim"):
        return "time"
    name = str(dim.get("name", "")).strip().lower()
    if not name:
        return "unclassified"
    for role, patterns in _ROLE_PATTERNS:
        if any(re.search(pat, name) for pat in patterns):
            return role
    return "unclassified"


def build_dim_roles_map(schema_summary: dict) -> dict[str, str]:
    """Classify every distinct dim in a schema summary by semantic role."""
    out: dict[str, str] = {}
    for cube in schema_summary.get("cubes") or []:
        for dim in cube.get("dimensions") or []:
            if isinstance(dim, str):
                synthetic = {"name": dim}
                out.setdefault(dim, get_dim_role(synthetic))
                continue
            name = str(dim.get("name", "")).strip() if isinstance(dim, dict) else ""
            if not name or name in out:
                continue
            out[name] = get_dim_role(dim)
    return out


# Roles where the user's element-name mention in the question SHOULD apply.
# Other roles should be filtered to default_filters / default_element regardless
# of whether the same string happens to be a member of that dim.
ENTITY_NAME_RECEPTIVE_ROLES: frozenset[str] = frozenset({"entity_subject"})


def accepts_entity_name(role: str) -> bool:
    return role in ENTITY_NAME_RECEPTIVE_ROLES


def get_dim_role(dim: dict, profile_roles: dict[str, str] | None = None) -> str:
    """Return the role for a dim. Order:
      1. Structural flags (is_measure / is_time_dim) — facts about the dim's
         technical type, never wrong.
      2. CONTENT-based classification — what the elements + their attributes
         actually describe. This is what "cube -> dim -> element -> attribute"
         means: dim names are conventions and may lie about purpose, but
         element content rarely does.
      3. Profile-stored override — user/operator can hand-correct any
         mis-classification by editing the JSON.
      4. Name-based regex — last-resort fallback when no other signal applies.
    """
    if dim.get("is_measure"):
        return "measure"
    if dim.get("is_time_dim"):
        # "Period Type" dims (YTD / Current / Full Year) are business classifiers
        # in TM1 even though they may be flagged is_time_dim=True by the server.
        # Name-based check wins here because the elements are not calendar values.
        _n = str(dim.get("name", "")).strip().lower()
        if re.search(r"\bperiod[\s_-]type\b", _n):
            return "business_classifier"
        return "time"
    content_role = classify_dim_by_content(dim)
    if content_role:
        return content_role
    name = str(dim.get("name", "")).strip()
    if profile_roles and name and name in profile_roles:
        stored = str(profile_roles.get(name, "")).strip()
        if stored:
            return stored
    return classify_dim(dim)
