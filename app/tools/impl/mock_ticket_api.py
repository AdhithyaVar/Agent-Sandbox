"""
Mock ticket API. Simulates an irreversible external side effect (Phase 4
approval queue will gate this). In Phase 1 this function is never called
without going through the permission layer first, which will route it to
REQUIRES_APPROVAL -- this file only defines what happens *after* approval.
"""
import uuid
from datetime import datetime
from pydantic import BaseModel, field_validator

_FAKE_TICKET_STORE: list[dict] = []  # stands in for a real ticketing system


class MockTicketInput(BaseModel):
    title: str
    body: str
    priority: str = "normal"

    @field_validator("title")
    @classmethod
    def title_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("title must not be empty")
        return v


def run(args: dict) -> dict:
    payload = MockTicketInput(**args)
    ticket = {
        "ticket_id": str(uuid.uuid4()),
        "title": payload.title,
        "body": payload.body,
        "priority": payload.priority,
        "created_at": datetime.utcnow().isoformat(),
    }
    _FAKE_TICKET_STORE.append(ticket)
    return ticket
