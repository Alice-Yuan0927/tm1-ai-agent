"""RAG feedback endpoints.

The agent no longer auto-saves successful queries to the knowledge base.
The frontend exposes 👍 (save) and 👎 (forget) controls on each result;
clicking one of those calls into here.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..ai.tools.rag_tools import forget_query, record_query

router = APIRouter()


class RagSaveRequest(BaseModel):
    question: str
    cube: str
    mdx: str
    row_count: int = 0
    grounded_members: list = Field(default_factory=list)


class RagForgetRequest(BaseModel):
    cube: str
    mdx: str


@router.post("/api/rag/save")
def rag_save(req: RagSaveRequest):
    if not req.cube.strip() or not req.mdx.strip():
        raise HTTPException(400, "cube and mdx are required")
    try:
        rowid = record_query(
            req.question, req.cube, req.mdx,
            row_count=req.row_count,
            grounded_members=req.grounded_members or [],
        )
    except Exception as exc:
        raise HTTPException(500, f"Could not save query: {exc}") from exc
    return {"success": True, "rowid": rowid}


@router.post("/api/rag/forget")
def rag_forget(req: RagForgetRequest):
    if not req.cube.strip() or not req.mdx.strip():
        raise HTTPException(400, "cube and mdx are required")
    try:
        removed = forget_query(req.cube, req.mdx)
    except Exception as exc:
        raise HTTPException(500, f"Could not forget query: {exc}") from exc
    return {"success": True, "removed": removed}
