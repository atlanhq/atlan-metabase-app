---
name: connector-review
description: Mandatory pre-commit review of the working diff against the L1 conformance suite and the L2/L3/L4 connector review rulesets. Run before every git commit; the commit gate blocks unreviewed changes. Use when the user says review, pre-commit review, or when a commit is blocked by the review gate.
---

# Connector review (local, L1–L4)

Review the current change exactly the way the CI connector reviewer does. Rule
files are the only authority — never review from memory of what a rule says.

## 1. Scope

`git diff HEAD --name-only` plus `git diff --cached --name-only` (union). If both
are empty, use `git diff origin/main...HEAD --name-only`. The changed files and
their diffs are the only commentable scope; read surrounding code to verify, not
to widen scope.

## 2. Load rules

1. Run `scripts/fetch-review-rules.sh` (safe to run every time; it is a no-op
   pinned to cache when offline).
2. L2: every rule file in `.mothership/.cache/review-rulesets/connector-app/rules/`.
   L4: every rule file in `.mothership/.cache/review-rulesets/platform/rules/`.
   L3: every rule file in `.mothership/review-rulesets/connector-app/rules/`
   (this repo, committed).
3. Select a rule when any frontmatter glob matches a changed file; `globs: []`
   means always selected. Honor `.mothership/review-rulesets/connector-app/suppressions.yaml`
   if present: skip a `suppressible: true` rule listed there with an unexpired
   `expires` date, and say so in the report.
4. If the L2/L4 cache is missing and cannot be fetched, review L1+L3 only and
   put "L2/L4 NOT REVIEWED — rules unavailable" at the top of the report.

## 3. L1 — conformance suite

Run: `uv sync --quiet && uvx atlan-application-sdk-conformance detect --repo . --series CEPODLTIBKS`

This is the local equivalent of this repo's `suite / Conformance Gate` check
(`.github/workflows/conformance.yaml` → the shared
`atlanhq/application-sdk/.github/workflows/conformance-reusable.yaml@main` →
`run-conformance-detect` composite action). CI runs one series per matrix leg
for per-check status reporting; locally all series run together in one pass.
CI-only flags (`--exit-zero`, SARIF-per-leg filenames) are stripped; SARIF
output still writes to `conformance.sarif` in the repo root.

Any BLOCK-tier failure is a BLOCKER finding (cite the check id). Surface WARN-tier
flags that intersect changed lines as observations. Never re-derive or restate the
suite's checks as review opinions — consume its output.

## 4. Pass A — rule-guided

Walk EVERY selected rule and record a verdict:
- `not_applicable` — the rule's subject does not appear in the changed files.
- `checked` — subject appears; you inspected the matching changed code and found
  no defect.
- `finding` — you cite the rule id in a finding.

Rules are violated indirectly more often than literally. For each rule also check:
scale-not-snapshot (a value small today that grows with tenant/source size),
distance (the violation one or two calls away from the diff), and removed
guardrails (deleting or weakening the check the rule relies on).

## 5. Pass B — open bug hunt

One independent pass over the diff for concrete correctness defects no rule names
(wrong logic, data loss, races). Prefix these `GENERAL-`. When you confirm one
defect, check the diff's sibling call sites for the same shape.

## 6. Verify, then report

For every candidate finding: quote the exact lines that make it true, attempt one
honest refutation, drop it if you cannot quote evidence or the defect predates
this change. Severity starts at the rule's `severity:` frontmatter.

Report format:
1. Verdict line: `REVIEW PASS` or `REVIEW FAIL (N blockers)`.
2. Findings: `[SEVERITY] RULE-ID file:line — one-line defect + concrete fix`.
3. Coverage table: one row per selected rule with its verdict.
4. Skipped/suppressed rules and any unavailable levels, named explicitly.

## 7. Write the review marker (required — the commit gate reads it)

\`\`\`bash
python3 - <<'EOF'
import json, subprocess, datetime, os
state = subprocess.run(
    "git rev-parse HEAD; git diff HEAD; git diff --cached",
    shell=True, capture_output=True, text=True).stdout
import hashlib
os.makedirs(".mothership/.cache", exist_ok=True)
lock = {}
try: lock = json.load(open(".mothership/.cache/rules.lock"))
except Exception: pass
json.dump({
    "state_hash": hashlib.sha256(state.encode()).hexdigest(),
    "verdict": "REPLACE_WITH_PASS_OR_FAIL",
    "sdk_rules_sha": lock.get("sha"),
    "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
}, open(".mothership/.cache/last-review.json", "w"))
EOF
\`\`\`

Set `verdict` to `PASS` only when there are zero unresolved BLOCKER findings.
Fix blockers, re-run this skill, and only then commit.
