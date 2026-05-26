"""POST /api/explain-cell - Cell Explainer Arc plugin endpoint."""

import logging
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services.cell_explain_service import (
    explain_root_cause,
    explain_tx_history,
    get_cube_info,
    list_cube_scenarios,
)

_log = logging.getLogger(__name__)
router = APIRouter()


class TupleElement(BaseModel):
    dimension: str
    hierarchy: str
    element: str


class ExplainCellRequest(BaseModel):
    instance: str
    cube: str
    tuple: list[TupleElement]
    value: Optional[str] = None
    action: Literal["tx_history", "root_cause", "followup"]
    scenarios: Optional[list[str]] = None
    question: Optional[str] = None
    tx_start: Optional[str] = None
    tx_end: Optional[str] = None
    tx_max_pages: Optional[int] = 3


@router.get("/api/cube-scenarios")
async def cube_scenarios(cube: str, dimension: str = ""):
    """Return leaf elements of the scenario/version dimension for compare cards."""
    return list_cube_scenarios(cube, dimension)


@router.get("/api/cube-info")
async def cube_info(cube: str):
    """Return cube dimensions + recent transaction activity."""
    return get_cube_info(cube)


@router.post("/api/explain-cell")
async def explain_cell(req: ExplainCellRequest):
    elements = [e.model_dump() for e in req.tuple]
    _log.info("explain-cell dims: %s", [e["dimension"] for e in elements])
    try:
        if req.action == "root_cause":
            return explain_root_cause(req.cube, elements, req.value, req.scenarios)
        if req.action == "tx_history":
            return explain_tx_history(
                req.cube,
                elements,
                tx_start=req.tx_start,
                tx_end=req.tx_end,
                tx_max_pages=req.tx_max_pages or 3,
            )
        if req.action == "followup":
            return {
                "action": "followup",
                "label": "FOLLOW-UP",
                "summary": f"<em>{req.question}</em>",
                "trace": {
                    "tool_calls": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "latency_ms": 0,
                },
            }
        raise HTTPException(status_code=400, detail=f"Unknown action: {req.action}")
    except RuntimeError as exc:
        _log.error("explain-cell TM1 error: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        _log.exception("explain-cell unexpected error")
        raise HTTPException(status_code=500, detail=str(exc)) from exc
