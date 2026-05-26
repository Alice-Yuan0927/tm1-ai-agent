from fastapi import APIRouter

from ..config import get_llm_model
from ..llm_models import validate_llm_selection
from ..tm1.cache import get_last_synced_at
from ..services import tm1_health as _health_svc

router = APIRouter()


@router.get("/api/health")
def health():
    needs_probe = _health_svc.needs_probe_and_mark_checking()
    if needs_probe:
        status, name = _health_svc.probe_tm1_name()
        _health_svc.set_probed(status, name)
    cached = _health_svc.get()
    return {
        "status": "ok",
        "model": get_llm_model(),
        "llm_warnings": validate_llm_selection(),
        "tm1_status": cached["status"],
        "tm1_name": cached["name"],
        "last_synced_at": get_last_synced_at(),
    }
