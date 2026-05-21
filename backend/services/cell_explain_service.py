"""Cell Explainer service: TM1 data + structured analysis for the Arc plugin."""

import logging
import time
from typing import Optional

from TM1py import TM1Service

from ..config import get_tm1_config

_log = logging.getLogger(__name__)

_BASELINE_DIM_KEYWORDS = ["version", "scenario", "stat", "datatype", "type", "ver ", "data type", "basis"]
_BASELINE_ELEM_KEYWORDS = ["act", "actual", "budget", "plan", "forecast", "fcst", "prior", "ytd", "mtd"]
_DRILL_DIM_KEYWORDS = ["segment", "entity", "department", "product", "channel", "region", "cost centre", "costcentre", "division", "company", "business"]
_MEASURE_DIM_KEYWORDS = ["measure", "account", "metric", "indicator", "kpi"]
_TIME_DIM_KEYWORDS = ["period", "month", "year", "quarter", "week", "time", "date"]

ACTION_LABELS = {
    "compare_baseline": "VARIANCE VS BASELINE",
    "tx_history": "TRANSACTION HISTORY",
    "explain_calc": "CALCULATION EXPLAINED",
    "root_cause": "ROOT CAUSE ANALYSIS",
    "followup": "FOLLOW-UP",
}


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def _fmt(val: Optional[float], signed: bool = True) -> str:
    if val is None:
        return "—"
    sign = "+" if signed and val > 0 else ""
    return f"{sign}{val:,.0f}"


def _fmt_ts(ts: str) -> str:
    """'2025-01-28T14:22:33' → '28 Jan 14:22'"""
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(ts[:19])
        return f"{dt.day} {dt.strftime('%b %H:%M')}"
    except Exception:
        return ts[:16].replace("T", " ")


# ---------------------------------------------------------------------------
# Tuple helpers
# ---------------------------------------------------------------------------

def _find_dim_idx(tuple_elements: list[dict], keywords: list[str]) -> int:
    for i, e in enumerate(tuple_elements):
        if any(kw in e["dimension"].lower() for kw in keywords):
            return i
    return -1


def _find_baseline_dim_idx(tuple_elements: list[dict]) -> int:
    """Find scenario/version dimension: first by dim name, then by element name."""
    idx = _find_dim_idx(tuple_elements, _BASELINE_DIM_KEYWORDS)
    if idx >= 0:
        return idx
    for i, e in enumerate(tuple_elements):
        if any(kw in e["element"].lower() for kw in _BASELINE_ELEM_KEYWORDS):
            return i
    return -1


def _build_where(tuple_elements: list[dict]) -> str:
    return ", ".join(
        f"[{e['dimension']}].[{e['hierarchy']}].[{e['element']}]"
        for e in tuple_elements
    )


# ---------------------------------------------------------------------------
# TM1 data calls
# ---------------------------------------------------------------------------

def _get_cell_value(tm1: TM1Service, cube: str, tuple_elements: list[dict]) -> Optional[float]:
    element_string = ",".join(e["element"] for e in tuple_elements)
    try:
        val = tm1.cells.get_value(cube, element_string)
        return float(val) if val is not None else None
    except Exception as exc:
        _log.debug("get_value failed (%s): %s", cube, exc)
    try:
        where = _build_where(tuple_elements)
        mdx = f"SELECT {{}} ON COLUMNS, {{}} ON ROWS FROM [{cube}] WHERE ({where})"
        cellset = tm1.cells.execute_mdx(mdx)
        if cellset:
            cell = next(iter(cellset.values()))
            return float(cell.get("Value") or 0) if isinstance(cell, dict) else float(cell or 0)
    except Exception as exc:
        _log.debug("MDX single-cell fallback failed: %s", exc)
    return None


def _fetch_scenario_values(
    tm1: TM1Service,
    cube: str,
    tuple_elements: list[dict],
    baselines: list[str],
    scenario_idx: int,
) -> tuple[dict, dict]:
    """Returns (scenario_values: {name: fmt_str}, scenario_raw_values: {name: float})."""
    scenario_values: dict[str, str] = {}
    scenario_raw_values: dict[str, float] = {}
    if not baselines or scenario_idx < 0:
        return scenario_values, scenario_raw_values
    for b in baselines:
        b_tuple = list(tuple_elements)
        orig = b_tuple[scenario_idx]
        b_tuple[scenario_idx] = {**orig, "element": b}
        val = _get_cell_value(tm1, cube, b_tuple)
        scenario_values[b] = _fmt(val, signed=False) if val is not None else "—"
        scenario_raw_values[b] = val if val is not None else 0.0
    return scenario_values, scenario_raw_values


def _get_transaction_log(tm1: TM1Service, cube: str, tuple_elements: list[dict], top: int = 30) -> list[dict]:
    try:
        entries = tm1.transaction_logs.get_entries(cube=cube, top=top)
        if not entries:
            all_entries = tm1.transaction_logs.get_entries(top=top * 5)
            entries = [e for e in all_entries if e.get("Cube") == cube][:top]

        key_elements = {e["element"].lower() for e in tuple_elements}

        result = []
        for item in entries:
            if not isinstance(item, dict):
                continue
            item_tuple = {str(t).lower() for t in (item.get("Tuple") or [])}
            if key_elements and not key_elements.intersection(item_tuple):
                continue
            try:
                old_val = float(item.get("OldValue") or 0)
                new_val = float(item.get("NewValue") or 0)
            except (TypeError, ValueError):
                old_val, new_val = 0.0, 0.0
            result.append({
                "time": _fmt_ts(item.get("TimeStamp", "")),
                "user": item.get("User", ""),
                "process": "",
                "from": f"{old_val:,.0f}",
                "to": f"{new_val:,.0f}",
            })
        return result
    except Exception as exc:
        _log.warning("Transaction log query failed for cube '%s': %s", cube, exc)
        return []


def _collect_dim_drivers_raw(
    tm1: TM1Service,
    cube: str,
    actual_tuple: list[dict],
    baseline_tuple: list[dict],
    drill_dim: str,
    top_n: int = 5,
) -> list[dict]:
    """Returns {name, dimension, _variance} dicts — raw, not yet formatted."""
    drill_elem = next((e for e in actual_tuple if e["dimension"] == drill_dim), None)
    if not drill_elem:
        return []

    measure_idx = _find_dim_idx(actual_tuple, _MEASURE_DIM_KEYWORDS)
    measure_elem = actual_tuple[measure_idx] if measure_idx >= 0 else None

    where_actual = [e for e in actual_tuple if e["dimension"] != drill_dim and e is not measure_elem]
    where_baseline = [e for e in baseline_tuple if e["dimension"] != drill_dim and (
        measure_elem is None or e["dimension"] != measure_elem["dimension"]
    )]

    col_expr = (
        f"[{measure_elem['dimension']}].[{measure_elem['hierarchy']}].[{measure_elem['element']}]"
        if measure_elem
        else "Measures.DefaultMember"
    )
    row_expr = f"[{drill_dim}].[{drill_dim}].[{drill_elem['element']}].Children"
    where_a = _build_where(where_actual) if where_actual else ""
    where_b = _build_where(where_baseline) if where_baseline else ""

    def _run(where_str: str) -> dict:
        where_clause = f"WHERE ({where_str})" if where_str else ""
        mdx = f"SELECT {{{col_expr}}} ON COLUMNS, NON EMPTY {{{row_expr}}} ON ROWS FROM [{cube}] {where_clause}"
        try:
            return tm1.cells.execute_mdx(mdx) or {}
        except Exception as exc:
            _log.debug("Drill MDX failed: %s", exc)
            return {}

    actual_cs = _run(where_a)
    baseline_cs = _run(where_b)

    drivers: list[dict] = []
    for coords, cell in actual_cs.items():
        member_coord = coords[1] if isinstance(coords, tuple) and len(coords) > 1 else (coords[0] if isinstance(coords, tuple) else coords)
        member = str(member_coord).rsplit("[", 1)[-1].rstrip("]")
        actual_val = float(cell.get("Value") or 0) if isinstance(cell, dict) else float(cell or 0)
        b_cell = baseline_cs.get(coords)
        baseline_val = float(b_cell.get("Value") or 0) if isinstance(b_cell, dict) else float(b_cell or 0) if b_cell else 0.0
        variance = actual_val - baseline_val
        if variance != 0:
            drivers.append({"name": member, "dimension": drill_dim, "_variance": variance})

    drivers.sort(key=lambda d: abs(d["_variance"]), reverse=True)
    return drivers[:top_n]


def _format_drivers(raw_drivers: list[dict]) -> list[dict]:
    """Normalize impact_pct across the given list and format _variance → impact."""
    if not raw_drivers:
        return raw_drivers
    max_abs = max(abs(d["_variance"]) for d in raw_drivers)
    for d in raw_drivers:
        d["impact"] = _fmt(d["_variance"])
        d["impact_pct"] = round(abs(d["_variance"]) / max_abs * 100) if max_abs else 0
        d["positive"] = d["_variance"] > 0
        del d["_variance"]
    return raw_drivers


def _get_top_drivers(
    tm1: TM1Service,
    cube: str,
    actual_tuple: list[dict],
    baseline_tuple: list[dict],
    drill_dim: str,
    top_n: int = 5,
) -> list[dict]:
    raw = _collect_dim_drivers_raw(tm1, cube, actual_tuple, baseline_tuple, drill_dim, top_n)
    return _format_drivers(raw)


def _find_consolidated_dims(tm1: TM1Service, tuple_elements: list[dict]) -> list[dict]:
    """Return tuple elements whose TM1 element is of Consolidated type."""
    result = []
    for e in tuple_elements:
        try:
            el = tm1.elements.get(e["dimension"], e["hierarchy"], e["element"])
            if el and "consolidated" in str(getattr(el, "element_type", "")).lower():
                result.append(e)
        except Exception:
            pass
    return result


def _get_root_cause_leaf_combo(
    tm1: TM1Service,
    cube: str,
    actual_tuple: list[dict],
    baseline_tuple: list[dict],
    top_n: int = 10,
) -> tuple[list[dict], list[str]]:
    """
    Root-cause: expand ALL consolidated dims (excluding measure/account),
    CrossJoin their leaf sets, rank top N by actual value.
    Uses execute_mdx_raw for reliable member-name extraction — avoids the
    ambiguous coordinate-tuple format that execute_mdx returns.
    """
    consol_elems = _find_consolidated_dims(tm1, actual_tuple)
    _log.info("root-cause consolidated dims: %s", [e["dimension"] for e in consol_elems])
    if not consol_elems:
        return [], []

    def _is_measure(dim: str) -> bool:
        return any(kw in dim.lower() for kw in _MEASURE_DIM_KEYWORDS)

    # Expand ALL consolidated dims except measure/account — time dims (Period, etc.) ARE included
    expand_info = [e for e in consol_elems if not _is_measure(e["dimension"])]
    if not expand_info:
        expand_info = consol_elems
    expand_names = {e["dimension"] for e in expand_info}

    # Column axis: the measure element
    measure_idx = _find_dim_idx(actual_tuple, _MEASURE_DIM_KEYWORDS)
    measure_e = actual_tuple[measure_idx] if measure_idx >= 0 else None
    col_expr = (
        f"[{measure_e['dimension']}].[{measure_e['hierarchy']}].[{measure_e['element']}]"
        if measure_e else "Measures.DefaultMember"
    )
    if measure_e:
        expand_names.add(measure_e["dimension"])

    # WHERE = fixed dims not being expanded
    where_fixed = [e for e in actual_tuple if e["dimension"] not in expand_names]

    def _leaf_set(e: dict) -> str:
        d, h, el = e["dimension"], e["hierarchy"], e["element"]
        return f"{{Tm1FilterByLevel(Descendants([{d}].[{h}].[{el}]),0)}}"

    sets = [_leaf_set(e) for e in expand_info]
    row_expr = sets[0]
    for s in sets[1:]:
        row_expr = f"CrossJoin({row_expr}, {s})"

    n_row = len(expand_info)
    dim_labels = [e["dimension"] for e in expand_info]
    where_str = _build_where(where_fixed) if where_fixed else ""
    wc = f"WHERE ({where_str})" if where_str else ""
    mdx = f"SELECT {{{col_expr}}} ON COLUMNS, NON EMPTY {{{row_expr}}} ON ROWS FROM [{cube}] {wc}"
    _log.info("root-cause MDX (%d expand dims): %.600s", n_row, mdx)

    try:
        raw = tm1.cells.execute_mdx_raw(mdx, member_properties=["Name"])
    except Exception as ex:
        _log.warning("root-cause MDX failed: %s\nMDX: %s", ex, mdx)
        return [], dim_labels

    # Parse raw TM1 REST response: Axis ordinal 0=COLUMNS, 1=ROWS
    axes_by_ord = {a.get("Ordinal", i): a for i, a in enumerate(raw.get("Axes", []))}
    row_tuples = axes_by_ord.get(1, {}).get("Tuples", [])
    cells_raw  = raw.get("Cells", [])
    n_cols     = max(1, len(axes_by_ord.get(0, {}).get("Tuples", [])))

    drivers: list[dict] = []
    for row_idx, tpl in enumerate(row_tuples):
        cell_idx = row_idx * n_cols
        if cell_idx >= len(cells_raw):
            break
        cell = cells_raw[cell_idx]
        raw_val = cell.get("Value") if isinstance(cell, dict) else cell
        if raw_val is None:
            continue
        try:
            actual_val = float(raw_val)
        except (TypeError, ValueError):
            continue
        if actual_val == 0:
            continue

        members = tpl.get("Members", [])
        names = [m.get("Name", "?") for m in members]
        if len(names) < n_row:
            continue

        drivers.append({
            "name":      " × ".join(names[:n_row]),
            "dimension": " × ".join(dim_labels),
            "_actual":   actual_val,
        })

    drivers.sort(key=lambda d: abs(d["_actual"]), reverse=True)
    drivers = drivers[:top_n]

    if drivers:
        max_abs = max(abs(d["_actual"]) for d in drivers)
        for d in drivers:
            d["actual"]     = _fmt(d["_actual"], signed=False)
            d["impact"]     = _fmt(d["_actual"], signed=False)
            d["impact_pct"] = round(abs(d["_actual"]) / max_abs * 100) if max_abs else 0
            d["positive"]   = d["_actual"] >= 0
            del d["_actual"]

    return drivers, dim_labels


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def explain_variance(
    cube: str,
    tuple_elements: list[dict],
    value: Optional[str],
    baseline: str,
    baselines: Optional[list[str]] = None,
) -> dict:
    started = time.time()

    with TM1Service(**get_tm1_config()) as tm1:
        scenario_idx = _find_baseline_dim_idx(tuple_elements)
        _log.info("explain-cell dims=%s baseline_idx=%d",
                  [e["dimension"] for e in tuple_elements], scenario_idx)

        baseline_tuple = list(tuple_elements)
        if scenario_idx >= 0:
            orig = baseline_tuple[scenario_idx]
            baseline_tuple[scenario_idx] = {**orig, "element": baseline}

        actual_val = _get_cell_value(tm1, cube, tuple_elements)
        if actual_val is None:
            try:
                actual_val = float((value or "").replace(",", "").replace(" ", ""))
            except ValueError:
                actual_val = None

        baseline_val = _get_cell_value(tm1, cube, baseline_tuple)

        current_scenario = tuple_elements[scenario_idx]["element"] if scenario_idx >= 0 else None
        scenario_values, scenario_raw_values = _fetch_scenario_values(
            tm1, cube, tuple_elements, baselines or [], scenario_idx
        )

        variance = (actual_val or 0.0) - (baseline_val or 0.0)
        variance_pct = (variance / baseline_val * 100) if baseline_val else None

        drill_dim_idx = _find_dim_idx(tuple_elements, _DRILL_DIM_KEYWORDS)
        drill_dim = tuple_elements[drill_dim_idx]["dimension"] if drill_dim_idx >= 0 else None

        drivers: list[dict] = []
        if drill_dim:
            drivers = _get_top_drivers(tm1, cube, tuple_elements, baseline_tuple, drill_dim)

        transactions = _get_transaction_log(tm1, cube, tuple_elements)

    actual_fmt = _fmt(actual_val, signed=False) if actual_val is not None else (value or "—")
    baseline_fmt = _fmt(baseline_val, signed=False) if baseline_val is not None else "—"
    variance_fmt = _fmt(variance)
    pct_str = f" ({variance_pct:+.1f}%)" if variance_pct is not None else ""
    direction = "higher" if variance > 0 else "lower"

    measure_elem = next((e["element"] for e in tuple_elements
                         if _find_dim_idx([e], _MEASURE_DIM_KEYWORDS) >= 0), tuple_elements[-1]["element"])
    period_elem = next((e["element"] for e in tuple_elements
                        if _find_dim_idx([e], _TIME_DIM_KEYWORDS) >= 0), "")

    summary = (
        f"<strong>{measure_elem}</strong>"
        + (f" in <strong>{period_elem}</strong>" if period_elem else "")
        + f" is <strong>{actual_fmt}</strong>"
        + (
            f", which is <strong>{variance_fmt} {direction} than {baseline}</strong>{pct_str}."
            if baseline_val is not None else "."
        )
    )

    followups = [
        f"Show all transactions on {measure_elem} this quarter",
        f"Drill into {drill_dim} by entity" if drill_dim else "Drill into another dimension",
        "Compare ACT vs FCST for the same cell",
        "Who last changed this cell?",
    ]

    return {
        "action": "compare_baseline",
        "label": ACTION_LABELS["compare_baseline"],
        "baseline": baseline,
        "current_scenario": current_scenario,
        "scenario_values": scenario_values,
        "scenario_raw_values": scenario_raw_values,
        "actual": actual_fmt,
        "plan": baseline_fmt,
        "variance": variance_fmt,
        "variance_pct": f"{variance_pct:+.1f}%" if variance_pct is not None else None,
        "variance_positive": variance >= 0,
        "drill_dim": drill_dim,
        "summary": summary,
        "drivers": drivers,
        "transactions": transactions,
        "followups": followups[:4],
        "trace": {
            "tool_calls": 2 + (1 if drivers else 0) + 1,
            "input_tokens": 0,
            "output_tokens": 0,
            "latency_ms": int((time.time() - started) * 1000),
        },
    }


def explain_root_cause(
    cube: str,
    tuple_elements: list[dict],
    value: Optional[str],
    baseline: str,
    baselines: Optional[list[str]] = None,
) -> dict:
    started = time.time()

    with TM1Service(**get_tm1_config()) as tm1:
        scenario_idx = _find_baseline_dim_idx(tuple_elements)
        _log.info("root-cause dims=%s baseline_idx=%d",
                  [e["dimension"] for e in tuple_elements], scenario_idx)

        baseline_tuple = list(tuple_elements)
        if scenario_idx >= 0:
            orig = baseline_tuple[scenario_idx]
            baseline_tuple[scenario_idx] = {**orig, "element": baseline}

        actual_val = _get_cell_value(tm1, cube, tuple_elements)
        if actual_val is None:
            try:
                actual_val = float((value or "").replace(",", "").replace(" ", ""))
            except ValueError:
                actual_val = None

        baseline_val = _get_cell_value(tm1, cube, baseline_tuple)

        current_scenario = tuple_elements[scenario_idx]["element"] if scenario_idx >= 0 else None
        scenario_values, scenario_raw_values = _fetch_scenario_values(
            tm1, cube, tuple_elements, baselines or [], scenario_idx
        )

        multi_drivers, dims_used = _get_root_cause_leaf_combo(
            tm1, cube, tuple_elements, baseline_tuple
        )

    variance = (actual_val or 0.0) - (baseline_val or 0.0)
    variance_pct = (variance / baseline_val * 100) if baseline_val else None

    actual_fmt = _fmt(actual_val, signed=False) if actual_val is not None else (value or "—")
    baseline_fmt = _fmt(baseline_val, signed=False) if baseline_val is not None else "—"

    if multi_drivers:
        top = multi_drivers[0]
        n_dims = len(dims_used)
        dim_str      = " × ".join(dims_used)
        scenario_lbl = current_scenario or tuple_elements[scenario_idx]["element"] if scenario_idx >= 0 else "current scenario"
        summary = (
            f"Top <strong>{len(multi_drivers)}</strong> leaf combinations across "
            f"<strong>{n_dims}</strong> dimension{'s' if n_dims != 1 else ''} "
            f"({dim_str}), ranked by <strong>{scenario_lbl}</strong> value. "
            f"Largest: <strong>{top['name']}</strong> at <strong>{top['actual']}</strong>."
        )
    else:
        summary = (
            "No significant child-level drivers found. "
            "This cell may already be at a leaf level or have no children with variance."
        )

    measure_elem = next((e["element"] for e in tuple_elements
                         if _find_dim_idx([e], _MEASURE_DIM_KEYWORDS) >= 0), tuple_elements[-1]["element"])

    return {
        "action": "root_cause",
        "label": ACTION_LABELS["root_cause"],
        "baseline": baseline,
        "current_scenario": current_scenario,
        "scenario_values": scenario_values,
        "scenario_raw_values": scenario_raw_values,
        "actual": actual_fmt,
        "plan": baseline_fmt,
        "variance": _fmt(variance),
        "variance_pct": f"{variance_pct:+.1f}%" if variance_pct is not None else None,
        "variance_positive": variance >= 0,
        "drill_dims": dims_used,
        "multi_drivers": multi_drivers,
        "summary": summary,
        "followups": [
            f"Show transactions for {multi_drivers[0]['name']}" if multi_drivers else "Show transaction history",
            f"Drill into {dims_used[1]} for {multi_drivers[0]['name']}" if len(dims_used) > 1 and multi_drivers else "Compare ACT vs FCST",
            f"Show all {measure_elem} variance by month",
            "Who last changed this cell?",
        ],
        "trace": {
            "tool_calls": 1 + len(dims_used) * 2,
            "input_tokens": 0,
            "output_tokens": 0,
            "latency_ms": int((time.time() - started) * 1000),
        },
    }


def explain_tx_history(cube: str, tuple_elements: list[dict]) -> dict:
    started = time.time()

    with TM1Service(**get_tm1_config()) as tm1:
        transactions = _get_transaction_log(tm1, cube, tuple_elements, top=50)

    count = len(transactions)
    summary = f"Found <strong>{count} transaction{'' if count == 1 else 's'}</strong> in the log for this cube."
    if transactions:
        latest = transactions[0]
        summary += f" Most recent: <strong>{latest['user']}</strong>"
        if latest.get("process"):
            summary += f" via <code>{latest['process']}</code>"
        summary += f" → <strong>{latest['to']}</strong>."

    return {
        "action": "tx_history",
        "label": ACTION_LABELS["tx_history"],
        "summary": summary,
        "transactions": transactions,
        "followups": [
            "Compare ACT vs Plan for this cell",
            "Show all changes by this user this month",
            "Who approved this entry?",
        ],
        "trace": {
            "tool_calls": 1,
            "input_tokens": 0,
            "output_tokens": 0,
            "latency_ms": int((time.time() - started) * 1000),
        },
    }
