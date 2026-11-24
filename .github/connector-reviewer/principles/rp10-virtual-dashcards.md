---
id: RP10
applies-to: [app, tests]
---
# A dashboard's text, heading and link cards are skipped, never read as questions

Principle: `GET /api/dashboard/:id` returns virtual dashcards (text, heading, link) beside question
cards. A virtual dashcard has `card_id` null and a `card` that is not null but a stub
(`{"query_average_duration": null}`) with no `id`. The dashcard loop in `process_assets` skips any
dashcard without a `card_id` before it reads `card["id"]`. That guard, or an equivalent check on
`card_id`, stays, and a dashcard is never treated as a question by the truthiness of `card`.
Why: Text and heading cards are on most real dashboards. Without the guard the stub `card` is
truthy, `card["id"]` raises `KeyError` and the whole asset activity fails. Unit fixtures hold only
question cards, so tests stay green while extraction breaks for most tenants.
Ask:
- Does the diff remove or reorder the `card_id` check, or test `card` instead of `card_id`?
- Does a new read of dashcard fields assume every dashcard has a real `card`?
- Is there a unit fixture with a virtual dashcard (`card_id` null, stub `card`) for the path touched?
Don't flag: the loop's style, or cards with a real `card_id` whose `card` is absent, which the code
already skips. Field renames between server versions are RP02's concern.
Severity: High when a virtual dashcard can reach `card["id"]` or be counted as a question; medium
when only a fixture for it is missing.
