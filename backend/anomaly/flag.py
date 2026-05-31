"""Flag dataclass — the unit of output every detector returns."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Layer(str, Enum):
    INTEGRITY = "integrity"   # data is broken; near-zero false positives
    VARIANCE = "variance"     # differs from a reference materially


@dataclass
class Flag:
    layer: Layer
    rule: str
    key: dict[str, str]                  # dimension coordinates, e.g. {"CostCenter": "CC100"}
    reason: str                          # human sentence — required, never auto-generated empty
    severity: float = 0.0                # 0..100, ranking only
    detail: dict = field(default_factory=dict)

    @property
    def key_str(self) -> str:
        return "|".join(f"{k}={v}" for k, v in sorted(self.key.items()))

    @property
    def dedupe_id(self) -> str:
        return f"{self.rule}::{self.key_str}"

    def to_dict(self) -> dict:
        return {
            "layer": self.layer.value,
            "rule": self.rule,
            "key": self.key,
            "reason": self.reason,
            "severity": self.severity,
            "detail": self.detail,
            "dedupe_id": self.dedupe_id,
        }
