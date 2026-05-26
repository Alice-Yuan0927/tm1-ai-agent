import json
from pathlib import Path

from backend import llm_models


def test_deepseek_is_accepted_as_execution_provider():
    warnings = llm_models.validate_llm_selection(
        "deepseek",
        "deepseek-chat",
        llm_models.STATIC_LLM_MODEL_CATALOG,
    )

    assert not any("executes only" in warning for warning in warnings)


def test_deepseek_is_in_static_catalog():
    assert "deepseek" in llm_models.STATIC_LLM_MODEL_CATALOG
    assert "deepseek-v4-flash" in llm_models.STATIC_LLM_MODEL_CATALOG["deepseek"]["models"]


def test_validate_warns_when_official_list_does_not_include_selected_model():
    catalog = {
        "openai": {
            "models": ["gpt-5.2"],
            "source_type": "official_api",
        }
    }

    warnings = llm_models.validate_llm_selection("openai", "claude-sonnet-4-20250514", catalog)

    assert any("not returned by the openai official model API" in warning for warning in warnings)


def test_refresh_persists_official_model_catalog(monkeypatch):
    test_dir = Path(__file__).resolve().parents[1] / "backend" / "data" / "runtime" / "test_llm_models"
    test_dir.mkdir(parents=True, exist_ok=True)
    catalog_path = test_dir / "llm_models.json"

    monkeypatch.setattr(llm_models, "LLM_MODELS_PATH", catalog_path)
    monkeypatch.setattr(llm_models, "get_llm_api_key", lambda: "test-key")
    monkeypatch.setattr(llm_models, "_fetch_official_models", lambda provider, api_key: ["gpt-test"])

    catalog, warnings = llm_models.refresh_llm_model_catalog("openai", "gpt-test")

    persisted = json.loads(catalog_path.read_text(encoding="utf-8"))
    assert warnings == []
    assert catalog["openai"]["models"] == ["gpt-test"]
    assert persisted["openai"]["source_type"] == "official_api"
