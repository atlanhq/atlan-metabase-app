---
id: RP08
applies-to: [contract, generated, app]
---
# The second entrypoint and the lineage publish leg stay wired as the DAG expects

Principle: `extract-lineage` runs only after `qi` and `publish` succeed, takes its prefix and
connection qualified name from `extract_metadata`'s outputs, and forwards `extraction_method` and
`agent_json` like the extract node. Asset publish is the typed `pipeline.publish` step;
`lineage-publish` reads `extract-lineage`'s outputs and never writes the connection cache.
Why: Node defaults differ by node class, so each mistake here is silent. A publish declared under
`extraNodes` stopped writing current state, a lineage publish that writes the connection cache
overwrites the asset index other lineage builders read, and a child workflow without the agent args
runs on the wrong queue.
Ask:
- Does the diff move a publish node between the typed step and `extraNodes`, or set a connection-cache or current-state flag?
- Does each `extract-lineage` arg and `lineage-publish` prefix still point at an output its producing entrypoint really returns?
- Do both entrypoints still receive the same routing args?
Don't flag: what conformance checks: manifest and contract drift (`K006`, `K018`, `K019`), node app
names (`K013`), agent args on the extract node (`P029`), stale generated files (`K003`, `K004`).
Node listing order in the manifest is presentational.
Severity: High when a run would skip current state, corrupt the connection cache, or send lineage
to the wrong queue or prefix. Medium for wiring that works today only through a default.
