import importlib.util
import json
import os
import shutil
from pathlib import Path


def _load_config_module():
    module_path = Path(__file__).resolve().parents[1] / "backend/config.py"
    spec = importlib.util.spec_from_file_location("test_backend_config", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def test_tm1_config_is_persisted_to_backend_runtime_json(monkeypatch):
    config = _load_config_module()
    test_dir = Path(__file__).resolve().parents[1] / "backend" / "runtime" / "test_tm1_config"
    shutil.rmtree(test_dir, ignore_errors=True)
    test_dir.mkdir(parents=True, exist_ok=True)
    runtime_path = test_dir / "tm1_config.json"
    llm_runtime_path = test_dir / "llm_config.json"
    env_path = test_dir / ".env"
    env_path.write_text("TM1_PORT=9510\n", encoding="utf-8")

    monkeypatch.setattr(config, "TM1_CONFIG_PATH", runtime_path)
    monkeypatch.setattr(config, "LLM_CONFIG_PATH", llm_runtime_path)
    monkeypatch.setattr(config, "ENV_PATH", env_path)
    monkeypatch.setattr(config, "RUNTIME_DIR", test_dir)
    monkeypatch.setitem(config.TM1_CONFIG, "port", 9510)
    monkeypatch.setitem(config.LLM_CONFIG, "model", "")
    monkeypatch.setitem(config.LLM_CONFIG, "provider", "openai")
    monkeypatch.setenv("TM1_PORT", "9510")

    try:
        saved = config.update_tm1_config({
            "address": "host.docker.internal",
            "llm_provider": "openai",
            "llm_model": "gpt-5.5",
            "cube_select_temperature": 0,
            "mdx_temperature": 0,
            "attribute_intent_temperature": 0,
            "semantic_profile_temperature": 0.2,
            "analysis_temperature": 0.2,
            "suggestions_temperature": 0.4,
            "port": 30090,
            "user": "admin",
            "password": "",
            "namespace": "",
            "ssl": False,
            "verify": False,
            "async_requests_mode": False,
        })

        persisted = json.loads(runtime_path.read_text(encoding="utf-8"))
        persisted_llm = json.loads(llm_runtime_path.read_text(encoding="utf-8"))
        assert saved["port"] == 30090
        assert saved["llm_provider"] == "openai"
        assert saved["llm_model"] == "gpt-5.5"
        assert saved["cube_select_temperature"] == 0
        assert saved["semantic_profile_temperature"] == 0.2
        assert persisted["port"] == 30090
        assert "llm_model" not in persisted
        assert "password" not in saved
        assert saved["has_password"] is False
        assert persisted_llm["provider"] == "openai"
        assert persisted_llm["model"] == "gpt-5.5"
        assert "api_key" not in persisted_llm
        assert persisted_llm["analysis_temperature"] == 0.2
        assert config.TM1_CONFIG["port"] == 30090
        assert config.get_llm_model() == "gpt-5.5"
        assert env_path.read_text(encoding="utf-8") == "TM1_PORT=9510\n"
        assert os.environ["TM1_PORT"] == "9510"
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)
