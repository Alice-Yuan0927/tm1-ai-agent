"""Orchestrator — runs integrity + variance rules against cube rows.

The detector takes plain ``list[dict]`` rows (the same shape TM1
service returns), applies the RuleSet, scores + suppresses, and returns
the ranked top-N flags. TM1 wiring lives elsewhere.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from . import memory as _memory
from .flag import Flag
from .rules.builtin import BUILTIN_INTEGRITY
from .rules.schema import RuleSet
from .severity import SeverityConfig, score_flag
from .variance import detect_variance

_log = logging.getLogger(__name__)


@dataclass
class DetectionResult:
    cube: str
    flags: list[Flag]
    suppressed: int
    total_raw: int

    def to_dict(self) -> dict:
        return {
            "cube": self.cube,
            "flags": [f.to_dict() for f in self.flags],
            "suppressed": self.suppressed,
            "total_raw": self.total_raw,
        }


def _infer_dims(rows: list[dict], prefer: list[str] | None = None) -> list[str]:
    """Best-effort dim list: prefer the rule's group_by, else non-numeric cols."""
    if prefer:
        sample = rows[0] if rows else {}
        in_rows = [d for d in prefer if d in sample]
        if in_rows:
            return in_rows
    if not rows:
        return []
    sample = rows[0]
    return [k for k, v in sample.items() if not isinstance(v, (int, float)) or isinstance(v, bool)]


def run_detection(
    rules: RuleSet,
    current: list[dict],
    reference: list[dict] | None = None,
    severity_cfg: SeverityConfig | None = None,
    top_n: int | None = 50,
    use_dismissal_memory: bool = True,
) -> DetectionResult:
    flags: list[Flag] = []

    # Layer 1 — integrity.
    for r in rules.integrity:
        if not r.enabled:
            continue
        fn = BUILTIN_INTEGRITY.get(r.name)
        if fn is None:
            _log.warning("[anomaly] unknown integrity rule '%s' — skipping", r.name)
            continue
        dims = _infer_dims(current, prefer=r.group_by)
        try:
            if r.name == "negative_values" and r.columns:
                flags += fn(current, dims, columns=r.columns)  # type: ignore[arg-type]
            elif r.name == "alloc_not_100pct":
                flags += fn(current, dims, group_by=r.group_by)  # type: ignore[arg-type]
            else:
                flags += fn(current, dims)
        except Exception as exc:
            _log.warning("[anomaly] integrity rule '%s' failed: %s", r.name, exc)

    # Layer 2 — variance.
    if reference is not None:
        for vr in rules.enabled_variance_rules():
            try:
                flags += detect_variance(vr, current, reference)
            except Exception as exc:
                _log.warning("[anomaly] variance rule '%s' failed: %s", vr.name, exc)

    total_raw = len(flags)

    for f in flags:
        f.severity = score_flag(f, severity_cfg)

    dismissed = _memory.list_dismissed_ids(rules.cube) if use_dismissal_memory else set()
    seen: set[str] = set()
    kept: list[Flag] = []
    suppressed = 0
    for f in sorted(flags, key=lambda x: x.severity, reverse=True):
        if f.dedupe_id in dismissed:
            suppressed += 1
            continue
        if f.dedupe_id in seen:
            continue
        seen.add(f.dedupe_id)
        kept.append(f)

    if top_n is not None:
        kept = kept[:top_n]

    return DetectionResult(
        cube=rules.cube,
        flags=kept,
        suppressed=suppressed,
        total_raw=total_raw,
    )
