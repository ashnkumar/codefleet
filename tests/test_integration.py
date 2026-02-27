"""WI-13: Integration tests against real Elasticsearch.

These tests exercise the full runner lifecycle against a live ES cluster.
They require ELASTIC_URL and ELASTIC_API_KEY to be set in .env.

Run with:  uv run pytest tests/test_integration.py -v
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime

import pytest

from src.config.settings import ElasticClient, Settings, get_settings
from src.models import (
    ActivityEvent,
    Agent,
    AgentStatus,
    EventType,
    FileChange,
    ChangeType,
    Task,
    TaskResult,
    TaskStatus,
)
from src.runners.base import BaseRunner, IDX_AGENTS, IDX_TASKS, IDX_ACTIVITY, IDX_CHANGES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _unique_id() -> str:
    """Short unique prefix for test isolation."""
    return f"inttest-{uuid.uuid4().hex[:8]}"


def _make_es_client() -> ElasticClient:
    """Build an ElasticClient from .env settings."""
    return ElasticClient(get_settings())


class StubRunner(BaseRunner):
    """Minimal concrete runner that records execute_task calls."""

    def __init__(self, name: str) -> None:
        super().__init__(name=name, agent_type="claude", capabilities=["integration-test"])
        self.executed_tasks: list[Task] = []
        self.result_to_return: TaskResult = TaskResult(
            success=True,
            summary="Integration test task completed successfully",
            files_changed=["tests/test_integration.py"],
            tokens_used=42,
            cost_usd=0.001,
            duration_ms=500,
        )

    async def execute_task(self, task: Task) -> TaskResult:
        self.executed_tasks.append(task)
        return self.result_to_return


# ---------------------------------------------------------------------------
# Skip if no ES credentials
# ---------------------------------------------------------------------------

def _es_available() -> bool:
    try:
        s = get_settings()
        return bool(s.elastic_url and s.elastic_api_key)
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _es_available(),
    reason="ELASTIC_URL / ELASTIC_API_KEY not configured",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def es_client():
    """Provide an ElasticClient and close it after the test."""
    client = _make_es_client()
    yield client
    asyncio.get_event_loop().run_until_complete(client.close())


@pytest.fixture
def test_id():
    """Unique prefix per test run to avoid collisions with seed data."""
    return _unique_id()


# ---------------------------------------------------------------------------
# 1. ES connectivity
# ---------------------------------------------------------------------------

class TestESConnectivity:
    """Verify basic Elasticsearch connectivity."""

    @pytest.mark.asyncio
    async def test_cluster_reachable(self):
        """Can we talk to ES at all?"""
        es = _make_es_client()
        try:
            info = await es.raw.info()
            assert info.body.get("cluster_name") or info.body.get("name")
        finally:
            await es.close()

    @pytest.mark.asyncio
    async def test_indices_exist(self):
        """All five codefleet-* indices should exist."""
        es = _make_es_client()
        try:
            for idx in [IDX_TASKS, IDX_AGENTS, IDX_ACTIVITY, IDX_CHANGES, "codefleet-conflicts"]:
                exists = await es.raw.indices.exists(index=idx)
                assert exists.body is True or exists.meta.status == 200, f"{idx} missing"
        finally:
            await es.close()

    @pytest.mark.asyncio
    async def test_seed_data_present(self):
        """Seeded task data should be queryable."""
        es = _make_es_client()
        try:
            resp = await es.raw.count(index=IDX_TASKS)
            assert resp.body["count"] > 0, "No tasks found — seed data missing?"
        finally:
            await es.close()


# ---------------------------------------------------------------------------
# 2. CRUD operations via ElasticClient wrapper
# ---------------------------------------------------------------------------

class TestElasticClientCRUD:
    """Verify the ElasticClient wrapper works against real ES."""

    @pytest.mark.asyncio
    async def test_index_and_search(self, test_id: str):
        """Index a document and retrieve it."""
        es = _make_es_client()
        try:
            task = Task(
                task_id=test_id,
                title=f"Integration test task {test_id}",
                description="This is a temporary task created by the integration test suite.",
                priority=1,
                labels=["integration-test"],
            )
            await es.index_document(
                index=IDX_TASKS,
                doc=task.model_dump(mode="json"),
                doc_id=task.task_id,
            )
            # Refresh so it's immediately searchable
            await es.raw.indices.refresh(index=IDX_TASKS)

            docs = await es.search(
                index=IDX_TASKS,
                query={"term": {"task_id": test_id}},
                size=1,
            )
            assert len(docs) == 1
            assert docs[0]["title"] == task.title
            assert docs[0]["status"] == "pending"
        finally:
            # Clean up
            try:
                await es.raw.delete(index=IDX_TASKS, id=test_id)
            except Exception:
                pass
            await es.close()

    @pytest.mark.asyncio
    async def test_update_document(self, test_id: str):
        """Index then partial-update a document."""
        es = _make_es_client()
        try:
            task = Task(
                task_id=test_id,
                title=f"Update test {test_id}",
                description="Will be updated.",
            )
            await es.index_document(
                index=IDX_TASKS,
                doc=task.model_dump(mode="json"),
                doc_id=task.task_id,
            )
            await es.raw.indices.refresh(index=IDX_TASKS)

            await es.update_document(
                index=IDX_TASKS,
                doc_id=test_id,
                partial_doc={"status": "assigned", "assigned_to": "test-agent"},
            )
            await es.raw.indices.refresh(index=IDX_TASKS)

            docs = await es.search(
                index=IDX_TASKS,
                query={"term": {"task_id": test_id}},
                size=1,
            )
            assert docs[0]["status"] == "assigned"
            assert docs[0]["assigned_to"] == "test-agent"
        finally:
            try:
                await es.raw.delete(index=IDX_TASKS, id=test_id)
            except Exception:
                pass
            await es.close()


# ---------------------------------------------------------------------------
# 3. Runner registration
# ---------------------------------------------------------------------------

class TestRunnerRegistration:
    """Test that a runner can register itself in ES."""

    @pytest.mark.asyncio
    async def test_register_creates_agent_document(self, test_id: str):
        """register() should create an agent doc in codefleet-agents."""
        runner = StubRunner(name=f"test-runner-{test_id}")
        runner._es = _make_es_client()
        try:
            agent_id = await runner.register()
            assert agent_id is not None

            await runner.es.raw.indices.refresh(index=IDX_AGENTS)

            docs = await runner.es.search(
                index=IDX_AGENTS,
                query={"term": {"agent_id": agent_id}},
                size=1,
            )
            assert len(docs) == 1
            assert docs[0]["name"] == runner.name
            assert docs[0]["status"] == "idle"
            assert docs[0]["type"] == "claude"
        finally:
            # Clean up
            try:
                await runner.es.raw.delete(index=IDX_AGENTS, id=agent_id)
            except Exception:
                pass
            await runner.es.close()

    @pytest.mark.asyncio
    async def test_register_logs_activity_event(self, test_id: str):
        """register() should log an agent_started activity event."""
        runner = StubRunner(name=f"test-runner-{test_id}")
        runner._es = _make_es_client()
        try:
            agent_id = await runner.register()
            await runner.es.raw.indices.refresh(index=IDX_ACTIVITY)

            docs = await runner.es.search(
                index=IDX_ACTIVITY,
                query={
                    "bool": {
                        "filter": [
                            {"term": {"agent_id": agent_id}},
                            {"term": {"event_type": "agent_started"}},
                        ]
                    }
                },
                size=5,
            )
            assert len(docs) >= 1
            assert "registered" in docs[0]["message"].lower()
        finally:
            try:
                await runner.es.raw.delete(index=IDX_AGENTS, id=agent_id)
            except Exception:
                pass
            await runner.es.close()


# ---------------------------------------------------------------------------
# 4. Heartbeat
# ---------------------------------------------------------------------------

class TestHeartbeat:
    """Test heartbeat updates the agent document."""

    @pytest.mark.asyncio
    async def test_heartbeat_updates_timestamp(self, test_id: str):
        runner = StubRunner(name=f"test-hb-{test_id}")
        runner._es = _make_es_client()
        try:
            agent_id = await runner.register()
            await runner.es.raw.indices.refresh(index=IDX_AGENTS)

            # Get initial heartbeat
            docs = await runner.es.search(
                index=IDX_AGENTS,
                query={"term": {"agent_id": agent_id}},
                size=1,
            )
            initial_hb = docs[0]["last_heartbeat"]

            # Small delay then heartbeat
            await asyncio.sleep(1.1)
            await runner.heartbeat()
            await runner.es.raw.indices.refresh(index=IDX_AGENTS)

            docs = await runner.es.search(
                index=IDX_AGENTS,
                query={"term": {"agent_id": agent_id}},
                size=1,
            )
            updated_hb = docs[0]["last_heartbeat"]
            assert updated_hb > initial_hb
        finally:
            try:
                await runner.es.raw.delete(index=IDX_AGENTS, id=agent_id)
            except Exception:
                pass
            await runner.es.close()


# ---------------------------------------------------------------------------
# 5. Full task lifecycle: create → assign → poll → execute → complete
# ---------------------------------------------------------------------------

class TestFullTaskLifecycle:
    """End-to-end: assign a task, runner picks it up, executes, completes."""

    @pytest.mark.asyncio
    async def test_assign_poll_execute_complete(self, test_id: str):
        """The core integration test."""
        runner = StubRunner(name=f"test-lifecycle-{test_id}")
        runner._es = _make_es_client()
        es = runner.es

        task_id = f"task-{test_id}"
        agent_id: str | None = None

        try:
            # Step 1: Register the runner
            agent_id = await runner.register()
            await es.raw.indices.refresh(index=IDX_AGENTS)

            # Step 2: Create a task assigned to this runner
            task = Task(
                task_id=task_id,
                title=f"Integration lifecycle test {test_id}",
                description="List the files in the current directory.",
                priority=5,
                status=TaskStatus.ASSIGNED,
                assigned_to=agent_id,
                labels=["integration-test"],
                file_scope=["tests/"],
            )
            await es.index_document(
                index=IDX_TASKS,
                doc=task.model_dump(mode="json"),
                doc_id=task_id,
            )
            await es.raw.indices.refresh(index=IDX_TASKS)

            # Step 3: Runner polls and finds the task
            found = await runner.poll_for_task()
            assert found is not None, "Runner did not find the assigned task"
            assert found.task_id == task_id
            assert found.title == task.title

            # Step 4: Execute the full _handle_task flow
            await runner._handle_task(found)
            await es.raw.indices.refresh(index=IDX_TASKS)
            await es.raw.indices.refresh(index=IDX_AGENTS)
            await es.raw.indices.refresh(index=IDX_ACTIVITY)

            # Step 5: Verify task is completed
            task_docs = await es.search(
                index=IDX_TASKS,
                query={"term": {"task_id": task_id}},
                size=1,
            )
            assert len(task_docs) == 1
            assert task_docs[0]["status"] == "completed"
            assert task_docs[0]["result_summary"] is not None
            assert task_docs[0]["actual_tokens_used"] == 42
            assert task_docs[0]["completed_at"] is not None

            # Step 6: Verify agent is back to idle
            agent_docs = await es.search(
                index=IDX_AGENTS,
                query={"term": {"agent_id": agent_id}},
                size=1,
            )
            assert len(agent_docs) == 1
            assert agent_docs[0]["status"] == "idle"

            # Step 7: Verify activity events were logged
            activity_docs = await es.search(
                index=IDX_ACTIVITY,
                query={
                    "bool": {
                        "filter": [
                            {"term": {"agent_id": agent_id}},
                            {"term": {"task_id": task_id}},
                        ]
                    }
                },
                size=20,
            )
            event_types = {d["event_type"] for d in activity_docs}
            assert "task_started" in event_types, f"Missing task_started in {event_types}"
            assert "task_completed" in event_types, f"Missing task_completed in {event_types}"

            # Step 8: Verify the stub recorded the task
            assert len(runner.executed_tasks) == 1
            assert runner.executed_tasks[0].task_id == task_id

        finally:
            # Clean up test data
            for idx, doc_id in [
                (IDX_TASKS, task_id),
                (IDX_AGENTS, agent_id),
            ]:
                if doc_id:
                    try:
                        await es.raw.delete(index=idx, id=doc_id)
                    except Exception:
                        pass
            # Clean up activity events
            if agent_id:
                try:
                    await es.raw.delete_by_query(
                        index=IDX_ACTIVITY,
                        query={"term": {"agent_id": agent_id}},
                    )
                except Exception:
                    pass
            await es.close()

    @pytest.mark.asyncio
    async def test_task_failure_lifecycle(self, test_id: str):
        """When execute_task returns failure, task should be marked failed."""
        runner = StubRunner(name=f"test-fail-{test_id}")
        runner._es = _make_es_client()
        runner.result_to_return = TaskResult(
            success=False,
            summary="",
            error="Intentional test failure",
        )
        es = runner.es

        task_id = f"task-fail-{test_id}"
        agent_id: str | None = None

        try:
            agent_id = await runner.register()
            await es.raw.indices.refresh(index=IDX_AGENTS)

            task = Task(
                task_id=task_id,
                title=f"Failure lifecycle test {test_id}",
                description="This task should fail.",
                status=TaskStatus.ASSIGNED,
                assigned_to=agent_id,
            )
            await es.index_document(
                index=IDX_TASKS,
                doc=task.model_dump(mode="json"),
                doc_id=task_id,
            )
            await es.raw.indices.refresh(index=IDX_TASKS)

            found = await runner.poll_for_task()
            assert found is not None

            await runner._handle_task(found)
            await es.raw.indices.refresh(index=IDX_TASKS)
            await es.raw.indices.refresh(index=IDX_AGENTS)
            await es.raw.indices.refresh(index=IDX_ACTIVITY)

            # Task should be failed
            task_docs = await es.search(
                index=IDX_TASKS,
                query={"term": {"task_id": task_id}},
                size=1,
            )
            assert task_docs[0]["status"] == "failed"
            assert "Intentional test failure" in task_docs[0]["error_message"]

            # Agent should be idle
            agent_docs = await es.search(
                index=IDX_AGENTS,
                query={"term": {"agent_id": agent_id}},
                size=1,
            )
            assert agent_docs[0]["status"] == "idle"

            # Activity should include task_failed
            activity_docs = await es.search(
                index=IDX_ACTIVITY,
                query={
                    "bool": {
                        "filter": [
                            {"term": {"agent_id": agent_id}},
                            {"term": {"task_id": task_id}},
                            {"term": {"event_type": "task_failed"}},
                        ]
                    }
                },
                size=5,
            )
            assert len(activity_docs) >= 1

        finally:
            for idx, doc_id in [
                (IDX_TASKS, task_id),
                (IDX_AGENTS, agent_id),
            ]:
                if doc_id:
                    try:
                        await es.raw.delete(index=idx, id=doc_id)
                    except Exception:
                        pass
            if agent_id:
                try:
                    await es.raw.delete_by_query(
                        index=IDX_ACTIVITY,
                        query={"term": {"agent_id": agent_id}},
                    )
                except Exception:
                    pass
            await es.close()


# ---------------------------------------------------------------------------
# 6. Shutdown
# ---------------------------------------------------------------------------

class TestShutdown:
    """Test graceful runner shutdown."""

    @pytest.mark.asyncio
    async def test_shutdown_sets_offline(self, test_id: str):
        runner = StubRunner(name=f"test-shutdown-{test_id}")
        runner._es = _make_es_client()
        agent_id: str | None = None

        try:
            agent_id = await runner.register()
            await runner.es.raw.indices.refresh(index=IDX_AGENTS)

            await runner.shutdown()
            await runner.es.raw.indices.refresh(index=IDX_AGENTS)

            docs = await runner.es.search(
                index=IDX_AGENTS,
                query={"term": {"agent_id": agent_id}},
                size=1,
            )
            assert docs[0]["status"] == "offline"

            # Activity should include agent_stopped
            await runner.es.raw.indices.refresh(index=IDX_ACTIVITY)
            activity = await runner.es.search(
                index=IDX_ACTIVITY,
                query={
                    "bool": {
                        "filter": [
                            {"term": {"agent_id": agent_id}},
                            {"term": {"event_type": "agent_stopped"}},
                        ]
                    }
                },
                size=5,
            )
            assert len(activity) >= 1
        finally:
            if agent_id:
                try:
                    await runner.es.raw.delete(index=IDX_AGENTS, id=agent_id)
                    await runner.es.raw.delete_by_query(
                        index=IDX_ACTIVITY,
                        query={"term": {"agent_id": agent_id}},
                    )
                except Exception:
                    pass
            await runner.es.close()


# ---------------------------------------------------------------------------
# 7. CLI add-task (against real ES)
# ---------------------------------------------------------------------------

class TestCLIAddTask:
    """Test the CLI add-task command creates a real task in ES."""

    @pytest.mark.asyncio
    async def test_add_task_via_client(self, test_id: str):
        """Simulates what the CLI does — index a task directly."""
        es = _make_es_client()
        task_id = f"cli-{test_id}"
        try:
            task = Task(
                task_id=task_id,
                title=f"CLI integration test {test_id}",
                description="Task created to test CLI flow.",
                priority=4,
                labels=["cli-test"],
                file_scope=["src/cli/main.py"],
            )
            await es.index_document(
                index=IDX_TASKS,
                doc=task.model_dump(mode="json"),
                doc_id=task_id,
            )
            await es.raw.indices.refresh(index=IDX_TASKS)

            # Verify it's there
            docs = await es.search(
                index=IDX_TASKS,
                query={"term": {"task_id": task_id}},
                size=1,
            )
            assert len(docs) == 1
            assert docs[0]["priority"] == 4
            assert docs[0]["status"] == "pending"
        finally:
            try:
                await es.raw.delete(index=IDX_TASKS, id=task_id)
            except Exception:
                pass
            await es.close()
