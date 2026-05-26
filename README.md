# atlan-metabase-app

Metabase connector built on the [Atlan Application SDK](https://github.com/atlanhq/application-sdk). Crawls Collections / Dashboards / Questions / Question-on-Dashboard lineage from a Metabase instance and publishes them to Atlan, plus emits ARS 2.0 cross-connector lineage edges from each native-SQL Metabase question to the upstream tables / columns in whichever connector owns them (Postgres, Snowflake, BigQuery, MySQL, …).

The app exposes:
- **HTTP handler** — `/workflows/v1/auth`, `/workflows/v1/check`, `/workflows/v1/metadata`, `/workflows/v1/start`, `/workflows/v1/result/<id>`. Used by the Atlan UI for credential test, 4 named preflight checks, the collection-tree picker, and to start/poll workflows.
- **Temporal workflows** — two `@entrypoint` methods (`extract_metadata`, `extract_lineage`) registered as separate workflow types; dispatched by Atlan's Automation Engine as a 5-node DAG.

---

## Try it in 30 seconds

You need **Python 3.11+** and [**`uv`**](https://github.com/astral-sh/uv). The SDK handles everything else for you (no Temporal CLI, no Dapr, no Redis required).

```bash
git clone https://github.com/atlanhq/atlan-metabase-app.git
cd atlan-metabase-app
uv sync --all-extras --all-groups   # one-time: install deps
uv run python -m app.run_dev        # boots the dev server on :8000
```

The first run takes ~30 s while the SDK fetches runtime binaries (cached after that).

In a second terminal, point the app at a Metabase instance:

```bash
curl -X POST http://localhost:8000/workflows/v1/start \
  -H "Content-Type: application/json" \
  -d '{
    "workflow_type": "metabase:extract-metadata",
    "credentials": [
      {"key": "host",     "value": "https://your-metabase.example.com"},
      {"key": "port",     "value": "443"},
      {"key": "username", "value": "service-account@example.com"},
      {"key": "password", "value": "<your-password>"}
    ],
    "connection": {
      "attributes": {"name": "metabase-local", "qualified_name": "default/metabase/local"}
    },
    "include_collections": {},
    "exclude_collections": {}
  }'
# → {"success": true, "data": {"workflow_id": "metabase-...", ...}}

curl http://localhost:8000/workflows/v1/result/<workflow_id>
# → {"data": {"status": "completed",
#             "result": {"total_records": 80,
#                        "transformed_data_prefix": "...",
#                        ...}}}
```

Hit `Ctrl-C` in the first terminal to stop the dev server.

> **No Metabase handy?** The repo ships an end-to-end harness that spins up `metabase/metabase` Docker, seeds it, and runs the connector against it — see [`tests/e2e/`](tests/e2e/README.md). Run it locally or trigger the `e2e-metabase` GitHub workflow.

---

## How the app fits together

```
                  ┌────────────────────────────────────┐
trigger   ─────▶  │  extract  (extract_metadata @ep)   │ ─── transformed/ JSONL
{credentials,     │     extract → filter → detail →    │     (Collection, Dashboard,
 connection,      │     enrich → transform → upload    │      Question, BIProcess)
 collection                       │
 filters}                         ▼
                  ┌────────────────────────────────────┐
                  │  qi  (QueryIntelligence app)       │ ─── parsed-SQL NDJSON
                  │  reads attributes.metabaseQuery,   │     ({QUERY_ID, SQL,
                  │  scoped by                         │      PARSED_DATA, ...})
                  │  metabaseSourceDatabaseName +      │
                  │  metabaseSourceSchemaName          │
                  └─────────────────┬──────────────────┘
                                    │
                  ┌────────────────────────────────────┐
                  │  publish  (atlan-publish-app)      │ ─── Atlan assets
                  └─────────────────┬──────────────────┘
                                    │
                  ┌────────────────────────────────────┐
                  │  extract-lineage  (extract_lineage @ep)
                  │  reads QI output → emits ARS 2.0   │
                  │  Process + ColumnProcess records   │ ─── lineage-stage/ JSONL
                  │  with PARTIAL_OBJECT / PARTIAL_FIELD│    (cross-connector refs)
                  └─────────────────┬──────────────────┘
                                    │
                  ┌────────────────────────────────────┐
                  │  lineage-publish (atlan-publish-app │
                  │  ARS 2.0 resolver mode)            │ ─── Atlan lineage edges
                  └────────────────────────────────────┘
```

Two `@entrypoint`s on `MetabaseApp`:

```python
class MetabaseApp(App):
    name = "metabase"

    @entrypoint
    async def extract_metadata(self, input: MetabaseInput) -> MetabaseOutput:
        # extract → filter → detail-fetch → enrich → transform → upload
        ...

    @entrypoint
    async def extract_lineage(
        self, input: MetabaseLineageInput
    ) -> MetabaseLineageOutput:
        # read QI parsed-SQL → build ARS 2.0 Process + ColumnProcess → upload
        ...
```

Detailed walkthrough: [`docs/flow.md`](docs/flow.md) (todo) — for now, see the comments at the top of `app/connector.py` and `contract/app.pkl`.

---

## Useful commands

```bash
# Install dependencies
uv sync --all-extras --all-groups

# Run the dev server (in-process Temporal + handlers; no Dapr / Temporal CLI needed)
uv run python -m app.run_dev

# Run unit tests (304 tests, 86% coverage)
uv run pytest tests/unit/

# Run the e2e harness against metabase/metabase Docker (locally; see tests/e2e/README.md)
docker compose -f tests/e2e/compose.yaml up -d   # spin up Metabase + postgres
uv run python tests/e2e/seed_metabase.py         # seed admin + collections + questions + dashboards
uv run pytest tests/e2e/ -v -m e2e               # run e2e tests

# Regenerate PKL contract artifacts after editing contract/app.pkl
pkl eval -m . --project-dir contract contract/app.pkl

# Run all pre-commit hooks (ruff, ruff-format, pyright, isort)
uv run pre-commit run --all-files
```

---

## Container

The container entry point is `python main.py`, which is a thin shim that calls `app.run_dev.main`. In production deployments the SDK runtime is invoked via `ATLAN_APP_MODULE=app.connector:MetabaseApp` (set in `atlan.yaml` → `deploy.env`); the image is built from `deploy/Dockerfile` by the shared build-and-publish workflow.

Build locally:

```bash
docker build --no-cache -t atlan-metabase-app:latest -f deploy/Dockerfile .
```

---

## Project Structure

```
atlan-metabase-app/
├── atlan.yaml                     # AUTO-GENERATED from contract/app.pkl by the
│                                  # PKL toolkit (App.pkl@0.10.0).
│                                  # Source of truth for Global Marketplace.
├── deploy/
│   └── Dockerfile                 # Built + published by build-and-publish.yaml
├── contract/
│   ├── app.pkl                    # PKL contract (declares the 5-node DAG, deploy
│   │                              # config, credential schema, UI form)
│   ├── PklProject
│   └── PklProject.deps.json
├── app/
│   ├── connector.py               # MetabaseApp — two @entrypoint methods
│   ├── contracts.py               # Typed Pydantic Input/Output for both
│   │                              # entrypoints + per-@task contracts
│   ├── client.py                  # Metabase REST client (session-token auth)
│   ├── handler.py                 # /workflows/v1/{auth,check,metadata}
│   │                              # (4 named preflight checks; personal-collection
│   │                              # filter in fetch_metadata)
│   ├── constants.py               # Metabase URL builders
│   ├── models.py                  # Shared Pydantic models
│   ├── utils.py                   # JSONL helpers
│   ├── run_dev.py                 # Local-dev entry (run_dev_combined)
│   ├── extracts/                  # Per-asset extraction + filter + enrich
│   │   ├── collections.py
│   │   ├── dashboards.py
│   │   ├── databases.py
│   │   ├── filter.py
│   │   ├── process.py             # Enrichment — stamps QI input keys
│   │   │                          # (metabaseQuery, metabaseSourceDatabaseName, …)
│   │   └── questions.py
│   ├── transformers/              # YAML-driven Atlas-JSON transforms
│   │                              # (Collection / Dashboard / Question / BIProcess)
│   ├── lineage/                   # ARS 2.0 cross-connector lineage builder
│   │   ├── ars_builder.py         # PARTIAL_OBJECT / PARTIAL_FIELD record builders
│   │   └── qi_reader.py           # Reads QueryIntelligence parsed-SQL NDJSON
│   └── generated/                 # PKL-generated — manifest.json, _input.py,
│                                  # configmap JSONs (do not hand-edit)
├── tests/
│   ├── unit/                      # 304 tests, 86% coverage
│   ├── integration/
│   └── e2e/                       # Live e2e against metabase/metabase Docker (~1000 assets)
├── main.py                        # Container entry shim → app.run_dev.main
└── pyproject.toml
```

---

## Contributing

- Run `uv run pre-commit run --all-files` before pushing.
- Unit tests must keep coverage at ≥ 85 % (`fail_under = 85` in `pyproject.toml`).
- After editing `contract/app.pkl`, regenerate artifacts with
  `pkl eval -m . --project-dir contract contract/app.pkl` and commit them.
- E2E gate runs against `metabase/metabase:v0.61.2.3` (pinned in
  `.github/workflows/e2e-metabase.yaml`); bump policy documented in
  `tests/e2e/README.md`.
