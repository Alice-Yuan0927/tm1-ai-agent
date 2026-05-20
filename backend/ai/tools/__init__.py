"""TM1 tools for the MDX generation agent and pipeline orchestration.

Note: execution.py is intentionally NOT re-exported here because
      generation.py imports this __init__, and execution.py imports
      services.mdx_execution which imports generation.py — circular.
      Import execution tools directly: from backend.ai.tools.execution import ...
"""

from .element_search import find_element_candidates, resolve_members
from .element_tools import ELEMENT_REGISTRY
from .registry import ToolRegistry, ToolSpec
from .preview import PreviewResult, build_cube_preview
from .rag_tools import get_similar_queries, record_query

__all__ = [
    # Tool registry
    "ELEMENT_REGISTRY",
    "ToolRegistry",
    "ToolSpec",
    # Pipeline tools (return typed Python objects)
    "find_element_candidates",
    "resolve_members",
    "build_cube_preview",
    "PreviewResult",
    "get_similar_queries",
    "record_query",
]
