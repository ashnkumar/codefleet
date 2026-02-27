"""Claude Agent SDK runner — executes coding tasks via Claude Code sessions."""

from __future__ import annotations

import time
from pathlib import Path

import structlog

import claude_agent_sdk
from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    HookContext,
    HookJSONOutput,
    HookMatcher,
    PostToolUseHookInput,
    ResultMessage,
)

from src.config.constants import (
    CLAUDE_ALLOWED_TOOLS,
    CLAUDE_FILE_CHANGE_TOOLS,
    CLAUDE_MAX_TURNS,
    CLAUDE_MODEL,
    CLAUDE_PERMISSION_MODE,
)
from src.models import ChangeType, FileChange, Task, TaskResult
from src.runners.base import BaseRunner

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


class ClaudeRunner(BaseRunner):
    """Runner that executes tasks using the Claude Agent SDK.

    Each task launches a ``claude_agent_sdk.query()`` session.  File-change
    hooks capture which files were touched so we can report them to
    Elasticsearch for conflict detection.
    """

    def __init__(
        self,
        name: str,
        workdir: str = ".",
        capabilities: list[str] | None = None,
    ) -> None:
        super().__init__(name=name, agent_type="claude", capabilities=capabilities or [])
        self.workdir = str(Path(workdir).resolve())
        self._files_changed: list[str] = []

    # ------------------------------------------------------------------
    # Task execution
    # ------------------------------------------------------------------

    async def execute_task(self, task: Task) -> TaskResult:
        """Execute a coding task by running a Claude Agent SDK session."""
        assert self.agent_id is not None

        prompt = self._build_prompt(task)
        options = self._build_options(task)

        self._files_changed = []
        start_time = time.monotonic()
        session_id: str | None = None
        result_text: str | None = None
        total_cost: float | None = None
        is_error = False

        logger.info(
            "runner.executing",
            agent_id=self.agent_id,
            task_id=task.task_id,
            title=task.title,
            workdir=self.workdir,
            model=CLAUDE_MODEL,
        )

        try:
            async for message in claude_agent_sdk.query(
                prompt=prompt,
                options=options,
            ):
                if isinstance(message, AssistantMessage):
                    for block in message.content:
                        if hasattr(block, "text"):
                            logger.debug(
                                "runner.assistant_text",
                                agent_id=self.agent_id,
                                task_id=task.task_id,
                                text=block.text[:200],
                            )

                elif isinstance(message, ResultMessage):
                    session_id = message.session_id
                    result_text = message.result
                    total_cost = message.total_cost_usd
                    is_error = message.is_error

            # Store session_id in agent registry
            if session_id:
                await self.es.update_document(
                    index="codefleet-agents",
                    doc_id=self.agent_id,
                    partial_doc={"session_id": session_id},
                )

        except Exception as exc:
            elapsed_ms = int((time.monotonic() - start_time) * 1000)
            logger.exception(
                "runner.session_failed",
                agent_id=self.agent_id,
                task_id=task.task_id,
            )
            return TaskResult(
                success=False,
                summary="",
                error=str(exc),
                files_changed=self._files_changed,
                duration_ms=elapsed_ms,
            )

        elapsed_ms = int((time.monotonic() - start_time) * 1000)

        # Log file changes to ES
        for fpath in self._files_changed:
            await self.report_file_change(
                FileChange(
                    agent_id=self.agent_id,
                    task_id=task.task_id,
                    file_path=fpath,
                    change_type=ChangeType.MODIFIED,
                )
            )

        if is_error:
            return TaskResult(
                success=False,
                summary=result_text or "Session ended with error",
                error=result_text,
                files_changed=self._files_changed,
                cost_usd=total_cost or 0.0,
                duration_ms=elapsed_ms,
            )

        return TaskResult(
            success=True,
            summary=result_text or "Task completed",
            files_changed=self._files_changed,
            cost_usd=total_cost or 0.0,
            duration_ms=elapsed_ms,
        )

    # ------------------------------------------------------------------
    # Prompt & options builders
    # ------------------------------------------------------------------

    def _build_prompt(self, task: Task) -> str:
        """Construct the prompt sent to the Claude agent session."""
        parts = [
            "You are working on the following task for the CodeFleet system:",
            "",
            f"**Title:** {task.title}",
            "",
            f"**Description:** {task.description}",
        ]
        if task.file_scope:
            parts.append("")
            parts.append(f"**File scope:** {', '.join(task.file_scope)}")
        if task.labels:
            parts.append("")
            parts.append(f"**Labels:** {', '.join(task.labels)}")
        parts.append("")
        parts.append(
            "Complete this task. When done, provide a brief summary of what you did."
        )
        return "\n".join(parts)

    def _build_options(self, task: Task) -> ClaudeAgentOptions:
        """Build ClaudeAgentOptions for the session."""
        return ClaudeAgentOptions(
            model=CLAUDE_MODEL,
            allowed_tools=CLAUDE_ALLOWED_TOOLS,
            permission_mode=CLAUDE_PERMISSION_MODE,
            cwd=self.workdir,
            max_turns=CLAUDE_MAX_TURNS,
        )

    # ------------------------------------------------------------------
    # Hooks
    # ------------------------------------------------------------------

    def _make_file_change_hook(self) -> claude_agent_sdk.HookCallback:
        """Create a hook closure that captures file changes on this runner."""
        runner = self

        async def hook(
            input: PostToolUseHookInput,
            _matcher: str | None,
            _ctx: HookContext,
        ) -> HookJSONOutput:
            tool_name = input.get("tool_name", "")
            tool_input = input.get("tool_input", {})

            if tool_name in CLAUDE_FILE_CHANGE_TOOLS:
                file_path = tool_input.get("file_path") or tool_input.get("path")
                if file_path and file_path not in runner._files_changed:
                    runner._files_changed.append(file_path)
                    logger.info(
                        "runner.file_changed",
                        agent_id=runner.agent_id,
                        file_path=file_path,
                        tool=tool_name,
                    )
            return {}

        return hook
