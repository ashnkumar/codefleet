"""Elastic setup scripts for creating indices, tools, agents, and workflows."""

from elastic.setup.create_indices import create_indices
from elastic.setup.create_tools import create_tools
from elastic.setup.create_agents import create_agents
from elastic.setup.create_workflows import create_workflows

__all__ = ["create_indices", "create_tools", "create_agents", "create_workflows"]
