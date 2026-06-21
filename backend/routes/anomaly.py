"""HTTP edge for the anomaly module.

Skeleton only — Phase 1. The endpoints exist and return well-typed
empty/ok responses so the frontend can wire up its mode switch and view
router. Real scan execution and Teams push land in later phases.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..anomaly import memory as _memory
from ..anomaly.rules.loader import (
    delete_rules,
    list_cubes_with_rules,
    load_rules,
    save_rules,
)
from ..anomaly.rules.schema import RuleSet
from ..tm1.cache import (
    get_cube_relationship_stats_cached,
    get_cube_relationships_cached,
    get_process_cube_links_cached,
)
from ..tm1.service import get_cube_schema, get_cubes_with_descriptions

router = APIRouter(prefix="/api/anomaly", tags=["anomaly"])

_log = logging.getLogger(__name__)


# ── Rule CRUD ───────────────────────────────────────────────────────────────


class RuleListResponse(BaseModel):
    cubes: list[str] = Field(default_factory=list)


class AnomalyCubeDimension(BaseModel):
    name: str
    is_measure: bool = False
    is_time_dim: bool = False
    element_count: int = 0


class AnomalyCubeSummary(BaseModel):
    cube: str
    description: str = ""
    dimensions: list[AnomalyCubeDimension] = Field(default_factory=list)
    rules: dict = Field(default_factory=dict)


class AnomalyCubeRelationship(BaseModel):
    from_cube: str = Field(alias="from")
    to_cube: str = Field(alias="to")
    type: str
    source: str = ""
    snippet: str = ""


class AnomalyProcessCubeLink(BaseModel):
    process: str
    cube: str
    role: str
    datasource_type: str = ""
    object: str = ""
    snippet: str = ""


class AnomalyCubeListResponse(BaseModel):
    count: int
    cubes: list[AnomalyCubeSummary] = Field(default_factory=list)
    relationships: list[AnomalyCubeRelationship] = Field(default_factory=list)
    process_links: list[AnomalyProcessCubeLink] = Field(default_factory=list)


@router.get("/rules", response_model=RuleListResponse)
def list_rules():
    return RuleListResponse(cubes=list_cubes_with_rules())


@router.get("/cubes", response_model=AnomalyCubeListResponse)
def list_schema_cubes():
    """Return cubes from the synced schema cache with anomaly rule metadata."""
    try:
        cube_rows = get_cubes_with_descriptions()
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    result: list[AnomalyCubeSummary] = []
    for row in cube_rows:
        cube_name = str(row.get("cube") or "").strip()
        if not cube_name:
            continue
        try:
            schema = get_cube_schema(cube_name)
        except RuntimeError as exc:
            _log.warning("[anomaly] could not read schema for %s: %s", cube_name, exc)
            schema = {"dimensions": []}
        dimensions = [
            AnomalyCubeDimension(
                name=str(dim.get("name") or ""),
                is_measure=bool(dim.get("is_measure")),
                is_time_dim=bool(dim.get("is_time_dim")),
                element_count=len(dim.get("elements") or []),
            )
            for dim in (schema.get("dimensions") or [])
            if dim.get("name")
        ]
        rules = load_rules(cube_name)
        result.append(AnomalyCubeSummary(
            cube=cube_name,
            description=str(row.get("description") or cube_name),
            dimensions=dimensions,
            rules=rules.model_dump(mode="json"),
        ))
    return AnomalyCubeListResponse(
        count=len(result),
        cubes=result,
        relationships=[
            AnomalyCubeRelationship.model_validate(rel)
            for rel in get_cube_relationships_cached()
        ],
        process_links=[
            AnomalyProcessCubeLink.model_validate(link)
            for link in get_process_cube_links_cached()
        ],
    )


@router.get("/relationships")
def list_relationships():
    relationships = get_cube_relationships_cached()
    process_links = get_process_cube_links_cached()
    return {
        "count": len(relationships),
        "stats": get_cube_relationship_stats_cached(),
        "relationships": relationships,
        "process_link_count": len(process_links),
        "process_links": process_links,
    }


@router.get("/rules/{cube}")
def get_rules(cube: str):
    rules = load_rules(cube)
    return rules.model_dump(mode="json")


@router.put("/rules/{cube}")
def put_rules(cube: str, payload: dict):
    payload = {**payload, "cube": cube}
    try:
        rules = RuleSet.model_validate(payload)
    except Exception as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    save_rules(rules)
    return {"ok": True, "cube": cube}


@router.delete("/rules/{cube}")
def del_rules(cube: str):
    deleted = delete_rules(cube)
    return {"ok": True, "deleted": deleted}


@router.get("/rules/{cube}/reference-elements")
def get_reference_elements(cube: str):
    """Return leaf elements from Scenario/Version-like dimensions for use in rule reference fields."""
    try:
        schema = get_cube_schema(cube)
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    ref_keywords = ("scenario", "version", "vers")
    dims = []
    for dim in (schema.get("dimensions") or []):
        name_lower = (dim.get("name") or "").lower()
        if any(kw in name_lower for kw in ref_keywords):
            elements = [e for e in (dim.get("elements") or []) if not str(e).startswith("}")]
            if elements:
                dims.append({"dim": dim["name"], "elements": elements})
    return {"dims": dims}


# ── Stubs for later phases ──────────────────────────────────────────────────


@router.post("/rules/{cube}/suggest")
def suggest_rules(cube: str):
    """Phase 4: LLM drafts a RuleSet from cube schema. Stubbed for now."""
    return {
        "ok": False,
        "stub": True,
        "message": "LLM rule suggestion not implemented yet (Phase 4).",
    }


@router.post("/scan/{cube}")
def run_scan(cube: str):
    """Phase 3: execute MDX, run detector, return result_id. Stubbed."""
    return {
        "ok": False,
        "stub": True,
        "message": "Scan execution not implemented yet (Phase 3).",
    }


@router.get("/scans")
def list_scans():
    return {"scans": []}


@router.post("/dismiss/{cube}")
def dismiss_flag(cube: str, payload: dict):
    """Persist a dismissal so the same flag is suppressed next run."""
    dedupe_id = str(payload.get("dedupe_id", "")).strip()
    if not dedupe_id:
        raise HTTPException(status_code=400, detail="dedupe_id required")
    # Minimal Flag-shaped object — only dedupe_id matters for storage.
    from ..anomaly.flag import Flag, Layer
    f = Flag(layer=Layer.VARIANCE, rule="manual", key={}, reason="")
    # Override dedupe_id by setting rule + key_str via constructed key string.
    # Simpler: use the raw id we got from the client.
    _memory.dismiss(cube, _ManualFlag(dedupe_id), note=str(payload.get("note", "")))
    return {"ok": True}


class _ManualFlag:
    """Tiny shim so we can persist a dedupe_id without constructing a real Flag."""
    def __init__(self, dedupe_id: str) -> None:
        self.dedupe_id = dedupe_id


@router.post("/push/teams/{cube}")
def push_to_teams(cube: str, payload: dict):
    """Phase 5: post to a Teams Incoming Webhook. Stubbed."""
    return {
        "ok": False,
        "stub": True,
        "message": "Teams push not implemented yet (Phase 5).",
    }
