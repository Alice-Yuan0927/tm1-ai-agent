"""POST /api/explain-cell — Cell Explainer Arc plugin endpoint."""

import logging
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from ..services.cell_explain_service import explain_root_cause, explain_tx_history

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
    try:
        from TM1py import TM1Service
        from ..config import get_tm1_config
        from ..services.cell_explain_service import _BASELINE_DIM_KEYWORDS

        with TM1Service(**get_tm1_config()) as tm1:
            dim_names = list(tm1.cubes.get_dimension_names(cube))
            scenario_dim = dimension
            if not scenario_dim:
                for d in dim_names:
                    if any(kw in d.lower() for kw in _BASELINE_DIM_KEYWORDS):
                        scenario_dim = d
                        break
            if not scenario_dim:
                return {"dimension": "", "elements": []}
            elements = list(tm1.elements.get_element_names(scenario_dim, scenario_dim))
            leaves = []
            for e in elements:
                try:
                    el = tm1.elements.get(scenario_dim, scenario_dim, e)
                    if str(getattr(el, "element_type", "")).lower() != "consolidated":
                        leaves.append(e)
                except Exception:
                    leaves.append(e)
            return {"dimension": scenario_dim, "elements": leaves[:30]}
    except Exception as exc:
        _log.warning("cube-scenarios failed: %s", exc)
        return {"dimension": "", "elements": []}


@router.get("/api/cube-info")
async def cube_info(cube: str):
    """Return cube dimensions + recent transaction activity (no element filter)."""
    try:
        from TM1py import TM1Service
        from ..config import get_tm1_config
        from ..services.cell_explain_service import _fmt_ts

        with TM1Service(**get_tm1_config()) as tm1:
            dimensions = list(tm1.cubes.get_dimension_names(cube))
            raw = tm1.transaction_logs.get_entries(cube=cube, top=20) or []
            transactions = []
            for item in raw:
                if not isinstance(item, dict):
                    continue
                try:
                    old_val = float(item.get("OldValue") or 0)
                    new_val = float(item.get("NewValue") or 0)
                except (TypeError, ValueError):
                    old_val, new_val = 0.0, 0.0
                transactions.append({
                    "time":    _fmt_ts(item.get("TimeStamp", "")),
                    "user":    item.get("User", ""),
                    "tuple":   " · ".join(str(t) for t in (item.get("Tuple") or [])),
                    "from":    f"{old_val:,.0f}",
                    "to":      f"{new_val:,.0f}",
                })
        return {"cube": cube, "dimensions": dimensions, "transactions": transactions}
    except Exception as exc:
        _log.warning("cube-info failed: %s", exc)
        return {"cube": cube, "dimensions": [], "transactions": []}


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
                "trace": {"tool_calls": 0, "input_tokens": 0, "output_tokens": 0, "latency_ms": 0},
            }
        raise HTTPException(status_code=400, detail=f"Unknown action: {req.action}")
    except RuntimeError as exc:
        _log.error("explain-cell TM1 error: %s", exc)
        raise HTTPException(status_code=502, detail=str(exc))
    except Exception as exc:
        _log.exception("explain-cell unexpected error")
        raise HTTPException(status_code=500, detail=str(exc))
