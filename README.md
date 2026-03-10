# Atlan Metabase App

Metabase application built using the [Atlan Python Application SDK](https://github.com/atlanhq/application-sdk). It extracts metadata (collections, dashboards, and questions) from a Metabase instance and pushes it to the Atlan platform.

The app has two components:

- **FastAPI server** — exposes REST endpoints used by the setup UI and the SDK.
- **Temporal workflow** — runs metadata extraction, transforms results, and pushes to object store.

---

## Prerequisites

| Tool | Required for | Install |
|---|---|---|
| Python 3.11+ | Everything | [python.org](https://www.python.org/downloads/) |
| [uv](https://docs.astral.sh/uv/) | Everything | `curl -LsSf https://astral.sh/uv/install.sh \| sh` |
| [Temporal CLI](https://docs.temporal.io/cli) | Everything | `brew install temporal` (macOS) |
| [Dapr CLI](https://docs.dapr.io/getting-started/install-dapr-cli/) | Running workflows | `brew install dapr/tap/dapr-cli && dapr init` (macOS) |

> **Dapr is only needed to execute workflows.** For testing the UI, credentials, preflight checks, and metadata fetch — Temporal alone is enough.

---

## Local Development

### 1. Clone and enter the repo

```bash
git clone <repository-url>
cd atlan-metabase-app
```

### 2. Install dependencies

```bash
uv sync --all-extras --all-groups
```

### 3. Download Dapr components

```bash
uv run poe download-components
```

This fetches the required Dapr YAML files into `./components/`.

### 4. Set up environment variables

```bash
cp .env.example .env
```

The defaults in `.env.example` work for local development. Key variables:

| Variable | Default | Description |
|---|---|---|
| `ATLAN_APPLICATION_NAME` | `metabase` | App identifier |
| `LOG_LEVEL` | `DEBUG` | Log verbosity |
| `ENABLE_OTLP_LOGS` | `false` | Disable remote log shipping locally |
| `ATLAN_ENABLE_OBSERVABILITY_DAPR_SINK` | `false` | **Must be `false` when Dapr is not running** — if `true` without Dapr, every API request hangs for 60 s waiting on the Dapr health check |
| `ATLAN_CLEANUP_BASE_PATHS` | `/tmp/no-cleanup` | Set to a non-existent path to preserve output files in `local/tmp/` after the workflow completes |

### 5. Start dependencies

In a separate terminal, start Temporal:

```bash
uv run poe start-temporal
```

That's enough to run the UI, test connections, preflight checks, and metadata fetches.

If you also want to **execute workflows**, start Dapr alongside Temporal:

```bash
uv run poe start-deps
```

To stop everything:

```bash
uv run poe stop-deps
```

### 6. Run the app

```bash
uv run main.py
```

The app starts on **http://localhost:8000**.

Open **http://localhost:8000** in your browser to access the setup UI — a 3-step wizard to configure your Metabase connection and kick off metadata extraction.

---

## Setup UI Walkthrough

| Step | What to enter |
|---|---|
| **1 — Credential** | Metabase host (e.g. `https://metabase.example.com`), optional port, username and password |
| **2 — Connection** | A name for this connection (e.g. `my-metabase`) |
| **3 — Metadata** | Include/exclude collection filters (tree selector). Click **Check** to run preflight checks before starting the workflow. Click **Run** to trigger metadata extraction. |

---

## Useful Commands

```bash
# Install dependencies
uv sync --all-extras --all-groups

# Download Dapr components
uv run poe download-components

# Start Temporal only (enough for UI + auth + preflight + metadata)
uv run poe start-temporal

# Start Temporal + Dapr (needed to execute workflows)
uv run poe start-deps

# Stop all deps
uv run poe stop-deps

# Run the app
uv run main.py

# Run tests
uv run pytest tests/
```

---

## Docker

### Build

```bash
docker build --no-cache -t atlan-metabase-app:latest .
```

### Run (Temporal on host machine)

```bash
docker run -p 8000:8000 \
  --add-host=host.docker.internal:host-gateway \
  -e ATLAN_WORKFLOW_HOST=host.docker.internal \
  -e ATLAN_WORKFLOW_PORT=7233 \
  --user 1000:1000 \
  atlan-metabase-app:latest
```

### Run (Temporal on a remote host)

```bash
docker run -p 8000:8000 \
  -e ATLAN_WORKFLOW_HOST=<temporal-host> \
  -e ATLAN_WORKFLOW_PORT=<temporal-port> \
  --user 1000:1000 \
  atlan-metabase-app:latest
```

---

## Project Structure

```
atlan-metabase-app/
├── app/
│   ├── activities/
│   │   ├── metadata_extraction.py   # Temporal activities (extract, filter, process)
│   │   └── transform.py             # Temporal activities (YAML transform + upload)
│   ├── workflows/
│   │   ├── metadata_extraction.py   # Temporal workflow orchestration (Workflow 1)
│   │   └── transform.py             # Temporal workflow orchestration (Workflow 2)
│   ├── extracts/                    # Per-asset extraction logic
│   │   ├── collections.py
│   │   ├── dashboards.py
│   │   ├── databases.py
│   │   ├── filter.py
│   │   ├── process.py
│   │   └── questions.py
│   ├── transformers/                # YAML-driven raw → Atlan transformations
│   │   ├── collection.yaml
│   │   ├── column_process.yaml
│   │   ├── dashboard.yaml
│   │   ├── process.yaml
│   │   ├── question.yaml
│   │   └── question_dashboard.yaml
│   ├── templates/
│   │   ├── atlan-connectors-metabase.json  # Credential UI config
│   │   └── workflow.json                   # Workflow UI config
│   ├── client.py                    # Metabase HTTP client
│   ├── constants.py                 # URL builders
│   ├── handler.py                   # Auth, preflight, metadata fetch
│   ├── models.py                    # Pydantic models
│   └── utils.py                     # Shared helpers
├── frontend/
│   ├── static/
│   │   ├── script.js
│   │   └── styles.css
│   └── templates/
│       └── index.html
├── tests/
│   ├── unit/
│   └── e2e/
├── main.py
├── pyproject.toml
└── Dockerfile
```
