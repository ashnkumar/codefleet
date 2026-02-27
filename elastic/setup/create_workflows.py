"""Deploy Elastic Workflows and register workflow-type tools in Agent Builder.

Reads each YAML file in elastic/workflows/, deploys it to the Kibana
Workflows API, then registers any matching workflow-type tools in Agent
Builder (linking them to the deployed workflow ID).

Usage:
    uv run python -m elastic.setup.create_workflows
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

import httpx
import structlog

from elastic.setup.config import (
    TOOLS_DIR,
    WORKFLOWS_DIR,
    get_kibana_api_key,
    get_kibana_url,
)

logger = structlog.get_logger()

# API paths
WORKFLOWS_API_PATH = "/api/workflows"
TOOLS_API_PATH = "/api/agent_builder/tools"


async def _list_existing_workflows(
    client: httpx.AsyncClient,
    kibana_url: str,
    headers: dict[str, str],
) -> dict[str, str]:
    """Fetch all existing workflows from the API and return name->id mapping.

    The Workflows API doesn't have a list endpoint, so we track deployed
    workflow IDs in a local manifest file.
    """
    manifest_path = WORKFLOWS_DIR / ".deployed_workflows.json"
    if manifest_path.exists():
        with open(manifest_path) as f:
            return json.load(f)
    return {}


async def _save_manifest(deployed: dict[str, str]) -> None:
    """Persist the workflow name -> ID mapping."""
    manifest_path = WORKFLOWS_DIR / ".deployed_workflows.json"
    with open(manifest_path, "w") as f:
        json.dump(deployed, f, indent=2)
    logger.info("manifest_saved", path=str(manifest_path), count=len(deployed))


async def _deploy_workflow(
    client: httpx.AsyncClient,
    kibana_url: str,
    headers: dict[str, str],
    yaml_path: Path,
    existing_id: str | None,
) -> str | None:
    """Deploy a single workflow YAML to Kibana. Returns the workflow ID."""
    workflow_name = yaml_path.stem
    with open(yaml_path) as f:
        yaml_content = f.read()

    payload = {"yaml": yaml_content, "name": workflow_name}

    if existing_id:
        # Update existing workflow
        url = f"{kibana_url}{WORKFLOWS_API_PATH}/{existing_id}"
        response = await client.put(url, headers=headers, json=payload)

        if response.status_code in (200, 201):
            data = response.json()
            valid = data.get("valid", False)
            errors = data.get("validationErrors", [])
            if not valid:
                logger.warning(
                    "workflow_updated_invalid",
                    name=workflow_name,
                    errors=errors,
                )
            else:
                logger.info("workflow_updated", name=workflow_name, id=existing_id)
            return existing_id
        else:
            logger.error(
                "workflow_update_failed",
                name=workflow_name,
                status=response.status_code,
                body=response.text,
            )
            return existing_id  # Keep old ID
    else:
        # Create new workflow
        url = f"{kibana_url}{WORKFLOWS_API_PATH}"
        response = await client.post(url, headers=headers, json=payload)

        if response.status_code in (200, 201):
            data = response.json()
            wf_id = data["id"]
            valid = data.get("valid", False)
            errors = data.get("validationErrors", [])

            # Enable the workflow
            enable_resp = await client.put(
                f"{url}/{wf_id}",
                headers=headers,
                json={"enabled": True},
            )
            if enable_resp.status_code in (200, 201):
                enable_data = enable_resp.json()
                valid = enable_data.get("valid", valid)
                errors = enable_data.get("validationErrors", errors)

            if not valid:
                logger.warning(
                    "workflow_created_invalid",
                    name=workflow_name,
                    id=wf_id,
                    errors=errors,
                )
            else:
                logger.info(
                    "workflow_created",
                    name=workflow_name,
                    id=wf_id,
                    enabled=enable_data.get("enabled", False),
                )
            return wf_id
        else:
            logger.error(
                "workflow_creation_failed",
                name=workflow_name,
                status=response.status_code,
                body=response.text,
            )
            return None


async def _register_workflow_tool(
    client: httpx.AsyncClient,
    kibana_url: str,
    headers: dict[str, str],
    tool_def: dict,
    workflow_id: str,
) -> None:
    """Register a workflow-type tool in Agent Builder, linked to a deployed workflow."""
    tool_id = tool_def["id"]

    # Build the registration payload with the real workflow_id
    reg_payload = {
        "id": tool_id,
        "type": "workflow",
        "description": tool_def["description"],
        "tags": tool_def.get("tags", []),
        "configuration": {"workflow_id": workflow_id},
    }

    url = f"{kibana_url}{TOOLS_API_PATH}"
    response = await client.post(url, headers=headers, json=reg_payload)

    already_exists = response.status_code == 409 or (
        response.status_code == 400 and "already exists" in response.text
    )

    if response.status_code in (200, 201):
        logger.info("workflow_tool_created", tool_id=tool_id, workflow_id=workflow_id)
    elif already_exists:
        # Update existing tool
        update_url = f"{url}/{tool_id}"
        update_body = {
            "description": tool_def["description"],
            "tags": tool_def.get("tags", []),
            "configuration": {"workflow_id": workflow_id},
        }
        update_resp = await client.put(update_url, headers=headers, json=update_body)
        if update_resp.status_code in (200, 201):
            logger.info(
                "workflow_tool_updated", tool_id=tool_id, workflow_id=workflow_id
            )
        else:
            logger.error(
                "workflow_tool_update_failed",
                tool_id=tool_id,
                status=update_resp.status_code,
                body=update_resp.text,
            )
    else:
        logger.error(
            "workflow_tool_creation_failed",
            tool_id=tool_id,
            status=response.status_code,
            body=response.text,
        )


async def create_workflows() -> None:
    """Deploy all workflows and register matching workflow-type tools."""
    kibana_url = get_kibana_url()
    api_key = get_kibana_api_key()

    headers = {
        "Authorization": f"ApiKey {api_key}",
        "Content-Type": "application/json",
        "kbn-xsrf": "true",
        "x-elastic-internal-origin": "Kibana",
    }

    # 1. Load existing workflow manifest
    deployed = await _list_existing_workflows(None, kibana_url, headers)

    # 2. Find workflow YAML files (skip hidden files)
    workflow_files = sorted(
        p for p in WORKFLOWS_DIR.glob("*.yaml") if not p.name.startswith(".")
    )
    if not workflow_files:
        logger.warning("no_workflow_files_found", directory=str(WORKFLOWS_DIR))
        return

    # 3. Find workflow-type tool definitions
    workflow_tools: dict[str, dict] = {}
    for tool_file in TOOLS_DIR.glob("*.json"):
        with open(tool_file) as f:
            tool_def = json.load(f)
        if tool_def.get("type") == "workflow":
            wf_name = tool_def.get("_workflow_name", "")
            if wf_name:
                workflow_tools[wf_name] = tool_def

    async with httpx.AsyncClient(timeout=30.0) as client:
        # 4. Deploy each workflow
        for wf_file in workflow_files:
            wf_name = wf_file.stem
            existing_id = deployed.get(wf_name)

            wf_id = await _deploy_workflow(
                client, kibana_url, headers, wf_file, existing_id
            )
            if wf_id:
                deployed[wf_name] = wf_id

                # 5. If there's a matching tool definition, register it
                if wf_name in workflow_tools:
                    await _register_workflow_tool(
                        client,
                        kibana_url,
                        headers,
                        workflow_tools[wf_name],
                        wf_id,
                    )

    # 6. Save manifest
    await _save_manifest(deployed)

    logger.info(
        "all_workflows_deployed",
        total=len(workflow_files),
        tools_registered=len(
            [n for n in workflow_tools if n in deployed]
        ),
    )


def main() -> None:
    """Entry point for running as a module."""
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
    )
    asyncio.run(create_workflows())


if __name__ == "__main__":
    main()
