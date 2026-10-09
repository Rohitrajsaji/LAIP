# LAIP step 7 — supplied inventory, dependencies and application membership

Implemented for [execution-plan step 7](plans/laip-execution.md#step-7). Internal service interfaces build conservative graph reports from durable offline imports. Public inventory screens and automatic worker dispatch remain later workflow work. Application modernization and code translation are outside scope.

## Graph and evidence boundaries

`build_inventory` consumes the accepted `PreparedImport` and a distinct analysis run ID. System, source-member and explicitly supplied compiled-object identities remain namespace-qualified. Manifest source/object associations become `SOURCE_OF`; filenames never establish compiled objects. Supplied application declarations become observed `MEMBER_OF` edges with confirmed membership evidence. Static reachability produces separate inferred membership evidence, allows shared objects and never promotes a proposal to analyst verification. Existing dependency reviews retain corrections as immutable review history.

The versioned normalized metadata profile describes supplied entities and references across inventory/schema/binding/job/DSPPGMREF envelopes. Opaque records outside the profile retain evidence and accounting without invented semantics. Qualified static references resolve only within supplied inventory and library context. A library list is scope; multiple matching objects remain ambiguous unless the record explicitly supplies an ordered list. Dynamic expressions remain dynamic even when a matching name exists. Missing, ambiguous and dynamic references have null resolved targets and evidence; ambiguous edges retain candidates.

Dependencies cover calls, file reads/writes/updates/deletes, service programs, files, screens, jobs and generic dependencies through the canonical relationship vocabulary. Source language parsing remains the next analyzer steps; this step does not infer language semantics from raw RPGLE text. Observed source associations describe supplied mapping declarations and do not prove runtime behavior.

Every imported occurrence has report accounting, including supplied bytes, missing declarations, exclusions, unsupported encodings and uninterpreted metadata. Completeness is explicitly limited to supplied material. Bounds apply to entities, dependencies, evidence, metadata records, work and inferred paths; cancellation and deadline checkpoints fail instead of returning a truncated complete graph.

## Durable publication

`register_inventory` requires a sealed or partial import and compares the immutable manifest/input-plan basis before freezing an analysis run. `persist_inventory` performs database work only inside a durable job checkpoint. It checks current-report parent/end-point/evidence closure and publishes entity observations, attached input artifacts, analysis-run evidence, dependencies and immutable accounting atomically. Replaying one run does not duplicate records; a later run keeps stable entity IDs and separate provenance/review history.

Migration 009 adds immutable inventory reports with composite run/import/namespace ownership, configuration/stage validation against the frozen run, and a namespace/import index. Runtime privileges are provisioned by the existing bootstrap. Applied migrations 001–008 are unchanged.

## Validation

- Tests initially failed because the new inventory and publisher modules did not exist.
- Database review approved after ownership and parent-closure regressions were corrected.
- Docker integration passes an enriched retained source/metadata fixture, including dynamic and ambiguous targets, confirmed/inferred membership, analyst rejection, fenced checkpoint replay, repeat analysis, immutable reports and frozen basis enforcement.
- Final code review approved with zero remaining blockers. Its performance finding was corrected with checked linear membership-support indexing; 27 focused tests passed independently, including Docker publication regressions.
- Full backend suite: **212 passed**, **93.55%** combined statement/branch coverage. Ruff, formatting and strict mypy passed; frontend type checks and all three tests passed. The frontend production build passed. `make inventory-check` passed all 27 focused tests. `make restart-check` restarted the isolated Docker database and retained history, stable entities and a unique checkpoint.
- Docker rebuilt successfully, upgraded the existing application through migration 009, and passed four-service startup/BFF smoke at `http://127.0.0.1:3030/`. Private service/storage boundaries, loopback-only frontend publication and AI disabled were verified.
- [Normalized metadata profile](contracts/0.1.0/inventory-metadata.md) exists and documents supported identity shapes, exact/ordered scope, opaque records and resource ceilings.

## Repeatable checks

`make inventory-check` runs the inventory resolver and Docker-backed publication tests. `make check` runs all locked backend/frontend checks. `make restart-check` verifies existing durable history across an isolated Docker database restart. The application is deployed with loopback-only frontend access and AI disabled. The isolated test stack was stopped after verification; the localhost application remains running.

- [Pure inventory and discovery](../backend/src/laip/inventory.py)
- [Database publication](../backend/src/laip/inventory_store.py)
- [Normalized metadata profile](contracts/0.1.0/inventory-metadata.md)
- [Resolver tests](../backend/tests/test_inventory.py)
- [Publication regression tests](../backend/tests/test_inventory_store.py)
