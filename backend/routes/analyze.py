from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from ..schemas import QuestionRequest
from ..services.analyze_pipeline import analyze_sse_gen

router = APIRouter()


@router.post("/api/analyze")
def analyze(req: QuestionRequest, request: Request):
    return StreamingResponse(
        analyze_sse_gen(req, request),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
