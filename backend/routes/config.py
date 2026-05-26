from typing import Any

from fastapi import APIRouter, Body, HTTPException

from ..config import public_tm1_config, update_tm1_config
from ..llm_models import load_llm_model_catalog, refresh_llm_model_catalog, validate_llm_selection
from ..schemas import TM1ConfigRequest
from ..tm1.cache import get_last_synced_at, sync_schema
from ..services import tm1_health as _health_svc
from ..services.embedding_sync import trigger_embedding_sync
from .schema import clear_suggestions_cache

router = APIRouter()


@router.get("/api/tm1-config")
def get_tm1_config():
    return {
        **public_tm1_config(),
        "llm_model_catalog": load_llm_model_catalog(),
        "llm_warnings": validate_llm_selection(),
    }


@router.post("/api/llm-models/refresh")
def refresh_llm_models(payload: dict[str, Any] = Body(default_factory=dict)):
    """Re-pull the provider's model list without touching TM1 config / cube sync."""
    provider = str(payload.get("provider", "")).strip() or None
    model = payload.get("model")
    model = str(model).strip() if model is not None else None
    try:
        catalog, warnings = refresh_llm_model_catalog(provider, model)
    except Exception as exc:
        raise HTTPException(500, f"Model refresh failed: {exc}") from exc
    provider_key = (provider or "").lower() or None
    provider_entry = catalog.get(provider_key) if provider_key else None
    return {
        "success": True,
        "provider": provider_key,
        "models": list((provider_entry or {}).get("models") or []),
        "source_type": (provider_entry or {}).get("source_type", ""),
        "refreshed_at": (provider_entry or {}).get("refreshed_at", ""),
        "llm_model_catalog": catalog,
        "llm_warnings": warnings,
    }


@router.post("/api/tm1-config")
def save_tm1_config(req: TM1ConfigRequest):
    try:
        config = update_tm1_config(req.model_dump())
        model_catalog, llm_warnings = refresh_llm_model_catalog(
            req.llm_provider,
            req.llm_model,
        )
        _health_svc.reset_for_config_change()
        summary = sync_schema()
        tm1_status, tm1_name = _health_svc.probe_tm1_name()
    except Exception as exc:
        _health_svc.update("down")
        raise HTTPException(500, f"TM1 configuration save failed: {exc}") from exc
    trigger_embedding_sync()
    _health_svc.set_probed(tm1_status, tm1_name)
    clear_suggestions_cache()
    return {
        "success": True,
        "config": config,
        "tm1_status": tm1_status,
        "tm1_name": tm1_name,
        "last_synced_at": get_last_synced_at(),
        "llm_model_catalog": model_catalog,
        "llm_warnings": llm_warnings,
        **summary,
    }
