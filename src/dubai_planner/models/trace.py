"""Execution trace and observability models for LangGraph agent runs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

StepType = Literal["start", "complete", "tool_call", "revision", "error"]


class AgentEvent(BaseModel):
    """Single observable event emitted by a LangGraph node during execution."""

    event_id: str = Field(default_factory=lambda: uuid4().hex)
    agent_name: str = Field(min_length=1)
    node_name: str = Field(min_length=1)
    step_type: StepType = "complete"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    duration_ms: float | None = Field(default=None, ge=0.0)
    tools_called: list[str] = Field(default_factory=list)
    summary: str = Field(min_length=1)
    inputs_summary: dict[str, Any] = Field(default_factory=dict)
    outputs_summary: dict[str, Any] = Field(default_factory=dict)
    revision_number: int = Field(default=0, ge=0)
    state_diff: dict[str, Any] | None = None


class ExecutionTrace(BaseModel):
    """Ordered collection of agent events and revision metadata for a planner run."""

    run_id: str = Field(default_factory=lambda: uuid4().hex)
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: datetime | None = None
    events: list[AgentEvent] = Field(default_factory=list)
    total_revisions: int = Field(default=0, ge=0)

    def add_event(self, event: AgentEvent) -> None:
        """Append an event and keep total_revisions synchronized."""
        self.events.append(event)
        if event.revision_number > self.total_revisions:
            self.total_revisions = event.revision_number

    def total_duration_ms(self) -> float:
        """Return total recorded duration in milliseconds across all events."""
        return sum(event.duration_ms for event in self.events if event.duration_ms is not None)
