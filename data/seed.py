"""Seed Elasticsearch with synthetic demo data.

Reads JSON files from the data/ directory and bulk-indexes them
into the appropriate codefleet-* indices.

Usage:
    python -m data.seed
    # or via CLI:
    codefleet seed
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import structlog

logger = structlog.get_logger(__name__)

DATA_DIR = Path(__file__).parent

INDEX_FILE_MAP: dict[str, str] = {
    "codefleet-tasks": "seed_tasks.json",
    "codefleet-agents": "seed_agents.json",
    "codefleet-activity": "seed_activity.json",
}

# Document ID field per index — used so re-seeding is idempotent
DOC_ID_FIELD: dict[str, str] = {
    "codefleet-tasks": "task_id",
    "codefleet-agents": "agent_id",
    "codefleet-activity": "event_id",
}


def _load_json(filename: str) -> list[dict]:
    """Load a JSON array from a file in the data directory."""
    path = DATA_DIR / filename
    if not path.exists():
        logger.error("seed_file_not_found", path=str(path))
        return []
    with open(path) as f:
        data = json.load(f)
    if not isinstance(data, list):
        logger.error("seed_file_not_array", path=str(path))
        return []
    logger.info("loaded_seed_data", file=filename, count=len(data))
    return data


def _build_bulk_body(index: str, docs: list[dict], id_field: str) -> list[dict]:
    """Build a list of action/document pairs for the ES bulk API."""
    body: list[dict] = []
    for doc in docs:
        doc_id = doc.get(id_field)
        action = {"index": {"_index": index}}
        if doc_id:
            action["index"]["_id"] = doc_id
        # Auto-populate description_semantic for tasks (enables semantic search)
        if index.endswith("-tasks") and "description" in doc and "description_semantic" not in doc:
            doc["description_semantic"] = f"{doc.get('title', '')}. {doc['description']}"
        body.append(action)
        body.append(doc)
    return body


async def seed_index(
    es_client,
    index: str,
    filename: str,
    id_field: str,
) -> int:
    """Seed a single index from a JSON file. Returns count of indexed docs."""
    docs = _load_json(filename)
    if not docs:
        return 0

    bulk_body = _build_bulk_body(index, docs, id_field)
    raw = es_client.raw if hasattr(es_client, "raw") else es_client
    resp = await raw.bulk(operations=bulk_body, refresh="wait_for")
    response = resp.body if hasattr(resp, "body") else resp

    if response.get("errors"):
        error_items = [
            item for item in response["items"] if "error" in item.get("index", {})
        ]
        logger.error(
            "bulk_index_errors",
            index=index,
            error_count=len(error_items),
            first_error=error_items[0] if error_items else None,
        )
    else:
        logger.info(
            "seed_index_complete",
            index=index,
            documents=len(docs),
        )
    return len(docs)


async def seed_all(es_client=None) -> dict[str, int]:
    """Seed all indices with synthetic data.

    Args:
        es_client: An AsyncElasticsearch client instance.
                   If None, creates one from settings.

    Returns:
        Dict mapping index name to number of documents indexed.
    """
    close_client = False
    if es_client is None:
        try:
            from src.config.settings import get_es_client

            es_client = get_es_client()
            close_client = True
        except ImportError:
            logger.error(
                "cannot_import_settings",
                hint="Run 'uv sync' first, or pass an es_client directly",
            )
            return {}

    results: dict[str, int] = {}
    for index, filename in INDEX_FILE_MAP.items():
        id_field = DOC_ID_FIELD[index]
        count = await seed_index(es_client, index, filename, id_field)
        results[index] = count

    total = sum(results.values())
    logger.info("seed_complete", total_documents=total, indices=results)

    if close_client:
        await es_client.close()

    return results


async def _main() -> None:
    """Entry point when run as a module."""
    results = await seed_all()
    if not results:
        sys.exit(1)
    total = sum(results.values())
    # Use structlog for output, not print
    logger.info("seeding_finished", total=total, breakdown=results)


if __name__ == "__main__":
    asyncio.run(_main())
