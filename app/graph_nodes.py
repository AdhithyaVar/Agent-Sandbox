"""
Node implementations for the graph (Phase 3 structure, Phase 4 adds approver
identity + args-modification tracking). Each node is a pure function of
(state, runtime) -> partial state update -- LangGraph merges the returned
dict into state. The planner is pulled from `runtime.context`, never from
`state`, so it never gets checkpointed (see graph_state.py's docstring).
"""
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from langgraph.runtime import Runtime

from app.executor import dispatch_only
from app.graph_state import AgentState
from app.llm.base import Planner
from app.permissions import check_permission
from app.schemas import Role

LOG_PATH = Path(__file__).resolve().parent.parent / "audit_log.jsonl"


@dataclass
class AgentContext:
    """Run-scoped dependencies that must NOT be persisted with graph state."""
    planner: Planner


def _audit(event: dict) -> None:
    event["logged_at"] = datetime.utcnow().isoformat()
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, default=str) + "\n")


def plan_node(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
    planner = runtime.context.planner
    role = Role(state["role"])
    step = planner.plan(task=state["task"], role=role, history=state.get("history", []))

    step_number = state.get("step_number", 0) + 1
    _audit({
        "event": "graph_step",
        "node": "plan",
        "step": step_number,
        "task": state["task"],
        "role": state["role"],
        "planned_tool": step.tool_name,
        "planned_args": step.args,
        "reasoning": step.reasoning,
        "is_final": step.is_final,
    })

    return {
        "step_number": step_number,
        "planned_tool": step.tool_name,
        "planned_args": step.args,
        "planned_reasoning": step.reasoning,
        "is_final": step.is_final,
        "final_answer": step.final_answer,
    }


def permission_node(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
    role = Role(state["role"])
    result = check_permission(role, state["planned_tool"])

    _audit({
        "event": "graph_step",
        "node": "permission",
        "step": state["step_number"],
        "tool_name": state["planned_tool"],
        "decision": result.decision.value,
        "reason": result.reason,
        "risk_tier": result.risk_tier.value,
    })

    return {
        "permission_decision": result.decision.value,
        "permission_reason": result.reason,
        "risk_tier": result.risk_tier.value,
        # Reset any stale approval decision from a previous high-risk attempt
        "approval_decision": None,
        "approval_note": None,
        "decided_by": None,
        "args_modified_by_approver": False,
    }


def execute_node(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
    # By the time we're here, permission_node already returned ALLOW, or
    # approval_wait_node already recorded a human "approved" decision. Either
    # way permission has already been resolved by an upstream node in THIS
    # graph -- re-running check_permission() here would just re-block every
    # high-risk tool immediately after it was approved (its registry entry
    # hasn't changed). See executor.py's dispatch_only() docstring.
    result = dispatch_only(state["planned_tool"], state.get("planned_args", {}))

    entry = {
        "tool_name": state["planned_tool"],
        "args": state.get("planned_args", {}),
        "success": result.success,
        "output": result.output,
        "error": result.error,
    }
    if state.get("decided_by"):
        # This execution happened because a human approved it -- record who,
        # so the audit trail answers "who authorized this specific action"
        # without needing to cross-reference the approval_wait log line.
        entry["decided_by"] = state["decided_by"]
        entry["args_modified_by_approver"] = state.get("args_modified_by_approver", False)

    history = list(state.get("history", []))
    history.append(entry)

    return {"history": history, "last_tool_result": entry}


def denied_node(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
    """Reached when PERMISSION returned DENY. Records the denial as a history
    entry (so the planner sees it on the next PLAN call) without ever
    touching the executor."""
    entry = {
        "tool_name": state["planned_tool"],
        "args": state.get("planned_args", {}),
        "success": False,
        "output": None,
        "error": f"DENIED: {state.get('permission_reason', '')}",
    }
    history = list(state.get("history", []))
    history.append(entry)
    return {"history": history, "last_tool_result": entry}


def approval_wait_node(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
    """
    This node is where the graph is interrupted (see graph_build.py's
    interrupt_before=["approval_wait"]). By the time this function body
    actually runs, a human has already called resume_run() with a decision
    and the graph has been re-invoked -- so this node's job is just to turn
    that decision into a history entry, same as denied_node does for DENY.
    """
    decision = state.get("approval_decision")
    _audit({
        "event": "graph_step",
        "node": "approval_wait",
        "step": state["step_number"],
        "tool_name": state["planned_tool"],
        "approval_decision": decision,
        "approval_note": state.get("approval_note"),
        "decided_by": state.get("decided_by"),
        "args_modified_by_approver": state.get("args_modified_by_approver", False),
        "final_args": state.get("planned_args", {}),  # post-modification, if any
    })

    if decision == "approved":
        # Approval doesn't execute the tool itself -- it just clears the way.
        # EXECUTE still runs next and still goes through dispatch_only() for
        # consistent audit logging. Approval is recorded here; execution and
        # its own log entry happen in execute_node, not here.
        return {}

    # Rejected (or somehow reached with no decision -- treat as rejected,
    # never as a silent approve)
    entry = {
        "tool_name": state["planned_tool"],
        "args": state.get("planned_args", {}),
        "success": False,
        "output": None,
        "error": f"REJECTED_BY_HUMAN: {state.get('approval_note') or 'no reason given'}",
        "decided_by": state.get("decided_by"),
    }
    history = list(state.get("history", []))
    history.append(entry)
    return {"history": history, "last_tool_result": entry}


def step_cap_node(state: AgentState, runtime: Runtime[AgentContext]) -> dict:
    _audit({
        "event": "graph_step_cap_reached",
        "task": state["task"],
        "role": state["role"],
        "step_number": state["step_number"],
    })
    return {
        "hit_step_cap": True,
        "final_answer": f"[stopped: reached {state.get('max_steps')}-step cap without finishing]",
    }
