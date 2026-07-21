"""
Planner interface. The agent loop only ever talks to this protocol -- it
never knows or cares whether the plan came from a scripted mock or a real
model. That's what lets Phase 2 be built and fully tested before an API key
exists, and lets the real planner be swapped in later without touching
agent_loop.py at all.
"""
from __future__ import annotations

from typing import Protocol

from app.schemas import Role


class PlanStep:
    """
    A single decision from the planner: either "call this tool" or
    "I'm done, here's the final answer."
    """
    def __init__(
        self,
        tool_name: str | None,
        args: dict | None,
        reasoning: str,
        final_answer: str | None = None,
    ):
        self.tool_name = tool_name
        self.args = args or {}
        self.reasoning = reasoning
        self.final_answer = final_answer  # set instead of tool_name when the agent is done

    @property
    def is_final(self) -> bool:
        return self.final_answer is not None

    def __repr__(self) -> str:
        if self.is_final:
            return f"PlanStep(FINAL: {self.final_answer!r})"
        return f"PlanStep(tool={self.tool_name!r}, args={self.args!r})"


class Planner(Protocol):
    def plan(
        self,
        task: str,
        role: Role,
        history: list[dict],
    ) -> PlanStep:
        """
        Given the original task, the requesting role, and the history of
        (tool, args, result) so far, decide the next action.
        `history` entries look like:
            {"tool_name": ..., "args": ..., "success": ..., "output": ..., "error": ...}
        """
        ...
