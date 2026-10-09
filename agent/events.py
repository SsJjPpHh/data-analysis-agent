#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Typed internal events for background jobs.

Business code and ``JobRunner`` create these dataclasses. Persistence adds the
session sequence and timestamp; the HTTP layer is the only place that converts
them to SSE JSON.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, ClassVar, Mapping, TypeAlias


@dataclass(frozen=True)
class _JobEvent:
    job_id: str
    event_type: ClassVar[str]

    def to_payload(self) -> dict[str, Any]:
        return {"type": self.event_type, **asdict(self)}


@dataclass(frozen=True)
class JobCreatedEvent(_JobEvent):
    event_type: ClassVar[str] = "job_created"
    job_type: str
    label: str = ""
    status: str = "created"


@dataclass(frozen=True)
class JobStartedEvent(_JobEvent):
    event_type: ClassVar[str] = "job_started"
    status: str = "running"


@dataclass(frozen=True)
class JobProgressEvent(_JobEvent):
    event_type: ClassVar[str] = "job_progress"
    job_type: str
    progress: int
    message: str = ""
    status: str = "running"


@dataclass(frozen=True)
class ArtifactCreatedEvent(_JobEvent):
    event_type: ClassVar[str] = "artifact_created"
    artifact: Mapping[str, Any]


@dataclass(frozen=True)
class JobDoneEvent(_JobEvent):
    event_type: ClassVar[str] = "job_done"
    result: Any
    status: str = "succeeded"


@dataclass(frozen=True)
class JobErrorEvent(_JobEvent):
    event_type: ClassVar[str] = "job_error"
    error: str
    status: str = "failed"


@dataclass(frozen=True)
class JobCanceledEvent(_JobEvent):
    event_type: ClassVar[str] = "job_canceled"
    status: str = "canceled"


JobEvent: TypeAlias = (
    JobCreatedEvent
    | JobStartedEvent
    | JobProgressEvent
    | ArtifactCreatedEvent
    | JobDoneEvent
    | JobErrorEvent
    | JobCanceledEvent
)


def serialize_event(event: JobEvent | Mapping[str, Any]) -> dict[str, Any]:
    """Convert a typed event (or an already-persisted mapping) to JSON data."""
    if isinstance(event, _JobEvent):
        return event.to_payload()
    return dict(event)
