"""TurboIntegrator (TI) process generation tools — developer mode only.

These tools are CODE-GENERATION ONLY. They do not execute against TM1 and
they do not write files. Their purpose is to let the agent assemble a
syntactically well-formed TI process skeleton from structured arguments
the LLM supplies, so the user can review and paste the result into TM1
Architect / PAW.

Only `generate_ti_process` is exposed today. Future iterations may add
dry-run preview, parameter validation against the live schema, or a
`run_ti_process` tool guarded by an explicit user confirmation.
"""

from __future__ import annotations

import json
import logging

from ...tm1.service import get_ti_process, list_ti_processes
from .registry import ToolRegistry, ToolSpec

_TEMPLATE_DEFAULT = "_New_Process_Template"

_log = logging.getLogger(__name__)


_DATASOURCE_TYPES = {"NULL", "ASCII", "VIEW", "ODBC", "TM1CUBEVIEW"}


def _render_parameters(params: list[dict]) -> str:
    if not params:
        return "# (no parameters)"
    lines = []
    for p in params:
        name = str(p.get("name", "")).strip()
        ptype = str(p.get("type", "String")).strip() or "String"
        default = p.get("default", "")
        if not name:
            continue
        lines.append(f"{name}  ({ptype})  default = {default!r}")
    return "\n".join(lines) or "# (no parameters)"


def _render_variables(vars_: list[dict]) -> str:
    if not vars_:
        return "# (no variables)"
    lines = []
    for v in vars_:
        name = str(v.get("name", "")).strip()
        vtype = str(v.get("type", "String")).strip() or "String"
        if not name:
            continue
        lines.append(f"{name}  ({vtype})")
    return "\n".join(lines) or "# (no variables)"


def _render_datasource(ds: dict) -> str:
    ds_type = str(ds.get("type", "NULL")).upper().strip() or "NULL"
    if ds_type not in _DATASOURCE_TYPES:
        ds_type = "NULL"
    rows = [f"DatasourceType: {ds_type}"]
    for key in ("name_for_server", "name_for_client", "ascii_delimiter",
                "ascii_decimal_separator", "ascii_thousand_separator",
                "ascii_quote_character", "ascii_header_records",
                "view_cube", "view_name", "odbc_password_use",
                "odbc_query"):
        if key in ds and ds[key] not in ("", None):
            rows.append(f"  {key}: {ds[key]}")
    return "\n".join(rows)


def _handle_list_ti_processes(args: dict, _cube_schema: dict) -> str:
    name_filter = str(args.get("name_filter", "") or "").strip()
    try:
        processes = list_ti_processes(name_filter)
    except Exception as exc:
        return json.dumps({"error": f"Could not list TI processes: {exc}"})

    names = [p["name"] for p in processes]
    has_default_template = _TEMPLATE_DEFAULT in names
    return json.dumps({
        "count": len(names),
        "processes": names[:200],
        "default_template": _TEMPLATE_DEFAULT,
        "default_template_exists": has_default_template,
        "note": (
            f"Use get_ti_process('{_TEMPLATE_DEFAULT}') as the starting template "
            "when it exists. Otherwise ask the user which existing process to "
            "base the new one on, or proceed from scratch."
        ),
    })


def _handle_get_ti_process(args: dict, _cube_schema: dict) -> str:
    name = str(args.get("name", "")).strip()
    if not name:
        return json.dumps({"error": "name is required"})
    try:
        process = get_ti_process(name)
    except Exception as exc:
        return json.dumps({"error": f"Could not fetch TI process {name!r}: {exc}"})
    return json.dumps(process, ensure_ascii=False)


def _handle_generate_ti_process(args: dict, _cube_schema: dict) -> str:
    """Stitch structured TI parts into a single review-ready process spec.

    Output is a JSON object whose `ti_process_code` field contains the
    full process definition as Markdown. The agent should embed this
    string in its next assistant message so the user sees it directly.
    """
    name = str(args.get("name", "")).strip()
    description = str(args.get("description", "")).strip()
    template_source = str(args.get("template_source", "")).strip()
    datasource = args.get("datasource") or {"type": "NULL"}
    parameters = args.get("parameters") or []
    variables = args.get("variables") or []
    prolog = str(args.get("prolog", "")).strip()
    metadata = str(args.get("metadata", "")).strip()
    data = str(args.get("data", "")).strip()
    epilog = str(args.get("epilog", "")).strip()

    if not name:
        return json.dumps({"error": "name is required (TI process name)"})
    if not template_source:
        return json.dumps({
            "error": (
                "template_source is required. Call list_ti_processes + "
                f"get_ti_process('{_TEMPLATE_DEFAULT}') first, or ask the "
                "user to pick an existing process as the template, and pass "
                "its name here."
            ),
        })
    if not (prolog or metadata or data or epilog):
        return json.dumps({
            "error": "At least one of prolog / metadata / data / epilog must be non-empty."
        })

    body = f"""# TM1 TurboIntegrator Process — DRAFT (review before importing)
# Process name: {name}
# Template source: {template_source}
# Purpose: {description or '(no description provided)'}

## Parameters
{_render_parameters(parameters)}

## Variables (data source columns)
{_render_variables(variables)}

## Datasource
{_render_datasource(datasource)}

## Prolog
```ti
{prolog or '# (inherited from template — no changes)'}
```

## Metadata
```ti
{metadata or '# (inherited from template — no changes)'}
```

## Data
```ti
{data or '# (inherited from template — no changes)'}
```

## Epilog
```ti
{epilog or '# (inherited from template — no changes)'}
```

---
> ⚠ Developer-mode output. This process is **not executed**. Review the
> logic, validate dimensions/elements against your model, then import via
> TM1 Architect, PAW, or `tm1py.processes.create()`.
"""

    return json.dumps({"ti_process_code": body, "process_name": name})


# ── Registry ──────────────────────────────────────────────────────────────────

TI_REGISTRY: ToolRegistry = ToolRegistry()

TI_REGISTRY.register(ToolSpec(
    name="list_ti_processes",
    description=(
        "DEVELOPER MODE. List TM1 TurboIntegrator (TI) processes already in "
        "the model. Always call this BEFORE generate_ti_process so you can "
        f"reuse '{_TEMPLATE_DEFAULT}' (or another existing process the user "
        "names) as the structural template. Returns process names only."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name_filter": {
                "type": "string",
                "description": "Optional case-insensitive substring filter on process name.",
            },
        },
        "required": [],
    },
    handler=_handle_list_ti_processes,
))

TI_REGISTRY.register(ToolSpec(
    name="get_ti_process",
    description=(
        "DEVELOPER MODE. Fetch the full definition of an existing TI process "
        "— parameters, variables, datasource, prolog/metadata/data/epilog. "
        f"Use this to load '{_TEMPLATE_DEFAULT}' (the default template) or a "
        "user-chosen process before generating a new one. Treat the returned "
        "code as the SCAFFOLD to adapt, not as a result to show the user."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Exact TI process name as returned by list_ti_processes.",
            },
        },
        "required": ["name"],
    },
    handler=_handle_get_ti_process,
))

TI_REGISTRY.register(ToolSpec(
    name="generate_ti_process",
    description=(
        "DEVELOPER MODE ONLY. Assemble a TM1 TurboIntegrator (TI) process "
        "draft from structured parts and return it for the user to review. "
        "PRE-CONDITIONS — you MUST have already: (a) called list_ti_processes "
        f"to confirm a template exists (default '{_TEMPLATE_DEFAULT}'), "
        "(b) called get_ti_process(template_source) to load that template's "
        "scaffold, and (c) grounded all referenced cubes / dimensions / "
        "elements via get_cube_schema and search_elements. The output is "
        "presented verbatim to the user — it is NOT executed. After calling "
        "this tool, respond with a short text message that quotes the returned "
        "`ti_process_code` in a fenced block and asks the user to confirm "
        "before importing."
    ),
    parameters={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "TI process name (PascalCase or dotted), e.g. 'Load.Actuals.Monthly'.",
            },
            "template_source": {
                "type": "string",
                "description": (
                    "Name of the existing TI process used as the structural "
                    f"template (e.g. '{_TEMPLATE_DEFAULT}' or whichever "
                    "process the user picked). Required — pass the exact "
                    "name you fetched via get_ti_process."
                ),
            },
            "description": {
                "type": "string",
                "description": "One-sentence purpose statement for the process.",
            },
            "datasource": {
                "type": "object",
                "description": (
                    "Datasource definition. Required key: type "
                    "(NULL | ASCII | VIEW | ODBC | TM1CUBEVIEW). "
                    "Optional keys depend on type: name_for_server, "
                    "name_for_client, ascii_delimiter, ascii_decimal_separator, "
                    "ascii_thousand_separator, ascii_quote_character, "
                    "ascii_header_records, view_cube, view_name, odbc_query."
                ),
            },
            "parameters": {
                "type": "array",
                "description": (
                    "Process parameters. Each item: "
                    "{name, type ('String'|'Numeric'), default}. "
                    "Preserve the template's parameter set unless the user "
                    "asked for changes."
                ),
            },
            "variables": {
                "type": "array",
                "description": (
                    "Data source variables (columns for ASCII/VIEW sources). "
                    "Each item: {name, type ('String'|'Numeric')}."
                ),
            },
            "prolog": {
                "type": "string",
                "description": (
                    "Prolog TI code — runs once before reading the datasource. "
                    "Start from the template's prolog and adapt it. Typical "
                    "use: declare temp values, open log file, validate "
                    "parameters, clear target cube region."
                ),
            },
            "metadata": {
                "type": "string",
                "description": (
                    "Metadata TI code — runs once per source record before "
                    "Data. Typical use: ensure dim elements exist via "
                    "DimensionElementInsert / DimensionElementComponentAdd."
                ),
            },
            "data": {
                "type": "string",
                "description": (
                    "Data TI code — runs once per source record. Reference "
                    "variables declared above. Typical use: CellPutN(...), "
                    "AttrPutS(...), DimensionElementInsert(...) etc."
                ),
            },
            "epilog": {
                "type": "string",
                "description": (
                    "Epilog TI code — runs once after the datasource is "
                    "exhausted. Typical use: SaveDataAll, close log file, "
                    "trigger downstream chore."
                ),
            },
        },
        "required": ["name", "template_source"],
    },
    handler=_handle_generate_ti_process,
))
