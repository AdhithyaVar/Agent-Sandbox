"""
Tool registry: the single source of truth for what tools exist, who can use
them, and how risky they are. This is deliberately NOT dynamic -- tools are
registered by a human editing this file, not by the LLM declaring a new
capability at runtime. That's a security boundary, not an oversight.
"""
from app.schemas import RiskScore, Role, ToolDefinition

REGISTRY: dict[str, ToolDefinition] = {
    "calculator": ToolDefinition(
        name="calculator",
        description="Evaluate a basic arithmetic expression.",
        allowed_roles=[Role.VIEWER, Role.ANALYST, Role.ADMIN],
        risk=RiskScore(
            reversibility=0,        # nothing to reverse, no state change
            data_sensitivity=0,     # no data touched
            external_side_effects=0,
            blast_radius=0,
        ),
        requires_approval=False,
    ),
    "file_reader": ToolDefinition(
        name="file_reader",
        description="Read a file's contents. Restricted to the sandbox directory.",
        allowed_roles=[Role.ANALYST, Role.ADMIN],
        risk=RiskScore(
            reversibility=0,        # read-only, nothing to undo
            data_sensitivity=1,     # sandbox files are non-production but not public
            external_side_effects=0,
            blast_radius=0,
        ),
        requires_approval=False,
    ),
    "csv_query": ToolDefinition(
        name="csv_query",
        description="Run a read-only filter/aggregate query against a sandboxed CSV.",
        allowed_roles=[Role.ANALYST, Role.ADMIN],
        risk=RiskScore(
            reversibility=0,        # read-only
            data_sensitivity=2,     # treat tabular data as potentially sensitive
            external_side_effects=0,
            blast_radius=1,         # a bad query could leak more rows than intended
        ),
        requires_approval=False,   # total=3 -> MEDIUM: flagged + role-gated, not blocked
    ),
    "mock_ticket_api": ToolDefinition(
        name="mock_ticket_api",
        description="Create a support ticket in an external (mocked) ticketing system.",
        allowed_roles=[Role.ADMIN],
        risk=RiskScore(
            reversibility=2,        # tickets aren't cleanly undoable once "sent"
            data_sensitivity=1,     # ticket body may contain user-supplied content
            external_side_effects=2,  # leaves the sandbox
            blast_radius=2,         # visible to a real (mocked) downstream team
        ),
        requires_approval=True,    # total=7 -> HIGH: blocked pending human approval
    ),
}


def get_tool(name: str) -> ToolDefinition | None:
    return REGISTRY.get(name)


def list_tools() -> list[ToolDefinition]:
    return list(REGISTRY.values())
