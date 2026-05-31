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

router = APIRouter(prefix="/api/anomaly", tags=["anomaly"])

_log = logging.getLogger(__name__)


# ── Rule CRUD ───────────────────────────────────────────────────────────────


class RuleListResponse(BaseModel):
    cubes: list[str] = Field(default_factory=list)


@router.get("/rules", response_model=RuleListResponse)
def list_rules():
    return RuleListResponse(cubes=list_cubes_with_rules())


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
