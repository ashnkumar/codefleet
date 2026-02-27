# CodeFleet — Technical Specification

This document is the **canonical source of truth** for all technical decisions. All agents building this project must reference this spec. If it's not in here, check with the coordinator before implementing.

## Table of Contents

1. [System Architecture](#system-architecture)
2. [Elasticsearch Indices](#elasticsearch-indices)
3. [ES|QL Tool Definitions](#esql-tool-definitions)
4. [Agent Builder Agents](#agent-builder-agents)
5. [Runner Framework](#runner-framework)
6. [Workflows](#workflows)
7. [MCP Configuration](#mcp-configuration)
8. [CLI Interface](#cli-interface)
9. [Kibana Dashboards](#kibana-dashboards)
10. [Elastic Cloud Setup](#elastic-cloud-setup)

---

## System Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    USER INTERFACES                       │
│                                                         │
│  Claude Desktop (MCP)  │  Kibana Chat  │  REST API      │
└────────────────────────┬────────────────────────────────┘
                         │
              ┌──────────▼──────────┐
              │  ELASTIC AGENT      │
              │  BUILDER            │
              │                     │
              │  Fleet Commander    │  ← Main orchestration agent
              │  ├─ search_backlog  │  ← ES|QL tool
              │  ├─ check_agents   │  ← ES|QL tool
              │  ├─ detect_conflicts│  ← ES|QL tool
              │  ├─ assign_task    │  ← ES|QL tool (write)
              │  └─ review_work    │  ← ES|QL tool
              │                     │
              │  Task Planner       │  ← Secondary agent (A2A)
              │  ├─ analyze_deps   │  ← ES|QL tool
              │  └─ suggest_plan   │  ← ES|QL tool
              └──────────┬──────────┘
                         │
              ┌──────────▼──────────┐
              │   ELASTICSEARCH     │
              │                     │
              │  tasks              │  ← Task queue with embeddings
              │  agent_registry     │  ← Agent status tracking
              │  agent_activity     │  ← Event log (all tool calls, changes)
              │  code_changes       │  ← File diffs with embeddings
              │  conflicts          │  ← Detected conflicts
              └──────────┬──────────┘
                         │ (runners poll)
          ┌──────────────┼──────────────┐
          │              │              │
     ┌────▼────┐   ┌────▼────┐   ┌────▼────┐
     │Runner 1 │   │Runner 2 │   │Runner 3 │   ... up to N
     │         │   │         │   │         │
     │Claude   │   │Claude   │   │Claude   │
     │Agent SDK│   │Agent SDK│   │Agent SDK│
     └─────────┘   └─────────┘   └─────────┘
```

### Data Flow

1. User adds tasks to ES (via CLI, API, or Fleet Commander)
2. Fleet Commander searches backlog, identifies priority tasks
3. Fleet Commander assigns tasks → writes assignment to `tasks` index
4. Runners poll `tasks` index for assignments matching their ID
5. Runner picks up task, updates status to `in_progress`
6. Runner launches Claude Agent SDK session with task prompt
7. During execution, runner logs activity to `agent_activity` index
8. Runner logs file changes to `code_changes` index
9. On completion, runner updates task status to `completed`
10. Conflict detection workflow checks `code_changes` for overlapping files
11. Fleet Commander reviews completed work, reports to user

---

## Elasticsearch Indices

### `codefleet-tasks`

The task queue. Every coding task is a document here.

```json
{
  "mappings": {
    "properties": {
      "task_id": { "type": "keyword" },
      "title": { "type": "text", "fields": { "keyword": { "type": "keyword" } } },
      "description": { "type": "text" },
      "description_embedding": {
        "type": "dense_vector",
        "dims": 384,
        "index": true,
        "similarity": "cosine"
      },
      "status": {
        "type": "keyword"
      },
      "priority": { "type": "integer" },
      "assigned_to": { "type": "keyword" },
      "depends_on": { "type": "keyword" },
      "blocked_by": { "type": "keyword" },
      "file_scope": { "type": "keyword" },
      "labels": { "type": "keyword" },
      "branch_name": { "type": "keyword" },
      "pr_url": { "type": "keyword" },
      "result_summary": { "type": "text" },
      "error_message": { "type": "text" },
      "estimated_complexity": { "type": "keyword" },
      "actual_tokens_used": { "type": "long" },
      "actual_cost_usd": { "type": "float" },
      "actual_duration_ms": { "type": "long" },
      "created_at": { "type": "date" },
      "updated_at": { "type": "date" },
      "assigned_at": { "type": "date" },
      "started_at": { "type": "date" },
      "completed_at": { "type": "date" }
    }
  }
}
```

**Status values**: `pending`, `assigned`, `in_progress`, `completed`, `failed`, `blocked`, `cancelled`

**Priority**: 1 (lowest) to 5 (highest/critical)

**Complexity**: `trivial`, `small`, `medium`, `large`, `xl`

### `codefleet-agents`

Registry of all coding agents (runners).

```json
{
  "mappings": {
    "properties": {
      "agent_id": { "type": "keyword" },
      "name": { "type": "keyword" },
      "type": { "type": "keyword" },
      "status": { "type": "keyword" },
      "current_task_id": { "type": "keyword" },
      "capabilities": { "type": "keyword" },
      "worktree_path": { "type": "keyword" },
      "session_id": { "type": "keyword" },
      "last_heartbeat": { "type": "date" },
      "tasks_completed": { "type": "integer" },
      "tasks_failed": { "type": "integer" },
      "total_tokens_used": { "type": "long" },
      "total_cost_usd": { "type": "float" },
      "created_at": { "type": "date" },
      "updated_at": { "type": "date" }
    }
  }
}
```

**Status values**: `idle`, `working`, `paused`, `offline`, `error`

**Type values**: `claude` (only type for MVP)

### `codefleet-activity`

Event log for all agent activity. Append-only — never update, only insert.

```json
{
  "mappings": {
    "properties": {
      "event_id": { "type": "keyword" },
      "agent_id": { "type": "keyword" },
      "task_id": { "type": "keyword" },
      "event_type": { "type": "keyword" },
      "message": { "type": "text" },
      "files_changed": { "type": "keyword" },
      "tool_name": { "type": "keyword" },
      "tokens_used": { "type": "integer" },
      "cost_usd": { "type": "float" },
      "duration_ms": { "type": "long" },
      "metadata": { "type": "object", "enabled": false },
      "timestamp": { "type": "date" }
    }
  }
}
```

**Event types**: `heartbeat`, `task_started`, `task_completed`, `task_failed`, `file_changed`, `tool_call`, `conflict_detected`, `agent_started`, `agent_stopped`, `error`

### `codefleet-changes`

Tracks file changes made by agents for conflict detection.

```json
{
  "mappings": {
    "properties": {
      "change_id": { "type": "keyword" },
      "agent_id": { "type": "keyword" },
      "task_id": { "type": "keyword" },
      "file_path": { "type": "keyword" },
      "change_type": { "type": "keyword" },
      "branch_name": { "type": "keyword" },
      "commit_sha": { "type": "keyword" },
      "lines_added": { "type": "integer" },
      "lines_removed": { "type": "integer" },
      "timestamp": { "type": "date" }
    }
  }
}
```

**Change types**: `created`, `modified`, `deleted`

### `codefleet-conflicts`

Detected and tracked conflicts between agents.

```json
{
  "mappings": {
    "properties": {
      "conflict_id": { "type": "keyword" },
      "agent_ids": { "type": "keyword" },
      "task_ids": { "type": "keyword" },
      "file_paths": { "type": "keyword" },
      "conflict_type": { "type": "keyword" },
      "status": { "type": "keyword" },
      "resolution": { "type": "text" },
      "detected_at": { "type": "date" },
      "resolved_at": { "type": "date" }
    }
  }
}
```

**Conflict types**: `file_overlap`, `dependency_violation`

**Status values**: `detected`, `resolving`, `resolved`, `escalated`

---

## ES|QL Tool Definitions

Each tool is defined as a JSON config that gets registered with Agent Builder via the REST API. The tool includes a parameterized ES|QL query, natural language description, and parameter definitions.

### Tool 1: `search_backlog`

**Purpose**: Find the highest-priority unblocked tasks ready for assignment.

```json
{
  "id": "search_backlog",
  "type": "esql",
  "description": "Search the task backlog for the highest-priority tasks that are ready to be assigned to agents. Returns pending tasks that have no unresolved dependencies, sorted by priority (highest first) then creation date (oldest first). Use this when you need to find work to assign to idle agents.",
  "tags": ["tasks", "backlog", "assignment"],
  "configuration": {
    "query": "FROM codefleet-tasks | WHERE status == \"pending\" | SORT priority DESC, created_at ASC | LIMIT ?count",
    "params": {
      "count": {
        "type": "number",
        "description": "Maximum number of tasks to return. Default 10.",
        "optional": true,
        "defaultValue": 10
      }
    }
  }
}
```

### Tool 2: `check_agent_status`

**Purpose**: See what all agents are doing right now.

```json
{
  "id": "check_agent_status",
  "type": "esql",
  "description": "Check the current status of all registered coding agents in the fleet, including what task each agent is working on, when they last sent a heartbeat, and their cumulative statistics. Use this to find idle agents for task assignment or detect agents that may be stuck.",
  "tags": ["agents", "status", "monitoring"],
  "configuration": {
    "query": "FROM codefleet-agents | WHERE status != \"offline\" | SORT status ASC, last_heartbeat DESC | LIMIT 50",
    "params": {}
  }
}
```

### Tool 3: `detect_conflicts`

**Purpose**: Find files that multiple agents have changed recently (potential merge conflicts).

```json
{
  "id": "detect_conflicts",
  "type": "esql",
  "description": "Detect potential merge conflicts by finding files that multiple agents have modified within a recent time window. Returns file paths with the count of distinct agents that touched them and which agents and tasks are involved. Use this proactively to prevent merge conflicts before they happen.",
  "tags": ["conflicts", "detection", "files"],
  "configuration": {
    "query": "FROM codefleet-changes | WHERE timestamp > NOW() - 1 hour | STATS agent_count = COUNT_DISTINCT(agent_id), agents = VALUES(agent_id), tasks = VALUES(task_id) BY file_path | WHERE agent_count > 1 | SORT agent_count DESC",
    "params": {}
  }
}
```

**Note on ES|QL syntax**: The exact syntax for `VALUES()` and time math should be verified against the ES|QL reference for the version of Elasticsearch being used. The intent is clear — aggregate by file_path and find those touched by multiple agents. If `VALUES()` is not available, use `MV_DEDUPE(agent_id)` or equivalent.

### Tool 4: `assign_task`

**Purpose**: Assign a task to an agent. This is a write operation.

```json
{
  "id": "assign_task",
  "type": "esql",
  "description": "Assign a specific task to a specific agent by updating the task's status to 'assigned' and setting the assigned_to field. Use this after checking the backlog for available tasks and the agent registry for idle agents. Always verify the agent is idle and the task is pending before assigning.",
  "tags": ["tasks", "assignment", "write"],
  "configuration": {
    "query": "FROM codefleet-tasks | WHERE task_id == ?task_id",
    "params": {
      "task_id": {
        "type": "string",
        "description": "The ID of the task to assign"
      },
      "agent_id": {
        "type": "string",
        "description": "The ID of the agent to assign the task to"
      }
    }
  }
}
```

**Implementation note**: ES|QL is read-only. The actual assignment write must happen through a **workflow** that this tool triggers. The tool queries the task to verify it exists and is pending, then the agent calls a workflow tool to perform the update. See the `task_assignment` workflow below.

**Alternative approach**: Use a dedicated workflow-type tool for the write operation:

```json
{
  "id": "assign_task",
  "type": "workflow",
  "description": "Assign a task to an agent. Updates the task status to 'assigned' and sets the assigned_to field. The agent must be idle and the task must be pending.",
  "tags": ["tasks", "assignment", "write"],
  "configuration": {
    "workflow_id": "task_assignment",
    "params": {
      "task_id": {
        "type": "string",
        "description": "The ID of the task to assign"
      },
      "agent_id": {
        "type": "string",
        "description": "The ID of the agent to assign the task to"
      }
    }
  }
}
```

### Tool 5: `review_completed`

**Purpose**: Review recently completed tasks with results and agent details.

```json
{
  "id": "review_completed",
  "type": "esql",
  "description": "Review recently completed tasks including their results, which agent completed them, how long they took, and token/cost usage. Use this to provide status updates to the user and to verify work quality.",
  "tags": ["tasks", "review", "completed"],
  "configuration": {
    "query": "FROM codefleet-tasks | WHERE status == \"completed\" AND completed_at > NOW() - ?since | SORT completed_at DESC | LIMIT ?count",
    "params": {
      "since": {
        "type": "string",
        "description": "Time window to look back, e.g., '1h', '24h', '7d'. Default '24h'.",
        "optional": true,
        "defaultValue": "24 hours"
      },
      "count": {
        "type": "number",
        "description": "Maximum number of tasks to return. Default 20.",
        "optional": true,
        "defaultValue": 20
      }
    }
  }
}
```

### Tool 6 (Task Planner): `analyze_dependencies`

```json
{
  "id": "analyze_dependencies",
  "type": "esql",
  "description": "Analyze task dependencies to find tasks that are currently blocked and what they're waiting on. Shows the dependency chain so you can identify bottlenecks and prioritize unblocking work.",
  "tags": ["tasks", "dependencies", "planning"],
  "configuration": {
    "query": "FROM codefleet-tasks | WHERE status IN (\"blocked\", \"pending\") | EVAL has_deps = LENGTH(depends_on) > 0 | SORT has_deps DESC, priority DESC | LIMIT ?count",
    "params": {
      "count": {
        "type": "number",
        "description": "Maximum tasks to analyze. Default 20.",
        "optional": true,
        "defaultValue": 20
      }
    }
  }
}
```

---

## Agent Builder Agents

### Fleet Commander

The primary orchestration agent.

```json
{
  "id": "fleet_commander",
  "name": "Fleet Commander",
  "description": "Orchestrates a fleet of AI coding agents. Manages task assignment, monitors agent status, detects conflicts, and reports progress. The central brain of the CodeFleet system.",
  "labels": ["orchestration", "fleet-management"],
  "avatar_color": "#0077CC",
  "avatar_symbol": "⚡",
  "configuration": {
    "instructions": "You are Fleet Commander, the orchestration brain of CodeFleet — a system that manages multiple AI coding agents working in parallel on a shared codebase.\n\nYour responsibilities:\n1. TASK MANAGEMENT: Search the backlog for high-priority unblocked tasks. Match tasks to available agents based on capabilities and current workload.\n2. AGENT MONITORING: Track which agents are idle, working, or stuck. Detect agents that haven't sent heartbeats.\n3. CONFLICT PREVENTION: Proactively check for file conflicts where multiple agents are editing the same files. When detected, pause the lower-priority agent and reassign their task.\n4. STATUS REPORTING: When asked, provide clear summaries of fleet status — how many tasks done, in progress, blocked. Include cost and token metrics.\n5. WORK REVIEW: Review completed tasks for quality signals before marking them as accepted.\n\nGuidelines:\n- Always check agent status before assigning tasks — never assign to a working agent.\n- When assigning, prefer agents that have completed similar tasks (matching capabilities/labels).\n- If a task has file_scope defined, check for conflicts with currently in-progress tasks before assigning.\n- Report costs and token usage when providing status updates.\n- If asked to plan work, delegate to the Task Planner agent for dependency analysis.\n- Be concise in responses. Use tables for status reports. Flag conflicts and blockers prominently.\n- Never make up data — only report what the tools return.",
    "tools": [
      { "tool_ids": ["search_backlog", "check_agent_status", "detect_conflicts", "assign_task", "review_completed"] }
    ]
  }
}
```

### Task Planner

Secondary agent for dependency analysis and work planning. Communicates with Fleet Commander via A2A.

```json
{
  "id": "task_planner",
  "name": "Task Planner",
  "description": "Analyzes task dependencies, suggests execution order, and estimates parallelism potential. Works with Fleet Commander to plan efficient work distribution.",
  "labels": ["planning", "dependencies"],
  "avatar_color": "#00AA55",
  "avatar_symbol": "📋",
  "configuration": {
    "instructions": "You are the Task Planner for CodeFleet. Your job is to analyze the task backlog and create efficient execution plans.\n\nYour responsibilities:\n1. DEPENDENCY ANALYSIS: Examine task dependencies and identify which can run in parallel vs which must be sequential.\n2. EXECUTION PLANNING: Given N available agents, create an optimal assignment plan that maximizes parallelism while respecting dependencies.\n3. BOTTLENECK IDENTIFICATION: Find blocked tasks and suggest which blocking tasks to prioritize.\n4. SCOPE ESTIMATION: Based on task descriptions and file scope, estimate relative complexity.\n\nWhen creating a plan, output a structured table with: Task ID, Title, Depends On, Suggested Agent, Order/Phase, and reasoning.\n\nAlways consider file scope overlap — tasks touching the same files should not run in parallel.",
    "tools": [
      { "tool_ids": ["search_backlog", "analyze_dependencies"] }
    ]
  }
}
```

---

## Runner Framework

### Architecture

Each runner is a Python async process that:
1. Registers itself in the `codefleet-agents` index on startup
2. Sends heartbeats every 30 seconds
3. Polls the `codefleet-tasks` index for tasks assigned to it
4. When a task is found, launches a Claude Agent SDK session
5. Streams activity events to `codefleet-activity` index
6. Logs file changes to `codefleet-changes` index
7. Updates task status on completion/failure
8. Returns to polling for next task

### `src/config/settings.py`

Shared configuration and Elasticsearch client.

```python
# Key components:
# - Settings class (pydantic BaseSettings, loads from .env)
# - ElasticClient class (async httpx wrapper for ES operations)
#   - index_document(index, doc) → index a document
#   - search(index, query) → search an index
#   - update_document(index, doc_id, partial_doc) → partial update
#   - bulk_index(index, docs) → bulk index documents
# - get_settings() → singleton
# - get_es_client() → singleton
```

**Settings fields:**
```python
class Settings(BaseSettings):
    elastic_url: str  # Elasticsearch endpoint URL (Serverless)
    elastic_api_key: str
    kibana_url: str
    kibana_api_key: str
    anthropic_api_key: str
    poll_interval: int = 5  # seconds
    max_runners: int = 5
    heartbeat_interval: int = 30  # seconds
    log_level: str = "INFO"

    model_config = SettingsConfigDict(env_file=".env")
```

**ElasticClient**: Use the `elasticsearch` Python client (`elasticsearch[async]`). For Serverless, initialize with the endpoint URL and api_key (NOT cloud_id):
```python
from elasticsearch import AsyncElasticsearch
es = AsyncElasticsearch(elastic_url, api_key=elastic_api_key)
```

### `src/runners/base.py`

Base runner class.

```python
class BaseRunner:
    """Base class for coding agent runners.

    Lifecycle:
    1. register() → creates entry in codefleet-agents index
    2. start() → begins poll loop
    3. poll_for_task() → queries codefleet-tasks for assigned work
    4. execute_task(task) → abstract, implemented by subclass
    5. report_activity(event) → logs to codefleet-activity
    6. report_file_change(change) → logs to codefleet-changes
    7. complete_task(task, result) → updates task status
    8. fail_task(task, error) → updates task status to failed
    9. heartbeat() → periodic heartbeat update
    10. shutdown() → sets status to offline
    """

    def __init__(self, name: str, agent_type: str, capabilities: list[str]):
        ...

    async def register(self) -> str:
        """Register this runner in the agent registry. Returns agent_id."""
        ...

    async def start(self):
        """Main loop: heartbeat + poll for tasks."""
        ...

    async def poll_for_task(self) -> Task | None:
        """Query ES for tasks assigned to this agent."""
        ...

    async def execute_task(self, task: Task) -> TaskResult:
        """Abstract. Subclass implements actual execution."""
        raise NotImplementedError

    async def report_activity(self, event: ActivityEvent):
        """Log an activity event to codefleet-activity."""
        ...

    async def report_file_change(self, change: FileChange):
        """Log a file change to codefleet-changes."""
        ...

    async def complete_task(self, task: Task, result: TaskResult):
        """Mark task as completed, update agent status to idle."""
        ...

    async def fail_task(self, task: Task, error: str):
        """Mark task as failed, update agent status to idle."""
        ...

    async def heartbeat(self):
        """Send heartbeat to agent registry."""
        ...

    async def shutdown(self):
        """Set agent status to offline."""
        ...
```

### `src/runners/claude_runner.py`

Claude Agent SDK runner implementation.

```python
class ClaudeRunner(BaseRunner):
    """Runner that executes tasks using the Claude Agent SDK.

    Uses `claude_agent_sdk.query()` to run coding tasks.
    Configures appropriate permissions and tools based on task type.
    Captures session events via hooks for activity logging.
    """

    def __init__(self, name: str, workdir: str, capabilities: list[str]):
        super().__init__(name=name, agent_type="claude", capabilities=capabilities)
        self.workdir = workdir

    async def execute_task(self, task: Task) -> TaskResult:
        """Execute a coding task using Claude Agent SDK.

        1. Build prompt from task title + description
        2. Configure allowed tools based on task type
        3. Set up hooks for activity tracking
        4. Run query() and stream results
        5. Parse results and file changes
        6. Return TaskResult
        """
        ...
```

**Key implementation details:**

- **Prompt construction**: Combine task title, description, and any file_scope hints into a focused prompt. Prepend with "You are working on the following task for the CodeFleet system:" context.
- **Allowed tools**: For coding tasks: `["Read", "Edit", "Write", "Bash", "Glob", "Grep"]`. Restrict `Bash` to safe patterns if possible.
- **Permission mode**: Use `"acceptEdits"` — auto-accept file edits but require approval for destructive bash commands. For fully autonomous mode, use `"bypassPermissions"`.
- **Hooks**: Use `PostToolUse` hooks to capture file changes (when tool is Edit/Write) and log them to ES.
- **Working directory**: Each runner should work in a git worktree to isolate changes. The `workdir` param points to the worktree root.
- **Session management**: Capture the session_id from the init message and store it in the agent registry for potential resumption.

### `src/runners/manager.py`

Manages multiple runners.

```python
class FleetManager:
    """Manages a fleet of Claude runners.

    Responsibilities:
    - Start/stop N runners
    - Monitor runner health (detect stuck/crashed runners)
    - Auto-restart failed runners
    - Graceful shutdown on SIGTERM/SIGINT
    """

    def __init__(self, num_runners: int, workdir: str):
        ...

    async def start(self):
        """Start all runners and monitor loop."""
        ...

    async def stop(self):
        """Gracefully stop all runners."""
        ...

    async def health_check(self):
        """Check for runners that haven't heartbeated recently."""
        ...
```

**Implementation details:**
- Use `asyncio.TaskGroup` to run multiple runners concurrently
- Each runner gets a unique name: `claude-runner-1`, `claude-runner-2`, etc.
- Health check runs every 60 seconds; if a runner hasn't heartbeated in 2 minutes, restart it
- Handle SIGTERM/SIGINT for graceful shutdown

### Data Models

All shared data models live in `src/models.py`:

```python
from pydantic import BaseModel, Field
from datetime import datetime
from enum import Enum
import uuid

class TaskStatus(str, Enum):
    PENDING = "pending"
    ASSIGNED = "assigned"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"

class AgentStatus(str, Enum):
    IDLE = "idle"
    WORKING = "working"
    PAUSED = "paused"
    OFFLINE = "offline"
    ERROR = "error"

class EventType(str, Enum):
    HEARTBEAT = "heartbeat"
    TASK_STARTED = "task_started"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    FILE_CHANGED = "file_changed"
    TOOL_CALL = "tool_call"
    CONFLICT_DETECTED = "conflict_detected"
    AGENT_STARTED = "agent_started"
    AGENT_STOPPED = "agent_stopped"
    ERROR = "error"

class Task(BaseModel):
    task_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    description: str
    status: TaskStatus = TaskStatus.PENDING
    priority: int = 3
    assigned_to: str | None = None
    depends_on: list[str] = []
    blocked_by: list[str] = []
    file_scope: list[str] = []
    labels: list[str] = []
    branch_name: str | None = None
    result_summary: str | None = None
    error_message: str | None = None
    estimated_complexity: str = "medium"
    actual_tokens_used: int | None = None
    actual_cost_usd: float | None = None
    actual_duration_ms: int | None = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    assigned_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None

class Agent(BaseModel):
    agent_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    type: str = "claude"
    status: AgentStatus = AgentStatus.IDLE
    current_task_id: str | None = None
    capabilities: list[str] = []
    worktree_path: str | None = None
    session_id: str | None = None
    last_heartbeat: datetime = Field(default_factory=datetime.utcnow)
    tasks_completed: int = 0
    tasks_failed: int = 0
    total_tokens_used: int = 0
    total_cost_usd: float = 0.0
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

class ActivityEvent(BaseModel):
    event_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agent_id: str
    task_id: str | None = None
    event_type: EventType
    message: str = ""
    files_changed: list[str] = []
    tool_name: str | None = None
    tokens_used: int | None = None
    cost_usd: float | None = None
    duration_ms: int | None = None
    metadata: dict = {}
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class FileChange(BaseModel):
    change_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agent_id: str
    task_id: str
    file_path: str
    change_type: str  # created, modified, deleted
    branch_name: str | None = None
    commit_sha: str | None = None
    lines_added: int = 0
    lines_removed: int = 0
    timestamp: datetime = Field(default_factory=datetime.utcnow)

class Conflict(BaseModel):
    conflict_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    agent_ids: list[str]
    task_ids: list[str]
    file_paths: list[str]
    conflict_type: str  # file_overlap, dependency_violation
    status: str = "detected"  # detected, resolving, resolved, escalated
    resolution: str | None = None
    detected_at: datetime = Field(default_factory=datetime.utcnow)
    resolved_at: datetime | None = None

class TaskResult(BaseModel):
    success: bool
    summary: str
    files_changed: list[str] = []
    tokens_used: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0
    error: str | None = None
```

---

## Workflows

### `conflict_detection` Workflow

Runs on a schedule (every 60 seconds) to detect file conflicts.

```yaml
name: conflict_detection
description: Detect files modified by multiple agents and create conflict records
trigger:
  type: schedule
  interval: 60s
steps:
  - id: find_overlapping_files
    action: elasticsearch.search
    params:
      index: codefleet-changes
      body:
        size: 0
        query:
          range:
            timestamp:
              gte: "now-5m"
        aggs:
          by_file:
            terms:
              field: file_path
              min_doc_count: 2
            aggs:
              agents:
                terms:
                  field: agent_id
              tasks:
                terms:
                  field: task_id
  - id: create_conflict_records
    action: elasticsearch.bulk
    condition: "{{ steps.find_overlapping_files.result.aggregations.by_file.buckets | length > 0 }}"
    params:
      index: codefleet-conflicts
      body: "{{ steps.find_overlapping_files.result.aggregations.by_file.buckets | map_conflicts }}"
```

**Note**: Exact workflow syntax should follow the Elastic Workflows specification. The above is the intent — a scheduled search that finds files touched by multiple agents, then creates conflict records.

### `task_assignment` Workflow

Triggered by the Fleet Commander agent to assign a task.

```yaml
name: task_assignment
description: Assign a task to an agent by updating both the task and agent records
inputs:
  - task_id
  - agent_id
steps:
  - id: update_task
    action: elasticsearch.update
    params:
      index: codefleet-tasks
      id: "{{ inputs.task_id }}"
      body:
        doc:
          status: assigned
          assigned_to: "{{ inputs.agent_id }}"
          assigned_at: "{{ now }}"
          updated_at: "{{ now }}"
  - id: update_agent
    action: elasticsearch.update
    params:
      index: codefleet-agents
      id: "{{ inputs.agent_id }}"
      body:
        doc:
          status: working
          current_task_id: "{{ inputs.task_id }}"
          updated_at: "{{ now }}"
  - id: log_activity
    action: elasticsearch.index
    params:
      index: codefleet-activity
      body:
        event_type: task_started
        agent_id: "{{ inputs.agent_id }}"
        task_id: "{{ inputs.task_id }}"
        message: "Task assigned to agent"
        timestamp: "{{ now }}"
```

### `agent_completion` Workflow

Triggered when a runner completes a task. Unblocks dependent tasks.

```yaml
name: agent_completion
description: Handle task completion - update statuses and unblock dependent tasks
inputs:
  - task_id
  - agent_id
steps:
  - id: find_dependent_tasks
    action: elasticsearch.search
    params:
      index: codefleet-tasks
      body:
        query:
          term:
            depends_on: "{{ inputs.task_id }}"
  - id: unblock_tasks
    action: elasticsearch.bulk_update
    condition: "{{ steps.find_dependent_tasks.result.hits.total.value > 0 }}"
    params:
      index: codefleet-tasks
      updates: "{{ steps.find_dependent_tasks.result.hits.hits | map_unblock(inputs.task_id) }}"
```

---

## MCP Configuration

The Fleet Commander agent is exposed via Elastic Agent Builder's built-in MCP server.

**Endpoint**: `{KIBANA_URL}/api/agent_builder/mcp`

**Claude Desktop config** (`~/.claude/mcp_servers.json` or equivalent):

```json
{
  "codefleet": {
    "command": "npx",
    "args": [
      "mcp-remote",
      "${KIBANA_URL}/api/agent_builder/mcp",
      "--header",
      "Authorization: ApiKey ${KIBANA_API_KEY}"
    ]
  }
}
```

**Required Kibana API key privileges:**
- Cluster: `monitor_inference`
- Indices: `read`, `view_index_metadata` on `codefleet-*`
- Application: `feature_agentBuilder.read` on `kibana-.kibana`

---

## CLI Interface

### `src/cli/main.py`

Uses `click` for CLI.

```
codefleet start [--runners N] [--workdir PATH]
    Start the fleet manager with N runners

codefleet run-single --name NAME [--workdir PATH]
    Run a single runner (useful for debugging)

codefleet add-task --title TITLE --description DESC [--priority N] [--labels L1,L2] [--file-scope F1,F2] [--depends-on T1,T2]
    Add a task to the backlog

codefleet list-tasks [--status STATUS] [--limit N]
    List tasks from the backlog

codefleet status
    Show fleet status (agents, tasks, conflicts)

codefleet setup
    Run all Elastic setup (indices, tools, agents, workflows)

codefleet seed
    Seed synthetic test data
```

---

## Kibana Dashboards

### Fleet Overview Dashboard

**Panels:**

1. **Fleet Status** (Metric) — Count of agents by status (idle/working/offline)
2. **Task Pipeline** (Metric) — Count of tasks by status (pending/assigned/in_progress/completed/failed)
3. **Agent Activity Timeline** (Line chart) — Events over time by agent
4. **Active Conflicts** (Table) — Current unresolved conflicts with file paths and agents
5. **Task Throughput** (Bar chart) — Completed tasks per hour
6. **Cost Tracker** (Metric) — Total tokens and cost across all agents
7. **Recent Activity** (Log stream) — Latest activity events

---

## Elastic Cloud Setup

### Step-by-step (done manually by coordinator):

1. Go to https://cloud.elastic.co/registration?cta=hackathon
2. Create a Serverless project (Elasticsearch type)
3. Note the Cloud ID and create an API key with full access to `codefleet-*` indices
4. Note the Kibana URL
5. Create a Kibana API key with Agent Builder privileges
6. Fill in `.env` with all values
7. Run `codefleet setup` to create indices, tools, agents, workflows
8. Run `codefleet seed` to populate test data
9. Verify in Kibana that Agent Builder shows Fleet Commander and Task Planner
10. Configure MCP in Claude Desktop (optional, for demo)

### Index Template (Optional)

For production, create an index template to handle dynamic index creation:

```json
PUT _index_template/codefleet
{
  "index_patterns": ["codefleet-*"],
  "template": {
    "settings": {
      "number_of_shards": 1,
      "number_of_replicas": 1
    }
  }
}
```
