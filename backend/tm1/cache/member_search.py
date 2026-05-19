"""Question-to-member matching via FTS + normalised token comparison."""

import re

from ...config import GROUNDED_MEMBER_LIMIT, GROUNDED_MEMBER_PER_DIM
from .db import connect, qmarks

_ELEMENT_NORM_RE = re.compile(r"[^a-z0-9]+")
_IDENTIFIER_PUNCT_RE = re.compile(r"[-_/\.]")


def _normalise_element_token(value: str) -> str:
    """Lower-case and collapse non-alphanumeric runs to a single space."""
    return _ELEMENT_NORM_RE.sub(" ", (value or "").lower()).strip()


def _is_strong_element_token(token: str) -> bool:
    """Domain-identifier-looking — rules out generic English words."""
    cleaned = token.strip()
    if not cleaned:
        return False
    if _IDENTIFIER_PUNCT_RE.search(cleaned):
        return True
    if len(cleaned.split()) >= 2:
        return True
    if any(ch.isdigit() for ch in cleaned):
        return True
    return False


def find_question_element_matches(
    question: str,
    *,
    max_phrase_words: int = 4,
    min_token_len: int = 3,
) -> list[tuple[str, str, str]]:
    """Find element names embedded in the user question after normalisation.

    Returns (matched_element, dim_name, element_name) tuples for domain-looking
    identifiers only — generic English words that happen to match an element
    name are filtered out.
    """
    text = (question or "").strip()
    if not text:
        return []
    norm_question = " " + _normalise_element_token(text) + " "
    q_tokens = norm_question.split()

    candidate_norms: list[str] = []
    seen_norms: set[str] = set()
    for i in range(len(q_tokens)):
        for n in range(1, max_phrase_words + 1):
            if i + n > len(q_tokens):
                break
            phrase = " ".join(q_tokens[i : i + n])
            if phrase not in seen_norms and len(phrase.replace(" ", "")) >= min_token_len:
                seen_norms.add(phrase)
                candidate_norms.append(phrase)

    if not candidate_norms:
        return []

    # SQLite-side approximation of _normalise_element_token. The Python check
    # below catches anything this SQL replace chain misses.
    norm_sql = (
        "TRIM(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(REPLACE("
        "LOWER(element_name),"
        "'-',' '),'_',' '),'/',' '),'.',' '),'(',' '),')',' '))"
    )
    rows = connect().execute(
        f"SELECT dim_name, element_name FROM elements"
        f" WHERE LENGTH(element_name) >= 2"
        f"   AND {norm_sql} IN ({qmarks(len(candidate_norms))})",
        candidate_norms,
    ).fetchall()

    results: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for dim_name, element_name in rows:
        token = str(element_name).strip()
        if not token or len(token.split()) > max_phrase_words:
            continue
        if not _is_strong_element_token(token):
            continue
        needle = _normalise_element_token(token)
        if len(needle.replace(" ", "")) < min_token_len:
            continue
        if f" {needle} " not in norm_question:
            continue
        key = (dim_name, element_name)
        if key not in seen:
            seen.add(key)
            results.append((token, dim_name, element_name))
    return results


def resolve_question_members(
    question: str,
    candidate_dims: list[str] | None = None,
    *,
    max_per_dim: int = GROUNDED_MEMBER_PER_DIM,
    max_total: int = GROUNDED_MEMBER_LIMIT,
) -> list[dict[str, str | int]]:
    """Question-scoped member candidates from the schema cache.

    Grounding layer for MDX generation: the LLM should reference these instead
    of inventing element names. Lexical and bounded by design.
    """
    text = (question or "").strip()
    if not text:
        return []
    question_norm = _normalise_element_token(text)
    if not question_norm:
        return []
    question_padded = f" {question_norm} "

    dim_filter = ""
    params: list[object] = []
    if candidate_dims:
        dim_filter = f" AND ms.dim_name IN ({qmarks(len(candidate_dims))})"
        params.extend(candidate_dims)

    fts_query = _member_fts_query(text)
    if not fts_query:
        return []

    rows = connect().execute(
        "SELECT e.dim_name, e.element_name, e.element_type,"
        "       ea.alias_value,"
        "       GROUP_CONCAT(eav.attr_name || '=' || eav.attr_value, char(31))"
        " FROM member_search ms"
        " JOIN elements e"
        "   ON e.dim_name = ms.dim_name AND e.element_name = ms.element_name"
        " LEFT JOIN element_aliases ea"
        "   ON ea.dim_name = e.dim_name AND ea.element_name = e.element_name"
        " LEFT JOIN element_attribute_values eav"
        "   ON eav.dim_name = e.dim_name AND eav.element_name = e.element_name"
        f" WHERE member_search MATCH ?{dim_filter}"
        " GROUP BY e.dim_name, e.element_name, e.element_type, ea.alias_value",
        [fts_query, *params],
    ).fetchall()

    ranked: list[tuple[int, int, dict[str, str | int]]] = []
    for dim_name, element_name, element_type, alias_value, attr_blob in rows:
        element = str(element_name or "").strip()
        if not element:
            continue
        matches = _member_match_signals(
            question_padded,
            element,
            str(alias_value or ""),
            str(attr_blob or ""),
        )
        if not matches:
            continue
        best_score, matched_text, matched_by = matches
        specificity = len(_normalise_element_token(matched_text).replace(" ", ""))
        ranked.append((
            best_score,
            specificity,
            {
                "dimension": str(dim_name),
                "element": element,
                "unique_name": f"[{dim_name}].[{dim_name}].[{element}]",
                "element_type": str(element_type or "Numeric"),
                "matched_text": matched_text,
                "matched_by": matched_by,
                "score": best_score,
            },
        ))

    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    results: list[dict[str, str | int]] = []
    per_dim: dict[str, int] = {}
    seen: set[tuple[str, str]] = set()
    for _score, _specificity, candidate in ranked:
        key = (str(candidate["dimension"]), str(candidate["element"]))
        if key in seen:
            continue
        dim_count = per_dim.get(key[0], 0)
        if dim_count >= max_per_dim:
            continue
        seen.add(key)
        per_dim[key[0]] = dim_count + 1
        results.append(candidate)
        if len(results) >= max_total:
            break
    return results


def _member_match_signals(
    question_padded: str,
    element: str,
    alias_value: str,
    attr_blob: str,
) -> tuple[int, str, str] | None:
    candidates: list[tuple[int, str, str]] = []
    for score, text, source in (
        (100, element, "element"),
        (90, alias_value, "alias"),
    ):
        matched = _match_normalised_phrase(question_padded, text)
        if matched:
            candidates.append((score, matched, source))

    if attr_blob:
        for item in attr_blob.split(chr(31)):
            if "=" not in item:
                continue
            attr_name, attr_value = item.split("=", 1)
            matched = _match_normalised_phrase(question_padded, attr_value)
            if matched:
                candidates.append((80, matched, f"attribute:{attr_name}"))

    if not candidates:
        return None
    return max(candidates, key=lambda item: (item[0], len(item[1])))


def _match_normalised_phrase(question_padded: str, value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    norm = _normalise_element_token(raw)
    compact_len = len(norm.replace(" ", ""))
    if compact_len < 2:
        return ""
    if compact_len < 4 and not any(ch.isdigit() for ch in norm):
        return ""
    if f" {norm} " in question_padded:
        return raw
    return ""


def _member_fts_query(text: str) -> str:
    words = re.findall(r"[a-zA-Z0-9一-鿿]+", text or "")
    useful = []
    for word in words:
        compact = re.sub(r"[^a-zA-Z0-9一-鿿]+", "", word)
        if len(compact) < 2:
            continue
        if len(compact) < 4 and not any(ch.isdigit() for ch in compact):
            continue
        useful.append(compact)
    if not useful:
        return ""
    return " OR ".join(f'"{word}"' for word in useful[:12])
