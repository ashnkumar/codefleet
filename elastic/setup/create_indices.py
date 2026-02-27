"""Create Elasticsearch indices from JSON mapping definitions.

Reads each JSON file in elastic/indices/ and creates the corresponding
index in Elasticsearch. Idempotent: skips indices that already exist,
or updates their mappings if they do.

Usage:
    uv run python -m elastic.setup.create_indices
"""

from __future__ import annotations

import asyncio
import json
import logging

import structlog
from elasticsearch import AsyncElasticsearch

from elastic.setup.config import (
    INDICES_DIR,
    INDEX_NAMES,
    get_elastic_api_key,
    get_elastic_url,
)

logger = structlog.get_logger()


async def create_indices() -> None:
    """Create all CodeFleet indices in Elasticsearch."""
    es = AsyncElasticsearch(
        get_elastic_url(),
        api_key=get_elastic_api_key(),
    )

    try:
        # Verify connection
        info = await es.info()
        logger.info("connected_to_elasticsearch", version=info["version"]["number"])

        for filename, index_name in INDEX_NAMES.items():
            mapping_file = INDICES_DIR / f"{filename}.json"
            if not mapping_file.exists():
                logger.warning("mapping_file_not_found", file=str(mapping_file))
                continue

            with open(mapping_file) as f:
                body = json.load(f)

            exists = await es.indices.exists(index=index_name)
            if exists:
                logger.info("index_already_exists", index=index_name)
                # Update mappings in case they changed
                mappings = body.get("mappings", {})
                if mappings:
                    await es.indices.put_mapping(
                        index=index_name,
                        properties=mappings.get("properties", {}),
                    )
                    logger.info("index_mappings_updated", index=index_name)
            else:
                # Serverless doesn't support shard/replica settings
                mappings = body.get("mappings", {})
                await es.indices.create(
                    index=index_name,
                    mappings=mappings,
                )
                logger.info("index_created", index=index_name)

        logger.info("all_indices_ready", count=len(INDEX_NAMES))

    finally:
        await es.close()


def main() -> None:
    """Entry point for running as a module."""
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )
    asyncio.run(create_indices())


if __name__ == "__main__":
    main()
