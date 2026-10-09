# ADR 0010: additive banking extraction contracts

Date: 2026-10-08. Status: architecture accepted by the orchestrator on 2026-10-08. Step-1 baseline review passed before migration testing began; see the [validation ledger — private material withheld](../private-validation.md). Durable pipeline publication still requires the later parser/publication acceptance gates. Scope: [banking fix step 2 — private material withheld](../private-validation.md). Application modernization and code translation are excluded.

## Current authored contracts

The authored public contracts are JSON Schema, specifically `docs/contracts/0.1.0/laip.schema.json` and its packaged mirror `backend/src/laip/contracts/laip.schema.json`, plus export/read-tool schemas. `persistence.validate` currently selects one packaged schema, not a Pydantic-generated canonical model. `read_service` has Pydantic request/envelope types, but these do not author Entity or RuleRevision. Locate each generation script before regenerating tool definitions or TypeScript projections; do not edit presumed generated models blindly.

`canonical.identity_id` hashes only the supplied namespace/kind/qualified identity. Existing entity kinds omit RecordFormat. RuleRevision requires at least one Program and repositories persist Application/Program/Procedure scopes. Existing entity tables accept text kinds and immutable observation payloads; existing revision/review hashes include their original payload. Changing old payload versions or kinds would alter revision hashes and break historical packages.

## Decision

Keep 0.1.0 schemas and their historical records unchanged. Add separately authored, strict 0.2.0 schemas and packaged mirrors, chosen by each record's declared `schema_version`; reject missing/unknown versions with a named compatibility error. A versioned response/package may contain original 0.1.0 records and 0.2.0 additions, validated independently under their original version. Never relabel historical records to match an envelope version.

New banking observations/revisions use 0.2.0. Reuse an existing canonical identity without changing its ID or core row; append observations. A newly derived entity uses the existing identity hash function without a schema-version salt. No migration rewrites old IDs, parent identities, evidence, reviews, snapshots or package files. Historical rules with a new claim-support basis receive a new revision linked through `previous_revision_id`; old approvals remain historical, and cannot apply to the new revision automatically.

### Qualified identity and ownership

Add the cross-platform **RecordFormat** entity kind. It denotes a declared record layout, not a runtime file instance, procedure or program. IBM i identities use these bounded profiles:

| Kind | Qualified segments | Owner |
| --- | --- | --- |
| RecordFormat | library, `*FILE`, file name, format name | actual supplied Table or display-file owner |
| Field | library, `*FILE`, file name, format name, field name | RecordFormat for newly published fields |
| Procedure | library, `*PGM`, program name, declaration scope, procedure name | supplied Program |

Existing Field/Procedure identities are preserved if already present; migration does not insert a scope segment into an old tuple. Profile differences are explicit producer metadata, not silent aliases. Case normalization follows the analyzer's declared RPG/DDS profile, retaining originals. Namespace remains part of identity; same-named objects in different libraries/files/formats/scopes cannot collide. A lexical scope ID must be declaration-based and stable under whitespace changes, with duplicate declarations diagnosed rather than numbered by discovery order.

Reuse existing `CONTAINS`, `CALLS`, `READS`, `UPDATES`, `WRITES`, `REFERENCES_FILE`, `SOURCE_OF` relations. RecordFormat→Field and file→RecordFormat use CONTAINS. For a synthetic example, UPDATE DEMO_FORMAT binds through supplied format ownership to DEMO_FILE; keep both syntactic name and resolution basis. Do not add arbitrary edge enums for DDS keywords. Format↔file ownership is supported by declaration evidence; name matching alone cannot establish foreign keys or screen/program linkage.

### Generic rule subjects

0.2.0 RuleRevision retains legacy scope arrays, allowing an empty `program_entity_ids`, and adds mandatory nonempty `subject_entity_ids`. Eligible kinds are SourceMember, RecordFormat, Field, Screen, Program and Procedure. Application is context, not a sole executable/validation subject. Validate actual subject kind, authorized namespace, evidence ownership and run/snapshot closure in the repository. A field VALUES validation attaches to the field and/or containing format with its original DDS span; no fabricated Program is created.

Add mandatory `claim_support` with a strict versioned structure: state (`supported_within_profile`, `conditional`, `blocked`, `unresolved`), profile ID/version, subject IDs, controlling predicates, symbol/effect dependencies, supporting evidence IDs, resolution decisions, barriers, and limitations. Conditions/actions remain source-cited Expression records. A recorded rule is not a verified workflow. Existing classification certainty and review status remain independent; support state must never be converted into an analyst approval.

0.1.0 approval validation stays on its original path. 0.2.0 approval additionally requires `supported_within_profile`, closed cited evidence, resolved dependent references, supported subjects and no blocking slice barriers. Conditional/blocked/unresolved candidates remain inspectable and cannot be verified by a direct repository call. Corrections revalidate support, keep immutable revisions and require a fresh approval. A source-only rule can be reviewable without a Program when its actual subject and support are sufficient.

### Internal analyzer facts and claim support

`analysis_facts.py` owns strict bounded immutable data validation, not source execution. A bundle contains `fact_schema_version`, namespace, run/artifact/provider/profile basis, declarations/symbol scopes, callsites, reference candidates, typed statement effects, diagnostics, and accounting. Each fact has a stable content ID, explicit source/evidence spans and original names; syntactic references and resolved references are separate. A reference with ambiguous/dynamic/missing resolution retains null target and candidate evidence. Both callsites survive even when graph relationships aggregate them.

`claim_support.py` owns strict claim-support validation and fingerprint computation. Unknown effects are explicit barriers; they are never encoded as no-ops. Structured control/data/effect dependence is computed by the analyzer in later steps, not invented in this contract step. Clock values are nondeterministic symbolic effects; `%FOUND` depends on a resolved file-status producer. Coverage records include supplied, analyzed, supported, unsupported, unresolved and failed accounting with bounded reasons and source ranges; counts do not imply compiler completeness.

A 0.2.0 support fingerprint hashes a version-tagged canonical basis containing evidence IDs/content hashes, providers/profile/configuration, subjects, controlling predicates, symbol/effect dependencies, resolution decisions and barriers. The repository recomputes it from closed persisted support rather than accepting a caller's claimed digest. A changed subject binding, intrinsic profile or branch guard invalidates approval even when the source byte hash stays unchanged. Preserve the exact old 0.1.0 support formula for historical replays.

## Small additive persistence migration

Add migration 012 after the current 011, without editing prior SQL. Add immutable `analyzer_fact_bundles` keyed by bundle ID with `(run_id,system_namespace)` foreign key and bounded versioned payload; add immutable `rule_subjects` keyed by `(revision_id,entity_id)` with namespace-qualified foreign keys to existing revisions/entities. Add namespace indexes and rejection triggers consistent with existing immutable history. Subject kind eligibility is validated transactionally by the repository. Claim support is part of the new immutable RuleRevision payload and hash, avoiding a second mutable approval authority.

Existing `rule_scopes` continue serving legacy application/program/procedure indexes. 0.2.0 writes those arrays as supplied plus generic subjects; historical revisions need no backfill. Publish facts/subjects/evidence in the existing fenced durable-job transaction. Same bundle/revision retries reuse exact content; conflicts fail and roll back. Core entities have no kind enum CHECK requiring a destructive rewrite; keep the schema validator as the kind gate.

Rollback means stop new 0.2.0 writes and deploy a reader capable of both versions; do not run old binaries against mixed new data or drop populated immutable tables. Test empty-database migration and upgrade of a populated 0.1.0 database, plus failed publication rollback and restart replay. Preserve old table counts/hashes and record identities. No destructive production downgrade is promised.

## Consumer compatibility gate

Expose supported versions through an explicit capabilities response and validate requested response/package version before traversing or publishing results. Requests lacking a negotiated version retain their current 0.1.0 behavior only for representable 0.1.0 snapshots. A mixed/new snapshot requested as 0.1.0 fails clearly; do not silently omit formats/subjects or fabricate legacy Program scopes. Existing historical snapshots remain retrievable/exportable unchanged. Unknown versions fail at API, MCP and export boundaries consistently.

Step 2 implements validation/compatibility primitives and versioned example records; step 7 completes live retrieval/export/UI projections and negotiation. Until those consumers support a complete 0.2.0 snapshot, reject its outward publication rather than returning a mislabeled 0.1.0 envelope. Source policy and AI-off defaults remain unchanged.

## Implementation handoff and acceptance

1. Author 0.2.0 JSON Schema/examples, package schema registry and strict analysis-fact/claim-support validators; test missing/unknown version, extra keys, bounds, invalid subject kinds and inconsistent resolution.
2. Add migration 012 and repository bundle/subject methods; branch rule persistence and fingerprint validation by version, retain 0.1.0 behavior verbatim. Test namespace violations, stale heads, immutable replay and no duplicate facts.
3. Add round-trip examples: old program rule unchanged; source-only DDS field VALUES rule with empty program scope; new RecordFormat with cited ownership; unresolved reference; blocked claim rejected for approval.
4. Add compatibility tests proving old IDs, revision/review hashes and historical 0.1.0 ZIP bytes remain valid. A mixed snapshot cannot be exported as 0.1.0, and an unsupported client cannot read it silently.

This design adds two versioned schemas and two bounded data contracts instead of replacing the parser stack or databases. The cost is explicit dual-version handling and conservative publication rejection until consumer support lands. Alternatives rejected: weakening 0.1.0 validation silently, encoding formats as Programs, mutable support metadata outside revision hashes, or translating DDS source to obtain intended behavior.

## Implemented contract gate

The reviewed contract stage now provides `schema_registry.validate_record`, `require_compatible_version`, `analysis_facts.validate_fact_bundle`, `claim_support.validate_claim_support`, `Repository.put_fact_bundle`, and `Repository.rule_support_fingerprint`. Migration 012 adds immutable fact bundles and rule subjects with namespace-qualified foreign keys and lookup indexes. It has been validated against empty isolated schemas and a populated legacy schema. Prototype data was not migrated by this stage.

Persisted fact IDs are `record_id("fact_", fact, "fact_id")`; bundle IDs are content-derived. Rule resolution decisions reference a persisted reference fact's ID and must match its stored resolution and target. Symbol/effect dependencies likewise reference persisted facts of the matching kind. The repository includes their contents, cited evidence closure, subject observations, provider/configuration and profile/slice data in the fingerprint; callers cannot relabel a dynamic reference or omit a dependent unknown effect to obtain approval. `claim_support.support_fingerprint` is a support-slice utility, not the persisted RuleRevision fingerprint; producers must use the repository's closed-basis method.

Repository review and service approval both enforce supported claim state, explicit barriers, dependent unknown effects, resolved source support and source span closure. Corrections append immutable revisions and require fresh review. Legacy 0.1.0 hashes, IDs, review payloads, scope behavior and export schemas are preserved. Public 0.2.0 consumer negotiation remains the step-7 gate; these schema examples are structural examples, not corpus extraction results.
