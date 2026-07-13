# Conformance status — open gaps for review

Snapshot for discussion. The **complete** suite (all series, `force-all`) runs on
push-to-`main`, the 6-hourly schedule, and `workflow_dispatch` — a plain PR only
runs the series touched by its diff. See the linked full-suite run in the PR that
introduces this doc.

## Blocking (BLOCK-tier)

**None.** The Conformance Gate is green on `main`.

- `B006` ×8 (contract ledger — inherited fields) — **fixed** (#187)
- `C003` (`.gitignore`) — **fixed** (#188)

## In-flight

- `T002` / `T003` (SDR integration test) — **PR #189** (adds `tests/sdr/`, `manifest_path`-driven).

## Open gaps (WARN-tier — non-blocking)

| Rule | Count | Where | Disposition |
|------|-------|-------|-------------|
| `E007` | 1 | `app/generated/_input.py` | **SDK codegen** — generated file, not app-fixable. Tracked in **BLDX-1542** (fix the contract-toolkit pkl template, or exclude `app/generated/` from the E/O series). |
| `O001` | 1 | `app/generated/_input.py` | Same as above (BLDX-1542). |
| `P028` | 17 | `asset_mapper.py`, `connector.py`, `extracts/process.py`, `lineage/ars_builder.py` | Hand-built `qualifiedName` f-strings. **13 are false positives** (storage-path prefixes, refs to existing assets, hash-suffixed lineage QNs) → justified suppressions. **4** (`asset_mapper.py` QN helpers) are an owner call: suppress-with-rationale vs pyatlan `.creator()` refactor. |
| `P012` | 13 | `app/contracts.py` | Path-typed `str` fields (`output_path` ×9, `processed_data_path` ×2, `qi_local_path`, `stage_dir`). Owner decision per group: suppress (SDK-normalised keys / local scratch) vs migrate to `FileReference` (a wire-contract change → pulls in the B006 ledger). |

## Deliberate suppressions (not defects — do not "fix")

| Rule | Count | Rationale |
|------|-------|-----------|
| `E020` | 7 | HTTP failures deliberately recorded to `residual/failures.jsonl` (best-effort per-item skip via `app/residuals.py`), not aborted. |
| `P001` | 1 | `credentials` field intentionally unbounded (B005-guarded; narrowing is a breaking contract change). |
| `P016` | 1 | Intentional single-card two-entrypoint app (BLDX-1342 pending). |
| `T013` | 1 | SDR suite must live under `tests/sdr/` (where `sdr-e2e` looks); the tier list omitting `sdr` is a known rule gap (BLDX-1542). |

## Discussion points

1. `E007`/`O001`: prefer the fleet-wide fix (exclude `app/generated/` from E/O) or the template fix? — **BLDX-1542**.
2. `P012` `output_path` family: adopt `FileReference` (type-safe, but a contract change) or suppress as SDK-normalised keys?
3. `P028` `asset_mapper.py` helpers: keep the single-source-of-truth string helpers (+suppress) or refactor to pyatlan factories?
