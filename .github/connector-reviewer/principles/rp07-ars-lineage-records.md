---
id: RP07
applies-to: [app]
---
# Lineage stage records keep the shape and location the publish app's resolver reads

Principle: Process and ColumnProcess records are written under `lineage-stage/resolvable/`, carry
their refs only in `attributes` with no `relationshipAttributes`, give every upstream ref an
`arsIdentity` whose components include `connectorType`, drop an edge whose upstream is not found
(`noMatchAction: "drop"`) while keeping the process (`arsNoNestedMatchAction: "keep"`), and build
each ColumnProcess's `process` ref from its Process's hash (same question id and SQL).
Why: Each rule was learned from a failed lineage publish. Records outside `resolvable/` skip
resolution, a mirrored relationship block leaks unresolved refs, a missing connector type yields a
null qualified name, and synthesised partial objects need a permission the publish user may lack.
Ask:
- Does the diff change the staging path, the record keys, or any `arsIdentity` field or action?
- Can an upstream ref now lack `connectorType`, or a database, schema or table component?
- Does each ColumnProcess's parent ref still match the qualified name of the Process built from the same question id and SQL?
Don't flag: that these records are plain dicts rather than pyatlan assets, which is conformance's
concern (`P058`). A change the PR ties to a stated change in the publish app is a decision, not a
defect.
Severity: High when lineage publish would reject Process records or silently drop their edges.
Medium for a field change the resolver ignores today.
