"""Shared lexical constants for TM1 intent, planning, and validation.

Keep business-language triggers here so planner, validators, clarification,
and query-intent detection do not drift independently.
"""

from __future__ import annotations

import re

STATEMENT_PATTERN = re.compile(
    r"\b(p\s*&\s*l|p\s*and\s*l|profit\s+and\s+loss|income\s+statement|"
    r"pnl|p\.?\s*&\s*l\.?\s*statement)\b",
    re.IGNORECASE,
)

STATEMENT_INTENTS = {"income_statement", "balance_sheet", "cash_flow", "trial_balance"}

# Per-intent token sets that identify a top-consolidation as belonging to that
# financial statement section.  Each inner set is a "must-all-match" group —
# any one group matching is sufficient.  "&" is normalised to "and" before
# tokenising so "Profit & Loss" hits {"profit","and","loss"}.
# Add rows here when a new statement intent is introduced; no other code needs
# to change.
STATEMENT_SECTION_VOCAB: dict[str, list[set[str]]] = {
    "income_statement": [
        {"profit", "and", "loss"},   # "Profit & Loss", "Profit and Loss"
        {"profit", "loss"},          # "Profit/Loss" (slash instead of &/and)
        {"pnl"},                     # "PnL"
        {"p", "and", "l"},           # "P&L" → normalised "P and L"
        {"income", "statement"},     # "Income Statement"
        {"net", "income"},
        {"net", "profit"},
        {"gross", "profit"},
        {"operating", "profit"},
        {"operating", "income"},
        {"comprehensive", "income"},
        {"ebit"},
        {"ebitda"},
        {"revenue"},                 # some models expose a top-level Revenue section
    ],
    "balance_sheet": [
        {"balance", "sheet"},
        {"financial", "position"},   # "Statement of Financial Position"
        {"total", "assets"},
        {"net", "assets"},
        {"total", "equity"},
        {"assets"},
        {"liabilities"},
        {"equity"},
    ],
    "cash_flow": [
        {"cash", "flow"},
        {"cash", "flows"},
        {"cashflow"},
        {"cash", "activities"},
    ],
    "trial_balance": [
        {"trial", "balance"},
        {"total", "debits"},
        {"total", "credits"},
    ],
}

SCENARIO_TERMS = [
    ("actual", ["actual", "actuals", "act"]),
    ("budget", ["budget", "budgeted", "bud", "bgt", "bdg"]),
    ("forecast", ["forecast", "forecasted", "fcst", "fc", "fcast"]),
    ("plan", ["plan", "planned", "planning"]),
    ("target", ["target", "targets"]),
    ("prior", ["prior year", "prior", "last year", "ly", "previous year"]),
    ("variance", ["variance", "var", "delta"]),
]

SCENARIO_ALIASES = sorted(
    {alias for _canon, aliases in SCENARIO_TERMS for alias in aliases},
    key=len,
    reverse=True,
)
SCENARIO_TERM_TO_CANON = {
    alias: canon for canon, aliases in SCENARIO_TERMS for alias in aliases
}

TIME_PERIOD_PATTERN = re.compile(
    r"""
    \b(?:
        jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|
        jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|
        oct(?:ober)?|nov(?:ember)?|dec(?:ember)?|
        q[1-4]|h[12]|
        f?ytd|[fq]?mtd|qtd|
        full[\s\-]?year|whole[\s\-]?year|all[\s\-]?year|
        year[\s\-]to[\s\-]?date|all[\s\-]months?|full[\s\-]?period
    )\b
    """,
    re.VERBOSE | re.IGNORECASE,
)

CURRENCY_VIEW_PATTERNS: list[tuple[str, str]] = [
    (r"\b(in\s+)?usd\s*(view|amount|value|terms|p\s*&\s*l)?\b", "parent"),
    (r"\bus\s+dollars?\b", "parent"),
    (r"\bparent\s+currency\b", "parent"),
    (r"\bpct\b", "parent"),
    (r"\bconsolidated\s+(view|p\s*&\s*l|amount|usd)?\b", "parent"),
    (r"\bconsol\s+usd\b", "parent"),
    (r"\bgroup\s+(currency|view|reporting|p\s*&\s*l|level)\b", "parent"),
    (r"\btranslated\b", "parent"),
    (r"\blocal\s+currency\b", "local"),
    (r"\blcy\b", "local"),
    (r"\bentity\s+currency\b", "local"),
    (r"\bas\s+reported\b", "local"),
    (r"\bin\s+local\b", "local"),
]
