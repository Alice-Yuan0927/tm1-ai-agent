# TM1 AI Analyst - Demo Setup

## Prerequisites
```bash
pip install -r backend/requirements.txt
```

## Configuration
Set configuration through environment variables or a `.env` file:

```env
ANTHROPIC_API_KEY=sk-ant-xxxxx
TM1_ADDRESS=localhost
TM1_PORT=9510
TM1_USER=admin
TM1_PASSWORD=apple
TM1_SSL=false
```

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
The **Send result by email** panel uses SMTP settings from environment variables:

```bash
export SMTP_HOST=smtp.example.com
export SMTP_PORT=587
export SMTP_USER=your-user
export SMTP_PASSWORD=your-password
export SMTP_FROM=tm1-analyst@example.com
export SMTP_TLS=true
```

If your SMTP server does not require TLS, set:
```bash
export SMTP_TLS=false
```

## Chat History
Chat history is stored in the browser with `localStorage`.
It keeps the latest 20 analysis results and can be cleared from the UI.

## Run
```bash
# Terminal 1 - backend
export ANTHROPIC_API_KEY=sk-ant-xxxxx
uvicorn backend.backend:app --reload --port 8000

# Terminal 2 - frontend
open frontend/frontend.html
# or open http://localhost:8000 when the backend is running
```

## Run with Docker
From PowerShell:

```powershell
$env:ANTHROPIC_API_KEY="sk-ant-xxxxx"
docker compose up --build
```

Then open:

```text
http://localhost:8000
```

By default Docker connects to TM1 on the Windows host at `host.docker.internal:9510`.
Override these values if your TM1 server is somewhere else:

```powershell
$env:TM1_ADDRESS="your-tm1-host"
$env:TM1_PORT="9510"
$env:TM1_USER="admin"
$env:TM1_PASSWORD="apple"
docker compose up --build
```

## Debug Endpoints
- GET http://localhost:8000/api/health - check backend is up
- GET http://localhost:8000/api/views - preview all views the AI can see
