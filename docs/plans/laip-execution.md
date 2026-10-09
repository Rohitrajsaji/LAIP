# LAIP Execution Plan

Derived from the LAIP architecture and offline MVP plan agreed in this chat on 2026-10-07. The broad implementation section is decomposed into smaller sequential steps without expanding the initial delivery scope.

## Delivery agreement

- First complete the REA assessment, architecture specification, canonical schemas, and phased backlog; then implement the offline MVP.
- Initial environment: one organization, one local user, localhost deployment, synthetic fixtures, and no live IBM i access.
- Stack: Next.js, TypeScript, Tailwind, Python, FastAPI, Pydantic, PostgreSQL, pgvector, and Docker Compose.
- Initial workflow: import, discover, analyze, review business rules, retrieve evidence, and export linked knowledge.
- Live IBM i connections, enterprise identity/RBAC, and wider language/platform support are later milestones. Steps 16–19 produce roadmap/design documents for these milestones; they do not implement them.
- Out of scope: Application modernization and code translation.
- AI and semantic embeddings are disabled until an approved private endpoint is configured. No external AI or automatic model downloads.
- No arbitrary enterprise commands, imported-source execution, repository hooks, or production-object changes.
- Treat code, comments, documentation, and metadata as untrusted content.
- Keep the initial release explicitly labeled a local prototype. Synthetic tests do not establish live IBM i compatibility or enterprise readiness.

## Shared architectural requirements

Use a modular backend and a separate worker process with durable PostgreSQL jobs. No extra broker or graph database initially. Store source artifacts and exports through a private, content-addressed storage interface backed by local volumes; retain an S3-compatible extension seam.

Providers declare identity, version, supported operations, platform/language coverage, limitations, and effects. Connectors expose describe/probe/collect. Analyzers expose supports/analyze and return declarations, control/data-flow IR, dependencies, source locations, and diagnostics. Expected errors distinguish missing source, unsupported functionality, insufficient authority, unresolved references, cancellation, and execution failure.

The canonical model includes System, Application, Program, Procedure, Module, Service Program, Database, Table, Field, Screen, Job, Business Rule, Workflow, Dependency, Evidence, and Analysis Run. Stable identities use system namespace, qualified identity, and entity kind. Source members and compiled objects have distinct identities; preserve original names and SQL/system aliases.

Evidence carries artifact identity and hash, source location, collection method/time, analyzer/provider version, excerpt or metadata, and supporting links. Classification (observed/inferred/unresolved) is separate from review status (pending_review/verified/rejected). Static references never prove runtime execution. Preserve unknown targets, ambiguity, contradictions, missing source, partial collection, and analyzer limitations.

Version schemas and generated TypeScript types. REST covers imports, runs, inventory, applications, dependencies, evidence, rules/reviews, retrieval, and exports. Long jobs return run identifiers, progress, and cancellation. Read-only MCP exposes the same retrieval services through composable analyst operations.

Knowledge exports use schema version 0.1.0 and linked JSON, JSONL, Markdown, graph JSON, Mermaid, rule catalogs, workflow documents, evidence indexes, and context chunks. Export manifests retain hashes, versions, run IDs, and limitations; full source requires an explicit export option.

<a id="step-1"></a>

## Step 1 — Assess REA and record integration decisions

Intent: Evaluate the pinned REA source and document architecture, source import, CLI/MCP integration, evidence semantics, dependency licensing, reusable components, and IBM i gaps before implementation.

Inspect commit bc2cd8b874e115eee446860043758a80bd583ad0. Keep LAIP separate and use a dedicated REA adapter. Initial reuse is the CLI reference-source inventory/hashing capability; MCP is reserved for compatible operations. Preserve upstream evidence records and identifiers alongside LAIP provenance. Do not install binary-analysis engines. Evaluate rpgleparser and syntax-highlighting alternatives before documenting the bounded grammar-based analyzer decision.

Acceptance: A source-backed REA assessment and integration decision exist; dependency/notices inventory is recorded; absent IBM i analysis support is explicit.

<a id="step-2"></a>

## Step 2 — Specify platform architecture and canonical contracts

Intent: Design the provider boundaries, knowledge/evidence schemas, processing pipeline, APIs, persistence responsibilities, and local deployment model.

Produce the architecture specification and a backlog mapping the original requirements to milestones. Include identity rules, source/object associations, supported capability reporting, graph resolution, immutable evidence/review history, uncertainty, masking, retention, and token-aware context contracts. Generate versioned schema examples for synthetic fixtures; do not implement runtime services during this document gate.

Acceptance: All major components have defined interfaces; representative schema examples distinguish classification from review status; the backlog separates MVP acceptance from later live and enterprise validation.

<a id="step-3"></a>

## Step 3 — Scaffold the local application and worker runtime

Intent: Implement the Python API, worker, TypeScript frontend skeleton, environment validation, and local Compose topology.

Use PostgreSQL-backed durable work with progress/cancellation and recovery seams. Compose runs frontend, API, worker, and database with private artifact volumes. Publish services only on loopback. Establish dependency locks, frontend/backend checks, health checks, and CI entrypoints without automatic AI or enterprise connections.

Acceptance: Compose configuration validates; local health/startup smoke checks pass; locked dependencies and repeatable check entrypoints exist.

<a id="step-4"></a>

## Step 4 — Implement PostgreSQL persistence and artifact storage

Intent: Implement migrations and repositories for entities, relationships, artifacts, evidence, runs, durable jobs, rule reviews, and search indexes.

Enforce namespace-aware identities and evidence linkage. Preserve immutable evidence and review revisions; repeated analysis must not duplicate entities. Add pgvector support, keyword indexes, and a private content-addressed storage interface. Workers claim jobs safely and expose interrupted/partial outcomes instead of silently treating them as successful.

Acceptance: Migrations pass on a clean database; duplicate names across libraries remain distinct; restart/repeat-analysis integration tests preserve history and avoid duplicates.

<a id="step-5"></a>

## Step 5 — Implement the pinned REA adapter

Intent: Implement a dedicated boundary for REA reference-source inventory/hashing and evidence import without coupling IBM i analyzers to REA internals.

Use a pinned executable and allowlisted CLI operations, structured arguments, bounded lifecycle, version checks, cancellation, and validated output. Preserve upstream records/IDs and map additional LAIP provenance separately. Surface unavailable or incompatible REA explicitly. Do not invoke setup or run imported application code.

Acceptance: A real pinned REA source-inventory fixture passes; malformed/incompatible output is rejected; original evidence payloads and semantic IDs survive import unchanged.

<a id="step-6"></a>

## Step 6 — Implement safe offline source and metadata imports

Intent: Implement ZIP uploads and configured local directory/repository-checkout imports with a versioned manifest and metadata adapters.

The manifest supplies system identity, library/object/source-member mappings, encoding, confirmed application membership, and optional inventory/schema/binding/job/DSPPGMREF exports. Retain raw bytes and decoded-to-original source mappings. Reject traversal and escaping symlinks; apply explicit exclusions, masking, and configurable resource limits. Never run Git hooks, imported commands, or remote collection.

Acceptance: Valid fixture imports retain exact bytes and source mappings; malformed manifests/traversal/symlink escapes are rejected; each artifact has an explicit processing status.

<a id="step-7"></a>

## Step 7 — Implement inventory, application discovery, and dependencies

Intent: Implement normalized inventory and graph discovery from imported source and metadata.

Map sources to objects only when the manifest or metadata establishes the association. Resolve qualified references within supplied scope/library context; preserve ambiguous and dynamic targets. Support CALLS, READS, WRITES, UPDATES, DELETES, USES_SERVICE_PROGRAM, REFERENCES_FILE, DISPLAYS_SCREEN, EXECUTES_JOB, and DEPENDS_ON. Confirm manifest membership and propose inferred reachability-based membership, allowing shared objects and corrections. Completeness refers only to supplied material.

Acceptance: Same-named objects remain isolated by namespace; application membership distinguishes confirmed/inferred; unresolved targets and every supplied artifact remain visible.

<a id="step-8"></a>

## Step 8 — Implement the bounded free-format RPGLE analyzer

Intent: Validate parser reuse, then implement bounded free-format RPGLE analysis with source-preserving IR.

Current implementation decision (2026-10-07): the user explicitly selected Python/Lark for this step, superseding the prior reuse gate below. See [parser decision](../rpgle-parser-decision.md); no upstream spike failure is claimed.

Prior parser gate from the completed REA assessment: first isolate and exercise Code for i's `vscode-rpgle` parser at commit `b357b356fbcc598de7f7e049d4f114f6b6f18408`. Record conformance, isolation, source coordinates, dependencies, and notices before choosing a minimal source adapter or TypeScript sidecar. Python/Lark is a declared-subset fallback only if that spike fails a documented requirement; the backend remains Python/FastAPI either way. Syntax recognized by a reused parser does not expand LAIP's declared semantic support automatically.

Cover declarations, procedures, subroutines, expressions, conditions, loops, assignments, calculations, calls, file operations, and monitor/error blocks. Expand available includes using declared scope/library context and retain origin mappings. Unknown statements become opaque analysis barriers with diagnostics. Build bounded control/data-flow representations and dependency evidence; do not imply full compiler semantics.

Acceptance: Fixture declarations/control flow/file operations match expected facts; nested expressions and includes preserve source spans; unsupported or missing constructs prevent unsupported rule conclusions.

<a id="step-9"></a>

## Step 9 — Implement the bounded CL and CLLE analyzer

Intent: Implement language-aware CL/CLLE tokenization, grammar parsing, control-flow IR, and dependency extraction.

Cover declarations, expressions, conditionals, loops, calls, job-submission references, file overrides, and message handling. Handle comments, quoted text, continuations, and dynamic arguments. Analyze command statements as source without executing them. Preserve unresolved targets and the effects of overrides rather than assuming runtime file identity.

Acceptance: Supported control flow/call/job/message fixtures match expected facts; continuations and strings parse correctly; dynamic targets and unsupported commands produce evidence-backed diagnostics.

<a id="step-10"></a>

## Step 10 — Implement positional DDS analysis

Intent: Implement a source-position-aware DDS reader and keyword/expression grammar for the declared subset.

Cover physical/logical-file relationships, record formats, fields, keys, display coordinates, usage, indicators, and common validation keywords. Preserve fixed columns, original spans, continuations, and unknown keywords. Link records/fields/screens to imported program/file evidence without inventing unavailable external descriptions or runtime screen behavior.

Acceptance: Physical/logical/display fixtures yield expected fields and relationships; original column locations remain correct; unsupported keywords and incomplete definitions are explicit.

<a id="step-11"></a>

## Step 11 — Implement business rules, workflows, and review history

Intent: Implement deterministic rule extraction, resolved-call workflow composition, and human review/correction.

Store conditions, actions, calculations, validation candidates, programs/procedures, supporting evidence, uncertainty, and interpretation provenance. Compose cross-program paths only through resolved links and classify composed conclusions as inferred. Reviews approve/reject/correct interpretations with immutable revision history. Reanalysis that changes supporting evidence invalidates affected approvals.

Acceptance: Expected fixture rules cite valid evidence; corrections preserve previous revisions; changed evidence requires review again and unresolved paths cannot become verified behavior automatically.

<a id="step-12"></a>

## Step 12 — Implement hybrid retrieval and optional private AI

Intent: Implement exact lookup, keyword search, dependency traversal, evidence retrieval, optional pgvector semantics, and token-aware context assembly.

Use a configurable approved private embedding/LLM endpoint with AI disabled by default. Deduplicate results and select linked evidence within a configured token budget. Without AI, return deterministic summaries and evidence. Separate extracted facts from model interpretations; validate structured outputs/citation IDs and prevent retrieved content from changing tool permissions. Unknown citations or unsupported claims prevent publication.

Acceptance: Expected exact/keyword/graph results are retrieved; context stays within budget and private semantic retrieval has a separate integration test; injection text and unknown citation IDs cannot produce published claims.

<a id="step-13"></a>

## Step 13 — Implement linked exports and read-only MCP access

Intent: Implement versioned knowledge export and read-only MCP tools backed by shared retrieval services.

Generate manifest, system/application/program/database/rule/workflow documents, graph JSON/Mermaid, evidence indexes, and linked JSONL context chunks. Export hashes, limitations, identifiers, and run/provider versions. Full source requires an explicit option. MCP supports exact entity lookup, search, graph traversal, evidence, and context retrieval without arbitrary commands or mutation tools. Use safe filenames and escape source-derived Mermaid/Markdown content.

Acceptance: Export schemas/checksums/links validate; source inclusion follows its explicit option; MCP fixture retrieval matches the API and exposes no mutation/command execution.

<a id="step-14"></a>

## Step 14 — Implement the analyst web workflow

Intent: Implement the Next.js/TypeScript/Tailwind interface for the complete offline workflow.

Provide import/run progress and cancellation, inventory/application views, dependency browsing, source/evidence inspection, business-rule corrections, retrieval with citations, and export downloads. Use generated backend types, accessible controls, and useful empty/error/partial states. Surface uncertainty and review state consistently; avoid claiming synthetic fixtures are real enterprise applications.

Acceptance: Browser tests complete import through export; corrected rules and evidence links remain inspectable; partial failures, cancellation, and disabled AI states are clear and accessible.

Implementation and Docker HTTP validation: [step 14 report](../laip-step-14-implementation.md). Interactive import-through-export acceptance completed during step 15 using the parent agent's in-app browser; see [step 14 browser record](../laip-step-14-browser-validation.md) and [step 15 observations](../laip-step-15-browser-validation.md).

<a id="step-15"></a>

## Step 15 — Validate MVP acceptance and document local operation

Intent: Test the complete synthetic customer-validation application and document reproducible startup, scope, and limitations.

The fixture includes a CL entry point, RPG procedures, DDS physical/logical/display files, metadata exports, a missing include, same-named objects in different libraries, and a dynamic call. Validate evidence lineage, reviews, retrieval, exports, repeated analysis, cancellation, worker recovery, and migrations. Add malicious archive/prompt/citation scenarios. Record real REA adapter verification and any unavailable test infrastructure truthfully.

Acceptance: Full offline acceptance passes with synthetic limitations visible; security/recovery regressions pass; local startup/check instructions and a truthful validation report exist.

Offline MVP acceptance validated: [step 15 report](../laip-step-15-validation.md) records synthetic workflow, security, actual database restart and pinned REA checks; [browser record](../laip-step-15-browser-validation.md) records the actual interactive journey. Live IBM i compatibility and screen-reader validation remain unclaimed.

<a id="step-16"></a>

## Step 16 — Plan live IBM i discovery and validation

Intent: Design the next read-only connector milestone and its real-environment validation protocol using official IBM documentation.

Specify JDBC through IBM Toolbox for Java, scoped SQL services/catalogs, authorized source exports/reads, capability probing by release/PTF, least-privilege authority, timeouts, and partial collection. Investigate OBJECT_STATISTICS, PROGRAM_INFO, BOUND_MODULE_INFO, binding catalogs, SSH/SFTP, and system APIs. Consume existing DSPPGMREF exports: outfile mode writes a database file and references may be historical. The deliverable is documentation/backlog, not a live connector.

Acceptance: An official-source compatibility/authority matrix exists; operations and effects are documented; a validation checklist identifies the access/release/libraries needed before live support claims.

Documentation milestone completed (2026-10-08): [step 16 connector design](../laip-step-16-connector-milestone.md) records the official availability/authority/effects matrix, access/library worksheet, implementation backlog and an entirely UNTESTED live-validation ledger. No live connector, connection or compatibility certification is delivered by this step.

<a id="step-17"></a>

## Step 17 — Plan expanded IBM i language and analysis coverage

Intent: Produce a staged P1 analyzer roadmap from the observed MVP limitations and parser evaluation.

Plan SQLRPGLE, fixed-format RPG variants/RPG III, COBOL, richer binding relationships, field lineage, and cross-program rules. Identify grammar reuse candidates, license/version constraints, dialect-specific test corpora, metadata prerequisites, and measurable unsupported-feature reductions. Preserve the common IR/provider contracts. Produce documentation/backlog only; do not implement new language providers in this step.

Acceptance: Each language/analysis extension has explicit coverage and fixtures; parser reuse choices are source-backed; missing real-source validation is an explicit prerequisite.

Documentation milestone completed (2026-10-08): [step 17 language roadmap](../laip-step-17-language-roadmap.md) records staged SQLRPGLE, fixed RPG/RPG III, COBOL, binding, field-lineage and cross-program rule extensions, with parser/license decisions and corpus/metadata/conformance gates. Existing Python/Lark free-format coverage remains bounded; broader analyzers and real-source validation remain outstanding.

<a id="step-18"></a>

## Step 18 — Plan enterprise security and deployment

Intent: Design the enterprise deployment milestone while retaining the single-user localhost MVP boundary.

Specify OIDC, role/source-access enforcement, encrypted connector credentials and secret-provider integration, audit policy, approved private AI authorization, S3-compatible storage, retention administration, and Kubernetes-compatible deployment. Document compatibility/migrations from the local prototype and required security/operational acceptance tests. Produce the roadmap/design only; do not expose the prototype as an enterprise service.

Acceptance: Identity/authorization/secrets/storage/retention requirements are specified; migration and operational gates are listed; enterprise readiness is conditional on implementation and validation.

Documentation milestone completed (2026-10-08): [step 18 enterprise design](../laip-step-18-enterprise-milestone.md) specifies identity/source authorization, secrets, audit, private AI, S3 retention, Kubernetes operations and ENT-01–08 migration/release gates. Enterprise controls remain unimplemented/unvalidated; the prototype remains localhost. The planner completed; delegated architect/reviewer availability and the primary-agent checks are recorded in the document.

<a id="step-19"></a>

## Step 19 — Plan additional legacy-platform providers

Intent: Design the provider-extension roadmap for z/OS, COBOL/CICS/JCL, Oracle Forms, PowerBuilder, and later technologies.

Document how connector/analyzer capability declarations, namespace-aware identity, evidence, IR, retrieval, and knowledge exports support other platforms. Identify platform-specific artifacts, parser/metadata candidates, authority boundaries, unsupported operations, and conformance fixtures. Produce documentation/backlog only; do not invent IBM i-equivalent behavior for other platforms.

Acceptance: Each target has an explicit provider capability proposal; shared contracts remain independent of IBM i; prerequisites and conformance gates precede any support claims.

Documentation milestone completed (2026-10-08): [step 19 provider roadmap](../laip-step-19-provider-extensions.md) records separate artifact/authority/format proposals, platform-owned identity profiles, schema extension decisions, parser/license evaluation prerequisites and EXT-01–08 conformance gates. All additional provider implementations and real-platform validation remain outstanding. The separate step 17 design is linked above.

The [delivery-status ledger](../laip-delivery-status.md) distinguishes bounded offline delivery, completed follow-up designs and the implementation/input/validation work remaining. Completing steps 16–19 does not provide their runtime capabilities.

## Reference provenance

- REA architecture: https://github.com/morluto/rea/blob/bc2cd8b874e115eee446860043758a80bd583ad0/docs/architecture.mermaid
- REA source import contracts: https://github.com/morluto/rea/blob/bc2cd8b874e115eee446860043758a80bd583ad0/src/application/ReferenceSourceImportTypes.ts
- REA provider contracts: https://github.com/morluto/rea/blob/bc2cd8b874e115eee446860043758a80bd583ad0/src/application/AnalysisProvider.ts
- REA evidence: https://github.com/morluto/rea/blob/bc2cd8b874e115eee446860043758a80bd583ad0/src/domain/evidence.ts
- REA license: https://github.com/morluto/rea/blob/bc2cd8b874e115eee446860043758a80bd583ad0/LICENSE
- RPG parser assessed at commit 1cd596ded2edff26f7f82639d5ca7ee51ebbfeb9: https://github.com/rpgleparser/rpgleparser
- IBM DSPPGMREF: https://www.ibm.com/docs/en/i/7.5.0?topic=d-display-program-references
