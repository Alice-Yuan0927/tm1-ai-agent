import re
from typing import Any

from fastapi import APIRouter, Body, HTTPException
from fastapi.responses import Response

from ..excel_service import build_excel

router = APIRouter()


@router.post("/api/export-excel")
def export_excel(payload: dict[str, Any] = Body(...)):
    try:
        xlsx = build_excel(
            cube=str(payload.get("cube", "")),
            question=str(payload.get("question", "")),
            analysis_rows=list(payload.get("analysis_rows", [])),
            structured_meta=dict(payload.get("structured_preview", {})),
        )
    except Exception as exc:
        raise HTTPException(500, str(exc)) from exc
    raw_name = str(payload.get("cube", "export") or "export")
    safe_name = re.sub(r'[^\w\-. ]', '_', raw_name).strip("_").strip() or "export"
    filename = safe_name + ".xlsx"
    return Response(
        content=xlsx,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": f"attachment; filename=\"{filename}\""},
    )
