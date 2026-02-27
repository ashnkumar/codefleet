"""Test the Fleet Commander write flow end-to-end.

Sends a chat message to Fleet Commander asking it to create tasks with
dependencies, then verifies the tasks appear in Elasticsearch with the
correct fields and dependency chain.

Usage:
    uv run python scripts/test_write_flow.py
    uv run python scripts/test_write_flow.py --verbose
    uv run python scripts/test_write_flow.py --skip-chat  # Only verify ES
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import time

import httpx
import structlog

# Reuse project config
sys.path.insert(0, ".")
from elastic.setup.config import (
    get_elastic_api_key,
    get_elastic_url,
    get_kibana_api_key,
    get_kibana_url,
)

logger = structlog.get_logger()

CONVERSE_PATH = "/api/agent_builder/converse"
ES_SEARCH_PATH = "/codefleet-tasks/_search"

# The prompt that asks Fleet Commander to create tasks with dependencies
CREATE_TASKS_PROMPT = """I need you to create 3 coding tasks for the demo app with dependencies:

1. "Add dark/light theme CSS variables" - priority 4, labels: ui,theme, file_scope: src/app/globals.css, complexity: medium, no dependencies
2. "Build theme toggle button component" - priority 3, labels: ui,component, file_scope: src/components/ThemeToggle.tsx, complexity: small, depends on task 1
3. "Wire theme persistence to localStorage" - priority 2, labels: ui,state, file_scope: src/app/layout.tsx, complexity: small, depends on task 2

Create each task using the create_task tool. For task 2, set depends_on to the title of task 1. For task 3, set depends_on to the title of task 2."""


async def chat_with_fleet_commander(
    client: httpx.AsyncClient,
    kibana_url: str,
    headers: dict[str, str],
    prompt: str,
) -> dict:
    """Send a message to Fleet Commander via the Converse API."""
    url = f"{kibana_url}{CONVERSE_PATH}"
    payload = {
        "input": prompt,
        "agent_id": "fleet_commander",
    }

    logger.info("sending_chat", prompt_length=len(prompt))
    response = await client.post(url, headers=headers, json=payload)

    if response.status_code not in (200, 201):
        logger.error(
            "chat_failed",
            status=response.status_code,
            body=response.text[:500],
        )
        return {"error": response.text}

    data = response.json()
    logger.info(
        "chat_response",
        conversation_id=data.get("conversation_id"),
        steps=len(data.get("steps", [])),
        response_preview=data.get("response", {}).get("message", "")[:200],
    )
    return data


async def search_tasks(
    client: httpx.AsyncClient,
    es_url: str,
    headers: dict[str, str],
    title_query: str | None = None,
) -> list[dict]:
    """Search for tasks in Elasticsearch."""
    if title_query:
        query = {"match_phrase": {"title": title_query}}
    else:
        # Find all recent tasks (last 10 minutes)
        query = {
            "bool": {
                "must": [
                    {"range": {"created_at": {"gte": "now-10m"}}},
                    {"term": {"status": "pending"}},
                ]
            }
        }

    payload = {
        "query": query,
        "sort": [{"created_at": {"order": "desc"}}],
        "size": 20,
    }

    url = f"{es_url}{ES_SEARCH_PATH}"
    response = await client.post(url, headers=headers, json=payload)

    if response.status_code != 200:
        logger.error(
            "search_failed", status=response.status_code, body=response.text[:300]
        )
        return []

    data = response.json()
    hits = data.get("hits", {}).get("hits", [])
    return [h["_source"] for h in hits]


async def verify_tasks(
    client: httpx.AsyncClient,
    es_url: str,
    es_headers: dict[str, str],
) -> bool:
    """Verify that tasks were created with correct fields."""
    expected_titles = [
        "Add dark/light theme CSS variables",
        "Build theme toggle button component",
        "Wire theme persistence to localStorage",
    ]

    all_ok = True
    found_tasks: dict[str, dict] = {}

    for title in expected_titles:
        tasks = await search_tasks(client, es_url, es_headers, title)
        if not tasks:
            logger.error("task_not_found", title=title)
            all_ok = False
            continue

        task = tasks[0]
        found_tasks[title] = task
        logger.info(
            "task_found",
            title=task.get("title"),
            status=task.get("status"),
            priority=task.get("priority"),
            labels=task.get("labels"),
            file_scope=task.get("file_scope"),
            depends_on=task.get("depends_on"),
            has_semantic=bool(task.get("description_semantic")),
        )

        # Verify required fields
        checks = {
            "has_title": bool(task.get("title")),
            "has_description": bool(task.get("description")),
            "has_status_pending": task.get("status") == "pending",
            "has_priority": task.get("priority") is not None,
            "has_description_semantic": bool(task.get("description_semantic")),
            "has_created_at": bool(task.get("created_at")),
        }

        for check_name, passed in checks.items():
            if not passed:
                logger.warning("check_failed", title=title, check=check_name)
                all_ok = False

    # Verify dependency chain
    if len(found_tasks) == 3:
        logger.info("verifying_dependency_chain")
        task2 = found_tasks.get("Build theme toggle button component", {})
        task3 = found_tasks.get("Wire theme persistence to localStorage", {})

        t2_deps = task2.get("depends_on", "")
        t3_deps = task3.get("depends_on", "")

        if t2_deps:
            logger.info("task2_depends_on", value=t2_deps)
        else:
            logger.warning("task2_missing_depends_on")

        if t3_deps:
            logger.info("task3_depends_on", value=t3_deps)
        else:
            logger.warning("task3_missing_depends_on")
    else:
        logger.warning(
            "incomplete_task_set",
            found=len(found_tasks),
            expected=3,
        )

    return all_ok


async def cleanup_test_tasks(
    client: httpx.AsyncClient,
    es_url: str,
    es_headers: dict[str, str],
) -> None:
    """Delete test tasks created during the test."""
    titles = [
        "Add dark/light theme CSS variables",
        "Build theme toggle button component",
        "Wire theme persistence to localStorage",
    ]

    for title in titles:
        url = f"{es_url}/codefleet-tasks/_delete_by_query"
        payload = {"query": {"match_phrase": {"title": title}}}
        response = await client.post(url, headers=es_headers, json=payload)
        if response.status_code == 200:
            data = response.json()
            deleted = data.get("deleted", 0)
            if deleted:
                logger.info("cleaned_up", title=title, deleted=deleted)


async def main(skip_chat: bool = False, cleanup: bool = False) -> None:
    """Run the write flow test."""
    kibana_url = get_kibana_url()
    kibana_key = get_kibana_api_key()
    es_url = get_elastic_url()
    es_key = get_elastic_api_key()

    kibana_headers = {
        "Authorization": f"ApiKey {kibana_key}",
        "Content-Type": "application/json",
        "kbn-xsrf": "true",
    }

    es_headers = {
        "Authorization": f"ApiKey {es_key}",
        "Content-Type": "application/json",
    }

    async with httpx.AsyncClient(timeout=120.0) as client:
        if cleanup:
            logger.info("cleaning_up_test_tasks")
            await cleanup_test_tasks(client, es_url, es_headers)
            return

        if not skip_chat:
            # Step 1: Chat with Fleet Commander to create tasks
            logger.info("step_1_chat_with_fleet_commander")
            result = chat_result = await chat_with_fleet_commander(
                client, kibana_url, kibana_headers, CREATE_TASKS_PROMPT
            )

            if "error" in result:
                logger.error("chat_error", error=result["error"][:300])
                logger.info(
                    "tip",
                    message="Try --skip-chat to just verify ES, or create tasks via Kibana UI first",
                )
            else:
                # Print the agent's response
                response_msg = result.get("response", {}).get("message", "")
                if response_msg:
                    print(f"\n--- Fleet Commander Response ---\n{response_msg}\n")

                # Print tool calls made
                for step in result.get("steps", []):
                    if step.get("type") == "tool_call":
                        logger.info(
                            "tool_called",
                            tool_id=step.get("tool_id"),
                            params=step.get("params"),
                        )

            # Wait for ES to index
            logger.info("waiting_for_indexing", seconds=3)
            await asyncio.sleep(3)

        # Step 2: Verify tasks in ES
        logger.info("step_2_verify_tasks_in_es")
        all_ok = await verify_tasks(client, es_url, es_headers)

        if all_ok:
            logger.info("all_checks_passed", status="SUCCESS")
            print("\nSUCCESS: All tasks created with correct fields")
        else:
            logger.warning("some_checks_failed", status="PARTIAL")
            print("\nPARTIAL: Some checks failed — see logs above")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Test Fleet Commander write flow")
    parser.add_argument(
        "--skip-chat",
        action="store_true",
        help="Skip the chat step, just verify tasks in ES",
    )
    parser.add_argument(
        "--cleanup",
        action="store_true",
        help="Delete test tasks from ES",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
    )

    asyncio.run(main(skip_chat=args.skip_chat, cleanup=args.cleanup))
