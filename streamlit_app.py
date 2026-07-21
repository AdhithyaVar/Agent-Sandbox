"""
Phase 4: human approval queue UI. A thin view over graph_runner.py's
start_run / resume_run / list_paused_runs / get_status -- the actual
pause/resume mechanism was built and tested in Phase 3; this file's only
job is to make it operable by a human without writing Python.

Run with: streamlit run streamlit_app.py   (from the project root)
"""
import json
import os
import uuid
from pathlib import Path

import streamlit as st

from app.graph_runner import get_status, list_paused_runs, resume_run, start_run
from app.llm.demo_planner import SingleToolDemoPlanner
from app.registry import list_tools
from app.schemas import Role

SQLITE_PATH = str(Path(__file__).resolve().parent / "agent_state.db")
AUDIT_LOG_PATH = Path(__file__).resolve().parent / "audit_log.jsonl"
HAS_ANTHROPIC_KEY = bool(os.environ.get("ANTHROPIC_API_KEY"))

st.set_page_config(page_title="Agent Sandbox — Approval Queue", layout="wide")


def get_resume_planner(tool_name: str, args: dict):
    """
    Reconstructs a planner suitable for resuming a paused run. If a real
    Claude key is configured, use it (it's naturally stateless -- no issue
    resuming with a fresh instance). Otherwise fall back to the stateless
    demo planner, reconstructed with whatever args are about to execute
    (which may have just been edited by the approver).
    """
    if HAS_ANTHROPIC_KEY:
        from app.llm.claude_planner import ClaudePlanner
        return ClaudePlanner()
    return SingleToolDemoPlanner(tool_name=tool_name, args=args)


st.title("🛡️ Permissioned Tool-Using Agent Sandbox")
st.caption(
    "Every action below goes through the same permission layer and audit log "
    "verified by tests/test_permissions.py, test_graph_agent.py, and "
    "test_approval_queue.py — this UI adds no new authorization logic of its own."
)

if not HAS_ANTHROPIC_KEY:
    st.info(
        "No `ANTHROPIC_API_KEY` set — running in **demo mode**: pick a tool and "
        "args directly rather than describing a task in free text. Set the "
        "env var and restart to use the real Claude planner instead.",
        icon="ℹ️",
    )

with st.sidebar:
    st.subheader("Reviewer identity")
    reviewer_name = st.text_input(
        "Your name / handle",
        value="reviewer",
        help="Recorded as `decided_by` on every approval or rejection you make.",
    )
    st.caption(
        "No real authentication in this system (see README) — this is a "
        "free-text audit label, not a security control."
    )
    st.divider()
    st.subheader("Risk taxonomy")
    for tool in list_tools():
        st.caption(f"**{tool.name}** — {tool.risk.tier.value} (score {tool.risk.total}/8)")

tab_start, tab_queue, tab_audit = st.tabs(
    ["▶️ Start a task", "⏸️ Approval queue", "📜 Audit log"]
)

# ---------------------------------------------------------------- Start tab
with tab_start:
    st.subheader("Start a new task")

    role_choice = st.selectbox("Acting as role", [r.value for r in Role], index=2)

    if HAS_ANTHROPIC_KEY:
        task_text = st.text_area("Task (free text — Claude will plan the steps)",
                                  placeholder="e.g. Compute 12*7 and read notes.txt")
        tool_name, args_dict = None, None
    else:
        task_text = st.text_input("Task description (for the record — demo mode doesn't parse this)",
                                   value="Demo task")
        tool_name = st.selectbox("Tool to call", [t.name for t in list_tools()])
        selected_tool = next(t for t in list_tools() if t.name == tool_name)
        st.caption(f"Risk tier: **{selected_tool.risk.tier.value}** "
                   f"(score {selected_tool.risk.total}/8) — "
                   f"{'will require approval' if selected_tool.requires_approval else 'auto-executes if role permits'}")

        default_args = {
            "calculator": {"expression": "6*7"},
            "file_reader": {"filename": "notes.txt"},
            "csv_query": {"filename": "sample.csv", "limit": 5},
            "mock_ticket_api": {"title": "example issue", "body": "details here"},
        }.get(tool_name, {})
        args_text = st.text_area("Tool args (JSON)", value=json.dumps(default_args, indent=2), height=100)
        try:
            args_dict = json.loads(args_text)
        except json.JSONDecodeError as e:
            st.error(f"Invalid JSON: {e}")
            args_dict = None

    if st.button("Start task", type="primary", disabled=(not HAS_ANTHROPIC_KEY and args_dict is None)):
        thread_id = f"ui-{uuid.uuid4().hex[:8]}"

        if HAS_ANTHROPIC_KEY:
            from app.llm.claude_planner import ClaudePlanner
            planner = ClaudePlanner()
        else:
            planner = SingleToolDemoPlanner(tool_name=tool_name, args=args_dict)

        try:
            status = start_run(task_text, Role(role_choice), planner, thread_id, sqlite_path=SQLITE_PATH)
        except Exception as e:
            st.error(f"Run failed: {e}")
        else:
            st.session_state["last_thread_id"] = thread_id
            if status["paused"]:
                st.warning(
                    f"⏸️ Task `{thread_id}` is **paused**, pending approval for "
                    f"`{status['planned_tool']}` (risk tier: {status['risk_tier']}). "
                    f"Check the **Approval queue** tab.",
                )
            else:
                st.success(f"✅ Task `{thread_id}` finished: {status['final_answer']}")
                with st.expander("Full history"):
                    st.json(status["history"])

# ------------------------------------------------------------- Queue tab
with tab_queue:
    st.subheader("Tasks pending human approval")
    queue = list_paused_runs(sqlite_path=SQLITE_PATH)

    if not queue:
        st.info("No tasks currently pending approval.")
    else:
        for item in queue:
            with st.container(border=True):
                col_main, col_meta = st.columns([3, 1])
                with col_main:
                    st.markdown(f"**Task:** {item['task']}")
                    st.markdown(f"**Tool:** `{item['planned_tool']}`  ·  "
                                f"**Role:** `{item['role']}`  ·  "
                                f"**Thread:** `{item['thread_id']}`")
                    st.markdown(f"**Model's stated reasoning:** _{item['planned_reasoning']}_")
                    st.caption(
                        "This reasoning is logged for audit purposes only — it carries "
                        "no authorization weight. The permission layer never reads it."
                    )
                with col_meta:
                    st.metric("Risk tier", item["risk_tier"])
                    st.caption(item.get("permission_reason", ""))

                args_text = st.text_area(
                    "Args (edit before approving to override what the agent proposed)",
                    value=json.dumps(item["planned_args"], indent=2),
                    key=f"args_{item['thread_id']}",
                    height=100,
                )
                note = st.text_input("Note (optional, logged either way)",
                                      key=f"note_{item['thread_id']}")

                col_approve, col_reject = st.columns(2)

                with col_approve:
                    if st.button("✅ Approve", key=f"approve_{item['thread_id']}", type="primary"):
                        try:
                            parsed_args = json.loads(args_text)
                        except json.JSONDecodeError as e:
                            st.error(f"Invalid JSON in args, cannot approve: {e}")
                        else:
                            modified = parsed_args if parsed_args != item["planned_args"] else None
                            planner = get_resume_planner(item["planned_tool"], parsed_args)
                            result = resume_run(
                                item["thread_id"], decision="approved", note=note,
                                planner=planner, sqlite_path=SQLITE_PATH,
                                decided_by=reviewer_name, modified_args=modified,
                            )
                            st.success(f"Approved. Final answer: {result['final_answer']}")
                            st.rerun()

                with col_reject:
                    if st.button("❌ Reject", key=f"reject_{item['thread_id']}"):
                        planner = get_resume_planner(item["planned_tool"], item["planned_args"])
                        result = resume_run(
                            item["thread_id"], decision="rejected", note=note,
                            planner=planner, sqlite_path=SQLITE_PATH,
                            decided_by=reviewer_name,
                        )
                        st.warning(f"Rejected. Final answer: {result['final_answer']}")
                        st.rerun()

# ------------------------------------------------------------- Audit tab
with tab_audit:
    st.subheader("Recent audit log entries")
    st.caption(f"Reading `{AUDIT_LOG_PATH.name}` — every permission check, agent step, "
               f"and approval decision is logged here as line-delimited JSON, "
               f"including denied and rejected attempts.")

    if not AUDIT_LOG_PATH.exists():
        st.info("No audit log yet — run a task first.")
    else:
        lines = AUDIT_LOG_PATH.read_text(encoding="utf-8").strip().splitlines()
        n = st.slider("Number of recent entries to show", 5, min(200, len(lines)) or 5,
                       value=min(25, len(lines)) or 5)
        for line in reversed(lines[-n:]):
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            event = entry.get("event", "?")
            icon = {
                "permission_check": "🔐", "tool_execution": "⚙️", "graph_step": "🧭",
                "graph_step_cap_reached": "🛑", "dispatch_error": "⚠️",
            }.get(event, "•")
            with st.expander(f"{icon} {event} — {entry.get('logged_at', '')}"):
                st.json(entry)
