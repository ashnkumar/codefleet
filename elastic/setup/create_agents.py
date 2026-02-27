"""Create Agent Builder agents from JSON definitions.

Reads each JSON file in elastic/agents/ and registers the agent with
the Kibana Agent Builder API. Idempotent: updates agents that already exist.

Usage:
    uv run python -m elastic.setup.create_agents
"""

from __future__ import annotations

import asyncio
import json
import logging

import httpx
import structlog

from elastic.setup.config import AGENTS_DIR, get_kibana_api_key, get_kibana_url

logger = structlog.get_logger()

# Agent Builder API paths
AGENTS_API_PATH = "/api/agent_builder/agents"


async def create_agents() -> None:
    """Register all CodeFleet agents with Agent Builder."""
    kibana_url = get_kibana_url()
    api_key = get_kibana_api_key()

    headers = {
        "Authorization": f"ApiKey {api_key}",
        "Content-Type": "application/json",
        "kbn-xsrf": "true",
    }

    agent_files = sorted(AGENTS_DIR.glob("*.json"))
    if not agent_files:
        logger.warning("no_agent_files_found", directory=str(AGENTS_DIR))
        return

    async with httpx.AsyncClient(timeout=30.0) as client:
        for agent_file in agent_files:
            with open(agent_file) as f:
                agent_def = json.load(f)

            agent_id = agent_def.get("id", agent_file.stem)
            logger.info("registering_agent", agent_id=agent_id, file=agent_file.name)

            # Try to create the agent
            url = f"{kibana_url}{AGENTS_API_PATH}"
            response = await client.post(url, headers=headers, json=agent_def)

            already_exists = (
                response.status_code == 409
                or (response.status_code == 400 and "already exists" in response.text)
            )

            if response.status_code in (200, 201):
                logger.info("agent_created", agent_id=agent_id)
            elif already_exists:
                # Agent already exists, try to update (strip id from body)
                update_url = f"{kibana_url}{AGENTS_API_PATH}/{agent_id}"
                update_body = {k: v for k, v in agent_def.items() if k != "id"}
                update_response = await client.put(
                    update_url, headers=headers, json=update_body
                )
                if update_response.status_code in (200, 201):
                    logger.info("agent_updated", agent_id=agent_id)
                else:
                    logger.error(
                        "agent_update_failed",
                        agent_id=agent_id,
                        status=update_response.status_code,
                        body=update_response.text,
                    )
            else:
                logger.error(
                    "agent_creation_failed",
                    agent_id=agent_id,
                    status=response.status_code,
                    body=response.text,
                )

    logger.info("all_agents_registered", count=len(agent_files))


def main() -> None:
    """Entry point for running as a module."""
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )
    asyncio.run(create_agents())


if __name__ == "__main__":
    main()
