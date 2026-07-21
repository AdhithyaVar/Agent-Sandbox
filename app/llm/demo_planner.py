"""
Stateless demo planner for the Streamlit UI, used when no Claude API key is
configured. ScriptedPlanner (Phase 2/3's mock) advances through an in-memory
list via a Python-side index -- fine for a test process that runs start-to-
finish in one call, but Streamlit reruns the whole script on every button
click, so an object holding "I'm on step 2 of 3" would lose that the moment
the page reruns, UNLESS it were pinned in st.session_state and carefully kept
alive across reruns for the right thread_id. That's solvable but adds a
layer of session-state bookkeeping whose only job is working around
ScriptedPlanner's statefulness -- simpler to just make the demo planner
stateless instead: it decides its next move purely from `history`, so a
brand new instance produces the correct next step every time, exactly like
ClaudePlanner does (which is naturally stateless -- it has no memory between
calls other than the state.json passed to it).

Scope, stated honestly: this executes exactly ONE specified tool call, then
finalizes based on whether it succeeded. It is not a reasoning agent -- it's
a demo harness for exercising the full PLAN -> PERMISSION -> EXECUTE/
APPROVAL_WAIT -> RESPOND path through the real graph without needing an API
key. Real multi-step reasoning is what ClaudePlanner is for.
"""
from app.llm.base import PlanStep
from app.schemas import Role


class SingleToolDemoPlanner:
    def __init__(self, tool_name: str, args: dict, reasoning: str = ""):
        self.tool_name = tool_name
        self.args = args
        self.reasoning = reasoning or (
            f"Demo mode: executing the single tool call the user configured "
            f"({tool_name}) with no further reasoning."
        )

    def plan(self, task: str, role: Role, history: list[dict]) -> PlanStep:
        if len(history) == 0:
            return PlanStep(tool_name=self.tool_name, args=self.args, reasoning=self.reasoning)

        last = history[-1]
        if last.get("success"):
            return PlanStep(
                tool_name=None,
                args=None,
                reasoning="Tool call succeeded; demo task complete.",
                final_answer=f"Done. Result: {last.get('output')}",
            )
        return PlanStep(
            tool_name=None,
            args=None,
            reasoning="Tool call did not succeed; stopping rather than "
                      "blindly retrying the same action.",
            final_answer=f"Could not complete the task: {last.get('error')}",
        )
