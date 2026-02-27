"""Base runner — core lifecycle for CodeFleet coding-agent runners."""

from __future__ import annotations

import asyncio
import signal
from abc import ABC, abstractmethod
from datetime import datetime

import structlog

from src.config.constants import (
    IDX_ACTIVITY,
    IDX_AGENTS,
    IDX_CHANGES,
    IDX_TASKS,
)
from src.config.settings import ElasticClient, get_es_client, get_settings
from src.models import (
    ActivityEvent,
    Agent,
    AgentStatus,
    EventType,
    FileChange,
    Task,
    TaskResult,
    TaskStatus,
)

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


class BaseRunner(ABC):
    """Abstract base class for coding-agent runners.

    Lifecycle:
        register → start (heartbeat loop + poll loop) →
        poll_for_task → execute_task → complete/fail → repeat →
        shutdown
    """

    def __init__(
        self,
        name: str,
        agent_type: str = "claude",
        capabilities: list[str] | None = None,
    ) -> None:
        self.name = name
        self.agent_type = agent_type
        self.capabilities = capabilities or []

        self.agent_id: str | None = None
        self._running = False
        self._current_task_id: str | None = None
        self._es: ElasticClient | None = None
        self._settings = get_settings()

    @property
    def es(self) -> ElasticClient:
        if self._es is None:
            self._es = get_es_client()
        return self._es

    @property
    def is_idle(self) -> bool:
        """True if this runner is active but not executing a task."""
        return self._running and self._current_task_id is None

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    async def register(self) -> str:
        """Register this runner in the ``codefleet-agents`` index.

        Returns the generated ``agent_id``.
        """
        agent = Agent(
            name=self.name,
            type=self.agent_type,
            status=AgentStatus.IDLE,
            capabilities=self.capabilities,
        )
        self.agent_id = agent.agent_id

        await self.es.index_document(
            index=IDX_AGENTS,
            doc=agent.model_dump(mode="json"),
            doc_id=self.agent_id,
        )
        await self.report_activity(
            ActivityEvent(
                agent_id=self.agent_id,
                event_type=EventType.AGENT_STARTED,
                message=f"Runner {self.name} registered",
            )
        )
        logger.info("runner.registered", agent_id=self.agent_id, name=self.name)
        return self.agent_id

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def start(self, *, install_signal_handlers: bool = True) -> None:
        """Start the heartbeat and poll loops.

        Runs until ``shutdown()`` is called or a signal is received.

        Args:
            install_signal_handlers: Set to ``False`` when the runner is
                managed by :class:`FleetManager` (which installs its own
                handlers).
        """
        if self.agent_id is None:
            await self.register()

        self._running = True
        if install_signal_handlers:
            self._install_signal_handlers()

        logger.info("runner.starting", name=self.name, agent_id=self.agent_id)

        async with asyncio.TaskGroup() as tg:
            tg.create_task(self._heartbeat_loop())
            tg.create_task(self._poll_loop())

    async def _heartbeat_loop(self) -> None:
        """Send heartbeats at the configured interval."""
        while self._running:
            try:
                await self.heartbeat()
            except Exception:
                logger.exception("runner.heartbeat_failed", agent_id=self.agent_id)
            await asyncio.sleep(self._settings.heartbeat_interval)

    async def _poll_loop(self) -> None:
        """Poll for assigned tasks and execute them."""
        while self._running:
            try:
                task = await self.poll_for_task()
                if task is not None:
                    await self._handle_task(task)
            except Exception:
                logger.exception("runner.poll_error", agent_id=self.agent_id)
            await asyncio.sleep(self._settings.poll_interval)

    async def _handle_task(self, task: Task) -> None:
        """Transition a task through in_progress → completed/failed."""
        assert self.agent_id is not None
        self._current_task_id = task.task_id

        try:
            # Mark in-progress
            now = datetime.utcnow().isoformat()
            await self.es.update_document(
                index=IDX_TASKS,
                doc_id=task.task_id,
                partial_doc={
                    "status": TaskStatus.IN_PROGRESS.value,
                    "started_at": now,
                    "updated_at": now,
                },
            )
            await self.es.update_document(
                index=IDX_AGENTS,
                doc_id=self.agent_id,
                partial_doc={
                    "status": AgentStatus.WORKING.value,
                    "current_task_id": task.task_id,
                    "updated_at": now,
                },
            )
            await self.report_activity(
                ActivityEvent(
                    agent_id=self.agent_id,
                    task_id=task.task_id,
                    event_type=EventType.TASK_STARTED,
                    message=f"Started task: {task.title}",
                )
            )

            try:
                result = await self.execute_task(task)
                if result.success:
                    await self.complete_task(task, result)
                else:
                    await self.fail_task(task, result.error or "Task returned failure")
            except Exception as exc:
                logger.exception(
                    "runner.task_execution_failed",
                    agent_id=self.agent_id,
                    task_id=task.task_id,
                )
                await self.fail_task(task, str(exc))
        finally:
            self._current_task_id = None

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    async def poll_for_task(self) -> Task | None:
        """Query ES for a task assigned to this runner by the Fleet Manager."""
        assert self.agent_id is not None

        docs = await self.es.search(
            index=IDX_TASKS,
            query={
                "bool": {
                    "filter": [
                        {"term": {"status": TaskStatus.ASSIGNED.value}},
                        {"term": {"assigned_to": self.agent_id}},
                    ]
                }
            },
            size=1,
            sort=[{"priority": {"order": "desc"}}, {"created_at": {"order": "asc"}}],
        )

        if not docs:
            return None

        task = Task(**docs[0])
        logger.info(
            "runner.task_found",
            agent_id=self.agent_id,
            task_id=task.task_id,
            title=task.title,
        )
        return task

    # ------------------------------------------------------------------
    # Execution (abstract)
    # ------------------------------------------------------------------

    @abstractmethod
    async def execute_task(self, task: Task) -> TaskResult:
        """Execute a coding task. Implemented by subclasses."""
        ...

    # ------------------------------------------------------------------
    # Reporting
    # ------------------------------------------------------------------

    async def report_activity(self, event: ActivityEvent) -> None:
        """Log an activity event to ``codefleet-activity``."""
        await self.es.index_document(
            index=IDX_ACTIVITY,
            doc=event.model_dump(mode="json"),
            doc_id=event.event_id,
        )

    async def report_file_change(self, change: FileChange) -> None:
        """Log a file change to ``codefleet-changes``."""
        await self.es.index_document(
            index=IDX_CHANGES,
            doc=change.model_dump(mode="json"),
            doc_id=change.change_id,
        )

    # ------------------------------------------------------------------
    # Task completion / failure
    # ------------------------------------------------------------------

    async def complete_task(self, task: Task, result: TaskResult) -> None:
        """Mark a task as completed and return the runner to idle."""
        assert self.agent_id is not None
        now = datetime.utcnow().isoformat()

        await self.es.update_document(
            index=IDX_TASKS,
            doc_id=task.task_id,
            partial_doc={
                "status": TaskStatus.COMPLETED.value,
                "result_summary": result.summary,
                "actual_tokens_used": result.tokens_used,
                "actual_cost_usd": result.cost_usd,
                "actual_duration_ms": result.duration_ms,
                "completed_at": now,
                "updated_at": now,
            },
        )
        # Use a scripted update to atomically increment counters
        await self.es.raw.update(
            index=IDX_AGENTS,
            id=self.agent_id,
            script={
                "source": (
                    "ctx._source.status = params.status;"
                    "ctx._source.current_task_id = null;"
                    "ctx._source.tasks_completed += 1;"
                    "ctx._source.total_tokens_used += params.tokens;"
                    "ctx._source.total_cost_usd += params.cost;"
                    "ctx._source.updated_at = params.now;"
                ),
                "params": {
                    "status": AgentStatus.IDLE.value,
                    "tokens": result.tokens_used,
                    "cost": result.cost_usd,
                    "now": now,
                },
            },
        )
        await self.report_activity(
            ActivityEvent(
                agent_id=self.agent_id,
                task_id=task.task_id,
                event_type=EventType.TASK_COMPLETED,
                message=result.summary,
                files_changed=result.files_changed,
                tokens_used=result.tokens_used,
                cost_usd=result.cost_usd,
                duration_ms=result.duration_ms,
            )
        )
        logger.info(
            "runner.task_completed",
            agent_id=self.agent_id,
            task_id=task.task_id,
            summary=result.summary,
        )


    async def fail_task(self, task: Task, error: str) -> None:
        """Mark a task as failed and return the runner to idle."""
        assert self.agent_id is not None
        now = datetime.utcnow().isoformat()

        await self.es.update_document(
            index=IDX_TASKS,
            doc_id=task.task_id,
            partial_doc={
                "status": TaskStatus.FAILED.value,
                "error_message": error,
                "updated_at": now,
            },
        )
        await self.es.raw.update(
            index=IDX_AGENTS,
            id=self.agent_id,
            script={
                "source": (
                    "ctx._source.status = params.status;"
                    "ctx._source.current_task_id = null;"
                    "ctx._source.tasks_failed += 1;"
                    "ctx._source.updated_at = params.now;"
                ),
                "params": {
                    "status": AgentStatus.IDLE.value,
                    "now": now,
                },
            },
        )
        await self.report_activity(
            ActivityEvent(
                agent_id=self.agent_id,
                task_id=task.task_id,
                event_type=EventType.TASK_FAILED,
                message=f"Task failed: {error}",
            )
        )
        logger.warning(
            "runner.task_failed",
            agent_id=self.agent_id,
            task_id=task.task_id,
            error=error,
        )

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    async def heartbeat(self) -> None:
        """Update ``last_heartbeat`` in the agent registry."""
        if self.agent_id is None:
            return
        now = datetime.utcnow().isoformat()
        await self.es.update_document(
            index=IDX_AGENTS,
            doc_id=self.agent_id,
            partial_doc={"last_heartbeat": now, "updated_at": now},
        )

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def shutdown(self) -> None:
        """Set agent status to offline and stop loops."""
        self._running = False
        if self.agent_id is None:
            return

        now = datetime.utcnow().isoformat()
        try:
            await self.es.update_document(
                index=IDX_AGENTS,
                doc_id=self.agent_id,
                partial_doc={
                    "status": AgentStatus.OFFLINE.value,
                    "current_task_id": None,
                    "updated_at": now,
                },
            )
            await self.report_activity(
                ActivityEvent(
                    agent_id=self.agent_id,
                    event_type=EventType.AGENT_STOPPED,
                    message=f"Runner {self.name} shutting down",
                )
            )
        except Exception:
            logger.exception("runner.shutdown_error", agent_id=self.agent_id)
        logger.info("runner.shutdown", agent_id=self.agent_id, name=self.name)

    # ------------------------------------------------------------------
    # Signal handling
    # ------------------------------------------------------------------

    def _install_signal_handlers(self) -> None:
        """Register SIGTERM/SIGINT handlers to trigger graceful shutdown."""
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._signal_handler)

    def _signal_handler(self) -> None:
        logger.info("runner.signal_received", name=self.name)
        self._running = False
