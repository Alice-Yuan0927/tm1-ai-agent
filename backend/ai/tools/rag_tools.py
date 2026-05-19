"""RAG retrieval tools for pipeline orchestration.

Wraps retrieve_similar and save_query so analyze_pipeline never imports
from ai.retrieval.rag directly.
"""

from __future__ import annotations

from ..retrieval.rag import retrieve_similar as _retrieve_similar
from ..retrieval.rag import save_query as _save_query


def get_similar_queries(question: str, cube: str) -> list[dict]:
    """Fetch few-shot similar queries for the given cube from the RAG store."""
    return _retrieve_similar(question, cube=cube)


def record_query(
    question: str,
    cube: str,
    mdx: str,
    row_count: int,
    grounded_members: list | None = None,
) -> None:
    """Persist a successful query to the RAG store for future few-shot retrieval."""
    _save_query(
        question, cube, mdx,
        row_count=row_count,
        grounded_members=grounded_members or [],
    )
