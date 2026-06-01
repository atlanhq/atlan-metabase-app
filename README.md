# Metabase Connector

Crawls Collections, Dashboards, Questions, and Question↔Dashboard relationships from a Metabase instance and publishes them to Atlan. Native-SQL Metabase questions also produce ARS 2.0 cross-connector lineage edges to upstream tables/columns in the underlying database connector (Snowflake, Postgres, BigQuery, MySQL, …).

---

## What it does

1. **Extract** — fetches collections, dashboards, questions (cards), and databases from Metabase's REST API into per-entity JSONL files under `raw/`.
2. **Filter** — applies `include_collections` / `exclude_collections` to drop personal collections and anything outside the selection; cascades to dashboards and questions by collection id.
3. **Detail-fetch** — per-id calls for each filtered dashboard (ordered cards), database (schemas/tables), and question (native SQL string).
4. **Enrich** — joins the streams, stamps `metabaseQuery` / `metabaseSourceDatabaseName` / `metabaseSourceSchemaName` onto each question (the keys QueryIntelligence reads), and emits `BIProcess` records for pinned-card relationships.
5. **Transform** — runs each record through a typed pyatlan asset mapper and writes the final Atlas JSON to `transformed/<TYPENAME>/result-0.json`.
6. **Upload** — `self.upload(UploadInput(...))` ships the `transformed/` tree to object storage; the prefix is handed to the Automation Engine's publish node.
7. **Build cross-connector lineage** — `extract_lineage` (second entrypoint) reads the QueryIntelligence app's parsed-SQL NDJSON, builds ARS 2.0 `Process` + `ColumnProcess` records with PARTIAL_OBJECT / PARTIAL_FIELD references, and uploads the staged NDJSON for `lineage-publish` to resolve against the owning connector.

### Assets produced

| Asset | Atlan type | QN pattern | Count |
|---|---|---|---|
| Collection | `MetabaseCollection` | `{connection_qn}/collection/{id}` | 1 per non-filtered collection |
| Dashboard | `MetabaseDashboard` | `{connection_qn}/dashboard/{id}` | 1 per dashboard in selected collections |
| Question | `MetabaseQuestion` | `{connection_qn}/question/{id}` | 1 per card in selected collections |
| BI process | `BIProcess` | `{connection_qn}/bi-process/{question_id}/{dashboard_id}` | 1 per pinned card on a dashboard |
| Process / ColumnProcess (ARS 2.0) | `Process` / `ColumnProcess` | hash-based; PARTIAL_OBJECT / PARTIAL_FIELD refs to upstream | Built by the platform's QueryIntelligence node, finalized by `extract_lineage` |

---

## Input

Input is defined in [`contract/app.pkl`](contract/app.pkl) and code-generated into [`app/generated/_input.py`](app/generated/_input.py). To regenerate after editing the pkl:

```
pkl eval -m . --project-dir contract contract/app.pkl
```

### Credential channels

Credentials can arrive on three paths (resolved by [`build_credential_ref`](app/credentials.py)):

| Channel | Field on input | When it is set |
|---|---|---|
| Pkl `CredentialRef` | `metabase_credential` | v3 native marketplace path — the secret store key is resolved at task time via `self.context.resolve_credential_raw`. |
| Legacy GUID | `credential_guid` | Older platform builds — a `CredentialRef` is synthesised from the GUID. |
| Inline | `credentials` (`list[{key,value}]` or `dict`) | Local dev / direct API consumers — passed through as `inline_credentials` to every `@task`. |

Per-task inputs (`FetchInput`, `FilterInput`, `FetchDetailInput`, `ProcessInput`) carry `credential_ref` + `inline_credentials` so each Temporal activity rebuilds its own client.

### `extract_metadata` input fields ([`MetabaseInput`](app/contracts.py))

| Field | Type | Default | Description |
|---|---|---|---|
| `workflow_id` | `str` | `""` | Temporal workflow id. Threaded into output paths. |
| `metabase_credential` | `CredentialRef \| None` | `None` | Pkl-contract credential reference. |
| `credential_guid` | `str` | `""` | Legacy credential GUID. |
| `credentials` | `list[dict] \| dict` | `[]` | Inline credentials (local dev). |
| `extraction_method` | `str` | `"direct"` | Always `"direct"` for Metabase. |
| `agent_json` | `str` | `""` | Reserved for agent-based credential resolution. |
| `connection` | `ConnectionRef` | empty | The Atlan connection assets are written under. |
| `include_collections` | `dict[str, CollectionSelection]` | `{}` | Collection ids to include. Empty = include all non-personal. |
| `exclude_collections` | `dict[str, CollectionSelection]` | `{}` | Collection ids to skip. |
| `output_path` | `str` | `""` | Local working directory. Defaults to a temp dir. |
| `output_prefix` | `str` | `""` | Object-store prefix for the `transformed/` upload. |
| `processed_data_path` | `str` | `""` | Override read root for `transform_data` (debug-only). |
| `chunk_start` | `int` | `0` | Chunk index threaded into output filenames. |

### `extract_lineage` input fields ([`MetabaseLineageInput`](app/contracts.py))

| Field | Type | Default | Description |
|---|---|---|---|
| `workflow_id` | `str` | `""` | Temporal workflow id. |
| `connection` | `ConnectionRef` | empty | Same connection as `extract_metadata`. |
| `connection_qualified_name` | `str` | `""` | Threaded from `extract_metadata.outputs.connection_qualified_name`. |
| `view_lineage_input_prefix` | `str` | `""` | Object-store prefix where the QueryIntelligence node wrote parsed-SQL NDJSON. |
| `output_path` | `str` | `""` | Local working directory. |
| `output_prefix` | `str` | `""` | Object-store prefix for the `lineage-stage/` upload. |

---

## Output

### `extract_metadata` output fields ([`MetabaseOutput`](app/contracts.py))

| Field | Type | Description |
|---|---|---|
| `transformed_data_prefix` | `str` | Object-store prefix of the uploaded `transformed/` tree. Read by the publish node. |
| `connection_qualified_name` | `str` | Echoed from `input.connection`. Used to scope downstream state buckets. |
| `output_path` | `str` | Local working directory (so re-runs / debug tools can find intermediate files). |
| `view_lineage_output_prefix` | `str` | Prefix the QueryIntelligence node will write parsed-SQL output to. Threaded into `extract_lineage`. |
| `publish_state_prefix` | `str` | Blue-green publish state prefix derived under `persistent-artifacts/`. |
| `current_state_prefix` | `str` | Current-state cache prefix derived under `argo-artifacts/`. |
| `lineage_publish_state_prefix` | `str` | Lineage-scoped publish state prefix. |
| `lineage_current_state_prefix` | `str` | Lineage-scoped current-state cache prefix. |
| `lineage_stage_prefix` | `str` | Prefix `extract_lineage` will upload its staged NDJSON to. |
| `total_records` | `int` | Total transformed assets across the four typenames. |

### `extract_lineage` output fields ([`MetabaseLineageOutput`](app/contracts.py))

| Field | Type | Description |
|---|---|---|
| `lineage_stage_prefix` | `str` | Object-store prefix of the uploaded `lineage-stage/` tree. Read by the lineage-publish node. |
| `connection_qualified_name` | `str` | Echoed for state-bucket scoping. |
| `lineage_publish_state_prefix` | `str` | Lineage-scoped publish state prefix. |
| `lineage_current_state_prefix` | `str` | Lineage-scoped current-state cache prefix. |
| `process_count` | `int` | Number of `Process` records emitted. |
| `column_process_count` | `int` | Number of `ColumnProcess` records emitted. |

---

## Local development

### Prerequisites

- Python 3.11+, [`uv`](https://github.com/astral-sh/uv)
- [`pkl`](https://pkl-lang.org/) — only if you edit `contract/app.pkl` (`brew install pkl`)
- Docker — only for the live e2e harness in `tests/e2e/`

The SDK boots embedded Temporal in-process via `run_dev_combined`, so the Temporal CLI and a Dapr sidecar are **not** required for local dev.

### Setup

```bash
uv sync --all-extras --all-groups
```

### Run

```bash
# Boots the SDK's combined HTTP handler + worker on http://localhost:8000.
# In-process Temporal, in-process backends — no external services required.
uv run python -m app.run_dev
```

### Trigger a run

```bash
# Start an extract_metadata workflow
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

# Check result (use workflow_id from the response above)
curl http://localhost:8000/workflows/v1/result/<workflow_id>
```

No Metabase handy? The repo ships an end-to-end harness that spins up `metabase/metabase` in Docker, seeds it, and runs the connector against it — see [`tests/e2e/`](tests/e2e/README.md).

### Tests

```bash
uv run python -m pytest tests/unit -q
```

### Regenerate contract artifacts

```bash
pkl eval -m . --project-dir contract contract/app.pkl
```

Regenerates `atlan.yaml`, `app/generated/_input.py`, `app/generated/manifest.json`, and the workflow + credential configmap JSONs from `contract/app.pkl`. Commit the outputs.

### Container

```bash
docker build --no-cache -t atlan-metabase-app:latest .
```

In production the SDK runtime is invoked via `ATLAN_APP_MODULE=app.connector:MetabaseApp` (set in `atlan.yaml` → `deploy.env`); the image is built from `Dockerfile` by the shared build-and-publish workflow.
