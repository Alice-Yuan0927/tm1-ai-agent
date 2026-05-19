import threading

from fastapi import APIRouter, HTTPException

from ..ai.output.narrative import generate_homepage_suggestions
from ..llm_models import refresh_llm_model_catalog
from ..tm1.cache import get_last_synced_at, sync_schema
from ..tm1.service import get_cubes_with_descriptions
from ..services import tm1_health as _health_svc
from ..services.embedding_sync import trigger_embedding_sync, trigger_summary_refresh
from ..services.model_profile import generate_current_model_profile_response, load_current_model_profile

router = APIRouter()

_suggestions_cache: list[str] | None = None
_suggestions_lock = threading.Lock()


def clear_suggestions_cache() -> None:
    global _suggestions_cache
    with _suggestions_lock:
        _suggestions_cache = None


@router.post("/api/sync-schema")
def sync_schema_endpoint():
    """Re-sync the TM1 schema cache (cubes, dimensions, elements)."""
    model_catalog, llm_warnings = refresh_llm_model_catalog()
    try:
        summary = sync_schema()
    except Exception as exc:
        _health_svc.update("down")
        raise HTTPException(500, f"Schema sync failed: {exc}") from exc
    _health_svc.update("up")
    trigger_embedding_sync()
    trigger_summary_refresh(model_profile=load_current_model_profile())
    clear_suggestions_cache()
    return {
        "success": True,
        "last_synced_at": get_last_synced_at(),
        "llm_model_catalog": model_catalog,
        "llm_warnings": llm_warnings,
        **summary,
    }


@router.get("/api/suggestions")
def get_suggestions():
    """Return 3 AI-generated suggested questions based on the connected TM1 cubes."""
    global _suggestions_cache
    with _suggestions_lock:
        if _suggestions_cache is not None:
            return {"suggestions": _suggestions_cache}
    try:
        cubes = get_cubes_with_descriptions()
    except RuntimeError as exc:
        _health_svc.update("down")
        raise HTTPException(500, str(exc)) from exc
    _health_svc.update("up")
    suggestions = generate_homepage_suggestions(cubes)
    with _suggestions_lock:
        _suggestions_cache = suggestions
    return {"suggestions": suggestions}


@router.get("/api/views")
def list_views():
    try:
        cubes = get_cubes_with_descriptions()
    except RuntimeError as exc:
        _health_svc.update("down")
        raise HTTPException(500, str(exc)) from exc
    _health_svc.update("up")
    return {"count": len(cubes), "cubes": cubes}


@router.post("/api/model-profile/generate")
def generate_model_profile_endpoint():
    return generate_current_model_profile_response()
