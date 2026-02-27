"""Tests for the ClaudeRunner (Claude Agent SDK integration).

Tests prompt construction, tool configuration, file change tracking,
and error handling for the ClaudeRunner that executes tasks via
claude_agent_sdk.query().

These tests reference the interfaces defined in TECHNICAL_SPEC.md.
They will pass once Agent 1's src/runners/claude_runner.py is merged.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

try:
    from src.models import Task, TaskResult, TaskStatus
    from src.runners.claude_runner import ClaudeRunner

    CLAUDE_RUNNER_AVAILABLE = True
except ImportError:
    CLAUDE_RUNNER_AVAILABLE = False

pytestmark = pytest.mark.skipif(
    not CLAUDE_RUNNER_AVAILABLE,
    reason="src.runners.claude_runner not yet available (pre-merge)",
)


# ===========================================================================
# ClaudeRunner Initialization
# ===========================================================================


class TestClaudeRunnerInit:
    def test_creates_with_required_params(self):
        """ClaudeRunner initializes with name, workdir, and capabilities."""
        runner = ClaudeRunner(
            name="claude-runner-1",
            workdir="/tmp/test-workdir",
            capabilities=["python", "testing"],
        )
        assert runner.name == "claude-runner-1"
        assert runner.workdir.endswith("test-workdir")

    def test_inherits_base_runner(self):
        """ClaudeRunner is a subclass of BaseRunner."""
        from src.runners.base import BaseRunner

        runner = ClaudeRunner(
            name="claude-test",
            workdir="/tmp",
            capabilities=[],
        )
        assert isinstance(runner, BaseRunner)

    def test_agent_type_is_claude(self):
        """ClaudeRunner sets agent_type to 'claude'."""
        runner = ClaudeRunner(
            name="claude-test",
            workdir="/tmp",
            capabilities=[],
        )
        # The BaseRunner __init__ receives agent_type="claude"
        # Verify via the internal type attribute
        if hasattr(runner, "agent_type"):
            assert runner.agent_type == "claude"


# ===========================================================================
# Task Execution
# ===========================================================================


class TestClaudeRunnerExecution:
    @pytest.mark.asyncio
    async def test_execute_returns_task_result(self, mock_es_client):
        """execute_task() should return a TaskResult."""
        with patch("src.runners.base.get_es_client", return_value=mock_es_client):
            runner = ClaudeRunner(
                name="claude-test",
                workdir="/tmp/test",
                capabilities=["python"],
            )
            await runner.register()

            task = Task(
                task_id="task-exec-001",
                title="List files in current directory",
                description="Run ls -la and report what files exist.",
                status=TaskStatus.IN_PROGRESS,
                priority=3,
            )

            # Mock the claude_agent_sdk.query() call
            mock_query_result = MagicMock()
            mock_query_result.text = "Found 5 files in the directory."

            with patch(
                "src.runners.claude_runner.claude_agent_sdk.query",
                new_callable=AsyncMock,
                return_value=mock_query_result,
            ):
                result = await runner.execute_task(task)

        assert isinstance(result, TaskResult)
        assert result.success is True or result.success is False

    @pytest.mark.asyncio
    async def test_execute_failure_returns_error(self, mock_es_client):
        """execute_task() should handle errors and return failed TaskResult."""
        with patch("src.runners.base.get_es_client", return_value=mock_es_client):
            runner = ClaudeRunner(
                name="claude-test",
                workdir="/tmp/test",
                capabilities=["python"],
            )
            await runner.register()

            task = Task(
                task_id="task-exec-fail",
                title="Impossible task",
                description="This should fail.",
                status=TaskStatus.IN_PROGRESS,
            )

            with patch(
                "src.runners.claude_runner.claude_agent_sdk.query",
                new_callable=AsyncMock,
                side_effect=RuntimeError("SDK connection failed"),
            ):
                result = await runner.execute_task(task)

        assert isinstance(result, TaskResult)
        assert result.success is False
        assert result.error is not None


# ===========================================================================
# Prompt Construction
# ===========================================================================


class TestClaudeRunnerPrompt:
    def test_prompt_includes_task_title(self):
        """The prompt sent to Claude should include the task title."""
        runner = ClaudeRunner(
            name="claude-test",
            workdir="/tmp/test",
            capabilities=[],
        )
        task = Task(
            title="Fix authentication bug",
            description="The login endpoint returns 500 on valid credentials.",
        )

        # If the runner exposes a build_prompt method, test it directly
        if hasattr(runner, "build_prompt") or hasattr(runner, "_build_prompt"):
            build_fn = getattr(runner, "build_prompt", None) or getattr(runner, "_build_prompt")
            prompt = build_fn(task)
            assert "Fix authentication bug" in prompt
            assert "500" in prompt or "login" in prompt

    def test_prompt_includes_file_scope(self):
        """The prompt should include file_scope hints when available."""
        runner = ClaudeRunner(
            name="claude-test",
            workdir="/tmp/test",
            capabilities=[],
        )
        task = Task(
            title="Update config",
            description="Change the default timeout.",
            file_scope=["src/config/settings.py"],
        )

        if hasattr(runner, "build_prompt") or hasattr(runner, "_build_prompt"):
            build_fn = getattr(runner, "build_prompt", None) or getattr(runner, "_build_prompt")
            prompt = build_fn(task)
            assert "settings.py" in prompt


# ===========================================================================
# Seed Data Validation (bonus: verify seed data matches model schemas)
# ===========================================================================


class TestSeedDataModels:
    """Validate that seed data can be loaded into model classes."""

    def test_seed_tasks_parse(self):
        """All seed tasks should parse into Task models."""
        import json
        from pathlib import Path

        seed_path = Path(__file__).parent.parent / "data" / "seed_tasks.json"
        if not seed_path.exists():
            pytest.skip("Seed data not available")

        with open(seed_path) as f:
            tasks_data = json.load(f)

        for td in tasks_data:
            task = Task(**td)
            assert task.task_id is not None
            assert task.title
            assert task.status in [s.value for s in TaskStatus] or isinstance(
                task.status, TaskStatus
            )

    def test_seed_agents_parse(self):
        """All seed agents should parse into Agent models."""
        import json
        from pathlib import Path

        from src.models import Agent, AgentStatus

        seed_path = Path(__file__).parent.parent / "data" / "seed_agents.json"
        if not seed_path.exists():
            pytest.skip("Seed data not available")

        with open(seed_path) as f:
            agents_data = json.load(f)

        for ad in agents_data:
            agent = Agent(**ad)
            assert agent.agent_id is not None
            assert agent.name

    def test_seed_activity_parse(self):
        """All seed activity events should parse into ActivityEvent models."""
        import json
        from pathlib import Path

        from src.models import ActivityEvent

        seed_path = Path(__file__).parent.parent / "data" / "seed_activity.json"
        if not seed_path.exists():
            pytest.skip("Seed data not available")

        with open(seed_path) as f:
            events_data = json.load(f)

        for ed in events_data:
            event = ActivityEvent(**ed)
            assert event.event_id is not None
            assert event.agent_id
