---
id: RP05
applies-to: [app, contract]
---
# Each question hands QueryIntelligence its SQL, scope and dialect under the keys the contract names

Principle: The `qi` node in `contract/app.pkl` reads four question attributes by name:
`metabaseQuery`, `metabaseSourceDatabaseName`, `metabaseSourceSchemaName` and
`metabaseSourceEngine`. The code emits exactly those. The engine value is a dialect name
QueryIntelligence knows, mapped from the Metabase engine through `METABASE_ATLAN_SOURCE_ENGINE_MAP`,
and an empty engine is left out rather than sent as an empty string.
Why: QueryIntelligence does not fail on a missing or unknown value. It falls back to a default
parser, and a past release lost about half of all SQL lineage because every query was parsed as the
wrong dialect.
Ask:
- Does the diff rename, drop or move one of these attributes, or change a `qi` node key, without changing the other side?
- For a new or changed Metabase engine, which dialect name reaches QueryIntelligence, and is an unmapped engine passed through raw?
- Can an empty string reach `metabaseSourceEngine` or a scope attribute where leaving the attribute out was intended?
Don't flag: manifest and form-key drift, which conformance reports (`K006`, `K019`), and stale
generated files (`K003`, `K004`). Do not flag an engine added to the map with a stated dialect name.
Severity: High when SQL lineage for a supported engine would be dropped or parsed as the wrong
dialect under a green run. Medium for an engine few customers use.
