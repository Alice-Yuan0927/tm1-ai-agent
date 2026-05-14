import json
import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR.parent / ".env"
RUNTIME_DIR = BASE_DIR / "runtime"
TM1_CONFIG_PATH = RUNTIME_DIR / "tm1_config.json"
LLM_CONFIG_PATH = RUNTIME_DIR / "llm_config.json"
LLM_MODELS_PATH = RUNTIME_DIR / "llm_models.json"
FRONTEND_DIR = BASE_DIR / "frontend"
if not FRONTEND_DIR.exists():
    FRONTEND_DIR = BASE_DIR.parent / "frontend"

DEFAULT_LLM_TEMPERATURES = {
    "cube_select_temperature": 0.0,
    "mdx_temperature": 0.0,
    "attribute_intent_temperature": 0.0,
    "semantic_profile_temperature": 0.2,
    "analysis_temperature": 0.2,
    "suggestions_temperature": 0.4,
}


def _load_env_file() -> None:
    """Load repo .env values for local runs without overriding real env vars."""
    if not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


_load_env_file()


def env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number") from exc


def env_float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise RuntimeError(f"{name} must be a number") from exc


def _default_tm1_config_from_env() -> dict:
    config = {
        "address": os.environ.get("TM1_ADDRESS", "localhost"),
        "port": env_int("TM1_PORT", 9510),
        "user": os.environ.get("TM1_USER", "admin"),
        "password": os.environ.get("TM1_PASSWORD", ""),
        "ssl": env_bool("TM1_SSL", False),
        "async_requests_mode": env_bool("TM1_ASYNC_REQUESTS_MODE", False),
        "verify": env_bool("TM1_VERIFY", False),
    }
    namespace = os.environ.get("TM1_NAMESPACE", "").strip()
    if namespace.lower() in {"none", "null", "false"}:
        namespace = ""
    if namespace:
        config["namespace"] = namespace
    return config


def _load_tm1_config() -> dict:
    if TM1_CONFIG_PATH.exists():
        try:
            return build_tm1_config(json.loads(TM1_CONFIG_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    return _default_tm1_config_from_env()


TM1_CONFIG = _load_tm1_config()


def _default_llm_config_from_env() -> dict:
    config = {
        "provider": os.environ.get("LLM_PROVIDER", "openai"),
        "model": os.environ.get("LLM_MODEL", ""),
    }
    config.update({
        "cube_select_temperature": env_float("CUBE_SELECT_TEMPERATURE", DEFAULT_LLM_TEMPERATURES["cube_select_temperature"]),
        "mdx_temperature": env_float("MDX_TEMPERATURE", DEFAULT_LLM_TEMPERATURES["mdx_temperature"]),
        "attribute_intent_temperature": env_float("ATTRIBUTE_INTENT_TEMPERATURE", DEFAULT_LLM_TEMPERATURES["attribute_intent_temperature"]),
        "semantic_profile_temperature": env_float("SEMANTIC_PROFILE_TEMPERATURE", DEFAULT_LLM_TEMPERATURES["semantic_profile_temperature"]),
        "analysis_temperature": env_float("ANALYSIS_TEMPERATURE", DEFAULT_LLM_TEMPERATURES["analysis_temperature"]),
        "suggestions_temperature": env_float("SUGGESTIONS_TEMPERATURE", DEFAULT_LLM_TEMPERATURES["suggestions_temperature"]),
    })
    return config


def _load_llm_config() -> dict:
    if LLM_CONFIG_PATH.exists():
        try:
            loaded = json.loads(LLM_CONFIG_PATH.read_text(encoding="utf-8"))
            return build_llm_config(loaded)
        except Exception:
            pass
    return _default_llm_config_from_env()


def build_tm1_config(values: dict[str, object]) -> dict:
    config = {
        "address": str(values.get("address", "localhost")).strip() or "localhost",
        "port": int(values.get("port", 9510)),
        "user": str(values.get("user", "admin")).strip() or "admin",
        "password": str(values.get("password", "")),
        "ssl": bool(values.get("ssl", False)),
        "async_requests_mode": bool(values.get("async_requests_mode", False)),
        "verify": bool(values.get("verify", False)),
    }
    namespace = str(values.get("namespace", "")).strip()
    if namespace and namespace.lower() not in {"none", "null", "false"}:
        config["namespace"] = namespace
    return config


def build_llm_config(values: dict[str, object]) -> dict:
    config = {
        "provider": str(values.get("provider", "openai")).strip().lower() or "openai",
        "model": str(values.get("model", "")).strip(),
    }
    for key, default in DEFAULT_LLM_TEMPERATURES.items():
        try:
            config[key] = float(values.get(key, default))
        except (TypeError, ValueError):
            config[key] = default
    return config


LLM_CONFIG = _load_llm_config()


def public_tm1_config() -> dict:
    return {
        "llm_provider": LLM_CONFIG.get("provider", "openai"),
        "llm_model": LLM_CONFIG.get("model", ""),
        "cube_select_temperature": get_llm_temperature("cube_select_temperature"),
        "mdx_temperature": get_llm_temperature("mdx_temperature"),
        "attribute_intent_temperature": get_llm_temperature("attribute_intent_temperature"),
        "semantic_profile_temperature": get_llm_temperature("semantic_profile_temperature"),
        "analysis_temperature": get_llm_temperature("analysis_temperature"),
        "suggestions_temperature": get_llm_temperature("suggestions_temperature"),
        "address": TM1_CONFIG.get("address", ""),
        "port": TM1_CONFIG.get("port", 9510),
        "user": TM1_CONFIG.get("user", ""),
        "password": TM1_CONFIG.get("password", ""),
        "namespace": TM1_CONFIG.get("namespace", ""),
        "ssl": bool(TM1_CONFIG.get("ssl", False)),
        "verify": bool(TM1_CONFIG.get("verify", False)),
        "async_requests_mode": bool(TM1_CONFIG.get("async_requests_mode", False)),
    }


def update_tm1_config(values: dict[str, object]) -> dict:
    llm_config = build_llm_config({
        "provider": values.get("llm_provider", "openai"),
        "model": values.get("llm_model", ""),
        "cube_select_temperature": values.get("cube_select_temperature", DEFAULT_LLM_TEMPERATURES["cube_select_temperature"]),
        "mdx_temperature": values.get("mdx_temperature", DEFAULT_LLM_TEMPERATURES["mdx_temperature"]),
        "attribute_intent_temperature": values.get("attribute_intent_temperature", DEFAULT_LLM_TEMPERATURES["attribute_intent_temperature"]),
        "semantic_profile_temperature": values.get("semantic_profile_temperature", DEFAULT_LLM_TEMPERATURES["semantic_profile_temperature"]),
        "analysis_temperature": values.get("analysis_temperature", DEFAULT_LLM_TEMPERATURES["analysis_temperature"]),
        "suggestions_temperature": values.get("suggestions_temperature", DEFAULT_LLM_TEMPERATURES["suggestions_temperature"]),
    })
    config = build_tm1_config(values)
    LLM_CONFIG.clear()
    LLM_CONFIG.update(llm_config)
    TM1_CONFIG.clear()
    TM1_CONFIG.update(config)
    _write_tm1_config(config)
    _write_llm_config(llm_config)
    return public_tm1_config()


def _write_tm1_config(config: dict) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    TM1_CONFIG_PATH.write_text(
        json.dumps(build_tm1_config(config), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_llm_config(config: dict) -> None:
    RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    LLM_CONFIG_PATH.write_text(
        json.dumps(build_llm_config(config), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def get_llm_model() -> str:
    return str(LLM_CONFIG.get("model") or os.environ.get("LLM_MODEL") or "").strip()


def get_llm_provider() -> str:
    return str(LLM_CONFIG.get("provider") or os.environ.get("LLM_PROVIDER") or "openai").strip().lower()


def get_llm_api_key() -> str:
    return str(os.environ.get("LLM_API_KEY") or "").strip()


def get_llm_temperature(key: str) -> float:
    if key not in DEFAULT_LLM_TEMPERATURES:
        raise KeyError(f"Unknown LLM temperature setting: {key}")
    value = LLM_CONFIG.get(key)
    if value is None:
        return DEFAULT_LLM_TEMPERATURES[key]
    try:
        return float(value)
    except (TypeError, ValueError):
        return DEFAULT_LLM_TEMPERATURES[key]


RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM = os.environ.get("RESEND_FROM", "").strip()
# ── Data size limits ──────────────────────────────────────────────────────────
# Hard cap on raw TM1 cells fetched from a cellset. This limits everything
# downstream: analysis_rows, full_preview, and Excel export. It is a safety
# valve against accidental wide MDX queries (which can return millions of
# cells and exhaust memory). 50 000 cells covers typical financial queries
# (e.g. 500 employees × 12 months × ~8 measures) with room to spare.
MAX_DATA_ROWS = 50_000

# Columns beyond this count flip the table to transposed layout (measures as
# rows, entities as columns). Time-series dimensions are never transposed.
TRANSPOSE_COLS = env_int("TRANSPOSE_COLS", 30)

# Max pivot rows forwarded to the AI model for analysis. Keeps prompts well under
# the 200K token limit even when MAX_DATA_ROWS is large. Raw TM1 cells contain
# a _dimensions dict that duplicates every field, making them 5-10x larger
# than the equivalent pivot row — so 300 pivoted rows ≈ safe budget.
AI_MAX_ROWS = env_int("AI_MAX_ROWS", 300)

# ── AI token budgets ───────────────────────────────────────────────────────────
# MDX generation: keep enough room for the visible SELECT/FROM/WHERE statement.
MDX_MAX_TOKENS = env_int("MDX_MAX_TOKENS", 4096)

# Cube selection: compact JSON with 2-3 cube names plus short reasons.
CUBE_SELECT_MAX_TOKENS = env_int("CUBE_SELECT_MAX_TOKENS", 700)

# Financial analysis: narrative text + SUGGESTIONS JSON array.
ANALYSIS_MAX_TOKENS = env_int("ANALYSIS_MAX_TOKENS", 1800)

# Semantic profile generation: compact JSON mapping schema terms to business language.
SEMANTIC_PROFILE_MAX_TOKENS = env_int("SEMANTIC_PROFILE_MAX_TOKENS", 6000)
