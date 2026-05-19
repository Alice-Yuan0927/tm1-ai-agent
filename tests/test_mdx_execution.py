import pytest

from backend.ai.mdx.context import MdxContext
from backend.services import mdx_execution


def _ctx() -> MdxContext:
    return MdxContext(
        question="show revenue",
        cube_schema={"cube": "Cube", "dimensions": []},
    )


def _disable_validators(monkeypatch) -> None:
    monkeypatch.setattr(mdx_execution, "_validate_static_mdx_schema", lambda *a, **k: None)
    monkeypatch.setattr(mdx_execution, "_validate_entity_filter", lambda *a, **k: None)
    monkeypatch.setattr(mdx_execution, "_validate_default_filter_warning", lambda *a, **k: None)
    monkeypatch.setattr(mdx_execution, "_validate_account_dim_choice", lambda *a, **k: None)
    monkeypatch.setattr(mdx_execution, "_validate_specific_focus", lambda *a, **k: None)
    monkeypatch.setattr(mdx_execution, "_validate_statement_line_items", lambda *a, **k: None)


def test_execute_mdx_with_repair_success(monkeypatch):
    _disable_validators(monkeypatch)
    rows = [{"_dimensions": {}, "value": 1}]
    layout = {"row_dimensions": [], "column_dimensions": []}
    monkeypatch.setattr(
        mdx_execution,
        "execute_generated_mdx",
        lambda cube, mdx: (rows, layout),
    )

    out_rows, out_layout, out_mdx, attempts = mdx_execution.execute_mdx_with_repair(
        _ctx(), "SELECT {} ON COLUMNS FROM [Cube]"
    )

    assert out_rows == rows
    assert out_layout == layout
    assert out_mdx == "SELECT {} ON COLUMNS FROM [Cube]"
    assert attempts == []


def test_execute_mdx_repairs_pre_execution_validation_failure(monkeypatch):
    _disable_validators(monkeypatch)
    calls = {"static": 0}

    def static_issue(*args, **kwargs):
        calls["static"] += 1
        return "bad schema member" if calls["static"] == 1 else None

    monkeypatch.setattr(mdx_execution, "_validate_static_mdx_schema", static_issue)
    monkeypatch.setattr(
        mdx_execution,
        "repair_cube_mdx",
        lambda ctx, mdx, error: "SELECT {} ON COLUMNS FROM [Cube]",
    )
    monkeypatch.setattr(
        mdx_execution,
        "execute_generated_mdx",
        lambda cube, mdx: ([{"_dimensions": {}, "value": 1}], {"column_dimensions": []}),
    )

    _, _, out_mdx, attempts = mdx_execution.execute_mdx_with_repair(
        _ctx(), "SELECT {} ON COLUMNS FROM [Cube]"
    )

    assert out_mdx == "SELECT {} ON COLUMNS FROM [Cube]"
    assert attempts == ["attempt 1: bad schema member"]


def test_execute_mdx_connection_error_does_not_repair(monkeypatch):
    _disable_validators(monkeypatch)
    repaired = []
    monkeypatch.setattr(
        mdx_execution,
        "execute_generated_mdx",
        lambda cube, mdx: (_ for _ in ()).throw(RuntimeError("connection refused")),
    )
    monkeypatch.setattr(
        mdx_execution,
        "repair_cube_mdx",
        lambda *args: repaired.append(True) or "SELECT {} ON COLUMNS FROM [Cube]",
    )

    with pytest.raises(RuntimeError, match="connection refused"):
        mdx_execution.execute_mdx_with_repair(_ctx(), "SELECT {} ON COLUMNS FROM [Cube]")

    assert repaired == []


def test_execute_mdx_records_default_warning(monkeypatch):
    _disable_validators(monkeypatch)
    monkeypatch.setattr(
        mdx_execution,
        "_validate_default_filter_warning",
        lambda *a, **k: "using profile default",
    )
    monkeypatch.setattr(
        mdx_execution,
        "execute_generated_mdx",
        lambda cube, mdx: ([{"_dimensions": {}, "value": 1}], {"column_dimensions": []}),
    )

    *_, attempts = mdx_execution.execute_mdx_with_repair(
        _ctx(), "SELECT {} ON COLUMNS FROM [Cube]"
    )

    assert attempts == ["using profile default"]
