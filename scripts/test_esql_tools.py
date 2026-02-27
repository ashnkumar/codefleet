"""Test all ES|QL tool queries against live Elasticsearch with seeded data."""

from __future__ import annotations

import asyncio
import logging

import structlog
from elasticsearch import AsyncElasticsearch

from elastic.setup.config import get_elastic_api_key, get_elastic_url

logger = structlog.get_logger()


async def test_all_tools() -> None:
    es = AsyncElasticsearch(get_elastic_url(), api_key=get_elastic_api_key())
    passed = 0
    failed = 0

    async def run_test(name: str, query: str, expect_rows: bool = True) -> None:
        nonlocal passed, failed
        try:
            r = await es.esql.query(query=query)
            cols = [c["name"] for c in r["columns"]]
            rows = r["values"]
            if expect_rows and len(rows) == 0:
                logger.warning("test_no_rows", tool=name, columns=cols)
                failed += 1
            else:
                logger.info(
                    "test_passed",
                    tool=name,
                    rows=len(rows),
                    columns=cols[:5],
                )
                passed += 1
                # Print first row summary
                if rows:
                    # Find key columns
                    for col_name in ("task_id", "agent_id", "title", "name", "status"):
                        if col_name in cols:
                            idx = cols.index(col_name)
                            vals = [row[idx] for row in rows[:3]]
                            logger.info("sample_values", column=col_name, values=vals)
        except Exception as e:
            logger.error("test_failed", tool=name, error=str(e))
            failed += 1

    # Tool 1: search_backlog
    await run_test(
        "search_backlog",
        'FROM codefleet-tasks | WHERE status == "pending" | SORT priority DESC, created_at ASC | LIMIT 10',
    )

    # Tool 2: check_agent_status
    await run_test(
        "check_agent_status",
        'FROM codefleet-agents | WHERE status != "offline" | SORT status ASC, last_heartbeat DESC | LIMIT 50',
    )

    # Tool 3: detect_conflicts (may have 0 rows if no recent changes)
    await run_test(
        "detect_conflicts",
        "FROM codefleet-changes | WHERE timestamp > NOW() - 1 hour | STATS agent_count = COUNT_DISTINCT(agent_id), agents = VALUES(agent_id), tasks = VALUES(task_id) BY file_path | WHERE agent_count > 1 | SORT agent_count DESC",
        expect_rows=False,  # No changes seeded in last hour
    )

    # Tool 4: assign_task (lookup by task_id)
    # First get a real task_id
    r = await es.esql.query(
        query='FROM codefleet-tasks | WHERE status == "pending" | LIMIT 1'
    )
    if r["values"]:
        cols = [c["name"] for c in r["columns"]]
        tid = r["values"][0][cols.index("task_id")]
        await run_test(
            "assign_task",
            f'FROM codefleet-tasks | WHERE task_id == "{tid}"',
        )
    else:
        logger.warning("skip_assign_task_test", reason="no pending tasks")

    # Tool 5: review_completed (use broader window since seed data is static)
    await run_test(
        "review_completed",
        'FROM codefleet-tasks | WHERE status == "completed" | SORT completed_at DESC | LIMIT 20',
    )

    # Tool 6: analyze_dependencies
    await run_test(
        "analyze_dependencies",
        'FROM codefleet-tasks | WHERE status IN ("blocked", "pending") | EVAL has_deps = MV_COUNT(depends_on) > 0 | SORT has_deps DESC, priority DESC | LIMIT 20',
    )

    logger.info("test_summary", passed=passed, failed=failed, total=passed + failed)
    await es.close()


def main() -> None:
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )
    asyncio.run(test_all_tools())


if __name__ == "__main__":
    main()
