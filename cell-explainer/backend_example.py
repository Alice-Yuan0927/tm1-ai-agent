"""
Cell Explainer Backend - FastAPI endpoint
=========================================

This is the API contract between the Arc plugin and your existing
MCP-based TM1 agent. Drop this into your existing FastAPI project.

The endpoint receives a tuple + chosen action, calls the appropriate
agent, and returns structured analysis.
"""

from typing import List, Optional, Literal
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import time

app = FastAPI()

# CRITICAL: Arc runs on a different origin (usually http://localhost:7070).
# Without CORS, browser will block the plugin's $http calls.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:7070",   # Arc default
        "http://localhost:8080",   # alternate Arc port
        "*",                       # tighten for production
    ],
    allow_credentials=False,
    allow_methods=["POST", "GET", "OPTIONS"],
    allow_headers=["*"],
)


# ============================================================================
# REQUEST / RESPONSE SCHEMAS
# ============================================================================

class TupleElement(BaseModel):
    dimension: str
    hierarchy: str
    element: str


class CellRequest(BaseModel):
    instance: str
    cube: str
    tuple: List[TupleElement]
    value: Optional[str] = None
    action: Literal[
        "explain_calc",
        "tx_history",
        "compare_baseline",
        "root_cause",
        "followup",
    ]
    baseline: Optional[str] = "Plan"
    question: Optional[str] = None     # for action='followup'


class Driver(BaseModel):
    name: str
    dimension: str
    impact: str          # pre-formatted, e.g. "+1.61M (+10.2%)"


class Transaction(BaseModel):
    time: str
    user: str
    process: Optional[str] = None
    note: Optional[str] = None
    from_value: str
    to_value: str

    class Config:
        fields = {"from_value": "from", "to_value": "to"}   # JSON field aliasing


class TraceInfo(BaseModel):
    tool_calls: int
    input_tokens: int
    output_tokens: int
    latency_ms: int


class CellResponse(BaseModel):
    summary: str                                  # HTML allowed (rendered via ng-bind-html)
    drivers: Optional[List[Driver]] = None
    transactions: Optional[List[dict]] = None     # dict to allow `from`/`to` keys
    followups: Optional[List[str]] = None
    trace: Optional[TraceInfo] = None


# ============================================================================
# ENDPOINT
# ============================================================================

@app.post("/api/explain-cell", response_model=CellResponse)
async def explain_cell(req: CellRequest):
    """
    Main entry point called by the Arc plugin.

    Route to the appropriate agent based on action.
    Each agent uses your MCP tools (search_elements, get_cell_value,
    execute_mdx, get_transaction_log, etc.) to gather context and
    returns structured analysis.
    """

    started = time.time()

    # Build the tuple as TM1py element string: "Elem1,Elem2,Elem3,..."
    element_string = ",".join(t.element for t in req.tuple)

    try:
        if req.action == "explain_calc":
            result = await run_explain_calc_agent(req, element_string)
        elif req.action == "tx_history":
            result = await run_tx_history_agent(req, element_string)
        elif req.action == "compare_baseline":
            result = await run_variance_agent(req, element_string)
        elif req.action == "root_cause":
            result = await run_root_cause_agent(req, element_string)
        elif req.action == "followup":
            result = await run_followup_agent(req, element_string)
        else:
            raise HTTPException(status_code=400, detail=f"Unknown action: {req.action}")

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    # Attach trace metadata
    result["trace"] = {
        "tool_calls": result.get("_tool_calls", 0),
        "input_tokens": result.get("_input_tokens", 0),
        "output_tokens": result.get("_output_tokens", 0),
        "latency_ms": int((time.time() - started) * 1000),
    }

    return result


# ============================================================================
# AGENT STUBS - replace these with calls to your existing MCP agent
# ============================================================================

async def run_variance_agent(req: CellRequest, element_string: str) -> dict:
    """
    Pseudocode for variance analysis. Replace with real agent invocation.

    Typical flow:
      1. tool: get_cell_value(req.cube, element_string)              -> actual
      2. tool: get_cell_value(req.cube, swap_version(element_string, baseline))
                                                                     -> plan/forecast
      3. tool: drill_dimension(req.cube, element_string, "Segment 1")
                                                                     -> contributors
      4. LLM synthesizes summary with citations to the tool results
    """
    # STUB - replace with real agent call
    return {
        "summary": (
            f"<strong>{req.tuple[-2].element}</strong> for "
            f"<strong>{req.tuple[4].element}</strong> in "
            f"<strong>{req.tuple[1].element}-{req.tuple[2].element}</strong> is "
            f"<strong>{req.value}</strong>, which is <strong>17.7% above {req.baseline}</strong>. "
            f"Top contributor: SEG_APAC_RETAIL."
        ),
        "drivers": [
            {"name": "SEG_APAC_RETAIL", "dimension": "Segment 1", "impact": "+1.61M (+10.2%)"},
            {"name": "SEG_EMEA_WHSL",   "dimension": "Segment 1", "impact": "+0.92M (+5.8%)"},
            {"name": "SEG_AMER_DTC",    "dimension": "Segment 1", "impact": "+0.21M (+1.3%)"},
        ],
        "followups": [
            "Show all transactions on this account this quarter",
            "Drill into SEG_APAC_RETAIL by Company",
            "Compare ACT vs FCST for the same cell",
        ],
        "_tool_calls": 5,
        "_input_tokens": 1840,
        "_output_tokens": 612,
    }


async def run_tx_history_agent(req: CellRequest, element_string: str) -> dict:
    """
    Query }tlog cube or TM1 REST Transactions endpoint for this tuple.
    Filter by cube + element coordinates, return chronological list.
    """
    return {
        "summary": "This cell has been modified <strong>2 times in the last 30 days</strong>. "
                   "Most recent change was an FX revaluation by alice.tan.",
        "transactions": [
            {"time": "2025-01-28 14:22", "user": "alice.tan",
             "process": "FX reval batch FX_JAN25_03",
             "from": "15,890,231", "to": "18,262,149"},
            {"time": "2025-01-15 09:08", "user": "consolidation_user",
             "process": "}bedrock.cube.data.copy",
             "from": "0", "to": "15,890,231"},
        ],
        "followups": [
            "Explain the FX reval batch FX_JAN25_03",
            "Show all changes by alice.tan this month",
        ],
        "_tool_calls": 2,
        "_input_tokens": 980,
        "_output_tokens": 340,
    }


async def run_explain_calc_agent(req: CellRequest, element_string: str) -> dict:
    """
    Check if cell is rule-calculated or input. If rule, parse rule text,
    identify driver cells, query their values, return dependency tree.
    """
    return {
        "summary": "This is an <strong>input cell</strong> (no rule applied). "
                   "Value was written directly via TI process. "
                   "Use Transaction History to see who and when.",
        "followups": ["Switch to Transaction History"],
        "_tool_calls": 1,
        "_input_tokens": 420,
        "_output_tokens": 88,
    }


async def run_root_cause_agent(req: CellRequest, element_string: str) -> dict:
    """
    Multi-level drill: pick most decomposable dimension, drill down,
    find biggest contributor, drill again, until stopping criterion met.
    """
    return {
        "summary": "Root cause traced 3 levels deep. The variance ultimately stems from "
                   "<strong>Customer CUST_APAC_001</strong> in segment SEG_APAC_RETAIL, "
                   "where ACT exceeded Plan by 1.4M (87% of the total +1.61M segment variance).",
        "drivers": [
            {"name": "CUST_APAC_001", "dimension": "Customer (L3 drill)", "impact": "+1.40M"},
            {"name": "CUST_APAC_007", "dimension": "Customer (L3 drill)", "impact": "+0.21M"},
        ],
        "followups": [
            "Pull the contract details for CUST_APAC_001",
            "Show CUST_APAC_001 monthly trend",
        ],
        "_tool_calls": 8,
        "_input_tokens": 3200,
        "_output_tokens": 890,
    }


async def run_followup_agent(req: CellRequest, element_string: str) -> dict:
    """
    Free-form follow-up question — pass to general-purpose agent
    with the cell context still in memory.
    """
    return {
        "summary": f"Follow-up: <em>{req.question}</em><br><br>"
                   f"(Wire this to your general agent.)",
        "_tool_calls": 0,
        "_input_tokens": 0,
        "_output_tokens": 0,
    }


# ============================================================================
# Run with: uvicorn backend_example:app --reload --port 8000
# ============================================================================
