import os
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
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

CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-opus-4-5")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
RESEND_FROM = os.environ.get("RESEND_FROM", "").strip()
MAX_DATA_ROWS = 150
APQ_CUBE = "}APQ Cube Views"
APQ_VIEW = "Default"
