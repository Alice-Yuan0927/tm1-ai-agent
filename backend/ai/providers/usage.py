"""Per-request LLM token-usage tracking.

Each provider calls one of the ``record_*`` helpers after every LLM
round-trip. The SSE pipeline opens a :func:`collect` context for the duration
of a request and calls :func:`drain` between events to stream the pending
usage records to the UI as ``token_usage`` events.

The sink is thread-local: ``analyze_sse_gen`` runs the agent loop and the
narrative stream inline on a single worker thread, so every LLM call made
while serving one request lands in that request's sink and nowhere else.
"""

from __future__ import annotations

import threading
from contextlib import contextmanager

_local = threading.local()


def _sink() -> list | None:
    return getattr(_local, "sink", None)


@contextmanager
def collect():
    """Activate usage collection for the current thread.

    Yields the pending-records list; safe to nest (inner contexts get their
    own sink and restore the outer one on exit)."""
    previous = _sink()
    sink: list[dict] = []
    _local.sink = sink
    try:
        yield sink
    finally:
        _local.sink = previous


def drain() -> list[dict]:
    """Return and clear the pending usage records (empty list if inactive)."""
    sink = _sink()
    if not sink:
        return []
    out = sink[:]
    sink.clear()
    return out


def record(*, input_tokens: int, output_tokens: int, model: str = "", label: str = "") -> None:
    """Append one usage record. No-op when collection is not active."""
    sink = _sink()
    if sink is None:
        return
    inp = int(input_tokens or 0)
    out = int(output_tokens or 0)
    sink.append({
        "input_tokens": inp,
        "output_tokens": out,
        "total_tokens": inp + out,
        "model": model,
        "label": label,
    })


def _get(obj, *names):
    for name in names:
        value = getattr(obj, name, None)
        if value is not None:
            return value
    return 0


def record_anthropic(response, *, model: str = "", label: str = "") -> None:
    """Record usage from an Anthropic Messages API response/message."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    record(
        input_tokens=_get(usage, "input_tokens"),
        output_tokens=_get(usage, "output_tokens"),
        model=model,
        label=label,
    )


def record_openai(response, *, model: str = "", label: str = "") -> None:
    """Record usage from an OpenAI response (Chat Completions or Responses API).

    Chat Completions exposes ``prompt_tokens`` / ``completion_tokens`` while the
    Responses API uses ``input_tokens`` / ``output_tokens`` — accept either."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    record(
        input_tokens=_get(usage, "input_tokens", "prompt_tokens"),
        output_tokens=_get(usage, "output_tokens", "completion_tokens"),
        model=model,
        label=label,
    )
