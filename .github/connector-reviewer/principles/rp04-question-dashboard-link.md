---
id: RP04
applies-to: [app]
---
# The question-to-dashboard link travels only as a BIProcess

Principle: A MetabaseQuestion never carries references to dashboards. The link between a question
and the dashboards it sits on is published only as one BIProcess per question, with the question as
its input and those dashboards as its outputs, and the refs travel on one channel only.
Why: Publish orders the Metabase types collection, question, dashboard, then BIProcess. A question
that references dashboards points at entities that do not exist yet and is rejected, and its
BIProcess fails with it. A ref sent on two channels is rejected only on incremental runs, so the
first full crawl looks fine.
Ask:
- Does the diff set a dashboard reference, or a reference to any type published later, on a question or on another Metabase asset?
- Does each question still get exactly one BIProcess, emitted only when it sits on at least one dashboard, whose outputs are those dashboards?
- Could the lineage refs now appear both in the serialized attributes and in a separate relationship block?
Don't flag: the child-to-collection links, whose direction conformance checks (`P055`), and
serialisation through `entity_bytes` (`P052`). `metabaseDashboardCount` is a plain count and fine.
Severity: High when publish would reject questions or BIProcess records, on full or incremental
runs. Medium for a new reference whose target type is published earlier.
