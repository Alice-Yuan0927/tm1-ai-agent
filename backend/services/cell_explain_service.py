"""Cell Explainer service: TM1 data + structured analysis for the Arc plugin."""

import logging
import threading
import time
from datetime import datetime, time as dt_time
from typing import Optional

from TM1py import TM1Service
from TM1py.Utils import format_url

from ..config import get_tm1_config
from ..tm1.cache import get_dim_hierarchy_edges
from ..tm1.cache.db import connect

_log = logging.getLogger(__name__)

_TX_LOG_CACHE_TTL_SEC = 180
_TX_LOG_CACHE_MAX = 128
_tx_log_cache_lock = threading.Lock()
_tx_log_cache: dict[tuple, tuple[float, list[dict]]] = {}

_BASELINE_DIM_KEYWORDS = ["version", "scenario", "stat", "datatype", "type", "ver ", "data type", "basis"]
_BASELINE_ELEM_KEYWORDS = ["act", "actual", "budget", "plan", "forecast", "fcst", "prior", "ytd", "mtd"]

ACTION_LABELS = {
    "tx_history": "TRANSACTION HISTORY",
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


def _tx_summary_row(item: dict) -> dict:
    try:
        old_val = float(item.get("OldValue") or 0)
        new_val = float(item.get("NewValue") or 0)
    except (TypeError, ValueError):
        old_val, new_val = 0.0, 0.0
    return {
        "time": _fmt_ts(item.get("TimeStamp", "")),
        "user": item.get("User", ""),
        "tuple": " · ".join(str(t) for t in (item.get("Tuple") or [])),
        "from": f"{old_val:,.0f}",
        "to": f"{new_val:,.0f}",
    }


def list_cube_scenarios(cube: str, dimension: str = "") -> dict:
    """Return leaf elements of the scenario/version dimension for compare cards."""
    try:
        with TM1Service(**get_tm1_config()) as tm1:
            dim_names = list(tm1.cubes.get_dimension_names(cube))
            scenario_dim = dimension
            if not scenario_dim:
                for dim_name in dim_names:
                    if any(kw in dim_name.lower() for kw in _BASELINE_DIM_KEYWORDS):
                        scenario_dim = dim_name
                        break
            if not scenario_dim:
                return {"dimension": "", "elements": []}

            elements = list(tm1.elements.get_element_names(scenario_dim, scenario_dim))
            leaves: list[str] = []
            for element in elements:
                try:
                    el = tm1.elements.get(scenario_dim, scenario_dim, element)
                    if str(getattr(el, "element_type", "")).lower() != "consolidated":
                        leaves.append(element)
                except Exception:
                    leaves.append(element)
            return {"dimension": scenario_dim, "elements": leaves[:30]}
    except Exception as exc:
        _log.warning("cube-scenarios failed: %s", exc)
        return {"dimension": "", "elements": []}


def get_cube_info(cube: str) -> dict:
    """Return cube dimensions and recent transaction activity."""
    try:
        with TM1Service(**get_tm1_config()) as tm1:
            dimensions = list(tm1.cubes.get_dimension_names(cube))
            raw = tm1.transaction_logs.get_entries(cube=cube, top=20) or []
            transactions = [
                _tx_summary_row(item)
                for item in raw
                if isinstance(item, dict)
            ]
        return {"cube": cube, "dimensions": dimensions, "transactions": transactions}
    except Exception as exc:
        _log.warning("cube-info failed: %s", exc)
        return {"cube": cube, "dimensions": [], "transactions": []}


def _parse_tx_date(value: str | None, *, end_of_day: bool = False) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        if len(raw) == 10:
            d = datetime.fromisoformat(raw).date()
            return datetime.combine(d, dt_time.max if end_of_day else dt_time.min)
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Tuple helpers
# ---------------------------------------------------------------------------

def _find_value_measure_dim_idx(tuple_elements: list[dict]) -> int:
    """In these TM1 cubes, the final cube dimension is the measure dimension."""
    return len(tuple_elements) - 1 if tuple_elements else -1


def _find_baseline_dim_idx(tuple_elements: list[dict]) -> int:
    """Find scenario/version dimension: first by dim name, then by element name."""
    for i, e in enumerate(tuple_elements):
        if any(kw in e["dimension"].lower() for kw in _BASELINE_DIM_KEYWORDS):
            return i
    for i, e in enumerate(tuple_elements):
        if any(kw in e["element"].lower() for kw in _BASELINE_ELEM_KEYWORDS):
            return i
    return -1


def _build_where(tuple_elements: list[dict]) -> str:
    return ", ".join(
        f"[{e['dimension']}].[{e['hierarchy']}].[{_resolve_element_name(e['dimension'], e['element'])}]"
        for e in tuple_elements
    )


def _get_cell_value(tm1: TM1Service, cube: str, tuple_elements: list[dict]) -> Optional[float]:
    element_string = ",".join(_resolve_element_name(e["dimension"], e["element"]) for e in tuple_elements)
    try:
        val = tm1.cells.get_value(cube, element_string)
        return float(val) if val is not None else None
    except Exception as exc:
        _log.debug("get_value failed (%s): %s", cube, exc)
        return None


def _fetch_scenario_values(
    tm1: TM1Service,
    cube: str,
    tuple_elements: list[dict],
    scenarios: list[str] | None,
) -> tuple[dict[str, str], dict[str, float]]:
    scenario_idx = _find_baseline_dim_idx(tuple_elements)
    if scenario_idx < 0 or not scenarios:
        return {}, {}

    scenario_values: dict[str, str] = {}
    scenario_raw_values: dict[str, float] = {}
    for scenario in scenarios[:30]:
        s_tuple = list(tuple_elements)
        orig = s_tuple[scenario_idx]
        s_tuple[scenario_idx] = {**orig, "element": scenario}
        val = _get_cell_value(tm1, cube, s_tuple)
        scenario_values[scenario] = _fmt(val, signed=False) if val is not None else "—"
        scenario_raw_values[scenario] = val if val is not None else 0.0
    return scenario_values, scenario_raw_values


def _is_consolidated_element(name: str) -> bool:
    """Name-only heuristic fallback — used when the TM1 API call fails."""
    lower = name.lower().strip()
    return lower.startswith("all ") or lower.startswith("total ") or lower in {"all", "total", "grand total"}


def _norm_elem(value) -> str:
    return str(value or "").strip().lower()


def _tm1_element_name(value) -> str:
    for attr in ("name", "element_name"):
        name = getattr(value, attr, None)
        if name:
            return str(name)
    if isinstance(value, dict):
        for key in ("name", "Name", "element_name"):
            if value.get(key):
                return str(value[key])
    return str(value or "")


def _tm1_element_type(value) -> str:
    for attr in ("element_type", "type"):
        etype = getattr(value, attr, None)
        if etype:
            return str(etype).lower()
    if isinstance(value, dict):
        for key in ("element_type", "ElementType", "type", "Type"):
            if value.get(key):
                return str(value[key]).lower()
    return ""


def _cache_element_info(dim: str, name: str) -> tuple[str, str] | None:
    """Resolve an element by principal name, alias, or attribute value from schema cache."""
    try:
        conn = connect()
        row = conn.execute(
            "SELECT element_name, element_type FROM elements"
            " WHERE dim_name = ? AND LOWER(element_name) = LOWER(?)"
            " LIMIT 1",
            (dim, name),
        ).fetchone()
        if row:
            return str(row[0]), str(row[1] or "")

        row = conn.execute(
            "SELECT e.element_name, e.element_type"
            " FROM elements e"
            " JOIN element_aliases a"
            "   ON a.dim_name = e.dim_name AND a.element_name = e.element_name"
            " WHERE e.dim_name = ? AND LOWER(a.alias_value) = LOWER(?)"
            " LIMIT 1",
            (dim, name),
        ).fetchone()
        if row:
            return str(row[0]), str(row[1] or "")

        row = conn.execute(
            "SELECT e.element_name, e.element_type"
            " FROM elements e"
            " JOIN element_attribute_values a"
            "   ON a.dim_name = e.dim_name AND a.element_name = e.element_name"
            " WHERE e.dim_name = ? AND LOWER(a.attr_value) = LOWER(?)"
            " LIMIT 1",
            (dim, name),
        ).fetchone()
        if row:
            return str(row[0]), str(row[1] or "")
    except Exception:
        return None
    return None


def _resolve_element_name(dim: str, name: str) -> str:
    info = _cache_element_info(dim, name)
    return info[0] if info else name


def _tx_log_cache_key(
    cube: str,
    tuple_elements: list[dict],
    top: int,
    since: datetime | None = None,
    until: datetime | None = None,
    max_pages: int = 1,
) -> tuple:
    return (
        cube,
        top,
        since.isoformat() if since else "",
        until.isoformat() if until else "",
        max_pages,
        tuple(
            (
                str(e.get("dimension", "")),
                str(e.get("hierarchy", "")),
                _resolve_element_name(str(e.get("dimension", "")), str(e.get("element", ""))),
            )
            for e in tuple_elements
        ),
    )


def _tx_log_cache_get(key: tuple) -> list[dict] | None:
    now = time.time()
    with _tx_log_cache_lock:
        cached = _tx_log_cache.get(key)
        if not cached:
            return None
        expires_at, rows = cached
        if expires_at <= now:
            _tx_log_cache.pop(key, None)
            return None
        return [dict(row) for row in rows]


def _tx_log_cache_set(key: tuple, rows: list[dict]) -> None:
    now = time.time()
    with _tx_log_cache_lock:
        if len(_tx_log_cache) >= _TX_LOG_CACHE_MAX:
            oldest_key = min(_tx_log_cache, key=lambda k: _tx_log_cache[k][0])
            _tx_log_cache.pop(oldest_key, None)
        _tx_log_cache[key] = (
            now + _TX_LOG_CACHE_TTL_SEC,
            [dict(row) for row in rows],
        )


def _is_tm1_consolidated(tm1: TM1Service, dim: str, hier: str, name: str, hier_obj=None) -> bool | None:
    resolved_name = _resolve_element_name(dim, name)
    lname = _norm_elem(name)
    resolved_lname = _norm_elem(resolved_name)
    if hier_obj is not None:
        elem_objs = getattr(hier_obj, "elements", {}) or {}
        elem_match = elem_objs.get(resolved_name) or elem_objs.get(name) or next(
            (v for k, v in elem_objs.items() if _norm_elem(k) in {lname, resolved_lname}), None
        )
        if elem_match is not None:
            etype = _tm1_element_type(elem_match)
            if etype:
                return "consolidated" in etype

    try:
        elem = tm1.elements.get(dim, hier, resolved_name)
        etype = _tm1_element_type(elem)
        if etype:
            return "consolidated" in etype
    except Exception:
        pass

    try:
        cons_names = tm1.elements.get_consolidated_element_names(dim, hier) or []
        return any(_norm_elem(c) in {lname, resolved_lname} for c in cons_names)
    except Exception:
        pass

    info = _cache_element_info(dim, name)
    if info:
        return str(info[1]).lower() == "consolidated"
    return None


def _leaf_descendants(hier_obj, element_name: str) -> set[str]:
    """BFS from element_name in a Hierarchy object; return all leaf (non-Consolidated) descendants."""
    elements = getattr(hier_obj, "elements", {}) or {}
    # Case-insensitive lookup for the root
    root_key = next((k for k in elements if k.lower() == element_name.lower()), element_name)
    leaves: set[str] = set()
    visited: set[str] = set()
    queue = [root_key]
    while queue:
        node = queue.pop()
        if node in visited:
            continue
        visited.add(node)
        elem = elements.get(node)
        if elem is None:
            continue
        etype = str(getattr(elem, "element_type", "")).lower()
        if "consolidated" in etype:
            for child in (getattr(elem, "element_components", None) or {}).keys():
                if child not in visited:
                    queue.append(child)
        else:
            leaves.add(node.lower())
    return leaves


def _leaf_descendants_from_tm1(tm1: TM1Service, dim: str, hier: str, name: str) -> set[str]:
    """Return leaf descendants through TM1py element APIs when Hierarchy.elements is unavailable."""
    leaves: set[str] = set()
    resolved_name = _resolve_element_name(dim, name)
    try:
        members = tm1.elements.get_members_under_consolidation(dim, hier, resolved_name, max_depth=999) or []
    except TypeError:
        try:
            members = tm1.elements.get_members_under_consolidation(dim, hier, resolved_name) or []
        except Exception:
            members = []
    except Exception:
        members = []

    for member in members:
        member_name = _tm1_element_name(member)
        if not member_name:
            continue
        is_consol = _is_tm1_consolidated(tm1, dim, hier, member_name)
        if is_consol is False:
            leaves.add(_norm_elem(member_name))

    if leaves:
        return leaves

    # Last resort: if TM1py returned names only and type checks failed, use all descendants
    # except the parent itself. This avoids requiring a consolidation name to appear in a
    # transaction tuple, which TM1 normally will not do for input leaves.
    for member in members:
        member_name = _tm1_element_name(member)
        if member_name and _norm_elem(member_name) != _norm_elem(name):
            leaves.add(_norm_elem(member_name))
    return leaves


def _leaf_descendants_from_cache(dim: str, name: str) -> set[str]:
    resolved_name = _resolve_element_name(dim, name)
    try:
        edges = get_dim_hierarchy_edges([dim]).get(dim, [])
    except Exception:
        return set()
    if not edges:
        return set()

    children_by_parent: dict[str, list[str]] = {}
    for parent, child in edges:
        p_norm, c_norm = _norm_elem(parent), _norm_elem(child)
        if p_norm and c_norm:
            children_by_parent.setdefault(p_norm, []).append(c_norm)

    leaves: set[str] = set()
    visited: set[str] = set()
    queue = list(children_by_parent.get(_norm_elem(resolved_name), []))
    while queue:
        node = queue.pop()
        if node in visited:
            continue
        visited.add(node)
        children = children_by_parent.get(node, [])
        if children:
            queue.extend(children)
        else:
            leaves.add(node)
    return leaves


def _get_tx_entries_page(
    tm1: TM1Service,
    *,
    cube: str,
    element_filter: dict[str, str] | None,
    top: int,
    skip: int = 0,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[dict]:
    """Fetch one TransactionLogEntries page. TM1py get_entries lacks $skip."""
    order = "desc"
    url = f"/TransactionLogEntries?$orderby=TimeStamp {order} "
    filters = []
    if cube:
        filters.append(format_url(f"Cube eq '{cube}'"))
    if element_filter:
        expr = " or ".join([f"e {op} '{elem}'" for elem, op in element_filter.items()])
        filters.append(format_url(f"Tuple/any(e: {expr})"))
    if since:
        filters.append(format_url(f"TimeStamp ge {since.strftime('%Y-%m-%dT%H:%M:%SZ')}"))
    if until:
        filters.append(format_url(f"TimeStamp le {until.strftime('%Y-%m-%dT%H:%M:%SZ')}"))
    if filters:
        url += "&$filter={}".format(" and ".join(filters))
    if top:
        url += f"&$top={int(top)}"
    if skip:
        url += f"&$skip={int(skip)}"
    response = tm1.transaction_logs._rest.GET(url)
    return response.json().get("value", [])


def _tx_process_source(item: dict) -> str:
    """Best-effort TI/source extraction from TM1 transaction log payloads."""
    for key in (
        "Process",
        "ProcessName",
        "Process_Name",
        "TIProcess",
        "TurboIntegratorProcess",
        "Source",
        "SourceName",
        "Chore",
        "ChoreName",
        "Application",
    ):
        value = item.get(key)
        if value:
            return str(value)
    return ""


def _data_bearing_leaf_descendants(
    tm1: TM1Service,
    cube: str,
    tuple_elements: list[dict],
    consol_element: dict,
    fallback_leaves: set[str],
) -> set[str]:
    """Return leaf descendants under consol_element that are non-empty in the current cell context."""
    dim = consol_element["dimension"]
    hier = consol_element["hierarchy"]
    element = _resolve_element_name(dim, consol_element["element"])

    measure_idx = _find_value_measure_dim_idx(tuple_elements)
    measure_elem = tuple_elements[measure_idx] if measure_idx >= 0 else None
    col_expr = (
        f"[{measure_elem['dimension']}].[{measure_elem['hierarchy']}].[{_resolve_element_name(measure_elem['dimension'], measure_elem['element'])}]"
        if measure_elem
        else "Measures.DefaultMember"
    )
    where_fixed = [
        e for e in tuple_elements
        if e["dimension"] != dim and (measure_elem is None or e["dimension"] != measure_elem["dimension"])
    ]
    where_str = _build_where(where_fixed) if where_fixed else ""
    wc = f"WHERE ({where_str})" if where_str else ""
    row_expr = f"{{Tm1FilterByLevel(Descendants([{dim}].[{hier}].[{element}]),0)}}"
    mdx = f"SELECT {{{col_expr}}} ON COLUMNS, NON EMPTY {row_expr} ON ROWS FROM [{cube}] {wc}"

    try:
        raw = tm1.cells.execute_mdx_raw(mdx, member_properties=["Name"])
    except Exception as exc:
        _log.warning("tx-log non-empty leaf MDX failed for %s:%s (%s)", dim, element, exc)
        return set(fallback_leaves)

    axes_by_ord = {a.get("Ordinal", i): a for i, a in enumerate(raw.get("Axes", []))}
    row_tuples = axes_by_ord.get(1, {}).get("Tuples", [])
    leaves: set[str] = set()
    for tpl in row_tuples:
        members = tpl.get("Members", []) if isinstance(tpl, dict) else []
        if not members:
            continue
        member = members[-1]
        if isinstance(member, dict):
            props = member.get("Properties", {})
            prop_name = props.get("Name") if isinstance(props, dict) else ""
            name = prop_name or member.get("Name") or member.get("UniqueName") or ""
        else:
            name = str(member)
        if not name:
            continue
        leaf = str(name).rsplit("[", 1)[-1].rstrip("]")
        leaf_norm = _norm_elem(leaf)
        if leaf_norm in fallback_leaves:
            leaves.add(leaf_norm)

    _log.warning(
        "tx-log consolidation %s:%s non-empty leaf descendants: %d / %d",
        dim, element, len(leaves), len(fallback_leaves),
    )
    return leaves or set(fallback_leaves)


def _get_transaction_log(
    tm1: TM1Service,
    cube: str,
    tuple_elements: list[dict],
    top: int = 30,
    since: datetime | None = None,
    until: datetime | None = None,
    max_pages: int = 1,
) -> list[dict]:
    max_pages = max(1, min(int(max_pages or 1), 10))
    cache_key = _tx_log_cache_key(cube, tuple_elements, top, since, until, max_pages)
    cached = _tx_log_cache_get(cache_key)
    if cached is not None:
        _log.warning("tx-log cache hit cube=%s top=%d rows=%d", cube, top, len(cached))
        return cached

    try:
        # For each tuple position, determine whether the element is a leaf or a consolidation.
        # Use the TM1 API (hierarchy object) to check the actual element type — name-only
        # heuristics miss consolidations like 'SLIM-HK' or '60303020'.
        #
        # required   = leaf elements that must ALL appear in every matching transaction
        # leaf_groups = per-consolidated-dim set of leaf descendants; at least one per group must match
        required: set[str] = set()
        leaf_groups: list[set[str]] = []
        leaf_group_filters: list[set[str]] = []
        seen_fixed: set[str] = set()
        hier_cache: dict = {}

        for e in tuple_elements:
            dim, hier, name = e["dimension"], e["hierarchy"], e["element"]
            resolved_name = _resolve_element_name(dim, name)
            lname = _norm_elem(resolved_name)
            if resolved_name != name:
                _log.warning("tx-log resolved element alias %s:%s -> %s", dim, name, resolved_name)

            # Fetch the hierarchy once per (dim, hier) pair
            hier_key = (dim, hier)
            if hier_key not in hier_cache:
                try:
                    hier_cache[hier_key] = tm1.hierarchies.get(dim, hier)
                except Exception:
                    hier_cache[hier_key] = None

            hier_obj = hier_cache[hier_key]

            # Determine consolidated status
            is_consol = _is_consolidated_element(name)  # name heuristic as fallback
            tm1_is_consol = _is_tm1_consolidated(tm1, dim, hier, name, hier_obj)
            if tm1_is_consol is not None:
                is_consol = tm1_is_consol

            if is_consol:
                leaves: set[str] = set()
                if hier_obj is not None:
                    leaves = _leaf_descendants(hier_obj, resolved_name)
                if not leaves:
                    leaves = _leaf_descendants_from_tm1(tm1, dim, hier, resolved_name)
                if not leaves:
                    leaves = _leaf_descendants_from_cache(dim, resolved_name)
                if leaves:
                    leaf_groups.append(leaves)
                    leaf_group_filters.append(_data_bearing_leaf_descendants(
                        tm1,
                        cube,
                        tuple_elements,
                        {**e, "element": resolved_name},
                        leaves,
                    ))
                    _log.warning(
                        "tx-log consolidation %s:%s:%s expanded to %d leaf descendants",
                        dim, hier, resolved_name, len(leaves),
                    )
                else:
                    _log.warning("tx-log consolidation %s:%s:%s had no leaf descendants", dim, hier, resolved_name)
            else:
                if lname not in seen_fixed:
                    seen_fixed.add(lname)
                    required.add(lname)

        # Server-side pre-filter: query several distinctive elements separately, then
        # merge locally. For consolidated cells, include leaf descendants first; TM1
        # transaction logs usually store the leaf account, not the parent caption.
        _GENERIC_ELEMS = frozenset({
            '0', '00', '000', '0000', '00000',
            'amount', 'amounts', 'value', 'values',
            'current', 'ytd', 'mtd', 'pct', 'lcy', 'usd', 'eur',
        })
        # Leaf-account groups are the most reliable server-side filters because
        # transaction logs usually store the posting leaf, not the consolidated
        # caption. Use the non-empty descendants in the current cell context so
        # broad consolidations are narrowed by data, not by an arbitrary limit.
        leaf_filter_candidates = {
            leaf
            for group in leaf_group_filters
            for leaf in group
            if leaf not in _GENERIC_ELEMS
        }
        selective = {e for e in required if e not in _GENERIC_ELEMS}
        def _sel_key(e: str):
            return (e.isdigit() and len(e) >= 5, len(e))

        leaf_filters = sorted(leaf_filter_candidates, key=_sel_key, reverse=True)
        required_filters = sorted(selective - set(leaf_filters), key=_sel_key, reverse=True)[:4]
        best_elements = leaf_filters + required_filters
        leaf_fetch_top = 500 if leaf_filters else min(500, max(100, top * 8))
        context_fetch_top = min(300, max(80, top * 4))
        fetch_top = leaf_fetch_top
        fallback_top = min(500, max(100, top * 4))

        _log.warning("tx-log required=%r  leaf_groups=%d  fetch_top=%d",
                     required, len(leaf_groups), fetch_top)
        _log.warning("tx-log time since=%s until=%s max_pages=%d", since, until, max_pages)
        _log.warning("tx-log server filters=%r", best_elements)
        entries = []
        result = []
        near_misses: list[tuple[int, list, set[str]]] = []
        seen_entry_keys: set[tuple] = set()
        seen_result_keys: set[tuple] = set()

        # Fetch cube dimension names once so we can label each position in the Tuple.
        try:
            dim_names = list(tm1.cubes.get_dimension_names(cube))
        except Exception:
            dim_names = []

        def _process_entry(item: dict):
            raw_tuple = item.get("Tuple") or []
            item_tuple_set = {_norm_elem(t) for t in raw_tuple}
            # TM1 REST may return transaction tuple members by alias/caption
            # (for example Forecast Apr for ACT, SALES for FIN), while copied
            # cell references usually carry principal element names. Compare
            # against both the displayed value and the resolved principal name.
            if dim_names:
                for i, value in enumerate(raw_tuple):
                    if i >= len(dim_names):
                        continue
                    dim = dim_names[i]
                    item_tuple_set.add(_norm_elem(_resolve_element_name(dim, str(value))))
            missing = required - item_tuple_set
            missing_leaf_groups = {
                f"leaf_group_{i + 1}"
                for i, grp in enumerate(leaf_groups)
                if not grp.intersection(item_tuple_set)
            }
            missing_all = set(missing) | missing_leaf_groups
            if missing_all:
                if len(near_misses) < 50:
                    near_misses.append((len(missing_all), item.get("Tuple") or [], missing_all))
                return
            try:
                old_val = float(item.get("OldValue") or 0)
                new_val = float(item.get("NewValue") or 0)
            except (TypeError, ValueError):
                old_val, new_val = 0.0, 0.0
            if dim_names:
                labeled = " · ".join(
                    f"{dim_names[i]}: {v}" if i < len(dim_names) else str(v)
                    for i, v in enumerate(raw_tuple)
                )
            else:
                labeled = " · ".join(str(t) for t in raw_tuple)
            row = {
                "time":    _fmt_ts(item.get("TimeStamp", "")),
                "user":    item.get("User", ""),
                "process": _tx_process_source(item),
                "tuple":   labeled,
                "from":    f"{old_val:,.0f}",
                "to":      f"{new_val:,.0f}",
            }
            sig = (row["time"], row["user"], row.get("tuple", ""), row["from"], row["to"])
            if sig not in seen_result_keys:
                seen_result_keys.add(sig)
                result.append(row)

        def _add_entries(raw_entries):
            for raw in raw_entries or []:
                if not isinstance(raw, dict):
                    continue
                key = (
                    raw.get("TimeStamp"),
                    raw.get("User"),
                    tuple(raw.get("Tuple") or []),
                    raw.get("OldValue"),
                    raw.get("NewValue"),
                )
                if key not in seen_entry_keys:
                    seen_entry_keys.add(key)
                    entries.append(raw)
                    _process_entry(raw)

        if best_elements:
            for elem in best_elements:
                elem_top = leaf_fetch_top if elem in leaf_filters else context_fetch_top
                pages = max_pages if elem in leaf_filters else 1
                for page in range(pages):
                    skip = page * elem_top
                    try:
                        page_entries = _get_tx_entries_page(
                            tm1,
                            cube=cube,
                            element_filter={elem: "eq"},
                            top=elem_top,
                            skip=skip,
                            since=since,
                            until=until,
                        )
                        _add_entries(page_entries)
                        if page > 0:
                            _log.warning(
                                "tx-log paged filter=%r page=%d returned=%d",
                                elem, page + 1, len(page_entries),
                            )
                        if len(page_entries) < elem_top:
                            break
                    except Exception as exc:
                        _log.warning("get_entries with element_tuple_filter=%r failed (%s)", elem, exc)
                        break
                    if len(result) >= top:
                        break
                if len(result) >= top:
                    break
        if not entries:
            _add_entries(_get_tx_entries_page(
                tm1,
                cube=cube,
                element_filter=None,
                top=fallback_top,
                skip=0,
                since=since,
                until=until,
            ))

        _log.warning("tx-log server returned %d entries", len(entries))
        _log.warning("tx-log after python filter: %d / %d entries kept", len(result), len(entries))
        if result:
            _log.warning("tx-log first kept tuple=%r", result[0].get("tuple", ""))
        if not result and near_misses:
            near_misses.sort(key=lambda row: row[0])
            for _miss_count, raw_tuple, missing in near_misses[:5]:
                _log.warning(
                    "tx-log near miss: missing=%r tuple=%r",
                    missing,
                    raw_tuple,
                )
        cached_result = result[:top]
        _tx_log_cache_set(cache_key, cached_result)
        return cached_result
    except Exception as exc:
        _log.warning("Transaction log query failed for cube '%s': %s", cube, exc)
        return []


def _find_consolidated_dims(tm1: TM1Service, tuple_elements: list[dict]) -> list[dict]:
    """Return tuple elements whose TM1 element is of Consolidated type."""
    result = []
    for e in tuple_elements:
        try:
            resolved = _resolve_element_name(e["dimension"], e["element"])
            el = tm1.elements.get(e["dimension"], e["hierarchy"], resolved)
            etype = str(getattr(el, "element_type", "")).lower() if el else ""
            if "consolidated" in etype:
                result.append({**e, "element": resolved})
                continue
        except Exception:
            pass
        is_consol = _is_tm1_consolidated(tm1, e["dimension"], e["hierarchy"], e["element"])
        if is_consol:
            result.append({**e, "element": _resolve_element_name(e["dimension"], e["element"])})
    return result


def _get_root_cause_leaf_combo(
    tm1: TM1Service,
    cube: str,
    actual_tuple: list[dict],
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

    # Column axis: use the numeric value/measure dimension. Account is a line-item
    # dimension in finance cubes, and putting it on both columns and rows causes
    # TM1's "dimension on more than one axis" error when drilling Account.
    measure_idx = _find_value_measure_dim_idx(actual_tuple)
    measure_e = actual_tuple[measure_idx] if measure_idx >= 0 else None

    # Expand all consolidated dimensions except the true numeric measure dimension.
    expand_info = [
        e for e in consol_elems
        if measure_e is None or e["dimension"] != measure_e["dimension"]
    ]
    if not expand_info:
        expand_info = consol_elems
    expand_names = {e["dimension"] for e in expand_info}

    def _measure_col_expr(measure_element: str | None = None) -> str:
        if not measure_e:
            return "Measures.DefaultMember"
        element = measure_element or measure_e["element"]
        return f"[{measure_e['dimension']}].[{measure_e['hierarchy']}].[{_resolve_element_name(measure_e['dimension'], element)}]"

    col_expr = _measure_col_expr()
    if measure_e:
        expand_names.add(measure_e["dimension"])

    # WHERE = fixed dims not being expanded
    where_fixed = [e for e in actual_tuple if e["dimension"] not in expand_names]

    def _leaf_set(e: dict) -> str:
        d, h, el = e["dimension"], e["hierarchy"], e["element"]
        el = _resolve_element_name(d, el)
        return f"{{Tm1FilterByLevel(Descendants([{d}].[{h}].[{el}]),0)}}"

    sets = [_leaf_set(e) for e in expand_info]
    row_expr = sets[0]
    for s in sets[1:]:
        row_expr = f"CrossJoin({row_expr}, {s})"

    n_row = len(expand_info)
    dim_labels = [e["dimension"] for e in expand_info]
    where_str = _build_where(where_fixed) if where_fixed else ""
    wc = f"WHERE ({where_str})" if where_str else ""
    def _run_root_mdx(column_expr: str) -> list[dict]:
        mdx = f"SELECT {{{column_expr}}} ON COLUMNS, NON EMPTY {{{row_expr}}} ON ROWS FROM [{cube}] {wc}"
        _log.info("root-cause MDX (%d expand dims): %.600s", n_row, mdx)

        try:
            raw = tm1.cells.execute_mdx_raw(mdx, member_properties=["Name"])
        except Exception as ex:
            _log.warning("root-cause MDX failed: %s\nMDX: %s", ex, mdx)
            return []

        axes_by_ord = {a.get("Ordinal", i): a for i, a in enumerate(raw.get("Axes", []))}
        row_tuples = axes_by_ord.get(1, {}).get("Tuples", [])
        cells_raw  = raw.get("Cells", [])
        n_cols     = max(1, len(axes_by_ord.get(0, {}).get("Tuples", [])))

        rows: list[dict] = []
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
            names = []
            for m in members:
                props = m.get("Properties", {}) if isinstance(m, dict) else {}
                prop_name = props.get("Name") if isinstance(props, dict) else ""
                name = prop_name or (m.get("Name") if isinstance(m, dict) else "") or "?"
                names.append(str(name).rsplit("[", 1)[-1].rstrip("]"))
            if len(names) < n_row:
                continue

            rows.append({
                "name":      " × ".join(names[:n_row]),
                "dimension": " × ".join(dim_labels),
                "_actual":   actual_val,
            })
        return rows

    drivers = _run_root_mdx(col_expr)

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

def explain_root_cause(
    cube: str,
    tuple_elements: list[dict],
    value: Optional[str],
    scenarios: Optional[list[str]] = None,
) -> dict:
    started = time.time()

    with TM1Service(**get_tm1_config()) as tm1:
        _log.info("root-cause dims=%s", [e["dimension"] for e in tuple_elements])

        multi_drivers, dims_used = _get_root_cause_leaf_combo(tm1, cube, tuple_elements)
        scenario_values, scenario_raw_values = _fetch_scenario_values(tm1, cube, tuple_elements, scenarios)

    scenario_idx = _find_baseline_dim_idx(tuple_elements)
    current_scenario = tuple_elements[scenario_idx]["element"] if scenario_idx >= 0 else "Current selection"

    if multi_drivers:
        top = multi_drivers[0]
        n_dims = len(dims_used)
        dim_str      = " × ".join(dims_used)
        summary = (
            f"Top <strong>{len(multi_drivers)}</strong> leaf combinations across "
            f"<strong>{n_dims}</strong> dimension{'s' if n_dims != 1 else ''} "
            f"({dim_str}), ranked by <strong>current selection</strong> value. "
            f"Largest: <strong>{top['name']}</strong> at <strong>{top['actual']}</strong>."
        )
    else:
        summary = (
            "No significant child-level drivers found. "
            "This cell may already be at a leaf level or have no non-empty children."
        )

    measure_idx = _find_value_measure_dim_idx(tuple_elements)
    measure_elem = tuple_elements[measure_idx]["element"] if measure_idx >= 0 else tuple_elements[-1]["element"]

    return {
        "action": "root_cause",
        "label": ACTION_LABELS["root_cause"],
        "current_scenario": current_scenario,
        "scenario_values": scenario_values,
        "scenario_raw_values": scenario_raw_values,
        "drill_dims": dims_used,
        "multi_drivers": multi_drivers,
        "summary": summary,
        "followups": [
            f"Show transactions for {multi_drivers[0]['name']}" if multi_drivers else "Show transaction history",
            f"Drill into {dims_used[1]} for {multi_drivers[0]['name']}" if len(dims_used) > 1 and multi_drivers else "Show values by month",
            f"Show all {measure_elem} values by month",
            "Who last changed this cell?",
        ],
        "trace": {
            "tool_calls": 1 + len(dims_used) * 2,
            "input_tokens": 0,
            "output_tokens": 0,
            "latency_ms": int((time.time() - started) * 1000),
        },
    }


def explain_tx_history(
    cube: str,
    tuple_elements: list[dict],
    tx_start: str | None = None,
    tx_end: str | None = None,
    tx_max_pages: int = 3,
) -> dict:
    started = time.time()
    since = _parse_tx_date(tx_start)
    until = _parse_tx_date(tx_end, end_of_day=True)

    with TM1Service(**get_tm1_config()) as tm1:
        transactions = _get_transaction_log(
            tm1,
            cube,
            tuple_elements,
            top=50,
            since=since,
            until=until,
            max_pages=tx_max_pages,
        )

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
