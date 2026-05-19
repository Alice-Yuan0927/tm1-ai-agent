from fastapi import APIRouter, HTTPException

from ..email_service import send_analysis_email
from ..schemas import EmailRequest

router = APIRouter()


@router.post("/api/send-email")
def send_email(req: EmailRequest):
    try:
        send_analysis_email(req)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(500, str(exc)) from exc
    return {"success": True}
