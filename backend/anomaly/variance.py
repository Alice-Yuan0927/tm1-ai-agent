"""Variance detection — Layer 2.

Compare a measure between 'current' and 'reference' row sets, keyed by
the rule's group_by dims. A row is flagged only if BOTH the relative (%)
and the absolute ($) gates are cleared. Works on plain ``list[dict]``
rows — no pandas dependency.
"""

from __future__ import annotations

from collections.abc import Iterable

from .flag import Flag, Layer
from .rules.schema import VarianceRule


def _fmt_pct(x: float) -> str:
    return f"{x * 100:+.0f}%"


def _fmt_money(x: float) -> str:
    return f"{x:+,.0f}"


def _num(row: dict, col: str) -> float | None:
    v = row.get(col)
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _has_col(rows: Iterable[dict], col: str) -> bool:
    for r in rows:
        if col in r:
            return True
    return False


def _index_by_dims(rows: list[dict], dims: list[str], measure: str) -> dict[tuple, float]:
    """Sum measure values by the dim tuple. Missing measure → 0."""
    acc: dict[tuple, float] = {}
    for r in rows:
        if any(d not in r for d in dims):
            continue
        v = _num(r, measure)
        if v is None:
            continue
        k = tuple(str(r[d]) for d in dims)
        acc[k] = acc.get(k, 0.0) + v
    return acc


def _default_reason(rule: VarianceRule, key: dict[str, str],
                    abs_delta: float, rel_delta: float) -> str:
    direction = "up" if abs_delta > 0 else "down"
    key_str = ", ".join(f"{k}={v}" for k, v in key.items())
    return (f"{rule.measure} {direction} {_fmt_pct(rel_delta)} "
            f"({_fmt_money(abs_delta)}) vs {rule.reference_label} for {key_str}.")


def detect_variance(
    rule: VarianceRule,
    current: list[dict],
    reference: list[dict],
) -> list[Flag]:
    measure = rule.measure
    dims = list(rule.group_by)

    if not _has_col(current, measure) or not _has_col(reference, measure):
        return []
    if dims and (not all(_has_col(current, d) for d in dims)
                 or not all(_has_col(reference, d) for d in dims)):
        return []

    cur_idx = _index_by_dims(current, dims, measure)
    ref_idx = _index_by_dims(reference, dims, measure)

    all_keys = set(cur_idx.keys()) | set(ref_idx.keys())
    out: list[Flag] = []
    for k in all_keys:
        cur_v = cur_idx.get(k, 0.0)
        ref_v = ref_idx.get(k, 0.0)
        abs_delta = cur_v - ref_v

        if ref_v != 0:
            rel_delta = abs_delta / ref_v
        else:
            rel_delta = 1.0 if cur_v != 0 else 0.0

        if abs(rel_delta) < rule.rel_pct or abs(abs_delta) < rule.abs_value:
            continue

        key = {d: k[i] for i, d in enumerate(dims)}

        if rule.reason_template:
            ctx = {
                **key,
                "direction": "up" if abs_delta > 0 else "down",
                "rel_delta_pct": _fmt_pct(rel_delta),
                "abs_delta_money": _fmt_money(abs_delta),
                "measure": rule.measure,
                "reference_label": rule.reference_label,
            }
            try:
                reason = rule.reason_template.format(**ctx)
            except KeyError:
                reason = _default_reason(rule, key, abs_delta, rel_delta)
        else:
            reason = _default_reason(rule, key, abs_delta, rel_delta)

        out.append(Flag(
            layer=Layer.VARIANCE,
            rule=rule.name,
            key=key,
            reason=reason,
            detail={
                "cur": cur_v,
                "ref": ref_v,
                "abs_delta": abs_delta,
                "rel_delta": rel_delta,
                "measure": rule.measure,
                "reference_label": rule.reference_label,
            },
        ))
    return out
