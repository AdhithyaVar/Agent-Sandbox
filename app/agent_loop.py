"""
Phase 2: the manual agent loop. Deliberately written as a plain Python
function with an explicit loop -- no LangGraph, no state-machine framework --
so that the mechanics (step counting, history feeding, blocked-call handling)
are fully understood and testable before Phase 3 wraps this same logic in a
graph.

Every iteration is logged to the same audit_log.jsonl the executor writes to,
plus a per-run "agent_step" event so the full loop trajectory is
reconstructable after the fact -- not just the individual tool calls.
"""
import json
from datetime import datetime
from pathlib import Path

from app.executor import execute_tool_call
from app.llm.base import Planner
from app.schemas import Role, ToolCallRequest

MAX_STEPS = 8
LOG_PATH = Path(__file__).resolve().parent.parent / "audit_log.jsonl"  # same file executor.py writes to


class AgentRunResult:
    def __init__(self, final_answer: str, steps_taken: int, history: list[dict], hit_step_cap: bool):
        self.final_answer = final_answer
        self.steps_taken = steps_taken
        self.history = history
        self.hit_step_cap = hit_step_cap

    def __repr__(self) -> str:
        return (
            f"AgentRunResult(steps={self.steps_taken}, "
            f"hit_cap={self.hit_step_cap}, answer={self.final_answer!r})"
        )


def _audit(event: dict) -> None:
    event["logged_at"] = datetime.utcnow().isoformat()
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, default=str) + "\n")


def run_agent(task: str, role: Role, planner: Planner, max_steps: int = MAX_STEPS) -> AgentRunResult:
    history: list[dict] = []

    for step_num in range(1, max_steps + 1):
        plan_step = planner.plan(task=task, role=role, history=history)

        _audit(
            {
                "event": "agent_step",
                "step": step_num,
                "task": task,
                "role": role.value,
                "planned_tool": plan_step.tool_name,
                "planned_args": plan_step.args,
                "reasoning": plan_step.reasoning,
                "is_final": plan_step.is_final,
            }
        )

        if plan_step.is_final:
            return AgentRunResult(
                final_answer=plan_step.final_answer,
                steps_taken=step_num,
                history=history,
                hit_step_cap=False,
            )

        result = execute_tool_call(
            ToolCallRequest(
                tool_name=plan_step.tool_name,
                role=role,
                args=plan_step.args,
                reasoning=plan_step.reasoning,
            )
        )

        history.append(
            {
                "tool_name": plan_step.tool_name,
                "args": plan_step.args,
                "success": result.success,
                "output": result.output,
                "error": result.error,
            }
        )

    # Step cap reached without a final answer -- the loop stops itself
    # rather than running forever. This is a safety property, not an
    # afterthought: an agent that never terminates is its own risk category.
    _audit({"event": "agent_step_cap_reached", "task": task, "role": role.value, "max_steps": max_steps})
    return AgentRunResult(
        final_answer=f"[stopped: reached {max_steps}-step cap without finishing]",
        steps_taken=max_steps,
        history=history,
        hit_step_cap=True,
    )
