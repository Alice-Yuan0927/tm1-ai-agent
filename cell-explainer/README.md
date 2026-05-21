# Cell Explainer — Arc Plugin

AI-powered cell explanation for IBM Planning Analytics (TM1) inside Cubewise Arc.

Paste a Cell Reference from Arc's cube viewer and get instant analysis:
- Variance vs Plan/Forecast/Prior Period/Prior Year
- Transaction log with who-changed-what-when
- Root cause drill-down through dimensions
- Calculation trace for rule-calculated cells

## Architecture

```
┌─────────────────────┐    paste TSV    ┌──────────────────────┐
│  Arc Cube Viewer    │ ──────────────▶ │  Cell Explainer      │
│  (right-click cell  │                 │  Plugin (AngularJS)  │
│   → Cell Reference) │                 │                      │
└─────────────────────┘                 └──────────┬───────────┘
                                                   │ POST /api/explain-cell
                                                   ▼
                                        ┌──────────────────────┐
                                        │  FastAPI Backend     │
                                        │  (MCP-based agent)   │
                                        └──────────┬───────────┘
                                                   │
                                ┌──────────────────┼──────────────────┐
                                ▼                  ▼                  ▼
                        ┌───────────────┐  ┌──────────────┐  ┌──────────────┐
                        │ Anthropic API │  │ TM1 REST API │  │  pgvector    │
                        │ (Claude)      │  │ (via TM1py)  │  │  (semantic)  │
                        └───────────────┘  └──────────────┘  └──────────────┘
```

## Installation

### 1. Place the plugin in Arc's plugin folder

Copy the entire `cell-explainer/` folder into your Arc plugins directory:

```
<Arc install path>/plugins/cell-explainer/
    plugin.js
    template.html
    README.md
```

On Windows Arc Desktop, the path is typically:
```
C:\Users\<user>\AppData\Local\Programs\Arc\plugins\
```

### 2. Configure backend URL

Open `plugin.js` and update `BACKEND_URL` near the top of the file to point at your FastAPI backend:

```js
var BACKEND_URL = 'http://localhost:8000';
```

### 3. Run the backend

```bash
pip install fastapi uvicorn pydantic
uvicorn backend_example:app --reload --port 8000
```

(The provided `backend_example.py` has stub responses — replace the agent functions with calls to your real MCP-based agent.)

### 4. Reload Arc

In Arc, press `SHIFT+F5` to clear cache and reload. No Arc restart needed.

The plugin will appear under **Tools → Cell Explainer** in the left menu.

## Usage

1. Open any cube view in Arc
2. **Right-click** a cell → **Cell Reference**
3. In the Cell Reference dialog, **Ctrl+A** then **Ctrl+C**
4. Open **Tools → Cell Explainer**
5. Click the paste box and **Ctrl+V**
6. Enter the cube name, choose a baseline, click an analysis action
7. Click follow-up chips to drill deeper

## Known Limitations

- **Plugin needs cube name manually** — Arc's Cell Reference dialog doesn't include cube name in the copied text, so the user must enter it. Future fix: read cube context from the currently active Arc tab via the `$rootScope` selected object (requires investigating Arc internals).
- **CORS** — backend must allow Arc's origin. If you see CORS errors in the browser console, add Arc's URL to `allow_origins` in `backend_example.py`.
- **Transaction log requires `AuditLogOn=T`** — if the target TM1 instance has audit logging disabled, the Transaction History action will return empty results. The backend should detect this and surface a helpful message.
- **Consolidated cells have no transactions** — if the user pastes a tuple containing rolled-up elements (like "All Segments"), the agent should route to the root cause drill instead of transaction history.

## Roadmap / Open Questions for Cubewise

To make this plugin truly native, the following Arc capabilities would help. These are good talking points with the Arc product team:

1. **Cube context from active tab** — expose the currently selected cube via a service so plugins don't need a manual cube input.
2. **Cell Reference event hook** — let plugins subscribe to `onCellReferenceOpened(callback)` so the AI action can be triggered directly from the right-click menu.
3. **Cell Reference Copy button** — add a "Copy as JSON" button to the dialog itself; plugins parse JSON instead of TSV.

## File Manifest

| File                  | Purpose                                                 |
| --------------------- | ------------------------------------------------------- |
| `plugin.js`           | AngularJS controller + service + plugin registration    |
| `template.html`       | Plugin UI (paste → review → analyze → result)           |
| `backend_example.py`  | FastAPI endpoint with stub agent responses              |
| `README.md`           | This file                                               |

## License

Internal demo. Not for distribution without permission.
