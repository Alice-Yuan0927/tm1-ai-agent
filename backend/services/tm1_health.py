import threading
import time as _time

from ..config import get_tm1_config

_tm1_health_lock = threading.Lock()
_tm1_health: dict = {"status": "unknown", "name": "", "ts": 0.0}


def fallback_name() -> str:
    cfg = get_tm1_config()
    address = cfg.get("address", "")
    port = cfg.get("port", "")
    return f"{address}:{port}" if port else str(address)


def probe_tm1_name() -> tuple[str, str]:
    """Probe the active TM1 connection and return (status, display_name)."""
    from TM1py import TM1Service
    fb = fallback_name()
    try:
        with TM1Service(**get_tm1_config()) as tm1:
            name = tm1.server.get_server_name() or fb
        return "up", name
    except Exception:
        return "down", fb


def needs_probe_and_mark_checking() -> bool:
    """Return True if health needs probing, atomically marking state as 'checking'."""
    global _tm1_health
    with _tm1_health_lock:
        if _tm1_health["status"] == "unknown":
            _tm1_health = {**_tm1_health, "status": "checking"}
            return True
    return False


def set_probed(status: str, name: str) -> None:
    global _tm1_health
    with _tm1_health_lock:
        _tm1_health = {"status": status, "name": name, "ts": _time.monotonic()}


def update(status: str, name: str | None = None) -> None:
    """Passively update TM1 status, preserving the display name unless given."""
    global _tm1_health
    with _tm1_health_lock:
        next_health = {**_tm1_health, "status": status, "ts": _time.monotonic()}
        if name is not None:
            next_health["name"] = name
        elif not next_health.get("name"):
            next_health["name"] = fallback_name()
        _tm1_health = next_health


def get() -> dict:
    with _tm1_health_lock:
        return dict(_tm1_health)


def reset_for_config_change() -> None:
    global _tm1_health
    with _tm1_health_lock:
        _tm1_health = {
            "status": "checking",
            "name": fallback_name(),
            "ts": _time.monotonic(),
        }
