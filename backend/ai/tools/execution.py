"""MDX execution tool for pipeline orchestration.

Wraps execute_mdx_with_repair so analyze_pipeline never imports
from services.mdx_execution directly.
"""

from __future__ import annotations

import logging

from ...ai.mdx.context import MdxContext
from ...services import tm1_health as _health_svc
from ...services.mdx_execution import execute_mdx_with_repair
from ...services.mdx_execution import is_connection_error as _is_connection_error

_log = logging.getLogger(__name__)

# Re-export so callers only need this module.
is_connection_error = _is_connection_error


def run_mdx(
    ctx: MdxContext,
    mdx: str,
) -> tuple[list[dict], dict, str, list[str]] | None:
    """Execute MDX with automatic repair loop.

    Returns (rows, layout, final_mdx, attempts) on success.
    Returns None on connection error (health is updated to 'down').
    Raises RuntimeError for non-connection execution failures.
    """
    try:
        rows, layout, final_mdx, attempts = execute_mdx_with_repair(ctx, mdx)
        _health_svc.update("up")
        return rows, layout, final_mdx, attempts
    except RuntimeError as exc:
        if _is_connection_error(str(exc)):
            _health_svc.update("down")
        raise
