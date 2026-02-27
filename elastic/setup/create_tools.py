"""Create Agent Builder tools from JSON definitions.

Reads each JSON file in elastic/tools/ and registers the tool with
the Kibana Agent Builder API. Idempotent: updates tools that already exist.

Workflow-type tools are skipped here — they are deployed by
``create_workflows.py`` which handles the full lifecycle (deploy
workflow YAML → register tool with resolved workflow_id).

Usage:
    uv run python -m elastic.setup.create_tools
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import structlog

from elastic.setup.config import TOOLS_DIR, get_kibana_api_key, get_kibana_url

logger = structlog.get_logger()

# Agent Builder API paths
TOOLS_API_PATH = "/api/agent_builder/tools"

# Tool types handled by other setup scripts
_SKIP_TYPES = {"workflow"}


async def create_tools() -> None:
    """Register all CodeFleet tools with Agent Builder.

    Skips workflow-type tools (handled by ``create_workflows.py``).
    """
    kibana_url = get_kibana_url()
    api_key = get_kibana_api_key()

    headers = {
        "Authorization": f"ApiKey {api_key}",
        "Content-Type": "application/json",
        "kbn-xsrf": "true",
    }

    tool_files = sorted(TOOLS_DIR.glob("*.json"))
    if not tool_files:
        logger.warning("no_tool_files_found", directory=str(TOOLS_DIR))
        return

    registered = 0
    async with httpx.AsyncClient(timeout=30.0) as client:
        for tool_file in tool_files:
            with open(tool_file) as f:
                tool_def = json.load(f)

            tool_id = tool_def.get("id", tool_file.stem)
            tool_type = tool_def.get("type", "esql")

            # Skip types handled by other scripts
            if tool_type in _SKIP_TYPES:
                logger.info(
                    "skipping_tool",
                    tool_id=tool_id,
                    type=tool_type,
                    reason="handled by create_workflows.py",
                )
                continue

            logger.info(
                "registering_tool",
                tool_id=tool_id,
                type=tool_type,
                file=tool_file.name,
            )

            # Try to create the tool
            url = f"{kibana_url}{TOOLS_API_PATH}"
            response = await client.post(url, headers=headers, json=tool_def)

            already_exists = (
                response.status_code == 409
                or (response.status_code == 400 and "already exists" in response.text)
            )

            if response.status_code in (200, 201):
                logger.info("tool_created", tool_id=tool_id)
                registered += 1
            elif already_exists:
                # Tool already exists, try to update (strip id from body)
                update_url = f"{kibana_url}{TOOLS_API_PATH}/{tool_id}"
                update_body = {
                    k: v for k, v in tool_def.items() if k not in ("id", "type")
                }
                update_response = await client.put(
                    update_url, headers=headers, json=update_body
                )
                if update_response.status_code in (200, 201):
                    logger.info("tool_updated", tool_id=tool_id)
                    registered += 1
                else:
                    logger.error(
                        "tool_update_failed",
                        tool_id=tool_id,
                        status=update_response.status_code,
                        body=update_response.text,
                    )
            else:
                logger.error(
                    "tool_creation_failed",
                    tool_id=tool_id,
                    status=response.status_code,
                    body=response.text,
                )

    logger.info("all_tools_registered", total=len(tool_files), registered=registered)


def main() -> None:
    """Entry point for running as a module."""
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )
    asyncio.run(create_tools())


if __name__ == "__main__":
    main()
