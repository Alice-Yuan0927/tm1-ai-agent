from TM1py import TM1Service

from .config import APQ_CUBE, APQ_VIEW, MAX_DATA_ROWS, TM1_CONFIG


def _member_name(unique_name: object) -> str:
    text = str(unique_name)
    if "[" in text and text.endswith("]"):
        return text.rsplit("[", 1)[-1][:-1]
    return text


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


def execute_view_safe(cube_name: str, view_name: str) -> list[dict]:
    """Execute a TM1 view and return non-zero rows formatted for Claude."""
    try:
        with TM1Service(**TM1_CONFIG) as tm1:
            cellset = tm1.cells.execute_view(
                cube_name=cube_name,
                view_name=view_name,
                private=False,
            )
    except Exception as exc:
        address = TM1_CONFIG.get("address")
        port = TM1_CONFIG.get("port")
        raise RuntimeError(f"Cannot connect to TM1 at {address}:{port} or execute view: {exc}") from exc

    rows = []
    for coords, cell in cellset.items():
        value = cell.get("Value") if isinstance(cell, dict) else cell
        if value is None or value == 0:
            continue
        row = {f"dim{i}": elem for i, elem in enumerate(coords)}
        row["value"] = value
        rows.append(row)
        if len(rows) >= MAX_DATA_ROWS:
            break
    return rows
