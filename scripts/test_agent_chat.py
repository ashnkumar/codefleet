"""Test Fleet Commander agent via the Kibana Agent Builder chat API."""

from __future__ import annotations

import asyncio
import logging

import httpx
import structlog

from elastic.setup.config import get_kibana_api_key, get_kibana_url

logger = structlog.get_logger()

CHAT_API_PATH = "/api/agent_builder/agents/{agent_id}/chat"


async def chat_with_agent(agent_id: str, message: str) -> str | None:
    """Send a message to an Agent Builder agent and return the response."""
    kibana_url = get_kibana_url()
    api_key = get_kibana_api_key()

    headers = {
        "Authorization": f"ApiKey {api_key}",
        "Content-Type": "application/json",
        "kbn-xsrf": "true",
    }

    url = f"{kibana_url}{CHAT_API_PATH.format(agent_id=agent_id)}"
    body = {
        "messages": [
            {"role": "user", "content": message}
        ]
    }

    logger.info("sending_message", agent_id=agent_id, message=message)

    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(url, headers=headers, json=body)

        if response.status_code in (200, 201):
            data = response.json()
            logger.info("response_received", status=response.status_code)
            return data
        else:
            logger.error(
                "chat_failed",
                status=response.status_code,
                body=response.text[:500],
            )
            return None


async def test_fleet_commander() -> None:
    """Test the Fleet Commander agent with key prompts."""
    # Test 1: Show backlog
    logger.info("test_1_show_backlog")
    result = await chat_with_agent("fleet_commander", "Show me the current task backlog")
    if result:
        logger.info("test_1_result", result_type=type(result).__name__, result_preview=str(result)[:500])

    # Test 2: Check agent status
    logger.info("test_2_check_agents")
    result = await chat_with_agent("fleet_commander", "What is the status of all agents in the fleet?")
    if result:
        logger.info("test_2_result", result_type=type(result).__name__, result_preview=str(result)[:500])

    # Test 3: Check for conflicts
    logger.info("test_3_detect_conflicts")
    result = await chat_with_agent("fleet_commander", "Are there any file conflicts between agents?")
    if result:
        logger.info("test_3_result", result_type=type(result).__name__, result_preview=str(result)[:500])


def main() -> None:
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )
    asyncio.run(test_fleet_commander())


if __name__ == "__main__":
    main()
