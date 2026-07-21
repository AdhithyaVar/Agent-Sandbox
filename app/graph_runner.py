"""
Public entry points for running the Phase 3/4 graph. Each function opens its
own SqliteSaver connection and closes it before returning -- deliberately,
not for tidiness. That means every call to start_run/resume_run/get_status
proves the state actually round-trips through disk, not just through a
long-lived in-memory connection. If resume_run only worked because the
Python process never restarted, that would be worth knowing before claiming
"paused task survives" anywhere.
"""
import sqlite3

from app.graph_build import build_graph
from app.graph_nodes import AgentContext
from app.llm.base import Planner
from app.schemas import Role


def _status_from_snapshot(snapshot, result: dict | None = None) -> dict:
    values = result if result is not None else snapshot.values
    return {
        "paused": bool(snapshot.next),
        "next_node": snapshot.next,
        "task": values.get("task"),
        "role": values.get("role"),
        "final_answer": values.get("final_answer"),
        "hit_step_cap": values.get("hit_step_cap", False),
        "history": values.get("history", []),
        "step_number": values.get("step_number", 0),
        "planned_tool": values.get("planned_tool"),
        "planned_args": values.get("planned_args"),
        "planned_reasoning": values.get("planned_reasoning"),
        "permission_decision": values.get("permission_decision"),
        "permission_reason": values.get("permission_reason"),
        "risk_tier": values.get("risk_tier"),
    }


def start_run(
    task: str,
    role: Role,
    planner: Planner,
    thread_id: str,
    sqlite_path: str = "agent_state.db",
    max_steps: int = 8,
) -> dict:
    graph, cm = build_graph(sqlite_path)
    try:
        config = {"configurable": {"thread_id": thread_id}}
        initial_state = {
            "task": task,
            "role": role.value,
            "history": [],
            "step_number": 0,
            "max_steps": max_steps,
            "hit_step_cap": False,
        }
        result = graph.invoke(initial_state, config=config, context=AgentContext(planner=planner))
        snapshot = graph.get_state(config)
        return _status_from_snapshot(snapshot, result)
    finally:
        cm.__exit__(None, None, None)


def resume_run(
    thread_id: str,
    decision: str,  # "approved" | "rejected"
    note: str,
    planner: Planner,
    sqlite_path: str = "agent_state.db",
    decided_by: str = "unknown",
    modified_args: dict | None = None,
) -> dict:
    """
    decided_by: free-text name/handle of whoever made the call. There's no
    real auth in this system (roles are hardcoded, see README), so this is
    exactly as trustworthy as whatever the caller passes -- logged for audit
    purposes, same honesty as everything else here.

    modified_args: if given, overrides the tool's args before it runs. This
    is the "Modify args" button from the original spec -- the approver can
    edit what the agent is about to do, not just rubber-stamp it. The change
    is logged (args_modified_by_approver=True) so the audit trail shows the
    agent's original proposal was NOT what actually executed.
    """
    if decision not in ("approved", "rejected"):
        raise ValueError(f"decision must be 'approved' or 'rejected', got {decision!r}")

    graph, cm = build_graph(sqlite_path)
    try:
        config = {"configurable": {"thread_id": thread_id}}
        before = graph.get_state(config)
        if not before.next:
            raise ValueError(
                f"Task '{thread_id}' is not currently paused (next={before.next!r}) "
                f"-- nothing to resume."
            )

        update = {
            "approval_decision": decision,
            "approval_note": note,
            "decided_by": decided_by,
        }
        if modified_args is not None:
            update["planned_args"] = modified_args
            update["args_modified_by_approver"] = True

        graph.update_state(config, update)
        result = graph.invoke(None, config=config, context=AgentContext(planner=planner))
        snapshot = graph.get_state(config)
        return _status_from_snapshot(snapshot, result)
    finally:
        cm.__exit__(None, None, None)


def get_status(thread_id: str, sqlite_path: str = "agent_state.db") -> dict:
    graph, cm = build_graph(sqlite_path)
    try:
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = graph.get_state(config)
        return _status_from_snapshot(snapshot)
    finally:
        cm.__exit__(None, None, None)


def _list_all_thread_ids(sqlite_path: str) -> list[str]:
    """
    Reads thread_ids directly from the checkpoints table via a plain sqlite3
    connection, separate from the LangGraph checkpointer's own connection.
    LangGraph doesn't expose a "list all threads" call, and going around it
    with a raw read-only query is simpler and more honest than faking one
    with an in-memory registry that could drift from what's actually on disk.
    """
    try:
        con = sqlite3.connect(sqlite_path)
        cur = con.cursor()
        cur.execute("SELECT DISTINCT thread_id FROM checkpoints")
        ids = [row[0] for row in cur.fetchall()]
        con.close()
        return ids
    except sqlite3.OperationalError:
        # No checkpoints table yet -- no runs have ever happened against this db.
        return []


def list_paused_runs(sqlite_path: str = "agent_state.db") -> list[dict]:
    """
    Returns one status dict (from _status_from_snapshot) per thread_id that
    is CURRENTLY paused at approval_wait, plus a "thread_id" key on each so
    the caller (Streamlit UI) knows which one to resume. This is what makes
    a real approval queue possible instead of requiring the UI to already
    know every thread_id in advance.
    """
    results = []
    for thread_id in _list_all_thread_ids(sqlite_path):
        status = get_status(thread_id, sqlite_path=sqlite_path)
        if status["paused"]:
            status["thread_id"] = thread_id
            results.append(status)
    return results
