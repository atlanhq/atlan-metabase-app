# atlan-metabase-app

A reference connector that crawls a Metabase instance and publishes its
Collections, Dashboards, Questions, and cross-connector lineage to Atlan.

This repo doubles as a **public sample app**: it is the smallest end-to-end
example of a v3 Atlan connector that is rich enough to show every moving
part — the SDK runtime, the Pkl contract toolkit, Temporal workflows, Dapr
sidecars, the 5-node DAG, and ARS 2.0 lineage. If you are about to build your
first Atlan app, read this file top to bottom once.

---

## What this app produces

| Asset | Source | Count per run |
|---|---|---|
| `MetabaseCollection` | Metabase collections (excluding personal) | 1 per non-filtered collection |
| `MetabaseDashboard` | Metabase dashboards | 1 per dashboard in selected collections |
| `MetabaseQuestion` | Metabase cards (a.k.a. "questions") | 1 per card |
| `BIProcess` | Question ↔ Dashboard pinned-card relationships | 1 per pinning |
| `Process` / `ColumnProcess` (ARS 2.0) | Native-SQL Metabase questions → upstream tables/columns in Snowflake/Postgres/BigQuery/MySQL/… | Built downstream by the platform's QueryIntelligence node, finalized by this app's `extract_lineage` entrypoint |

---

## The four moving parts

If you are new to Atlan apps, these are the four things you need to know
about before reading any code.

1. **[`application-sdk`](https://github.com/atlanhq/application-sdk)** — the
   Python runtime every v3 app sits on top of. It gives you `App`, the
   `@entrypoint` and `@task` decorators, a typed Temporal worker, an
   in-process HTTP handler (for the credential test, preflight checks, and
   the asset-tree picker the Atlan UI talks to), and the `self.upload(…)` /
   `self.download(…)` helpers backed by Dapr objectstore. You import from
   `application_sdk.app`, `application_sdk.contracts.*`, and
   `application_sdk.credentials.*` throughout this repo.

2. **The Pkl contract toolkit** ([`app-contract-toolkit`](https://github.com/atlanhq/application-sdk/tree/main/contract-toolkit)) —
   a [Pkl](https://pkl-lang.org/) library. You declare your app's identity,
   deployment shape, credential form, UI form, and DAG once in
   [`contract/app.pkl`](contract/app.pkl); the toolkit generates
   `atlan.yaml` (Global Marketplace registration), `app/generated/_input.py`
   (the typed Pydantic input model your entrypoint receives),
   `app/generated/manifest.json` (the Automation Engine DAG), and the
   credential/workflow configmap JSON the Atlan frontend reads to draw
   forms. Edit Pkl, regenerate; never hand-edit the outputs.

3. **Temporal** — the workflow engine the SDK targets. Each `@entrypoint`
   becomes a Temporal workflow; each `@task` becomes an activity with its
   own timeout / heartbeat / retry policy. Locally, the SDK boots an
   embedded Temporal dev server for you; in production, the platform's
   Temporal cluster runs the workflows.

4. **Dapr** — sidecar abstraction for secret store and object store.
   `secretstore: true` in `atlan.yaml` is what lets
   `self.context.resolve_credential_raw(credential_ref)` reach the platform
   vault from a `@task`. `objectstore: true` is what backs
   `self.upload(UploadInput(...))`. You will not write Dapr code in this
   repo; the SDK calls it for you.

The DAG, in pictures:

```
                  ┌────────────────────────────────────┐
trigger   ─────▶  │  extract  (extract_metadata @ep)   │ ─── transformed/ JSONL
{credentials,     │     extract → filter → detail →    │     (Collection, Dashboard,
 connection,      │     enrich → transform → upload    │      Question, BIProcess)
 collection                       │
 filters}                         ▼
                  ┌────────────────────────────────────┐
                  │  qi  (QueryIntelligence app)       │ ─── parsed-SQL NDJSON
                  │  reads attributes.metabaseQuery,   │
                  │  scoped by                         │
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

Two `@entrypoint`s on `MetabaseApp` (`app/connector.py`):

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

The marketplace treats this as **one connector card** even though there are
two entrypoints in code — only `extract_metadata` is invoked by the user;
`extract_lineage` is invoked downstream in the DAG by the lineage-publish
node. See [`contract/app.pkl`](contract/app.pkl) for the wiring.

---

## Prerequisites

- **Python 3.11+** and [**`uv`**](https://github.com/astral-sh/uv)
- **[`pkl`](https://pkl-lang.org/)** — only needed if you edit
  `contract/app.pkl` and regenerate artifacts (`brew install pkl`).
- **Docker** — only needed for the live e2e harness in `tests/e2e/`.

The SDK boots embedded Temporal and embedded Dapr for local dev, so you do
**not** need the Temporal CLI or `dapr` installed to run the app locally.

---

## Try it in 30 seconds

```bash
git clone https://github.com/atlanhq/atlan-metabase-app.git
cd atlan-metabase-app
uv sync --all-extras --all-groups   # one-time: install deps
uv run python -m app.run_dev        # boots the dev server on :8000
```

The first run takes ~30 s while the SDK fetches runtime binaries (cached
after that).

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

> **No Metabase handy?** The repo ships an end-to-end harness that spins up
> `metabase/metabase` in Docker, seeds it, and runs the connector against
> it — see [`tests/e2e/`](tests/e2e/README.md). Run it locally or trigger
> the `e2e-metabase` GitHub workflow.

---

## How a metadata run flows

`extract_metadata` is a thin orchestrator over a flat fan-out of `@task`s.
Each `@task` is a separate Temporal activity with its own retry policy.

1. **Resolve credentials.** `build_credential_ref(input)` collapses the
   three possible credential channels (Pkl `CredentialRef`, legacy GUID,
   inline list-of-pairs) into a `(credential_ref, inline_dict)` pair that
   is threaded into every task's input — see [`app/credentials.py`](app/credentials.py).
2. **Extract.** Four parallel `@task`s — `extract_collections`,
   `extract_dashboards`, `extract_questions`, `extract_databases` — fetch
   the raw summaries from Metabase's REST API and write JSONL to
   `raw/<entity>/result-0.json`.
3. **Filter.** `filter_data` applies the user's
   `include_collections`/`exclude_collections` to drop personal collections
   and anything outside the selection. Cascades to dashboards and questions
   by collection id.
4. **Detail-fetch.** `extract_individual_dashboards`,
   `extract_individual_databases`, and `fetch_question_queries_activity`
   fan out per-id calls to pull the rich per-entity data (ordered cards,
   schema metadata, native SQL string).
5. **Enrich.** `process_metabaseprocess` joins the four streams, stamps
   QueryIntelligence input keys (`metabaseQuery`,
   `metabaseSourceDatabaseName`, `metabaseSourceSchemaName`) onto each
   question, and emits the `BIProcess` records that link pinned cards to
   dashboards.
6. **Transform.** Four parallel `transform_data` calls (one per asset
   typename) run each record through the typed asset mappers in
   `app/asset_mapper.py` and write the final Atlas JSON to
   `transformed/<TYPENAME>/result-0.json`.
7. **Upload.** `self.upload(UploadInput(...))` ships the entire
   `transformed/` tree to object storage; the prefix is returned as
   `transformed_data_prefix` for the downstream publish node.

`extract_lineage` is much smaller — it reads the QI app's parsed-SQL
output, builds `Process` + `ColumnProcess` records with PARTIAL_OBJECT /
PARTIAL_FIELD references (the publish-app's ARS 2.0 resolver matches them
to whichever upstream connector owns the resolved table), and uploads the
staged NDJSON.

---

## The contract (`contract/app.pkl`)

Pkl is a configuration language with a strict type system. The contract
toolkit ships a base module (`@app-contract-toolkit/App.pkl`) that defines
the typed shape of an Atlan app; your `contract/app.pkl` `amends` that
module, fills in the slots, and regeneration produces:

| Output | What it is | Who reads it |
|---|---|---|
| `atlan.yaml` | Global Marketplace registration (name, icon, deploy, Dapr/KEDA, env) | Marketplace + Helm |
| `app/generated/_input.py` | Typed Pydantic input class your `@entrypoint` receives | The SDK runtime |
| `app/generated/manifest.json` | The 5-node DAG the Automation Engine dispatches | Automation Engine |
| `app/generated/metabase.json` | Workflow UI form (collection-tree picker, preflight checks) | Atlan frontend |
| `app/generated/atlan-connectors-metabase.json` | Credential UI form | Atlan frontend |

Edit Pkl, then regenerate:

```bash
pkl eval -m . --project-dir contract contract/app.pkl
```

The generated artifacts are checked into git so the Marketplace can read
them without running Pkl. Never hand-edit them.

---

## Useful commands

```bash
# Install dependencies (runtime + dev + test)
uv sync --all-extras --all-groups

# Run the dev server (embedded Temporal + handlers; no Dapr CLI needed)
uv run python -m app.run_dev

# Run unit tests (296 tests, ~86% coverage)
uv run pytest tests/unit/

# Run the e2e harness against metabase/metabase Docker
docker compose -f tests/e2e/compose.yaml up -d
uv run python tests/e2e/seed_metabase.py
uv run pytest tests/e2e/ -v -m e2e

# Regenerate PKL contract artifacts after editing contract/app.pkl
pkl eval -m . --project-dir contract contract/app.pkl

# Lint + type check
uv run pre-commit run --all-files
```

---

## Container

The container entry point is `python main.py`, which is a thin shim that
calls `app.run_dev.main`. In production deployments the SDK runtime is
invoked via `ATLAN_APP_MODULE=app.connector:MetabaseApp` (set in
`atlan.yaml` → `deploy.env`); the image is built from `deploy/Dockerfile`
by the shared build-and-publish workflow.

Build locally:

```bash
docker build --no-cache -t atlan-metabase-app:latest -f deploy/Dockerfile .
```

---

## Project structure

```
atlan-metabase-app/
├── atlan.yaml                     # Generated from contract/app.pkl by the
│                                  # Pkl toolkit (App.pkl@0.10.0).
│                                  # Source of truth for Global Marketplace.
├── deploy/
│   └── Dockerfile                 # Built + published by build-and-publish.yaml
├── contract/
│   ├── app.pkl                    # Pkl contract (DAG, deploy, credentials, UI)
│   ├── PklProject
│   └── PklProject.deps.json
├── app/
│   ├── connector.py               # MetabaseApp — two @entrypoint methods
│   ├── contracts.py               # Typed Pydantic Input/Output for entrypoints
│   │                              # + per-@task contracts
│   ├── credentials.py             # MetabaseCredential, parse_metabase_credentials,
│   │                              # build_credential_ref — credential model + routing
│   ├── paths.py                   # raw_file / processed_file / output-dir helpers
│   ├── client.py                  # Metabase REST client (session-token auth)
│   ├── handler.py                 # /workflows/v1/{auth,check,metadata} endpoints
│   ├── constants.py               # Metabase URL builders
│   ├── api_types.py               # Typed records bridging raw JSON → asset mappers
│   ├── asset_mapper.py            # Typed pyatlan asset mappers (Collection,
│   │                              # Dashboard, Question, BIProcess)
│   ├── utils.py                   # JSONL helpers + string normalization
│   ├── run_dev.py                 # Local-dev entry (run_dev_combined)
│   ├── extracts/                  # Per-asset extraction + filter + enrich
│   │   ├── collections.py
│   │   ├── dashboards.py
│   │   ├── databases.py
│   │   ├── filter.py
│   │   ├── process.py             # Enrichment — stamps QI input keys
│   │   └── questions.py
│   ├── lineage/                   # ARS 2.0 cross-connector lineage builder
│   │   ├── ars_builder.py         # PARTIAL_OBJECT / PARTIAL_FIELD record builders
│   │   └── qi_reader.py           # Reads QueryIntelligence parsed-SQL NDJSON
│   └── generated/                 # Pkl-generated (do not hand-edit)
├── tests/
│   ├── unit/                      # 296 tests, ~86% coverage
│   └── e2e/                       # Live e2e against metabase/metabase Docker
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

Questions or stuck building your own app? Ping `#bu-apps` in Slack or open
an issue.
