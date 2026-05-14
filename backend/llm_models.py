import json
from datetime import datetime, timezone
from typing import Any

import requests

try:
    from openai import OpenAI
except ImportError:  # pragma: no cover - covered by environments without optional dependency installed
    OpenAI = None

from .config import LLM_MODELS_PATH, get_llm_api_key, get_llm_model, get_llm_provider


STATIC_LLM_MODEL_CATALOG: dict[str, dict[str, Any]] = {
    "openai": {
        "label": "OpenAI / GPT",
        "models": [
            "gpt-5.5", "gpt-5.5-pro",
            "gpt-5.4", "gpt-5.4-mini", "gpt-5.4-nano", "gpt-5.4-pro",
            "gpt-5.2", "gpt-5.2-pro",
            "gpt-5.1",
            "gpt-5", "gpt-5-mini", "gpt-5-nano", "gpt-5-pro",
            "gpt-4.1", "gpt-4.1-mini", "gpt-4.1-nano",
            "o3", "o3-mini", "o4-mini",
        ],
        "source": "https://platform.openai.com/docs/models",
        "source_type": "static_fallback",
    },
    "anthropic": {
        "label": "Anthropic / Claude",
        "models": [
            "claude-opus-4-7",
            "claude-opus-4-6",
            "claude-opus-4-5-20251101",
            "claude-opus-4-1-20250805",
            "claude-sonnet-4-6",
            "claude-sonnet-4-5-20250929",
            "claude-haiku-4-5-20251001",
        ],
        "source": "https://docs.anthropic.com/en/docs/about-claude/models/overview",
        "source_type": "static_fallback",
    },
    "google": {
        "label": "Google / Gemini",
        "models": [
            "gemini-3-pro-preview",
            "gemini-2.5-pro",
            "gemini-2.5-flash",
            "gemini-2.5-flash-lite-preview-09-2025",
            "gemini-2.0-flash",
        ],
        "source": "https://ai.google.dev/gemini-api/docs/models/gemini",
        "source_type": "static_fallback",
    },
    "xai": {
        "label": "xAI / Grok",
        "models": ["grok-4.20", "grok-4", "grok-4-latest", "grok-3", "grok-3-latest"],
        "source": "https://docs.x.ai/docs/models/",
        "source_type": "static_fallback",
    },
    "deepseek": {
        "label": "DeepSeek",
        "models": ["deepseek-v4-flash", "deepseek-v4-pro", "deepseek-chat", "deepseek-reasoner"],
        "source": "https://api-docs.deepseek.com/api/list-models",
        "source_type": "static_fallback",
    },
}

EXECUTION_PROVIDERS = {"openai", "anthropic"}
_REQUEST_TIMEOUT = 12


def load_llm_model_catalog() -> dict[str, dict[str, Any]]:
    if not LLM_MODELS_PATH.exists():
        return _copy_catalog(STATIC_LLM_MODEL_CATALOG)
    try:
        loaded = json.loads(LLM_MODELS_PATH.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            return _merge_catalog(loaded)
    except Exception:
        pass
    return _copy_catalog(STATIC_LLM_MODEL_CATALOG)


def refresh_llm_model_catalog(provider: str | None = None, model: str | None = None) -> tuple[dict[str, dict[str, Any]], list[str]]:
    provider = (provider or get_llm_provider() or "openai").strip().lower()
    model = (model if model is not None else get_llm_model()).strip()
    api_key = get_llm_api_key()
    catalog = load_llm_model_catalog()
    warnings: list[str] = []

    if provider not in STATIC_LLM_MODEL_CATALOG:
        warnings.append(f"Unknown LLM provider '{provider}'.")
    elif not api_key:
        warnings.append("LLM_API_KEY is not set, so the official model list could not be refreshed.")
    else:
        try:
            models = _fetch_official_models(provider, api_key)
            if models:
                catalog[provider] = {
                    **STATIC_LLM_MODEL_CATALOG[provider],
                    "models": models,
                    "source_type": "official_api",
                    "refreshed_at": _utc_now(),
                }
            else:
                warnings.append(f"{provider} official model list returned no models; using cached/fallback list.")
        except Exception as exc:
            warnings.append(f"{provider} official model list refresh failed: {_short_error(exc)}")

    warnings.extend(validate_llm_selection(provider, model, catalog))
    _write_catalog(catalog)
    return catalog, warnings


def validate_llm_selection(
    provider: str | None = None,
    model: str | None = None,
    catalog: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    provider = (provider or get_llm_provider() or "openai").strip().lower()
    model = (model if model is not None else get_llm_model()).strip()
    catalog = catalog or load_llm_model_catalog()
    warnings: list[str] = []

    if provider not in EXECUTION_PROVIDERS:
        warnings.append(
            f"LLM provider '{provider}' is configured, but this backend currently executes only OpenAI and Anthropic calls."
        )
    if not model:
        warnings.append("No LLM model is selected.")
        return warnings

    provider_catalog = catalog.get(provider) or {}
    models = [str(item) for item in provider_catalog.get("models") or []]
    if provider_catalog.get("source_type") == "official_api":
        if model not in models:
            warnings.append(
                f"Selected model '{model}' was not returned by the {provider} official model API. Check the provider/API key/model combination."
            )
    elif models and model not in models:
        warnings.append(
            f"Selected model '{model}' is not in the cached {provider} model list; it could not be verified against the official API."
        )

    return warnings


def _fetch_official_models(provider: str, api_key: str) -> list[str]:
    if provider == "openai":
        if OpenAI is None:
            raise RuntimeError("openai package is not installed")
        data = OpenAI(api_key=api_key).models.list()
        ids = [item.id for item in data.data if getattr(item, "id", "")]
    elif provider == "anthropic":
        response = requests.get(
            "https://api.anthropic.com/v1/models",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01"},
            timeout=_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        ids = [item.get("id") for item in response.json().get("data", []) if item.get("id")]
    elif provider == "google":
        response = requests.get(
            "https://generativelanguage.googleapis.com/v1beta/models",
            params={"key": api_key},
            timeout=_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        ids = [
            str(item.get("name", "")).removeprefix("models/")
            for item in response.json().get("models", [])
            if item.get("name")
        ]
    elif provider == "xai":
        response = requests.get(
            "https://api.x.ai/v1/models",
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        ids = [item.get("id") for item in response.json().get("data", []) if item.get("id")]
    elif provider == "deepseek":
        response = requests.get(
            "https://api.deepseek.com/models",
            headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
            timeout=_REQUEST_TIMEOUT,
        )
        response.raise_for_status()
        ids = [item.get("id") for item in response.json().get("data", []) if item.get("id")]
    else:
        ids = []

    return sorted({model_id for model_id in ids if isinstance(model_id, str) and model_id.strip()})


def _merge_catalog(loaded: dict[str, Any]) -> dict[str, dict[str, Any]]:
    catalog = _copy_catalog(STATIC_LLM_MODEL_CATALOG)
    for provider, meta in loaded.items():
        if not isinstance(meta, dict):
            continue
        base = catalog.get(provider, {})
        models = meta.get("models")
        if not isinstance(models, list) or not all(isinstance(model, str) for model in models):
            models = base.get("models", [])
        catalog[provider] = {**base, **meta, "models": models}
    return catalog


def _copy_catalog(catalog: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return json.loads(json.dumps(catalog))


def _write_catalog(catalog: dict[str, dict[str, Any]]) -> None:
    LLM_MODELS_PATH.parent.mkdir(parents=True, exist_ok=True)
    LLM_MODELS_PATH.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _short_error(exc: Exception) -> str:
    return str(exc).replace("\n", " ")[:240]
