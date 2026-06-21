"""OpenAI provider using the Responses API."""

import json
import re
from collections.abc import Iterator

from ...config import get_llm_api_key, get_llm_model
from . import usage as _usage

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - optional runtime dep
    OpenAI = None  # type: ignore[assignment]


_client_instance: "OpenAI | None" = None
_client_api_key: str = ""


def openai_client() -> "OpenAI":
    """Lazy singleton OpenAI client, rebuilt when the API key changes."""
    global _client_instance, _client_api_key
    api_key = get_llm_api_key()
    if not api_key:
        raise RuntimeError("LLM_API_KEY environment variable not set")
    if OpenAI is None:
        raise RuntimeError("openai package is not installed")
    if _client_instance is None or _client_api_key != api_key:
        _client_instance = OpenAI(api_key=api_key)
        _client_api_key = api_key
    return _client_instance


def _needs_min_token_budget(model: str) -> bool:
    name = model.strip().lower()
    return bool(re.match(r"o\d", name) or re.match(r"gpt-5(?:[.\-]|$)", name))


def _token_budget(max_tokens: int, model: str) -> int:
    if _needs_min_token_budget(model):
        return max(max_tokens, 4096)
    return max_tokens


def _output_text(response) -> str:
    text = (getattr(response, "output_text", "") or "").strip()
    status = getattr(response, "status", None)
    details = getattr(response, "incomplete_details", None)
    reason = getattr(details, "reason", None)
    if not text and status == "incomplete" and reason == "max_output_tokens":
        raise RuntimeError(
            "OpenAI response used the output token budget before producing visible "
            "text; increase MDX_MAX_TOKENS"
        )
    return text


def _is_unsupported_temperature_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return "unsupported parameter" in message and "temperature" in message


class OpenAIProvider:
    def complete(self, prompt: str, *, max_tokens: int, temperature: float) -> str:
        model = get_llm_model()
        kwargs = {
            "model": model,
            "input": prompt,
            "max_output_tokens": _token_budget(max_tokens, model),
            "temperature": temperature,
        }
        try:
            response = openai_client().responses.create(**kwargs)
        except Exception as exc:
            if not _is_unsupported_temperature_error(exc):
                raise
            kwargs.pop("temperature", None)
            response = openai_client().responses.create(**kwargs)
        _usage.record_openai(response, model=model, label="completion")
        return _output_text(response)

    def stream(self, prompt: str, *, max_tokens: int, temperature: float) -> Iterator[str]:
        model = get_llm_model()
        kwargs = {
            "model": model,
            "input": prompt,
            "max_output_tokens": _token_budget(max_tokens, model),
            "temperature": temperature,
        }
        try:
            yield from self._stream_kwargs(kwargs)
        except Exception as exc:
            if not _is_unsupported_temperature_error(exc):
                raise
            kwargs.pop("temperature", None)
            yield from self._stream_kwargs(kwargs)

    @staticmethod
    def _stream_kwargs(kwargs: dict) -> Iterator[str]:
        model = kwargs.get("model", "")
        with openai_client().responses.stream(**kwargs) as stream:
            for event in stream:
                if event.type == "response.output_text.delta":
                    yield event.delta
                elif event.type == "response.completed":
                    _usage.record_openai(event.response, model=model, label="analysis")

    def call_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        max_tokens: int,
        temperature: float,
    ) -> tuple[str | None, list[dict]]:
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
        model = get_llm_model()
        kwargs: dict = {
            "model": model,
            "messages": oai_messages,
            "tools": oai_tools,
            "tool_choice": "auto",
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        try:
            response = openai_client().chat.completions.create(**kwargs)
        except Exception as exc:
            if not _is_unsupported_temperature_error(exc):
                raise
            kwargs.pop("temperature", None)
            response = openai_client().chat.completions.create(**kwargs)

        _usage.record_openai(response, model=model, label="agent")
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
            return None, tool_calls
        return (choice.message.content or "").strip(), []


def _to_oai_chat_messages(messages: list[dict]) -> list[dict]:
    """Convert canonical internal message format to OpenAI Chat Completions format."""
    out: list[dict] = []
    for m in messages:
        role = m["role"]
        if role == "tool":
            out.append({
                "role": "tool",
                "tool_call_id": m["tool_call_id"],
                "content": m["content"],
            })
        elif role == "assistant" and "tool_calls" in m:
            oai_msg: dict = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": tc["id"],
                        "type": "function",
                        "function": {
                            "name": tc["name"],
                            "arguments": json.dumps(tc["args"]),
                        },
                    }
                    for tc in m["tool_calls"]
                ],
            }
            if "_reasoning_content" in m:
                oai_msg["reasoning_content"] = m["_reasoning_content"]
            out.append(oai_msg)
        else:
            out.append({"role": role, "content": m.get("content", "")})
    return out
