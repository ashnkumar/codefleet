# CodeFleet — Product Requirements Document

## Vision

CodeFleet is a fleet management system for AI coding agents. You describe what you want to a PM-like "Fleet Commander" agent in Elastic Agent Builder. It breaks your request into tasks with dependencies, creates them in Elasticsearch, and a local daemon (Fleet Manager) automatically spins up Claude Code agents to execute the work in parallel — dynamically scaling, resolving dependencies, preserving context, and reporting everything back to ES for real-time monitoring.

**You are the PM. Fleet Commander is your AI PM assistant. The Fleet Manager is your engineering manager. The runners are your engineers.**

## The Problem

AI coding agents are powerful individually. But coordinating multiple agents on the same codebase is completely manual:

- **No task queue**: You manually tell each agent what to do in a terminal
- **No decomposition**: You break down work yourself and assign it
- **No conflict detection**: Two agents can edit the same file and create merge conflicts
- **No monitoring**: You don't know which agent is doing what, or when they finish
- **No auto-assignment**: When an agent finishes, it sits idle until you give it more work
- **No dependency awareness**: You manually track what's blocked by what
- **No persistence**: If you close your terminal, everything is lost
- **No analytics**: You can't see cost, duration, or throughput metrics

**This is exactly what happened building CodeFleet itself.** We used 3 parallel Claude Code agents in worktrees to build this project. The human (AK) acted as the message bus — relaying completion signals, merging branches, copying .env files, assigning next tasks. CodeFleet automates everything AK did manually.

## Architecture — Three Roles

```
┌─────────────────────────────────────────────────────────────┐
│  FLEET COMMANDER (Elastic Agent Builder)  =  The PM         │
│  Lives in Elastic Cloud. Has AI reasoning via ES|QL tools.  │
│                                                             │
│  - You chat with it to define what you want (PRD-style)     │
│  - It breaks requests into tasks with dependencies          │
│  - It creates tasks in ES (via workflow tools)              │
│  - It answers questions about backlog, progress, costs      │
│  - It finds related tasks via semantic search (embeddings)  │
│  - It does NOT execute code or manage runners               │
└─────────────────────────┬───────────────────────────────────┘
                          │
                  ┌───────▼───────┐
                  │ ELASTICSEARCH │ ← The shared brain (all state lives here)
                  │               │    Tasks, agents, activity, file changes,
                  │               │    conflicts — indexed, searchable, real-time
                  └───────┬───────┘
                          │
┌─────────────────────────▼───────────────────────────────────┐
│  FLEET MANAGER (local Python daemon)  =  The EM             │
│  Lives on your machine. Deterministic scheduling code.      │
│                                                             │
│  - Polls ES every 5 seconds for state changes               │
│  - Sees pending unblocked tasks → spins up runners          │
│  - Assigns tasks to runners (smart routing by context)      │
│  - When task completes → unblocks dependents                │
│  - Dynamically scales: 0 tasks = 0 runners, 10 tasks = 5   │
│  - Preserves context: sequential tasks → same runner        │
│  - You don't interact with this. It just runs.              │
└─────────────────────────┬───────────────────────────────────┘
                          │
           ┌──────────────┼──────────────┐
           │              │              │
      ┌────▼────┐   ┌────▼────┐   ┌────▼────┐
      │Runner 1 │   │Runner 2 │   │Runner 3 │   ... dynamically scaled
      │(Claude) │   │(Claude) │   │(Claude) │
      │ Opus    │   │ Opus    │   │ Opus    │
      └────┬────┘   └────┬────┘   └────┬────┘
           │              │              │
           └──────────────┼──────────────┘
                          │
                  YOUR CODEBASE (localhost, hot reload)
```

## How It Works — Full Flow

### Setup (one-time, after cloning)
```bash
git clone https://github.com/you/codefleet.git
cd codefleet
cp .env.example .env  # fill in ES + Anthropic credentials
uv sync
codefleet setup       # creates indices, registers tools + agents in Agent Builder
```

### Start the Fleet
```bash
codefleet start --workdir /path/to/your-webapp --max-runners 5
```
Starts the Fleet Manager daemon. It begins with 0 runners and polls ES for work.

### Chat with Fleet Commander (Kibana Agent Builder UI)
> **You:** "I want to add dark mode, a dashboard showing completed tasks per day, and a settings page."

Fleet Commander breaks this down:
```
T1: "Add CSS custom properties for dark/light themes"     [no deps, P4]
T2: "Build theme toggle component"                        [depends: T1, P3]
T3: "Create /api/stats endpoint for tasks per day"        [no deps, P4]
T4: "Build dashboard chart component"                     [depends: T3, P3]
T5: "Create settings page layout and routing"             [no deps, P4]
T6: "Wire theme persistence into settings"                [depends: T2+T5, P2]
```
Creates all 6 tasks in ES.

### Automatic Execution
1. Fleet Manager polls ES, finds 3 unblocked tasks (T1, T3, T5)
2. Spins up 3 runners, each in its own git worktree
3. Assigns T1 → runner-1, T3 → runner-2, T5 → runner-3
4. All 3 work in parallel. Web app hot-reloads as changes land.
5. runner-1 finishes T1 → Fleet Manager unblocks T2 → assigns T2 to runner-1 (keeps context)
6. runner-3 finishes T5 → T6 still blocked (needs T2) → runner-3 shuts down
7. runner-1 finishes T2 → T6 unblocked → spins up runner for T6
8. All tasks complete. Fleet idles. Web app has dark mode + dashboard + settings.

### You Can Add More Work Anytime
Go back to Fleet Commander: "Also add user avatars and email notifications." New tasks appear in ES, Fleet Manager picks them up automatically.

## The Elastic Stack — Why It's Essential (Not Just a Database)

| What ES Provides | What You'd Build Without It |
|---|---|
| **Agent Builder chat UI** | Custom chat frontend + tool-calling framework |
| **ES|QL tools** (7 expert-defined queries) | Build parameter routing + query engine |
| **Semantic search** (auto-embeddings via EIS) | pgvector + embedding pipeline + model hosting |
| **Kibana Discover** (real-time event viewer) | Build log viewer frontend |
| **Kibana Dashboards** (cost, throughput, health) | Build React dashboard |
| **Workflows** (scheduled automation) | Build job scheduler |
| **MCP server** (IDE integration) | Build MCP server from scratch |
| **Conflict detection** (ES aggregation queries) | Complex SQL + scheduler |

**ES is the orchestration platform, not just storage.** The Fleet Commander's intelligence comes from ES|QL tools. The real-time visibility comes from Kibana. The semantic grouping comes from built-in inference endpoints. With Postgres you'd have a table and build everything else from scratch.

## Key Features

### Implemented
- **Task Queue**: Tasks in ES with priority, dependencies, file scope, status, semantic embeddings
- **Fleet Commander**: Agent Builder agent with 7 ES|QL tools (backlog search, agent status, conflict detection, semantic similarity, dependency analysis, task assignment, work review)
- **Task Planner**: Secondary agent for dependency analysis (A2A communication)
- **Claude Runners**: Agent SDK integration, polling, execution, activity reporting
- **Fleet Manager**: Multi-runner management, health monitoring
- **Semantic Search**: Auto-generated embeddings via Elastic Inference Service, MATCH queries
- **MCP Exposure**: Fleet Commander accessible from Claude Desktop
- **CLI**: Full task management (add, list, assign, status)
- **Activity Logging**: Every agent event indexed and searchable in ES

### In Progress (Phase 2)
- **Fleet Commander writes** (create tasks, assign tasks via workflow tools)
- **Smart Fleet Manager**: Dynamic runner scaling, dependency-aware scheduling
- **Dependency unblocking**: Automatic cascade when tasks complete
- **Context continuity**: Session resume for sequential tasks on same runner

### Future
- Web UI with per-agent activity panes
- GitHub/Linear integration for importing backlogs
- Git automation (worktree management, branch creation, PR creation)
- Cost budgeting per task/project
- Multi-model routing (Opus for complex, Sonnet for simple)

## Hackathon Context

- **Hackathon**: Elastic Agent Builder Hackathon (Jan 22 - Feb 27, 2026)
- **Prizes**: $10K / $5K / $3K + 4x $500 "Wow Factor"
- **Judging**: Technical Execution (30%), Impact & Wow Factor (30%), Demo (30%), Social (10%)
- **Requirements**: Elastic Agent Builder, open source repo, ~3 min video, ~400 word description
- **Full details**: See `docs/HACKATHON.md`

## Portfolio Context

- **Anthropic Labs Research PM** ($385K-$460K) — demonstrates 0→1 product thinking, deep Agent SDK expertise, developer tooling intuition
- **Staff/Principal AI Engineer roles** — demonstrates multi-agent orchestration, production patterns
- **Key narrative**: "I built CodeFleet using the exact manual process it automates. 3 parallel agents, worktrees, manual coordination — then I built the system that replaces all of it."

## Build Statistics

- **~9,500 lines of code** written by 3 parallel Claude Code agents
- **84 tests passing** (unit + integration + e2e)
- **Wall clock: ~1.5 hours** from spec to working system (parallel agents)
- **0 merge conflicts** (directory-level ownership)
- **Real end-to-end execution proven**: Task created → Fleet Commander found it → runner executed → code written → task completed in ES
