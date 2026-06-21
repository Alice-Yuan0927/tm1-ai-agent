"""Pydantic models that define the "language" of anomaly rules.

A rule set is per-cube and lives on disk as YAML under
``backend/data/anomaly_rules/<cube>.yaml``. The schema is split into:

- ``IntegrityRule``  — a built-in deterministic check the user toggles on/off
                       (negative_values, headcount_no_salary, ...).
- ``VarianceRule``   — a user-defined "compare measure to a reference and flag
                       material gaps" rule with a dual (% AND absolute) gate.
- ``RuleSet``        — the file-level container for one cube.

Why pydantic + YAML rather than free-form JSON:
  * the LLM rule-suggestion endpoint has a concrete target shape to fill in
  * the editor UI knows what fields exist without guessing
  * the detector can trust validated objects, not raw dicts
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


# ── Integrity (Layer 1) ──────────────────────────────────────────────────────

# Names map 1:1 to functions in ``backend.anomaly.rules.builtin``.
BuiltinRuleName = Literal[
    "negative_values",
    "headcount_no_salary",
    "fte_gt_headcount",
    "alloc_not_100pct",
]


class IntegrityRule(BaseModel):
    """Toggle a built-in deterministic rule on/off for a cube.

    Built-in rules carry their own logic; the user only chooses which to run
    and (optionally) overrides which columns/dimensions the rule looks at.
    """
    name: BuiltinRuleName
    enabled: bool = True
    # Optional per-cube overrides. None = use rule defaults.
    columns: list[str] | None = None
    group_by: list[str] | None = None


# ── Variance (Layer 2) ──────────────────────────────────────────────────────

ReferenceKind = Literal["budget", "forecast", "prior_week", "prior_year", "custom"]


class VarianceRule(BaseModel):
    """Flag when a measure deviates materially from a reference.

    The dual threshold (rel_pct AND abs_value) is non-negotiable: skipping
    either gate produces noise. Both must be present and both must be hit.
    """
    name: str = Field(..., min_length=1, max_length=80,
                      description="Short id, e.g. 'labor_cost_vs_budget'")
    description: str = ""

    measure: str = Field(..., description="Column name in the cube result, e.g. 'GrossSalary'")
    reference: ReferenceKind = "budget"
    reference_label: str = Field("", description="Display label; falls back to reference kind")

    # Dual materiality gate
    rel_pct: float = Field(0.10, ge=0, le=10, description="e.g. 0.10 = 10%")
    abs_value: float = Field(5_000.0, ge=0, description="Absolute movement floor")

    # Grouping — which dimensions form the row key for comparison.
    group_by: list[str] = Field(default_factory=list)

    # Optional reason template; {placeholders} resolved against the row.
    # If omitted, the detector synthesises a default sentence.
    reason_template: str | None = None

    enabled: bool = True

    @model_validator(mode="after")
    def _label_default(self) -> "VarianceRule":
        if not self.reference_label:
            self.reference_label = self.reference.replace("_", " ").title()
        return self


# ── File-level container ────────────────────────────────────────────────────


class RuleSet(BaseModel):
    """One YAML file per cube. Edited by users, drafted by LLM."""
    cube: str = Field(..., min_length=1)
    version: int = 1
    description: str = ""

    integrity: list[IntegrityRule] = Field(default_factory=list)
    variance: list[VarianceRule] = Field(default_factory=list)

    @model_validator(mode="after")
    def _no_duplicate_variance_names(self) -> "RuleSet":
        names = [r.name for r in self.variance]
        if len(names) != len(set(names)):
            raise ValueError("variance rule names must be unique within a cube")
        return self

    def enabled_integrity_names(self) -> list[str]:
        return [r.name for r in self.integrity if r.enabled]

    def enabled_variance_rules(self) -> list[VarianceRule]:
        return [r for r in self.variance if r.enabled]
