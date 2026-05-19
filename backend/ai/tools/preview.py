"""Structured preview builder for pipeline orchestration.

Consolidates dim metadata lookup, hierarchy resolution, alias/attribute
mapping, and structured preview construction into a single call so
analyze_pipeline never imports from tm1.cache or tm1.service directly
for display purposes.

Public API:
  build_cube_preview(rows, layout, forced_apply_attributes) -> PreviewResult
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ...config import PREVIEW_COLUMN_LIMIT, PREVIEW_ROW_LIMIT
from ...tm1.cache import (
    get_alias_attribute_names,
    get_alias_maps,
    get_dim_hierarchy_edges,
    get_dim_metadata,
    get_named_attribute_map,
)
from ...tm1.service import build_structured_preview

_log = logging.getLogger(__name__)


@dataclass
class PreviewResult:
    full: dict
    limited: dict
    effective_row_count: int


def build_cube_preview(
    rows: list[dict],
    layout: dict,
    forced_apply_attributes: dict[str, str] | None = None,
) -> PreviewResult:
    """Build full + limited structured previews from raw MDX rows.

    Args:
        rows: Raw MDX result rows from execute_mdx_with_repair.
        layout: Layout dict (column_dimensions, row_dimensions, transpose, ...).
        forced_apply_attributes: {dim_name: attr_name} when the user explicitly
            requested a specific attribute column (e.g. "show employee names").
            Pass None/empty to use default alias columns.

    Returns:
        PreviewResult with .full, .limited, and .effective_row_count.
    """
    col_dims: list[str] = list(layout.get("column_dimensions") or [])
    row_dims: list[str] = list(layout.get("row_dimensions") or [])
    all_dims = list({d for d in (col_dims + row_dims) if d})

    dim_metadata: dict = {}
    if all_dims:
        try:
            dim_metadata = get_dim_metadata(all_dims)
        except Exception as exc:
            _log.debug("dim metadata lookup failed: %s", exc)

    hierarchy_edges: dict = {}
    if row_dims:
        try:
            hierarchy_edges = get_dim_hierarchy_edges(row_dims)
        except Exception as exc:
            _log.debug("row hierarchy lookup failed: %s", exc)

    alias_maps, col_names = _resolve_display_attributes(
        forced_apply_attributes or {}, row_dims
    )

    full = build_structured_preview(
        rows, layout,
        limit=len(rows),
        dim_metadata=dim_metadata,
        alias_maps=alias_maps,
        apply_attributes=col_names or None,
        hierarchy_edges=hierarchy_edges,
    )

    preview_cols = (
        full["columns"][:PREVIEW_COLUMN_LIMIT]
        if full.get("transpose")
        else full["columns"]
    )
    limited = {
        **full,
        "rows": full["rows"][:PREVIEW_ROW_LIMIT],
        "columns": preview_cols,
    }
    effective_row_count = (
        len(full["columns"]) if full.get("transpose") else len(full["rows"])
    )

    return PreviewResult(full=full, limited=limited, effective_row_count=effective_row_count)


def _resolve_display_attributes(
    forced_apply_attributes: dict[str, str],
    row_dims: list[str],
) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    try:
        if forced_apply_attributes:
            am: dict[str, dict[str, str]] = {}
            col_names: dict[str, str] = {}
            for dim_name, attr_name in forced_apply_attributes.items():
                attr_map = get_named_attribute_map(dim_name, attr_name)
                if attr_map:
                    am[dim_name] = attr_map
                    col_names[dim_name] = attr_name
        else:
            am = get_alias_maps(row_dims) if row_dims else {}
            attr_names = get_alias_attribute_names(list(am.keys())) if am else {}
            col_names = {d: attr_names.get(d, f"{d} Name") for d in am}
    except Exception as exc:
        _log.debug("alias/attribute resolution failed: %s", exc)
        am, col_names = {}, {}
    return am, col_names
