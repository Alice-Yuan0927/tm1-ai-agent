import json
import logging
import os
import threading
from pathlib import Path

try:
    from .util.io import atomic_write_json
except ImportError:  # Allows tests to load this file directly by path.
    from backend.util.io import atomic_write_json

_log = logging.getLogger(__name__)


BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR.parent / ".env"
DATA_DIR = BASE_DIR / "data"
RUNTIME_DIR = DATA_DIR / "runtime"
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
        except Exception as exc:
            _log.warning("TM1 config file unreadable, falling back to env: %s", exc)
    return _default_tm1_config_from_env()


TM1_CONFIG = _load_tm1_config()


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
        except Exception as exc:
            _log.warning("LLM config file unreadable, falling back to env: %s", exc)
    return _default_llm_config_from_env()


LLM_CONFIG = _load_llm_config()
_config_lock = threading.Lock()


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
        "has_password": bool(TM1_CONFIG.get("password", "")),
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
    tm1_values = dict(values)
    if "password" not in tm1_values:
        tm1_values["password"] = TM1_CONFIG.get("password", "")
    config = build_tm1_config(tm1_values)
    with _config_lock:
        LLM_CONFIG.clear()
        LLM_CONFIG.update(llm_config)
        TM1_CONFIG.clear()
        TM1_CONFIG.update(config)
    _write_tm1_config(config)
    _write_llm_config(llm_config)
    return public_tm1_config()


def _write_tm1_config(config: dict) -> None:
    atomic_write_json(TM1_CONFIG_PATH, build_tm1_config(config))


def _write_llm_config(config: dict) -> None:
    atomic_write_json(LLM_CONFIG_PATH, build_llm_config(config))


def get_tm1_config() -> dict:
    """Return a snapshot copy of the current TM1 connection config.

    Always call this instead of reading TM1_CONFIG directly — callers
    that capture the dict reference will miss live updates from
    update_tm1_config().
    """
    with _config_lock:
        return dict(TM1_CONFIG)


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

# Max pivot table size forwarded to the AI model for narrative analysis. This
# is intentionally lower than MAX_DATA_ROWS because hosted LLMs also enforce
# tokens-per-minute limits; Claude's default TPM can be much lower than its
# context window.
AI_MAX_ROWS = env_int("AI_MAX_ROWS", 80)
AI_MAX_COLUMNS = env_int("AI_MAX_COLUMNS", 40)

# ── AI token budgets ───────────────────────────────────────────────────────────
# MDX generation: keep enough room for the visible SELECT/FROM/WHERE statement.
MDX_MAX_TOKENS = env_int("MDX_MAX_TOKENS", 4096)

# Cube selection: compact JSON with 2-3 cube names plus short reasons.
CUBE_SELECT_MAX_TOKENS = env_int("CUBE_SELECT_MAX_TOKENS", 700)

# Financial analysis: narrative text + SUGGESTIONS JSON array.
ANALYSIS_MAX_TOKENS = env_int("ANALYSIS_MAX_TOKENS", 1800)

# Semantic profile generation: compact JSON mapping schema terms to business language.
SEMANTIC_PROFILE_MAX_TOKENS = env_int("SEMANTIC_PROFILE_MAX_TOKENS", 6000)

# ── Conversation context window ───────────────────────────────────────────────
HISTORY_WINDOW = env_int("HISTORY_WINDOW", 4)
HISTORY_ANALYSIS_MAX_CHARS = env_int("HISTORY_ANALYSIS_MAX_CHARS", 800)

# ── MDX execution + repair loop ───────────────────────────────────────────────
MAX_MDX_ATTEMPTS = env_int("MAX_MDX_ATTEMPTS", 3)

# ── Preview limits (rows actually rendered in the UI) ─────────────────────────
PREVIEW_ROW_LIMIT = env_int("PREVIEW_ROW_LIMIT", 15)
PREVIEW_COLUMN_LIMIT = env_int("PREVIEW_COLUMN_LIMIT", 15)

# ── Member grounding (SQL-side caps) ──────────────────────────────────────────
GROUNDED_MEMBER_LIMIT = env_int("GROUNDED_MEMBER_LIMIT", 40)
GROUNDED_MEMBER_PER_DIM = env_int("GROUNDED_MEMBER_PER_DIM", 5)

# ── Embeddings ───────────────────────────────────────────────────────────────
EMBEDDING_BATCH_SIZE = env_int("EMBEDDING_BATCH_SIZE", 500)

# ── Cube scope picker ─────────────────────────────────────────────────────────
CUBE_SELECT_LIMIT_AUTO = env_int("CUBE_SELECT_LIMIT_AUTO", 3)
CUBE_SELECT_LIMIT_MANUAL = env_int("CUBE_SELECT_LIMIT_MANUAL", 10)

# ── Cube context for selection (compact dim sampling) ─────────────────────────
CUBE_CONTEXT_ELEMENTS_PER_DIM = env_int("CUBE_CONTEXT_ELEMENTS_PER_DIM", 12)
CUBE_CONTEXT_MEASURE_LIMIT = env_int("CUBE_CONTEXT_MEASURE_LIMIT", 25)
CUBE_CONTEXT_ATTR_LIMIT = env_int("CUBE_CONTEXT_ATTR_LIMIT", 25)

# ── TM1 system cube (Sys Parameter) ──────────────────────────────────────────
# Standard TM1 cube that stores current-period values. Override via env if your
# model uses a different cube name (e.g. "System Parameters").
SYS_PARAMETER_CUBE = os.environ.get("TM1_SYS_PARAMETER_CUBE", "Sys Parameter")
# Element names inside that cube that map to current period values.
SYS_PARAMETER_PARAMS = [
    os.environ.get("TM1_SYS_PARAM_YEAR",          "Current Actual Year"),
    os.environ.get("TM1_SYS_PARAM_MONTH",         "Current Actual Month"),
    os.environ.get("TM1_SYS_PARAM_DAY",           "Current Actual Day in Month"),
    os.environ.get("TM1_SYS_PARAM_WEEK",          "Current Actual Week"),
    os.environ.get("TM1_SYS_PARAM_FORECAST_YEAR", "Current Forecast Year"),
]

# ── Currency fallback codes ───────────────────────────────────────────────────
# When the user asks for "parent / consolidated" currency without naming one,
# the planner tries these ISO codes in order against the model's currency dim.
# Override as a comma-separated list via env (e.g. "EUR,GBP,CHF").
PARENT_CURRENCY_FALLBACKS: list[str] = [
    c.strip()
    for c in os.environ.get("PARENT_CURRENCY_FALLBACKS", "USD,EUR,GBP").split(",")
    if c.strip()
]
