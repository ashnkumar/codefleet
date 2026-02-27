"""End-to-end tests for the CodeFleet system.

Tests the full lifecycle against real Elasticsearch:
1. Index creation / verification
2. Seed data ingestion
3. CLI task operations (add, list, status)
4. Runner registration, heartbeat, polling, task execution
5. Activity and file-change logging
6. Conflict detection data flow

Requires a running Elasticsearch instance configured in .env.
Mark with ``pytest -m e2e`` to run only these tests.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

import pytest

from src.config.settings import ElasticClient, get_settings
from src.models import (
    ActivityEvent,
    Agent,
    AgentStatus,
    ChangeType,
    Conflict,
    ConflictStatus,
    ConflictType,
    EventType,
    FileChange,
    Task,
    TaskResult,
    TaskStatus,
)
from src.runners.base import (
    IDX_ACTIVITY,
    IDX_AGENTS,
    IDX_CHANGES,
    IDX_TASKS,
    BaseRunner,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_RUN_ID = uuid.uuid4().hex[:8]

IDX_CONFLICTS = "codefleet-conflicts"
ALL_INDICES = [IDX_TASKS, IDX_AGENTS, IDX_ACTIVITY, IDX_CHANGES, IDX_CONFLICTS]


def _can_connect() -> bool:
    try:
        settings = get_settings()
        return bool(settings.elastic_url and settings.elastic_api_key)
    except Exception:
        return False


pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not _can_connect(), reason="No Elasticsearch connection configured"),
]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def es_client() -> ElasticClient:
    """Create an ElasticClient for this test."""
    return ElasticClient()


# ---------------------------------------------------------------------------
# Stub runners
# ---------------------------------------------------------------------------


class E2EStubRunner(BaseRunner):
    """Runner that returns a canned success result and logs file changes."""

    def __init__(self, name: str):
        super().__init__(name=name, agent_type="claude", capabilities=["python", "testing"])

    async def execute_task(self, task: Task) -> TaskResult:
        # Log a file change like ClaudeRunner does
        await self.report_file_change(
            FileChange(
                agent_id=self.agent_id or "unknown",
                task_id=task.task_id,
                file_path="tests/e2e_stub.py",
                change_type=ChangeType.MODIFIED,
                lines_added=5,
                lines_removed=1,
            )
        )
        return TaskResult(
            success=True,
            summary=f"E2E stub completed: {task.title}",
            files_changed=["tests/e2e_stub.py"],
            tokens_used=100,
            cost_usd=0.001,
            duration_ms=500,
        )


class FailingStubRunner(BaseRunner):
    """Runner that always fails task execution."""

    def __init__(self, name: str):
        super().__init__(name=name, agent_type="claude", capabilities=[])

    async def execute_task(self, task: Task) -> TaskResult:
        return TaskResult(
            success=False,
            summary="Deliberate failure for testing",
            error="E2E test: simulated execution failure",
            duration_ms=100,
        )


# ===========================================================================
# Test: Elasticsearch Indices Exist
# ===========================================================================


class TestIndicesExist:
    @pytest.mark.asyncio
    async def test_all_indices_exist(self, es_client: ElasticClient):
        """All 5 codefleet indices should exist after setup."""
        for index in ALL_INDICES:
            exists = await es_client.raw.indices.exists(index=index)
            assert exists, f"Index {index} does not exist"
        await es_client.close()


# ===========================================================================
# Test: Seed Data Ingestion
# ===========================================================================


class TestSeedData:
    @pytest.mark.asyncio
    async def test_seed_data_loads(self, es_client: ElasticClient):
        """Running seed_all() should populate indices with documents."""
        from data.seed import seed_all

        results = await seed_all(es_client)
        assert results, "seed_all returned empty results"
        assert results.get("codefleet-tasks", 0) > 0
        assert results.get("codefleet-agents", 0) > 0
        assert results.get("codefleet-activity", 0) > 0
        await es_client.close()

    @pytest.mark.asyncio
    async def test_seed_tasks_queryable(self, es_client: ElasticClient):
        """Seeded tasks should be queryable by status."""
        docs = await es_client.search(
            index=IDX_TASKS,
            query={"term": {"status": "pending"}},
            size=50,
        )
        assert len(docs) > 0, "No pending tasks found after seeding"
        await es_client.close()

    @pytest.mark.asyncio
    async def test_seed_agents_queryable(self, es_client: ElasticClient):
        """Seeded agents should be queryable."""
        docs = await es_client.search(index=IDX_AGENTS, size=50)
        assert len(docs) > 0, "No agents found after seeding"
        await es_client.close()

    @pytest.mark.asyncio
    async def test_seed_activity_queryable(self, es_client: ElasticClient):
        """Seeded activity events should be queryable."""
        docs = await es_client.search(index=IDX_ACTIVITY, size=5)
        assert len(docs) > 0, "No activity events found after seeding"
        await es_client.close()

    @pytest.mark.asyncio
    async def test_seed_idempotent(self, es_client: ElasticClient):
        """Running seed twice should not create duplicates."""
        from data.seed import seed_all

        await seed_all(es_client)
        count1_resp = await es_client.raw.count(index=IDX_TASKS)
        count1 = count1_resp.body["count"]

        await seed_all(es_client)
        count2_resp = await es_client.raw.count(index=IDX_TASKS)
        count2 = count2_resp.body["count"]

        assert count1 == count2, f"Seed is not idempotent: {count1} vs {count2}"
        await es_client.close()


# ===========================================================================
# Test: Task CRUD via ElasticClient
# ===========================================================================


class TestTaskCRUD:
    @pytest.mark.asyncio
    async def test_create_and_read_task(self, es_client: ElasticClient):
        """A task indexed to ES should be retrievable by query."""
        task = Task(
            task_id=f"e2e-task-{_RUN_ID}",
            title="E2E test task",
            description="Created by end-to-end test",
            priority=5,
            labels=["e2e-test"],
        )
        await es_client.index_document(
            index=IDX_TASKS,
            doc=task.model_dump(mode="json"),
            doc_id=task.task_id,
        )
        await es_client.raw.indices.refresh(index=IDX_TASKS)

        docs = await es_client.search(
            index=IDX_TASKS,
            query={"term": {"task_id": task.task_id}},
        )
        assert len(docs) == 1
        assert docs[0]["title"] == "E2E test task"
        assert docs[0]["priority"] == 5
        await es_client.close()

    @pytest.mark.asyncio
    async def test_update_task_status(self, es_client: ElasticClient):
        """Updating a task's status should be reflected in ES."""
        task_id = f"e2e-task-{_RUN_ID}"

        # Ensure the task exists first
        existing = await es_client.search(
            index=IDX_TASKS,
            query={"term": {"task_id": task_id}},
        )
        if not existing:
            task = Task(
                task_id=task_id,
                title="E2E test task",
                description="Created by e2e test",
                priority=5,
            )
            await es_client.index_document(
                index=IDX_TASKS,
                doc=task.model_dump(mode="json"),
                doc_id=task_id,
            )
            await es_client.raw.indices.refresh(index=IDX_TASKS)

        await es_client.update_document(
            index=IDX_TASKS,
            doc_id=task_id,
            partial_doc={"status": TaskStatus.ASSIGNED.value, "assigned_to": "e2e-agent"},
        )
        await es_client.raw.indices.refresh(index=IDX_TASKS)

        docs = await es_client.search(
            index=IDX_TASKS,
            query={"term": {"task_id": task_id}},
        )
        assert len(docs) == 1
        assert docs[0]["status"] == "assigned"
        assert docs[0]["assigned_to"] == "e2e-agent"
        await es_client.close()


# ===========================================================================
# Test: Runner Registration and Heartbeat
# ===========================================================================


class TestRunnerLifecycle:
    @pytest.mark.asyncio
    async def test_runner_register_heartbeat_shutdown(self, es_client: ElasticClient):
        """Runner should register, heartbeat, and shutdown correctly."""
        runner = E2EStubRunner(name=f"e2e-runner-{_RUN_ID}")
        runner._es = es_client

        # Register
        agent_id = await runner.register()
        await es_client.raw.indices.refresh(index=IDX_AGENTS)

        docs = await es_client.search(
            index=IDX_AGENTS,
            query={"term": {"agent_id": agent_id}},
        )
        assert len(docs) == 1
        assert docs[0]["name"] == f"e2e-runner-{_RUN_ID}"
        assert docs[0]["status"] == "idle"

        # Heartbeat
        old_hb = docs[0]["last_heartbeat"]
        await asyncio.sleep(1)
        await runner.heartbeat()
        await es_client.raw.indices.refresh(index=IDX_AGENTS)

        new_docs = await es_client.search(
            index=IDX_AGENTS,
            query={"term": {"agent_id": agent_id}},
        )
        assert new_docs[0]["last_heartbeat"] >= old_hb

        # Shutdown
        await runner.shutdown()
        await es_client.raw.indices.refresh(index=IDX_AGENTS)

        final_docs = await es_client.search(
            index=IDX_AGENTS,
            query={"term": {"agent_id": agent_id}},
        )
        assert final_docs[0]["status"] == "offline"
        await es_client.close()


# ===========================================================================
# Test: Task Assignment -> Execution -> Completion Flow
# ===========================================================================


class TestTaskExecutionFlow:
    @pytest.mark.asyncio
    async def test_full_task_lifecycle(self, es_client: ElasticClient):
        """Runner should pick up an assigned task, execute it, and mark complete."""
        # 1. Create a runner
        runner = E2EStubRunner(name=f"e2e-lifecycle-{_RUN_ID}")
        runner._es = es_client
        agent_id = await runner.register()
        await es_client.raw.indices.refresh(index=IDX_AGENTS)

        # 2. Create a task assigned to this runner
        task = Task(
            task_id=f"e2e-lifecycle-task-{_RUN_ID}",
            title="E2E lifecycle test task",
            description="This task tests the full assignment-to-completion flow.",
            status=TaskStatus.ASSIGNED,
            assigned_to=agent_id,
            priority=5,
            labels=["e2e-test"],
        )
        await es_client.index_document(
            index=IDX_TASKS,
            doc=task.model_dump(mode="json"),
            doc_id=task.task_id,
        )
        await es_client.raw.indices.refresh(index=IDX_TASKS)

        # 3. Poll for the task
        found_task = await runner.poll_for_task()
        assert found_task is not None, "Runner did not find assigned task"
        assert found_task.task_id == task.task_id

        # 4. Execute
        await runner._handle_task(found_task)
        await es_client.raw.indices.refresh(index=IDX_TASKS)
        await es_client.raw.indices.refresh(index=IDX_AGENTS)
        await es_client.raw.indices.refresh(index=IDX_ACTIVITY)
        await es_client.raw.indices.refresh(index=IDX_CHANGES)

        # 5. Verify task completed
        task_docs = await es_client.search(
            index=IDX_TASKS,
            query={"term": {"task_id": task.task_id}},
        )
        assert task_docs[0]["status"] == "completed"
        assert "E2E stub completed" in task_docs[0]["result_summary"]

        # 6. Verify agent back to idle
        agent_docs = await es_client.search(
            index=IDX_AGENTS,
            query={"term": {"agent_id": agent_id}},
        )
        assert agent_docs[0]["status"] == "idle"
        assert agent_docs[0]["tasks_completed"] >= 1

        # 7. Verify activity events
        activity_docs = await es_client.search(
            index=IDX_ACTIVITY,
            query={"term": {"task_id": task.task_id}},
            size=50,
        )
        event_types = {d["event_type"] for d in activity_docs}
        assert "task_started" in event_types
        assert "task_completed" in event_types

        # 8. Verify file changes logged
        change_docs = await es_client.search(
            index=IDX_CHANGES,
            query={"term": {"task_id": task.task_id}},
        )
        assert len(change_docs) > 0, "No file changes logged"

        await runner.shutdown()
        await es_client.close()


# ===========================================================================
# Test: Failed Task Flow
# ===========================================================================


class TestFailedTaskFlow:
    @pytest.mark.asyncio
    async def test_failed_task_is_recorded(self, es_client: ElasticClient):
        """A failed task should be marked with error message."""
        runner = FailingStubRunner(name=f"e2e-fail-{_RUN_ID}")
        runner._es = es_client
        agent_id = await runner.register()

        task = Task(
            task_id=f"e2e-fail-task-{_RUN_ID}",
            title="E2E failure test task",
            description="This task is designed to fail.",
            status=TaskStatus.ASSIGNED,
            assigned_to=agent_id,
        )
        await es_client.index_document(
            index=IDX_TASKS,
            doc=task.model_dump(mode="json"),
            doc_id=task.task_id,
        )
        await es_client.raw.indices.refresh(index=IDX_TASKS)

        found_task = await runner.poll_for_task()
        assert found_task is not None
        await runner._handle_task(found_task)
        await es_client.raw.indices.refresh(index=IDX_TASKS)
        await es_client.raw.indices.refresh(index=IDX_AGENTS)
        await es_client.raw.indices.refresh(index=IDX_ACTIVITY)

        # Verify failed
        task_docs = await es_client.search(
            index=IDX_TASKS,
            query={"term": {"task_id": task.task_id}},
        )
        assert task_docs[0]["status"] == "failed"
        assert task_docs[0]["error_message"] is not None

        # Verify agent idle (not stuck)
        agent_docs = await es_client.search(
            index=IDX_AGENTS,
            query={"term": {"agent_id": agent_id}},
        )
        assert agent_docs[0]["status"] == "idle"
        assert agent_docs[0]["tasks_failed"] >= 1

        # Verify failure event
        activity_docs = await es_client.search(
            index=IDX_ACTIVITY,
            query={
                "bool": {
                    "filter": [
                        {"term": {"task_id": task.task_id}},
                        {"term": {"event_type": "task_failed"}},
                    ]
                }
            },
        )
        assert len(activity_docs) > 0

        await runner.shutdown()
        await es_client.close()


# ===========================================================================
# Test: Conflict Detection Data Flow
# ===========================================================================


class TestConflictDetection:
    @pytest.mark.asyncio
    async def test_overlapping_changes_detectable(self, es_client: ElasticClient):
        """Changes to the same file by different agents should be queryable."""
        shared_file = f"src/shared_{_RUN_ID}.py"

        change_a = FileChange(
            agent_id="e2e-agent-a",
            task_id="e2e-task-a",
            file_path=shared_file,
            change_type=ChangeType.MODIFIED,
            lines_added=10,
            lines_removed=2,
        )
        await es_client.index_document(
            index=IDX_CHANGES,
            doc=change_a.model_dump(mode="json"),
            doc_id=change_a.change_id,
        )

        change_b = FileChange(
            agent_id="e2e-agent-b",
            task_id="e2e-task-b",
            file_path=shared_file,
            change_type=ChangeType.MODIFIED,
            lines_added=5,
            lines_removed=3,
        )
        await es_client.index_document(
            index=IDX_CHANGES,
            doc=change_b.model_dump(mode="json"),
            doc_id=change_b.change_id,
        )
        await es_client.raw.indices.refresh(index=IDX_CHANGES)

        docs = await es_client.search(
            index=IDX_CHANGES,
            query={"term": {"file_path": shared_file}},
        )
        assert len(docs) == 2
        agents = {d["agent_id"] for d in docs}
        assert agents == {"e2e-agent-a", "e2e-agent-b"}
        await es_client.close()

    @pytest.mark.asyncio
    async def test_conflict_record_indexable(self, es_client: ElasticClient):
        """A conflict record should be indexable and queryable."""
        conflict = Conflict(
            conflict_id=f"e2e-conflict-{_RUN_ID}",
            agent_ids=["e2e-agent-a", "e2e-agent-b"],
            task_ids=["e2e-task-a", "e2e-task-b"],
            file_paths=[f"src/shared_{_RUN_ID}.py"],
            conflict_type=ConflictType.FILE_OVERLAP,
            status=ConflictStatus.DETECTED,
        )
        await es_client.index_document(
            index=IDX_CONFLICTS,
            doc=conflict.model_dump(mode="json"),
            doc_id=conflict.conflict_id,
        )
        await es_client.raw.indices.refresh(index=IDX_CONFLICTS)

        docs = await es_client.search(
            index=IDX_CONFLICTS,
            query={"term": {"conflict_id": conflict.conflict_id}},
        )
        assert len(docs) == 1
        assert docs[0]["status"] == "detected"
        assert len(docs[0]["agent_ids"]) == 2
        await es_client.close()


# ===========================================================================
# Test: CLI Commands (sync — run outside event loop)
# ===========================================================================


def _reset_es_singleton() -> None:
    """Reset the module-level ES client singleton so CLI tests get a fresh client."""
    import src.config.settings as _settings_mod
    _settings_mod._es_client = None


class TestCLI:
    """Test CLI commands using Click's CliRunner.

    CLI commands use asyncio.run() internally. We reset the ES singleton
    before each test so the new event loop gets a fresh client.
    """

    def test_status_command(self):
        """The 'status' CLI command should run without errors."""
        _reset_es_singleton()
        from click.testing import CliRunner
        from src.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["status"])
        assert result.exit_code == 0, f"status failed: {result.output}\n{result.exception}"
        assert "Agents" in result.output or "Task" in result.output

    def test_list_tasks_command(self):
        """The 'list-tasks' CLI command should return tasks."""
        _reset_es_singleton()
        from click.testing import CliRunner
        from src.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, ["list-tasks", "--limit", "5"])
        assert result.exit_code == 0, f"list-tasks failed: {result.output}\n{result.exception}"

    def test_add_task_command(self):
        """The 'add-task' CLI command should create a task."""
        _reset_es_singleton()
        from click.testing import CliRunner
        from src.cli.main import cli

        runner = CliRunner()
        result = runner.invoke(cli, [
            "add-task",
            "--title", f"CLI test task {_RUN_ID}",
            "--description", "Created by e2e test CLI",
            "--priority", "2",
            "--labels", "e2e-test,cli-test",
        ])
        assert result.exit_code == 0, f"add-task failed: {result.output}\n{result.exception}"
        assert "Task created" in result.output
