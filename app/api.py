"""
Phase 5: FastAPI layer. Same principle as Phase 4's Streamlit app -- this is
a thin transport wrapper around app/graph_runner.py's already-tested
start_run / resume_run / get_status / list_paused_runs. No permission
decision, risk scoring, or approval logic lives here; it lives in
app/permissions.py, app/registry.py, and the graph itself, all covered by
tests/test_permissions.py and tests/test_graph_agent.py already.

Endpoints:
  POST /tasks          submit a task (role + either tool/args for demo mode,
                        or free-text task if ANTHROPIC_API_KEY is set)
  GET  /tasks/{id}      current status + full trace for one task
  GET  /approvals       list of tasks currently paused for human approval
  POST /approvals/{id}  approve or reject a paused task
  GET  /health          liveness check
"""
import json
import os
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app.graph_runner import get_status, list_paused_runs, resume_run, start_run
from app.llm.demo_planner import SingleToolDemoPlanner
from app.registry import list_tools
from app.schemas import Role

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SQLITE_PATH = str(PROJECT_ROOT / "agent_state.db")
AUDIT_LOG_PATH = PROJECT_ROOT / "audit_log.jsonl"
HAS_ANTHROPIC_KEY = bool(os.environ.get("ANTHROPIC_API_KEY"))

app = FastAPI(
    title="Permissioned Tool-Using Agent Sandbox API",
    description=(
        "HTTP interface over the same permission layer, LangGraph state "
        "machine, and audit log used by the Streamlit UI and the test "
        "suite. See the project README for the risk taxonomy and known gaps."
    ),
    version="0.1.0",
)


# --------------------------------------------------------------------------- schemas
class TaskCreateRequest(BaseModel):
    task: str = Field(..., description="Human-readable task description.")
    role: Role
    tool_name: Optional[str] = Field(
        None, description="Required in demo mode (no ANTHROPIC_API_KEY set). "
                           "The single tool this run will call."
    )
    args: Optional[dict[str, Any]] = Field(
        None, description="Required alongside tool_name in demo mode."
    )


class TaskResponse(BaseModel):
    thread_id: str
    paused: bool
    task: Optional[str] = None
    role: Optional[str] = None
    final_answer: Optional[str] = None
    hit_step_cap: bool = False
    step_number: int = 0
    planned_tool: Optional[str] = None
    planned_args: Optional[dict] = None
    planned_reasoning: Optional[str] = None
    permission_decision: Optional[str] = None
    permission_reason: Optional[str] = None
    risk_tier: Optional[str] = None
    history: list[dict] = []


class ApprovalDecisionRequest(BaseModel):
    decision: str = Field(..., pattern="^(approved|rejected)$")
    note: str = ""
    decided_by: str = "api-caller"
    modified_args: Optional[dict[str, Any]] = Field(
        None, description="If given, overrides the tool's args before execution "
                           "(only meaningful when decision='approved')."
    )


def _to_task_response(thread_id: str, status: dict) -> TaskResponse:
    excluded = {"next_node", "thread_id"}  # thread_id passed explicitly; status may
                                             # already carry one too (list_paused_runs does)
    return TaskResponse(thread_id=thread_id, **{k: v for k, v in status.items() if k not in excluded})


def _planner_for(role: Role, tool_name: Optional[str], args: Optional[dict]):
    """Same auto-detect logic as streamlit_app.py's get_resume_planner /
    start-task path: real Claude if a key is configured, otherwise the
    stateless single-tool demo planner."""
    if HAS_ANTHROPIC_KEY:
        from app.llm.claude_planner import ClaudePlanner
        return ClaudePlanner()
    if not tool_name or args is None:
        raise HTTPException(
            status_code=400,
            detail="No ANTHROPIC_API_KEY configured (demo mode) -- tool_name "
                   "and args are required in the request body.",
        )
    return SingleToolDemoPlanner(tool_name=tool_name, args=args)


# --------------------------------------------------------------------------- routes
@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.post("/tasks", response_model=TaskResponse, status_code=201)
def create_task(body: TaskCreateRequest) -> TaskResponse:
    import uuid
    thread_id = f"api-{uuid.uuid4().hex[:8]}"
    planner = _planner_for(body.role, body.tool_name, body.args)

    try:
        status = start_run(body.task, body.role, planner, thread_id, sqlite_path=SQLITE_PATH)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Run failed: {e}")

    return _to_task_response(thread_id, status)


@app.get("/tasks/{thread_id}", response_model=TaskResponse)
def get_task(thread_id: str) -> TaskResponse:
    status = get_status(thread_id, sqlite_path=SQLITE_PATH)
    if status.get("task") is None and not status["history"]:
        # get_status on a thread_id that was never started returns an empty
        # snapshot rather than raising -- surface that as a 404, not a 200
        # with nulls, since "task doesn't exist" and "task exists but is
        # brand new" are different things a caller needs to distinguish.
        raise HTTPException(status_code=404, detail=f"No task found for thread_id '{thread_id}'.")
    return _to_task_response(thread_id, status)


@app.get("/approvals", response_model=list[TaskResponse])
def list_approvals() -> list[TaskResponse]:
    queue = list_paused_runs(sqlite_path=SQLITE_PATH)
    return [_to_task_response(item["thread_id"], item) for item in queue]


@app.post("/approvals/{thread_id}", response_model=TaskResponse)
def decide_approval(thread_id: str, body: ApprovalDecisionRequest) -> TaskResponse:
    current = get_status(thread_id, sqlite_path=SQLITE_PATH)
    if not current["paused"]:
        raise HTTPException(
            status_code=409,
            detail=f"Task '{thread_id}' is not currently paused for approval.",
        )

    planner = _planner_for(
        Role(current["role"]),
        current["planned_tool"],
        body.modified_args or current["planned_args"],
    )

    try:
        status = resume_run(
            thread_id, decision=body.decision, note=body.note, planner=planner,
            sqlite_path=SQLITE_PATH, decided_by=body.decided_by,
            modified_args=body.modified_args,
        )
    except ValueError as e:
        raise HTTPException(status_code=409, detail=str(e))

    return _to_task_response(thread_id, status)


@app.get("/tools")
def get_tools() -> list[dict]:
    """Not in the original checklist, but the UI needs this list and it's
    trivial to expose -- lets an API caller discover valid tool_name values
    and risk tiers without reading the source."""
    return [
        {
            "name": t.name,
            "description": t.description,
            "risk_tier": t.risk.tier.value,
            "risk_score": t.risk.total,
            "requires_approval": t.requires_approval,
            "allowed_roles": [r.value for r in t.allowed_roles],
        }
        for t in list_tools()
    ]


@app.get("/audit-log")
def get_audit_log(limit: int = 50) -> list[dict]:
    """Read-only tail of the audit log, most recent last (same order as the
    file). Not in the original checklist's endpoint list either, but the
    checklist DOES require 'SQLite audit log: every tool call, permission
    decision, approval, final output' -- this is that requirement's read
    path. See README for why the audit log is JSONL rather than a SQLite
    table (deliberate substitution, not a shortcut)."""
    if not AUDIT_LOG_PATH.exists():
        return []
    lines = AUDIT_LOG_PATH.read_text(encoding="utf-8").strip().splitlines()
    entries = []
    for line in lines[-limit:]:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return entries
