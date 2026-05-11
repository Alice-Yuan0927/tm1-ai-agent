"""Build a styled Excel workbook from TM1 analysis data."""
from __future__ import annotations

import io
import re
from datetime import datetime

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .tm1.service import build_structured_preview

# ── colour palette ────────────────────────────────────────────────────────────
_BLUE_700  = "01497C"
_BLUE_50   = "EFF6FF"
_BLUE_100  = "DBEAFE"
_SLATE_900 = "0F172A"
_SLATE_600 = "475569"
_SLATE_400 = "697D9B"
_SLATE_200 = "E2E8F0"
_WHITE     = "FFFFFF"

_HEADER_FILL      = PatternFill("solid", fgColor=_BLUE_700)
_ALT_FILL         = PatternFill("solid", fgColor=_BLUE_50)
_HEADER_FONT      = Font(color=_WHITE,     bold=True,  size=11, name="Calibri")
_TITLE_FONT       = Font(color=_SLATE_900, bold=True,  size=14, name="Calibri")
_META_FONT        = Font(color=_SLATE_600, italic=True, size=10, name="Calibri")
_SMALL_FONT       = Font(color=_SLATE_400, size=9,  name="Calibri")
_FILTER_LABEL     = Font(color=_SLATE_400, size=9,  name="Calibri", italic=True)
_FILTER_VALUE     = Font(color=_BLUE_700,  bold=True, size=10, name="Calibri")
_DATA_FONT        = Font(color=_SLATE_900, size=10, name="Calibri")
_TOTAL_LABEL_FONT = Font(color=_SLATE_900, bold=True, size=10, name="Calibri")
_TOTAL_VALUE_FONT = Font(color=_BLUE_700,  bold=True, size=10, name="Calibri")
_THIN             = Side(border_style="thin",   color=_SLATE_200)
_MED_BLUE         = Side(border_style="medium", color=_BLUE_700)
_ROW_BORDER       = Border(bottom=_THIN)

# Transpose flag comes from build_structured_preview (backend decides based on
# TRANSPOSE_COLS config); no hardcoded threshold here.


def _cell(ws, row: int, col: int, value, font=None, fill=None, number_format=None,
          align: str = "left", border=None):
    c = ws.cell(row, col, value)
    if font:          c.font = font
    if fill:          c.fill = fill
    if number_format: c.number_format = number_format
    if border:        c.border = border
    c.alignment = Alignment(horizontal=align, vertical="center", wrap_text=False)
    return c


def _col_widths(ws, start_row: int, end_row: int, n_cols: int) -> None:
    """Auto-fit column widths based on cell content."""
    for ci in range(1, n_cols + 1):
        col_letter = get_column_letter(ci)
        max_len = 0
        for ri in range(start_row, end_row):
            val = ws.cell(ri, ci).value
            if val is not None and not str(val).startswith("="):
                max_len = max(max_len, len(str(val)))
        ws.column_dimensions[col_letter].width = min(max_len + 3, 42)


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

    row_dims:  list[str]  = preview.get("row_dimensions", [])
    col_names: list[str]  = preview.get("columns", [])
    filters:   list[dict] = preview.get("filters", [])
    data_rows: list[dict] = preview.get("rows", [])

    wb = Workbook()
    ws = wb.active
    ws.title = (re.sub(r'[\\/*?:\[\]]', '-', cube)[:31] or "Sheet")

    r = 1  # current row pointer

    # ── Title block ───────────────────────────────────────────────────────────
    _cell(ws, r, 1, cube, font=_TITLE_FONT); r += 1
    _cell(ws, r, 1, f"Generated: {datetime.now().strftime('%d %b %Y  %H:%M')}",
          font=_SMALL_FONT); r += 1
    r += 1  # blank

    # ── Active filters — one row per filter ───────────────────────────────────
    if filters:
        for f in filters:
            _cell(ws, r, 1, f"{f['dimension']}:", font=_FILTER_LABEL, align="right")
            _cell(ws, r, 2, f["element"],          font=_FILTER_VALUE)
            ws.row_dimensions[r].height = 15
            r += 1
        r += 1  # blank after filter block

    transpose = bool(preview.get("transpose")) and bool(data_rows)

    if not transpose:
        _write_normal(ws, r, row_dims, col_names, data_rows)
    else:
        _write_transposed(ws, r, row_dims, col_names, data_rows, preview)

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ── Normal layout (few columns) ───────────────────────────────────────────────

def _write_normal(ws, r: int, row_dims, col_names, data_rows) -> None:
    headers = row_dims + col_names

    # Headers
    header_row = r
    for ci, h in enumerate(headers, 1):
        is_measure = ci > len(row_dims)
        _cell(ws, r, ci, h,
              font=_HEADER_FONT, fill=_HEADER_FILL,
              align="right" if is_measure else "left",
              border=Border(bottom=Side(border_style="medium", color=_WHITE)))
    ws.row_dimensions[r].height = 22
    r += 1

    # Data rows
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

    # Totals row
    if data_rows and col_names:
        _cell(ws, r, 1, "Total", font=_TOTAL_LABEL_FONT,
              border=Border(top=_MED_BLUE, bottom=_THIN))
        for ci, col in enumerate(col_names, len(row_dims) + 1):
            letter = get_column_letter(ci)
            formula = f"=SUM({letter}{header_row + 1}:{letter}{r - 1})"
            _cell(ws, r, ci, formula, font=_TOTAL_VALUE_FONT,
                  number_format="#,##0.00", align="right",
                  border=Border(top=_MED_BLUE, bottom=_THIN))
        r += 1

    _col_widths(ws, header_row, r, len(headers))
    ws.freeze_panes = ws.cell(header_row + 1, 1)


# ── Transposed layout (many columns → measures become rows) ───────────────────

def _write_transposed(ws, r: int, row_dims, col_names, data_rows, preview) -> None:
    # Build column header labels from row-dimension values of each data row
    def _row_label(row_data: dict) -> str:
        parts = [str(row_data.get(d, "")) for d in row_dims]
        return " / ".join(p for p in parts if p) or "Value"

    col_headers = [_row_label(rd) for rd in data_rows]
    measure_col_header = preview.get("measure_dimension") or "Measure"
    all_headers = [measure_col_header] + col_headers

    # Header row
    header_row = r
    for ci, h in enumerate(all_headers, 1):
        _cell(ws, r, ci, h,
              font=_HEADER_FONT, fill=_HEADER_FILL,
              align="left" if ci == 1 else "right",
              border=Border(bottom=Side(border_style="medium", color=_WHITE)))
    ws.row_dimensions[r].height = 22
    r += 1

    # One row per measure column
    for ri, col in enumerate(col_names):
        fill = _ALT_FILL if ri % 2 == 1 else None
        _cell(ws, r, 1, col, font=_DATA_FONT, fill=fill,
              align="left", border=_ROW_BORDER)
        for ci, row_data in enumerate(data_rows, 2):
            val = row_data.get(col, "")
            num_fmt = "#,##0.00" if isinstance(val, (int, float)) else None
            _cell(ws, r, ci, val, font=_DATA_FONT, fill=fill,
                  number_format=num_fmt, align="right", border=_ROW_BORDER)
        ws.row_dimensions[r].height = 16
        r += 1

    # Totals row — sum across all shown data columns per measure
    if data_rows:
        _cell(ws, r, 1, "Total", font=_TOTAL_LABEL_FONT,
              border=Border(top=_MED_BLUE, bottom=_THIN))
        for ci in range(2, len(data_rows) + 2):
            letter = get_column_letter(ci)
            formula = f"=SUM({letter}{header_row + 1}:{letter}{r - 1})"
            _cell(ws, r, ci, formula, font=_TOTAL_VALUE_FONT,
                  number_format="#,##0.00", align="right",
                  border=Border(top=_MED_BLUE, bottom=_THIN))
        r += 1

    _col_widths(ws, header_row, r, len(all_headers))
    # Freeze header row AND first (measure-name) column
    ws.freeze_panes = ws.cell(header_row + 1, 2)
