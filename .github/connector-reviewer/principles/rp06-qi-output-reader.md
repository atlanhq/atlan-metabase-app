---
id: RP06
applies-to: [app]
---
# The lineage build reads every QueryIntelligence output shape still in use

Principle: QueryIntelligence is a live platform app this connector does not pin, and its output has
changed shape several times. `app/lineage/qi_reader.py` accepts each generation (`parsedData`,
`gudusoft` or `PARSED_DATA`; `sql` or `SQL`; the question in `extra.attributes` or `QUERY_ID`;
id-based or inline column sources) and reads from the prefix QueryIntelligence really writes to.
Why: A reader that misses the current shape or location builds zero Process and ColumnProcess
records while `extract_lineage` still succeeds. Lineage has disappeared this way more than once
with no failed run.
Ask:
- Does the diff remove or reorder a fallback key, or assume a field is always present that an older or newer generation lacks?
- Does it change the prefix or file pattern read from QueryIntelligence output, and does the PR show where the current release writes?
- If records arrive but none yield a question id or a table, is that visible (a count, a log, a declared gap) rather than an empty success?
Don't flag: unparseable lines skipped with a warning, or QueryIntelligence's own parse failures.
Swallowed exceptions and values returned from an `except` are reported by conformance (`E001`,
`E007`, `E014`).
Severity: High when lineage would come out empty or partial under a green run. Medium when the
miss fails loudly.
