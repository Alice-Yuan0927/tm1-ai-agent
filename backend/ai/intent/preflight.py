"""Cheap, deterministic preflight checks (no LLM calls)."""

import re


def is_unclear_question(question: str) -> bool:
    """True when a question is too short / sparse to be worth running."""
    text = question.strip()
    if len(text) < 4:
        return True

    words = re.findall(r"[A-Za-z0-9]+", text)
    if len(words) <= 1 and len(text) < 12:
        return True

    meaningful_words = [
        word for word in words
        if len(word) >= 3 or word.isdigit()
    ]
    return not meaningful_words
