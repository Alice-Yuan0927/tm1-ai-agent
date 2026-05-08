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
Fill in the **Description** column in `}APQ Cube Views` for key views.
The AI selects views entirely based on this field.

Suggested views to fill:

| Cube                     | View    | Description                                                     |
|--------------------------|---------|-----------------------------------------------------------------|
| Labor Summary            | Default | Consolidated labor cost summary across all categories           |
| Labor Gross Salary       | Default | Gross salary by employee and department including all allowances |
| Labor Headcount Movement | Default | Headcount changes: new hires, departures, transfers by period   |
| Labor Base Pay           | Default | Monthly base salary by employee, position and department        |
| Labor Analysis           | Default | Labor cost analysis comparing actual vs budget vs forecast      |
| Labor FTE Allocation     | Default | FTE headcount allocation by department and cost center          |

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

## Debug Endpoints
- GET http://localhost:8000/api/health - check backend is up
- GET http://localhost:8000/api/views - preview all views the AI can see

## Troubleshooting
If the UI shows `Internal Server Error`, open the debug endpoint:

```text
http://localhost:8000/api/views
```

Common causes:

- `Connection refused` with `localhost`: when running Docker, set `TM1_ADDRESS=host.docker.internal` in `.env`.
- `401 Unauthorized`: TM1 is reachable, but `TM1_USER` / `TM1_PASSWORD` or the TM1 authentication mode is wrong. If your TM1 uses CAM/LDAP, set `TM1_NAMESPACE` as well.
- `No views with descriptions found`: fill in the `Description` column in `}APQ Cube Views`.

After editing `.env`, restart Docker:

```powershell
docker compose up -d --build
```
