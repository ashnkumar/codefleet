"""Create Kibana dashboards via the Saved Objects API.

Creates data views (index patterns) and a Fleet Overview dashboard
with all panels defined programmatically.

Usage:
    uv run python dashboards/create_dashboards.py

Requires KIBANA_URL and KIBANA_API_KEY environment variables.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import httpx
import structlog
from dotenv import load_dotenv

logger = structlog.get_logger(__name__)

load_dotenv()

KIBANA_URL = os.environ.get("KIBANA_URL", "").rstrip("/")
KIBANA_API_KEY = os.environ.get("KIBANA_API_KEY", "")

DATA_VIEWS = [
    {
        "id": "codefleet-tasks-dv",
        "name": "codefleet-tasks",
        "title": "codefleet-tasks",
        "timeFieldName": "created_at",
    },
    {
        "id": "codefleet-agents-dv",
        "name": "codefleet-agents",
        "title": "codefleet-agents",
        "timeFieldName": "last_heartbeat",
    },
    {
        "id": "codefleet-activity-dv",
        "name": "codefleet-activity",
        "title": "codefleet-activity",
        "timeFieldName": "timestamp",
    },
    {
        "id": "codefleet-changes-dv",
        "name": "codefleet-changes",
        "title": "codefleet-changes",
        "timeFieldName": "timestamp",
    },
    {
        "id": "codefleet-conflicts-dv",
        "name": "codefleet-conflicts",
        "title": "codefleet-conflicts",
        "timeFieldName": "detected_at",
    },
]

# Lens visualization state definitions for each panel
PANEL_DEFINITIONS = [
    {
        "id": "codefleet-panel-fleet-status",
        "title": "Fleet Status",
        "description": "Agent count by status",
        "visualizationType": "lnsMetric",
        "state": {
            "datasourceStates": {
                "formBased": {
                    "layers": {
                        "layer1": {
                            "columns": {
                                "col1": {
                                    "operationType": "count",
                                    "label": "Agents",
                                    "dataType": "number",
                                    "isBucketed": False,
                                },
                                "col2": {
                                    "operationType": "terms",
                                    "sourceField": "status",
                                    "label": "Status",
                                    "dataType": "string",
                                    "isBucketed": True,
                                    "params": {"size": 5, "orderBy": {"type": "column", "columnId": "col1"}, "orderDirection": "desc"},
                                },
                            },
                            "columnOrder": ["col2", "col1"],
                            "indexPatternId": "codefleet-agents-dv",
                        }
                    }
                }
            },
            "visualization": {
                "layerId": "layer1",
                "layerType": "data",
                "metricAccessor": "col1",
                "breakdownByAccessor": "col2",
            },
        },
    },
    {
        "id": "codefleet-panel-task-pipeline",
        "title": "Task Pipeline",
        "description": "Task count by status",
        "visualizationType": "lnsMetric",
        "state": {
            "datasourceStates": {
                "formBased": {
                    "layers": {
                        "layer1": {
                            "columns": {
                                "col1": {
                                    "operationType": "count",
                                    "label": "Tasks",
                                    "dataType": "number",
                                    "isBucketed": False,
                                },
                                "col2": {
                                    "operationType": "terms",
                                    "sourceField": "status",
                                    "label": "Status",
                                    "dataType": "string",
                                    "isBucketed": True,
                                    "params": {"size": 10, "orderBy": {"type": "column", "columnId": "col1"}, "orderDirection": "desc"},
                                },
                            },
                            "columnOrder": ["col2", "col1"],
                            "indexPatternId": "codefleet-tasks-dv",
                        }
                    }
                }
            },
            "visualization": {
                "layerId": "layer1",
                "layerType": "data",
                "metricAccessor": "col1",
                "breakdownByAccessor": "col2",
            },
        },
    },
    {
        "id": "codefleet-panel-activity-timeline",
        "title": "Agent Activity Timeline",
        "description": "Events over time by agent",
        "visualizationType": "lnsXY",
        "state": {
            "datasourceStates": {
                "formBased": {
                    "layers": {
                        "layer1": {
                            "columns": {
                                "col1": {
                                    "operationType": "count",
                                    "label": "Events",
                                    "dataType": "number",
                                    "isBucketed": False,
                                },
                                "col2": {
                                    "operationType": "date_histogram",
                                    "sourceField": "timestamp",
                                    "label": "Time",
                                    "dataType": "date",
                                    "isBucketed": True,
                                    "params": {"interval": "auto"},
                                },
                                "col3": {
                                    "operationType": "terms",
                                    "sourceField": "agent_id",
                                    "label": "Agent",
                                    "dataType": "string",
                                    "isBucketed": True,
                                    "params": {"size": 5},
                                },
                            },
                            "columnOrder": ["col2", "col3", "col1"],
                            "indexPatternId": "codefleet-activity-dv",
                        }
                    }
                }
            },
            "visualization": {
                "layerId": "layer1",
                "layerType": "data",
                "accessors": ["col1"],
                "xAccessor": "col2",
                "splitAccessor": "col3",
                "preferredSeriesType": "line",
            },
        },
    },
    {
        "id": "codefleet-panel-active-conflicts",
        "title": "Active Conflicts",
        "description": "Unresolved conflicts with file paths and agents",
        "visualizationType": "lnsDatatable",
        "state": {
            "datasourceStates": {
                "formBased": {
                    "layers": {
                        "layer1": {
                            "columns": {
                                "col1": {"operationType": "terms", "sourceField": "conflict_id", "label": "Conflict", "dataType": "string", "isBucketed": True, "params": {"size": 20}},
                                "col2": {"operationType": "terms", "sourceField": "file_paths", "label": "Files", "dataType": "string", "isBucketed": True, "params": {"size": 20}},
                                "col3": {"operationType": "terms", "sourceField": "agent_ids", "label": "Agents", "dataType": "string", "isBucketed": True, "params": {"size": 10}},
                                "col4": {"operationType": "terms", "sourceField": "status", "label": "Status", "dataType": "string", "isBucketed": True, "params": {"size": 5}},
                                "col5": {"operationType": "count", "label": "Count", "dataType": "number", "isBucketed": False},
                            },
                            "columnOrder": ["col1", "col2", "col3", "col4", "col5"],
                            "indexPatternId": "codefleet-conflicts-dv",
                        }
                    }
                }
            },
            "visualization": {
                "layerId": "layer1",
                "layerType": "data",
                "columns": [
                    {"columnId": "col1"},
                    {"columnId": "col2"},
                    {"columnId": "col3"},
                    {"columnId": "col4"},
                    {"columnId": "col5"},
                ],
            },
        },
    },
    {
        "id": "codefleet-panel-task-throughput",
        "title": "Task Throughput",
        "description": "Completed tasks per hour",
        "visualizationType": "lnsXY",
        "state": {
            "datasourceStates": {
                "formBased": {
                    "layers": {
                        "layer1": {
                            "columns": {
                                "col1": {
                                    "operationType": "count",
                                    "label": "Completed Tasks",
                                    "dataType": "number",
                                    "isBucketed": False,
                                },
                                "col2": {
                                    "operationType": "date_histogram",
                                    "sourceField": "completed_at",
                                    "label": "Completed At",
                                    "dataType": "date",
                                    "isBucketed": True,
                                    "params": {"interval": "1h"},
                                },
                            },
                            "columnOrder": ["col2", "col1"],
                            "indexPatternId": "codefleet-tasks-dv",
                        }
                    }
                }
            },
            "visualization": {
                "layerId": "layer1",
                "layerType": "data",
                "accessors": ["col1"],
                "xAccessor": "col2",
                "preferredSeriesType": "bar",
            },
        },
    },
    {
        "id": "codefleet-panel-cost-tracker",
        "title": "Cost Tracker",
        "description": "Total tokens and cost across all agents",
        "visualizationType": "lnsMetric",
        "state": {
            "datasourceStates": {
                "formBased": {
                    "layers": {
                        "layer1": {
                            "columns": {
                                "col1": {
                                    "operationType": "sum",
                                    "sourceField": "total_tokens_used",
                                    "label": "Total Tokens",
                                    "dataType": "number",
                                    "isBucketed": False,
                                },
                                "col2": {
                                    "operationType": "sum",
                                    "sourceField": "total_cost_usd",
                                    "label": "Total Cost (USD)",
                                    "dataType": "number",
                                    "isBucketed": False,
                                },
                            },
                            "columnOrder": ["col1", "col2"],
                            "indexPatternId": "codefleet-agents-dv",
                        }
                    }
                }
            },
            "visualization": {
                "layerId": "layer1",
                "layerType": "data",
                "metricAccessor": "col1",
                "secondaryMetricAccessor": "col2",
            },
        },
    },
    {
        "id": "codefleet-panel-recent-activity",
        "title": "Recent Activity",
        "description": "Latest activity events across all agents",
        "visualizationType": "lnsDatatable",
        "state": {
            "datasourceStates": {
                "formBased": {
                    "layers": {
                        "layer1": {
                            "columns": {
                                "col1": {"operationType": "date_histogram", "sourceField": "timestamp", "label": "Time", "dataType": "date", "isBucketed": True, "params": {"interval": "auto"}},
                                "col2": {"operationType": "terms", "sourceField": "agent_id", "label": "Agent", "dataType": "string", "isBucketed": True, "params": {"size": 10}},
                                "col3": {"operationType": "terms", "sourceField": "event_type", "label": "Event", "dataType": "string", "isBucketed": True, "params": {"size": 10}},
                                "col4": {"operationType": "count", "label": "Count", "dataType": "number", "isBucketed": False},
                            },
                            "columnOrder": ["col1", "col2", "col3", "col4"],
                            "indexPatternId": "codefleet-activity-dv",
                        }
                    }
                }
            },
            "visualization": {
                "layerId": "layer1",
                "layerType": "data",
                "columns": [
                    {"columnId": "col1"},
                    {"columnId": "col2"},
                    {"columnId": "col3"},
                    {"columnId": "col4"},
                ],
            },
        },
    },
]

# Dashboard panel layout configuration
DASHBOARD_PANELS = [
    # Row 1: Status metrics
    {"panelIndex": "0", "panelRefId": "codefleet-panel-fleet-status", "gridData": {"x": 0, "y": 0, "w": 16, "h": 8, "i": "0"}},
    {"panelIndex": "1", "panelRefId": "codefleet-panel-task-pipeline", "gridData": {"x": 16, "y": 0, "w": 16, "h": 8, "i": "1"}},
    {"panelIndex": "2", "panelRefId": "codefleet-panel-cost-tracker", "gridData": {"x": 32, "y": 0, "w": 16, "h": 8, "i": "2"}},
    # Row 2: Timeline and conflicts
    {"panelIndex": "3", "panelRefId": "codefleet-panel-activity-timeline", "gridData": {"x": 0, "y": 8, "w": 32, "h": 12, "i": "3"}},
    {"panelIndex": "4", "panelRefId": "codefleet-panel-active-conflicts", "gridData": {"x": 32, "y": 8, "w": 16, "h": 12, "i": "4"}},
    # Row 3: Throughput and activity log
    {"panelIndex": "5", "panelRefId": "codefleet-panel-task-throughput", "gridData": {"x": 0, "y": 20, "w": 24, "h": 12, "i": "5"}},
    {"panelIndex": "6", "panelRefId": "codefleet-panel-recent-activity", "gridData": {"x": 24, "y": 20, "w": 24, "h": 12, "i": "6"}},
]


def _get_headers() -> dict[str, str]:
    return {
        "Authorization": f"ApiKey {KIBANA_API_KEY}",
        "kbn-xsrf": "true",
        "Content-Type": "application/json",
    }


async def create_data_views(client: httpx.AsyncClient) -> None:
    """Create Kibana data views (index patterns) for all codefleet indices."""
    for dv in DATA_VIEWS:
        body = {
            "data_view": {
                "id": dv["id"],
                "title": dv["title"],
                "name": dv["name"],
                "timeFieldName": dv["timeFieldName"],
            },
            "override": True,
        }
        resp = await client.post(
            f"{KIBANA_URL}/api/data_views/data_view",
            headers=_get_headers(),
            json=body,
        )
        if resp.status_code in (200, 201):
            logger.info("data_view_created", name=dv["name"], id=dv["id"])
        elif resp.status_code == 409:
            logger.info("data_view_exists", name=dv["name"])
        else:
            logger.error(
                "data_view_error",
                name=dv["name"],
                status=resp.status_code,
                body=resp.text[:500],
            )


async def import_ndjson(client: httpx.AsyncClient) -> None:
    """Import the NDJSON dashboard file via Saved Objects API."""
    ndjson_path = Path(__file__).parent / "fleet_overview.ndjson"
    if not ndjson_path.exists():
        logger.error("ndjson_not_found", path=str(ndjson_path))
        return

    with open(ndjson_path, "rb") as f:
        resp = await client.post(
            f"{KIBANA_URL}/api/saved_objects/_import",
            headers={
                "Authorization": f"ApiKey {KIBANA_API_KEY}",
                "kbn-xsrf": "true",
            },
            params={"overwrite": "true"},
            files={"file": ("fleet_overview.ndjson", f, "application/x-ndjson")},
        )

    if resp.status_code == 200:
        result = resp.json()
        logger.info(
            "ndjson_imported",
            success=result.get("success"),
            count=result.get("successCount"),
        )
    else:
        logger.error(
            "ndjson_import_error",
            status=resp.status_code,
            body=resp.text[:500],
        )


async def main() -> None:
    """Create data views and import dashboard."""
    if not KIBANA_URL or not KIBANA_API_KEY:
        logger.error(
            "missing_config",
            hint="Set KIBANA_URL and KIBANA_API_KEY in your .env file",
        )
        sys.exit(1)

    async with httpx.AsyncClient(timeout=30.0) as client:
        logger.info("creating_data_views")
        await create_data_views(client)

        logger.info("importing_dashboard_ndjson")
        await import_ndjson(client)

        logger.info("dashboard_setup_complete",
                     hint="Open Kibana > Dashboards > 'CodeFleet - Fleet Overview'")


if __name__ == "__main__":
    asyncio.run(main())
