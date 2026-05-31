"""Severity scoring — used only for ranking, not for filtering.

Integrity flags are inherently high-confidence (data is broken). Variance
flags scale by how far past BOTH gates they sit; the geometric mean
ensures both axes must be material for a high score.
"""

from __future__ import annotations

from dataclasses import dataclass

from .flag import Flag, Layer


@dataclass
class SeverityConfig:
    rel_cap: float = 0.50         # 50%+ deviation = full marks on the rel axis
    abs_cap: float = 100_000.0    # $100k+ movement = full marks on the abs axis
    variance_confidence: float = 0.8  # variance < integrity in confidence
    integrity_score: float = 90.0     # broken data sits near the top by default


def score_flag(flag: Flag, cfg: SeverityConfig | None = None) -> float:
    cfg = cfg or SeverityConfig()
    if flag.layer is Layer.INTEGRITY:
        return cfg.integrity_score

    d = flag.detail or {}
    rel_axis = min(abs(d.get("rel_delta", 0)) / cfg.rel_cap, 1.0)
    abs_axis = min(abs(d.get("abs_delta", 0)) / cfg.abs_cap, 1.0)
    magnitude = (rel_axis * abs_axis) ** 0.5
    return round(magnitude * cfg.variance_confidence * 100, 1)
