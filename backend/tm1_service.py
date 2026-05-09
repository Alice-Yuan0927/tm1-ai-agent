import re

from TM1py import TM1Service

from .config import APQ_CUBE, APQ_VIEW, MAX_DATA_ROWS, TM1_CONFIG


def _member_name(unique_name: object) -> str:
    text = str(unique_name)
    if "[" in text and text.endswith("]"):
        return text.rsplit("[", 1)[-1][:-1]
    return text


def _normalize_token(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _acronym(value: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", value)
    return "".join(word[0] for word in words if word).lower()


def _question_acronyms(question: str) -> set[str]:
    acronyms = set()
    for match in re.findall(r"\b[A-Za-z](?:\s*[&/.-]\s*[A-Za-z])+\b", question):
        acronyms.add(re.sub(r"[^A-Za-z0-9]+", "", match).lower())
    for match in re.findall(r"\b[A-Z]{2,}\b", question):
        acronyms.add(match.lower())
    return acronyms


def _member_unique_name(dimension: str, element: str) -> str:
    safe_element = element.replace("]", "]]")
    return f"[{dimension}].[{dimension}].[{safe_element}]"


def _singleton_set(dimension: str, element: str) -> str:
    return "{" + _member_unique_name(dimension, element) + "}"


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
        if lowered.startswith("m ") or "measure" in lowered or "cost" in lowered and lowered.startswith("m"):
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


def _replace_dimension_set(expression: str, dimension: str, element: str) -> tuple[str, bool]:
    singleton = _singleton_set(dimension, element)
    escaped_dimension = re.escape(dimension)
    patterns = [
        rf"TM1SubsetToSet\(\s*\[{escaped_dimension}\]\.\[{escaped_dimension}\]\s*,\s*\"[^\"]+\"\s*\)",
        rf"\{{[^{{}}]*\[{escaped_dimension}\]\.\[{escaped_dimension}\][^{{}}]*\}}",
    ]

    for pattern in patterns:
        new_expression, count = re.subn(pattern, singleton, expression, count=1, flags=re.IGNORECASE | re.DOTALL)
        if count:
            return new_expression, True
    return expression, False


def _replace_where_member(mdx: str, dimension: str, element: str) -> tuple[str, bool]:
    singleton = _member_unique_name(dimension, element)
    escaped_dimension = re.escape(dimension)
    pattern = rf"\[{escaped_dimension}\]\.\[{escaped_dimension}\]\.\[[^\]]+\]"
    new_mdx, count = re.subn(pattern, singleton, mdx, count=1, flags=re.IGNORECASE)
    return new_mdx, bool(count)


def apply_mdx_filters(mdx: str, filters: list[dict]) -> str:
    if not filters:
        return mdx

    filtered_mdx = mdx
    for item in filters:
        dimension = item.get("dimension", "").strip()
        element = item.get("element", "").strip()
        if not dimension or not element:
            continue

        columns_expr = _extract_axis_expression(filtered_mdx, "COLUMNS")
        rows_expr = _extract_axis_expression(filtered_mdx, "ROWS")

        if dimension in _extract_dimensions(columns_expr):
            new_expr, replaced = _replace_dimension_set(columns_expr, dimension, element)
            if replaced:
                filtered_mdx = filtered_mdx.replace(columns_expr, new_expr, 1)
                continue

        if dimension in _extract_dimensions(rows_expr):
            new_expr, replaced = _replace_dimension_set(rows_expr, dimension, element)
            if replaced:
                filtered_mdx = filtered_mdx.replace(rows_expr, new_expr, 1)
                continue

        filtered_mdx, _ = _replace_where_member(filtered_mdx, dimension, element)

    return filtered_mdx


def get_view_layout(tm1: TM1Service, cube_name: str, view_name: str) -> dict:
    try:
        view = tm1.views.get_mdx_view(cube_name, view_name, private=False)
        mdx = getattr(view, "MDX", "") or getattr(view, "mdx", "")
    except Exception:
        return {}

    columns_expr = _extract_axis_expression(mdx, "COLUMNS")
    rows_expr = _extract_axis_expression(mdx, "ROWS")
    return {
        "mdx": mdx,
        "column_dimensions": _extract_dimensions(columns_expr),
        "row_dimensions": _extract_dimensions(rows_expr),
        "filters": _extract_where_filters(mdx),
    }


def get_view_layout_from_mdx(mdx: str, applied_filters: list[dict] | None = None) -> dict:
    columns_expr = _extract_axis_expression(mdx, "COLUMNS")
    rows_expr = _extract_axis_expression(mdx, "ROWS")
    filters = _extract_where_filters(mdx)
    for item in applied_filters or []:
        if not any(existing["dimension"] == item["dimension"] for existing in filters):
            filters.append({"dimension": item["dimension"], "element": item["element"]})
    return {
        "mdx": mdx,
        "column_dimensions": _extract_dimensions(columns_expr),
        "row_dimensions": _extract_dimensions(rows_expr),
        "filters": filters,
        "applied_filters": applied_filters or [],
    }


def find_question_filters(tm1: TM1Service, cube_name: str, question: str, dimensions: list[str]) -> list[dict]:
    question_norm = _normalize_token(question)
    explicit_acronyms = _question_acronyms(question)
    filters = []

    for dimension in dimensions:
        try:
            elements = tm1.elements.get_element_names(dimension, dimension)
        except Exception:
            continue

        best = None
        for element in elements:
            if str(element).lower().startswith("all "):
                continue
            element_norm = _normalize_token(element)
            element_acronym = _acronym(element)
            if not element_norm:
                continue

            matched = element_norm in question_norm
            if not matched and element_acronym and len(element_acronym) >= 2:
                matched = element_acronym in explicit_acronyms

            if matched:
                score = len(element_norm)
                if element_acronym and element_acronym in explicit_acronyms:
                    score += 25
                if not best or score > best[0]:
                    best = (score, element)

        if best:
            filters.append({"dimension": dimension, "element": best[1], "source": "question"})

    return filters


def build_structured_preview(rows: list[dict], layout: dict | None = None, limit: int = 30) -> dict:
    if not rows:
        return {"filters": [], "row_dimensions": [], "measure_dimension": "", "columns": [], "rows": []}

    dimensions = list(rows[0].get("_dimensions", {}).keys())
    unique_by_dimension = {
        dimension: list(dict.fromkeys(row["_dimensions"].get(dimension, "") for row in rows))
        for dimension in dimensions
    }
    layout = layout or {}
    mdx_row_dimensions = [dimension for dimension in layout.get("row_dimensions", []) if dimension in dimensions]
    mdx_column_dimensions = [dimension for dimension in layout.get("column_dimensions", []) if dimension in dimensions]

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
            row_key = tuple(row["_dimensions"].get(dimension, "") for dimension in row_dimensions)
            target = pivot.setdefault(row_key, {dimension: row["_dimensions"].get(dimension, "") for dimension in row_dimensions})
            column = " ".join(row["_dimensions"].get(dimension, "") for dimension in mdx_column_dimensions).strip() or "Value"
            if column not in columns:
                columns.append(column)
            target[column] = row["value"]

        return {
            "filters": filters,
            "row_dimensions": row_dimensions,
            "column_dimensions": mdx_column_dimensions,
            "measure_dimension": "Value",
            "columns": columns,
            "rows": list(pivot.values())[:limit],
        }

    measure_dimension = _guess_measure_dimension(rows)

    filters = [
        {"dimension": dimension, "element": values[0]}
        for dimension, values in unique_by_dimension.items()
        if dimension != measure_dimension and len(values) == 1
    ]
    row_dimensions = [
        dimension for dimension in dimensions
        if dimension != measure_dimension and len(unique_by_dimension.get(dimension, [])) > 1
    ]
    if not row_dimensions:
        row_dimensions = [
            dimension for dimension in dimensions
            if dimension != measure_dimension and dimension not in {item["dimension"] for item in filters}
        ]

    columns = unique_by_dimension.get(measure_dimension, []) if measure_dimension else ["Value"]
    pivot = {}
    for row in rows:
        row_key = tuple(row["_dimensions"].get(dimension, "") for dimension in row_dimensions)
        target = pivot.setdefault(row_key, {dimension: row["_dimensions"].get(dimension, "") for dimension in row_dimensions})
        column = row["_dimensions"].get(measure_dimension, "Value") if measure_dimension else "Value"
        target[column] = row["value"]

    return {
        "filters": filters,
        "row_dimensions": row_dimensions,
        "measure_dimension": measure_dimension or "Value",
        "columns": columns,
        "rows": list(pivot.values())[:limit],
    }


def get_views_from_apq() -> list[dict]:
    """
    Read }APQ Cube Views and return view metadata used by the AI selector.

    Only rows with a description are included. System cubes are excluded.
    """
    try:
        with TM1Service(**TM1_CONFIG) as tm1:
            cellset = tm1.cells.execute_view(
                cube_name=APQ_CUBE,
                view_name=APQ_VIEW,
                private=False,
            )
    except Exception as exc:
        address = TM1_CONFIG.get("address")
        port = TM1_CONFIG.get("port")
        raise RuntimeError(f"Cannot connect to TM1 at {address}:{port} or read {APQ_CUBE}: {exc}") from exc

    view_data: dict[str, dict] = {}
    for coords, cell in cellset.items():
        if not isinstance(coords, (list, tuple)) or len(coords) < 2:
            continue
        view_key = _member_name(coords[0])
        measure = _member_name(coords[-1])
        raw = cell.get("Value") if isinstance(cell, dict) else cell
        value = str(raw).strip() if raw is not None else ""
        view_data.setdefault(view_key, {})[measure] = value

    views = []
    seen = set()
    for view_key, data in view_data.items():
        cube = data.get("Cube Name", "").strip()
        view = data.get("View Name", "").strip()
        desc = data.get("Description", "").strip()
        if (not cube or not view) and "\\" in view_key:
            cube, view = [part.strip() for part in view_key.split("\\", 1)]
        if cube and view.startswith(f"{cube}:"):
            view = view.split(":", 1)[1].strip()
        if not (cube and view and desc):
            continue
        if cube.startswith("}") or cube.startswith("Sys"):
            continue
        key = (cube, view)
        if key in seen:
            continue
        seen.add(key)
        views.append({"cube": cube, "view": view, "description": desc})

    return views


def execute_view_safe(cube_name: str, view_name: str, question: str = "") -> tuple[list[dict], dict]:
    """Execute a TM1 view and return non-zero rows formatted for Claude."""
    try:
        with TM1Service(**TM1_CONFIG) as tm1:
            layout = get_view_layout(tm1, cube_name, view_name)
            dimension_names = []
            try:
                cube = tm1.cubes.get(cube_name)
                dimension_names = [str(dimension) for dimension in (getattr(cube, "dimensions", []) or [])]
            except Exception:
                dimension_names = []
            question_filters = find_question_filters(tm1, cube_name, question, dimension_names)
            layout["applied_filters"] = question_filters

            mdx = layout.get("mdx")
            if mdx and question_filters:
                mdx = apply_mdx_filters(mdx, question_filters)
                layout = get_view_layout_from_mdx(mdx, applied_filters=question_filters)
                cellset = tm1.cells.execute_mdx(mdx, skip_zeros=False)
            else:
                cellset = tm1.cells.execute_view(
                    cube_name=cube_name,
                    view_name=view_name,
                    private=False,
                    skip_zeros=False,
                )
    except Exception as exc:
        address = TM1_CONFIG.get("address")
        port = TM1_CONFIG.get("port")
        raise RuntimeError(f"Cannot connect to TM1 at {address}:{port} or execute view: {exc}") from exc

    return _format_cellset_rows(cellset, dimension_names), layout
