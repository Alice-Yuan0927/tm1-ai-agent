"""DeepSeek provider — OpenAI-compatible Chat Completions endpoint.

Uses DeepSeek's native API (api.deepseek.com) rather than the Anthropic-compatible
endpoint, so thinking blocks are never triggered regardless of model choice.
"""

import json
from collections.abc import Iterator

from ...config import get_llm_api_key, get_llm_model

try:
    from openai import OpenAI as _OpenAI
except ImportError:  # pragma: no cover
    _OpenAI = None  # type: ignore[assignment]

_DEEPSEEK_BASE_URL = "https://api.deepseek.com"


class DeepSeekProvider:
    def _client(self) -> "_OpenAI":
        if _OpenAI is None:
            raise RuntimeError("openai package is not installed")
        api_key = get_llm_api_key()
        if not api_key:
            raise RuntimeError("LLM_API_KEY environment variable not set")
        return _OpenAI(api_key=api_key, base_url=_DEEPSEEK_BASE_URL)

    def complete(self, prompt: str, *, max_tokens: int, temperature: float) -> str:
        response = self._client().chat.completions.create(
            model=get_llm_model(),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=temperature,
        )
        return (response.choices[0].message.content or "").strip()

    def stream(self, prompt: str, *, max_tokens: int, temperature: float) -> Iterator[str]:
        stream = self._client().chat.completions.create(
            model=get_llm_model(),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
            temperature=temperature,
            stream=True,
        )
        for chunk in stream:
            content = chunk.choices[0].delta.content
            if content:
                yield content

    def call_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        max_tokens: int,
        temperature: float,
    ) -> tuple[str | None, list[dict]]:
        from .openai_provider import _to_oai_chat_messages
        oai_messages = _to_oai_chat_messages(messages)
        oai_tools = [
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": t["description"],
                    "parameters": t["parameters"],
                },
            }
            for t in tools
        ]
        response = self._client().chat.completions.create(
            model=get_llm_model(),
            messages=oai_messages,
            tools=oai_tools,
            tool_choice="auto",
            max_tokens=max_tokens,
            temperature=temperature,
        )
        choice = response.choices[0]
        if choice.finish_reason == "tool_calls" and choice.message.tool_calls:
            tool_calls = [
                {
                    "id": tc.id,
                    "name": tc.function.name,
                    "args": json.loads(tc.function.arguments or "{}"),
                }
                for tc in choice.message.tool_calls
            ]
            reasoning = getattr(choice.message, "reasoning_content", None)
            if reasoning and tool_calls:
                tool_calls[0]["_reasoning_content"] = reasoning
            return None, tool_calls
        return (choice.message.content or "").strip(), []
