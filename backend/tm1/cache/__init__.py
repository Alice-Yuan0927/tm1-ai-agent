"""SQLite schema cache for TM1 cube / dimension / element metadata.

Sub-modules:
    db              connection + DDL + migrations + is_empty
    sync            sync_schema (pull from TM1, replace cache)
    defaults        default-element picker + Sys Parameter cube probe
    read            cube_schema_cached, alias / attribute / metadata readers
    member_search   FTS-backed question-to-member matching

This package re-exports the public surface used by upstream modules so existing
imports (`from backend.tm1.cache import sync_schema`) keep working.
"""

from .db import connect, init_schema_db, is_empty
from .defaults import get_current_period_defaults
from .read import (
    element_exists,
    get_alias_attribute_names,
    get_alias_maps,
    get_cube_schema_cached,
    get_cubes_cached,
    get_dim_hierarchy_edges,
    get_dim_metadata,
    get_last_synced_at,
    get_named_attribute_map,
    is_consolidated_element,
    lookup_element_dim,
)
from .member_search import find_question_element_matches, resolve_question_members
from .sync import sync_schema

__all__ = [
    "connect",
    "init_schema_db",
    "is_empty",
    "get_current_period_defaults",
    "element_exists",
    "find_question_element_matches",
    "get_alias_attribute_names",
    "get_alias_maps",
    "get_cube_schema_cached",
    "get_cubes_cached",
    "get_dim_hierarchy_edges",
    "get_dim_metadata",
    "get_last_synced_at",
    "get_named_attribute_map",
    "is_consolidated_element",
    "lookup_element_dim",
    "resolve_question_members",
    "sync_schema",
]
