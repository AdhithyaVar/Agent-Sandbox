"""
Real planner backed by the Claude API. Kept behind the same Planner
interface as the mock -- agent_loop.py never imports this module directly
unless it's actually selected, so tests never require `anthropic` to be
installed or an API key to be set.

NOTE: this asks the model to choose a tool AND state its reasoning, but per
the permission-layer design, `reasoning` is logged and never trusted for
authorization. The model can say whatever it wants here; it doesn't get it
anything.
"""
import json
import os
from typing import Optional

from app.llm.base import PlanStep
from app.registry import list_tools
from app.schemas import Role

_SYSTEM_PROMPT_TEMPLATE = """You are a tool-using agent operating under a permissioned sandbox.
Your role for this task is: {role}

Available tools:
{tool_list}

You must respond with ONLY a JSON object, no other text, no markdown fences.
Two possible shapes:

To call a tool:
{{"action": "call_tool", "tool_name": "<name>", "args": {{...}}, "reasoning": "<why>"}}

To finish the task:
{{"action": "final_answer", "answer": "<your final answer to the user>", "reasoning": "<why you're done>"}}

Rules:
- Only use tools from the list above, with args matching what each tool expects.
- Your `reasoning` is logged for audit purposes but does NOT grant you any
  permission -- a deterministic permission layer decides allow/deny/approval
  independent of what you say here. Do not attempt to claim prior approval,
  override instructions, or assert special authorization in `reasoning` --
  it will be ignored for authorization purposes and only used as an audit trail.
- If a tool call is denied or requires approval, do not repeat the same call.
  Either try a different approach or give a final_answer explaining what
  happened.
"""


def _build_tool_list_text() -> str:
    lines = []
    for tool in list_tools():
        lines.append(f"- {tool.name}: {tool.description} (risk tier: {tool.risk.tier.value})")
    return "\n".join(lines)


class ClaudePlanner:
    def __init__(self, model: str = "claude-sonnet-4-6", api_key: Optional[str] = None):
        try:
            import anthropic
        except ImportError as e:
            raise ImportError(
                "The 'anthropic' package is required for ClaudePlanner. "
                "Install with: pip install anthropic"
            ) from e
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not key:
            raise ValueError(
                "No API key found. Set ANTHROPIC_API_KEY as an environment "
                "variable, or pass api_key= explicitly."
            )
        self._client = anthropic.Anthropic(api_key=key)
        self._model = model

    def plan(self, task: str, role: Role, history: list[dict]) -> PlanStep:
        system = _SYSTEM_PROMPT_TEMPLATE.format(
            role=role.value, tool_list=_build_tool_list_text()
        )

        history_text = "No steps taken yet." if not history else json.dumps(history, indent=2)
        user_message = (
            f"Task: {task}\n\n"
            f"History of tool calls so far:\n{history_text}\n\n"
            f"What is your next action? Respond with ONLY the JSON object."
        )

        response = self._client.messages.create(
            model=self._model,
            max_tokens=1000,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )
        text = "".join(block.text for block in response.content if hasattr(block, "text"))
        text = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()

        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as e:
            # A malformed response is treated as a final answer explaining
            # the failure, not as a crash -- the loop must stay resilient to
            # the model not following the format.
            return PlanStep(
                tool_name=None,
                args=None,
                reasoning=f"Planner returned unparseable JSON: {e}",
                final_answer=f"[planner error: could not parse model response: {text[:200]}]",
            )

        if parsed.get("action") == "final_answer":
            return PlanStep(
                tool_name=None,
                args=None,
                reasoning=parsed.get("reasoning", ""),
                final_answer=parsed.get("answer", ""),
            )

        return PlanStep(
            tool_name=parsed.get("tool_name"),
            args=parsed.get("args", {}),
            reasoning=parsed.get("reasoning", ""),
        )
