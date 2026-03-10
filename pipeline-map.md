# Pipeline Map: atlan-metabase

Generated from: `platform-packages/packages/atlan/metabase/templates/default.yaml`

## Architecture

**Two-workflow pattern** (Workflow 1: extraction + filter + process, Workflow 2: lineage transform)

Reason: The `extract` sub-DAG contains a `parse-queries` step that calls `templateRef: atlan-sql-parser / lineage`. This is an external SQL parser (Gudusoft-based, as evidenced by `metabase_lineage.py` referencing `gudusoft` objects and `gudusoft_data`). The `process-lineage` step that follows depends exclusively on the SQL parser's output (`parsed-queries` artifact). Neither `parse-queries` nor `process-lineage` can be implemented in the app natively. They must remain in Argo between two app workflows.

Workflow 1 covers: extract-base → filter → extract-individual → extract-queries → process (enrichment/query map building)
Argo covers: parse-queries (SQL parser) → process-lineage (lineage edge building from parser output)
Workflow 2 covers: transform → publish (reads all processed data including Argo lineage output)

---

## Execution Order

Top-level DAG (`main` template):

```
[skip]               prepare-workflow          → no dependencies
[extract-sub-dag]    extract                   → depends on: prepare-workflow  (inline sub-DAG; see extract sub-DAG below)
[skip]               publish                   → depends on: extract
```

`extract` sub-DAG (recurse into `extract` template):

```
[skip]                              fetch-credentials         → no dependencies
[skip]                              host-to-param             → depends on: fetch-credentials
[extract-base]                      extract                   → depends on: host-to-param
[filter]                            filter-data               → depends on: extract
[extract-individual]                extract-detailed          → depends on: filter-data
[extract-individual]                extract-queries           → depends on: filter-data
[process]                           process                   → depends on: extract-detailed, extract-queries
[argo — SQL parser]                 parse-queries             → depends on: process
[argo — depends on SQL parser output]  process-lineage        → depends on: parse-queries
```

---

## Stage Details

### [skip] prepare-workflow
Reason: `templateRef: atlan-workflow-helpers / prepare-workflow` — framework setup task, no implementation needed.

---

### [skip] fetch-credentials (inside extract sub-DAG)
Reason: `templateRef: rest-api / oauth2-client-credentials` — framework credential fetching via the platform service. No implementation needed; the app handles credentials via its own Temporal activity configuration.

---

### [skip] host-to-param (inside extract sub-DAG)
Reason: `templateRef: utils / artifact-to-key-param` — framework utility to promote an artifact key to a workflow parameter. No implementation needed.

---

### [extract-base] extract (inside extract sub-DAG)
Type: API curl — `api-request` template (inline), fan-out via `withItems`
Auth: Custom session-based — POST to `/api/session` with username/password, sets `X-Metabase-Session` header.

Endpoints fetched (4 items in parallel):
- GET `api/collection`   [output-key: `""`     ] → writes to `collections/`
- GET `api/dashboard`    [output-key: `""`     ] → writes to `dashboards/`
- GET `api/card`         [output-key: `""`     ] → writes to `questions/`
- GET `api/database`     [output-key: `"data"` ] → writes to `databases/`

Paginate: not set (raw-input-paginate default `0`). No list-by-ID; these are full-list fetches.
Writes: NDJSON result files per entity to S3 at `argo-artifacts/<connection-qn>/extract/<workflow>/`.
App layer: `app/extracts/collections.py`, `app/extracts/dashboards.py`, `app/extracts/questions.py`, `app/extracts/databases.py` → `fetch_<entity>_summaries()`

---

### [filter] filter-data (inside extract sub-DAG)
Type: Python module
Module: `marketplace_scripts.metabase.filter`
CLI args:
- `--data-prefix` — path to extracted data (default `/tmp/extracted-data`)
- `--include-collections` — JSON dict of collection IDs to include (default `{}`)
- `--exclude-collections` — JSON dict of collection IDs to exclude (default `{}`)
- `--output-prefix` — output path (default `/tmp/data`)

What it does:
1. Builds a `collections` map — applies include/exclude filter by collection ID.
2. Filters `dashboards` — keeps only those whose `collection_id` is in the accepted collections map.
3. Filters `questions` (cards) — same collection_id check.
4. Passes `databases` through unfiltered.
Outputs: `collections/collections.json`, `dashboards/dashboards.json`, `questions/questions.json`, `databases/databases.json` under the filtered prefix.

App layer: `app/extracts/filter.py` + `filter_data` Temporal activity

---

### [extract-individual] extract-detailed (inside extract sub-DAG)
Type: API curl — `api-request` template, fan-out via `withItems`, list-then-detail pattern
Reads: filtered IDs from filter step output (one item per line in the filtered `.json` file)
`raw-input-paginate: 1` → iterates through every item in the file; `raw-input-multiline: True`

Endpoints fetched (2 items, each ID-by-ID):
- GET `api/database/<ID>/metadata`  → writes to `database-metadata/`
- GET `api/dashboard/<ID>`          → writes to `dashboard-detailed/`

The execution script replaces `<ID>` with `raw_input[0]['id']` from each filtered record.
App layer:
- `app/extracts/databases.py` → `fetch_database_metadata(database_id)`
- `app/extracts/dashboards.py` → `fetch_dashboard_details(dashboard_id)`

---

### [extract-individual] extract-queries (inside extract sub-DAG)
Type: API curl — `api-request` template (single call, not withItems), list-then-detail pattern
Method: POST
Reads: filtered `questions/questions.json` — iterates one question at a time (`raw-input-paginate: 1`)
Endpoint: POST `api/dataset/native`

What it does per question:
- Sends the question's `dataset_query` as the POST body with `question_id` param.
- If `dataset_query` is absent → skips (sets `ignore = True`).
- On response: extracts the `query` field if present, attaches `question_id`; otherwise writes nothing.
- On API failure: uses `FailureHandler.NONE` (silently skips failures, does not retry).

Writes: `question-queries/` — one record per question that has a native SQL query.
App layer: `app/extracts/questions.py` → `fetch_question_queries(question_id, dataset_query)`

---

### [process] process (inside extract sub-DAG)
Type: Python module
Module: `marketplace_scripts.metabase.main`
CLI args:
- `--metabase-host` — Metabase host URL (from `host-to-param` output parameter)
- `--data-prefix` — filtered data prefix
- `--output-prefix` — processed output prefix

What it does (pure Python enrichment — no external SQL parser):
1. `generate_collections_map()` — reads filtered collections, annotates with `metabase_host`, `sourceURL`, builds lookup dict.
2. `generate_databases_map()` — reads detailed database metadata, annotates with `metabase_host`, `sourceURL`.
3. `generate_questions_query_map()` — reads `question-queries`, builds `{question_id: {query, params}}` dict.
4. `filter_assets(questions_query_map)` — reads detailed dashboards; enriches with collection info, question count, `cards_dashboard_map`; then enriches questions with query object (including `default_database_name`, `default_schema_name`, `engine`), collection info, and list of linked dashboards; writes `questions-dashboards` lineage records.

Outputs written to `processed/<workflow>/`:
- `collections/` — enriched collections
- `dashboards/` — enriched dashboards (with collection reference, question count, sourceURL)
- `questions/` — enriched questions (with query object, collection, dashboards list)
- `questions-dashboards/` — BIProcess lineage records (question → dashboards)

Note: This step prepares the `questions` data with the query object needed by the SQL parser (`parse-queries`). It is the boundary between app-implementable work and Argo-dependent work.

App layer: `app/activities/process.py` → `process_metabase_data(metabase_host, data_prefix, output_prefix)`

---

### [argo — SQL parser] parse-queries (inside extract sub-DAG)
Stays in Argo. Not implemented in the app.
TemplateRef: `atlan-sql-parser / lineage`
Parser: Gudusoft (confirmed by `MetabaseLineage` referencing `gudusoft_data`, `gudusoft_table`, and `obj['gudusoft']` fields from the parser output)

Args:
- `--include-file-pattern`: `**/questions.json`
- `--sql-json-key`: `query/query`
- `--catalog-json-key`: `query/default_database_name`
- `--schema-json-key`: `query/default_schema_name`
- `--vendor-json-key`: `query/engine`

Reads: processed data at `processed/<workflow>/` (which includes enriched `questions.json` with query objects).
Reason: external binary SQL parser (Gudusoft) — not natively supported in the app. Parses each question's SQL and emits `success.json` with `gudusoft` parse trees, `vendorName`, and `extra` (question metadata).
Output: `processed/<workflow>/parsed-queries/` — feeds `process-lineage`.

---

### [argo — depends on SQL parser output] process-lineage (inside extract sub-DAG)
Stays in Argo. Not implemented in the app.
Module: `marketplace_scripts.metabase.lineage`
CLI args:
- `--parsed-data-prefix` — path to `parsed-queries/` output from `parse-queries`
- `--cache-prefix` — connection cache path (used by `QueryEngine` for table/column resolution)
- `--output-prefix` — output path

Reason: depends exclusively on SQL parser output (`parse-queries` artifacts). Uses `MetabaseLineage` + `QueryEngine` to resolve Gudusoft parse trees against the connection cache and emit Atlas-ready lineage records.

What it does:
- Reads `parsed-queries/success.json` line by line.
- For each parsed query, calls `MetabaseLineage.get_entities()` which uses `QueryEngine` to look up tables and columns by dialect in the connection cache.
- Writes two output files:
  - `processes/` — table-level lineage: `{question_id, question_name, sql, table_entities}`
  - `column_processes/` — column-level lineage: `{question_id, question_name, sql, column_entities}`

Output: consumed by Workflow 2 (publish/transform) via `processed_data_path` at `processed/<workflow>/`.

---

### [skip] publish (top-level DAG)
Type: `templateRef: atlan-crawler / generic-publish` — handled by Argo/SDK crawler framework. No implementation needed for the publish/diff machinery itself, but the transformer configs define what Workflow 2 must produce.

Transformer config reads from S3 at `processed/<workflow>/` and runs Jinja2 templates for each entity type.

---

## Entity Types

From the `publish` task's `transformer-config`. These define what goes in `app/transformers/`:

| Input file pattern                  | Jinja2 template                                      | Transformer YAML (to create)                        | Source workflow     |
|-------------------------------------|------------------------------------------------------|-----------------------------------------------------|---------------------|
| `/tmp/inputs/collections.json`      | `atlan/metabase/transformers/collection.jinja2`      | `app/transformers/collection.yaml`                  | Workflow 1 (process step) |
| `/tmp/inputs/dashboards.json`       | `atlan/metabase/transformers/dashboard.jinja2`       | `app/transformers/dashboard.yaml`                   | Workflow 1 (process step) |
| `/tmp/inputs/questions.json`        | `atlan/metabase/transformers/question.jinja2`        | `app/transformers/question.yaml`                    | Workflow 1 (process step) |
| `/tmp/inputs/questions-dashboards.json` | `atlan/metabase/transformers/question_dashboard.jinja2` | `app/transformers/question_dashboard.yaml`     | Workflow 1 (process step) |
| `/tmp/inputs/processes.json`        | `atlan/metabase/transformers/process.jinja2`         | `app/transformers/process.yaml`                     | Argo (`process-lineage`) |
| `/tmp/inputs/column_processes.json` | `atlan/metabase/transformers/column_process.jinja2`  | `app/transformers/column_process.yaml`              | Argo (`process-lineage`) |

**Entity type notes:**

- **MetabaseCollection** (`collections`) — source: Workflow 1 `process` step. Represents a Metabase collection (folder). Annotated with `metabase_host`, `sourceURL`, `metabaseSlug`, `metabaseColor`, `metabaseNamespace`, `metabaseIsPersonalCollection`.

- **MetabaseDashboard** (`dashboards`) — source: Workflow 1 `process` step. Represents a Metabase dashboard. Contains collection reference, `metabaseQuestionCount`, `sourceURL`, timestamps, editor info.

- **MetabaseQuestion** (`questions`) — source: Workflow 1 `process` step. Represents a Metabase question/card. Contains query text, collection reference, linked dashboards list, `metabaseQueryType`, timestamps, creator info.

- **BIProcess** (`question_dashboards`) — source: Workflow 1 `process` step. Lineage edges from MetabaseQuestion → MetabaseDashboard(s). Written by `questions-dashboards` writer in `main.py`.

- **Process** (`processes`) — source: Argo `process-lineage` step. Table-level SQL lineage: external source tables → MetabaseQuestion. Requires Gudusoft parser output to resolve table qualified names.

- **ColumnProcess** (`column_processes`) — source: Argo `process-lineage` step. Column-level SQL lineage: source columns → MetabaseQuestion. Requires Gudusoft parser output to resolve column qualified names.

**Workflow 2 reads** all six entity files from `processed/<workflow>/` in S3 (both Workflow 1 output and Argo `process-lineage` output must be present before Workflow 2's transform step runs).

---

## Data Flow Summary

```
Metabase API
    │
    ▼
[Workflow 1 — App]
  extract-base: GET api/collection, api/dashboard, api/card, api/database
    │
    ▼
  filter: include/exclude by collection_id
    │
    ├──► extract-detailed: GET api/database/<ID>/metadata, api/dashboard/<ID>  (per-ID)
    └──► extract-queries: POST api/dataset/native  (per question, gets native SQL)
    │
    ▼
  process (main.py): enrich collections, databases, questions, build cards_dashboard_map
    │  Outputs: collections/, dashboards/, questions/, questions-dashboards/
    │
    ▼
[Argo — stays in Argo]
  parse-queries (atlan-sql-parser/Gudusoft): parse SQL in questions.json
    │
    ▼
  process-lineage (lineage.py): resolve tables/columns via QueryEngine + connection cache
    │  Outputs: processes/, column_processes/
    │
    ▼
[Workflow 2 / Argo publish — atlan-crawler]
  transform: Jinja2 templates → Atlas JSON entities
  publish: diff + upload to Atlas
```

---

## Notes

1. **SQL parser dependency chain**: The `parse-queries` step (`atlan-sql-parser/lineage` templateRef) is the hard boundary. It uses the Gudusoft parser internally (confirmed by `metabase_lineage.py` operating on `gudusoft_data` and `obj['gudusoft']` parse tree objects). The `process-lineage` step depends exclusively on its output and cannot run without it.

2. **`process` step is app-implementable**: Despite being labeled inside the "Extract" stage in the workflow, `marketplace_scripts.metabase.main` does purely Python enrichment — building lookup maps, annotating records with `metabase_host`/`sourceURL`, joining questions with dashboards. It has no external binary dependency and must run in the app as a Temporal activity.

3. **Connection cache**: `process-lineage` requires a `connection-cache` S3 artifact (mapped to `/tmp/cache`). This is an Atlan-managed cache mapping qualified names; it is not generated by this pipeline and is handled by Argo infrastructure.

4. **`raw-input-paginate: 1`** on `extract-detailed` and `extract-queries`: these steps iterate through every record in the input file and make one API call per record. The app implementation must replicate this fan-out behaviour.

5. **`output-key: "data"`** on the `api/database` extract: the API response wraps the database list in a `{"data": [...]}` envelope, and the `output-key` parameter unwraps it before writing. The app must perform the same unwrap.

6. **`extract-queries` failures are silently ignored** (`FailureHandler.NONE`): questions without a native SQL query are expected and should not fail the pipeline.
