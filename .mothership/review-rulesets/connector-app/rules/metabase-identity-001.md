---
schema: 2
id: META-IDENTITY-001
level: L3
category: correctness
globs: []
severity: HIGH
suppressible: true
---
# Preserve Metabase metadata identity

- For changes that can affect Metabase-specific asset identity or hierarchy,
  verify the behavior against an existing repository fixture, test, or source
  contract and cite that evidence.
- Report an unexplained change to stable qualified-name components, source
  identifiers, or parent relationships because it can duplicate, delete, or
  detach previously published assets.
- Do not report when the diff cannot affect Metabase-specific identity or
  hierarchy.
- This rule does not restate L1 conformance, shared L2 connector rules, or
  shared L4 platform-runtime rules; those layers remain authoritative.
