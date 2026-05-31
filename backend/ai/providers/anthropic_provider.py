"""Anthropic provider using the Messages API."""

from collections.abc import Iterator

from ...config import get_llm_api_key, get_llm_model
from . import usage as _usage


try:
    import anthropic as _anthropic_sdk
except ImportError:  # pragma: no cover
    _anthropic_sdk = None  # type: ignore[assignment]


class AnthropicProvider:
    def __init__(self, base_url: str | None = None) -> None:
        self._base_url = base_url

    def _client(self):
        if _anthropic_sdk is None:
            raise RuntimeError("anthropic package is not installed")
        api_key = get_llm_api_key()
        if not api_key:
            raise RuntimeError("LLM_API_KEY environment variable not set")
        kwargs: dict = {"api_key": api_key}
        if self._base_url:
            kwargs["base_url"] = self._base_url
        return _anthropic_sdk.Anthropic(**kwargs)

    def complete(self, prompt: str, *, max_tokens: int, temperature: float) -> str:
        model = get_llm_model()
        response = self._client().messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        )
        _usage.record_anthropic(response, model=model, label="completion")
        return "".join(b.text for b in response.content if b.type == "text").strip()

    def stream(self, prompt: str, *, max_tokens: int, temperature: float) -> Iterator[str]:
        model = get_llm_model()
        with self._client().messages.stream(
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            messages=[{"role": "user", "content": prompt}],
        ) as stream:
            yield from stream.text_stream
            try:
                _usage.record_anthropic(stream.get_final_message(), model=model, label="analysis")
            except Exception:
                pass

    def call_with_tools(
        self,
        messages: list[dict],
        tools: list[dict],
        *,
        max_tokens: int,
        temperature: float,
    ) -> tuple[str | None, list[dict]]:
        anthropic_messages = _to_anthropic_messages(messages)
        anthropic_tools = [
            {
                "name": t["name"],
                "description": t["description"],
                "input_schema": t["parameters"],
            }
            for t in tools
        ]
        model = get_llm_model()
        response = self._client().messages.create(
            model=model,
            messages=anthropic_messages,
            tools=anthropic_tools,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        _usage.record_anthropic(response, model=model, label="agent")
        if response.stop_reason == "tool_use":
            tool_calls = [
                {"id": b.id, "name": b.name, "args": b.input}
                for b in response.content
                if b.type == "tool_use"
            ]
            return None, tool_calls
        text = "".join(b.text for b in response.content if b.type == "text").strip()
        return text, []


def _to_anthropic_messages(messages: list[dict]) -> list[dict]:
    """Convert canonical internal message format to Anthropic Messages API format.

    Anthropic specifics:
    - tool_use blocks live inside assistant content arrays
    - tool_result blocks live inside user content arrays (not a separate role)
    """
    out: list[dict] = []
    for m in messages:
        role = m["role"]
        if role == "tool":
            # Merge consecutive tool results into one user message content array.
            tool_result = {
                "type": "tool_result",
                "tool_use_id": m["tool_call_id"],
                "content": m["content"],
            }
            if out and out[-1]["role"] == "user" and isinstance(out[-1]["content"], list):
                out[-1]["content"].append(tool_result)
            else:
                out.append({"role": "user", "content": [tool_result]})
        elif role == "assistant" and "tool_calls" in m:
            if "_raw_content" in m:
                # Preserve original content blocks (e.g. DeepSeek thinking blocks).
                out.append({"role": "assistant", "content": m["_raw_content"]})
            else:
                out.append({
                    "role": "assistant",
                    "content": [
                        {"type": "tool_use", "id": tc["id"], "name": tc["name"], "input": tc["args"]}
                        for tc in m["tool_calls"]
                    ],
                })
        else:
            out.append({"role": role, "content": m.get("content", "")})
    return out
