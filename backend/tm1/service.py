import re

from TM1py import TM1Service

from ..config import MAX_DATA_ROWS, TRANSPOSE_COLS, TM1_CONFIG


def _member_name(unique_name: object) -> str:
    text = str(unique_name)
    if "[" in text and text.endswith("]"):
        return text.rsplit("[", 1)[-1][:-1]
    return text


def _dimension_name(unique_name: object, fallback: str) -> str:
    text = str(unique_name)
    if text.startswith("[") and "]." in text:
        return text.split("]", 1)[0][1:]
    return fallback


def _format_cellset_rows(cellset: dict, dimension_names: list[str] | None = None) -> list[dict]:
    rows = []
    dimension_names = dimension_names or []
    for coords, cell in cellset.items():
        value = cell.get("Value") if isinstance(cell, dict) else cell
        if value is None:
            continue

        row = {}
        dimensions = {}
        for index, coord in enumerate(coords):
            dimension = dimension_names[index] if index < len(dimension_names) else _dimension_name(coord, f"dim{index}")
            element = _member_name(coord)
            row[dimension] = element
            dimensions[dimension] = element
        row["value"] = value
        row["_dimensions"] = dimensions
        rows.append(row)
        if len(rows) >= MAX_DATA_ROWS:
            break
    return rows


def _guess_measure_dimension(rows: list[dict]) -> str | None:
    if not rows:
        return None

    dimensions = list(rows[0].get("_dimensions", {}).keys())
    for dimension in dimensions:
        lowered = dimension.lower()
        if lowered.startswith("m ") or "measure" in lowered or ("cost" in lowered and lowered.startswith("m")):
            return dimension

    varying = [
        dimension for dimension in dimensions
        if len({row["_dimensions"].get(dimension) for row in rows}) > 1
    ]
    return varying[-1] if varying else (dimensions[-1] if dimensions else None)


def _extract_axis_expression(mdx: str, axis: str) -> str:
    pattern = rf"SELECT\s+(.*?)\s+ON\s+{axis}"
    if axis.upper() == "ROWS":
        pattern = r"ON\s+COLUMNS\s*,\s*(.*?)\s+ON\s+ROWS"
    match = re.search(pattern, mdx, flags=re.IGNORECASE | re.DOTALL)
    return match.group(1) if match else ""


def _extract_dimensions(expression: str) -> list[str]:
    dimensions = []
    for dimension in re.findall(r"\[([^\]]+)\]\.\[[^\]]+\]", expression):
        if dimension not in dimensions:
            dimensions.append(dimension)
    return dimensions


def _extract_where_filters(mdx: str) -> list[dict]:
    match = re.search(r"\bWHERE\s*\((.*)\)\s*$", mdx, flags=re.IGNORECASE | re.DOTALL)
    if not match:
        return []

    filters = []
    for dimension, _hierarchy, element in re.findall(r"\[([^\]]+)\]\.\[([^\]]+)\]\.\[([^\]]+)\]", match.group(1)):
        filters.append({"dimension": dimension, "element": element})
    return filters


def get_view_layout_from_mdx(mdx: str) -> dict[str, object]:
    columns_expr = _extract_axis_expression(mdx, "COLUMNS")
    rows_expr = _extract_axis_expression(mdx, "ROWS")
    return {
        "mdx": mdx,
        "column_dimensions": _extract_dimensions(columns_expr),
        "row_dimensions": _extract_dimensions(rows_expr),
        "filters": _extract_where_filters(mdx),
        "applied_filters": [],
    }


def _build_display_rows(
    pivot_rows: "list[dict]",
    row_dimensions: "list[str]",
    am: "dict[str, dict[str, str]]",
    apply_attributes: "dict[str, str] | None",
    limit: int,
) -> "tuple[list[str], list[dict]]":
    """
    Return (augmented_row_dims, processed_rows).

    For each row dimension that has entries in am, the original element ID is kept
    and a new column is inserted immediately after it containing the attribute value.
    The new column name comes from apply_attributes[dim_name] (e.g. "Employee Name").
    """
    aug_dims: list[str] = []
    for d in row_dimensions:
        aug_dims.append(d)
        if d in am and apply_attributes and d in apply_attributes:
            aug_dims.append(apply_attributes[d])

    processed: list[dict] = []
    for prow in list(pivot_rows)[:limit]:
        row = dict(prow)
        for d in row_dimensions:
            if d in am and apply_attributes and d in apply_attributes:
                attr_col = apply_attributes[d]
                row[attr_col] = am[d].get(str(row.get(d, "")), "")
        processed.append(row)

    return aug_dims, processed


def build_structured_preview(
    rows: list[dict],
    layout: dict | None = None,
    limit: int = 30,
    dim_metadata: "dict[str, dict] | None" = None,
    alias_maps: "dict[str, dict[str, str]] | None" = None,
    apply_attributes: "dict[str, str] | None" = None,
) -> dict:
    """
    dim_metadata     : {dim_name: {"is_time_dim": bool, "consolidated": set[str]}}
    alias_maps       : {dim_name: {element_name: attr_value}} — attribute values to display
    apply_attributes : {dim_name: col_name} — column header for the new attribute column;
                       must be provided alongside alias_maps for the column to appear.
                       Original element IDs are always preserved; the attribute value is
                       inserted as a new column immediately to the right of its dimension.
    """
    if not rows:
        return {
            "filters": [], "row_dimensions": [], "measure_dimension": "",
            "columns": [], "rows": [],
            "consolidated_columns": [], "column_dim_is_time": False,
            "transpose": False,
        }

    dm = dim_metadata or {}
    am = alias_maps or {}

    dimensions = list(rows[0].get("_dimensions", {}).keys())
    unique_by_dimension = {
        dimension: list(dict.fromkeys(row["_dimensions"].get(dimension, "") for row in rows))
        for dimension in dimensions
    }
    layout = layout or {}
    mdx_row_dimensions = [d for d in layout.get("row_dimensions", []) if d in dimensions]
    mdx_column_dimensions = [d for d in layout.get("column_dimensions", []) if d in dimensions]

    if mdx_column_dimensions:
        filter_dimensions = {item["dimension"] for item in layout.get("filters", [])}
        filters = [
            item for item in layout.get("filters", [])
            if item["dimension"] in dimensions
        ]
        for dimension, values in unique_by_dimension.items():
            if dimension in filter_dimensions or dimension in mdx_row_dimensions or dimension in mdx_column_dimensions:
                continue
            if len(values) == 1:
                filters.append({"dimension": dimension, "element": values[0]})

        row_dimensions = mdx_row_dimensions
        columns = []
        pivot = {}
        for row in rows:
            row_key = tuple(row["_dimensions"].get(d, "") for d in row_dimensions)
            target = pivot.setdefault(
                row_key,
                {d: row["_dimensions"].get(d, "") for d in row_dimensions},
            )
            column = " ".join(
                row["_dimensions"].get(d, "") for d in mdx_column_dimensions
            ).strip() or "Value"
            if column not in columns:
                columns.append(column)
            target[column] = row["value"]

        # Collect all consolidated element names across column dimensions
        cons_set: set[str] = set()
        for d in mdx_column_dimensions:
            cons_set |= dm.get(d, {}).get("consolidated", set())
        consolidated_columns = [c for c in columns if c in cons_set]

        col_dim_is_time = any(dm.get(d, {}).get("is_time_dim", False) for d in mdx_column_dimensions)

        display_row_dims, aliased_rows = _build_display_rows(
            list(pivot.values()), row_dimensions, am, apply_attributes, limit
        )

        return {
            "filters": filters,
            "row_dimensions": display_row_dims,
            "column_dimensions": mdx_column_dimensions,
            "measure_dimension": " / ".join(mdx_column_dimensions) if mdx_column_dimensions else "Value",
            "columns": columns,
            "rows": aliased_rows,
            "consolidated_columns": consolidated_columns,
            "column_dim_is_time": col_dim_is_time,
            "transpose": not col_dim_is_time and len(columns) > TRANSPOSE_COLS,
        }

    measure_dimension = _guess_measure_dimension(rows)

    filters = [
        {"dimension": dimension, "element": values[0]}
        for dimension, values in unique_by_dimension.items()
        if dimension != measure_dimension and len(values) == 1
    ]
    row_dimensions = [
        d for d in dimensions
        if d != measure_dimension and len(unique_by_dimension.get(d, [])) > 1
    ]
    if not row_dimensions:
        row_dimensions = [
            d for d in dimensions
            if d != measure_dimension and d not in {item["dimension"] for item in filters}
        ]

    columns = unique_by_dimension.get(measure_dimension, []) if measure_dimension else ["Value"]
    pivot = {}
    for row in rows:
        row_key = tuple(row["_dimensions"].get(d, "") for d in row_dimensions)
        target = pivot.setdefault(
            row_key,
            {d: row["_dimensions"].get(d, "") for d in row_dimensions},
        )
        column = row["_dimensions"].get(measure_dimension, "Value") if measure_dimension else "Value"
        target[column] = row["value"]

    # Measure elements can also be consolidated (e.g. "Total Cost" = sum of sub-measures)
    cons_set = dm.get(measure_dimension, {}).get("consolidated", set()) if measure_dimension else set()
    consolidated_columns = [c for c in columns if c in cons_set]
    col_dim_is_time = dm.get(measure_dimension, {}).get("is_time_dim", False) if measure_dimension else False

    display_row_dims, aliased_rows = _build_display_rows(
        list(pivot.values()), row_dimensions, am, apply_attributes, limit
    )

    return {
        "filters": filters,
        "row_dimensions": display_row_dims,
        "measure_dimension": measure_dimension or "Value",
        "columns": columns,
        "rows": aliased_rows,
        "consolidated_columns": consolidated_columns,
        "column_dim_is_time": col_dim_is_time,
        "transpose": not col_dim_is_time and len(columns) > TRANSPOSE_COLS,
    }


def get_cube_schema(cube_name: str) -> dict:
    """Return cube structure for AI MDX generation.

    Cache path: reads from SQLite (fast, complete element lists, no TM1 connection).
    Live fallback: used only before the first sync-schema call.
    """
    from .cache import get_cube_schema_cached, is_empty as _cache_is_empty

    if not _cache_is_empty():
        cached = get_cube_schema_cached(cube_name)
        if cached:
            return cached

    # --- live TM1 fallback (pre-sync only) ---
    try:
        with TM1Service(**TM1_CONFIG) as tm1:
            try:
                dim_names = tm1.cubes.get_dimension_names(cube_name)
                measure_dim = tm1.cubes.get_measure_dimension(cube_name)
            except Exception as exc:
                raise RuntimeError(f"Cannot get dimensions for {cube_name}: {exc}") from exc

            dimensions = []
            for dim_name in dim_names:
                try:
                    elements = list(tm1.elements.get_element_names(dim_name, dim_name))
                except Exception:
                    elements = []
                dimensions.append({
                    "name": dim_name,
                    "is_measure": dim_name == measure_dim,
                    "usage": "",
                    "elements": elements[:60],
                })

            return {"cube": cube_name, "dimensions": dimensions}

    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Cannot get schema for {cube_name}: {exc}") from exc


def execute_generated_mdx(cube_name: str, mdx: str) -> tuple[list[dict[str, object]], dict[str, object]]:
    """Execute an AI-generated MDX query and return formatted rows + layout."""
    try:
        with TM1Service(**TM1_CONFIG) as tm1:
            try:
                dimension_names = tm1.cubes.get_dimension_names(cube_name)
            except Exception:
                dimension_names = []
            layout = get_view_layout_from_mdx(mdx)
            cellset = tm1.cells.execute_mdx(mdx, skip_zeros=True)
    except Exception as exc:
        address = TM1_CONFIG.get("address")
        port = TM1_CONFIG.get("port")
        raise RuntimeError(f"Cannot execute MDX at {address}:{port}: {exc}") from exc

    return _format_cellset_rows(cellset, dimension_names), layout


def get_cubes_with_descriptions() -> list[dict]:
    """Return cube metadata for AI source selection.

    Cache path  : reads from SQLite schema cache (instant, no TM1 connection).
    Live path   : get_attribute_of_elements for Description, falls back to
                  cube names only — all avoiding MDX coordinate ambiguity.
    System cubes (} or Sys prefix) are always excluded.
    """
    from .cache import get_cubes_cached, is_empty as _cache_is_empty

    if not _cache_is_empty():
        return get_cubes_cached()

    try:
        with TM1Service(**TM1_CONFIG) as tm1:
            result = []

            # Primary: element attribute API
            try:
                attr_map = tm1.elements.get_attribute_of_elements(
                    "}Cubes", "}Cubes", "Description"
                ) or {}
                result = [
                    {"cube": cube, "description": str(desc).strip()}
                    for cube, desc in attr_map.items()
                    if desc and str(desc).strip()
                    and not cube.startswith("}") and not cube.startswith("Sys")
                ]
            except Exception:
                pass

            # Fallback: cube names only
            if not result:
                try:
                    all_names = tm1.cubes.get_all_names(skip_control_cubes=True)
                    result = [
                        {"cube": name, "description": name}
                        for name in (all_names or [])
                        if not name.startswith("}") and not name.startswith("Sys")
                    ]
                except Exception:
                    pass

    except Exception as exc:
        address = TM1_CONFIG.get("address")
        port = TM1_CONFIG.get("port")
        raise RuntimeError(
            f"Cannot connect to TM1 at {address}:{port}: {exc}"
        ) from exc

    return result
