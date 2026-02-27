"""Run all Elastic setup steps in order.

Creates indices, registers tools, and registers agents.

Usage:
    uv run python scripts/setup_all.py
"""

from __future__ import annotations

import asyncio
import logging
import sys

import structlog

from elastic.setup.create_agents import create_agents
from elastic.setup.create_indices import create_indices
from elastic.setup.create_tools import create_tools

logger = structlog.get_logger()


async def setup_all() -> None:
    """Run all setup steps in order: indices → tools → agents."""
    logger.info("setup_starting")

    logger.info("step_1_creating_indices")
    await create_indices()

    logger.info("step_2_registering_tools")
    await create_tools()

    logger.info("step_3_registering_agents")
    await create_agents()

    logger.info("setup_complete", message="All Elastic resources are ready")


def main() -> None:
    """Entry point."""
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )

    try:
        asyncio.run(setup_all())
    except EnvironmentError as e:
        logger.error("setup_failed", error=str(e))
        sys.exit(1)
    except Exception as e:
        logger.error("setup_failed", error=str(e), error_type=type(e).__name__)
        sys.exit(1)


if __name__ == "__main__":
    main()
