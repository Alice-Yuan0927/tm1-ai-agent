"""LLM provider abstraction.

Each provider implements the same minimal surface so the rest of the AI code
never branches on provider name.

Tool calling message format (internal canonical):
  {"role": "user", "content": str}
  {"role": "assistant", "content": str}                        — text reply
  {"role": "assistant", "tool_calls": [                        — tool request
      {"id": str, "name": str, "args": dict}]}
  {"role": "tool", "tool_call_id": str, "name": str,          — tool result
   "content": str}
"""

from collections.abc import Iterator
from typing import Protocol


class LLMProvider(Protocol):
    """Synchronous text-in / text-out interface used by the analyzer."""

    def complete(self, prompt: str, *, max_tokens: int, temperature: float) -> str: ...

    def stream(self, prompt: str, *, max_tokens: int, temperature: float) -> Iterator[str]: ...

    def call_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        max_tokens: int,
        temperature: float,
    ) -> tuple[str | None, list[dict]]:
        """One round of tool-calling.

        Returns (text, tool_calls). Exactly one is non-empty:
          - text is set when the model gives a final text answer.
          - tool_calls is a list of {"id", "name", "args"} dicts when the
            model wants to call tools.
        """
        ...
