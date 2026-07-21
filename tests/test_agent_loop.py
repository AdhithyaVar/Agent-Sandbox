"""
Phase 2 verification, using ScriptedPlanner so the loop's behavior is tested
independent of any real LLM's judgment quality.

Covers the three scenarios called for in the original spec's "Expected
Output" section:
  1. A low-risk multi-step task completes (calculate + read file + summarize)
  2. A high-risk tool attempt gets blocked and the agent reacts to that
  3. The step cap actually stops a runaway loop
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.agent_loop import run_agent
from app.llm.base import PlanStep
from app.llm.mock_planner import ScriptedPlanner
from app.schemas import Role

PASS, FAIL = "PASS", "FAIL"
_results = []


def check(name: str, condition: bool, detail: str = ""):
    status = PASS if condition else FAIL
    _results.append((status, name, detail))
    print(f"[{status}] {name}" + (f" -- {detail}" if detail and status == FAIL else ""))


def main():
    # --- Scenario 1: low-risk multi-step task completes successfully ---
    script = [
        PlanStep(tool_name="calculator", args={"expression": "12 * 4"}, reasoning="compute total"),
        PlanStep(tool_name="file_reader", args={"filename": "notes.txt"}, reasoning="read context"),
        PlanStep(
            tool_name=None, args=None, reasoning="have everything needed",
            final_answer="The total is 48 and the notes file confirms the sandbox setup.",
        ),
    ]
    result = run_agent(task="Compute 12*4 and summarize notes.txt", role=Role.ANALYST,
                        planner=ScriptedPlanner(script))
    check("multi-step task completes with final answer, not step cap",
          not result.hit_step_cap and result.steps_taken == 3, f"got {result}")
    check("history contains both successful tool calls",
          len(result.history) == 2 and all(h["success"] for h in result.history),
          f"got {result.history}")

    # --- Scenario 2: high-risk tool attempt gets blocked, agent reacts ---
    script = [
        PlanStep(
            tool_name="mock_ticket_api",
            args={"title": "bug", "body": "found an issue"},
            reasoning="filing a ticket for the reported bug",
        ),
        PlanStep(
            tool_name=None, args=None,
            reasoning="ticket creation was blocked pending approval, cannot proceed autonomously",
            final_answer="I attempted to create a ticket but it requires human approval. "
                         "Please approve the pending request to proceed.",
        ),
    ]
    result = run_agent(task="File a bug ticket", role=Role.ADMIN, planner=ScriptedPlanner(script))
    check("agent's first attempt was blocked (not silently succeeded)",
          result.history[0]["success"] is False
          and "PENDING_APPROVAL" in result.history[0]["error"],
          f"got {result.history}")
    check("agent gave a final answer explaining the block, did not retry forever",
          not result.hit_step_cap and "approval" in result.final_answer.lower(),
          f"got {result}")

    # --- Scenario 2b: role without permission at all (viewer trying admin tool) ---
    script = [
        PlanStep(tool_name="mock_ticket_api", args={"title": "x", "body": "y"}, reasoning="try anyway"),
        PlanStep(tool_name=None, args=None, reasoning="denied, giving up",
                  final_answer="Not permitted for this role."),
    ]
    result = run_agent(task="File a ticket", role=Role.VIEWER, planner=ScriptedPlanner(script))
    check("viewer's attempt is DENIED outright (not requires_approval)",
          "DENIED" in result.history[0]["error"], f"got {result.history}")

    # --- Scenario 3: step cap actually stops a runaway loop ---
    # A planner that NEVER gives a final answer -- always requests calculator.
    infinite_script = [
        PlanStep(tool_name="calculator", args={"expression": "1+1"}, reasoning="looping forever")
        for _ in range(20)  # more than MAX_STEPS, script won't run out first
    ]
    result = run_agent(task="Never finish", role=Role.VIEWER, planner=ScriptedPlanner(infinite_script))
    check("runaway planner is stopped by the 8-step cap",
          result.hit_step_cap and result.steps_taken == 8, f"got {result}")
    check("step-capped run still returns partial history, not a crash",
          len(result.history) == 8, f"got {len(result.history)} history entries")

    # --- Summary ---
    failed = [r for r in _results if r[0] == FAIL]
    print(f"\n{len(_results) - len(failed)}/{len(_results)} checks passed.")
    if failed:
        print("FAILURES:")
        for status, name, detail in failed:
            print(f"  - {name}: {detail}")
        sys.exit(1)
    print("All Phase 2 agent-loop checks passed.")


if __name__ == "__main__":
    main()
