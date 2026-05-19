"""Pure-function smoke tests for the new util/ helpers."""

import json
import os
from pathlib import Path

import pytest

from backend.util.io import atomic_write_json, atomic_write_text
from backend.util.llm_json import parse_llm_json


def test_parse_llm_json_strips_json_fence():
    raw = "```json\n{\"a\": 1, \"b\": [2, 3]}\n```"
    assert parse_llm_json(raw) == {"a": 1, "b": [2, 3]}


def test_parse_llm_json_strips_plain_fence():
    raw = "```\n[1, 2]\n```"
    assert parse_llm_json(raw) == [1, 2]


def test_parse_llm_json_handles_no_fence():
    assert parse_llm_json('{"x": "y"}') == {"x": "y"}


def test_parse_llm_json_rejects_invalid():
    with pytest.raises(json.JSONDecodeError):
        parse_llm_json("not json at all")


def test_atomic_write_text_creates_parent_dirs(tmp_path: Path):
    target = tmp_path / "a" / "b" / "out.txt"
    atomic_write_text(target, "hello")
    assert target.read_text(encoding="utf-8") == "hello"


def test_atomic_write_text_overwrites(tmp_path: Path):
    target = tmp_path / "out.txt"
    atomic_write_text(target, "v1")
    atomic_write_text(target, "v2")
    assert target.read_text(encoding="utf-8") == "v2"


def test_atomic_write_text_cleans_tmp_on_failure(tmp_path: Path, monkeypatch):
    target = tmp_path / "out.txt"

    def boom(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(RuntimeError, match="disk full"):
        atomic_write_text(target, "hello")
    # The tmp file should NOT be left behind.
    leftovers = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert leftovers == []


def test_atomic_write_json_round_trips(tmp_path: Path):
    target = tmp_path / "out.json"
    payload = {"k": [1, 2, {"nested": True}]}
    atomic_write_json(target, payload)
    assert json.loads(target.read_text(encoding="utf-8")) == payload
