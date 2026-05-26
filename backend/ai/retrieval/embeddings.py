"""Embedding-based element search for TM1 schema members.

Responsibilities (intentionally narrow):
  - embed_texts()              — call OpenAI, return L2-normalised float32 array
  - ensure_element_embeddings() — pre-compute + store at sync time (idempotent)
  - search_by_embedding()      — query-time fuzzy match, same return format as
                                  find_question_element_matches()

What this module does NOT do:
  - role gating (handled by _matches_by_dim / accepts_entity_name)
  - dim classification (handled by get_dim_role)
  - MDX generation
"""

from __future__ import annotations

import logging
import re
import sqlite3

import numpy as np

from ...config import DATA_DIR, EMBEDDING_BATCH_SIZE, get_llm_api_key, get_llm_provider
from ...tm1.cache.db import cache_scope_matches

_log = logging.getLogger(__name__)

_EMBED_MODEL = "text-embedding-3-small"
_DB_PATH = DATA_DIR / "schema_cache.db"

# Attribute names that carry human-readable descriptions (checked in order).
_DESCRIPTION_ATTR_NAMES = {"description", "label", "name", "longname", "long name"}


# ── Embedding call ─────────────────────────────────────────────────────────────

def _embedding_client():
    """Lazy OpenAI client using an embedding-capable API key."""
    import os
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("openai package is not installed") from exc

    api_key = (
        os.environ.get("LLM_EMBEDDING_API_KEY", "").strip()
        or os.environ.get("LLM_embedding_api_key", "").strip()
        or os.environ.get("OPENAI_API_KEY", "").strip()
    )
    if not api_key and get_llm_provider() == "openai":
        api_key = get_llm_api_key()
    if not api_key:
        raise RuntimeError(
            "Embedding API key is not set. Set LLM_EMBEDDING_API_KEY "
            "(or legacy LLM_embedding_api_key)."
        )
    return OpenAI(api_key=api_key)


def _embed_texts(texts: list[str]) -> np.ndarray:
    """Call OpenAI embedding API in batches using the dedicated embedding key.

    Returns float32 array shape (len(texts), embedding_dims), L2-normalised so
    cosine similarity == dot product.
    """
    client = _embedding_client()
    all_vecs: list[np.ndarray] = []

    for i in range(0, len(texts), EMBEDDING_BATCH_SIZE):
        batch = texts[i : i + EMBEDDING_BATCH_SIZE]
        resp = client.embeddings.create(model=_EMBED_MODEL, input=batch)
        vecs = np.array([d.embedding for d in resp.data], dtype=np.float32)
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1.0, norms)
        all_vecs.append(vecs / norms)

    return np.vstack(all_vecs) if all_vecs else np.empty((0, 0), dtype=np.float32)


# ── Text to embed per element ──────────────────────────────────────────────────

def _build_embed_text(
    element_name: str,
    alias: str | None,
    description: str | None,
) -> str:
    """Combine element name, alias, and description into one rich search string.

    Richer context lets the model understand "HK01 | Hong Kong Office" and match
    it to "hong kong" even though the element name alone is opaque.
    """
    parts = [element_name.strip()]
    if alias and alias.strip() and alias.strip().lower() != element_name.strip().lower():
        parts.append(alias.strip())
    if description and description.strip():
        parts.append(description.strip())
    return " | ".join(parts)


def _extract_description(attr_blob: str | None) -> str | None:
    """Pull the first description-like value out of the GROUP_CONCAT attr blob."""
    if not attr_blob:
        return None
    for pair in attr_blob.split(chr(31)):
        if "|" not in pair:
            continue
        attr_name, _, attr_val = pair.partition("|")
        if attr_name.strip().lower() in _DESCRIPTION_ATTR_NAMES and attr_val.strip():
            return attr_val.strip()
    return None


# ── Pre-computation (sync time) ────────────────────────────────────────────────

def ensure_element_embeddings() -> int:
    """Pre-compute embeddings for all elements that don't have one yet.

    Idempotent — skips rows that already have an embedding for the current model.
    Runs at schema-sync time (background thread) so it never blocks a user query.
    Returns the count of newly embedded elements.
    """
    with sqlite3.connect(_DB_PATH) as conn:
        if not cache_scope_matches(conn):
            return 0
        rows = conn.execute(
            "SELECT e.dim_name, e.element_name,"
            "       ea.alias_value,"
            "       GROUP_CONCAT(eav.attr_name || '|' || eav.attr_value, char(31))"
            " FROM elements e"
            " LEFT JOIN element_aliases ea"
            "   ON ea.dim_name = e.dim_name AND ea.element_name = e.element_name"
            " LEFT JOIN element_attribute_values eav"
            "   ON eav.dim_name = e.dim_name AND eav.element_name = e.element_name"
            "   AND LOWER(eav.attr_name) IN"
            "       ('description','label','name','longname','long name')"
            " LEFT JOIN element_embeddings ee"
            "   ON ee.dim_name = e.dim_name AND ee.element_name = e.element_name"
            "   AND ee.model = ?"
            " WHERE ee.embedding IS NULL"
            " GROUP BY e.dim_name, e.element_name, ea.alias_value",
            [_EMBED_MODEL],
        ).fetchall()

    if not rows:
        _log.info("[embeddings] all elements already embedded — nothing to do")
        return 0

    texts: list[str] = []
    records: list[tuple[str, str, str]] = []
    for dim_name, elem_name, alias, attr_blob in rows:
        description = _extract_description(attr_blob)
        text = _build_embed_text(elem_name, alias, description)
        texts.append(text)
        records.append((dim_name, elem_name, text))

    _log.info("[embeddings] embedding %d elements …", len(records))
    try:
        vecs = _embed_texts(texts)
    except Exception as exc:
        _log.error("[embeddings] API call failed: %s", exc)
        return 0

    with sqlite3.connect(_DB_PATH) as conn:
        conn.executemany(
            "INSERT OR REPLACE INTO element_embeddings"
            " (dim_name, element_name, embed_text, embedding, model)"
            " VALUES (?, ?, ?, ?, ?)",
            [
                (dim, elem, text, vecs[i].astype(np.float32).tobytes(), _EMBED_MODEL)
                for i, (dim, elem, text) in enumerate(records)
            ],
        )

    _log.info("[embeddings] stored %d embeddings", len(records))
    return len(records)


# ── Query-time search ──────────────────────────────────────────────────────────

_TOKEN_SPLIT_RE = re.compile(r"[^a-z0-9]+")


def _candidate_phrases(question: str) -> list[str]:
    """Extract n-gram (1–4 word) candidates from the normalised question.

    These are the phrases we embed and compare against stored element embeddings.
    We keep phrases with >= 3 chars; single generic tokens are fine because the
    similarity threshold + downstream role gating handle false positives.
    """
    norm = _TOKEN_SPLIT_RE.sub(" ", question.lower()).strip()
    tokens = norm.split()
    seen: set[str] = set()
    phrases: list[str] = []
    for i in range(len(tokens)):
        for n in range(1, 5):
            if i + n > len(tokens):
                break
            phrase = " ".join(tokens[i : i + n])
            if len(phrase) < 3 or phrase in seen:
                continue
            seen.add(phrase)
            phrases.append(phrase)
    return phrases


def search_by_embedding(
    question: str,
    candidate_dims: list[str] | None = None,
    *,
    top_k: int = 3,
    threshold: float = 0.72,
) -> list[tuple[str, str, str]]:
    """Return (matched_phrase, dim_name, element_name) tuples for question phrases
    that are semantically close to stored element embeddings.

    Return format is identical to find_question_element_matches() so callers can
    merge both lists transparently.

    Role gating (only entity_subject dims receive entity mentions) happens
    DOWNSTREAM in _matches_by_dim() — this function returns raw candidates.
    """
    phrases = _candidate_phrases(question)
    if not phrases:
        return []

    # Load stored embeddings for the relevant dims.
    with sqlite3.connect(_DB_PATH) as conn:
        if not cache_scope_matches(conn):
            return []
        if candidate_dims:
            placeholders = ",".join("?" * len(candidate_dims))
            elem_rows = conn.execute(
                f"SELECT ee.dim_name, ee.element_name, ee.embedding"
                f" FROM element_embeddings ee"
                f" JOIN elements e"
                f"   ON e.dim_name = ee.dim_name AND e.element_name = ee.element_name"
                f" WHERE ee.model = ? AND ee.embedding IS NOT NULL"
                f" AND ee.dim_name IN ({placeholders})",
                [_EMBED_MODEL, *candidate_dims],
            ).fetchall()
        else:
            elem_rows = conn.execute(
                "SELECT ee.dim_name, ee.element_name, ee.embedding"
                " FROM element_embeddings ee"
                " JOIN elements e"
                "   ON e.dim_name = ee.dim_name AND e.element_name = ee.element_name"
                " WHERE ee.model = ? AND ee.embedding IS NOT NULL",
                [_EMBED_MODEL],
            ).fetchall()

    if not elem_rows:
        return []

    # Embed all candidate phrases in one batched API call.
    try:
        phrase_vecs = _embed_texts(phrases)  # (n_phrases, dims)
    except Exception as exc:
        _log.warning("[embeddings] query embedding failed: %s", exc)
        return []

    dim_names = [r[0] for r in elem_rows]
    elem_names = [r[1] for r in elem_rows]
    elem_vecs = np.stack([
        np.frombuffer(r[2], dtype=np.float32) for r in elem_rows
    ])  # (n_elems, dims)

    # Cosine similarity matrix (both sides L2-normalised → dot product suffices).
    scores = phrase_vecs @ elem_vecs.T  # (n_phrases, n_elems)

    # Collect best-phrase match per element, then rank and deduplicate.
    hits: list[tuple[float, str, str, str]] = []
    seen_keys: set[tuple[str, str]] = set()
    per_dim: dict[str, int] = {}

    best_phrase_per_elem = scores.argmax(axis=0)   # (n_elems,)
    best_score_per_elem  = scores.max(axis=0)       # (n_elems,)

    order = best_score_per_elem.argsort()[::-1]    # highest score first
    for ei in order:
        score = float(best_score_per_elem[ei])
        if score < threshold:
            break
        dim  = dim_names[ei]
        elem = elem_names[ei]
        key  = (dim, elem)
        if key in seen_keys or per_dim.get(dim, 0) >= top_k:
            continue
        seen_keys.add(key)
        per_dim[dim] = per_dim.get(dim, 0) + 1
        hits.append((score, phrases[int(best_phrase_per_elem[ei])], dim, elem))

    return [(phrase, dim, elem) for _, phrase, dim, elem in hits]
