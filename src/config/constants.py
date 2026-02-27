"""Centralized constants for CodeFleet.

All configurable values that aren't environment-specific live here.
Environment-specific values (URLs, API keys) stay in Settings (.env).
Change values here and they flow through the entire system.
"""

# ---------------------------------------------------------------------------
# Elasticsearch Index Names
# ---------------------------------------------------------------------------
INDEX_PREFIX = "codefleet"

IDX_TASKS = f"{INDEX_PREFIX}-tasks"
IDX_AGENTS = f"{INDEX_PREFIX}-agents"
IDX_ACTIVITY = f"{INDEX_PREFIX}-activity"
IDX_CHANGES = f"{INDEX_PREFIX}-changes"
IDX_CONFLICTS = f"{INDEX_PREFIX}-conflicts"

ALL_INDICES = [IDX_TASKS, IDX_AGENTS, IDX_ACTIVITY, IDX_CHANGES, IDX_CONFLICTS]

# Mapping from short name to full index name (used by setup scripts)
INDEX_NAMES = {
    "tasks": IDX_TASKS,
    "agents": IDX_AGENTS,
    "activity": IDX_ACTIVITY,
    "changes": IDX_CHANGES,
    "conflicts": IDX_CONFLICTS,
}

# Document ID fields per index (used by seed script)
DOC_ID_FIELDS = {
    IDX_TASKS: "task_id",
    IDX_AGENTS: "agent_id",
    IDX_ACTIVITY: "event_id",
    IDX_CHANGES: "change_id",
    IDX_CONFLICTS: "conflict_id",
}

# ---------------------------------------------------------------------------
# Claude Agent SDK Configuration
# ---------------------------------------------------------------------------
CLAUDE_MODEL = "claude-sonnet-4-6"  # Model for runner agent sessions

CLAUDE_ALLOWED_TOOLS = [
    "Read", "Edit", "Write", "Bash", "Glob", "Grep",
]

CLAUDE_FILE_CHANGE_TOOLS = {"Edit", "Write"}  # Tools that modify files

CLAUDE_PERMISSION_MODE = "bypassPermissions"  # For autonomous operation
# Options: "default", "acceptEdits", "bypassPermissions"

CLAUDE_MAX_TURNS = 50  # Max agent loop iterations per task

# ---------------------------------------------------------------------------
# Elasticsearch Inference (Embeddings)
# ---------------------------------------------------------------------------
INFERENCE_ENDPOINT_ID = "codefleet-embeddings"
EMBEDDING_MODEL = ".multilingual-e5-small-elasticsearch"  # Built-in ES model
EMBEDDING_DIMS = 384  # Dimension of embedding vectors

# ---------------------------------------------------------------------------
# Fleet Defaults
# ---------------------------------------------------------------------------
DEFAULT_NUM_RUNNERS = 3
DEFAULT_POLL_INTERVAL = 5  # seconds
DEFAULT_HEARTBEAT_INTERVAL = 30  # seconds
DEFAULT_MAX_RUNNERS = 5

STALE_THRESHOLD_MINUTES = 2  # Runner considered stale after this many minutes
HEALTH_CHECK_INTERVAL = 60  # seconds between health checks

# ---------------------------------------------------------------------------
# ES Client Defaults
# ---------------------------------------------------------------------------
ES_REQUEST_TIMEOUT = 30  # seconds
ES_MAX_RETRIES = 3
ES_DEFAULT_SEARCH_SIZE = 10

# ---------------------------------------------------------------------------
# CLI Defaults
# ---------------------------------------------------------------------------
CLI_DEFAULT_TASK_LIMIT = 20
CLI_DEFAULT_AGENT_LIMIT = 50
CLI_DEFAULT_PRIORITY = 3

# ---------------------------------------------------------------------------
# Elastic Agent Builder API
# ---------------------------------------------------------------------------
AGENT_BUILDER_TOOLS_PATH = "/api/agent_builder/tools"
AGENT_BUILDER_AGENTS_PATH = "/api/agent_builder/agents"
AGENT_BUILDER_MCP_PATH = "/api/agent_builder/mcp"

# HTTP timeout for Agent Builder API calls
AGENT_BUILDER_TIMEOUT = 30.0  # seconds

# ---------------------------------------------------------------------------
# Elastic Workflows
# ---------------------------------------------------------------------------
WORKFLOWS_API_PATH = "/api/workflows"

# Workflow IDs — set after deployment via create_workflows.py.
# These are read from the deployed manifest at runtime.
COMPLETION_WORKFLOW_ID = ""  # handle_task_completion — set at startup

# ---------------------------------------------------------------------------
# Logging / Output
# ---------------------------------------------------------------------------
AGENT_LOG_DIR = "logs/agents"  # Directory for per-agent activity logs
