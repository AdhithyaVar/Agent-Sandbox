"""
Core data contracts for the agent sandbox.

Everything that crosses a trust boundary (LLM -> tool, tool -> permission layer,
permission layer -> audit log) is a Pydantic model. Nothing untyped moves through
this system.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


class Role(str, Enum):
    VIEWER = "viewer"
    ANALYST = "analyst"
    ADMIN = "admin"


# Ordering matters: used for "role >= analyst" style checks.
ROLE_RANK = {Role.VIEWER: 0, Role.ANALYST: 1, Role.ADMIN: 2}


class RiskAxis(str, Enum):
    REVERSIBILITY = "reversibility"
    DATA_SENSITIVITY = "data_sensitivity"
    EXTERNAL_SIDE_EFFECTS = "external_side_effects"
    BLAST_RADIUS = "blast_radius"


class RiskScore(BaseModel):
    """
    Each axis is scored 0-2 by whoever registers the tool (a human, at
    registration time -- NOT the LLM at call time). The LLM never gets to
    self-report how risky its own action is.
    """
    reversibility: int = Field(ge=0, le=2)
    data_sensitivity: int = Field(ge=0, le=2)
    external_side_effects: int = Field(ge=0, le=2)
    blast_radius: int = Field(ge=0, le=2)

    @property
    def total(self) -> int:
        return (
            self.reversibility
            + self.data_sensitivity
            + self.external_side_effects
            + self.blast_radius
        )

    @property
    def tier(self) -> "RiskTier":
        if self.total <= 2:
            return RiskTier.LOW
        if self.total <= 5:
            return RiskTier.MEDIUM
        return RiskTier.HIGH


class RiskTier(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ToolDefinition(BaseModel):
    """
    Static registration record for a tool. This is written once by a human
    when the tool is added to the registry -- it is the source of truth for
    what a tool is allowed to do, by whom, at what risk.
    """
    name: str
    description: str
    allowed_roles: list[Role]
    risk: RiskScore
    requires_approval: bool  # derived from risk.tier == HIGH, but stored explicitly
                              # so a human can override the derivation with justification
    approval_override_reason: Optional[str] = None

    @field_validator("requires_approval")
    @classmethod
    def validate_override_has_reason(cls, v, info):
        # If a tool's stored requires_approval disagrees with what its risk tier
        # would imply, someone made a deliberate call -- that call must be logged.
        risk = info.data.get("risk")
        if risk is not None:
            implied = risk.tier == RiskTier.HIGH
            if v != implied and not info.data.get("approval_override_reason"):
                raise ValueError(
                    f"requires_approval={v} disagrees with risk tier "
                    f"{risk.tier} (implies {implied}) but no "
                    f"approval_override_reason was given."
                )
        return v


class PermissionDecision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRES_APPROVAL = "requires_approval"


class PermissionResult(BaseModel):
    decision: PermissionDecision
    reason: str
    tool_name: str
    role: Role
    risk_tier: RiskTier
    checked_at: datetime = Field(default_factory=datetime.utcnow)


class ToolCallRequest(BaseModel):
    """What the LLM (or a human, in Phase 1 tests) proposes to do."""
    tool_name: str
    role: Role
    args: dict[str, Any]
    reasoning: str = ""  # the LLM's stated justification -- logged, never trusted


class ToolCallResult(BaseModel):
    tool_name: str
    success: bool
    output: Any = None
    error: Optional[str] = None
