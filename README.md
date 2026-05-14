# TM1 AI Analyst

A web-based AI analyst for IBM Planning Analytics (TM1). Ask questions in plain language and get TM1-backed analysis, tables, charts, Excel exports, and optional email sharing without writing MDX.

## Prerequisites

For Docker:

```text
Docker Desktop
```

For local Python runs:

```powershell
py -m pip install -r backend/requirements.txt
```

## Configuration

Create a `.env` file in the project root:

```env
LLM_API_KEY=sk-xxxxx

# Optional email settings through Resend
RESEND_API_KEY=re_xxxxx
RESEND_FROM=TM1 AI Analyst <onboarding@resend.dev>

# Optional startup model default; the app settings UI is the source of truth
# after you save a model there.
LLM_MODEL=

# Limits
MAX_DATA_ROWS=50000
AI_MAX_ROWS=300
TRANSPOSE_COLS=30
MDX_MAX_TOKENS=800
ANALYSIS_MAX_TOKENS=1800
CUBE_SELECT_MAX_TOKENS=400
SEMANTIC_PROFILE_MAX_TOKENS=6000

# Task-specific temperatures
CUBE_SELECT_TEMPERATURE=0
MDX_TEMPERATURE=0
ATTRIBUTE_INTENT_TEMPERATURE=0
SEMANTIC_PROFILE_TEMPERATURE=0.2
ANALYSIS_TEMPERATURE=0.2
SUGGESTIONS_TEMPERATURE=0.4
```

TM1 connection settings are saved from the app UI to `backend/runtime/tm1_config.json`.
Use the gear icon in the top-right to enter address, port, user, namespace, SSL,
and related settings, then click `Save & sync`.

## Run With Docker

From the project root in PowerShell:

```powershell
docker compose up --build
```

Then open:

```text
http://localhost:8000
```

Run in the background:

```powershell
docker compose up -d
```

Shut down:

```powershell
docker compose down
```

## Run Backend Locally

From the project root in PowerShell, load `.env` into the current terminal:

```powershell
Get-Content .env | Where-Object { $_ -and $_ -notmatch '^\s*#' } | ForEach-Object {
  $name, $value = $_ -split '=', 2
  [Environment]::SetEnvironmentVariable($name.Trim(), $value.Trim(), 'Process')
}
```

Start the backend:

```powershell
py -m uvicorn backend.backend:app --reload --port 8000
```

Then open `http://localhost:8000`.

## TM1 Model Setup

On startup, the backend syncs TM1 metadata into `backend/schema_cache.db`. The cache stores:

- cubes and descriptions
- dimensions in each cube
- all elements with element types
- dimension attributes
- alias and string attribute values

Cube descriptions are still useful, but cube selection no longer depends only on descriptions. The selector also receives compact schema context: dimensions, measures, attributes, and sample dimension elements.

To manually refresh the schema, click the TM1 status badge in the top-right corner or call:

```text
POST /api/sync-schema
```

## Switching TM1 Models

Use the gear icon in the top-right corner of the home screen.

The TM1 settings popup lets you edit:

- address
- port
- user
- password, with show/hide toggle
- namespace
- SSL
- verify SSL
- async request mode

Click `Save & sync` to:

1. update the active runtime `TM1_CONFIG`
2. write the TM1 settings back to `.env`
3. re-sync the schema cache for the new model
4. refresh TM1 status and suggestions

No manual `.env` edit is needed for normal model switching.

## Semantic Profiles

The app can generate a semantic profile for the active TM1 model. This is not a vector database. It is a structured, editable business map that helps the AI interpret terms such as `department`, `staff`, `labor cost`, `occupancy`, or `revenue` against the current model's cubes, dimensions, measures, and attributes.

Use `Generate profile` in the TM1 settings popup after syncing a new model. The backend reads the current schema cache, asks OpenAI to generate the profile, and saves it under:

```text
backend/model_profiles/<tm1_address>_<port>.json
```

Cube selection automatically loads the current model's profile when available, falling back to `backend/model_profiles/default.json`.

The profile contains:

- `business_terms`
- `metric_mappings`
- `cube_roles`
- `default_filters`
- `selection_guidance`
- `chart_guidance`

## Email Results

The Share menu can send the current analysis by email through Resend.

The email form includes an `Attach Excel files` checkbox:

- unchecked: sends only the analysis email
- checked: attaches every Excel workbook that is available from the current conversation's `Download Excel` buttons

`RESEND_FROM` must be accepted by your Resend account.

## Charts

Charts are generated from structured preview data:

- time-series data uses line charts
- categorical absolute measures use bar charts
- percentage/share/rate measures are split into separate doughnut charts
- consolidated rollup columns are excluded from leaf-level charts where needed
- wide tables are transposed by the backend when they exceed `TRANSPOSE_COLS`

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/health` | Backend status, LLM model, TM1 connection, last sync time |
| `GET` | `/api/views` | Cubes visible to the app |
| `POST` | `/api/sync-schema` | Re-sync TM1 schema cache |
| `GET` | `/api/tm1-config` | Return current TM1 connection settings |
| `POST` | `/api/tm1-config` | Save TM1 settings, update runtime config, and sync schema |
| `POST` | `/api/model-profile/generate` | Generate semantic profile for the active model |
| `GET` | `/api/suggestions` | AI-generated homepage suggested questions |
| `POST` | `/api/analyze` | SSE stream for the full analysis pipeline |
| `POST` | `/api/export-excel` | Generate and download an Excel workbook |
| `POST` | `/api/send-email` | Send analysis result by email via Resend |

## Project Structure

```text
backend/
  ai/
    service.py              # OpenAI calls: cube selection, MDX, profile generation, analysis
    rag.py                  # SQLite FTS5 query history for MDX examples
  model_profiles/
    default.json            # Generated semantic profile fallback
  tm1/
    cache.py                # SQLite schema cache and metadata lookups
    service.py              # TM1py wrappers, MDX execution, structured previews
  backend.py                # FastAPI routes and SSE pipeline
  config.py                 # Env config, TM1 runtime config, limits, temperatures
  excel_service.py          # Styled Excel export
  email_service.py          # Resend integration with optional Excel attachments
  schemas.py                # Pydantic request models

frontend/
  frontend.html             # App shell and layout
  assets/
    logo.svg
    block.png
    block2.png
    blockchain.png
  js/
    config.js               # API base URL, localStorage keys, class helpers
    api.js                  # Analyze SSE and Excel download
    render.js               # Message/source rendering
    table.js                # Table rendering
    charts.js               # Chart.js rendering and measure splitting
    store.js                # Browser localStorage
    share.js                # Email/link sharing
    ui.js                   # Chat mode and prompt dock
    sidebar.js              # Sidebar and history UI
    main.js                 # Page init, TM1 status, settings popup, suggestions
    markdown.js             # Markdown/table rendering
    frontend.tailwind.js    # Tailwind theme config
```

## How It Works

1. **Schema sync**: TM1 metadata is cached locally in SQLite.

2. **Semantic profile**: Optional model-specific profile maps natural business terms to schema concepts. This profile is generated from the schema cache and can be edited later.

3. **Cube selection**: The model receives the question, cube metadata, compact schema context, and the active semantic profile. It selects primary and fallback cubes.

4. **MDX generation**: The model generates MDX for each selected cube using the cube schema, previous conversation context, and similar successful queries.

5. **Execution and preview**: TM1py executes MDX, then the backend pivots results into structured rows with filters, row dimensions, columns, alias attributes, and chart metadata.

6. **Analysis**: The model receives compact pivoted data, not raw duplicated cell rows, and streams a concise financial analysis back to the UI.

7. **Output**: The frontend renders tables, charts, Excel export buttons, email sharing, and chat history.

## Troubleshooting

Common issues:

- `Connection refused` with Docker: set `TM1_ADDRESS=host.docker.internal`.
- `401 Unauthorized`: check `TM1_USER`, `TM1_PASSWORD`, and `TM1_NAMESPACE`.
- New model still shows old cubes: click `Save & sync` or call `POST /api/sync-schema`.
- Favicon or assets do not update: hard refresh with `Ctrl+F5`; favicon cache can be sticky.
- AI chooses the wrong cube after switching model: generate a semantic profile from the TM1 settings popup.
- `No usable data`: refresh schema, then retry with explicit year/scenario/month filters.

After editing `.env` manually, restart Docker:

```powershell
docker compose up -d --build
```



