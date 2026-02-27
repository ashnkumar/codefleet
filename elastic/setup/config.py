"""Shared configuration for setup scripts.

Loads connection details from environment variables (via .env file).
This is self-contained so setup scripts can run independently of the
main src.config.settings module.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root
_project_root = Path(__file__).resolve().parent.parent.parent
load_dotenv(_project_root / ".env")


def get_elastic_url() -> str:
    val = os.environ.get("ELASTIC_URL", "")
    if not val:
        raise EnvironmentError("ELASTIC_URL not set in environment or .env file")
    return val


def get_elastic_api_key() -> str:
    val = os.environ.get("ELASTIC_API_KEY", "")
    if not val:
        raise EnvironmentError("ELASTIC_API_KEY not set in environment or .env file")
    return val


def get_kibana_url() -> str:
    val = os.environ.get("KIBANA_URL", "").rstrip("/")
    if not val:
        raise EnvironmentError("KIBANA_URL not set in environment or .env file")
    return val


def get_kibana_api_key() -> str:
    val = os.environ.get("KIBANA_API_KEY", "")
    if not val:
        raise EnvironmentError("KIBANA_API_KEY not set in environment or .env file")
    return val


# Directory containing JSON definition files
ELASTIC_DIR = _project_root / "elastic"
INDICES_DIR = ELASTIC_DIR / "indices"
TOOLS_DIR = ELASTIC_DIR / "tools"
AGENTS_DIR = ELASTIC_DIR / "agents"
WORKFLOWS_DIR = ELASTIC_DIR / "workflows"

# Re-export from centralized constants
from src.config.constants import INDEX_NAMES, INDEX_PREFIX  # noqa: E402
