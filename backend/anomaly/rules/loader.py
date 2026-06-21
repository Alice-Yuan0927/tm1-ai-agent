"""Load / save RuleSet YAML files from ``backend/data/anomaly_rules/``.

Filenames are cube-name slugged so they survive on disk and in git review.
The loader is forgiving on read (missing file = empty rule set so the UI
can show "no rules yet") and strict on write (always pydantic-validated).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import yaml

from .schema import RuleSet

_log = logging.getLogger(__name__)

_RULES_DIR = Path(__file__).resolve().parents[2] / "data" / "anomaly_rules"


def _slug(cube: str) -> str:
    """Filesystem-safe filename for an arbitrary cube name."""
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", cube).strip("_") or "cube"


def _path_for(cube: str) -> Path:
    return _RULES_DIR / f"{_slug(cube)}.yaml"


def list_cubes_with_rules() -> list[str]:
    """All cube names that currently have a rules file on disk."""
    if not _RULES_DIR.exists():
        return []
    out: list[str] = []
    for p in sorted(_RULES_DIR.glob("*.yaml")):
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            cube = str(data.get("cube") or p.stem)
            out.append(cube)
        except Exception as exc:
            _log.warning("[anomaly] could not read %s: %s", p, exc)
    return out


def load_rules(cube: str) -> RuleSet:
    """Return the saved RuleSet for ``cube``, or an empty one if none exists."""
    p = _path_for(cube)
    if not p.exists():
        return RuleSet(cube=cube)
    try:
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        # Tolerate a missing cube key — trust the filename intent.
        data.setdefault("cube", cube)
        return RuleSet.model_validate(data)
    except Exception as exc:
        _log.warning("[anomaly] failed to parse %s: %s — returning empty set", p, exc)
        return RuleSet(cube=cube)


def save_rules(rules: RuleSet) -> Path:
    """Persist ``rules`` to disk. Returns the file path written."""
    _RULES_DIR.mkdir(parents=True, exist_ok=True)
    p = _path_for(rules.cube)
    payload = rules.model_dump(mode="json", exclude_none=False)
    p.write_text(
        yaml.safe_dump(payload, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return p


def delete_rules(cube: str) -> bool:
    p = _path_for(cube)
    if p.exists():
        p.unlink()
        return True
    return False
