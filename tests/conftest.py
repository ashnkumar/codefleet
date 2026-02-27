"""Shared test fixtures for the CodeFleet test suite.

Provides mock Elasticsearch client, sample model instances,
and test settings for unit and integration testing.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


# ---------------------------------------------------------------------------
# Mock Elasticsearch client
# ---------------------------------------------------------------------------

class MockAsyncElasticsearch:
    """Mock async Elasticsearch client for unit tests.

    Simulates index, search, update, and bulk operations
    using an in-memory document store.
    """

    def __init__(self) -> None:
        self._store: dict[str, dict[str, dict]] = {}
        self._doc_counter = 0

    @property
    def raw(self) -> "MockAsyncElasticsearch":
        """Mimic ElasticClient.raw returning the underlying ES client."""
        return self

    async def index(
        self,
        *,
        index: str,
        id: str | None = None,
        document: dict | None = None,
        body: dict | None = None,
        refresh: str | None = None,
        **kwargs: Any,
    ) -> dict:
        doc = document or body or {}
        if index not in self._store:
            self._store[index] = {}
        if id is None:
            self._doc_counter += 1
            id = f"auto-{self._doc_counter}"
        self._store[index][id] = doc
        resp = MagicMock()
        resp.body = {"_index": index, "_id": id, "result": "created", "_version": 1}
        resp.__getitem__ = lambda s, k: resp.body[k]
        return resp

    async def search(
        self,
        *,
        index: str,
        body: dict | None = None,
        query: dict | None = None,
        size: int = 10,
        **kwargs: Any,
    ) -> dict:
        docs = self._store.get(index, {})
        hits = [
            {"_index": index, "_id": doc_id, "_source": doc}
            for doc_id, doc in docs.items()
        ]
        return {
            "hits": {
                "total": {"value": len(hits), "relation": "eq"},
                "hits": hits[:size],
            }
        }

    async def update(
        self,
        *,
        index: str,
        id: str,
        doc: dict | None = None,
        body: dict | None = None,
        script: dict | None = None,
        refresh: str | None = None,
        **kwargs: Any,
    ) -> MagicMock:
        partial = doc or (body.get("doc") if body else None) or {}
        if index in self._store and id in self._store[index]:
            self._store[index][id].update(partial)
        # Return a mock that supports .body attribute (like real ES response)
        resp = MagicMock()
        resp.body = {"_index": index, "_id": id, "result": "updated"}
        resp.__getitem__ = lambda s, k: resp.body[k]
        return resp

    async def bulk(
        self,
        *,
        operations: list[dict],
        refresh: str | None = None,
        **kwargs: Any,
    ) -> dict:
        items = []
        i = 0
        while i < len(operations):
            action = operations[i]
            if "index" in action:
                meta = action["index"]
                idx = meta["_index"]
                doc_id = meta.get("_id")
                i += 1
                doc = operations[i] if i < len(operations) else {}
                if idx not in self._store:
                    self._store[idx] = {}
                if doc_id is None:
                    self._doc_counter += 1
                    doc_id = f"auto-{self._doc_counter}"
                self._store[idx][doc_id] = doc
                items.append({"index": {"_index": idx, "_id": doc_id, "status": 201}})
            i += 1
        return {"errors": False, "items": items}

    # ---------------------------------------------------------------
    # ElasticClient-compatible interface (used by runners)
    # These methods match src.config.settings.ElasticClient signatures
    # ---------------------------------------------------------------

    async def index_document(
        self, index: str, doc: dict, doc_id: str | None = None
    ) -> dict:
        return await self.index(index=index, id=doc_id, document=doc)

    async def update_document(
        self, index: str, doc_id: str, partial_doc: dict
    ) -> dict:
        return await self.update(index=index, id=doc_id, doc=partial_doc)

    async def bulk_index(self, index: str, docs: list[dict]) -> dict:
        operations: list[dict] = []
        for doc in docs:
            operations.append({"index": {"_index": index}})
            operations.append(doc)
        return await self.bulk(operations=operations)

    async def search_raw(self, **kwargs: Any) -> dict:
        """Raw search returning full ES response (for direct ES client compat)."""
        return await self.search(**kwargs)

    # Override search to return ElasticClient format (list of source dicts)
    # when called with positional index arg (ElasticClient style)
    async def search(  # type: ignore[override]
        self,
        index: str | None = None,
        query: dict | None = None,
        *,
        size: int = 10,
        sort: list[dict] | None = None,
        body: dict | None = None,
        **kwargs: Any,
    ) -> list[dict]:
        """Search returning list of _source dicts (matching ElasticClient.search)."""
        if index is None:
            return []
        docs = self._store.get(index, {})
        # Simple query matching for test support
        results = []
        for doc_id, doc in docs.items():
            if query and "bool" in query:
                filters = query["bool"].get("filter", [])
                match = True
                for f in filters:
                    if "term" in f:
                        for field, value in f["term"].items():
                            if doc.get(field) != value:
                                match = False
                if not match:
                    continue
            results.append(doc)
        return results[:size]

    async def close(self) -> None:
        pass

    def get_store(self, index: str) -> dict[str, dict]:
        return self._store.get(index, {})


@pytest.fixture
def mock_es_client() -> MockAsyncElasticsearch:
    """Provide a mock async Elasticsearch client."""
    return MockAsyncElasticsearch()


# ---------------------------------------------------------------------------
# Sample model data
# ---------------------------------------------------------------------------

@pytest.fixture
def sample_task_data() -> dict:
    """Return sample task data matching the Task model schema."""
    return {
        "task_id": "task-test-001",
        "title": "Fix flaky test in payment module",
        "description": "The test_process_refund test fails intermittently due to a race condition in the mock setup.",
        "status": "pending",
        "priority": 4,
        "assigned_to": None,
        "depends_on": [],
        "blocked_by": [],
        "file_scope": ["tests/payments/test_refund.py"],
        "labels": ["bug", "test", "payments"],
        "branch_name": None,
        "result_summary": None,
        "error_message": None,
        "estimated_complexity": "small",
        "actual_tokens_used": None,
        "actual_cost_usd": None,
        "actual_duration_ms": None,
        "created_at": "2026-02-25T10:00:00Z",
        "updated_at": "2026-02-25T10:00:00Z",
        "assigned_at": None,
        "started_at": None,
        "completed_at": None,
    }


@pytest.fixture
def sample_agent_data() -> dict:
    """Return sample agent data matching the Agent model schema."""
    return {
        "agent_id": "agent-test-001",
        "name": "claude-runner-test",
        "type": "claude",
        "status": "idle",
        "current_task_id": None,
        "capabilities": ["python", "testing"],
        "worktree_path": "/tmp/codefleet/test-worktree",
        "session_id": None,
        "last_heartbeat": "2026-02-25T10:00:00Z",
        "tasks_completed": 0,
        "tasks_failed": 0,
        "total_tokens_used": 0,
        "total_cost_usd": 0.0,
        "created_at": "2026-02-25T10:00:00Z",
        "updated_at": "2026-02-25T10:00:00Z",
    }


@pytest.fixture
def sample_activity_data() -> dict:
    """Return sample activity event data matching the ActivityEvent model schema."""
    return {
        "event_id": "evt-test-001",
        "agent_id": "agent-test-001",
        "task_id": "task-test-001",
        "event_type": "task_started",
        "message": "Picked up task: Fix flaky test in payment module",
        "files_changed": [],
        "tool_name": None,
        "tokens_used": None,
        "cost_usd": None,
        "duration_ms": None,
        "metadata": {"task_priority": 4},
        "timestamp": "2026-02-25T10:01:00Z",
    }


@pytest.fixture
def sample_file_change_data() -> dict:
    """Return sample file change data matching the FileChange model schema."""
    return {
        "change_id": "chg-test-001",
        "agent_id": "agent-test-001",
        "task_id": "task-test-001",
        "file_path": "tests/payments/test_refund.py",
        "change_type": "modified",
        "branch_name": "fix/flaky-refund-test",
        "commit_sha": "abc1234",
        "lines_added": 15,
        "lines_removed": 3,
        "timestamp": "2026-02-25T10:05:00Z",
    }


@pytest.fixture
def sample_conflict_data() -> dict:
    """Return sample conflict data matching the Conflict model schema."""
    return {
        "conflict_id": "cfl-test-001",
        "agent_ids": ["agent-test-001", "agent-test-002"],
        "task_ids": ["task-test-001", "task-test-002"],
        "file_paths": ["src/config/settings.py"],
        "conflict_type": "file_overlap",
        "status": "detected",
        "resolution": None,
        "detected_at": "2026-02-25T10:10:00Z",
        "resolved_at": None,
    }


@pytest.fixture
def sample_task_result_data() -> dict:
    """Return sample task result data matching the TaskResult model schema."""
    return {
        "success": True,
        "summary": "Fixed race condition in mock setup by adding proper async synchronization.",
        "files_changed": ["tests/payments/test_refund.py"],
        "tokens_used": 12500,
        "cost_usd": 0.05,
        "duration_ms": 45000,
        "error": None,
    }
