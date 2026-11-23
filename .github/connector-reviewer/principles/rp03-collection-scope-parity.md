---
id: RP03
applies-to: [app]
---
# A collection selection means the same thing in the picker, preflight and the crawl

Principle: Three places decide which collections are in scope: the collection picker
(`fetch_metadata`), the preflight count checks, and the extraction filter (`filter_collections`
with its dashboard and question cascade). They agree on personal collections, on the root collection
(a null `collection_id` is `root`), on ids compared as strings, and on exclude winning over include.
Why: They already differ: the picker and preflight drop personal collections while the crawl keeps
them, and preflight compares a root-level item as the text `None` while the crawl uses `root`. Each
new difference makes preflight promise one scope while the run publishes another.
Ask:
- Does the diff change scope in one of the three places without the other two, or add a fourth place that decides it?
- How does the change treat a personal collection, a root-level dashboard or question, and an integer versus string id?
- Can a filter value the app cannot read (bad JSON, a non-object) widen the crawl to every collection without saying so?
Don't flag: whether filter fields accept the string form the platform sends, which conformance
reports (`K021`). Flag only a difference the diff adds or widens; the existing differences above
may be mentioned, but never require fixing them.
Severity: High when a run would publish collections the customer excluded or drop ones they
selected. Medium when only preflight counts disagree with what the crawl publishes.
