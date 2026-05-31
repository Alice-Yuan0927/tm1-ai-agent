"""Pull cubes / dimensions / elements from TM1 and write to SQLite."""

import logging
import re

from TM1py import TM1Service
from TM1py.Utils import format_url

from ...config import get_tm1_config
from .db import connect, mark_cache_scope

_log = logging.getLogger(__name__)


# ── Time-dimension detection helpers ──────────────────────────────────────────

_TIME_DIM_NAME = re.compile(
    r"\b(period|month|calendar|fiscal|year|quarter|week|date|time)\b", re.I
)
_TIME_ELEM_RE = [
    re.compile(r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)", re.I),
    re.compile(r"^(january|february|march|april|may|june|july|august"
               r"|september|october|november|december)", re.I),
    re.compile(r"^(0[1-9]|1[0-2])$"),
    re.compile(r"^q[1-4](\b|$)", re.I),
    re.compile(r"^(19|20)\d{2}$"),
    re.compile(r"^(fy|cy)\d{2,4}$", re.I),
    re.compile(r"^(period|month|quarter|week|wk)\s*\d+", re.I),
]

_DB_REF_RE = re.compile(r"\bDB\s*\(\s*['\"]([^'\"]+)['\"]", re.I)
_CELL_GET_CUBE_ARG_RE = re.compile(
    r"\bCellGet[NS]\s*\(\s*(?:'([^']+)'|\"([^\"]+)\"|([A-Za-z_]\w*))",
    re.I,
)
_CELL_WRITE_CUBE_ARG_RE = re.compile(
    r"\b(?:CellPut[NS]|CellIncrementN)\s*\("
    r"\s*(?:[^,()]|\([^)]*\)|'[^']*'|\"[^\"]*\")+\s*,\s*"
    r"(?:'([^']+)'|\"([^\"]+)\"|([A-Za-z_]\w*))",
    re.I,
)
_STRING_ASSIGN_RE = re.compile(
    r"^\s*([A-Za-z_]\w*)\s*=\s*(?:'([^']+)'|\"([^\"]+)\")\s*;?",
    re.I | re.M,
)
_PROCESS_DATASOURCE_CUBE_TYPES = {"tm1cubeview"}


def _detect_time_dim(dim_name: str, leaf_names: list[str]) -> bool:
    if _TIME_DIM_NAME.search(dim_name):
        return True
    if not leaf_names:
        return False
    matches = sum(
        1 for n in leaf_names
        if any(p.match(str(n).strip()) for p in _TIME_ELEM_RE)
    )
    return matches >= len(leaf_names) * 0.5


# ── TM1py enum normalisation ──────────────────────────────────────────────────

def _attr_type_value(raw) -> str:
    if raw is None:
        return "String"
    if hasattr(raw, "value"):
        return str(raw.value)
    s = str(raw)
    for t in ("Numeric", "Alias", "String"):
        if t.lower() in s.lower():
            return t
    return "String"


def _elem_type_value(raw) -> str:
    """TM1py ElementTypes -> 'Numeric' / 'String' / 'Consolidated'."""
    if raw is None:
        return "Numeric"
    name = getattr(raw, "name", None)
    if name:
        candidate = str(name).strip().lower()
        for t in ("Consolidated", "String", "Numeric"):
            if t.lower() == candidate:
                return t
    s = str(raw).strip()
    code_map = {"1": "Numeric", "2": "String", "3": "Consolidated"}
    if s in code_map:
        return code_map[s]
    for t in ("Consolidated", "String", "Numeric"):
        if t.lower() in s.lower():
            return t
    return "Numeric"


def _component_name(component) -> str:
    for attr in ("element_name", "name", "component_name"):
        value = getattr(component, attr, None)
        if value:
            return str(value)
    if isinstance(component, dict):
        for key in ("element_name", "name", "component_name"):
            if component.get(key):
                return str(component[key])
    return str(component or "")


def _component_weight(component) -> float:
    for attr in ("weight", "factor"):
        value = getattr(component, attr, None)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                return 1.0
    if isinstance(component, dict):
        for key in ("weight", "factor"):
            if component.get(key) is not None:
                try:
                    return float(component[key])
                except (TypeError, ValueError):
                    return 1.0
    return 1.0


def _fetch_dim_edges(tm1, dim_name: str) -> list[tuple[str, str, float]]:
    """Fall-back hierarchy fetch when `element.components` came back empty.

    TM1py versions vary in whether they populate `components` directly; try a
    few alternative APIs in order of cost.
    """
    elements_ns = getattr(tm1, "elements", None)
    if elements_ns is None:
        return []

    get_edges = getattr(elements_ns, "get_edges", None)
    if callable(get_edges):
        try:
            raw = get_edges(dim_name, dim_name)
            if isinstance(raw, dict):
                return [
                    (str(p), str(c), float(w) if w is not None else 1.0)
                    for (p, c), w in raw.items()
                    if p and c
                ]
            if raw:
                edges: list[tuple[str, str, float]] = []
                for item in raw:
                    if isinstance(item, (tuple, list)) and len(item) >= 2:
                        parent, child, *rest = item
                        weight = float(rest[0]) if rest and rest[0] is not None else 1.0
                        edges.append((str(parent), str(child), weight))
                if edges:
                    return edges
        except Exception as exc:
            _log.debug("get_edges() failed for %s: %s", dim_name, exc)

    try:
        hierarchies = tm1.dimensions.hierarchies.get(dim_name, dim_name)
        edges_obj = getattr(hierarchies, "edges", None)
        if edges_obj and isinstance(edges_obj, dict):
            return [
                (str(p), str(c), float(w) if w is not None else 1.0)
                for (p, c), w in edges_obj.items()
                if p and c
            ]
    except Exception as exc:
        _log.debug("hierarchies.get() failed for %s: %s", dim_name, exc)

    try:
        cons_names = elements_ns.get_consolidated_element_names(dim_name, dim_name)
    except Exception:
        cons_names = []
    edges: list[tuple[str, str, float]] = []
    for parent in cons_names or []:
        try:
            children = elements_ns.get_members_under_consolidation(dim_name, dim_name, parent, max_depth=1)
            for c in children or []:
                child = str(getattr(c, "name", c))
                if child and child != parent:
                    edges.append((str(parent), child, 1.0))
        except Exception:
            continue
    return edges


def _object_text(obj) -> str:
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, (list, tuple, set)):
        return "\n".join(_object_text(item) for item in obj)
    if isinstance(obj, dict):
        return "\n".join(_object_text(v) for v in obj.values())
    parts: list[str] = []
    for attr in (
        "prolog_procedure", "metadata_procedure", "data_procedure", "epilog_procedure",
        "rules", "rule", "feeders", "feeder", "text", "definition", "ui_data", "body",
    ):
        value = getattr(obj, attr, None)
        if value:
            parts.append(_object_text(value))
    return "\n".join(parts) if parts else str(obj)


def _rule_parts(rule_obj) -> tuple[str, str]:
    rules = _object_text(getattr(rule_obj, "rules", None) or getattr(rule_obj, "rule", None))
    feeders = _object_text(getattr(rule_obj, "feeders", None) or getattr(rule_obj, "feeder", None))
    full = _object_text(rule_obj)
    if not rules and full:
        marker = re.search(r"\bFEEDERS\s*;", full, re.I)
        if marker:
            rules = full[:marker.start()]
            feeders = feeders or full[marker.end():]
        else:
            rules = full
    return rules, feeders


def _snippet_for_cube(text: str, cube: str) -> str:
    for line in text.splitlines():
        if cube.lower() in line.lower():
            return line.strip()[:240]
    return ""


def _canonical_refs(candidates: list[str], cube_lookup: dict[str, str]) -> set[str]:
    refs: set[str] = set()
    for candidate in candidates:
        key = str(candidate or "").strip().lower()
        if key in cube_lookup:
            refs.add(cube_lookup[key])
    return refs


def _literal_assignments(text: str) -> dict[str, str]:
    assignments: dict[str, str] = {}
    for var_name, single, double in _STRING_ASSIGN_RE.findall(text):
        value = single or double
        if var_name and value:
            assignments[var_name.lower()] = value
    return assignments


def _call_cube_refs(matches: list[tuple[str, ...]], cube_lookup: dict[str, str], assignments: dict[str, str]) -> set[str]:
    candidates: list[str] = []
    for match in matches:
        value = next((part for part in match if part), "")
        if not value:
            continue
        candidates.append(assignments.get(value.lower(), value))
    return _canonical_refs(candidates, cube_lookup)


def _process_datasource_cubes(process, cube_lookup: dict[str, str]) -> set[str]:
    raw = getattr(process, "datasource_type", None)
    if not raw:
        return set()
    # str(SomeEnum.MEMBER) returns "SomeEnum.MEMBER" on all Python versions for plain
    # Enum — take the last segment after "." to get just "TM1CUBEVIEW".
    raw_str = str(getattr(raw, "name", None) or raw).strip()
    datasource_type = raw_str.split(".")[-1].lower()
    if datasource_type not in _PROCESS_DATASOURCE_CUBE_TYPES:
        return set()

    candidates = [
        getattr(process, "datasource_data_source_name_for_server", ""),
        getattr(process, "datasource_data_source_name_for_client", ""),
        getattr(process, "datasource_name_for_server", ""),
        getattr(process, "datasource_name_for_client", ""),
    ]
    return _canonical_refs(candidates, cube_lookup)


def _process_cube_link_rows(
    process_name: str,
    process,
    text: str,
    source_cubes: set[str],
    target_cubes: set[str],
    datasource_cubes: set[str],
) -> list[tuple[str, str, str, str, str, str]]:
    datasource_type = str(getattr(process, "datasource_type", "") or "")
    view_name = str(getattr(process, "datasource_view", "") or "")
    rows: list[tuple[str, str, str, str, str, str]] = []
    for cube in sorted(source_cubes):
        object_name = view_name if cube in datasource_cubes else ""
        snippet = (
            f"DataSource {datasource_type}: {cube}"
            + (f" / {view_name}" if object_name else "")
        ) if cube in datasource_cubes else _snippet_for_cube(text, cube)
        rows.append((process_name, cube, "source", datasource_type, object_name, snippet))
    for cube in sorted(target_cubes):
        rows.append((process_name, cube, "target", datasource_type, "", _snippet_for_cube(text, cube)))
    return rows


def _fetch_rule_relationships(tm1, cube_names: list[str]) -> list[tuple[str, str, str, str, str]]:
    relationships: list[tuple[str, str, str, str, str]] = []
    cube_lookup = {cube.lower(): cube for cube in cube_names}
    rest = getattr(tm1, "_tm1_rest", None)
    if rest is None:
        return relationships
    for cube_name in cube_names:
        try:
            response = rest.GET(format_url("/Cubes('{}')?$select=Name,Rules", cube_name))
            rules_text = str((response.json() or {}).get("Rules") or "")
        except Exception as exc:
            _log.debug("Rule fetch failed for %s: %s", cube_name, exc)
            continue
        if not rules_text.strip():
            continue
        rules_text, feeders_text = _rule_parts(rules_text)
        for source_cube in sorted(_canonical_refs(_DB_REF_RE.findall(rules_text), cube_lookup)):
            if source_cube != cube_name:
                relationships.append((
                    source_cube,
                    cube_name,
                    "rule",
                    cube_name,
                    _snippet_for_cube(rules_text, source_cube),
                ))
        for target_cube in sorted(_canonical_refs(_DB_REF_RE.findall(feeders_text), cube_lookup)):
            if target_cube != cube_name:
                relationships.append((
                    cube_name,
                    target_cube,
                    "feeder",
                    cube_name,
                    _snippet_for_cube(feeders_text, target_cube),
                ))
    return relationships


def _fetch_process_relationships(
    tm1,
    cube_names: list[str],
) -> tuple[list[tuple[str, str, str, str, str]], list[tuple[str, str, str, str, str, str]]]:
    relationships: list[tuple[str, str, str, str, str]] = []
    process_links: list[tuple[str, str, str, str, str, str]] = []
    cube_lookup = {cube.lower(): cube for cube in cube_names}
    processes_ns = getattr(tm1, "processes", None)
    get_all_names = getattr(processes_ns, "get_all_names", None)
    get_process = getattr(processes_ns, "get", None)
    if not callable(get_all_names) or not callable(get_process):
        return relationships, process_links
    try:
        process_names = get_all_names() or []
    except Exception as exc:
        _log.warning("Process list fetch failed — process relationships skipped: %s", exc)
        return relationships, process_links
    for process_name in process_names:
        try:
            process = get_process(process_name)
        except Exception as exc:
            _log.warning("Process fetch failed for %s: %s", process_name, exc)
            continue
        text = _object_text(process)
        assignments = _literal_assignments(text)
        source_cubes = _call_cube_refs(_CELL_GET_CUBE_ARG_RE.findall(text), cube_lookup, assignments)
        target_cubes = _call_cube_refs(_CELL_WRITE_CUBE_ARG_RE.findall(text), cube_lookup, assignments)
        datasource_cubes = _process_datasource_cubes(process, cube_lookup)
        source_cubes.update(datasource_cubes)
        process_links.extend(_process_cube_link_rows(
            str(process_name),
            process,
            text,
            source_cubes,
            target_cubes,
            datasource_cubes,
        ))
        if not source_cubes or not target_cubes:
            continue
        for source_cube in sorted(source_cubes):
            for target_cube in sorted(target_cubes):
                if source_cube == target_cube:
                    continue
                relationships.append((
                    source_cube,
                    target_cube,
                    "process",
                    str(process_name),
                    _snippet_for_cube(text, target_cube) or _snippet_for_cube(text, source_cube),
                ))
    _log.info(
        "[sync] processes scanned: %d  →  process_links: %d  cube-to-cube edges: %d",
        len(process_names), len(process_links), len(relationships),
    )
    return relationships, process_links


# ── The big sync ──────────────────────────────────────────────────────────────

def sync_schema() -> dict:
    """Pull all cubes / dims / elements from TM1 and replace the SQLite cache."""
    with TM1Service(**get_tm1_config()) as tm1:
        all_names = tm1.cubes.get_all_names(skip_control_cubes=True) or []
        cube_names = [
            n for n in all_names
            if not n.startswith("}") and not n.startswith("Sys")
        ]

        try:
            desc_map = (
                tm1.elements.get_attribute_of_elements("}Cubes", "}Cubes", "Description")
                or {}
            )
        except Exception as exc:
            _log.debug("Cube Description attr lookup failed: %s", exc)
            desc_map = {}

        cube_dims: dict[str, list[str]] = {}
        cube_measure: dict[str, str] = {}
        for cube_name in cube_names:
            try:
                cube_dims[cube_name] = tm1.cubes.get_dimension_names(cube_name)
                cube_measure[cube_name] = tm1.cubes.get_measure_dimension(cube_name)
            except Exception as exc:
                _log.debug("Skipping cube %s during dim lookup: %s", cube_name, exc)
                continue

        unique_dims = {dim for dims in cube_dims.values() for dim in dims}

        dim_elements: dict[str, list[tuple[str, str]]] = {}
        dim_edges:    dict[str, list[tuple[str, str, float]]] = {}
        dim_is_time:  dict[str, bool] = {}
        dim_attrs:    dict[str, list[tuple[str, str]]] = {}

        for dim_name in unique_dims:
            try:
                elems = tm1.elements.get_elements(dim_name, dim_name)
                pairs: list[tuple[str, str]] = [
                    (e.name, _elem_type_value(e.element_type)) for e in elems
                ]
                edges: list[tuple[str, str, float]] = []
                for element in elems:
                    if _elem_type_value(getattr(element, "element_type", None)) != "Consolidated":
                        continue
                    parent = str(getattr(element, "name", "") or "")
                    for component in getattr(element, "components", []) or []:
                        child = _component_name(component)
                        if child:
                            edges.append((parent, child, _component_weight(component)))
                if not edges:
                    edges = _fetch_dim_edges(tm1, dim_name)
                leaf_names = [name for name, etype in pairs if etype != "Consolidated"]
                dim_elements[dim_name] = pairs
                dim_edges[dim_name] = edges
                dim_is_time[dim_name] = _detect_time_dim(dim_name, leaf_names)
            except Exception as exc:
                _log.debug("Falling back to names-only for dim %s: %s", dim_name, exc)
                try:
                    names = list(tm1.elements.get_element_names(dim_name, dim_name))
                    dim_elements[dim_name] = [(n, "Numeric") for n in names]
                    dim_edges[dim_name] = _fetch_dim_edges(tm1, dim_name)
                    dim_is_time[dim_name] = _detect_time_dim(dim_name, names)
                except Exception as inner_exc:
                    _log.warning("Could not enumerate dim %s: %s", dim_name, inner_exc)
                    dim_elements[dim_name] = []
                    dim_edges[dim_name] = []
                    dim_is_time[dim_name] = False

            try:
                attr_objs = tm1.elements.get_element_attributes(dim_name, dim_name)
                dim_attrs[dim_name] = [
                    (a.name, _attr_type_value(getattr(a, "attribute_type", None)))
                    for a in attr_objs
                ]
            except Exception as exc:
                _log.debug("No attributes for dim %s: %s", dim_name, exc)
                dim_attrs[dim_name] = []

        # Attribute values (Alias + String). Stored so the AI can request any
        # attribute by name (e.g. "Employee Name") without a new TM1 round trip.
        dim_alias_values: dict[str, dict[str, str]] = {}
        dim_all_attr_values: dict[str, dict[str, dict[str, str]]] = {}
        for dim_name in unique_dims:
            for attr_name, atype in dim_attrs.get(dim_name, []):
                if atype not in ("Alias", "String"):
                    continue
                try:
                    raw = tm1.elements.get_attribute_of_elements(
                        dim_name, dim_name, attr_name
                    ) or {}
                    values = {k: str(v).strip() for k, v in raw.items() if v and str(v).strip()}
                    if not values:
                        continue
                    dim_all_attr_values.setdefault(dim_name, {})[attr_name] = values
                    if atype == "Alias" and dim_name not in dim_alias_values:
                        dim_alias_values[dim_name] = values
                except Exception as exc:
                    _log.debug("Attr %s.%s unreadable: %s", dim_name, attr_name, exc)

        cube_relationships: list[tuple[str, str, str, str, str]] = []
        process_cube_links: list[tuple[str, str, str, str, str, str]] = []
        try:
            cube_relationships.extend(_fetch_rule_relationships(tm1, cube_names))
        except Exception as exc:
            _log.warning("Cube rule relationship extraction skipped: %s", exc)
        try:
            process_relationships, process_links = _fetch_process_relationships(tm1, cube_names)
            cube_relationships.extend(process_relationships)
            process_cube_links.extend(process_links)
        except Exception as exc:
            _log.warning("Cube process relationship extraction skipped: %s", exc)

    conn = connect()
    with conn:
        conn.execute("DELETE FROM process_cube_links")
        conn.execute("DELETE FROM cube_relationships")
        conn.execute("DELETE FROM cube_summaries")
        conn.execute("DELETE FROM element_attribute_values")
        conn.execute("DELETE FROM element_embeddings")
        conn.execute("DELETE FROM member_search")
        conn.execute("DELETE FROM element_aliases")
        conn.execute("DELETE FROM element_edges")
        conn.execute("DELETE FROM dim_attributes")
        conn.execute("DELETE FROM elements")
        conn.execute("DELETE FROM dim_in_cube")
        conn.execute("DELETE FROM cubes")

        for cube_name, dims in cube_dims.items():
            desc    = str(desc_map.get(cube_name, "")).strip()
            measure = cube_measure.get(cube_name, "")
            conn.execute(
                "INSERT INTO cubes(name, description, measure_dim) VALUES (?, ?, ?)",
                (cube_name, desc, measure),
            )
            for pos, dim_name in enumerate(dims):
                conn.execute(
                    "INSERT INTO dim_in_cube"
                    "(cube_name, dim_name, position, is_measure, is_time_dim)"
                    " VALUES (?, ?, ?, ?, ?)",
                    (
                        cube_name, dim_name, pos,
                        1 if dim_name == measure else 0,
                        1 if dim_is_time.get(dim_name, False) else 0,
                    ),
                )

        total_elems = 0
        for dim_name, pairs in dim_elements.items():
            if pairs:
                conn.executemany(
                    "INSERT OR IGNORE INTO elements(dim_name, element_name, element_type)"
                    " VALUES (?, ?, ?)",
                    [(dim_name, name, etype) for name, etype in pairs],
                )
                total_elems += len(pairs)

        total_edges = 0
        for dim_name, edges in dim_edges.items():
            if edges:
                conn.executemany(
                    "INSERT OR IGNORE INTO element_edges(dim_name, parent_name, child_name, weight)"
                    " VALUES (?, ?, ?, ?)",
                    [(dim_name, parent, child, weight) for parent, child, weight in edges],
                )
                total_edges += len(edges)

        for dim_name, attrs in dim_attrs.items():
            if attrs:
                conn.executemany(
                    "INSERT OR IGNORE INTO dim_attributes(dim_name, attribute_name, attribute_type)"
                    " VALUES (?, ?, ?)",
                    [(dim_name, name, atype) for name, atype in attrs],
                )

        total_aliases = 0
        for dim_name, alias_map in dim_alias_values.items():
            if alias_map:
                conn.executemany(
                    "INSERT OR IGNORE INTO element_aliases(dim_name, element_name, alias_value)"
                    " VALUES (?, ?, ?)",
                    [(dim_name, elem, alias) for elem, alias in alias_map.items()],
                )
                total_aliases += len(alias_map)

        total_attr_vals = 0
        for dim_name, attr_dict in dim_all_attr_values.items():
            for attr_name, elem_vals in attr_dict.items():
                if elem_vals:
                    conn.executemany(
                        "INSERT OR IGNORE INTO element_attribute_values"
                        "(dim_name, element_name, attr_name, attr_value) VALUES (?, ?, ?, ?)",
                        [(dim_name, elem, attr_name, val) for elem, val in elem_vals.items()],
                    )
                    total_attr_vals += len(elem_vals)

        member_docs: list[tuple[str, str, str]] = []
        for dim_name, pairs in dim_elements.items():
            attr_by_elem: dict[str, list[str]] = {}
            for attr_name, elem_vals in dim_all_attr_values.get(dim_name, {}).items():
                for elem, val in elem_vals.items():
                    attr_by_elem.setdefault(elem, []).append(f"{attr_name} {val}")
            alias_map = dim_alias_values.get(dim_name, {})
            for elem, _etype in pairs:
                parts = [str(elem)]
                if alias_map.get(elem):
                    parts.append(str(alias_map[elem]))
                parts.extend(attr_by_elem.get(elem, []))
                member_docs.append((dim_name, elem, " ".join(parts)))
        if member_docs:
            conn.executemany(
                "INSERT INTO member_search(dim_name, element_name, searchable_text)"
                " VALUES (?, ?, ?)",
                member_docs,
            )
        if cube_relationships:
            conn.executemany(
                "INSERT OR IGNORE INTO cube_relationships"
                "(from_cube, to_cube, relationship_type, source_name, snippet)"
                " VALUES (?, ?, ?, ?, ?)",
                cube_relationships,
            )
        if process_cube_links:
            conn.executemany(
                "INSERT OR IGNORE INTO process_cube_links"
                "(process_name, cube_name, role, datasource_type, object_name, snippet)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                process_cube_links,
            )
        mark_cache_scope(conn)

    return {
        "cubes": len(cube_dims),
        "dims": len(unique_dims),
        "elements": total_elems,
        "element_edges": total_edges,
        "cube_relationships": len(cube_relationships),
        "process_cube_links": len(process_cube_links),
        "aliases": total_aliases,
        "attribute_values": total_attr_vals,
    }
