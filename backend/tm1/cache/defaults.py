"""Default-element picker + current-period probe via the Sys Parameter cube."""

import logging
import re
from datetime import date

from TM1py import TM1Service

from ...config import get_tm1_config, SYS_PARAMETER_CUBE, SYS_PARAMETER_PARAMS

_log = logging.getLogger(__name__)


# ── Current period (from TM1 Sys Parameter cube) ─────────────────────────────

def _normalise_month(value: object) -> str:
    text = str(value or "").strip()
    match = re.search(r"(\d{1,2})", text)
    if not match:
        return text
    month = max(1, min(12, int(match.group(1))))
    return f"{month:02d}"


def _fallback_current_period() -> dict[str, str | bool]:
    today = date.today()
    return {
        "Year": str(today.year),
        "Month": f"{today.month:02d}",
        "current_year": str(today.year),
        "current_month": f"{today.month:02d}",
        "current_day": str(today.day),
        "source": "server_date",
        "used_real_current_date": True,
    }


def get_current_period_defaults() -> dict[str, str | bool]:
    """Return current-period defaults from the TM1 Sys Parameter cube.

    Falls back to the backend server date when the cube or cells are unavailable.
    """
    fallback = _fallback_current_period()
    try:
        with TM1Service(**get_tm1_config()) as tm1:
            dims = list(tm1.cubes.get_dimension_names(SYS_PARAMETER_CUBE))
            measure_dim = tm1.cubes.get_measure_dimension(SYS_PARAMETER_CUBE)
            param_dim = next((d for d in dims if d != measure_dim), "")
            if not param_dim or not measure_dim:
                return fallback

            measure_elements = list(tm1.elements.get_element_names(measure_dim, measure_dim))
            text_measure = next((m for m in measure_elements if m.lower() == "text"), None)
            if not text_measure:
                return fallback

            rows = ", ".join(f"[{param_dim}].[{param_dim}].[{p}]" for p in SYS_PARAMETER_PARAMS)
            mdx = (
                f"SELECT {{[{measure_dim}].[{measure_dim}].[{text_measure}]}} ON COLUMNS, "
                f"{{{rows}}} ON ROWS FROM [{SYS_PARAMETER_CUBE}]"
            )
            cellset = tm1.cells.execute_mdx(mdx, skip_zeros=False)

        values: dict[str, str] = {}
        for coords, cell in cellset.items():
            param = ""
            for coord in coords:
                text = str(coord)
                if text.startswith(f"[{param_dim}]."):
                    param = text.rsplit("[", 1)[-1][:-1]
                    break
            raw_value = cell.get("Value") if isinstance(cell, dict) else cell
            if param and raw_value not in (None, ""):
                values[param] = str(raw_value).strip()

        year = values.get("Current Actual Year") or fallback["Year"]
        month = _normalise_month(values.get("Current Actual Month") or fallback["Month"])
        day = values.get("Current Actual Day in Month") or fallback["current_day"]
        return {
            "Year": str(year),
            "Month": month,
            "current_year": str(year),
            "current_month": month,
            "current_day": str(day),
            "current_week": values.get("Current Actual Week", ""),
            "forecast_year": values.get("Current Forecast Year", ""),
            "source": "tm1_sys_parameter",
            "used_real_current_date": False,
        }
    except Exception as exc:
        _log.debug("%s probe failed, using server date: %s", SYS_PARAMETER_CUBE, exc)
        return fallback


# ── Default element picker ────────────────────────────────────────────────────

def _dim_base_name(dim_name: str) -> str:
    """'Segment 1' -> 'segment', 'Account Report' -> 'account report'."""
    cleaned = re.sub(r"\s+\d+$", "", str(dim_name).strip())
    return cleaned.lower()


def _base_word_variants(base: str) -> set[str]:
    variants: set[str] = {base}
    if base.endswith("y") and len(base) > 1:
        variants.add(base[:-1] + "ies")
    elif base.endswith("ies") and len(base) > 3:
        variants.add(base[:-3] + "y")
    if base.endswith("s") and len(base) > 1:
        variants.add(base[:-1])
    else:
        variants.add(base + "s")
    return variants


_PNL_BOTTOMLINE_TOKEN_SETS: list[set[str]] = [
    {"net", "income"},
    {"net", "profit"},
    {"net", "earnings"},
    {"profit", "after", "tax"},
    {"loss", "after", "tax"},
    {"earnings", "after", "tax"},
    {"profit", "before", "tax"},
    {"loss", "before", "tax"},
    {"earnings", "before", "tax"},
    {"operating", "profit"},
    {"operating", "income"},
    {"operating", "result"},
    {"gross", "profit"},
    {"gross", "margin"},
    {"comprehensive", "income"},
    {"income", "statement"},
    {"profit", "and", "loss"},
    {"ebit"},
    {"ebitda"},
    {"result", "for", "year"},
    {"result", "for", "period"},
]


def looks_like_pnl_bottom_line(name: str) -> bool:
    normalised = re.sub(r"\s*&\s*", " and ", str(name).lower())
    words = set(re.findall(r"[a-z]+", normalised))
    if not words:
        return False
    return any(tokens.issubset(words) for tokens in _PNL_BOTTOMLINE_TOKEN_SETS)


def pick_default_element(
    dim_name: str,
    top_consolidations: list[str],
    consolidations: list[str],
    elements: list[str],
) -> str:
    """Pick the broadest aggregate element to use as default_element."""
    candidates = top_consolidations or consolidations
    if not candidates:
        return (elements[:1] or [""])[0]

    base = _dim_base_name(dim_name).lower()
    base_variants = _base_word_variants(base) if base else set()

    if base_variants:
        for variant in sorted(base_variants, key=len, reverse=True):
            target = f"all {variant}"
            for name in candidates:
                if name.lower() == target:
                    return name

    if base_variants:
        for name in candidates:
            lower = name.lower()
            if any(re.search(rf"\b{re.escape(v)}\b", lower) for v in base_variants):
                return name

    all_total = [n for n in candidates if re.match(r"(?i)^(all|total)\s", n)]
    if all_total:
        all_total.sort(key=len)
        return all_total[0]

    return candidates[0]
