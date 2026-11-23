---
id: RP01
applies-to: [app]
---
# A Metabase qualified name is keyed on the Metabase id, and the id stays its last segment

Principle: Collection, dashboard and question qualified names are keyed on the Metabase id alone
(the literal `root` for the root collection), never on a name, slug or position. The id stays the
last segment, because the lineage build takes the question id from the qualified name
QueryIntelligence echoes back, and BIProcess records take dashboard ids from their output refs.
Why: Metabase names are not unique and change freely. If the key or the last segment changes,
assets duplicate on the next run, and the code that splits ids off qualified names attaches lineage
and BIProcess edges to the wrong question or to none, with no error.
Ask:
- Does the diff change what is passed as `metabase_id`, or let a name, slug or blank value reach a qualified name rather than only the display name?
- Does it change the grammar of a Metabase, BIProcess or lineage process qualified name, or any code that splits an id off one (`rsplit("/", 1)`)?
- Does a root-level item (null `collection_id`) still resolve to the one `root` collection and its single qualified name?
Don't flag: asset identity in general, which FP02 covers, and hand-built names, which conformance
reports (`P028`). Do not flag a blank display name falling back to the id: that is deliberate.
Severity: High when published Metabase assets would be renamed, merged or orphaned, or lineage would
attach to the wrong question. Medium when names stay the same but the id becomes harder to recover.
