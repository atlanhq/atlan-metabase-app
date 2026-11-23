---
id: RP02
applies-to: [app, tests]
---
# Every supported Metabase server version still parses

Principle: Metabase has renamed and moved fields between releases, and the app reads both forms of
`dashcards` and `ordered_cards`, `dataset_query.type` and `dataset_query.stages[0]` `lib/type`, and
`details.dbname`, `details.db` and the database name. For `/api/database`, preflight accepts both a
bare list and the `data` envelope; extraction assumes the envelope. A change keeps every form the
code reads today unless the PR names the server versions it drops.
Why: Customers run many Metabase versions at once. Losing a fallback does not fail the run:
dashboards lose their questions, native questions are typed as MBQL, or SQL loses its database
scope, and lineage quietly shrinks.
Ask:
- Does the diff read a new response field, or rewrite a read that has a fallback, without keeping the other server form?
- When a field is missing, does the code fall through to the other form, or read absence as empty (no cards, no query type, no database)?
- Integration and end-to-end tests run one pinned Metabase image; is each form the diff touches covered by a unit fixture of that shape?
Don't flag: a new fallback added beside the old one, or a field only one server version has ever
sent. HTTP failures turned into empty results are reported by conformance (`E020`). A workaround
scoped to one server is FP13's concern; this principle is about not dropping a supported version.
Severity: High when a supported Metabase version would lose dashboard cards, question SQL or lineage
under a green run. Medium when the older form fails loudly instead.
