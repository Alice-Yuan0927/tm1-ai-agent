# TM1 AI Analyst - Demo Setup

## Prerequisites
```bash
pip install fastapi uvicorn TM1py anthropic
```

## Configuration
Edit `backend.py` top section:
```python
TM1_CONFIG = {
    "address": "localhost",
    "port":    9510,
    "user":    "admin",
    "password":"apple",
    "ssl":     False,
}
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
uvicorn backend:app --reload --port 8000

# Terminal 2 - frontend
open frontend.html
# or double-click frontend.html in File Explorer
```

## Debug Endpoints
- GET http://localhost:8000/api/health - check backend is up
- GET http://localhost:8000/api/views - preview all views the AI can see
