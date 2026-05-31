"""LLM commentary on flags — interpret only, never extrapolate.

Two pieces:
  - build_commentary_prompt: shape the messages payload (system + user).
  - validate_commentary:     hallucination guard — every meaningful number
                             in the commentary must appear in the source flags.

Wired into the existing provider abstraction; we do NOT call the SDK directly.
"""

from __future__ import annotations

import json
import re
from collections.abc import Sequence

from .flag import Flag


COMMENTARY_SYSTEM_PROMPT = """\
You are an FP&A analyst writing variance commentary for a planning review.

NON-NEGOTIABLE RULES:
1. Reference ONLY numbers, percentages, and dimension values that appear in
   the <flags> JSON provided. Do not invent figures, totals, ratios, or
   comparisons.
2. Do NOT speculate on root causes (hiring, attrition, FX, market events,
   etc.) unless the explanation is literally present in a flag's
   `reason` or `detail`.
3. Do NOT bring in external context: no benchmarks, industry averages,
   historical events, or forward projections.
4. If a flag is ambiguous, restate it neutrally. Do not fill gaps with
   plausible-sounding narrative.
5. Output a single JSON object. No prose before or after.

Your job is to INTERPRET and PRIORITISE flags, not to add information.
The human reviewer decides on causes and actions.
"""


_OUTPUT_SHAPE_EXAMPLE = {
    "exec_summary": "<1-2 sentences; only numbers present in flags>",
    "flags": [
        {
            "dedupe_id": "<echo from input>",
            "headline": "<<=12 words, business language>",
            "interpretation": "<<=2 sentences; numbers only from this flag>",
            "suggested_action": "review|approve|dismiss|investigate",
        }
    ],
}


def build_commentary_prompt(
    flags: Sequence[Flag],
    cube: str = "",
    period: str = "",
) -> dict:
    """Return {'system': ..., 'user': ...} ready for the provider abstraction."""
    payload = [
        {
            "dedupe_id": f.dedupe_id,
            "layer": f.layer.value,
            "rule": f.rule,
            "severity": f.severity,
            "key": f.key,
            "reason": f.reason,
            "detail": f.detail,
        }
        for f in flags
    ]
    user_msg = (
        f"<context>\n"
        f"  <cube>{cube or 'unspecified'}</cube>\n"
        f"  <period>{period or 'unspecified'}</period>\n"
        f"  <flag_count>{len(payload)}</flag_count>\n"
        f"</context>\n\n"
        f"<flags>\n{json.dumps(payload, indent=2, default=str)}\n</flags>\n\n"
        f"Return a JSON object matching this exact shape "
        f"(no extra keys, no text outside the object):\n\n"
        f"{json.dumps(_OUTPUT_SHAPE_EXAMPLE, indent=2)}"
    )
    return {"system": COMMENTARY_SYSTEM_PROMPT.strip(), "user": user_msg}


# ── Groundedness check ──────────────────────────────────────────────────────

_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")
_TRIVIAL_THRESHOLD = 10.0  # ignore tiny ints likely used as enumerators


def _numbers(text: str) -> set[str]:
    return set(_NUM_RE.findall(text.replace(",", "")))


def validate_commentary(commentary: dict | str, flags: Sequence[Flag]) -> list[str]:
    """Return suspicious numeric tokens (empty list = grounded)."""
    source = " ".join(f"{f.reason} {json.dumps(f.detail, default=str)}" for f in flags)
    src_nums = _numbers(source)

    text = commentary if isinstance(commentary, str) else json.dumps(
        commentary, default=str, ensure_ascii=False)
    used = _numbers(text)

    suspicious: list[str] = []
    for n in used:
        if n in src_nums:
            continue
        try:
            if abs(float(n)) < _TRIVIAL_THRESHOLD:
                continue
        except ValueError:
            continue
        suspicious.append(n)
    return sorted(suspicious, key=lambda x: float(x))
