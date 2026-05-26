"""LLM provider registry.

Adding a new provider is a one-file change here plus an entry in `_PROVIDERS`.
The rest of the AI code calls `complete()` / `stream()` and never branches on
provider name.
"""

from collections.abc import Iterator

from ...config import get_llm_provider
from .anthropic_provider import AnthropicProvider
from .base import LLMProvider
from .deepseek_provider import DeepSeekProvider
from .openai_provider import OpenAIProvider, openai_client

_PROVIDERS: dict[str, LLMProvider] = {
    "openai": OpenAIProvider(),
    "anthropic": AnthropicProvider(),
    "deepseek": DeepSeekProvider(),
}


def _provider() -> LLMProvider:
    name = get_llm_provider()
    if name not in _PROVIDERS:
        # Fall back to OpenAI rather than crash — llm_models.validate_llm_selection
        # already warns the user when their configured provider isn't executable.
        return _PROVIDERS["openai"]
    return _PROVIDERS[name]


def complete_text(prompt: str, *, max_tokens: int, temperature: float) -> str:
    return _provider().complete(prompt, max_tokens=max_tokens, temperature=temperature)


def stream_text(prompt: str, *, max_tokens: int, temperature: float) -> Iterator[str]:
    yield from _provider().stream(prompt, max_tokens=max_tokens, temperature=temperature)


def call_with_tools(
    messages: list[dict],
    tools: list[dict],
    *,
    max_tokens: int,
    temperature: float,
) -> tuple[str | None, list[dict]]:
    """One round of tool-calling. See LLMProvider.call_with_tools for contract."""
    return _provider().call_with_tools(
        messages, tools, max_tokens=max_tokens, temperature=temperature
    )


__all__ = ["LLMProvider", "complete_text", "stream_text", "call_with_tools", "openai_client"]
