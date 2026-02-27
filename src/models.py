"""CodeFleet data models.

All shared Pydantic models for tasks, agents, activity events,
file changes, conflicts, and task results.
"""

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

import uuid


def _uuid() -> str:
    return str(uuid.uuid4())


class TaskStatus(str, Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class AgentStatus(str, Enum):
    IDLE = "idle"
    WORKING = "working"
    PAUSED = "paused"
    OFFLINE = "offline"
    ERROR = "error"


class EventType(str, Enum):
    HEARTBEAT = "heartbeat"
    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    FILE_CHANGED = "file_changed"
    TOOL_CALL = "tool_call"
    CONFLICT_DETECTED = "conflict_detected"
    AGENT_STARTED = "agent_started"
    AGENT_STOPPED = "agent_stopped"
    ERROR = "error"


class ChangeType(str, Enum):
    CREATED = "created"
    MODIFIED = "modified"
    DELETED = "deleted"


class ConflictType(str, Enum):
    FILE_OVERLAP = "file_overlap"
    DEPENDENCY_VIOLATION = "dependency_violation"


class ConflictStatus(str, Enum):
    DETECTED = "detected"
    RESOLVING = "resolving"
    RESOLVED = "resolved"
    ESCALATED = "escalated"


class Complexity(str, Enum):
    TRIVIAL = "trivial"
    SMALL = "small"
    MEDIUM = "medium"
    LARGE = "large"
    XL = "xl"


class Task(BaseModel):
    model_config = {"extra": "ignore"}

    task_id: str = Field(default_factory=_uuid)
    title: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    priority: int = Field(default=3, ge=1, le=5)
    assigned_to: str | None = None
    depends_on: list[str] = Field(default_factory=list)
    blocked_by: list[str] = Field(default_factory=list)
    file_scope: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    branch_name: str | None = None
    pr_url: str | None = None
    result_summary: str | None = None
    error_message: str | None = None
    estimated_complexity: Complexity = Complexity.MEDIUM
    actual_tokens_used: int | None = None
    actual_cost_usd: float | None = None
    actual_duration_ms: int | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    assigned_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

    @field_validator("depends_on", "blocked_by", "file_scope", "labels", mode="before")
    @classmethod
    def _coerce_csv_to_list(cls, v: Any) -> list[str]:
        """Accept comma-separated strings (from ES workflows) or lists."""
        if isinstance(v, list):
            return v
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()] if v else []
        return []

    @field_validator("priority", mode="before")
    @classmethod
    def _coerce_priority(cls, v: Any) -> int:
        """Accept string priorities from workflows (e.g. '5')."""
        if isinstance(v, str):
            try:
                return int(v)
            except ValueError:
                return 3
        return v

    @field_validator("estimated_complexity", mode="before")
    @classmethod
    def _coerce_complexity(cls, v: Any) -> str:
        """Accept any string and default to medium if invalid."""
        if isinstance(v, Complexity):
            return v
        if isinstance(v, str):
            try:
                return Complexity(v)
            except ValueError:
                return Complexity.MEDIUM
        return Complexity.MEDIUM


class Agent(BaseModel):
    agent_id: str = Field(default_factory=_uuid)
    name: str
    type: str = "claude"
    status: AgentStatus = AgentStatus.IDLE
    current_task_id: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    worktree_path: str | None = None
    session_id: str | None = None
    last_heartbeat: datetime = Field(default_factory=datetime.utcnow)
    tasks_completed: int = 0
    tasks_failed: int = 0
    total_tokens_used: int = 0
    total_cost_usd: float = 0.0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ActivityEvent(BaseModel):
    event_id: str = Field(default_factory=_uuid)
    agent_id: str
    task_id: str | None = None
    event_type: EventType
    message: str = ""
    files_changed: list[str] = Field(default_factory=list)
    tool_name: str | None = None
    tokens_used: int | None = None
    cost_usd: float | None = None
    duration_ms: int | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class FileChange(BaseModel):
    change_id: str = Field(default_factory=_uuid)
    agent_id: str
    task_id: str
    file_path: str
    change_type: ChangeType
    branch_name: str | None = None
    commit_sha: str | None = None
    lines_added: int = 0
    lines_removed: int = 0
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class Conflict(BaseModel):
    conflict_id: str = Field(default_factory=_uuid)
    agent_ids: list[str]
    task_ids: list[str]
    file_paths: list[str]
    conflict_type: ConflictType
    status: ConflictStatus = ConflictStatus.DETECTED
    resolution: str | None = None
    detected_at: datetime = Field(default_factory=datetime.utcnow)
    resolved_at: datetime | None = None


class TaskResult(BaseModel):
    success: bool
    summary: str
    files_changed: list[str] = Field(default_factory=list)
    tokens_used: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0
    error: str | None = None
