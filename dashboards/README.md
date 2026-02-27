# CodeFleet Dashboards

Kibana dashboard definitions for monitoring the CodeFleet agent fleet.

## Fleet Overview Dashboard

The main dashboard provides 7 panels for real-time fleet monitoring:

| # | Panel | Type | Index | Description |
|---|-------|------|-------|-------------|
| 1 | Fleet Status | Metric | codefleet-agents | Agent count by status (idle/working/offline) |
| 2 | Task Pipeline | Metric | codefleet-tasks | Task count by status (pending/assigned/in_progress/completed/failed) |
| 3 | Agent Activity Timeline | Line chart | codefleet-activity | Events over time grouped by agent_id |
| 4 | Active Conflicts | Table | codefleet-conflicts | Unresolved conflicts with file paths and agents |
| 5 | Task Throughput | Bar chart | codefleet-tasks | Completed tasks per hour (by completed_at) |
| 6 | Cost Tracker | Metric | codefleet-agents | Total tokens used and total cost (USD) |
| 7 | Recent Activity | Table | codefleet-activity | Latest events with event type and agent |

## Setup

### Option A: Import NDJSON (Recommended)

1. Open Kibana and navigate to **Stack Management > Saved Objects**
2. Click **Import**
3. Select `dashboards/fleet_overview.ndjson`
4. Click **Import**
5. Navigate to **Dashboards** and open "CodeFleet - Fleet Overview"

> **Note**: You must create the index patterns first. The NDJSON provides
> scaffolding saved objects. For the best results, recreate the panels
> manually in Lens using the instructions below after importing.

### Option B: Create Manually in Kibana Lens (Best Results)

For polished demo-quality panels, create them directly in Kibana:

#### Prerequisites

1. Ensure all `codefleet-*` indices exist and contain seed data
2. Create data views (index patterns) in **Stack Management > Data Views**:
   - `codefleet-tasks` (time field: `created_at`)
   - `codefleet-agents` (time field: `last_heartbeat`)
   - `codefleet-activity` (time field: `timestamp`)
   - `codefleet-changes` (time field: `timestamp`)
   - `codefleet-conflicts` (time field: `detected_at`)

#### Panel 1: Fleet Status

1. Create new Lens visualization
2. Data view: `codefleet-agents`
3. Visualization type: **Metric**
4. Metric: Count of records
5. Break down by: `status` (Top 5 values)
6. Title: "Fleet Status"

#### Panel 2: Task Pipeline

1. Create new Lens visualization
2. Data view: `codefleet-tasks`
3. Visualization type: **Metric** (or **Mosaic/Treemap**)
4. Metric: Count of records
5. Break down by: `status` (Top 10 values)
6. Title: "Task Pipeline"

#### Panel 3: Agent Activity Timeline

1. Create new Lens visualization
2. Data view: `codefleet-activity`
3. Visualization type: **Line**
4. Y-axis: Count of records
5. X-axis: `timestamp` (auto date histogram)
6. Break down by: `agent_id` (Top 5 values)
7. Title: "Agent Activity Timeline"

#### Panel 4: Active Conflicts

1. Create new Lens visualization
2. Data view: `codefleet-conflicts`
3. Visualization type: **Table**
4. Columns: `conflict_id`, `file_paths`, `agent_ids`, `task_ids`, `status`, `detected_at`
5. Filter: `status: detected OR status: resolving`
6. Sort by: `detected_at` descending
7. Title: "Active Conflicts"

#### Panel 5: Task Throughput

1. Create new Lens visualization
2. Data view: `codefleet-tasks`
3. Visualization type: **Bar (vertical)**
4. Y-axis: Count of records
5. X-axis: `completed_at` (date histogram, 1 hour interval)
6. Filter: `status: completed`
7. Title: "Task Throughput"

#### Panel 6: Cost Tracker

1. Create new Lens visualization
2. Data view: `codefleet-agents`
3. Visualization type: **Metric**
4. Metrics:
   - Sum of `total_tokens_used` (label: "Total Tokens")
   - Sum of `total_cost_usd` (label: "Total Cost (USD)")
5. Title: "Cost Tracker"

#### Panel 7: Recent Activity

1. Create new Lens visualization
2. Data view: `codefleet-activity`
3. Visualization type: **Table**
4. Columns: `timestamp`, `agent_id`, `event_type`, `message`, `task_id`
5. Sort by: `timestamp` descending
6. Row limit: 50
7. Title: "Recent Activity"

#### Assembling the Dashboard

1. Go to **Dashboards > Create dashboard**
2. Title: "CodeFleet - Fleet Overview"
3. Add all 7 panels created above
4. Suggested layout (top to bottom, left to right):
   - Row 1: Fleet Status (1/3 width) | Task Pipeline (1/3) | Cost Tracker (1/3)
   - Row 2: Agent Activity Timeline (2/3 width) | Active Conflicts (1/3)
   - Row 3: Task Throughput (1/2 width) | Recent Activity (1/2)
5. Set time range to "Last 7 days" for seed data visibility
6. Save the dashboard

### Option C: API Script

For automated setup, use the Python script:

```bash
uv run python dashboards/create_dashboards.py
```

This requires `KIBANA_URL` and `KIBANA_API_KEY` in your `.env` file.

## Exporting Dashboards

After customizing in Kibana, export your dashboard:

1. Go to **Stack Management > Saved Objects**
2. Select the dashboard and all referenced objects
3. Click **Export** (NDJSON format)
4. Save as `dashboards/fleet_overview.ndjson`

## Troubleshooting

- **No data visible**: Ensure seed data has been loaded (`codefleet seed`) and the time range includes the seed data timestamps (Feb 24-25, 2026)
- **Missing index patterns**: Create data views in Stack Management first
- **Import errors**: The NDJSON references index pattern IDs that may differ in your deployment. Create panels manually if import fails.
