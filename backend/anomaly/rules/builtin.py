"""Built-in integrity rule implementations.

These work on plain ``list[dict]`` rows — the same shape the TM1 service
returns from MDX execution — so the anomaly module does not pull in pandas.

Each function silently skips itself if a required column is missing.
A single RuleSet can therefore live across cubes without crashing on
shape mismatch.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from ..flag import Flag, Layer


def _row_key(row: dict, dims: Sequence[str]) -> dict[str, str]:
    return {d: str(row.get(d, "")) for d in dims if d in row}


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


def rule_negative_values(rows: list[dict], dims: Sequence[str],
                         columns: Sequence[str] = ("GrossSalary", "FTE", "Headcount")) -> list[Flag]:
    out: list[Flag] = []
    for col in columns:
        if not _has_col(rows, col):
            continue
        for r in rows:
            v = _num(r, col)
            if v is None or v >= 0:
                continue
            key = _row_key(r, dims)
            out.append(Flag(
                layer=Layer.INTEGRITY,
                rule=f"negative_{col.lower()}",
                key=key,
                reason=f"Negative {col} ({v:,.0f}) for {key}. Impossible value.",
                detail={col: v},
            ))
    return out


def rule_headcount_no_salary(rows: list[dict], dims: Sequence[str]) -> list[Flag]:
    if not (_has_col(rows, "Headcount") and _has_col(rows, "GrossSalary")):
        return []
    out: list[Flag] = []
    for r in rows:
        hc = _num(r, "Headcount")
        sal = _num(r, "GrossSalary")
        if hc is None or sal is None:
            continue
        if hc > 0 and sal <= 0:
            key = _row_key(r, dims)
            out.append(Flag(
                layer=Layer.INTEGRITY,
                rule="headcount_no_salary",
                key=key,
                reason=f"{int(hc)} headcount but $0 gross salary for {key}. Salary data missing.",
                detail={"headcount": int(hc)},
            ))
    return out


def rule_fte_gt_headcount(rows: list[dict], dims: Sequence[str]) -> list[Flag]:
    if not (_has_col(rows, "FTE") and _has_col(rows, "Headcount")):
        return []
    out: list[Flag] = []
    for r in rows:
        fte = _num(r, "FTE")
        hc = _num(r, "Headcount")
        if fte is None or hc is None:
            continue
        if fte > hc:
            key = _row_key(r, dims)
            out.append(Flag(
                layer=Layer.INTEGRITY,
                rule="fte_gt_headcount",
                key=key,
                reason=(f"FTE ({fte:.1f}) exceeds headcount ({int(hc)}) "
                        f"for {key}. Allocation error likely."),
                detail={"fte": fte, "headcount": int(hc)},
            ))
    return out


def rule_alloc_not_100pct(rows: list[dict], dims: Sequence[str],
                          group_by: Sequence[str] | None = None,
                          alloc_col: str = "AllocPct",
                          tol: float = 0.001) -> list[Flag]:
    if not _has_col(rows, alloc_col):
        return []
    group_dims = list(group_by or dims)
    if not group_dims:
        return []

    totals: dict[tuple, float] = {}
    sample_row: dict[tuple, dict] = {}
    for r in rows:
        if any(d not in r for d in group_dims):
            continue
        v = _num(r, alloc_col)
        if v is None:
            continue
        gk = tuple(str(r[d]) for d in group_dims)
        totals[gk] = totals.get(gk, 0.0) + v
        sample_row.setdefault(gk, r)

    out: list[Flag] = []
    for gk, total in totals.items():
        if abs(total - 1.0) <= tol:
            continue
        key = {d: gk[i] for i, d in enumerate(group_dims)}
        out.append(Flag(
            layer=Layer.INTEGRITY,
            rule="alloc_not_100pct",
            key=key,
            reason=f"Allocation sums to {total * 100:.1f}% (not 100%) for {key}.",
            detail={"alloc_total": total},
        ))
    return out


# Name → callable. The RuleSet's IntegrityRule.name must match a key here.
BUILTIN_INTEGRITY = {
    "negative_values": rule_negative_values,
    "headcount_no_salary": rule_headcount_no_salary,
    "fte_gt_headcount": rule_fte_gt_headcount,
    "alloc_not_100pct": rule_alloc_not_100pct,
}
