"""CodeFleet configuration and Elasticsearch client."""

from __future__ import annotations

import logging
from functools import lru_cache
from typing import Any

import structlog
from elasticsearch import AsyncElasticsearch
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

logger: structlog.stdlib.BoundLogger = structlog.get_logger()


class Settings(BaseSettings):
    """Application settings loaded from environment / .env file."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="",
        extra="ignore",
    )

    elastic_url: str = ""
    elastic_api_key: str = ""
    kibana_url: str = ""
    kibana_api_key: str = ""
    anthropic_api_key: str = ""

    # Runner config (env prefix CODEFLEET_)
    poll_interval: int = Field(default=5, alias="CODEFLEET_POLL_INTERVAL")
    max_runners: int = Field(default=5, alias="CODEFLEET_MAX_RUNNERS")
    workdir: str = Field(default=".", alias="CODEFLEET_WORKDIR")
    heartbeat_interval: int = Field(default=30, alias="CODEFLEET_HEARTBEAT_INTERVAL")
    log_level: str = Field(default="INFO", alias="CODEFLEET_LOG_LEVEL")


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings singleton."""
    settings = Settings()
    _configure_logging(settings.log_level)
    return settings


def _configure_logging(level: str) -> None:
    """Configure structlog with JSON output."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


# ---------------------------------------------------------------------------
# Elasticsearch async client wrapper
# ---------------------------------------------------------------------------

_es_client: ElasticClient | None = None


class ElasticClient:
    """Async wrapper around the Elasticsearch Python client.

    Provides simplified methods for common operations used by the runner
    framework: index, search, update, and bulk index.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        settings = settings or get_settings()
        if settings.elastic_url:
            self._es = AsyncElasticsearch(
                settings.elastic_url,
                api_key=settings.elastic_api_key,
                request_timeout=30,
                retry_on_timeout=True,
                max_retries=3,
            )
        else:
            raise ValueError("ELASTIC_URL must be set")

    @property
    def raw(self) -> AsyncElasticsearch:
        """Access the underlying AsyncElasticsearch instance."""
        return self._es

    async def index_document(
        self,
        index: str,
        doc: dict[str, Any],
        doc_id: str | None = None,
    ) -> dict[str, Any]:
        """Index a single document. Returns the ES response."""
        resp = await self._es.index(index=index, id=doc_id, document=doc)
        return resp.body

    async def search(
        self,
        index: str,
        query: dict[str, Any] | None = None,
        *,
        size: int = 10,
        sort: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Search an index. Returns the list of source documents."""
        body: dict[str, Any] = {"size": size}
        if query is not None:
            body["query"] = query
        if sort is not None:
            body["sort"] = sort
        resp = await self._es.search(index=index, body=body)
        results = []
        for hit in resp["hits"]["hits"]:
            doc = hit["_source"]
            # Inject ES _id so workflow-created docs (no task_id field) can be
            # tracked by their actual document ID.
            if "_id" not in doc:
                doc["_id"] = hit["_id"]
            # If the doc has no task_id, use the ES _id
            if "task_id" not in doc:
                doc["task_id"] = hit["_id"]
            # Normalize CSV string fields to lists (workflow creates strings,
            # our code expects arrays).
            for field in ("depends_on", "blocked_by", "file_scope", "labels"):
                val = doc.get(field)
                if isinstance(val, str):
                    doc[field] = [s.strip() for s in val.split(",") if s.strip()]
            results.append(doc)
        return results

    async def update_document(
        self,
        index: str,
        doc_id: str,
        partial_doc: dict[str, Any],
    ) -> dict[str, Any]:
        """Partial-update a document by ID."""
        resp = await self._es.update(index=index, id=doc_id, doc=partial_doc)
        return resp.body

    async def bulk_index(
        self,
        index: str,
        docs: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Bulk-index a list of documents."""
        operations: list[dict[str, Any]] = []
        for doc in docs:
            operations.append({"index": {"_index": index}})
            operations.append(doc)
        resp = await self._es.bulk(operations=operations)
        return resp.body

    async def close(self) -> None:
        """Close the underlying transport."""
        await self._es.close()


def get_es_client() -> ElasticClient:
    """Return a module-level ElasticClient singleton."""
    global _es_client
    if _es_client is None:
        _es_client = ElasticClient()
    return _es_client
