# Changelog

## v2.0.1 (June 22, 2026)

Full Changelog: https://github.com/atlanhq/atlan-metabase-app/compare/v2.0.0...v2.0.1

### Bug Fixes

- read QI output from _staging sibling prefix (#113) (by @vaibhavatlan in [f80f8b8](https://github.com/atlanhq/atlan-metabase-app/commit/f80f8b8))


## v2.0.0 (June 15, 2026)

Full Changelog: https://github.com/atlanhq/atlan-metabase-app/compare/v1.0.0...v2.0.0

### Features

- add e2e Docker pipeline using public metabase/metabase image (by @atlan-ci in [758416a](https://github.com/atlanhq/atlan-metabase-app/commit/758416a))
- replace shared LineageNode with custom extract_lineage @entrypoint (by @atlan-ci in [d6ad139](https://github.com/atlanhq/atlan-metabase-app/commit/d6ad139))
- ARS-based cross-connector lineage + unit-test coverage to 86% (by @atlan-ci in [8539886](https://github.com/atlanhq/atlan-metabase-app/commit/8539886))
- use latest canonical PKL patterns + workflowTypeOverride (by @atlan-ci in [ce6061c](https://github.com/atlanhq/atlan-metabase-app/commit/ce6061c))
- wire app to Global Marketplace + remove obsolete frontend (by @atlan-ci in [ef9e6bb](https://github.com/atlanhq/atlan-metabase-app/commit/ef9e6bb))
- migrate to toolkit 0.10.0 (atlan.yaml is now contract-generated) + remove SDR + add Trivy/Snyk (by @atlan-ci in [fc86a95](https://github.com/atlanhq/atlan-metabase-app/commit/fc86a95))
- scale to ~1000 Metabase assets + harden workflow + new tests (by @atlan-ci in [03b6cf4](https://github.com/atlanhq/atlan-metabase-app/commit/03b6cf4))
- apply the 3 field changes that missed the PR #23 merge (by @atlan-ci in [322a62a](https://github.com/atlanhq/atlan-metabase-app/commit/322a62a))
- migrate from QueryBasedTransformer to asset-mapper (by @atlan-ci in [2092d5d](https://github.com/atlanhq/atlan-metabase-app/commit/2092d5d))
- align structure with atlan-openapi-app (by @atlan-ci in [59ee959](https://github.com/atlanhq/atlan-metabase-app/commit/59ee959))
- add system-apps full-DAG e2e harness (by @atlan-ci in [7b93c15](https://github.com/atlanhq/atlan-metabase-app/commit/7b93c15))
- light shared Metabase seed for integration + e2e (by @atlan-ci in [1311a7c](https://github.com/atlanhq/atlan-metabase-app/commit/1311a7c))
- unblock lineage via MySQL source + drop postgres metadata DB (#49) (by @atlan-ci in [1b272c4](https://github.com/atlanhq/atlan-metabase-app/commit/1b272c4))
- wire AE credential body + agent-json refs (mysql-app parity) (#54) (by @atlan-ci in [27fd8b0](https://github.com/atlanhq/atlan-metabase-app/commit/27fd8b0))
- consume agent_json via CredentialRef.resolve — fixes _build_client error (#57) (by @atlan-ci in [b6324bd](https://github.com/atlanhq/atlan-metabase-app/commit/b6324bd))

### Bug Fixes

- regenerate PKL artifacts + fix pyright issues; pre-commit green (by @atlan-ci in [1d308ab](https://github.com/atlanhq/atlan-metabase-app/commit/1d308ab))
- support Metabase v0.49+ 'dashcards' field name in process_assets (by @atlan-ci in [1eea4db](https://github.com/atlanhq/atlan-metabase-app/commit/1eea4db))
- use PUT /api/dashboard/{id} with dashcards array (v0.49+ API) (by @atlan-ci in [ff11b56](https://github.com/atlanhq/atlan-metabase-app/commit/ff11b56))
- live-run e2e on local Metabase Docker surfaces 2 bugs (by @atlan-ci in [5800716](https://github.com/atlanhq/atlan-metabase-app/commit/5800716))
- lineage helper return types are dict[str, Any] (nested dicts) (by @atlan-ci in [38c4df0](https://github.com/atlanhq/atlan-metabase-app/commit/38c4df0))
- guard seed against Metabase virtual 'root' collection id (by @atlan-ci in [6b161db](https://github.com/atlanhq/atlan-metabase-app/commit/6b161db))
- seed root collections before nested + lighter QI-input assertion + port-aware mb_get (by @atlan-ci in [6302579](https://github.com/atlanhq/atlan-metabase-app/commit/6302579))
- drop dataset_query check — Metabase v0.61 /api/card summary omits it (by @atlan-ci in [3cedff3](https://github.com/atlanhq/atlan-metabase-app/commit/3cedff3))
- rename leftover workflow_transform_metadata_start scenario (by @atlan-ci in [218bd76](https://github.com/atlanhq/atlan-metabase-app/commit/218bd76))
- suppress auto-generated entrypoints block (align with hightouch) (by @atlan-ci in [ed41b52](https://github.com/atlanhq/atlan-metabase-app/commit/ed41b52))
- coerce stringified collection filters back to dict (by @atlan-ci in [3debad4](https://github.com/atlanhq/atlan-metabase-app/commit/3debad4))
- pin lineage-publish connection-cache flags to false (by @atlan-ci in [2f2dc3f](https://github.com/atlanhq/atlan-metabase-app/commit/2f2dc3f))
- emit BIProcess lineage with name and Atlas refs (by @atlan-ci in [a64b045](https://github.com/atlanhq/atlan-metabase-app/commit/a64b045))
- declare connection_qualified_name on ProcessInput (by @atlan-ci in [799784d](https://github.com/atlanhq/atlan-metabase-app/commit/799784d))
- move extract_lineage file I/O into a @task (by @atlan-ci in [41cbf83](https://github.com/atlanhq/atlan-metabase-app/commit/41cbf83))
- download QI prefix to local before reading (by @atlan-ci in [41501d9](https://github.com/atlanhq/atlan-metabase-app/commit/41501d9))
- per-query vendor routing + metabaseQueryType extraction (by @atlan-ci in [e673908](https://github.com/atlanhq/atlan-metabase-app/commit/e673908))
- drop MetabaseQuestion.metabase_dashboards (publish hazard) (by @atlan-ci in [5877ee2](https://github.com/atlanhq/atlan-metabase-app/commit/5877ee2))
- read current QI output shape (sql/gudusoft/extra) (by @atlan-ci in [54317a4](https://github.com/atlanhq/atlan-metabase-app/commit/54317a4))
- emit relationshipAttributes on Process/ColumnProcess (by @atlan-ci in [51c4d94](https://github.com/atlanhq/atlan-metabase-app/commit/51c4d94))
- resolve Gudusoft id-based column refs (ColumnProcess empty fix) (by @atlan-ci in [89cb732](https://github.com/atlanhq/atlan-metabase-app/commit/89cb732))
- emit ARS 2.0 records directly (replaces ARS 1.0 producer shape) (by @atlan-ci in [b556d2b](https://github.com/atlanhq/atlan-metabase-app/commit/b556d2b))
- match mysql-app's SDK env-var + execution-id-prefix pattern (by @atlan-ci in [0b44a66](https://github.com/atlanhq/atlan-metabase-app/commit/0b44a66))
- tighten ARS 2.0 contract after publish-app source audit (by @atlan-ci in [af494fd](https://github.com/atlanhq/atlan-metabase-app/commit/af494fd))
- pass entry_point — MetabaseApp is multi-entry-point (by @atlan-ci in [52fb1ea](https://github.com/atlanhq/atlan-metabase-app/commit/52fb1ea))
- use flat username/password keys in inline credentials (by @atlan-ci in [372c07d](https://github.com/atlanhq/atlan-metabase-app/commit/372c07d))
- write Process/ColumnProcess under resolvable/ subdir (by @atlan-ci in [39fac42](https://github.com/atlanhq/atlan-metabase-app/commit/39fac42))
- drop relationshipAttributes mirror + propagate dbvendor (by @atlan-ci in [6a0ce0b](https://github.com/atlanhq/atlan-metabase-app/commit/6a0ce0b))
- drop-on-miss for upstream refs (no Partial synthesis) (by @atlan-ci in [4e1a8c3](https://github.com/atlanhq/atlan-metabase-app/commit/4e1a8c3))
- add app.yaml at repo root so SDR action's configurator step works (#51) (by @atlan-ci in [973a40e](https://github.com/atlanhq/atlan-metabase-app/commit/973a40e))
- remove obsolete apk add git to resolve CVE-2026-6846 (binutils) (#50) (by @mothership-ai[bot] in [76814e5](https://github.com/atlanhq/atlan-metabase-app/commit/76814e5))
- re-land orphaned PR #55 + unique credential name per attempt + pytest timeout bump (#56) (by @atlan-ci in [60e1776](https://github.com/atlanhq/atlan-metabase-app/commit/60e1776))
- route extract-lineage to SDR queue via agent_json (mirror extract) (#58) (by @atlan-ci in [f8fc94e](https://github.com/atlanhq/atlan-metabase-app/commit/f8fc94e))
- re-apply PR #63 compose env-var change reverted by force-push (#64) (by @vaibhavatlan in [0373be9](https://github.com/atlanhq/atlan-metabase-app/commit/0373be9))
- add merge_group trigger to pre-commit workflow (#75) (by @cmgrote in [6700154](https://github.com/atlanhq/atlan-metabase-app/commit/6700154))


All notable changes to the Atlan Metabase App will be documented in this file.

This file is maintained automatically by the SDK's `release-version-bump`
workflow — entries are generated from conventional commit messages
(`feat:`, `fix:`, `chore:`, etc.) on each merge to `main`. Do not edit by
hand; the next release PR will overwrite manual edits.

## 1.0.0 (2026-06-03)

Initial GA release. v3 connector built on the asset-mapper pipeline:

- Extract: collections, dashboards, databases, questions (typed dataclass
  records → JSONL via `app/extracts/`).
- Transform: `app/asset_mapper.py` maps records to `pyatlan_v9` typed
  asset instances; lineage built by `app/lineage/ars_builder.py` from
  Query-Intelligence parsed-SQL output.
- Publish: standard `publish-app` activity carries the JSONL bundle to
  Atlas.
