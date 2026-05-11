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
