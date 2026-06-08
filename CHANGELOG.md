# Changelog

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
