"""Python-callable element search API for pipeline orchestration.

Distinct from element_tools.py (which returns JSON strings for LLM tool-use).
These functions return typed Python objects and are imported by analyze_pipeline.

Public API:
  find_element_candidates(question, cube_dims)  -> list[tuple[str,str,str]]
  resolve_members(question, cube_dims)          -> list[dict]
"""

from __future__ import annotations

import logging

from ..retrieval.embeddings import search_by_embedding
from ...tm1.cache import find_question_element_matches, resolve_question_members

_log = logging.getLogger(__name__)


def find_element_candidates(
    question: str,
    cube_dims: list[str] | None = None,
) -> list[tuple[str, str, str]]:
    """Return element candidates for MDX planner grounding.

    Runs FTS first (fast, exact/near-exact); falls back to embedding search
    when FTS returns nothing.  Candidate dims scope the embedding search.

    Returns list of (matched_text, dim_name, element_name) tuples — same
    shape as find_question_element_matches().
    """
    matches = find_question_element_matches(question)
    if matches:
        return matches

    embedding_hits = search_by_embedding(question, candidate_dims=cube_dims)
    if embedding_hits:
        _log.info(
            "[element-search] embedding fallback: %d candidates for %r",
            len(embedding_hits), question[:60],
        )
        # Normalise to the same (matched_text, dim_name, element_name) shape.
        result: list[tuple[str, str, str]] = []
        for hit in embedding_hits:
            elem = str(hit.get("element", "")).strip()
            dim = str(hit.get("dimension", "")).strip()
            if elem and dim:
                result.append((elem, dim, elem))
        return result

    return []


def resolve_members(
    question: str,
    cube_dims: list[str] | None = None,
) -> list[dict]:
    """Resolve grounded members from the question for MDX context injection.

    Thin wrapper that keeps the pipeline isolated from the cache module path.
    Returns list[dict] with keys: dimension, element, unique_name, ...
    """
    return resolve_question_members(question, candidate_dims=cube_dims)
