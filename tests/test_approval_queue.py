"""
Phase 4 verification: the pieces the Streamlit UI sits on top of, tested
without a browser.

Covers:
  1. list_paused_runs() finds paused tasks across multiple thread_ids without
     the caller having to know their IDs in advance
  2. list_paused_runs() correctly EXCLUDES threads that already resolved
  3. decided_by is recorded and shows up in the executed tool's history entry
  4. modify-args: an approver can change the tool's args before approving,
     and the audit trail reflects that the executed args differ from the
     agent's original proposal
"""
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.graph_runner import get_status, list_paused_runs, resume_run, start_run
from app.llm.base import PlanStep
from app.llm.mock_planner import ScriptedPlanner
from app.schemas import Role

PASS, FAIL = "PASS", "FAIL"
_results = []
TEST_DB = str(Path(__file__).resolve().parent / "_test_phase4_state.db")


def check(name: str, condition: bool, detail: str = ""):
    status = PASS if condition else FAIL
    _results.append((status, name, detail))
    print(f"[{status}] {name}" + (f" -- {detail}" if detail and status == FAIL else ""))


def fresh_thread_id() -> str:
    return f"p4-{uuid.uuid4()}"


def main():
    Path(TEST_DB).unlink(missing_ok=True)

    # --- Start three tasks: two that pause for approval, one that completes clean ---
    paused_ids = []

    for i in range(2):
        tid = fresh_thread_id()
        planner = ScriptedPlanner([
            PlanStep(tool_name="mock_ticket_api",
                      args={"title": f"issue-{i}", "body": "details"}, reasoning="filing"),
            PlanStep(tool_name=None, args=None, reasoning="done", final_answer=f"Ticket {i} filed."),
        ])
        start_run(f"File ticket {i}", Role.ADMIN, planner, tid, sqlite_path=TEST_DB)
        paused_ids.append(tid)

    clean_tid = fresh_thread_id()
    clean_planner = ScriptedPlanner([
        PlanStep(tool_name="calculator", args={"expression": "2+2"}, reasoning="compute"),
        PlanStep(tool_name=None, args=None, reasoning="done", final_answer="4."),
    ])
    start_run("Compute 2+2", Role.VIEWER, clean_planner, clean_tid, sqlite_path=TEST_DB)

    # --- list_paused_runs finds both paused tasks, without being told the IDs ---
    queue = list_paused_runs(sqlite_path=TEST_DB)
    queue_ids = {item["thread_id"] for item in queue}
    check("list_paused_runs finds both paused tasks", set(paused_ids).issubset(queue_ids),
          f"got {queue_ids}")
    check("list_paused_runs excludes the already-completed task",
          clean_tid not in queue_ids, f"got {queue_ids}")
    check("queue entries carry enough info for a UI to render (task, tool, risk_tier)",
          all(item.get("task") and item.get("planned_tool") and item.get("risk_tier")
              for item in queue if item["thread_id"] in paused_ids),
          f"got {[{'task': i.get('task'), 'tool': i.get('planned_tool')} for i in queue]}")

    # --- decided_by is recorded and appears on the executed history entry ---
    planner_for_first = ScriptedPlanner([
        PlanStep(tool_name=None, args=None, reasoning="done", final_answer="Ticket 0 filed."),
    ])
    resumed = resume_run(paused_ids[0], decision="approved", note="looks routine",
                          planner=planner_for_first, sqlite_path=TEST_DB,
                          decided_by="priya@ops")
    check("resumed run's history entry records who approved it",
          resumed["history"][0].get("decided_by") == "priya@ops",
          f"got {resumed['history']}")
    check("resumed run's history entry shows args were NOT modified",
          resumed["history"][0].get("args_modified_by_approver") is False,
          f"got {resumed['history']}")

    # --- Queue shrinks by one after resolving it ---
    queue_after = list_paused_runs(sqlite_path=TEST_DB)
    queue_after_ids = {item["thread_id"] for item in queue_after}
    check("resolved task no longer appears in the paused queue",
          paused_ids[0] not in queue_after_ids and paused_ids[1] in queue_after_ids,
          f"got {queue_after_ids}")

    # --- Modify-args: approver edits the tool's args before approving ---
    planner_for_second = ScriptedPlanner([
        PlanStep(tool_name=None, args=None, reasoning="done", final_answer="Ticket 1 filed, edited."),
    ])
    original_status = get_status(paused_ids[1], sqlite_path=TEST_DB)
    check("original planned args are what the agent proposed, before any edit",
          original_status["planned_args"] == {"title": "issue-1", "body": "details"},
          f"got {original_status['planned_args']}")

    edited_args = {"title": "issue-1 [ESCALATED]", "body": "details", "priority": "high"}
    resumed2 = resume_run(paused_ids[1], decision="approved", note="approved with edits",
                           planner=planner_for_second, sqlite_path=TEST_DB,
                           decided_by="priya@ops", modified_args=edited_args)
    check("executed args match the APPROVER's edited version, not the agent's original",
          resumed2["history"][0]["args"] == edited_args, f"got {resumed2['history'][0]['args']}")
    check("history entry flags that args were modified by the approver",
          resumed2["history"][0].get("args_modified_by_approver") is True,
          f"got {resumed2['history']}")

    # --- Summary ---
    failed = [r for r in _results if r[0] == FAIL]
    print(f"\n{len(_results) - len(failed)}/{len(_results)} checks passed.")
    if failed:
        print("FAILURES:")
        for status_, name, detail in failed:
            print(f"  - {name}: {detail}")
        sys.exit(1)
    print("All Phase 4 approval-queue checks passed.")


if __name__ == "__main__":
    main()
