# TM1 AI Analyst - Demo Setup

## Prerequisites
For Docker:

```text
Docker Desktop
```

For local Python runs:

```bash
pip install -r backend/requirements.txt
```

## Configuration
Create a `.env` file in the project root:

```env
ANTHROPIC_API_KEY=sk-ant-xxxxx

# Docker default when TM1 runs on your Windows host:
TM1_ADDRESS=host.docker.internal
TM1_PORT=9510
TM1_USER=admin
TM1_PASSWORD=
TM1_NAMESPACE=
TM1_SSL=false
TM1_VERIFY=false

# Optional email settings through Resend
RESEND_API_KEY=re_xxxxx
RESEND_FROM=TM1 AI Analyst <onboarding@resend.dev>
```

If you run the backend directly on Windows instead of Docker, use `TM1_ADDRESS=localhost`
when TM1 is on the same machine.

## Before Running
Fill in the **Description** attribute on each cube in TM1.
The AI selects which cubes to query entirely based on these descriptions.

In TM1 / Architect, open the `}Cubes` dimension and add a `Description` string attribute
for each cube you want the AI to use. Example descriptions:

| Cube                     | Description                                                     |
|--------------------------|-----------------------------------------------------------------|
| Labor Summary            | Consolidated labor cost summary across all categories           |
| Labor Gross Salary       | Gross salary by employee and department including all allowances |
| Labor Headcount Movement | Headcount changes: new hires, departures, transfers by period   |
| Labor Base Pay           | Monthly base salary by employee, position and department        |
| Labor Analysis           | Labor cost analysis comparing actual vs budget vs forecast      |
| Labor FTE Allocation     | FTE headcount allocation by department and cost center          |

After the backend starts for the first time it auto-syncs the TM1 schema (cubes,
dimensions, elements) into a local SQLite cache. To refresh manually after changing
descriptions or adding cubes, call:

```text
POST http://localhost:8000/api/sync-schema
```

## Email Results
The **Send result by email** panel uses Resend API settings from `.env`.
Leave them blank if you do not need email sending.

`RESEND_FROM` must be a sender accepted by your Resend account. You can use
Resend's test sender while testing, then switch to your verified domain sender.

## Chat History
Chat history is stored in the browser with `localStorage`.
It keeps the latest 20 analysis results and can be cleared from the UI.

# Run with Docker
From the project root in PowerShell:

```powershell
docker compose up --build

docker compose up -d
```


Then open:

```text
http://localhost:8000
```

Docker Compose automatically reads `.env`.

Shut Down:

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

Install dependencies and start the backend:

```powershell
py -m pip install -r backend/requirements.txt
py -m uvicorn backend.backend:app --reload --port 8000
```

Then open `http://localhost:8000`.

## Switching TM1 Models

To point the agent at a different TM1 server or model (e.g. staging → production,
or a different Planning Analytics Workspace tenant):

**Step 1 — Update connection settings in `.env`**

```env
TM1_ADDRESS=new-server.example.com
TM1_PORT=9510
TM1_USER=admin
TM1_PASSWORD=newpassword
TM1_NAMESPACE=               # leave blank for native auth, or set CAM namespace
```

**Step 2 — Restart the container and sync the schema**

```powershell
docker compose up -d --build
```

Then call the sync endpoint to pull the new model's cube/dimension/element data:

```text
POST http://localhost:8000/api/sync-schema
```

That's it. The AI will immediately start selecting from the new model's cubes and
generating correct MDX for its dimensions and elements.

> **Tip:** If you only changed cube descriptions (not the server), you can skip
> the restart and just call `/api/sync-schema`.

## Debug Endpoints
- `GET  /api/health`       — check backend is up and see which Claude model is active
- `GET  /api/views`        — list all cubes the AI can see (with descriptions)
- `POST /api/sync-schema`  — re-sync TM1 cube/dimension/element cache from TM1

## Troubleshooting
If the UI shows `Internal Server Error`, open the debug endpoint:

```text
http://localhost:8000/api/views
```

Common causes:

- `Connection refused` with `localhost`: when running Docker, set `TM1_ADDRESS=host.docker.internal` in `.env`.
- `401 Unauthorized`: TM1 is reachable, but `TM1_USER` / `TM1_PASSWORD` or the TM1 authentication mode is wrong. If your TM1 uses CAM/LDAP, set `TM1_NAMESPACE` as well.
- `No cubes with descriptions found`: add a `Description` string attribute to the `}Cubes` dimension in TM1, or ensure at least some cubes have non-empty descriptions.
- `No usable data`: the AI generated MDX that returned empty — try calling `POST /api/sync-schema` to refresh the element cache, then ask again.

After editing `.env`, restart Docker:

```powershell
docker compose up -d --build
```

## Project Structure

```
backend/
  tm1/
    cache.py     — SQLite schema cache (cubes, dimensions, elements)
    service.py   — TM1py wrappers: schema fetch, MDX execution, view utilities
  ai/
    service.py   — Claude prompts: cube selection, MDX generation, analysis
    rag.py       — SQLite FTS5 query history for MDX few-shot examples
  backend.py     — FastAPI app and route handlers
  config.py      — Environment variable loading
  email_service.py
  schemas.py
```
