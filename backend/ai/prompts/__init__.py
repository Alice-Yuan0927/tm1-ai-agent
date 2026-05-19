"""Externalised LLM prompt fragments — versioned text, not Python strings."""

from pathlib import Path

_PROMPTS_DIR = Path(__file__).parent


def _load(name: str) -> str:
    return (_PROMPTS_DIR / name).read_text(encoding="utf-8").rstrip()


MDX_HARD_RULES = _load("mdx_hard_rules.md")

from . import prompt_rules  # noqa: E402  re-export module so `from ai.prompts import prompt_rules` works
