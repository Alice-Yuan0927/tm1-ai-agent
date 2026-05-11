import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
ENV_PATH = BASE_DIR.parent / ".env"
FRONTEND_DIR = BASE_DIR / "frontend"
if not FRONTEND_DIR.exists():
    FRONTEND_DIR = BASE_DIR.parent / "frontend"


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


TM1_CONFIG = {
    "address": os.environ.get("TM1_ADDRESS", "localhost"),
    "port": env_int("TM1_PORT", 9510),
    "user": os.environ.get("TM1_USER", "admin"),
    "password": os.environ.get("TM1_PASSWORD", ""),
    "ssl": env_bool("TM1_SSL", False),
    "async_requests_mode": env_bool("TM1_ASYNC_REQUESTS_MODE", False),
    "verify": env_bool("TM1_VERIFY", False),
}

tm1_namespace = os.environ.get("TM1_NAMESPACE", "").strip()
if tm1_namespace.lower() in {"none", "null", "false"}:
    tm1_namespace = ""
if tm1_namespace:
    TM1_CONFIG["namespace"] = tm1_namespace


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


def public_tm1_config() -> dict:
    return {
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
    config = build_tm1_config(values)
    TM1_CONFIG.clear()
    TM1_CONFIG.update(config)
    _write_env_tm1_config(config)
    return public_tm1_config()


def _write_env_tm1_config(config: dict) -> None:
    replacements = {
        "TM1_ADDRESS": str(config.get("address", "")),
        "TM1_PORT": str(config.get("port", "")),
        "TM1_USER": str(config.get("user", "")),
        "TM1_PASSWORD": str(config.get("password", "")),
        "TM1_NAMESPACE": str(config.get("namespace", "")),
        "TM1_SSL": str(bool(config.get("ssl", False))).lower(),
        "TM1_VERIFY": str(bool(config.get("verify", False))).lower(),
        "TM1_ASYNC_REQUESTS_MODE": str(bool(config.get("async_requests_mode", False))).lower(),
    }
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    seen: set[str] = set()
    next_lines: list[str] = []

    for line in lines:
        key = line.split("=", 1)[0].strip() if "=" in line else ""
        if key in replacements:
            next_lines.append(f"{key}={replacements[key]}")
            seen.add(key)
        else:
            next_lines.append(line)

    if next_lines and next_lines[-1].strip():
        next_lines.append("")
    for key, value in replacements.items():
        if key not in seen:
            next_lines.append(f"{key}={value}")

    ENV_PATH.write_text("\n".join(next_lines).rstrip() + "\n", encoding="utf-8")

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-4-5")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
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

# Max pivot rows forwarded to Claude for analysis. Keeps prompts well under
# the 200K token limit even when MAX_DATA_ROWS is large. Raw TM1 cells contain
# a _dimensions dict that duplicates every field, making them 5-10x larger
# than the equivalent pivot row — so 300 pivoted rows ≈ safe budget.
AI_MAX_ROWS = env_int("AI_MAX_ROWS", 300)

# ── Claude token budgets ───────────────────────────────────────────────────────
# MDX generation: needs enough room for a complete SELECT/FROM/WHERE statement.
MDX_MAX_TOKENS = env_int("MDX_MAX_TOKENS", 800)

# Cube selection: only returns a short JSON object with 2-3 cube names.
CUBE_SELECT_MAX_TOKENS = env_int("CUBE_SELECT_MAX_TOKENS", 400)

# Financial analysis: narrative text + SUGGESTIONS JSON array.
ANALYSIS_MAX_TOKENS = env_int("ANALYSIS_MAX_TOKENS", 1800)

# Task-specific model temperatures. Keep planning/MDX deterministic; allow a
# little more natural language variation for narrative analysis and suggestions.
CUBE_SELECT_TEMPERATURE = env_float("CUBE_SELECT_TEMPERATURE", 0.0)
MDX_TEMPERATURE = env_float("MDX_TEMPERATURE", 0.0)
ATTRIBUTE_INTENT_TEMPERATURE = env_float("ATTRIBUTE_INTENT_TEMPERATURE", 0.0)
SEMANTIC_PROFILE_TEMPERATURE = env_float("SEMANTIC_PROFILE_TEMPERATURE", 0.2)
ANALYSIS_TEMPERATURE = env_float("ANALYSIS_TEMPERATURE", 0.2)
SUGGESTIONS_TEMPERATURE = env_float("SUGGESTIONS_TEMPERATURE", 0.4)
