"""Finance ontology matcher for TM1 schema summaries.

The ontology is model-independent. The matcher maps generic finance concepts
to the current TM1 schema using cube names, dimension names, attributes, and
sample elements from the schema cache.
"""

import re
from dataclasses import dataclass


FINANCE_ONTOLOGY = {
    "income_statement": {
        "aliases": ["p&l", "p and l", "profit and loss", "income statement", "is"],
        "cube_hints": ["p&l", "profit and loss", "income statement", "profitability"],
        "line_item_dimension_hints": [
            "account",
            "gl account",
            "chart of accounts",
            "line item",
            "reporting line",
            "p&l account",
        ],
        "line_item_element_hints": [
            "revenue",
            "sales",
            "cogs",
            "cost of goods sold",
            "gross profit",
            "operating expense",
            "operating profit",
            "ebitda",
            "net income",
        ],
        "measure_hints": ["amount", "value", "activity"],
        "time_behavior": "activity",
    },
    "balance_sheet": {
        "aliases": ["balance sheet", "statement of financial position", "bs"],
        "cube_hints": ["balance sheet", "financial position"],
        "line_item_dimension_hints": [
            "account",
            "gl account",
            "chart of accounts",
            "balance sheet account",
            "line item",
        ],
        "line_item_element_hints": [
            "assets",
            "liabilities",
            "equity",
            "cash",
            "accounts receivable",
            "inventory",
            "retained earnings",
        ],
        "measure_hints": ["ending balance", "balance", "closing balance"],
        "time_behavior": "balance",
    },
    "trial_balance": {
        "aliases": ["trial balance", "tb"],
        "cube_hints": ["trial balance", "general ledger", "ledger"],
        "line_item_dimension_hints": ["account", "gl account", "chart of accounts"],
        "line_item_element_hints": ["assets", "liabilities", "revenue", "expense"],
        "measure_hints": ["beginning balance", "debit", "credit", "activity", "ending balance"],
        "time_behavior": "activity_and_balance",
    },
    "cash_flow": {
        "aliases": ["cash flow", "cashflow", "statement of cash flows"],
        "cube_hints": ["cash flow", "cashflow"],
        "line_item_dimension_hints": ["account", "cash flow account", "line item"],
        "line_item_element_hints": [
            "operating activities",
            "investing activities",
            "financing activities",
            "net income",
            "depreciation",
            "capital expenditure",
        ],
        "measure_hints": ["amount", "value", "cash flow"],
        "time_behavior": "cash_movement",
    },
}


@dataclass
class _ScoredMatch:
    name: str
    score: float
    evidence: list[str]


def build_finance_semantic_profile(schema_summary: dict) -> dict:
    """Map generic finance concepts to the current TM1 schema."""
    cubes = list(schema_summary.get("cubes") or [])
    concepts = {
        concept: _match_concept(concept, spec, cubes)
        for concept, spec in FINANCE_ONTOLOGY.items()
    }
    return {
        "ontology_version": 1,
        "source": "deterministic_schema_matcher",
        "concepts": concepts,
    }


def _match_concept(concept: str, spec: dict, cubes: list[dict]) -> dict:
    cube_matches = [_score_cube(cube, spec) for cube in cubes]
    cube_matches = [match for match in cube_matches if match.score > 0]
    cube_matches.sort(key=lambda match: match.score, reverse=True)

    primary_cube = cube_matches[0].name if cube_matches else ""
    primary_cube_info = next((cube for cube in cubes if cube.get("cube") == primary_cube), {})
    line_dim = _best_line_item_dimension(primary_cube_info, spec)
    measures = _preferred_measures(primary_cube_info, spec)
    confidence = min(0.99, round((cube_matches[0].score / 10.0) if cube_matches else 0.0, 2))
    if line_dim:
        confidence = min(0.99, round(confidence + 0.15, 2))

    return {
        "aliases": spec.get("aliases", []),
        "primary_cube": primary_cube,
        "matched_cubes": [
            {
                "cube": match.name,
                "confidence": min(0.99, round(match.score / 10.0, 2)),
                "evidence": match.evidence[:6],
            }
            for match in cube_matches[:3]
        ],
        "line_item_dimension": line_dim,
        "preferred_measures": measures,
        "time_behavior": spec.get("time_behavior", ""),
        "confidence": confidence,
    }


def _score_cube(cube: dict, spec: dict) -> _ScoredMatch:
    cube_name = str(cube.get("cube", ""))
    description = str(cube.get("description", ""))
    dimensions = [str(dim) for dim in cube.get("dimensions", [])]
    attributes = [str(attr) for attr in cube.get("attributes", [])]
    dimension_elements = {
        str(dim): [str(element) for element in elements]
        for dim, elements in dict(cube.get("dimension_elements") or {}).items()
    }
    measures = [str(measure) for measure in cube.get("measures", [])]

    score = 0.0
    evidence: list[str] = []
    score += _score_terms(cube_name, spec.get("cube_hints", []), 4.0, evidence, "cube")
    score += _score_terms(description, spec.get("cube_hints", []), 2.0, evidence, "description")
    score += _score_terms(" ".join(dimensions), spec.get("line_item_dimension_hints", []), 2.0, evidence, "dimension")
    score += _score_terms(" ".join(attributes), ["statement", "section", "account type"], 1.0, evidence, "attribute")
    score += _score_terms(" ".join(measures), spec.get("measure_hints", []), 1.0, evidence, "measure")

    element_text = " ".join(
        element
        for elements in dimension_elements.values()
        for element in elements
    )
    score += _score_terms(element_text, spec.get("line_item_element_hints", []), 1.5, evidence, "element")

    return _ScoredMatch(cube_name, score, evidence)


def _best_line_item_dimension(cube: dict, spec: dict) -> str:
    candidates: list[_ScoredMatch] = []
    dimensions = [str(dim) for dim in cube.get("dimensions", [])]
    dimension_elements = {
        str(dim): [str(element) for element in elements]
        for dim, elements in dict(cube.get("dimension_elements") or {}).items()
    }
    for dim in dimensions:
        evidence: list[str] = []
        score = _score_terms(dim, spec.get("line_item_dimension_hints", []), 4.0, evidence, "dimension")
        score += _score_terms(" ".join(dimension_elements.get(dim, [])), spec.get("line_item_element_hints", []), 2.0, evidence, "element")
        if score > 0:
            candidates.append(_ScoredMatch(dim, score, evidence))
    if not candidates:
        return ""
    candidates.sort(key=lambda item: item.score, reverse=True)
    return candidates[0].name


def _preferred_measures(cube: dict, spec: dict) -> list[str]:
    measures = [str(measure) for measure in cube.get("measures", [])]
    scored: list[_ScoredMatch] = []
    for measure in measures:
        evidence: list[str] = []
        score = _score_terms(measure, spec.get("measure_hints", []), 1.0, evidence, "measure")
        if score > 0:
            scored.append(_ScoredMatch(measure, score, evidence))
    scored.sort(key=lambda item: item.score, reverse=True)
    return [item.name for item in scored[:5]]


def _score_terms(
    text: str,
    terms: list[str],
    weight: float,
    evidence: list[str],
    label: str,
) -> float:
    normalised = _normalise(text)
    score = 0.0
    for term in terms:
        norm_term = _normalise(term)
        if not norm_term:
            continue
        if re.search(rf"(?<![a-z0-9]){re.escape(norm_term)}(?![a-z0-9])", normalised):
            score += weight
            evidence.append(f"{label}:{term}")
    return score


def _normalise(value: str) -> str:
    text = str(value).lower().replace("&", " and ")
    return " ".join(re.findall(r"[a-z0-9]+", text))
