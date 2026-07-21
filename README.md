# Permissioned Tool-Using Agent Sandbox

**Status:** All 5 phases complete. Tool Registry + Permission Layer, Manual
Agent Loop, LangGraph State Machine with real pause/resume, a Streamlit
approval queue UI, and a FastAPI HTTP layer over the same underlying
functions — plus Docker Compose to run the API and UI together, sharing one
SQLite state store. The system runs entirely free of cost: `ANTHROPIC_API_KEY`
is optional everywhere (Streamlit, the API, and the tests). Without it,
everything runs in a deterministic demo mode that still exercises the full
permission layer, risk taxonomy, pause/approve/execute flow, and audit
trail — the safety-layer story doesn't depend on having a model at all.

## Why this order

The interview story is "I built the safety layer around an agent," not "I
built an agent." That means the permission layer has to be provably correct
*before* an LLM ever calls it — otherwise you can't tell whether a demo
succeeded because the safety layer works, or because the LLM happened to
behave.

## Risk taxonomy

Risk isn't a single number someone eyeballed. Each tool is scored 0–2 on four
independent axes at *registration time*, by a human, in `app/registry.py`:

| Axis | Meaning |
|---|---|
| Reversibility | Can the action be undone? |
| Data sensitivity | Does it touch PII / financial / confidential data? |
| External side-effects | Does it leave the sandbox? |
| Blast radius | Could a mistake affect more than the requester? |

Total score 0–8 maps to a tier:

- **0–2 LOW** → auto-execute
- **3–5 MEDIUM** → execute, but role-gated and flagged in the audit log
- **6–8 HIGH** → blocked pending human approval, regardless of role

Current scoring:

| Tool | Reversibility | Sensitivity | External | Blast radius | Total | Tier |
|---|---|---|---|---|---|---|
| calculator | 0 | 0 | 0 | 0 | 0 | LOW |
| file_reader | 0 | 1 | 0 | 0 | 1 | LOW |
| csv_query | 0 | 2 | 0 | 1 | 3 | MEDIUM |
| mock_ticket_api | 2 | 1 | 2 | 2 | 7 | HIGH |

**The critical design decision:** the LLM's stated `reasoning` for a tool
call is logged for audit purposes but is *never* consulted by the permission
layer. See `tests/test_permissions.py::"high-risk tool call is BLOCKED even
when reasoning field claims prior approval"` — this is a deliberate defense
against prompt injection where the model's own text tries to talk the system
into skipping approval.

## What's actually enforced right now

- **Deny-by-default**: an unregistered tool name is denied, not silently
  ignored or allowed.
- **Role gate before risk gate**: a disallowed role is denied before risk
  tier is even evaluated.
- **Sandbox jail on file access**: `file_reader` and `csv_query` resolve the
  requested path to an absolute path and verify it's a descendant of
  `sandbox_files/`. Path traversal is blocked by path comparison, not
  string matching.
- **No `eval()` anywhere.** `calculator` parses expressions with Python's
  `ast` module and only evaluates a whitelisted set of arithmetic node types.
- **Every attempt is audited**, not just successful ones. `audit_log.jsonl`
  records every permission check, agent planning step, and approval
  decision as line-delimited JSON.
- **The agent genuinely pauses for high-risk actions.** The LangGraph
  checkpointer writes state to SQLite and the Python call stack actually
  returns — there is no thread sitting in a loop waiting. Resuming later
  re-invokes the graph from that saved state, even from a different process.
- **Approval carries a real identity and a real audit trail.** Every
  approval/rejection is tagged with `decided_by` (who), an optional note,
  and a timestamp. If the approver edits the tool's args before approving,
  the history entry flags `args_modified_by_approver=True` and stores the
  args that actually ran — not the agent's original proposal.

## A bug worth mentioning (found by the tests, not by inspection)

Phase 3's first version had `execute_node` call the same `execute_tool_call()`
function Phase 1/2 use, which independently re-checks permission. That meant
a human-approved high-risk action would immediately get re-blocked with
`PENDING_APPROVAL`, because the tool's registry risk score hadn't changed —
only the graph's routing had. `tests/test_graph_agent.py` caught this
immediately (`resumed run actually executed the ticket tool after approval`
failed on first run). Fixed by splitting the executor into
`execute_tool_call()` (checks permission, for Phase 1/2 and any caller
without its own gate) and `dispatch_only()` (no permission check, for the
graph's EXECUTE node specifically, which only reaches that point after
permission was already resolved upstream). See `app/executor.py`'s
docstring for the full reasoning.

## Known gaps (honest, not hidden)

- **No real LLM wired in as the default.** `ClaudePlanner` exists and
  implements the same `Planner` interface as the mock; the Streamlit app and
  the FastAPI `/tasks` endpoint both switch to it automatically if
  `ANTHROPIC_API_KEY` is set, but tests always use deterministic mocks, and
  neither UI requires the key to function.
- **"Demo mode" (no API key) is intentionally narrow, in both the UI and the
  API.** Without a Claude key, you pick one tool + args directly rather than
  describing a task in free text — `SingleToolDemoPlanner`
  (`app/llm/demo_planner.py`) executes that single call and finalizes. It's
  a harness for exercising the full pause/approve/execute path at zero cost,
  not a reasoning agent. Stated explicitly rather than left to look like a
  limited LLM.
- **UI click-through hasn't been verified by an automated browser test** —
  only that the Streamlit process starts without raising an exception, and
  that every function the UI calls has its own passing test. The FastAPI
  layer, by contrast, IS tested end-to-end via `TestClient` in
  `tests/test_api.py`, since HTTP request/response wiring is real,
  automatable surface area in a way Streamlit's click handlers aren't
  without a browser driver.
- **Roles are hardcoded**, not backed by real auth. `decided_by` in the
  approval flow is a free-text field, not an authenticated identity — stated
  plainly in both the UI's sidebar and the API's field description.
- **Audit log is JSONL, not SQLite**, despite the original spec's checklist
  saying "SQLite audit log." Deliberate substitution: line-delimited JSON is
  simpler to append to and grep through for a project this size, and
  `GET /audit-log` gives the same read access a SQL query would. Worth
  naming as a conscious tradeoff, not silently deviating from the spec.
- **No concurrency testing.** Each `thread_id` is independent in the
  checkpointer, but nothing here has been tested under concurrent approval
  requests landing at once, and the FastAPI dev server run via `uvicorn`
  without `--workers` is single-process.
- **Docker Compose is written and verified.** `docker compose up --build`
  brings up both containers successfully: the API's `/health` endpoint
  responds `200 OK` on the healthcheck interval, and Streamlit serves on
  `:8501` (auto-detecting WSL and switching to poll-based file watching).
  Confirmed by running it, not just by the Dockerfiles looking reasonable.

## Running it

```bash
pip install -r requirements.txt
python tests/test_permissions.py     # Phase 1: 13/13 checks
python tests/test_agent_loop.py      # Phase 2: 7/7 checks
python tests/test_graph_agent.py     # Phase 3: 12/12 checks
python tests/test_approval_queue.py  # Phase 4: 9/9 checks
python tests/test_api.py             # Phase 5: 13/13 checks
```

54/54 across all five phases.

Phase 1 covers two adversarial cases (path traversal, injection-via-
reasoning-field) and one non-arithmetic injection attempt against the
calculator. Phase 2 covers a successful multi-step task, a high-risk call
being blocked, and the 8-step cap stopping a runaway planner. Phase 3 covers
the graph genuinely pausing, `get_status()` confirming it from a fresh
connection, resuming from an entirely new SQLite connection (real
restart-survival), and a rejected-approval path that never touches the tool.
Phase 4 covers `list_paused_runs()` finding pending tasks across multiple
threads without knowing their IDs in advance, `decided_by` showing up on the
executed history entry, and an approver's edited args actually being what
runs (not the agent's original proposal). Phase 5 covers the same
scenarios again, but through actual HTTP requests via FastAPI's
`TestClient` — correct status codes (201 created, 404 unknown thread, 409
not-currently-paused), not just correct Python return values.

### Running the Streamlit approval queue

```bash
streamlit run streamlit_app.py
```

Opens at `http://localhost:8501`. Three tabs:

- **Start a task** — pick a role and (in demo mode) a tool + JSON args, or
  (with `ANTHROPIC_API_KEY` set) describe a task in free text for Claude to
  plan.
- **Approval queue** — every currently-paused task, with the tool, args
  (editable before approving), the model's stated reasoning (shown but
  explicitly labeled as carrying no authorization weight), and Approve/
  Reject buttons.
- **Audit log** — the raw `audit_log.jsonl` trail, most recent first,
  expandable per entry.

### Running the API (Phase 5)

```bash
uvicorn app.api:app --reload --port 8000
```

Interactive docs at `http://localhost:8000/docs`. Quick example:

```bash
curl -X POST http://localhost:8000/tasks -H "Content-Type: application/json" \
  -d '{"task":"file a ticket","role":"admin","tool_name":"mock_ticket_api",
       "args":{"title":"issue","body":"details"}}'
# -> 201, paused: true, permission_decision: "requires_approval"

curl http://localhost:8000/approvals
# -> lists the task above

curl -X POST http://localhost:8000/approvals/<thread_id> -H "Content-Type: application/json" \
  -d '{"decision":"approved","decided_by":"you","note":"looks fine"}'
# -> 200, paused: false, ticket actually created
```

### Running everything together with Docker Compose

```bash
docker compose up --build
```

Starts the API on `:8000` and the Streamlit UI on `:8501`, sharing the same
`agent_state.db` and `audit_log.jsonl` via a mounted volume — so a task
started through curl shows up in the Streamlit approval queue and vice
versa. `ANTHROPIC_API_KEY` is read from your shell environment if set;
both services fall back to demo mode if it isn't.

### Running the manual loop (Phase 2) or the graph (Phase 3) directly

```python
from app.graph_runner import start_run, resume_run
from app.llm.mock_planner import ScriptedPlanner
from app.llm.base import PlanStep
from app.schemas import Role

script = [
    PlanStep(tool_name="mock_ticket_api", args={"title": "bug", "body": "..."}, reasoning="file it"),
    PlanStep(tool_name=None, args=None, reasoning="done", final_answer="Ticket filed."),
]
planner = ScriptedPlanner(script)

status = start_run("File a ticket", Role.ADMIN, planner, thread_id="demo-1")
print(status)  # paused == True, permission_decision == "requires_approval"

final = resume_run("demo-1", decision="approved", note="looks fine",
                    planner=planner, decided_by="you@example.com")
print(final)  # paused == False, ticket actually created, decided_by recorded
```

To use the real Claude planner instead of the mock, once you have
`ANTHROPIC_API_KEY` set and `pip install anthropic`:

```python
from app.llm.claude_planner import ClaudePlanner
status = start_run("...", Role.ANALYST, ClaudePlanner(), thread_id="run-1")
```

No other code changes needed — that's the point of the `Planner` interface.

## Project structure
