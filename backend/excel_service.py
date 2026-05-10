"""Build a styled Excel workbook from TM1 analysis data."""
from __future__ import annotations

import io
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .tm1.service import build_structured_preview

# ── colour palette ────────────────────────────────────────────────────────────
_BLUE_700  = "1D4ED8"
_BLUE_50   = "EFF6FF"
_BLUE_100  = "DBEAFE"
_SLATE_900 = "0F172A"
_SLATE_600 = "475569"
_SLATE_400 = "94A3B8"
_SLATE_200 = "E2E8F0"
_WHITE     = "FFFFFF"

_HEADER_FILL = PatternFill("solid", fgColor=_BLUE_700)
_ALT_FILL    = PatternFill("solid", fgColor=_BLUE_50)
_HEADER_FONT = Font(color=_WHITE,     bold=True,  size=11, name="Calibri")
_TITLE_FONT  = Font(color=_SLATE_900, bold=True,  size=14, name="Calibri")
_META_FONT   = Font(color=_SLATE_600, italic=True, size=10, name="Calibri")
_SMALL_FONT  = Font(color=_SLATE_400, size=9,  name="Calibri")
_FILTER_FONT = Font(color=_BLUE_700,  bold=True, size=10, name="Calibri")
_DATA_FONT   = Font(color=_SLATE_900, size=10, name="Calibri")
_THIN        = Side(border_style="thin", color=_SLATE_200)
_ROW_BORDER  = Border(bottom=_THIN)


def _cell(ws, row: int, col: int, value, font=None, fill=None, number_format=None,
          align: str = "left", border=None):
    c = ws.cell(row, col, value)
    if font:          c.font = font
    if fill:          c.fill = fill
    if number_format: c.number_format = number_format
    if border:        c.border = border
    c.alignment = Alignment(horizontal=align, vertical="center", wrap_text=False)
    return c


def build_excel(
    cube: str,
    question: str,
    analysis_rows: list[dict],
    structured_meta: dict,
) -> bytes:
    layout: dict = {
        "row_dimensions":    structured_meta.get("row_dimensions", []),
        "column_dimensions": structured_meta.get("column_dimensions", []),
        "filters":           structured_meta.get("filters", []),
        "applied_filters":   structured_meta.get("filters", []),
    }
    limit = max(len(analysis_rows), 1)
    preview = build_structured_preview(analysis_rows, layout, limit=limit)

    row_dims: list[str] = preview.get("row_dimensions", [])
    col_names: list[str] = preview.get("columns", [])
    filters:   list[dict] = preview.get("filters", [])
    data_rows: list[dict] = preview.get("rows", [])
    headers = row_dims + col_names

    wb = Workbook()
    ws = wb.active
    ws.title = cube[:31]  # Excel sheet name limit

    r = 1  # current row pointer

    # ── Title block ───────────────────────────────────────────────────────────
    _cell(ws, r, 1, cube, font=_TITLE_FONT); r += 1
    _cell(ws, r, 1, f"Generated: {datetime.now().strftime('%d %b %Y  %H:%M')}",
          font=_SMALL_FONT); r += 1
    r += 1  # blank

    # ── Active filters ────────────────────────────────────────────────────────
    if filters:
        filter_str = "   |   ".join(f"{f['dimension']}: {f['element']}" for f in filters)
        span = max(len(headers), 5)
        _cell(ws, r, 1, filter_str, font=_FILTER_FONT)
        if span > 1:
            ws.merge_cells(start_row=r, start_column=1, end_row=r, end_column=span)
        ws.row_dimensions[r].height = 18
        r += 1
        r += 1  # blank

    # ── Column headers ────────────────────────────────────────────────────────
    header_row = r
    for ci, h in enumerate(headers, 1):
        is_measure = ci > len(row_dims)
        _cell(ws, r, ci, h,
              font=_HEADER_FONT, fill=_HEADER_FILL,
              align="right" if is_measure else "left",
              border=Border(bottom=Side(border_style="medium", color=_WHITE)))
    ws.row_dimensions[r].height = 22
    r += 1

    # ── Data ──────────────────────────────────────────────────────────────────
    for ri, row_data in enumerate(data_rows):
        fill = _ALT_FILL if ri % 2 == 1 else None
        for ci, h in enumerate(headers, 1):
            val = row_data.get(h, "")
            is_measure = ci > len(row_dims)
            num_fmt = "#,##0.00" if is_measure and isinstance(val, (int, float)) else None
            _cell(ws, r, ci, val,
                  font=_DATA_FONT, fill=fill,
                  number_format=num_fmt,
                  align="right" if is_measure else "left",
                  border=_ROW_BORDER)
        ws.row_dimensions[r].height = 16
        r += 1

    # ── Row / column totals row ───────────────────────────────────────────────
    if data_rows and col_names:
        _cell(ws, r, 1, "Total", font=Font(bold=True, size=10, name="Calibri", color=_SLATE_900),
              border=Border(top=Side(border_style="medium", color=_BLUE_700),
                            bottom=_THIN))
        for ci, col in enumerate(col_names, len(row_dims) + 1):
            col_letter = get_column_letter(ci)
            formula = f"=SUM({col_letter}{header_row + 1}:{col_letter}{r - 1})"
            c = _cell(ws, r, ci, formula,
                      font=Font(bold=True, size=10, name="Calibri", color=_BLUE_700),
                      number_format="#,##0.00", align="right",
                      border=Border(top=Side(border_style="medium", color=_BLUE_700),
                                    bottom=_THIN))
        r += 1

    # ── Auto column widths ────────────────────────────────────────────────────
    for ci, h in enumerate(headers, 1):
        col_letter = get_column_letter(ci)
        max_len = len(str(h))
        for ri2 in range(header_row, r):
            val = ws.cell(ri2, ci).value
            if val is not None and not str(val).startswith("="):
                max_len = max(max_len, len(str(val)))
        ws.column_dimensions[col_letter].width = min(max_len + 3, 42)

    # ── Freeze panes below header ─────────────────────────────────────────────
    ws.freeze_panes = ws.cell(header_row + 1, 1)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
