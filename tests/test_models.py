"""Tests for CodeFleet data models.

Tests model validation, default values, status enums, and serialization
for all Pydantic models defined in src/models.py.

These tests reference the interfaces defined in TECHNICAL_SPEC.md.
They will pass once Agent 1's src/models.py is merged.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest


# ---------------------------------------------------------------------------
# Import helpers — models will be available after Agent 1 merge
# ---------------------------------------------------------------------------

try:
    from src.models import (
        ActivityEvent,
        Agent,
        AgentStatus,
        Conflict,
        EventType,
        FileChange,
        Task,
        TaskResult,
        TaskStatus,
    )

    MODELS_AVAILABLE = True
except ImportError:
    MODELS_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not MODELS_AVAILABLE,
    reason="src.models not yet available (pre-merge)",
)


# ===========================================================================
# TaskStatus Enum
# ===========================================================================


class TestTaskStatus:
    def test_all_values_exist(self):
        expected = {"pending", "assigned", "in_progress", "completed", "failed", "blocked", "cancelled"}
        actual = {s.value for s in TaskStatus}
        assert actual == expected

    def test_string_conversion(self):
        assert str(TaskStatus.PENDING) == "TaskStatus.PENDING"
        assert TaskStatus.PENDING.value == "pending"

    def test_from_value(self):
        assert TaskStatus("pending") == TaskStatus.PENDING
        assert TaskStatus("completed") == TaskStatus.COMPLETED


# ===========================================================================
# AgentStatus Enum
# ===========================================================================


class TestAgentStatus:
    def test_all_values_exist(self):
        expected = {"idle", "working", "paused", "offline", "error"}
        actual = {s.value for s in AgentStatus}
        assert actual == expected


# ===========================================================================
# EventType Enum
# ===========================================================================


class TestEventType:
    def test_all_values_exist(self):
        expected = {
            "heartbeat", "task_started", "task_completed", "task_failed",
            "file_changed", "tool_call", "conflict_detected",
            "agent_started", "agent_stopped", "error",
        }
        actual = {e.value for e in EventType}
        assert actual == expected


# ===========================================================================
# Task Model
# ===========================================================================


class TestTask:
    def test_create_minimal(self):
        """Task can be created with just title and description."""
        task = Task(title="Fix bug", description="Something is broken")
        assert task.title == "Fix bug"
        assert task.description == "Something is broken"
        assert task.status == TaskStatus.PENDING
        assert task.priority == 3
        assert task.assigned_to is None
        assert task.depends_on == []
        assert task.blocked_by == []
        assert task.file_scope == []
        assert task.labels == []
        assert task.estimated_complexity == "medium"

    def test_create_full(self, sample_task_data):
        """Task can be created with all fields."""
        task = Task(**sample_task_data)
        assert task.task_id == "task-test-001"
        assert task.title == "Fix flaky test in payment module"
        assert task.priority == 4
        assert task.labels == ["bug", "test", "payments"]

    def test_auto_generated_id(self):
        """task_id is auto-generated if not provided."""
        task = Task(title="Test", description="Test desc")
        assert task.task_id is not None
        assert len(task.task_id) > 0

    def test_unique_auto_ids(self):
        """Each auto-generated task_id is unique."""
        t1 = Task(title="Task 1", description="Desc 1")
        t2 = Task(title="Task 2", description="Desc 2")
        assert t1.task_id != t2.task_id

    def test_default_timestamps(self):
        """created_at and updated_at get default timestamps."""
        task = Task(title="Test", description="Test")
        assert task.created_at is not None
        assert task.updated_at is not None
        assert isinstance(task.created_at, datetime)

    def test_optional_fields_none(self):
        """Optional fields default to None."""
        task = Task(title="Test", description="Test")
        assert task.assigned_to is None
        assert task.branch_name is None
        assert task.result_summary is None
        assert task.error_message is None
        assert task.actual_tokens_used is None
        assert task.actual_cost_usd is None
        assert task.actual_duration_ms is None
        assert task.assigned_at is None
        assert task.started_at is None
        assert task.completed_at is None

    def test_serialization_roundtrip(self, sample_task_data):
        """Task can be serialized to dict and deserialized back."""
        task = Task(**sample_task_data)
        data = task.model_dump()
        task2 = Task(**data)
        assert task2.task_id == task.task_id
        assert task2.title == task.title
        assert task2.status == task.status

    def test_json_roundtrip(self, sample_task_data):
        """Task can be serialized to JSON and deserialized back."""
        task = Task(**sample_task_data)
        json_str = task.model_dump_json()
        task2 = Task.model_validate_json(json_str)
        assert task2.task_id == task.task_id

    def test_status_values(self):
        """Task accepts all valid status values."""
        for status in TaskStatus:
            task = Task(title="Test", description="Test", status=status)
            assert task.status == status

    def test_priority_range(self):
        """Task accepts priority values 1-5."""
        for p in range(1, 6):
            task = Task(title="Test", description="Test", priority=p)
            assert task.priority == p

    def test_complexity_values(self):
        """Task accepts valid complexity values."""
        for c in ["trivial", "small", "medium", "large", "xl"]:
            task = Task(title="Test", description="Test", estimated_complexity=c)
            assert task.estimated_complexity == c


# ===========================================================================
# Agent Model
# ===========================================================================


class TestAgent:
    def test_create_minimal(self):
        """Agent can be created with just a name."""
        agent = Agent(name="claude-runner-1")
        assert agent.name == "claude-runner-1"
        assert agent.type == "claude"
        assert agent.status == AgentStatus.IDLE
        assert agent.current_task_id is None
        assert agent.capabilities == []
        assert agent.tasks_completed == 0
        assert agent.tasks_failed == 0
        assert agent.total_tokens_used == 0
        assert agent.total_cost_usd == 0.0

    def test_create_full(self, sample_agent_data):
        """Agent can be created with all fields."""
        agent = Agent(**sample_agent_data)
        assert agent.agent_id == "agent-test-001"
        assert agent.name == "claude-runner-test"
        assert agent.capabilities == ["python", "testing"]

    def test_auto_generated_id(self):
        """agent_id is auto-generated if not provided."""
        agent = Agent(name="test-runner")
        assert agent.agent_id is not None
        assert len(agent.agent_id) > 0

    def test_status_transitions(self):
        """Agent accepts all valid status values."""
        for status in AgentStatus:
            agent = Agent(name="test", status=status)
            assert agent.status == status

    def test_default_heartbeat(self):
        """last_heartbeat gets a default timestamp."""
        agent = Agent(name="test")
        assert agent.last_heartbeat is not None
        assert isinstance(agent.last_heartbeat, datetime)


# ===========================================================================
# ActivityEvent Model
# ===========================================================================


class TestActivityEvent:
    def test_create_minimal(self):
        """ActivityEvent requires agent_id and event_type."""
        event = ActivityEvent(
            agent_id="agent-001",
            event_type=EventType.HEARTBEAT,
        )
        assert event.agent_id == "agent-001"
        assert event.event_type == EventType.HEARTBEAT
        assert event.task_id is None
        assert event.message == ""
        assert event.files_changed == []
        assert event.metadata == {}

    def test_create_full(self, sample_activity_data):
        """ActivityEvent can be created with all fields."""
        event = ActivityEvent(**sample_activity_data)
        assert event.event_id == "evt-test-001"
        assert event.event_type == EventType.TASK_STARTED
        assert event.metadata == {"task_priority": 4}

    def test_auto_generated_id(self):
        """event_id is auto-generated if not provided."""
        event = ActivityEvent(
            agent_id="agent-001",
            event_type=EventType.HEARTBEAT,
        )
        assert event.event_id is not None

    def test_all_event_types(self):
        """ActivityEvent accepts all valid event types."""
        for et in EventType:
            event = ActivityEvent(agent_id="test", event_type=et)
            assert event.event_type == et

    def test_default_timestamp(self):
        """timestamp gets a default value."""
        event = ActivityEvent(
            agent_id="agent-001",
            event_type=EventType.HEARTBEAT,
        )
        assert event.timestamp is not None


# ===========================================================================
# FileChange Model
# ===========================================================================


class TestFileChange:
    def test_create_minimal(self):
        """FileChange requires agent_id, task_id, file_path, and change_type."""
        change = FileChange(
            agent_id="agent-001",
            task_id="task-001",
            file_path="src/main.py",
            change_type="modified",
        )
        assert change.file_path == "src/main.py"
        assert change.change_type == "modified"
        assert change.lines_added == 0
        assert change.lines_removed == 0

    def test_create_full(self, sample_file_change_data):
        """FileChange can be created with all fields."""
        change = FileChange(**sample_file_change_data)
        assert change.change_id == "chg-test-001"
        assert change.lines_added == 15
        assert change.branch_name == "fix/flaky-refund-test"

    def test_change_types(self):
        """FileChange accepts valid change type values."""
        for ct in ["created", "modified", "deleted"]:
            change = FileChange(
                agent_id="a", task_id="t", file_path="f.py", change_type=ct
            )
            assert change.change_type == ct


# ===========================================================================
# Conflict Model
# ===========================================================================


class TestConflict:
    def test_create_minimal(self):
        """Conflict requires agent_ids, task_ids, file_paths, and conflict_type."""
        conflict = Conflict(
            agent_ids=["a1", "a2"],
            task_ids=["t1", "t2"],
            file_paths=["src/main.py"],
            conflict_type="file_overlap",
        )
        assert conflict.status == "detected"
        assert conflict.resolution is None
        assert conflict.resolved_at is None

    def test_create_full(self, sample_conflict_data):
        """Conflict can be created with all fields."""
        conflict = Conflict(**sample_conflict_data)
        assert conflict.conflict_id == "cfl-test-001"
        assert len(conflict.agent_ids) == 2
        assert conflict.conflict_type == "file_overlap"

    def test_conflict_types(self):
        """Conflict accepts valid conflict type values."""
        for ct in ["file_overlap", "dependency_violation"]:
            conflict = Conflict(
                agent_ids=["a1"], task_ids=["t1"],
                file_paths=["f.py"], conflict_type=ct,
            )
            assert conflict.conflict_type == ct

    def test_status_values(self):
        """Conflict accepts valid status values."""
        for s in ["detected", "resolving", "resolved", "escalated"]:
            conflict = Conflict(
                agent_ids=["a1"], task_ids=["t1"],
                file_paths=["f.py"], conflict_type="file_overlap",
                status=s,
            )
            assert conflict.status == s


# ===========================================================================
# TaskResult Model
# ===========================================================================


class TestTaskResult:
    def test_create_success(self, sample_task_result_data):
        """TaskResult for a successful task."""
        result = TaskResult(**sample_task_result_data)
        assert result.success is True
        assert result.error is None
        assert result.tokens_used == 12500
        assert result.cost_usd == 0.05
        assert len(result.files_changed) == 1

    def test_create_failure(self):
        """TaskResult for a failed task."""
        result = TaskResult(
            success=False,
            summary="Task failed due to compilation error",
            error="SyntaxError in src/main.py line 42",
        )
        assert result.success is False
        assert result.error is not None
        assert result.files_changed == []
        assert result.tokens_used == 0

    def test_defaults(self):
        """TaskResult has sensible defaults."""
        result = TaskResult(success=True, summary="Done")
        assert result.files_changed == []
        assert result.tokens_used == 0
        assert result.cost_usd == 0.0
        assert result.duration_ms == 0
        assert result.error is None
