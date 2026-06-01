# E2E pipeline (`metabase/metabase` Docker)

End-to-end tests that run the connector against a real Metabase server
spun up as a GitHub Actions service container.

## What it validates

- The 4 named preflight checks (`collectionCountCheck`,
  `dashboardCountCheck`, `questionCountCheck`,
  `nativeQueryPermissionCheck`) all pass against seeded data.
- `MetabaseHandler.fetch_metadata()` filters personal collections out
  of the apitree response.
- All extract `@task` methods return the seeded collections, dashboards,
  questions, and databases.
- `filter_data` correctly drops cards from excluded collections.
- `parse_lineage` (sqlglot) emits `Process` records whose
  `input_table_qualified_names` reference the source tables joined in
  each question's native SQL, and `ColumnProcess` records for the
  columns explicitly selected.
- BIProcess records (question → dashboard) are present after the
  enrichment step.

## How it runs

The `e2e` job in `.github/workflows/tests.yaml` is triggered by the `e2e`
PR label or `workflow_dispatch`. It:

1. Brings up three service containers — `postgres` (Metabase's metadata
   DB), `metabase` (the BI tool we crawl), and `mb-source` (a postgres
   source registered as a Metabase database for lineage testing).
2. Seeds `mb-source` with `fixtures/seed_source.sql`.
3. Runs `seed_metabase.py` to: poll for Metabase health, create the
   admin user (or log in if already set up), register `mb-source`,
   trigger a metadata sync, and apply the declarative seed spec.
4. Runs `pytest tests/e2e/ -m e2e`.
5. Uploads the JUnit report + any captured artifacts on completion.

## Image pinning + bump policy

The pin lives in two places — keep them in sync:
- `.github/workflows/tests.yaml` → `services.metabase.image:` (in the `e2e` job)
- `tests/e2e/conftest.py` (referenced in comments)

**To bump the Metabase image**:
1. Open a PR with the new tag (e.g. `v0.61.3.0`) in both files above.
2. Apply the `e2e` label to trigger the workflow.
3. Promote the pin only when the workflow goes green; if API contracts
   change in the new Metabase version, fix the seed script and tests
   first.
4. Pin **exactly** — do not use `latest` or floating tags.

Current pin: `metabase/metabase:v0.61.2.3` (2026-05-21).

## Local dev

The full e2e flow runs in CI. For local iteration:

```bash
# Bring up the stack (substitute your own credentials)
docker run -d --name e2e-mb-pg -e POSTGRES_USER=metabase \
  -e POSTGRES_PASSWORD=metabase -e POSTGRES_DB=metabase_app_db \
  -p 5432:5432 postgres:15

docker run -d --name e2e-mb \
  -e MB_DB_TYPE=postgres -e MB_DB_HOST=host.docker.internal \
  -e MB_DB_PORT=5432 -e MB_DB_DBNAME=metabase_app_db \
  -e MB_DB_USER=metabase -e MB_DB_PASS=metabase \
  -p 3000:3000 metabase/metabase:v0.61.2.3

docker run -d --name e2e-mb-source \
  -e POSTGRES_USER=source -e POSTGRES_PASSWORD=source \
  -e POSTGRES_DB=testdata -p 5433:5432 postgres:15

# Seed
PGPASSWORD=source psql -h localhost -p 5433 -U source -d testdata \
  -f tests/e2e/fixtures/seed_source.sql

MB_URL=http://localhost:3000 \
MB_ADMIN_EMAIL=e2e@atlan.com \
MB_ADMIN_PASSWORD='AtlanMetabaseE2E!1' \
MB_SOURCE_HOST=host.docker.internal MB_SOURCE_PORT=5433 \
MB_SOURCE_USER=source MB_SOURCE_PASSWORD=source MB_SOURCE_DB=testdata \
uv run python tests/e2e/seed_metabase.py

# Run
E2E_METABASE_HOST=http://localhost:3000 \
E2E_METABASE_USERNAME=e2e@atlan.com \
E2E_METABASE_PASSWORD='AtlanMetabaseE2E!1' \
uv run pytest tests/e2e/ -v -m e2e
```

Note: `MB_SOURCE_HOST` differs between CI (`mb-source` — the service
container hostname) and local (`host.docker.internal` — the host's
loopback alias from inside the Metabase container).

## Layout

```
tests/e2e/
├── README.md                          ← you are here
├── __init__.py
├── conftest.py                        ← pytest fixtures (metabase URL, session, spec)
├── seed_metabase.py                   ← idempotent seed script (run in CI before pytest)
├── fixtures/
│   ├── seed_source.sql                ← postgres source DDL + sample rows
│   └── seed_metabase_spec.yaml        ← declarative Metabase state (canonical)
├── test_e2e_preflight.py              ← 4 named preflight checks
├── test_e2e_personal_collections.py   ← personal collections filtered
├── test_e2e_extraction.py             ← every extract @task returns seeded data
└── test_e2e_lineage.py                ← sqlglot Process + ColumnProcess
```
