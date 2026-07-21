"""
Executor: the only place tool implementations get called from.

Two entry points, deliberately separate:
  - execute_tool_call(request): checks permission, THEN dispatches if allowed.
    Use this whenever the caller has not already resolved permission itself.
  - dispatch_only(tool_name, args): runs a tool with NO permission check.
    Use this only when an upstream caller has already deterministically
    resolved permission/approval (currently: the LangGraph EXECUTE node,
    app/graph_nodes.py::execute_node, which only reaches this point after
    the graph's own PERMISSION node returned ALLOW or a human explicitly
    approved via APPROVAL_WAIT).

This split exists because of a real bug caught by Phase 3 tests: calling
execute_tool_call() from the graph's execute_node re-ran check_permission()
independently, which re-blocked every high-risk tool immediately after a
human had just approved it -- the tool's registry risk score doesn't change
just because it was approved once. dispatch_only() is the fix: an explicit,
documented "permission already resolved, just run it" path, not a bypass hack.

This is also what an audit trail needs to reconstruct: not just "tool X was
called" but "role R attempted tool X, permission layer said Y because Z, and
the outcome was W" -- for every single attempt, including denied ones.
"""
import json
from datetime import datetime
from pathlib import Path

from app.permissions import check_permission
from app.schemas import (
    PermissionDecision,
    ToolCallRequest,
    ToolCallResult,
)
from app.tools.impl import calculator, csv_query, file_reader, mock_ticket_api

LOG_PATH = Path(__file__).resolve().parents[1] / "audit_log.jsonl"

_DISPATCH = {
    "calculator": calculator.run,
    "file_reader": file_reader.run,
    "csv_query": csv_query.run,
    "mock_ticket_api": mock_ticket_api.run,
}


def _audit(event: dict) -> None:
    event["logged_at"] = datetime.utcnow().isoformat()
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, default=str) + "\n")


def dispatch_only(tool_name: str, args: dict) -> ToolCallResult:
    """Run a tool's implementation and log the outcome, with NO permission
    check. See module docstring for why this exists and who should call it."""
    fn = _DISPATCH.get(tool_name)
    if fn is None:
        result = ToolCallResult(
            tool_name=tool_name,
            success=False,
            error=f"Tool '{tool_name}' has no dispatch implementation "
                  f"-- registry/executor drift.",
        )
        _audit({"event": "dispatch_error", "tool_name": tool_name})
        return result

    try:
        output = fn(args)
        result = ToolCallResult(tool_name=tool_name, success=True, output=output)
    except Exception as e:
        result = ToolCallResult(tool_name=tool_name, success=False, error=str(e))

    _audit(
        {
            "event": "tool_execution",
            "tool_name": tool_name,
            "success": result.success,
            "output": result.output,
            "error": result.error,
        }
    )
    return result


def execute_tool_call(request: ToolCallRequest) -> ToolCallResult:
    """Full path: check permission, then dispatch if and only if allowed.
    Use this for Phase 1/2 and anything without its own permission gate
    upstream."""
    perm = check_permission(request.role, request.tool_name)

    _audit(
        {
            "event": "permission_check",
            "tool_name": request.tool_name,
            "role": request.role.value,
            "args": request.args,
            "reasoning": request.reasoning,
            "decision": perm.decision.value,
            "reason": perm.reason,
            "risk_tier": perm.risk_tier.value,
        }
    )

    if perm.decision == PermissionDecision.DENY:
        return ToolCallResult(
            tool_name=request.tool_name,
            success=False,
            error=f"DENIED: {perm.reason}",
        )

    if perm.decision == PermissionDecision.REQUIRES_APPROVAL:
        # Phase 1/2: no approval queue exists yet, so this is a hard stop.
        # (The graph's real approval flow -- app/graph_nodes.py -- does NOT
        # go through this branch; see dispatch_only()'s docstring.)
        return ToolCallResult(
            tool_name=request.tool_name,
            success=False,
            error=f"PENDING_APPROVAL: {perm.reason} "
                  f"(no approval queue implemented yet -- Phase 4)",
        )

    return dispatch_only(request.tool_name, request.args)
