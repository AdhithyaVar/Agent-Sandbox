"""
Graph state schema. This is exactly what gets serialized into SQLite by the
checkpointer on every step -- which is why it is deliberately plain data
(strings, dicts, lists, primitives) and contains NOTHING that can't survive a
round trip through JSON. The planner (which may hold a live API client) is
never part of this -- see graph_build.py's use of Runtime[AgentContext] for
how it's injected instead.
"""
from __future__ import annotations

from typing import Any, Optional, TypedDict


class AgentState(TypedDict, total=False):
    task: str
    role: str  # Role.value -- stored as plain string, not the enum, for JSON-safety
    history: list[dict]  # list of {tool_name, args, success, output, error}
    step_number: int
    max_steps: int

    # Set by the PLAN node each iteration
    planned_tool: Optional[str]
    planned_args: dict
    planned_reasoning: str
    is_final: bool
    final_answer: Optional[str]

    # Set by the PERMISSION node each iteration
    permission_decision: Optional[str]  # "allow" | "deny" | "requires_approval"
    permission_reason: Optional[str]
    risk_tier: Optional[str]

    # Set by the EXECUTE node
    last_tool_result: Optional[dict]

    # Set when the graph halts for a reason other than a clean final answer
    hit_step_cap: bool

    # Set by APPROVAL_WAIT once a human has responded (None while pending)
    approval_decision: Optional[str]  # "approved" | "rejected" | None
    approval_note: Optional[str]
    decided_by: Optional[str]  # who made the decision -- no real auth yet, so this
                                # is a free-text name/handle supplied at resume time
    args_modified_by_approver: bool  # True if the approver edited planned_args
                                      # before approving (Phase 4's "Modify args")
