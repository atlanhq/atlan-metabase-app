---
id: RP09
applies-to: [app]
---
# A question's asset does not hang on its SQL

Principle: A question is published because it sits in a selected collection. Its SQL (from
`/api/dataset/native`) and its database details only add lineage attributes. Today
`process_assets` skips a question whole when either is missing. A change must not add a new
condition that skips a question; a new gap in SQL or database details leaves the question published
without lineage attributes.
Why: The SQL call depends on query permissions and on the question's shape, so it fails or comes
back empty for valid questions. A question skipped for that reason vanishes from the catalog, and an
empty SQL response records no residual, so the run can stay green.
Ask:
- Does the diff add a new reason a question, or its BIProcess, is left out of `process_assets` output?
- Where the diff adds a new way for SQL or database details to go missing, is the question still published, or at least recorded as a gap?
- Does the change make SQL fetching depend on something new: a permission, a field, a server version?
Don't flag: questions outside the selected collections, or the existing skip itself: mention it if
useful, never require fixing it. A gap declared through a residual record is fine; FP01 states the
general rule.
Severity: High when valid questions would vanish under a green run. Medium when they vanish with a
recorded gap.
