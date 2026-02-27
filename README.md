# CodeFleet

**AI Coding Agent Fleet Commander** — orchestrate parallel Claude Code agents with Elasticsearch as the orchestration platform, not just the database.

CodeFleet is a multi-agent orchestration system that coordinates parallel AI coding agents (via Claude Agent SDK) working on a shared codebase. Elasticsearch is the state backbone. Elastic Agent Builder is the orchestration brain. Elastic Workflows handle assignment, dependency cascading, and health recovery server-side. Local runners simply poll for assignments, execute code, and report results.

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    USER INTERFACES                       │
│  Claude Desktop (MCP)  │  Kibana Chat  │  CLI           │
└────────────────────────┬────────────────────────────────┘
                         │
              ┌──────────▼──────────┐
              │  ELASTIC AGENT      │
              │  BUILDER            │
              │                     │
              │  Fleet Commander    │  ← AI PM agent
              │  ├─ search_backlog  │    8 tools (7 ES|QL + 1 workflow)
              │  ├─ check_agents    │
              │  ├─ detect_conflicts│
              │  ├─ find_similar    │
              │  └─ create_task     │
              └──────────┬──────────┘
                         │
              ┌──────────▼──────────┐
              │  ELASTIC WORKFLOWS  │  ← Orchestration engine
              │                     │
              │  auto_assign_tasks  │  Schedule: 5s
              │  handle_completion  │  Event: task completed
              │  handle_stale       │  Schedule: 60s
              │  create_task        │  Manual (Fleet Commander)
              └──────────┬──────────┘
                         │
              ┌──────────▼──────────┐
              │   ELASTICSEARCH     │
              │                     │
              │  codefleet-tasks    │  Task queue
              │  codefleet-agents   │  Agent registry
              │  codefleet-activity │  Event log
              │  codefleet-changes  │  File diffs
              │  codefleet-conflicts│  Merge conflicts
              └──────────┬──────────┘
                         │ (runners poll)
          ┌──────────────┼──────────────┐
          │              │              │
     ┌────▼────┐   ┌────▼────┐   ┌────▼────┐
     │Runner 1 │   │Runner 2 │   │Runner 3 │   ... up to N
     │Claude   │   │Claude   │   │Claude   │
     │Agent SDK│   │Agent SDK│   │Agent SDK│
     └─────────┘   └─────────┘   └─────────┘
```

**Data flow:**
1. You tell Fleet Commander what to build (via Kibana, MCP, or CLI)
2. Fleet Commander breaks it into tasks with dependencies and creates them in ES via the `create_task` Workflow
3. **Auto-Assignment Workflow** fires every 5 seconds — finds pending unblocked tasks, matches them to idle runners, assigns them
4. Runners poll ES, find their assignments, launch Claude Agent SDK sessions, write code
5. During execution, runners log every event and file change to ES in real-time
6. On completion, runner updates task status → **Task Completion Workflow** fires automatically, unblocks dependents, moves them to pending
7. Cycle repeats: Auto-Assignment Workflow picks up the newly-pending tasks and assigns them to idle runners
8. **Stale Agent Workflow** runs every 60 seconds, re-queues tasks from crashed runners
9. Fleet Commander can answer status questions, reassign work, or review completed tasks — anytime via ES|QL tools

## Why Elasticsearch? (Not Just a Database)

The orchestration logic itself runs natively in Elasticsearch via Elastic Workflows. The local machine only needs thin runners that poll for assignments and execute code.

| What Elastic Provides | What You'd Build Without It |
|---|---|
| **Agent Builder** — Fleet Commander with AI reasoning | Custom chat frontend + tool-calling framework |
| **ES\|QL tools** — 7 expert-defined queries as agent tools | Build parameter routing + query engine from scratch |
| **Elastic Workflows** — auto-assignment, dependency unblocking, health checks | Write a custom job scheduler + state machine in Python |
| **Semantic search** — built-in embeddings for duplicate detection | External embedding API + vector DB setup |
| **Kibana Discover** — real-time activity stream, zero frontend | Build a log viewer + monitoring dashboard |
| **MCP server** — IDE integration out of the box | Build an MCP server from scratch |

## Quick Start

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager
- Elastic Cloud account ([sign up free](https://cloud.elastic.co/registration))
- Anthropic API key (for Claude Agent SDK runners)

### 1. Clone and Install

```bash
git clone https://github.com/ashnkumar/codefleet.git
cd codefleet
uv sync
```

### 2. Configure Environment

```bash
cp .env.example .env
# Edit .env with your credentials:
#   ELASTIC_URL          - Elasticsearch endpoint URL
#   ELASTIC_API_KEY      - ES API key with codefleet-* index access
#   KIBANA_URL           - Kibana URL for Agent Builder API
#   KIBANA_API_KEY       - Kibana API key
#   ANTHROPIC_API_KEY    - For Claude Agent SDK runners
```

### 3. Set Up Elasticsearch

```bash
# Create indices, tools, workflows, and Agent Builder agents
codefleet setup

# Seed with sample data
codefleet seed
```

### 4. Run the Fleet

```bash
# Start 3 runners in parallel
codefleet start --runners 3

# Or run a single runner for debugging
codefleet run-single --name claude-1
```

### 5. Manage Tasks

```bash
# Add a task to the backlog
codefleet add-task \
  --title "Fix authentication timeout" \
  --description "JWT validation race condition in middleware" \
  --priority 5 \
  --labels "bug,auth" \
  --file-scope "src/auth/middleware.py"

# List tasks
codefleet list-tasks --status pending

# Show fleet status
codefleet status
```

## Elastic Workflows

The orchestration logic lives in Elastic, not in Python. Four workflows power CodeFleet:

| Workflow | Trigger | What It Does |
|----------|---------|-------------|
| `create_task` | Manual (called by Fleet Commander) | Indexes a new task document with all fields including semantic embeddings for duplicate detection |
| `auto_assign_tasks` | Scheduled (every 5s) | Queries for pending unblocked tasks and idle agents, assigns tasks to agents, updates both documents |
| `handle_task_completion` | Alert (task status → completed) | Finds all tasks that depend on the completed one, removes it from their `blocked_by` lists, sets fully-unblocked tasks to `pending` |
| `handle_stale_agents` | Scheduled (every 60s) | Finds agents with stale heartbeats (>2 min), marks them offline, re-queues their assigned tasks back to `pending` |

## ES|QL Tools

The Fleet Commander agent's reasoning is grounded in real data through ES|QL tools:

| Tool | What It Does |
|------|-------------|
| `search_backlog` | Find highest-priority unblocked tasks |
| `check_agent_status` | See what all agents are doing right now |
| `detect_conflicts` | Find files modified by multiple agents |
| `assign_task` | Look up task details for assignment verification |
| `review_completed` | Review recently completed work with cost and duration metrics |
| `find_similar_tasks` | Semantic search for duplicate/related tasks via built-in embeddings |
| `create_task` | Create a new task in the backlog (via Workflow) |
| `analyze_dependencies` | Check what's blocked by what |

Queries are expert-defined and parameterized — the LLM decides *when* to query and fills parameters, but never writes raw queries.

## Project Structure

```
src/
  config/
    settings.py           ES client, env vars, CSV→list normalization
    constants.py          Model config, index names, SDK settings
  models.py               Pydantic data models with field validators
  runners/
    base.py               Base runner lifecycle (register, poll, execute, report)
    claude_runner.py       Claude Agent SDK integration
    manager.py            Fleet manager (runner process management)
  cli/main.py             CLI entry point

elastic/
  indices/                ES index mappings (5 indices)
  tools/                  ES|QL tool definitions (8 tools)
  agents/                 Agent Builder agent configs
  workflows/              Workflow definitions (4 YAML files)
  setup/                  Deployment scripts

data/                     Seed data for indices
dashboards/               Kibana dashboard definitions
tests/                    Test suite
docs/                     Documentation
```

## CLI Reference

```
codefleet start [--runners N] [--workdir PATH]
    Start the fleet manager with N runners

codefleet run-single --name NAME [--workdir PATH]
    Run a single runner (useful for debugging)

codefleet add-task --title T --description D [--priority N] [--labels L] [--file-scope F]
    Add a task to the backlog

codefleet list-tasks [--status STATUS] [--limit N]
    List tasks from the backlog

codefleet status
    Show fleet status (agents, tasks, conflicts)

codefleet setup
    Create Elasticsearch indices, tools, workflows, and agents

codefleet seed
    Populate indices with sample data

codefleet reset
    Reset all indices to clean state
```

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Language | Python 3.12+ |
| Package Manager | uv |
| AI Agent SDK | Claude Agent SDK (Sonnet 4.6) |
| State Store | Elasticsearch Serverless |
| Orchestration | Elastic Agent Builder + Workflows |
| Embeddings | `.multilingual-e5-small-elasticsearch` (built-in) |
| Data Models | Pydantic v2 |
| HTTP Client | httpx (async) |
| CLI | Click |
| Logging | structlog (JSON) |

## Agent Builder Features Used

| Feature | How We Use It |
|---------|---------------|
| **Custom Agents** | Fleet Commander with specialized fleet orchestration instructions |
| **ES\|QL Tools** | 7 parameterized tools for backlog search, agent status, conflict detection, semantic search, dependency analysis, task lookup, work review |
| **Elastic Workflows** | 4 workflows: task creation, auto-assignment, dependency unblocking, stale agent recovery |
| **Semantic Search** | `semantic_text` field with built-in `.multilingual-e5-small-elasticsearch` inference |
| **MCP Server** | Fleet Commander accessible from Claude Desktop |
| **Kibana Discover** | Real-time activity stream: every agent event indexed and explorable |

## License

[Apache 2.0](LICENSE)
