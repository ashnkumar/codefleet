"""CodeFleet CLI — command-line interface for the fleet manager."""

from __future__ import annotations

import asyncio
import json
import tomllib
from datetime import datetime
from importlib import metadata
from pathlib import Path

import click
import structlog

from src.config.settings import get_es_client, get_settings
from src.models import AgentStatus, Task, TaskStatus
from src.runners.base import IDX_AGENTS, IDX_TASKS
from src.runners.claude_runner import ClaudeRunner
from src.runners.manager import FleetManager

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


def _run(coro):
    """Run an async coroutine in a new event loop, closing ES client on exit."""
    async def _wrapper():
        try:
            return await coro
        finally:
            es = get_es_client()
            await es.close()
    asyncio.run(_wrapper())


@click.group()
def cli() -> None:
    """CodeFleet — AI Coding Agent Fleet Commander."""
    get_settings()  # trigger logging config


@cli.command()
def version() -> None:
    """Print the CodeFleet version."""
    try:
        ver = metadata.version("codefleet")
    except metadata.PackageNotFoundError:
        # Fallback: read directly from pyproject.toml (dev / editable installs)
        pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        with pyproject.open("rb") as f:
            ver = tomllib.load(f)["project"]["version"]
    click.echo(f"CodeFleet v{ver}")


# ------------------------------------------------------------------
# Fleet commands
# ------------------------------------------------------------------


@cli.command()
@click.option("--runners", "-n", default=3, help="Number of runners to start.")
@click.option("--workdir", "-w", default=".", help="Working directory for agents.")
def start(runners: int, workdir: str) -> None:
    """Start the fleet manager with N runners."""
    manager = FleetManager(num_runners=runners, workdir=workdir)
    _run(manager.start())


@cli.command("run-single")
@click.option("--name", required=True, help="Runner name.")
@click.option("--workdir", "-w", default=".", help="Working directory for the agent.")
def run_single(name: str, workdir: str) -> None:
    """Run a single runner (useful for debugging)."""
    runner = ClaudeRunner(name=name, workdir=workdir)

    async def _go():
        await runner.register()
        await runner.start()

    _run(_go())


# ------------------------------------------------------------------
# Task commands
# ------------------------------------------------------------------


@cli.command("add-task")
@click.option("--title", "-t", required=True, help="Task title.")
@click.option("--description", "-d", required=True, help="Task description.")
@click.option("--priority", "-p", default=3, type=int, help="Priority 1-5 (default 3).")
@click.option("--labels", "-l", default="", help="Comma-separated labels.")
@click.option("--file-scope", "-f", default="", help="Comma-separated file paths.")
@click.option("--depends-on", default="", help="Comma-separated task IDs this depends on.")
@click.option("--complexity", "-c", default="medium", help="Estimated complexity.")
def add_task(
    title: str,
    description: str,
    priority: int,
    labels: str,
    file_scope: str,
    depends_on: str,
    complexity: str,
) -> None:
    """Add a task to the backlog."""
    task = Task(
        title=title,
        description=description,
        priority=priority,
        labels=[l.strip() for l in labels.split(",") if l.strip()],
        file_scope=[f.strip() for f in file_scope.split(",") if f.strip()],
        depends_on=[d.strip() for d in depends_on.split(",") if d.strip()],
        estimated_complexity=complexity,
    )

    async def _go():
        es = get_es_client()
        doc = task.model_dump(mode="json")
        # Populate semantic field for vector search
        doc["description_semantic"] = f"{task.title}. {task.description}"
        await es.index_document(
            index=IDX_TASKS,
            doc=doc,
            doc_id=task.task_id,
        )
        click.echo(f"Task created: {task.task_id}")
        click.echo(f"  Title: {task.title}")
        click.echo(f"  Priority: {task.priority}")
        click.echo(f"  Status: {task.status.value}")

    _run(_go())


@cli.command("list-tasks")
@click.option(
    "--status",
    "-s",
    default=None,
    help="Filter by status (pending, assigned, in_progress, completed, failed).",
)
@click.option("--limit", "-n", default=20, type=int, help="Max results.")
def list_tasks(status: str | None, limit: int) -> None:
    """List tasks from the backlog."""

    async def _go():
        es = get_es_client()
        query = None
        if status:
            query = {"term": {"status": status}}
        docs = await es.search(
            index=IDX_TASKS,
            query=query,
            size=limit,
            sort=[{"priority": {"order": "desc"}}, {"created_at": {"order": "asc"}}],
        )
        if not docs:
            click.echo("No tasks found.")
            return

        click.echo(f"{'ID':<38} {'Status':<12} {'Pri':>3} {'Title'}")
        click.echo("-" * 80)
        for doc in docs:
            task_id = doc.get("task_id", "?")
            t_status = doc.get("status", "?")
            pri = doc.get("priority", 0)
            title = doc.get("title", "?")
            click.echo(f"{task_id:<38} {t_status:<12} {pri:>3} {title}")

    _run(_go())


# ------------------------------------------------------------------
# Assignment command
# ------------------------------------------------------------------


@cli.command("assign")
@click.option("--task-id", "-t", required=True, help="Task ID to assign.")
@click.option("--agent-name", "-a", required=True, help="Agent name to assign to.")
def assign(task_id: str, agent_name: str) -> None:
    """Assign a task to a runner agent."""

    async def _go():
        es = get_es_client()
        now = datetime.utcnow().isoformat()

        # Find the agent by name
        agents = await es.search(
            index=IDX_AGENTS,
            query={"term": {"name": agent_name}},
            size=1,
        )
        if not agents:
            click.echo(f"Error: Agent '{agent_name}' not found.")
            return
        agent_id = agents[0]["agent_id"]

        # Update task
        await es.update_document(
            index=IDX_TASKS,
            doc_id=task_id,
            partial_doc={
                "status": "assigned",
                "assigned_to": agent_id,
                "assigned_at": now,
                "updated_at": now,
            },
        )

        # Update agent
        await es.update_document(
            index=IDX_AGENTS,
            doc_id=agent_id,
            partial_doc={
                "status": "working",
                "current_task_id": task_id,
                "updated_at": now,
            },
        )

        click.echo(f"Assigned task {task_id} to {agent_name} ({agent_id})")

    _run(_go())


# ------------------------------------------------------------------
# Status command
# ------------------------------------------------------------------


@cli.command()
def status() -> None:
    """Show fleet status (agents, tasks, conflicts)."""

    async def _go():
        es = get_es_client()

        # Agents
        agents = await es.search(
            index=IDX_AGENTS,
            query={"bool": {"must_not": [{"term": {"status": "offline"}}]}},
            size=50,
        )
        click.echo("=== Agents ===")
        if agents:
            click.echo(f"{'Name':<25} {'Status':<10} {'Task':<38} {'Last HB'}")
            click.echo("-" * 90)
            for a in agents:
                click.echo(
                    f"{a.get('name', '?'):<25} "
                    f"{a.get('status', '?'):<10} "
                    f"{(a.get('current_task_id') or '-'):<38} "
                    f"{a.get('last_heartbeat', '?')}"
                )
        else:
            click.echo("No active agents.")

        click.echo()

        # Task counts by status
        click.echo("=== Task Summary ===")
        for s in TaskStatus:
            docs = await es.search(
                index=IDX_TASKS,
                query={"term": {"status": s.value}},
                size=0,
            )
            # The search returns [] for size=0 but we need a count
            # Use raw client for count
            resp = await es.raw.count(
                index=IDX_TASKS,
                query={"term": {"status": s.value}},
            )
            count = resp.body.get("count", 0)
            click.echo(f"  {s.value:<14} {count}")

    _run(_go())


# ------------------------------------------------------------------
# Setup & seed (delegates to other modules)
# ------------------------------------------------------------------


@cli.command()
def setup() -> None:
    """Run all Elastic setup (indices, tools, agents, workflows)."""
    click.echo("Running Elastic setup...")
    try:
        from elastic.setup import create_indices, create_tools, create_agents

        _run(create_indices.main())
        _run(create_tools.main())
        _run(create_agents.main())
        click.echo("Setup complete.")
    except ImportError:
        click.echo("Error: elastic.setup modules not found. Is Agent 2's code merged?")


@cli.command()
def seed() -> None:
    """Seed synthetic test data."""
    click.echo("Seeding data...")
    try:
        from data import seed

        _run(seed.main())
        click.echo("Seeding complete.")
    except ImportError:
        click.echo("Error: data.seed module not found. Is Agent 3's code merged?")


@cli.command()
def reset() -> None:
    """Reset all tasks to pending and clear stale agents. Use before re-running the fleet."""

    async def _go():
        es = get_es_client()
        reset_count = 0

        # Reset non-pending/non-blocked/non-completed tasks back to pending
        for status in ["assigned", "in_progress", "failed"]:
            docs = await es.search(
                index=IDX_TASKS,
                query={"term": {"status": status}},
                size=100,
            )
            for doc in docs:
                await es.update_document(
                    index=IDX_TASKS,
                    doc_id=doc["task_id"],
                    partial_doc={
                        "status": "pending",
                        "assigned_to": None,
                        "assigned_at": None,
                        "started_at": None,
                        "error_message": None,
                    },
                )
                reset_count += 1
                click.echo(f"  Reset: {doc['title']}")

        # Delete all agents
        agents = await es.search(index=IDX_AGENTS, size=100)
        for agent in agents:
            await es.raw.delete(index=IDX_AGENTS, id=agent["agent_id"])
            click.echo(f"  Deleted agent: {agent['name']}")

        # Clear activity log
        try:
            await es.raw.delete_by_query(
                index="codefleet-activity",
                body={"query": {"match_all": {}}},
            )
            click.echo("  Cleared activity log")
        except Exception:
            pass

        click.echo(f"Done! Reset {reset_count} tasks to pending.")

    _run(_go())


if __name__ == "__main__":
    cli()
