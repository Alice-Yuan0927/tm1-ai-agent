"""MDX execution and AI-driven repair loop."""

import logging
from dataclasses import dataclass, field
from typing import cast

from ..ai.mdx.context import MdxContext
from ..ai.mdx.generation import repair_cube_mdx
from ..ai.mdx.normalize import validate_generated_mdx
from ..ai.schema.result_validators import (
    account_dim_choice_issue as _validate_account_dim_choice,
    default_filter_warning as _validate_default_filter_warning,
    entity_filter_issue as _validate_entity_filter,
    result_shape_issue as _validate_result_shape,
    specific_focus_issue as _validate_specific_focus,
    statement_line_item_issue as _validate_statement_line_items,
    static_mdx_schema_issue as _validate_static_mdx_schema,
)
from ..config import MAX_MDX_ATTEMPTS
from ..tm1.cache import get_dim_hierarchy_edges, get_dim_metadata
from ..tm1.service import execute_generated_mdx

_log = logging.getLogger(__name__)

_CONNECTION_ERROR_PATTERNS = (
    "connection refused",
    "failed to establish",
    "max retries exceeded",
    "name or service not known",
    "nodename nor servname",
    "timed out",
    "timeout",
    "unauthorized",
    "authentication",
    "certificate",
    "ssl",
)


@dataclass
class _ValidationOutcome:
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class _ExecutionAttempt:
    rows: list[dict[str, object]] = field(default_factory=list)
    layout: dict[str, object] = field(default_factory=dict)
    error: str | None = None
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None


def is_connection_error(message: str) -> bool:
    text = message.lower()
    return any(pattern in text for pattern in _CONNECTION_ERROR_PATTERNS)


def _result_shape_issue(rows: list[dict[str, object]], layout: dict[str, object]) -> str | None:
    if not rows:
        return None
    column_dims = cast(list[str], layout.get("column_dimensions") or [])
    if not column_dims:
        return None
    try:
        dim_meta = get_dim_metadata(column_dims)
    except Exception as exc:
        _log.debug("dim metadata lookup failed: %s", exc)
        dim_meta = {}
    try:
        hierarchy_edges = get_dim_hierarchy_edges(column_dims)
    except Exception as exc:
        _log.debug("hierarchy edges lookup failed: %s", exc)
        hierarchy_edges = {}
    return _validate_result_shape(rows, layout, dim_meta, hierarchy_edges)


def _pre_execution_validation(ctx: MdxContext, mdx: str) -> _ValidationOutcome:
    """Validate an MDX statement before it hits TM1."""
    validate_generated_mdx(mdx, ctx.cube_name)

    static_issue = _validate_static_mdx_schema(mdx, ctx.cube_schema)
    if static_issue:
        return _ValidationOutcome(error=static_issue)

    entity_issue = _validate_entity_filter(
        ctx.question, ctx.cube_schema, mdx, model_profile=ctx.model_profile
    )
    if entity_issue:
        return _ValidationOutcome(error=entity_issue)

    warnings: list[str] = []
    default_warning = _validate_default_filter_warning(
        ctx.question, ctx.cube_schema, mdx, model_profile=ctx.model_profile
    )
    if default_warning:
        warnings.append(default_warning)

    account_choice_issue = _validate_account_dim_choice(ctx.question, ctx.cube_schema, mdx)
    if account_choice_issue:
        return _ValidationOutcome(error=account_choice_issue, warnings=warnings)

    return _ValidationOutcome(warnings=warnings)


def _post_execution_issue(
    ctx: MdxContext,
    rows: list[dict[str, object]],
    layout: dict[str, object],
) -> str | None:
    """Validate TM1 result shape and business focus after execution."""
    shape_issue = _result_shape_issue(rows, layout)
    if shape_issue:
        return shape_issue

    focus_issue = _validate_specific_focus(ctx.question, ctx.cube_schema, rows, layout)
    if focus_issue:
        return focus_issue

    return _validate_statement_line_items(
        ctx.question, ctx.cube_schema, layout, model_profile=ctx.model_profile
    )


def _execute_attempt(ctx: MdxContext, mdx: str) -> _ExecutionAttempt:
    """Run one validate -> execute -> validate pass."""
    pre = _pre_execution_validation(ctx, mdx)
    if pre.error:
        return _ExecutionAttempt(error=pre.error, warnings=pre.warnings)

    rows, layout = execute_generated_mdx(ctx.cube_name, mdx)
    post_issue = _post_execution_issue(ctx, rows, layout)
    if post_issue:
        return _ExecutionAttempt(
            rows=rows,
            layout=layout,
            error=post_issue,
            warnings=pre.warnings,
        )

    return _ExecutionAttempt(rows=rows, layout=layout, warnings=pre.warnings)


def execute_once(
    ctx: MdxContext,
    mdx: str,
) -> tuple[list[dict], dict, list[str], str | None]:
    """Execute MDX once — no repair loop, no LLM calls.

    Returns (rows, layout, warnings, error).
    error is None on success. Connection errors are re-raised as RuntimeError.
    Used by the agentic loop so the LLM can inspect the outcome and retry itself.
    """
    try:
        result = _execute_attempt(ctx, mdx)
        return list(result.rows), dict(result.layout), list(result.warnings), result.error
    except RuntimeError as exc:
        err = str(exc)
        if is_connection_error(err):
            raise
        return [], {}, [], err


def execute_mdx_with_repair(
    ctx: MdxContext,
    mdx: str,
) -> tuple[list[dict[str, object]], dict[str, object], str, list[str]]:
    """Execute generated MDX, asking the AI to repair it after TM1 errors."""
    attempts: list[str] = []
    current_mdx = mdx
    last_error = ""

    for attempt in range(1, MAX_MDX_ATTEMPTS + 1):
        try:
            result = _execute_attempt(ctx, current_mdx)
        except RuntimeError as exc:
            last_error = str(exc)
        else:
            attempts.extend(result.warnings)
            if result.ok:
                return result.rows, result.layout, current_mdx, attempts
            last_error = result.error or "MDX execution failed"

        attempts.append(f"attempt {attempt}: {last_error}")
        if is_connection_error(last_error):
            raise RuntimeError(last_error)
        if attempt >= MAX_MDX_ATTEMPTS:
            break
        try:
            current_mdx = repair_cube_mdx(ctx, current_mdx, last_error)
        except RuntimeError as repair_exc:
            attempts.append(f"repair {attempt}: {repair_exc}")
            break

    raise RuntimeError(last_error or "MDX execution failed")
