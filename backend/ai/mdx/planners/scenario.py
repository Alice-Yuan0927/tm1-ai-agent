"""Scenario-axis resolution for MDX planners."""

from __future__ import annotations

import re

from ...schema.tm1_lexicon import SCENARIO_ALIASES, SCENARIO_TERM_TO_CANON, SCENARIO_TERMS

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

_SCENARIO_ABBREVS = {
    "actual": ["act", "actl", "actuals", "ac"],
    "budget": ["bud", "bgt", "bdg", "budg"],
    "forecast": ["fcst", "fc", "fcs", "for"],
    "plan": ["pln", "planned"],
    "target": ["tgt", "tg"],
}


def _resolve_scenario_axis(
    question: str,
    dimensions: list[dict],
) -> tuple[dict | None, list[str], str]:
    """Inspect the question for scenario references and resolve to elements.

    Returns (scenario_dim, [elements], status):
      - "none":       no scenario terms in question
      - "pair":       two scenario elements on COLS
      - "single":     one scenario element in WHERE
      - "unresolved": scenario terms found but no element match — caller defers to LLM
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
        variance_elem = _find_variance_element(consolidations + elements)
        if variance_elem:
            return scenario_dim, [variance_elem], "single"
        return scenario_dim, [], "unresolved"

    singles = list(_SCENARIO_SINGLE_RE.finditer(question))
    if singles:
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
    for dim in dimensions:
        if dim.get("is_measure"):
            continue
        name = str(dim.get("name", "")).lower()
        if "scenario" in name or "version" in name:
            return dim
    for dim in dimensions:
        if dim.get("is_measure"):
            continue
        lower_elements = [str(e).lower() for e in dim.get("elements", [])]
        has_actual = any("actual" in e for e in lower_elements)
        has_budget = any("budget" in e or "plan" in e or "forecast" in e for e in lower_elements)
        if has_actual and has_budget:
            return dim
    return None


def _find_scenario_element(term: str, elements: list[str]) -> str:
    """Return element whose name best matches `term`, including TM1 abbreviations."""
    candidates = [term.lower()] + _SCENARIO_ABBREVS.get(term.lower(), [])
    for needle in candidates:
        for e in elements:
            if e.lower() == needle:
                return e
        for e in elements:
            if re.search(rf"\b{re.escape(needle)}\b", e.lower()):
                return e
        for e in elements:
            if needle in e.lower():
                return e
    return ""


def _find_variance_element(candidates: list[str]) -> str:
    for hint in _VARIANCE_HINTS:
        for e in candidates:
            if hint in e.lower():
                return e
    return ""
