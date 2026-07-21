"""
Phase 5 verification. Uses FastAPI's TestClient to hit the actual HTTP
endpoints -- not calling graph_runner functions directly (those are already
covered by test_graph_agent.py and test_approval_queue.py). This test's job
is narrower and different: prove the FastAPI wiring itself is correct --
request/response schemas, status codes, route params -- since that's new
code Phase 1-4's tests never touched.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# IMPORTANT: point the API at an isolated test database BEFORE importing the
# app, since app/api.py resolves SQLITE_PATH at import time.
import app.api as api_module

TEST_DB = str(Path(__file__).resolve().parent / "_test_api_state.db")
TEST_LOG = Path(__file__).resolve().parent / "_test_api_audit_log.jsonl"
api_module.SQLITE_PATH = TEST_DB
api_module.AUDIT_LOG_PATH = TEST_LOG

from fastapi.testclient import TestClient  # noqa: E402

client = TestClient(api_module.app)

PASS, FAIL = "PASS", "FAIL"
_results = []


def check(name: str, condition: bool, detail: str = ""):
    status = PASS if condition else FAIL
    _results.append((status, name, detail))
    print(f"[{status}] {name}" + (f" -- {detail}" if detail and status == FAIL else ""))


def main():
    Path(TEST_DB).unlink(missing_ok=True)
    TEST_LOG.unlink(missing_ok=True)
    # executor.py and graph_nodes.py resolve their own LOG_PATH independently
    # of api.py's AUDIT_LOG_PATH (both default to project-root audit_log.jsonl) --
    # that's fine for this test since we only assert on API-level behavior,
    # not on the shared log file's contents.

    # --- /health ---
    r = client.get("/health")
    check("/health returns 200 with status ok", r.status_code == 200 and r.json() == {"status": "ok"},
          f"got {r.status_code} {r.text}")

    # --- /tools ---
    r = client.get("/tools")
    check("/tools lists all 4 registered tools with risk info",
          r.status_code == 200 and len(r.json()) == 4
          and all("risk_tier" in t for t in r.json()),
          f"got {r.status_code} {r.text}")

    # --- POST /tasks: demo mode requires tool_name + args ---
    r = client.post("/tasks", json={"task": "no tool given", "role": "viewer"})
    check("POST /tasks without tool_name in demo mode returns 400, not a crash",
          r.status_code == 400, f"got {r.status_code} {r.text}")

    # --- POST /tasks: low-risk tool auto-executes, no pause ---
    r = client.post("/tasks", json={
        "task": "compute something", "role": "viewer",
        "tool_name": "calculator", "args": {"expression": "6*7"},
    })
    check("POST /tasks with calculator returns 201 and completes (not paused)",
          r.status_code == 201 and r.json()["paused"] is False
          and r.json()["final_answer"] is not None,
          f"got {r.status_code} {r.text}")
    calc_thread_id = r.json()["thread_id"]

    # --- GET /tasks/{id} for a completed task ---
    r = client.get(f"/tasks/{calc_thread_id}")
    check("GET /tasks/{id} retrieves the completed calculator task",
          r.status_code == 200 and len(r.json()["history"]) == 1
          and r.json()["history"][0]["success"] is True,
          f"got {r.status_code} {r.text}")

    # --- GET /tasks/{id} for a nonexistent thread returns 404, not nulls ---
    r = client.get("/tasks/does-not-exist-at-all")
    check("GET /tasks/{id} for unknown thread_id returns 404",
          r.status_code == 404, f"got {r.status_code} {r.text}")

    # --- POST /tasks: high-risk tool pauses for approval ---
    r = client.post("/tasks", json={
        "task": "file an urgent ticket", "role": "admin",
        "tool_name": "mock_ticket_api",
        "args": {"title": "prod issue", "body": "details"},
    })
    check("POST /tasks with mock_ticket_api PAUSES (201, paused=True)",
          r.status_code == 201 and r.json()["paused"] is True
          and r.json()["permission_decision"] == "requires_approval",
          f"got {r.status_code} {r.text}")
    ticket_thread_id = r.json()["thread_id"]

    # --- GET /approvals: the paused task shows up ---
    r = client.get("/approvals")
    check("GET /approvals lists the paused ticket task",
          r.status_code == 200
          and any(item["thread_id"] == ticket_thread_id for item in r.json()),
          f"got {r.status_code} {r.text}")

    # --- POST /approvals/{id}: reject with a bogus thread_id returns 409, not 500 ---
    r = client.post("/approvals/nonexistent-thread", json={"decision": "approved", "note": ""})
    check("POST /approvals/{id} for a non-paused/nonexistent thread returns 409",
          r.status_code == 409, f"got {r.status_code} {r.text}")

    # --- POST /approvals/{id}: reject the real paused task ---
    r = client.post(f"/approvals/{ticket_thread_id}", json={
        "decision": "rejected", "note": "not urgent enough", "decided_by": "test-reviewer",
    })
    check("POST /approvals/{id} rejects successfully, tool never runs",
          r.status_code == 200 and r.json()["paused"] is False
          and "REJECTED_BY_HUMAN" in r.json()["history"][0]["error"],
          f"got {r.status_code} {r.text}")

    # --- Task no longer appears in /approvals after being resolved ---
    r = client.get("/approvals")
    check("resolved task no longer appears in GET /approvals",
          all(item["thread_id"] != ticket_thread_id for item in r.json()),
          f"got {r.json()}")

    # --- POST /tasks + approve with modified_args, end to end through HTTP ---
    r = client.post("/tasks", json={
        "task": "file another ticket", "role": "admin",
        "tool_name": "mock_ticket_api",
        "args": {"title": "original", "body": "x"},
    })
    tid2 = r.json()["thread_id"]
    r = client.post(f"/approvals/{tid2}", json={
        "decision": "approved", "note": "approved with edits", "decided_by": "test-reviewer",
        "modified_args": {"title": "EDITED BY REVIEWER", "body": "x"},
    })
    check("approving with modified_args via HTTP executes the edited args, not the original",
          r.status_code == 200 and r.json()["history"][0]["args"]["title"] == "EDITED BY REVIEWER",
          f"got {r.status_code} {r.text}")
    check("HTTP-level modified approval also records decided_by",
          r.json()["history"][0].get("decided_by") == "test-reviewer",
          f"got {r.json()}")

    # --- Summary ---
    failed = [r for r in _results if r[0] == FAIL]
    print(f"\n{len(_results) - len(failed)}/{len(_results)} checks passed.")
    if failed:
        print("FAILURES:")
        for status_, name, detail in failed:
            print(f"  - {name}: {detail}")
        sys.exit(1)
    print("All Phase 5 API checks passed.")


if __name__ == "__main__":
    main()
