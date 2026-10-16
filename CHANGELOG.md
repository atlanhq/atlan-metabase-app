# Changelog

## v2.4.1 (October 15, 2026)

Full Changelog: https://github.com/atlanhq/atlan-metabase-app/compare/v2.4.0...v2.4.1

### Bug Fixes

- ignore ruff G201, which contradicts conformance L017 (#677) (by @vaibhavatlan in [c2a7ec1](https://github.com/atlanhq/atlan-metabase-app/commit/c2a7ec1))
- call super().close() so BaseClient releases its connection pool (#716) (by @cmgrote in [4b00497](https://github.com/atlanhq/atlan-metabase-app/commit/4b00497))

### Refactoring

- replace build_credential_ref with the SDK's route_credentials (#621) (by @cmgrote in [967bb0c](https://github.com/atlanhq/atlan-metabase-app/commit/967bb0c))
- build BIProcess via pyatlan_v9 BIProcess.creator (clears last P028) (#706) (by @vaibhavatlan in [bb1f69c](https://github.com/atlanhq/atlan-metabase-app/commit/bb1f69c))

### Chores

- lock file maintenance (#610) (by @atlan-app-fleet[bot] in [aaa8201](https://github.com/atlanhq/atlan-metabase-app/commit/aaa8201))
- lock file maintenance (#613) (by @atlan-app-fleet[bot] in [143dfc8](https://github.com/atlanhq/atlan-metabase-app/commit/143dfc8))
- update dependency atlan-application-sdk to v3.39.1 (#616) (by @atlan-app-fleet[bot] in [d318847](https://github.com/atlanhq/atlan-metabase-app/commit/d318847))
- update dependency atlan-application-sdk-conformance to v0.39.1 (#617) (by @atlan-app-fleet[bot] in [1be5755](https://github.com/atlanhq/atlan-metabase-app/commit/1be5755))
- lock file maintenance (#615) (by @atlan-app-fleet[bot] in [36d09d5](https://github.com/atlanhq/atlan-metabase-app/commit/36d09d5))
- lock file maintenance (#620) (by @atlan-app-fleet[bot] in [b156ca2](https://github.com/atlanhq/atlan-metabase-app/commit/b156ca2))
- lock file maintenance (#622) (by @atlan-app-fleet[bot] in [770a609](https://github.com/atlanhq/atlan-metabase-app/commit/770a609))
- update dependency atlan-application-sdk to v3.40.0 (#623) (by @atlan-app-fleet[bot] in [6d6f0b9](https://github.com/atlanhq/atlan-metabase-app/commit/6d6f0b9))
- lock file maintenance (#624) (by @atlan-app-fleet[bot] in [1363fb5](https://github.com/atlanhq/atlan-metabase-app/commit/1363fb5))
- update dependency atlan-application-sdk-conformance to v0.40.0 (#625) (by @atlan-app-fleet[bot] in [ef75ac5](https://github.com/atlanhq/atlan-metabase-app/commit/ef75ac5))
- lock file maintenance (#626) (by @atlan-app-fleet[bot] in [2a05170](https://github.com/atlanhq/atlan-metabase-app/commit/2a05170))
- update dependency atlan-application-sdk-conformance to v0.40.1 (#627) (by @atlan-app-fleet[bot] in [d13365b](https://github.com/atlanhq/atlan-metabase-app/commit/d13365b))
- lock file maintenance (#628) (by @atlan-app-fleet[bot] in [2b83d7d](https://github.com/atlanhq/atlan-metabase-app/commit/2b83d7d))
- lock file maintenance (#632) (by @atlan-app-fleet[bot] in [b61143c](https://github.com/atlanhq/atlan-metabase-app/commit/b61143c))
- lock file maintenance (#633) (by @atlan-app-fleet[bot] in [2dce91f](https://github.com/atlanhq/atlan-metabase-app/commit/2dce91f))
- lock file maintenance (#634) (by @atlan-app-fleet[bot] in [84894de](https://github.com/atlanhq/atlan-metabase-app/commit/84894de))
- lock file maintenance (#635) (by @atlan-app-fleet[bot] in [d92d706](https://github.com/atlanhq/atlan-metabase-app/commit/d92d706))
- lock file maintenance (#636) (by @atlan-app-fleet[bot] in [d8e8316](https://github.com/atlanhq/atlan-metabase-app/commit/d8e8316))
- lock file maintenance (#639) (by @atlan-app-fleet[bot] in [be369de](https://github.com/atlanhq/atlan-metabase-app/commit/be369de))
- update dependency atlan-application-sdk-conformance to v0.41.0 (#640) (by @atlan-app-fleet[bot] in [2216376](https://github.com/atlanhq/atlan-metabase-app/commit/2216376))
- lock file maintenance (#641) (by @atlan-app-fleet[bot] in [fdf1f86](https://github.com/atlanhq/atlan-metabase-app/commit/fdf1f86))
- lock file maintenance (#645) (by @atlan-app-fleet[bot] in [820e183](https://github.com/atlanhq/atlan-metabase-app/commit/820e183))
- lock file maintenance (#647) (by @atlan-app-fleet[bot] in [0b66a2a](https://github.com/atlanhq/atlan-metabase-app/commit/0b66a2a))
- lock file maintenance (#648) (by @atlan-app-fleet[bot] in [8377096](https://github.com/atlanhq/atlan-metabase-app/commit/8377096))
- lock file maintenance (#649) (by @atlan-app-fleet[bot] in [a5181be](https://github.com/atlanhq/atlan-metabase-app/commit/a5181be))
- lock file maintenance (#654) (by @atlan-app-fleet[bot] in [29469d9](https://github.com/atlanhq/atlan-metabase-app/commit/29469d9))
- lock file maintenance (#656) (by @atlan-app-fleet[bot] in [4a587ba](https://github.com/atlanhq/atlan-metabase-app/commit/4a587ba))
- update atlan framework dependencies (#655) (by @atlan-app-fleet[bot] in [370021e](https://github.com/atlanhq/atlan-metabase-app/commit/370021e))
- lock file maintenance (#657) (by @atlan-app-fleet[bot] in [2d7b675](https://github.com/atlanhq/atlan-metabase-app/commit/2d7b675))
- lock file maintenance (#659) (by @atlan-app-fleet[bot] in [5e677d0](https://github.com/atlanhq/atlan-metabase-app/commit/5e677d0))
- lock file maintenance (#661) (by @atlan-app-fleet[bot] in [b910560](https://github.com/atlanhq/atlan-metabase-app/commit/b910560))
- lock file maintenance (#662) (by @atlan-app-fleet[bot] in [2795cc5](https://github.com/atlanhq/atlan-metabase-app/commit/2795cc5))
- lock file maintenance (#665) (by @atlan-app-fleet[bot] in [7273274](https://github.com/atlanhq/atlan-metabase-app/commit/7273274))
- lock file maintenance (#667) (by @atlan-app-fleet[bot] in [cc2da81](https://github.com/atlanhq/atlan-metabase-app/commit/cc2da81))
- lock file maintenance (#668) (by @atlan-app-fleet[bot] in [47dbdd6](https://github.com/atlanhq/atlan-metabase-app/commit/47dbdd6))
- lock file maintenance (#669) (by @atlan-app-fleet[bot] in [7540d79](https://github.com/atlanhq/atlan-metabase-app/commit/7540d79))
- lock file maintenance (#672) (by @atlan-app-fleet[bot] in [366e4c6](https://github.com/atlanhq/atlan-metabase-app/commit/366e4c6))
- update dependency atlan-application-sdk-conformance to v0.42.1 (#671) (by @atlan-app-fleet[bot] in [88f9a78](https://github.com/atlanhq/atlan-metabase-app/commit/88f9a78))
- lock file maintenance (#675) (by @atlan-app-fleet[bot] in [52542d6](https://github.com/atlanhq/atlan-metabase-app/commit/52542d6))
- lock file maintenance (#676) (by @atlan-app-fleet[bot] in [0356cf6](https://github.com/atlanhq/atlan-metabase-app/commit/0356cf6))
- lock file maintenance (#678) (by @atlan-app-fleet[bot] in [24c686c](https://github.com/atlanhq/atlan-metabase-app/commit/24c686c))
- lock file maintenance (#679) (by @atlan-app-fleet[bot] in [56e90b1](https://github.com/atlanhq/atlan-metabase-app/commit/56e90b1))
- update atlan framework dependencies (#680) (by @atlan-app-fleet[bot] in [b7b9ab3](https://github.com/atlanhq/atlan-metabase-app/commit/b7b9ab3))
- lock file maintenance (#681) (by @atlan-app-fleet[bot] in [e2f1a1f](https://github.com/atlanhq/atlan-metabase-app/commit/e2f1a1f))
- lock file maintenance (#683) (by @atlan-app-fleet[bot] in [d03d237](https://github.com/atlanhq/atlan-metabase-app/commit/d03d237))
- update atlan framework dependencies (#684) (by @atlan-app-fleet[bot] in [946e101](https://github.com/atlanhq/atlan-metabase-app/commit/946e101))
- lock file maintenance (#685) (by @atlan-app-fleet[bot] in [22018d9](https://github.com/atlanhq/atlan-metabase-app/commit/22018d9))
- lock file maintenance (#687) (by @atlan-app-fleet[bot] in [3de7216](https://github.com/atlanhq/atlan-metabase-app/commit/3de7216))
- update dependency app-contract-toolkit to v0.30.0 (#691) (by @atlan-app-fleet[bot] in [dc1befe](https://github.com/atlanhq/atlan-metabase-app/commit/dc1befe))
- lock file maintenance (#694) (by @atlan-app-fleet[bot] in [8d668e2](https://github.com/atlanhq/atlan-metabase-app/commit/8d668e2))
- update atlan framework dependencies (#695) (by @atlan-app-fleet[bot] in [03d903a](https://github.com/atlanhq/atlan-metabase-app/commit/03d903a))
- lock file maintenance (#698) (by @atlan-app-fleet[bot] in [5699787](https://github.com/atlanhq/atlan-metabase-app/commit/5699787))
- update dependency atlan-application-sdk-conformance to v0.44.1 (#699) (by @atlan-app-fleet[bot] in [8c23383](https://github.com/atlanhq/atlan-metabase-app/commit/8c23383))
- update atlan framework dependencies (#703) (by @atlan-app-fleet[bot] in [7279756](https://github.com/atlanhq/atlan-metabase-app/commit/7279756))
- lock file maintenance (#704) (by @atlan-app-fleet[bot] in [c42730c](https://github.com/atlanhq/atlan-metabase-app/commit/c42730c))
- lock file maintenance (#705) (by @atlan-app-fleet[bot] in [4d3069b](https://github.com/atlanhq/atlan-metabase-app/commit/4d3069b))
- lock file maintenance (#707) (by @atlan-app-fleet[bot] in [80aea50](https://github.com/atlanhq/atlan-metabase-app/commit/80aea50))
- update atlan framework dependencies (#708) (by @atlan-app-fleet[bot] in [c5c8944](https://github.com/atlanhq/atlan-metabase-app/commit/c5c8944))
- lock file maintenance (#709) (by @atlan-app-fleet[bot] in [fe8e8cf](https://github.com/atlanhq/atlan-metabase-app/commit/fe8e8cf))
- lock file maintenance (#710) (by @atlan-app-fleet[bot] in [8a46f33](https://github.com/atlanhq/atlan-metabase-app/commit/8a46f33))
- lock file maintenance (#711) (by @atlan-app-fleet[bot] in [de56ab9](https://github.com/atlanhq/atlan-metabase-app/commit/de56ab9))
- lock file maintenance (#712) (by @atlan-app-fleet[bot] in [863199a](https://github.com/atlanhq/atlan-metabase-app/commit/863199a))
- lock file maintenance (#713) (by @atlan-app-fleet[bot] in [ead0c8f](https://github.com/atlanhq/atlan-metabase-app/commit/ead0c8f))
- lock file maintenance (#714) (by @atlan-app-fleet[bot] in [707fb89](https://github.com/atlanhq/atlan-metabase-app/commit/707fb89))
- lock file maintenance (#715) (by @atlan-app-fleet[bot] in [5f2d3eb](https://github.com/atlanhq/atlan-metabase-app/commit/5f2d3eb))
- lock file maintenance (#717) (by @atlan-app-fleet[bot] in [2541bce](https://github.com/atlanhq/atlan-metabase-app/commit/2541bce))
- update dependency atlan-application-sdk to v3.46.0 (#718) (by @atlan-app-fleet[bot] in [2deffcc](https://github.com/atlanhq/atlan-metabase-app/commit/2deffcc))
- update dependency atlan-application-sdk-conformance to v0.47.0 (#719) (by @atlan-app-fleet[bot] in [f38af4a](https://github.com/atlanhq/atlan-metabase-app/commit/f38af4a))
- lock file maintenance (#723) (by @atlan-app-fleet[bot] in [c22d551](https://github.com/atlanhq/atlan-metabase-app/commit/c22d551))
- update atlan framework dependencies (#724) (by @atlan-app-fleet[bot] in [d8b3091](https://github.com/atlanhq/atlan-metabase-app/commit/d8b3091))
- lock file maintenance (#725) (by @atlan-app-fleet[bot] in [2c8d949](https://github.com/atlanhq/atlan-metabase-app/commit/2c8d949))
- lock file maintenance (#727) (by @atlan-app-fleet[bot] in [9133134](https://github.com/atlanhq/atlan-metabase-app/commit/9133134))
- update atlan framework dependencies (#728) (by @atlan-app-fleet[bot] in [1084a94](https://github.com/atlanhq/atlan-metabase-app/commit/1084a94))
- lock file maintenance (#731) (by @atlan-app-fleet[bot] in [b7c42a6](https://github.com/atlanhq/atlan-metabase-app/commit/b7c42a6))
- update atlan framework dependencies (#730) (by @atlan-app-fleet[bot] in [8d0c053](https://github.com/atlanhq/atlan-metabase-app/commit/8d0c053))
- lock file maintenance (#733) (by @atlan-app-fleet[bot] in [a6fa06a](https://github.com/atlanhq/atlan-metabase-app/commit/a6fa06a))


## v2.4.0 (September 25, 2026)

Full Changelog: https://github.com/atlanhq/atlan-metabase-app/compare/v2.3.0...v2.4.0

### Features

- bound probes by the gate budget and register the F016 matrix (#580) (by @cmgrote in [a258943](https://github.com/atlanhq/atlan-metabase-app/commit/a258943))

### Bug Fixes

- unpin the yanked pytest-timeout 2.5.0 (FND-379) (#350) (by @cmgrote in [760f161](https://github.com/atlanhq/atlan-metabase-app/commit/760f161))
- report Release Gate on merge-queue branches (FND-851) (#361) (by @cmgrote in [f5c1adb](https://github.com/atlanhq/atlan-metabase-app/commit/f5c1adb))
- local review kit v2 — real L1 run, diff-scoped gate, precise commit match (#368) (by @chetanchoudhary-atlan in [a489134](https://github.com/atlanhq/atlan-metabase-app/commit/a489134))
- set terminationGracePeriodSeconds to 24h to cover longest activity (#413) (by @SanilK2108 in [0fa52ea](https://github.com/atlanhq/atlan-metabase-app/commit/0fa52ea))
- publish via typed pipeline step so current-state is written (#438) (by @vaibhavatlan in [fc6e006](https://github.com/atlanhq/atlan-metabase-app/commit/fc6e006))
- guide the unresolved-leaf error site the classifier can't type (#451) (by @fyzanshaik-atlan in [b31782b](https://github.com/atlanhq/atlan-metabase-app/commit/b31782b))
- bound MetabaseInput.credentials and drop the P001 opt-out (#573) (by @vaibhavatlan in [15ef032](https://github.com/atlanhq/atlan-metabase-app/commit/15ef032))
- hand residual failures across pods by reference and drop shared output_path (#582) (by @cmgrote in [3ef391a](https://github.com/atlanhq/atlan-metabase-app/commit/3ef391a))
- raise failed Metabase responses as typed errors and drop E020 suppressions (#589) (by @vaibhavatlan in [3ebeb66](https://github.com/atlanhq/atlan-metabase-app/commit/3ebeb66))
- FND-2501 return a typed test-auth error (#585) (by @sachi-atlan in [2106f3a](https://github.com/atlanhq/atlan-metabase-app/commit/2106f3a))


## v2.3.0 (August 12, 2026)

Full Changelog: https://github.com/atlanhq/atlan-metabase-app/compare/v2.2.3...v2.3.0

### Features

- enforce hard gate mode and bump application-sdk to 3.27.1 (#318) (by @fyzanshaik-atlan in [3181635](https://github.com/atlanhq/atlan-metabase-app/commit/3181635))


## v2.2.3 (August 10, 2026)

Full Changelog: https://github.com/atlanhq/atlan-metabase-app/compare/v2.2.2...v2.2.3

### Bug Fixes

- resolve Pkl lockfile to app-contract-toolkit 0.20.0 (#305) (by @vaibhavatlan in [df0f603](https://github.com/atlanhq/atlan-metabase-app/commit/df0f603))


## v2.2.2 (August 09, 2026)

Full Changelog: https://github.com/atlanhq/atlan-metabase-app/compare/v2.2.1...v2.2.2

### Bug Fixes

- log auth-check failure before returning failed PreflightCheck (E009) (#220) (by @vaibhavatlan in [b194313](https://github.com/atlanhq/atlan-metabase-app/commit/b194313))
- offload sync JSONL/QI file I/O via SDK run_in_thread (heartbeat-timeout fix) (#229) (by @zaman-atlan in [b74e682](https://github.com/atlanhq/atlan-metabase-app/commit/b74e682))


## v2.2.1 (July 16, 2026)

Full Changelog: https://github.com/atlanhq/atlan-metabase-app/compare/v2.2.0...v2.2.1

### Bug Fixes

- drop hard-coded agent_spec override (inherit per-leg queue) (#204) (by @cmgrote in [cc6245b](https://github.com/atlanhq/atlan-metabase-app/commit/cc6245b))
- adopt release_model=semver so GitHub Releases publish semver image tags (#207) (by @vaibhavatlan in [039ef4c](https://github.com/atlanhq/atlan-metabase-app/commit/039ef4c))
- stop duplicating BIProcess lineage refs across attribute channels (#212) (by @vaibhavatlan in [aac06d5](https://github.com/atlanhq/atlan-metabase-app/commit/aac06d5))


## v2.2.0 (July 14, 2026)

Full Changelog: https://github.com/atlanhq/atlan-metabase-app/compare/v2.1.0...v2.2.0

### Features

- retire emitEntrypoints=false via toolkit route/card split (BLDX-1342) (#191) (by @vaibhavatlan in [c0f55b1](https://github.com/atlanhq/atlan-metabase-app/commit/c0f55b1))
- adopt SDK-native gate with tiered blocking/advisory checks and typed errors (#200) (by @fyzanshaik-atlan in [bd7b61c](https://github.com/atlanhq/atlan-metabase-app/commit/bd7b61c))

### Bug Fixes

- adopt toolkit 0.16.0 e2e codegen — generate everything, drop hand-written overrides (#138) (by @vaibhavatlan in [58b4eab](https://github.com/atlanhq/atlan-metabase-app/commit/58b4eab))
- migrate deploy block to pools schema so pkl eval regenerates (#186) (by @vaibhavatlan in [df548b6](https://github.com/atlanhq/atlan-metabase-app/commit/df548b6))


## v2.1.0 (June 24, 2026)

Full Changelog: https://github.com/atlanhq/atlan-metabase-app/compare/v2.0.1...v2.1.0

### Features

- enable KEDA scale-to-zero for metabase worker (via contract) (#129) (by @vaibhavatlan in [e35a9ca](https://github.com/atlanhq/atlan-metabase-app/commit/e35a9ca))


## v2.0.1 (June 22, 2026)

Full Changelog: https://github.com/atlanhq/atlan-metabase-app/compare/v2.0.0...v2.0.1

### Bug Fixes

- read QI output from _staging sibling prefix (#113) (by @vaibhavatlan in [f80f8b8](https://github.com/atlanhq/atlan-metabase-app/commit/f80f8b8))


## v2.0.0 (June 15, 2026)

Full Changelog: https://github.com/atlanhq/atlan-metabase-app/compare/v1.0.0...v2.0.0

### Features

- add e2e Docker pipeline using public metabase/metabase image (by @atlan-ci in [758416a](https://github.com/atlanhq/atlan-metabase-app/commit/758416a))
- replace shared LineageNode with custom extract_lineage @entrypoint (by @atlan-ci in [d6ad139](https://github.com/atlanhq/atlan-metabase-app/commit/d6ad139))
- ARS-based cross-connector lineage + unit-test coverage to 86% (by @atlan-ci in [8539886](https://github.com/atlanhq/atlan-metabase-app/commit/8539886))
- use latest canonical PKL patterns + workflowTypeOverride (by @atlan-ci in [ce6061c](https://github.com/atlanhq/atlan-metabase-app/commit/ce6061c))
- wire app to Global Marketplace + remove obsolete frontend (by @atlan-ci in [ef9e6bb](https://github.com/atlanhq/atlan-metabase-app/commit/ef9e6bb))
- migrate to toolkit 0.10.0 (atlan.yaml is now contract-generated) + remove SDR + add Trivy/Snyk (by @atlan-ci in [fc86a95](https://github.com/atlanhq/atlan-metabase-app/commit/fc86a95))
- scale to ~1000 Metabase assets + harden workflow + new tests (by @atlan-ci in [03b6cf4](https://github.com/atlanhq/atlan-metabase-app/commit/03b6cf4))
- apply the 3 field changes that missed the PR #23 merge (by @atlan-ci in [322a62a](https://github.com/atlanhq/atlan-metabase-app/commit/322a62a))
- migrate from QueryBasedTransformer to asset-mapper (by @atlan-ci in [2092d5d](https://github.com/atlanhq/atlan-metabase-app/commit/2092d5d))
- align structure with atlan-openapi-app (by @atlan-ci in [59ee959](https://github.com/atlanhq/atlan-metabase-app/commit/59ee959))
- add system-apps full-DAG e2e harness (by @atlan-ci in [7b93c15](https://github.com/atlanhq/atlan-metabase-app/commit/7b93c15))
- light shared Metabase seed for integration + e2e (by @atlan-ci in [1311a7c](https://github.com/atlanhq/atlan-metabase-app/commit/1311a7c))
- unblock lineage via MySQL source + drop postgres metadata DB (#49) (by @atlan-ci in [1b272c4](https://github.com/atlanhq/atlan-metabase-app/commit/1b272c4))
- wire AE credential body + agent-json refs (mysql-app parity) (#54) (by @atlan-ci in [27fd8b0](https://github.com/atlanhq/atlan-metabase-app/commit/27fd8b0))
- consume agent_json via CredentialRef.resolve — fixes _build_client error (#57) (by @atlan-ci in [b6324bd](https://github.com/atlanhq/atlan-metabase-app/commit/b6324bd))

### Bug Fixes

- regenerate PKL artifacts + fix pyright issues; pre-commit green (by @atlan-ci in [1d308ab](https://github.com/atlanhq/atlan-metabase-app/commit/1d308ab))
- support Metabase v0.49+ 'dashcards' field name in process_assets (by @atlan-ci in [1eea4db](https://github.com/atlanhq/atlan-metabase-app/commit/1eea4db))
- use PUT /api/dashboard/{id} with dashcards array (v0.49+ API) (by @atlan-ci in [ff11b56](https://github.com/atlanhq/atlan-metabase-app/commit/ff11b56))
- live-run e2e on local Metabase Docker surfaces 2 bugs (by @atlan-ci in [5800716](https://github.com/atlanhq/atlan-metabase-app/commit/5800716))
- lineage helper return types are dict[str, Any] (nested dicts) (by @atlan-ci in [38c4df0](https://github.com/atlanhq/atlan-metabase-app/commit/38c4df0))
- guard seed against Metabase virtual 'root' collection id (by @atlan-ci in [6b161db](https://github.com/atlanhq/atlan-metabase-app/commit/6b161db))
- seed root collections before nested + lighter QI-input assertion + port-aware mb_get (by @atlan-ci in [6302579](https://github.com/atlanhq/atlan-metabase-app/commit/6302579))
- drop dataset_query check — Metabase v0.61 /api/card summary omits it (by @atlan-ci in [3cedff3](https://github.com/atlanhq/atlan-metabase-app/commit/3cedff3))
- rename leftover workflow_transform_metadata_start scenario (by @atlan-ci in [218bd76](https://github.com/atlanhq/atlan-metabase-app/commit/218bd76))
- suppress auto-generated entrypoints block (align with hightouch) (by @atlan-ci in [ed41b52](https://github.com/atlanhq/atlan-metabase-app/commit/ed41b52))
- coerce stringified collection filters back to dict (by @atlan-ci in [3debad4](https://github.com/atlanhq/atlan-metabase-app/commit/3debad4))
- pin lineage-publish connection-cache flags to false (by @atlan-ci in [2f2dc3f](https://github.com/atlanhq/atlan-metabase-app/commit/2f2dc3f))
- emit BIProcess lineage with name and Atlas refs (by @atlan-ci in [a64b045](https://github.com/atlanhq/atlan-metabase-app/commit/a64b045))
- declare connection_qualified_name on ProcessInput (by @atlan-ci in [799784d](https://github.com/atlanhq/atlan-metabase-app/commit/799784d))
- move extract_lineage file I/O into a @task (by @atlan-ci in [41cbf83](https://github.com/atlanhq/atlan-metabase-app/commit/41cbf83))
- download QI prefix to local before reading (by @atlan-ci in [41501d9](https://github.com/atlanhq/atlan-metabase-app/commit/41501d9))
- per-query vendor routing + metabaseQueryType extraction (by @atlan-ci in [e673908](https://github.com/atlanhq/atlan-metabase-app/commit/e673908))
- drop MetabaseQuestion.metabase_dashboards (publish hazard) (by @atlan-ci in [5877ee2](https://github.com/atlanhq/atlan-metabase-app/commit/5877ee2))
- read current QI output shape (sql/gudusoft/extra) (by @atlan-ci in [54317a4](https://github.com/atlanhq/atlan-metabase-app/commit/54317a4))
- emit relationshipAttributes on Process/ColumnProcess (by @atlan-ci in [51c4d94](https://github.com/atlanhq/atlan-metabase-app/commit/51c4d94))
- resolve Gudusoft id-based column refs (ColumnProcess empty fix) (by @atlan-ci in [89cb732](https://github.com/atlanhq/atlan-metabase-app/commit/89cb732))
- emit ARS 2.0 records directly (replaces ARS 1.0 producer shape) (by @atlan-ci in [b556d2b](https://github.com/atlanhq/atlan-metabase-app/commit/b556d2b))
- match mysql-app's SDK env-var + execution-id-prefix pattern (by @atlan-ci in [0b44a66](https://github.com/atlanhq/atlan-metabase-app/commit/0b44a66))
- tighten ARS 2.0 contract after publish-app source audit (by @atlan-ci in [af494fd](https://github.com/atlanhq/atlan-metabase-app/commit/af494fd))
- pass entry_point — MetabaseApp is multi-entry-point (by @atlan-ci in [52fb1ea](https://github.com/atlanhq/atlan-metabase-app/commit/52fb1ea))
- use flat username/password keys in inline credentials (by @atlan-ci in [372c07d](https://github.com/atlanhq/atlan-metabase-app/commit/372c07d))
- write Process/ColumnProcess under resolvable/ subdir (by @atlan-ci in [39fac42](https://github.com/atlanhq/atlan-metabase-app/commit/39fac42))
- drop relationshipAttributes mirror + propagate dbvendor (by @atlan-ci in [6a0ce0b](https://github.com/atlanhq/atlan-metabase-app/commit/6a0ce0b))
- drop-on-miss for upstream refs (no Partial synthesis) (by @atlan-ci in [4e1a8c3](https://github.com/atlanhq/atlan-metabase-app/commit/4e1a8c3))
- add app.yaml at repo root so SDR action's configurator step works (#51) (by @atlan-ci in [973a40e](https://github.com/atlanhq/atlan-metabase-app/commit/973a40e))
- remove obsolete apk add git to resolve CVE-2026-6846 (binutils) (#50) (by @mothership-ai[bot] in [76814e5](https://github.com/atlanhq/atlan-metabase-app/commit/76814e5))
- re-land orphaned PR #55 + unique credential name per attempt + pytest timeout bump (#56) (by @atlan-ci in [60e1776](https://github.com/atlanhq/atlan-metabase-app/commit/60e1776))
- route extract-lineage to SDR queue via agent_json (mirror extract) (#58) (by @atlan-ci in [f8fc94e](https://github.com/atlanhq/atlan-metabase-app/commit/f8fc94e))
- re-apply PR #63 compose env-var change reverted by force-push (#64) (by @vaibhavatlan in [0373be9](https://github.com/atlanhq/atlan-metabase-app/commit/0373be9))
- add merge_group trigger to pre-commit workflow (#75) (by @cmgrote in [6700154](https://github.com/atlanhq/atlan-metabase-app/commit/6700154))


All notable changes to the Atlan Metabase App will be documented in this file.

This file is maintained automatically by the SDK's `release-version-bump`
workflow — entries are generated from conventional commit messages
(`feat:`, `fix:`, `chore:`, etc.) on each merge to `main`. Do not edit by
hand; the next release PR will overwrite manual edits.

## 1.0.0 (2026-06-03)

Initial GA release. v3 connector built on the asset-mapper pipeline:

- Extract: collections, dashboards, databases, questions (typed dataclass
  records → JSONL via `app/extracts/`).
- Transform: `app/asset_mapper.py` maps records to `pyatlan_v9` typed
  asset instances; lineage built by `app/lineage/ars_builder.py` from
  Query-Intelligence parsed-SQL output.
- Publish: standard `publish-app` activity carries the JSONL bundle to
  Atlas.
