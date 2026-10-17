"""Request and response contracts for a small support-ticket API."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Status(StrEnum):
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CANCELLED = "cancelled"


class Priority(StrEnum):
    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"


class TicketCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=4000)
    priority: Priority = Priority.NORMAL


class StatusChange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Status
    expected_version: int = Field(ge=1, strict=True)


class Ticket(BaseModel):
    id: int
    title: str
    description: str
    priority: Priority
    status: Status
    version: int
    created_at: str
    updated_at: str


class TicketPage(BaseModel):
    items: list[Ticket]
    total: int
    limit: int
    offset: int


class Event(BaseModel):
    id: int
    ticket_id: int
    kind: str
    old_status: Status | None
    new_status: Status
    created_at: str


class Stats(BaseModel):
    total: int
    by_status: dict[str, int]


TRANSITIONS = {
    Status.OPEN: {Status.IN_PROGRESS, Status.CANCELLED},
    Status.IN_PROGRESS: {Status.OPEN, Status.RESOLVED},
    Status.RESOLVED: {Status.IN_PROGRESS},
    Status.CANCELLED: set(),
}
