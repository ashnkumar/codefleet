"""Fleet manager — runner process manager for CodeFleet.

Starts N Claude runners, keeps them alive (restarting on crash),
and shuts them all down on signal. Task assignment and orchestration
logic lives in Elastic Workflows (auto_assign_tasks, handle_task_completion,
handle_stale_agents), not here.
"""

from __future__ import annotations

import asyncio
import signal

import structlog

from src.config.constants import DEFAULT_MAX_RUNNERS
from src.config.settings import get_settings
from src.runners.claude_runner import ClaudeRunner

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


class FleetManager:
    """Manages Claude runner processes.

    Spins up N runners, monitors their health, restarts crashed ones,
    and performs graceful shutdown on signal. All task orchestration
    (assignment, dependency unblocking, stale recovery) is handled by
    Elastic Workflows running server-side.
    """

    def __init__(
        self,
        workdir: str = ".",
        max_runners: int = DEFAULT_MAX_RUNNERS,
        *,
        num_runners: int | None = None,
    ) -> None:
        settings = get_settings()
        effective_max = num_runners if num_runners is not None else max_runners
        self.workdir = workdir
        self.max_runners = min(effective_max, settings.max_runners)
        self.runners: dict[str, ClaudeRunner] = {}
        self._runner_tasks: dict[str, asyncio.Task[None]] = {}
        self._running = False
        self._runner_counter = 0

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start N runners and keep them alive until shutdown."""
        self._running = True
        self._install_signal_handlers()

        logger.info(
            "fleet.starting",
            max_runners=self.max_runners,
            workdir=self.workdir,
        )

        # Spin up the requested number of runners
        for _ in range(self.max_runners):
            await self._spin_up_runner()

        # Wait for all runner tasks to complete (they run until shutdown)
        try:
            await asyncio.gather(*self._runner_tasks.values())
        except Exception as exc:
            if not isinstance(exc, asyncio.CancelledError):
                logger.error("fleet.error", error=str(exc))

        await self._shutdown_all()
        logger.info("fleet.stopped", total_runners_created=self._runner_counter)

    async def stop(self) -> None:
        """Signal all loops to stop."""
        self._running = False
        for runner in self.runners.values():
            runner._running = False

    # ------------------------------------------------------------------
    # Runner management
    # ------------------------------------------------------------------

    async def _spin_up_runner(self) -> ClaudeRunner:
        """Create, register, and start a new runner."""
        self._runner_counter += 1
        name = f"runner-{self._runner_counter}"
        runner = ClaudeRunner(name=name, workdir=self.workdir)
        await runner.register()

        loop_task = asyncio.create_task(self._run_runner(runner))
        self.runners[name] = runner
        self._runner_tasks[name] = loop_task

        logger.info(
            "fleet.runner_spun_up",
            name=name,
            agent_id=runner.agent_id,
            total_runners=len(self.runners),
        )
        return runner

    async def _run_runner(self, runner: ClaudeRunner) -> None:
        """Drive a runner's heartbeat + poll loops, restarting on failure."""
        while self._running:
            try:
                await runner.start(install_signal_handlers=False)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception(
                    "fleet.runner_crashed",
                    runner=runner.name,
                    agent_id=runner.agent_id,
                )
            if not self._running or not runner._running:
                break
            logger.info("fleet.runner_restarting", runner=runner.name)
            await asyncio.sleep(5)

    async def _remove_runner(self, name: str) -> None:
        """Shut down and deregister a single runner."""
        runner = self.runners.pop(name, None)
        if runner is None:
            return

        runner._running = False
        try:
            await runner.shutdown()
        except Exception:
            logger.exception("fleet.runner_shutdown_error", runner=name)

        task = self._runner_tasks.pop(name, None)
        if task and not task.done():
            task.cancel()

        logger.info(
            "fleet.runner_removed",
            name=name,
            total_runners=len(self.runners),
        )

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    async def _shutdown_all(self) -> None:
        """Gracefully shut down every runner in the fleet."""
        for name in list(self.runners.keys()):
            await self._remove_runner(name)

    # ------------------------------------------------------------------
    # Signal handling
    # ------------------------------------------------------------------

    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, self._signal_handler)

    def _signal_handler(self) -> None:
        logger.info("fleet.signal_received")
        self._running = False
        for runner in self.runners.values():
            runner._running = False
