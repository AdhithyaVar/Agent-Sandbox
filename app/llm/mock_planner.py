"""
Deterministic mock planner. Takes a pre-written script of PlanSteps and
returns them in order, one per call to `plan()`. No randomness, no API call.

This exists to answer one question cleanly: "does the agent LOOP work
correctly?" -- independent of "does the LLM make good decisions?" Those are
two different things to verify, and conflating them (by only ever testing
with a real, non-deterministic model) makes loop bugs hard to reproduce.
"""
from app.llm.base import PlanStep
from app.schemas import Role


class ScriptedPlanner:
    def __init__(self, script: list[PlanStep]):
        self._script = script
        self._index = 0

    def plan(self, task: str, role: Role, history: list[dict]) -> PlanStep:
        if self._index >= len(self._script):
            # Script exhausted without the test giving a final step -- treat
            # as a bug in the test script, not silently loop forever.
            return PlanStep(
                tool_name=None,
                args=None,
                reasoning="Script exhausted with no final_answer provided.",
                final_answer="[mock planner ran out of scripted steps]",
            )
        step = self._script[self._index]
        self._index += 1
        return step
