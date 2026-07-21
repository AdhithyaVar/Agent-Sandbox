"""
The permission layer. This function is the single choke point every tool
call must pass through. It is 100% deterministic -- no LLM call, no
probabilistic judgment. Given the same (role, tool_name), it always returns
the same decision. That determinism is the entire point: it's the thing an
LLM's plausible-sounding reasoning cannot talk its way around.
"""
from app.registry import get_tool
from app.schemas import (
    PermissionDecision,
    PermissionResult,
    Role,
    ROLE_RANK,
    RiskTier,
)


def check_permission(role: Role, tool_name: str) -> PermissionResult:
    tool = get_tool(tool_name)

    if tool is None:
        return PermissionResult(
            decision=PermissionDecision.DENY,
            reason=f"Unknown tool '{tool_name}' -- not in registry. "
                   f"Deny-by-default: unregistered tools cannot be invoked "
                   f"no matter what the LLM claims about them.",
            tool_name=tool_name,
            role=role,
            risk_tier=RiskTier.HIGH,  # unknown = treat as worst case
        )

    if role not in tool.allowed_roles:
        return PermissionResult(
            decision=PermissionDecision.DENY,
            reason=f"Role '{role.value}' is not in allowed_roles "
                   f"{[r.value for r in tool.allowed_roles]} for tool '{tool_name}'.",
            tool_name=tool_name,
            role=role,
            risk_tier=tool.risk.tier,
        )

    if tool.requires_approval:
        return PermissionResult(
            decision=PermissionDecision.REQUIRES_APPROVAL,
            reason=f"Tool '{tool_name}' is risk tier {tool.risk.tier.value} "
                   f"(score {tool.risk.total}/8) and requires human approval "
                   f"regardless of role.",
            tool_name=tool_name,
            role=role,
            risk_tier=tool.risk.tier,
        )

    return PermissionResult(
        decision=PermissionDecision.ALLOW,
        reason=f"Role '{role.value}' permitted; risk tier "
               f"{tool.risk.tier.value} does not require approval.",
        tool_name=tool_name,
        role=role,
        risk_tier=tool.risk.tier,
    )
