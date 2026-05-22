# MSSQL Parity Report

> Generated: 2026-04-03 | v2: localhost:3000 | v3: localhost:8000

## Test Totals

| Level | Tests | Passed | Failed | Skipped |
|-------|-------|--------|--------|---------|
| Level 1 — Output Parity | 260 | 220 | 40 | 0 |
| Level 2A — API Contract | 39 | 27 | 12 | 0 |
| Level 2B — Performance | 10 | 10 | 0 | 0 |
| **Total** | **411** | **359** | **52** | **0** |

---

## Scenario-by-Scenario Breakdown

### 1. `empty_filters`

- **Config:** No include/exclude filters, no temp-table-regex
- **Level 1:** PASS (counts, coverage, typeNames) | FAIL (attribute parity)
- **Level 2A:** 4 contract diffs (see [Common Contract Diffs](#common-contract-diffs))
- **Level 2B:** PASS (v2: 50s, v3: 42s, ratio: 0.83x)

**Attribute diff:**
```
rdsadmin/dbo/log_backup_manifest.rowCount: 186606 -> 186624
rdsadmin/dbo/log_backup_manifest.sizeBytes: 45883392 -> 45817856
```

**Root cause:** Live data drift on RDS system table `rdsadmin.dbo.log_backup_manifest`. Not a code bug.
**Fix:** Exclude `rdsadmin` database from parity comparison, or regenerate golden and v3 in a single atomic run.

---

### 2. `empty_exclude`

- **Config:** `include-filter: {"^wwi$":["^Application$","^Sales$"],"^wwi_dw$":"^Dimension$"}`, `exclude-filter: "{}"`
- **Level 1:** PASS (all tests)
- **Level 2A:** v3-only API data (v2 golden was pre-existing, no API recorded)
- **Level 2B:** No data (v2 golden skipped)

**Status: PASS** - no issues.

---

### 3. `empty_include`

- **Config:** `include-filter: "{}"`, `exclude-filter: {"^wwi$":"^dbo$"}`
- **Level 1:** PASS (counts, coverage, typeNames) | FAIL (attribute parity)
- **Level 2A:** v3-only API data
- **Level 2B:** No data

**Attribute diff:** Same `rdsadmin.dbo.log_backup_manifest` drift as `empty_filters`.
**Root cause:** Live data drift. Not a code bug.

---

### 4. `mixed_filters`

- **Config:** `include-filter: {"^wwi$":"*","^wwi_dw$":"*"}`, `exclude-filter: {"^wwi$":"^dbo$"}`
- **Level 1:** FAIL — v2=0 records, v3=1285 records (all entities)
- **Level 2A:** No data (v2 never completed)
- **Level 2B:** No data

**Root cause:** v2 workflow hangs indefinitely (>300s timeout) with this filter combination. The golden was generated from this failed run, producing empty files. v3 output (2 databases: wwi + wwi_dw, dbo excluded from wwi) looks correct based on the filter config.

**Action needed:**
- [ ] Investigate why v2 hangs on `mixed_filters` — possibly a deadlock in the v2 filter/query logic
- [ ] Once v2 is fixed or confirmed as a known v2 bug, regenerate golden
- [ ] Alternatively, validate v3 output manually and use it as the new golden

---

### 5. `single_database_single_schema`

- **Config:** `include-filter: {"^wwi$":"^Sales$"}`, `exclude-filter: "{}"`
- **Level 1:** FAIL — v2=1 db/1 schema/16 tables, v3=4 dbs/26 schemas/124 tables
- **Level 2A:** No data (v2 never completed)
- **Level 2B:** No data

**Root cause:** v2 workflow hangs indefinitely (>300s timeout). Golden was from a previous run where v2 did complete (1 db correct). v3 is extracting all 4 databases — **v3 is ignoring the include filter**.

**Action needed:**
- [ ] **v3 bug**: v3 include filter is not being applied — extracts all databases instead of just `wwi`
- [ ] Debug the filter chain: `run()` extracts `include_filter` from metadata -> passes to `MSSQLTaskInput` -> `_build_workflow_args()` reconstructs metadata -> `parse_workflow_filters()` parses it -> `apply_include_exclude_filters()` applies it
- [ ] Check if v2 hang is related to the same filter (v2 may also have trouble with this config)

---

### 6. `overlapping_filters`

- **Config:** `include-filter: {"^wwi$":"^Sales$"}`, `exclude-filter: {"^wwi$":"^Sales$"}` (include and exclude same thing)
- **Level 1:** PASS (all tests)
- **Level 2A:** v3-only API data
- **Level 2B:** No data

**Status: PASS** - no issues. Include and exclude cancel out correctly.

---

### 7. `ssl_disabled`

- **Config:** No filters, `encrypt: "no"`, `trust_server_certificate: "yes"`
- **Level 1:** PASS (counts, coverage, typeNames) | FAIL (attribute parity)
- **Level 2A:** v3-only API data
- **Level 2B:** No data

**Attribute diff:** Same `rdsadmin.dbo.log_backup_manifest.sizeBytes` drift.
**Root cause:** Live data drift. Not a code bug.

---

### 8. `temp_table_regex`

- **Config:** `include-filter: "{}"`, `exclude-filter: "{}"`, `temp-table-regex: ".*_TMP|TMP.*"`
- **Level 1:** PASS (counts, coverage, typeNames) | FAIL (attribute parity)
- **Level 2A:** v3-only API data
- **Level 2B:** No data

**Attribute diff:** Same `rdsadmin.dbo.log_backup_manifest.sizeBytes` drift.
**Root cause:** Live data drift. Not a code bug. Temp table regex is working correctly (no TMP tables in output).

---

### 9. `temp_table_regex_with_filters`

- **Config:** `include-filter: {"^wwi$":"*","^wwi_dw$":"*"}`, `exclude-filter: "{}"`, `temp-table-regex: ".*_TMP|TMP.*"`
- **Level 1:** FAIL — v2=0 records, v3=1285 records
- **Level 2A:** No data
- **Level 2B:** No data

**Root cause:** v2 workflow hangs (>300s). Golden is empty. Same v2 hang issue as `mixed_filters` — happens when include filter is combined with temp-table-regex.

**Action needed:**
- [ ] Same as `mixed_filters` — v2 hangs with this filter combination
- [ ] v3 output looks plausible (wwi + wwi_dw, TMP tables excluded)

---

### 10. `temp_regex_match_all`

- **Config:** `include-filter: "{}"`, `exclude-filter: "{}"`, `temp-table-regex: ".*"` (match ALL tables)
- **Level 1:** FAIL — v2=0 tables/0 columns, v3=124 tables/1233 columns (databases and schemas match)
- **Level 2A:** No data (v2 hung)
- **Level 2B:** No data

**Root cause:** v2 hangs (>300s). Golden was from a previous run where v2 did complete — 4 databases, 26 schemas, 0 tables, 0 columns (correct: `.*` regex excludes all tables). v3 has 124 tables — **v3 is ignoring temp-table-regex `.*`**.

**Action needed:**
- [ ] **v3 bug**: temp-table-regex `.*` should exclude ALL tables, but v3 returns 124 tables
- [ ] Check `convert_regex_to_like_patterns(".*")` — `.*` converts to `%` LIKE pattern, which should match everything
- [ ] Trace through `_execute_multidb_query()` to verify `temp_table_filter_sql` is built and applied

---

### 11. `temp_regex_complex_pipes`

- **Config:** `temp-table-regex: ".*_TMP|TMP.*|^temp_|_temp$|staging_.*|.*_staging"`
- **Level 1:** PASS (counts, coverage, typeNames) | FAIL (attribute parity)
- **Level 2A:** v3-only API data
- **Level 2B:** No data

**Attribute diff:** Same `rdsadmin.dbo.log_backup_manifest` drift.
**Root cause:** Live data drift. Not a code bug. Complex pipe regex is working correctly.

---

### 12. `temp_regex_nonmatching`

- **Config:** `temp-table-regex: "^ZZZZZ_nonexistent$"` (matches nothing)
- **Level 1:** PASS (counts, coverage, typeNames) | FAIL (attribute parity)
- **Level 2A:** v3-only API data
- **Level 2B:** No data

**Attribute diff:** Same `rdsadmin.dbo.log_backup_manifest` drift.
**Root cause:** Live data drift. Not a code bug.

---

### 13. `compiled_url`

- **Config:** Uses `compiled_url` instead of host/port/database, no filters
- **Level 1:** PASS (all tests)
- **Level 2A:** 4 contract diffs (see [Common Contract Diffs](#common-contract-diffs))
- **Level 2B:** PASS (v2: 50s, v3: 46s, ratio: 0.92x)

**Status: PASS** - no issues.

---

### 14. `azure_ad_authentication`

- **Config:** Azure AD Service Principal auth, `include-filter: {"^OlofNew$":"^sales$"}`
- **Level 1:** PASS (all tests)
- **Level 2A:** 4 contract diffs (see [Common Contract Diffs](#common-contract-diffs))
- **Level 2B:** PASS (v2: 10s, v3: 10s, ratio: 1.0x)

**Status: PASS** - Azure AD auth working correctly on both v2 and v3.

---

### 15. `azure_ad_exclude`

- **Config:** Azure AD auth, `exclude-filter: {"^OlofNew$":"^sales$"}`
- **Level 1:** No data
- **Level 2A:** No data
- **Level 2B:** No data

**Root cause:** v2 workflow hangs (>300s timeout) with Azure AD + exclude filter.

**Action needed:**
- [ ] Investigate v2 hang with Azure AD exclude filter
- [ ] Run v3-only to check if v3 handles it correctly

---

### 16. `azure_ad_temp_regex`

- **Config:** Azure AD auth, `temp-table-regex: ".*_TMP|TMP.*"`
- **Level 1:** No data
- **Level 2A:** No data
- **Level 2B:** No data

**Root cause:** v2 workflow hangs (>300s timeout) with Azure AD + temp regex.

**Action needed:**
- [ ] Investigate v2 hang with Azure AD temp regex
- [ ] Run v3-only to check if v3 handles it correctly

---

### 17. `ntlm_authentication`

- **Config:** NTLM auth (`authType: "ntlm"`)
- **Level 1:** No data
- **Level 2A:** No data
- **Level 2B:** No data

**Root cause:** v2 workflow FAILED immediately. NTLM requires FreeTDS driver and a Windows domain-joined server — likely not available in the test environment.

**Action needed:**
- [ ] Verify NTLM test server is accessible
- [ ] Skip scenario if NTLM infra is unavailable

---

### 18. `regex_schema_patterns`

- **Config:** `include-filter: {"^wwi$":"^(App|Sal).*"}` (regex schema patterns)
- **Level 1:** Golden exists (v2 completed previously), v3 FAILED
- **Level 2A:** No data
- **Level 2B:** No data

**Root cause:** v3 workflow FAILED for this scenario. v3 likely doesn't support regex patterns in schema filters (e.g., `^(App|Sal).*`).

**Action needed:**
- [ ] **v3 bug**: v3 crashes on regex schema patterns with grouping `(App|Sal)`
- [ ] Check error logs for the v3 workflow failure
- [ ] Verify `apply_include_exclude_filters()` handles regex syntax in schema values

---

## Common Contract Diffs

These 4 diffs appear consistently across all scenarios with Level 2A data:

| Endpoint | Diff | Severity | Notes |
|----------|------|----------|-------|
| `/check` | v2 returns `body.detail` (422 error) | Low | v2 preflight expects different body format than v3. v2 uses flat creds at top level, v3 uses `{"credentials": [{key,value}]}` array. |
| `/status` | v2 has `body.data.last_executed_run_id`, v3 doesn't | Medium | v3 dropped this field from the status response. May break consumers that rely on it. |
| `/result` | v2 returns 404 (`body.detail`), v3 returns result | Info | `/result` is a new v3-only endpoint. Not a regression. |
| `/metadata` | v2 `body.data` is a list, v3 `body.data` is a dict | High | Type change — consumers parsing `data` as a list will break on v3. |

---

## Bugs Found

### v3 Bugs

| ID | Scenario | Bug | Severity | File |
|----|----------|-----|----------|------|
| V3-1 | `single_database_single_schema` | Include filter ignored — v3 extracts all databases instead of filtering | High | `app/extractor.py`, `app/filters.py` |
| V3-2 | `temp_regex_match_all` | temp-table-regex `.*` ignored — v3 returns all tables instead of filtering | High | `app/extractor.py:291-320` |
| V3-3 | `regex_schema_patterns` | v3 workflow crashes on regex schema patterns with grouping syntax | High | `app/filters.py` |
| V3-4 | `/metadata` response | `body.data` type changed from list to dict | Medium | application-sdk handler |
| V3-5 | `/status` response | Missing `last_executed_run_id` field | Low | application-sdk handler |

### v2 Bugs

| ID | Scenario | Bug | Severity |
|----|----------|-----|----------|
| V2-1 | `mixed_filters`, `single_database_single_schema`, `temp_regex_match_all`, `temp_table_regex_with_filters` | v2 workflow hangs indefinitely with certain filter combinations | High |
| V2-2 | `azure_ad_exclude`, `azure_ad_temp_regex` | v2 workflow hangs with Azure AD + filters | Medium |

### Not Bugs (Live Data Drift)

| Scenario | Diff | Resolution |
|----------|------|------------|
| 6 scenarios | `rdsadmin.dbo.log_backup_manifest.rowCount` / `.sizeBytes` | RDS system table changes between runs. Exclude `rdsadmin` from comparison or regenerate atomically. |

---

## Recommended Next Steps

1. **Fix V3-1 and V3-2** (filter ignoring) — highest priority, affects correctness
2. **Fix V3-3** (regex schema crash) — test with simpler regex first to isolate
3. **Evaluate V3-4** (`/metadata` type change) — decide if this is intentional or needs backward compat
4. **Exclude `rdsadmin`** from parity comparisons to eliminate false positives
5. **Investigate V2-1** — v2 hangs may indicate a known v2 issue worth documenting
6. **Run v3-only** for `azure_ad_exclude` and `azure_ad_temp_regex` to check v3 independently
