# LAIP requirements traceability and delivery backlog

**Specification date:** 2026-10-07. **Updated:** 2026-10-08. **Status:** offline MVP validated within its [step 15 limits](laip-step-15-validation.md); [LIVE connector design](laip-step-16-connector-milestone.md), [LANGUAGE roadmap](laip-step-17-language-roadmap.md), [ENTERPRISE design](laip-step-18-enterprise-milestone.md) and [PLATFORM proposals](laip-step-19-provider-extensions.md) documented. Their implementation and real-environment validation remain outstanding; see the [delivery-status ledger](laip-delivery-status.md).

This backlog maps the original pasted requirements to the [execution plan](plans/laip-execution.md). The original attachment ends abruptly in phase 1 at `Deliverables: - RE`; this document covers every substantive requirement present before that truncation and does not invent missing later-phase deliverables. The [REA assessment](rea-assessment.md) supplies the reuse and parser decisions. The current delivery agreement is an offline, one-organization, one-user localhost prototype using synthetic fixtures. Modernization, code translation, migration, and replacement application generation are outside every milestone below.

## Coverage vocabulary and milestone boundaries

- **MVP-bounded:** implementation target in steps 3–15 for an explicit fixture-backed subset; unsupported input remains visible. Coverage is bounded by the [step 15 validation record](laip-step-15-validation.md); individual requirement rows are scope allocations, not blanket support certifications.
- **MVP-contract:** canonical records/interfaces exist in the initial design, but population or behavior depends on source/metadata actually supplied. A schema field is not implemented platform support.
- **Conditional:** available only after its named configuration, authorization, validation, or parser decision gate.
- **Later:** not delivered by the offline MVP; steps 16–19 produce designs/backlogs for a subsequent implementation milestone.
- **Unsupported/out of scope:** report explicitly; do not silently claim behavior or substitute an LLM explanation.

| Milestone | Plan step | Deliverable and boundary | Implementation prerequisites |
| --- | --- | --- | --- |
| Offline MVP | 3–15 | Import → inventory → bounded analysis → review → evidence retrieval → linked export, served on localhost | Step 2 contracts approved; pinned REA adapter; declared parser subsets; synthetic acceptance corpus; local checks actually run |
| LIVE | 16 | [Completed design and official-source release/PTF/authority/effects matrix](laip-step-16-connector-milestone.md); implementation remains future work | Authorized target/release/PTF data, scoped libraries, verified service availability and least-privilege account; real-environment validation before any live-support claim |
| LANGUAGE | 17 | [Completed expanded IBM i language/semantic roadmap](laip-step-17-language-roadmap.md) | Parser reuse/licensing decisions, representative licensed dialect corpora, origin mapping, metadata/real-source validation; later implementation and conformance checks |
| ENTERPRISE | 18 | [Completed security/operations design](laip-step-18-enterprise-milestone.md), ENT-01–08 implementation and release gates | Implemented OIDC and role/source policy across all transports/jobs/derivatives; secret provider, approved private AI, audit/storage/retention, coordinated migration/restore and validated cluster compatibility before rollout |
| PLATFORM | 19 | [Completed additional provider proposals](laip-step-19-provider-extensions.md), EXT-01–08 conformance gates | Platform-specific authority, licensed source/metadata formats, identity profiles and versioned schema extensions, parser decisions, fixtures and real-environment validation; all runtime providers remain later work |

Steps 16–19 must leave an explicit implementation backlog. Completion of a roadmap step does not make its corresponding capability available. Local source confidentiality relies on host/volume access controls until ENTERPRISE implements per-user access; localhost is not enterprise RBAC.

## Architecture invariants

1. Core identity, evidence, graph, jobs, retrieval, APIs and exports contain no IBM i execution assumptions. Platform attributes live in versioned provider-specific payloads or typed qualifiers. Provider capability declarations identify platform, dialect/subset, operation, version, prerequisites, effects, coverage and limitations.
2. A system namespace, entity kind and qualified logical identity determine an entity identifier. Preserve original names and SQL/system aliases. Source artifacts/members, compiled program objects and procedures have distinct identities; associations require explicit manifest/metadata evidence.
3. Static references are evidence of a reference. They do not establish execution, current production behavior, confirmed application ownership, or completeness beyond supplied scope. Dynamic targets, ambiguity, contradictions and historical metadata must remain queryable.
4. Classification is `observed`, `inferred` or `unresolved`; review status is independently `pending_review`, `verified` or `rejected`. Human approval does not turn inference into observation. Reviews preserve immutable revisions and links to the exact supporting evidence/finding version; changed support requires a new review.
5. Evidence retains raw artifact digest/locator, collection time/method, provider/analyzer versions, source span/excerpt or metadata and supporting links. Derived results identify their input evidence/run. No orphan dependency, rule or explanation is publishable as a supported conclusion.
6. REA stays an independently versioned optional CLI inventory/hashing adapter pinned to `bc2cd8b874e115eee446860043758a80bd583ad0`; it neither retrieves RPG source text nor parses IBM i. If missing or incompatible, report that adapter explicitly unavailable while LAIP's independent controlled reader remains usable. LAIP owns secure reading and IR. Preserve upstream evidence payloads/IDs unchanged if present; importer graph output is not assumed to contain REA `Evidence` records.
7. Imports and enterprise data are untrusted, inert inputs. No hooks, source execution, arbitrary enterprise commands, automatic model downloads or production modifications. Read-only MCP calls the same application services as REST and has no job creation, import, review, deletion, credential or command mutation surface.
8. PostgreSQL owns durable jobs, entity/evidence relationships and review history. A separate worker uses claims/leases with fencing, retries and per-artifact idempotent publication, cancellation checkpoints and restart recovery; a frontend disconnect cannot cancel or erase a run. Publish committed results with completeness/limitations, never mark partial work complete silently.
9. AI and embedding calls are disabled by default. Enable only an explicitly configured approved private endpoint and data-access policy. Exact/keyword/graph/evidence retrieval works without AI; structured-output and citation checks do not by themselves prove semantic truth.
10. Linked exports use a versioned schema and bounded context budget. Evidence availability, masking, skipped results, unknowns and truncation are visible. Original source is excluded unless explicitly requested and authorized. No export implies compiler-equivalent semantics or live IBM i compatibility.

## Component and transport traceability

These rows define responsibility coverage; the architecture specification owns exact method, request/response and persistence contracts. Each named component requires a typed boundary and explicit expected errors before its implementation step starts.

| ID | Original architectural requirement | Coverage and step | Interface/acceptance prerequisite |
| --- | --- | --- | --- |
| ARC-01 | Enterprise Connector Layer | MVP-bounded offline provider, 5–6; live providers Later LIVE 16 | `describe/probe/collect` contracts; declared effects and authorized scope; unavailable/missing-source/insufficient-authority distinguished |
| ARC-02 | Legacy Application Discovery Engine | MVP-bounded, 7 | Input normalized collections; output inventory, membership, graph proposals, completeness and diagnostics; shared/ambiguous membership retained |
| ARC-03 | Static Analysis and Reverse Engineering Engine | MVP-bounded, 8–10; LANGUAGE 17 | `supports/analyze`; versioned source-preserving IR, dependencies, diagnostics; parser gates below |
| ARC-04 | Business Logic Extraction Engine | MVP-bounded, 11; richer semantics LANGUAGE 17 | Versioned conditions/actions/rule/workflow proposals cite IR/evidence and analysis barriers |
| ARC-05 | Application Knowledge Graph | MVP-bounded PostgreSQL, 4/7 | Namespace-safe entity/edge repositories; traversal depth/size and scope bounded; unresolved targets retained |
| ARC-06 | Evidence and Provenance Management | MVP-bounded, 4/6–13 | Immutable observations and derivation links, evidence lookup, revision-aware review; masked display/export separated from raw private content |
| ARC-07 | LLM Context Generation Engine | MVP-bounded deterministic, 12–13; Conditional private AI | Linked retrievable chunks, budget accounting and citation validation; emit omissions and unknowns |
| ARC-08 | Knowledge Repository | MVP-bounded, 4 | PostgreSQL transactional metadata plus private content-addressed artifact storage; repository/storage interfaces, retention and dangling-reference prevention |
| ARC-09 | Enterprise API | MVP-bounded local REST/read-only MCP, 3/13; ENTERPRISE 18 | Versioned imports/runs/inventory/applications/dependencies/evidence/rules/reviews/retrieval/exports; common error envelope/pagination; enterprise access enforcement later |
| ARC-10 | Web-Based UI | MVP-bounded, 14 | Typed local API client; import/run/cancel, inventory/graph/evidence, rule correction, retrieval and export journeys; accessible partial/error/empty states |
| ARC-11 | Modular providers and platform extensibility | MVP-contract, 2–4; PLATFORM 19 | Versioned capability/IR/source locator interfaces; no IBM i in core; unsupported operation/dialect reported |
| ARC-12 | Clear responsibilities and interfaces for all components | MVP-contract, 2 | All above plus worker/job/store/review/LLM interfaces specified; transport adapters have no duplicated business logic |

## Connect and discovery requirements

| ID | Original requirement | Coverage and step | Bounded acceptance / later gate |
| --- | --- | --- | --- |
| CON-01 | Direct enterprise system connections | Later LIVE 16 + ENTERPRISE 18 | Offline MVP has no live target; service/release/authority probe and credentials/access controls precede connection implementation |
| CON-02 | Source repository imports | MVP-bounded local configured checkout, 5–6 | Read existing directory without Git commands/hooks or network; remote repository fetch/auth belongs to later connector design |
| CON-03 | Offline source-code uploads | MVP-bounded ZIP, 6 | Versioned manifest, allowed root, encoding and resource limits; malformed/archive traversal/escaping symlink rejection; raw bytes preserved |
| CON-04 | Database metadata extraction | MVP-bounded imported catalog/schema exports, 6–7; Later LIVE 16 | Provenance and scope known; imported representation is not current live inventory |
| CON-05 | System object metadata collection | MVP-bounded supplied inventory/binding/job/reference exports, 6–7; Later LIVE 16 | Imported rows retain method/time/authority/coverage; malformed or omitted fields surfaced |
| CON-06 | Investigate JDBC, Db2 for i services, SSH/SFTP, APIs, authorized exports | Later LIVE 16; authorized export ingestion MVP 6 | IBM Toolbox/JDBC bridge proposal, allowlisted services, transfer/import policy and official syntax/release/PTF/authority matrix; no arbitrary shell surface |
| CON-07 | Least-privilege read-only discovery | MVP-bounded inert local import, 5–6; Later LIVE 16/ENTERPRISE 18 | Effects declaration and denial by default; live collection account/service permissions verified independently |
| DIS-01 | Applications and application boundaries | MVP-bounded, 7/11/14 | Manifest-confirmed membership separate from reachability-inferred membership; shared members, corrections and no-evidence cases supported |
| DIS-02 | Programs, procedures, modules, service programs | MVP-contract + bounded source/metadata population, 6–10 | Explicit object/source association and procedure identity; absent binding/compiled metadata leaves unknowns |
| DIS-03 | Database objects and fields | MVP-bounded imported metadata + declared DDS subset, 6–7/10 | Table/file/view/procedure subtype retained; fields only from supplied catalog/DDS; missing externally described fields unresolved |
| DIS-04 | Display files and screens | MVP-bounded declared DDS subset, 10 | Screen definitions/coordinates, indicators and static program linkage; no 5250 interactive execution claim |
| DIS-05 | Batch processes and jobs | MVP-bounded manifest/job exports/static CL references, 6–7/9 | Job identity, available descriptions and static submission relations; scheduling/execution time unknown without supplied metadata |
| DIS-06 | Program calls, cross-program/data dependencies | MVP-bounded, 7–10 | CALLS, READS, WRITES, UPDATES, DELETES, USES_SERVICE_PROGRAM, REFERENCES_FILE, DISPLAYS_SCREEN, EXECUTES_JOB, DEPENDS_ON; evidence on every edge; direction and method retained |
| DIS-07 | Entry points | MVP-bounded declared entry points + inferred graph candidates, 7 | Confirmed and inferred entry points distinct; incomplete caller scope is not proof of an entry point |
| DIS-08 | Complete inventory within authorized scope | MVP-bounded supplied-material completeness, 6–7/15; Later LIVE 16 | Account for every artifact/row including skipped/failed/unsupported; report scope, exclusions, denied access and `complete/partial/unknown`; no whole-system completeness claim |
| DIS-09 | Object names/types/libraries/source members/languages/descriptions | MVP-contract, 2/6–7 | Preserve raw name and qualified namespace; optional fields stay unknown rather than fabricated |
| DIS-10 | Entry points/references/dependencies/source availability/analysis status | MVP-contract + bounded extraction, 6–10 | Separate collection availability from parse/analysis outcome; metadata-only objects remain inventory-visible |
| DIS-11 | Normalized internal representation and stable namespace-aware IDs | MVP-bounded, 2/4/7 | Same object name in different libraries/systems/kinds cannot collide; repeat analysis upserts logical identity with history |
| DIS-12 | Source and collection time for every discovered object | MVP-contract, 2/4/6 | Supporting collection evidence, timestamp and run; imported export generation time separately optional; unknown upstream timestamp explicit |
| DIS-13 | Static reference is not proof of runtime execution | MVP-bounded invariant, 7–13 | Scope/method/authority label follows traversal, rule interpretation and export |
| DIS-14 | Dynamic/unresolved dependencies and graceful failures | MVP-bounded, 6–15 | Unknown target expression retained; ambiguous candidates, unavailable source, insufficient authority, unsupported types and parser failures are explicit diagnostics |

## IBM i technologies and analysis coverage

All languages remain inventory-identifiable from trusted manifest/metadata even when their semantic analyzer is unsupported. A parser may recognize more syntax than the LAIP analyzer safely lowers; only the declared analysis subset is supported.

| ID | Original requirement | Coverage and step | Bounded acceptance / later gate |
| --- | --- | --- | --- |
| IBI-01 | RPGLE / RPG IV / free-format RPG (P0) | MVP-bounded free-format RPGLE, 8; broader RPG IV LANGUAGE 17 | Parser isolation gate and source-position tests; declared free-format subset, opaque unsupported statements and missing-include barriers |
| IBI-02 | RPG III and fixed-format RPG variants (P1) | Later LANGUAGE 17 | Dialect-specific corpus/continuations/columns/indicators and semantics; Code for i syntax recognition alone is insufficient |
| IBI-03 | SQLRPGLE (P1) | Later LANGUAGE 17 | MVP diagnoses embedded SQL/opaque effects; subsequent SQL parsing, host variables, catalog resolution and transactions require own fixtures |
| IBI-04 | CL and CLLE (P0 CLLE) | MVP-bounded, 9 | Tokenization/grammar for listed commands/control forms; continuation/comments/strings/override/dynamic arguments; source statements never executed |
| IBI-05 | COBOL (P1) | Later LANGUAGE 17; z/OS COBOL PLATFORM 19 | Separate dialect/copybook/data/control-flow corpus and reuse/license gate; IBM i capability not transferred to z/OS automatically |
| IBI-06 | Db2 for i, physical/logical files and DDS definitions (P0 DDS) | MVP-bounded supplied schema + DDS subset, 6/10; LIVE 16 | Record formats/fields/keys/PF-LF relations; external descriptions only from supplied metadata; dedicated DDS parser gate |
| IBI-07 | SQL tables, views and stored procedures | MVP-bounded catalog inventory, 6–7; semantics LANGUAGE 17/LIVE 16 | Names/columns/declared catalog dependencies may populate; SQL procedure/view body analysis unsupported in MVP |
| IBI-08 | Program objects/modules/service programs/binding relationships | MVP-bounded imported object and binding metadata, 6–7 | Manifest source/object mapping and supplied binding edges; runtime binder behavior unresolved; richer binding LANGUAGE 17/LIVE 16 |
| IBI-09 | Display files and 5250 screen definitions | MVP-bounded static DDS definitions, 10/14 | Preserve positions/usage/indicators/validation; runtime screen flow inferred only from supported source paths, no terminal automation |
| IBI-10 | Data areas and data queues | MVP-contract inventory/reference payloads only, 2/6–7; LIVE 16/LANGUAGE 17 | Explicit supplied objects/references can be recorded; content collection, queue operations and behavior analysis unsupported until gated |
| IBI-11 | Batch programs, job descriptions and available scheduling metadata | MVP-bounded supplied inventory/job/schedule exports, 6–7/9 | Parse declared format and source references; not actual job history/runtime scheduling; live collection LIVE 16 |
| IBI-12 | OBJECT_STATISTICS, PROGRAM_INFO, BOUND_MODULE_INFO | MVP imported observations only, 6; Later LIVE 16 | Official per-release/PTF syntax, authorities and availability verified before live implementation; authority failures reduce completeness |
| IBI-13 | DSPPGMREF | MVP-bounded existing exports, 6–7; LIVE 16 | Historical object-reference semantics retained; no outfile command executed because it writes a database file |
| IBI-14 | Db2 system catalogs/available SQL services | MVP-bounded normalized supplied catalog formats, 6; LIVE 16 | Allowlisted queries and release/service matrix; unavailable services explicitly unsupported, no inferred uniform support |
| ANA-01 | Program structure, variables, data definitions, procedures, subroutines | MVP-bounded, 8–10 | Origin spans, original identifiers, scoped declarations and synthetic expected-IR fixtures; missing external declarations unknown |
| ANA-02 | Conditions, loops, expressions, assignments, calculations | MVP-bounded, 8–9 | Nested grammar/expression fixtures and bounded CFG/DFG; unsupported indicator/alias/runtime semantics block dependent conclusions |
| ANA-03 | Calls and file operations | MVP-bounded, 7–9 | Qualified-context resolution and operation-specific edges; unresolved targets/file overrides retained |
| ANA-04 | Embedded SQL | Later LANGUAGE 17 | MVP structural diagnostics may recognize SQL sections; no semantic SQLRPGLE claim until SQL lowering/corpus gate |
| ANA-05 | Error handling | MVP-bounded RPG monitor/error blocks and CL message handling, 8–9 | Supported success/error branch extraction; unsupported exception behavior explicit |
| ANA-06 | Evaluate existing parsers/libraries before custom parsers | Conditional parser gates, 8–10/17 | REA assessment plus pinned Code for i spike; separate CL/DDS candidate search and licensed corpus; record decision with notices |
| ANA-07 | No exclusive regex reliance; language-aware parsing | MVP-bounded, 8–10 | Grammar/token/position-aware parsing; regex may assist lexical classification only; opaque statements are analysis barriers |
| ANA-08 | Control-flow/data-flow/dependency IR | MVP-bounded, 2/8–10; richer semantics LANGUAGE 17 | Stable versioned IR, declaration/statement source links, explicit opaque blocks; no compiler-equivalence claim |
| ANA-09 | Preserve identifiers and source references | MVP-bounded, 6/8–10 | Exact raw bytes plus decoding/origin map; includes and continuations keep physical source coordinates |
| ANA-10 | REA abstractions and dedicated IBM i providers | MVP-bounded, 2/5/8–10 | REA CLI boundary and evidence concepts adapted; do not use BinarySession/private modules as IBM i analyzer core |

## Business understanding, graph and evidence requirements

| ID | Original requirement | Coverage and step | Bounded acceptance / later gate |
| --- | --- | --- | --- |
| BUS-01 | Business rules, validation, calculations, transformations, workflow decisions | MVP-bounded deterministic candidates, 11 | Conditions/actions/calculations cite supported IR spans; opaque inputs block unsupported conclusions; calculations reflect declared subset, not runtime precision guarantee |
| BUS-02 | Program responsibilities, inputs/outputs, DB interaction, error behavior | MVP-bounded factual summaries, 8–12 | Derive from supported declarations/operations with citations; semantic responsibility prose is labeled interpretation and reviewable |
| BUS-03 | Rules within programs and across procedures | MVP-bounded, 11 | Supported resolved calls and finite path composition; recursion/unknown paths bounded with diagnostics |
| BUS-04 | Cross-program rules and workflows | MVP-bounded resolved-link composition, 11; richer semantics LANGUAGE 17 | Composed conclusions inferred; no invented value flow through unresolved calls, shared mutable state or unavailable modules |
| BUS-05 | Database-dependent conditions | MVP-bounded supported expressions and file-operation context, 11 | Cite catalog/field links if present; database contents, isolation/transaction behavior and full row lineage not inferred; richer lineage LANGUAGE 17 |
| BUS-06 | Structured business rule record | MVP-contract, 2/11 | ID/application/program/name/description/conditions/actions/evidence/review fields; separate classification and interpretation provenance; revision history |
| BUS-07 | Deterministic analysis; LLM explains extracted behavior | MVP-bounded deterministic + Conditional private AI, 11–12 | Facts and interpretations separate; unsupported/extraneous claims not published; report unknowns explicitly |
| BUS-08 | Human approval/correction and uncertainty | MVP-bounded, 11/14 | Approve/reject/correct revisions; evidence change invalidates approval; uncertainty persists after review |
| KNO-01 | System/Application/Program/Procedure/Module/Database/Table/Field/Screen/Job | MVP-contract + bounded population, 2/4/6–10 | Core schema accommodates all; preserve database subtype and source/member/object associations |
| KNO-02 | Business Rule/Workflow/Dependency/Evidence/Analysis Run | MVP-bounded, 2/4/7/11 | Provenance-linked records and immutable observations; runs carry scope/completeness/errors |
| KNO-03 | Programs accessing table; programs calling program; application business rules | MVP-bounded, 7/11–12 | Query observed static/metadata links with classification/evidence; no runtime execution inference |
| KNO-04 | Procedures contributing to workflow; entry points; unresolved dependencies | MVP-bounded, 7/11–12 | Resolved graph paths, membership and inferred entry-point labels; unknown target records queryable |
| KNO-05 | What depends on a field? | MVP-bounded declared local field references, 8–12; richer field lineage LANGUAGE 17 | Return explicit available field edges plus limitations; do not imply complete interprocedural/database lineage |
| KNO-06 | PostgreSQL first; graph database only if justified | MVP-bounded, 4/7/12 | Relational entities/edges and bounded traversals; add graph store only after measured query requirement beyond current interfaces |
| EVI-01 | Evidence ID/artifact/location/method/time/hash/version/excerpt/verification | MVP-contract + enforced linkage, 2/4/6–13 | Evidence example/schema includes all mandatory provenance; no source locations fabricated for metadata-only evidence |
| EVI-02 | Every dependency/rule/conclusion supports source where possible | MVP-bounded, 7–13 | Unsupported claim remains unresolved diagnostic; evidence existence and accessibility checked before export/explanation |
| EVI-03 | Observed/inferred/unresolved/verified distinction | MVP-bounded, 2/11–14 | Verified is review, not certainty; examples include verified inference and pending-review observation |
| EVI-04 | Parser limitations/skips/authority failures/incomplete discovery | MVP-bounded, 6–15; live authority LIVE 16 | Diagnostic code, stage, locator, affected coverage and run; every skipped artifact accounted for |

## Knowledge generation, retrieval, UI and technology requirements

| ID | Original requirement | Coverage and step | Bounded acceptance / later gate |
| --- | --- | --- | --- |
| OUT-01 | JSON/JSONL/Markdown/dependency graphs/Mermaid | MVP-bounded, 13 | Version `0.1.0`, safe filenames, escaped labels, hashes, identifier links; validate schemas/references |
| OUT-02 | Rule catalogs/application/program documentation/workflows/LLM context | MVP-bounded, 11–13 | Separate linked artifacts with evidence and uncertainty; unresolved workflow descriptions remain incomplete |
| OUT-03 | Suggested export tree and manifest/system/application/program/database/rule/workflow/evidence/chunk indexes | MVP-contract + generation, 2/13 | Versioned equivalent linked layout; original source artifacts only explicit authorized option; no broken links after masking/omission |
| OUT-04 | System/application/program/rule/source detail levels | MVP-bounded, 12–13 | Scope-aware exact IDs retrieve smaller independent chunks and supporting evidence; no system-wide monolithic document |
| OUT-05 | Reusable foundation for Java/Python/.NET/Node.js/React and other modernization | MVP-contract knowledge portability, 13; modernization out of scope | Language-neutral facts/IR/exports; no replacement code, translation, migration plan execution or modernization engine |
| RET-01 | Exact identifier lookup | MVP-bounded, 12 | Namespace-aware stable lookup and accessible not-found; returns canonical entity and provenance |
| RET-02 | Keyword search | MVP-bounded, 4/12 | PostgreSQL text indexes; scope, limits, deduplication and citations |
| RET-03 | Semantic search and hybrid PostgreSQL/pgvector | Conditional, 4/12 | Schema/index seam present; approved private embedding endpoint, model identity/dimension compatibility, reindex policy and integration checks before enablement |
| RET-04 | Dependency traversal and evidence retrieval | MVP-bounded, 7/12 | Bounded depth/size, cycles/unknowns/omissions explicit; citation links resolve under source policy |
| RET-05 | Customer-validation explanation workflow | MVP-bounded deterministic fixture; Conditional AI, 12/15 | Find capability/programs, direct graph paths, rules and evidence; explanation cites support and unresolved assumptions |
| RET-06 | Token-aware context selection | MVP-bounded, 12 | Configured budget/provider tokenizer, reserved output overhead, deterministic dedup/priorities and truncation report; source references retained |
| RET-07 | Configurable LLM and structured-output validation | Conditional, 12; policy ENTERPRISE 18 | Approved private endpoint only, validated output schema/citation set; no automatic fallback to external service |
| UI-01 | Web interface | MVP-bounded, 14 | Import→run→inventory→graph→evidence→review→retrieve→export; cancellation/partial/no-AI/unsupported states; accessible controls |
| TEC-01 | Next.js/TypeScript/Tailwind | MVP-bounded, 3/14 | Dependency locks, generated backend schema types and local build/checks |
| TEC-02 | Python/FastAPI/Pydantic | MVP-bounded, 2–4 | Versioned validation/error contract, modular application services and worker; TypeScript parser sidecar allowed only after justified gate |
| TEC-03 | REA/MCP/language parsers/IBM i providers | MVP-bounded + Conditional parser, 5/8–10/13 | Pinned CLI inventory fixture and parser/MCP contract fixtures; no binary-analysis engines installed |
| TEC-04 | PostgreSQL/pgvector/object storage | MVP-bounded local, 4; S3-compatible ENTERPRISE 18 | Durable DB, content-addressed private volumes/storage interface; independent metadata/storage recovery/retention contracts |
| TEC-05 | Docker and Compose local development; Kubernetes future | MVP-bounded, 3/15; Later ENTERPRISE 18 | Loopback published UI/API, internal DB/worker, persistence/health; Kubernetes design does not make prototype enterprise-ready |
| TEC-06 | Extra technologies only when justified; simple local testing | MVP-bounded, 2–15 | No broker/graph DB/model downloads by default; parser sidecar/runtime additional dependencies require recorded isolation decision |
| DEV-01 | Incremental development, inspect REA before code, review licenses/reuse/gaps/design | Step 1 assessed; Step 2 document gate; implementation 3–15 | Existing REA assessment, contracts, architecture and backlog complete before runtime; distribution-specific notices before redistribution |

## Security and operation requirements

| ID | Original requirement | Coverage and step | Bounded acceptance / later gate |
| --- | --- | --- | --- |
| SEC-01 | Read-only connections/least privilege | MVP bounded inert input, 5–6; LIVE 16 | Explicit effects and scope; unavailable operations rejected; read-only account verification before live |
| SEC-02 | Encrypted credentials/secrets management | Later ENTERPRISE 18/LIVE 16 | MVP stores no live enterprise connector secrets; environment/private endpoint secrets not echoed or exported; encrypted credential store/secret provider before live deployment |
| SEC-03 | RBAC | Later ENTERPRISE 18 | Single local user only now; role/scope/source-access design and enforcement tests required before multi-user/network service |
| SEC-04 | Audit logging | MVP-bounded local import/run/review/export/settings events, 4/11–13; enterprise policy ENTERPRISE 18 | Actor/time/action/resource/outcome; redact sensitive content/credentials; retention/recovery specified; enterprise tamper/central audit requirements later |
| SEC-05 | Data masking | MVP-bounded configurable display/retrieval/export masking, 6/12–14 | Raw artifact remains private immutable source; masked excerpt distinguished from hashed original; masking must not invent coordinates or bypass evidence access |
| SEC-06 | Configurable data retention | MVP-bounded local policy, 4/15; administration ENTERPRISE 18 | Deletion/expiry covers artifacts/exports/chunks/vectors/derived records with referential integrity, review history policy, active-job exclusion; no public MCP deletion tool |
| SEC-07 | Source-code access restrictions | MVP-bounded private storage/no-source-default export, 4/6/13; ENTERPRISE 18 | Local host boundary declared; API handles policy-masked source; per-user/role authorized retrieval enforced before enterprise |
| SEC-08 | On-premises deployment | MVP-bounded localhost Compose, 3/15; operational scale ENTERPRISE 18 | No public service binding; startup/storage/backup/recovery instructions; enterprise identity/network/hardening later |
| SEC-09 | Private LLM support; external transmission only explicit configuration/authorization | Conditional private endpoint, 12; enterprise approval ENTERPRISE 18 | AI off by default; private allowlist/data scopes; no external AI or automatic downloads in delivery agreement; broad future requirement cannot override this local boundary |
| SEC-10 | Never arbitrary commands or production-object modification | MVP invariant, 5–15; LIVE 16 | Allowlisted typed CLI operations only; inert imports; no enterprise command shell and no `DSPPGMREF` outfile execution |
| SEC-11 | Side effects disabled by default and explicit approval | MVP no enterprise side-effect surface; LIVE 16 | Providers declare effects; future privileged operation has explicit policy/approval separate from ordinary read-only API; unavailable now |
| SEC-12 | Source/comments/docs/metadata untrusted; no instruction/tool permission override | MVP-bounded, 6/12–13/15 | Separate system policy from retrieved data; retrieval cannot invoke tools; malicious content and unknown citation acceptance scenarios |
| OPS-01 | Long-running import/analysis/export with local testing | MVP-bounded, 3–4/15 | PostgreSQL durable jobs, idempotent/retry-safe stages, bounded limits/timeouts, progress/cancel/recovery; failures/partial publication explicit |

## Required parser decision gates

### Current RPGLE decision and future extension gates

The explicit step-8 Python/Lark selection superseded the original assessment's reuse prerequisite; the [recorded parser decision](rpgle-parser-decision.md) and [step-8 validation](laip-step-8-validation.md) establish the current bounded implementation. No upstream spike failure is claimed. Retain that implementation. Optional extension evaluation in [step 17](laip-step-17-language-roadmap.md) may compare Code for i `vscode-rpgle` commit `b357b356fbcc598de7f7e049d4f114f6b6f18408` (MIT); do not import the VS Code extension as an assumed public parser package. A minimal pinned source slice/sidecar requires runtime/dependency/notices and upgrade decisions. Keep LAIP IR/lowering provider-neutral; parser AST/cache is not business-rule/control/data-flow analysis.

Any future candidate comparison must test declared dialects, continuations, strings/comments, includes and origin mappings, file operations, embedded-language barriers, diagnostics, cancellation/resource bounds and reproducibility. Capture corpus provenance and exact parser pins. Adoption requires an isolated API, precise source positions, no source execution/network fetch, license/notice review and enough deterministic fixtures for its declared subset. Record actual failures without retroactively inventing a failed step-8 spike. The old ANTLR `rpgleparser` is not the default; reuse requires Java/tooling/license and action-isolation reassessment. Fixed-format, SQLRPGLE and broader semantics remain LANGUAGE implementation work until their own gates pass.

### CL/CLLE and DDS gates before steps 9–10

Evaluate existing parsers and their licenses before building custom grammars. Record candidates/rejections and choose bounded token/grammar contracts. DDS syntax highlighting is not a semantic parser. DDS acceptance must include PF/LF/display definitions, positions/continuations, keys/fields/indicators/validation and unknown keywords; CL acceptance must include continuation, quoting, comments, override scope, dynamic command arguments and message/control forms. Declare supported operations by provider version; preserve opaque barriers and avoid claims based on recognized keywords alone.

## Dependency order and incremental acceptance

| Stage | Depends on | Deliverable / acceptance before proceeding | Risk |
| --- | --- | --- | --- |
| 1–2 assessment/contracts | Original request and delivery agreement | Pinned REA decision, parser gates, component interfaces, canonical examples, API/jobs/deployment contracts and requirement map | High: scope/identity/uncertainty decisions propagate everywhere |
| 3 scaffold | 2 | Loopback Compose topology, repeatable locked builds, API/worker/DB/storage health, no live/AI default | Medium |
| 4 persistence/storage | 3 and schema contracts | Clean migrations; identity collisions/reanalysis history; transaction/job claim/lease/recovery; artifact hashing/storage consistency | High |
| 5 REA adapter | 3 and contract; 4 for persisted provenance | Actual pinned CLI fixture validates version/output/lifecycle; no source text/analysis assumption | Medium |
| 6 safe imports | 4–5 | All supplied artifacts/statuses accounted for; raw-byte/origin mapping; traversal/limit/masking and malformed input scenarios | High |
| 7 inventory/graph | 4/6 | Namespace isolation, confirmed/inferred memberships, unknown targets and all relationship kinds | High |
| 8 RPGLE subset | 6–7 + RPGLE decision gate | Source-preserving parser and declared IR subset; includes/barriers supported; expected facts/evidence fixtures | High |
| 9 CL subset | 6–7 + CL decision gate | Control/call/job/error fixtures; dynamic target/override/unknown command semantics visible | High |
| 10 DDS subset | 6–7 + DDS decision gate | PF/LF/display field/position/validation fixtures; unresolved external descriptions retained | High |
| 11 rules/review | 7–10 | Supported-path rules/workflows cite evidence; inference/review separate; corrections/reanalysis preserve history | High |
| 12 retrieval/private AI seam | 4/7/11 | Deterministic exact/keyword/graph/evidence and budget checks; optional private AI separately validated; citation/injection publication controls | High |
| 13 exports/MCP | 11–12 | Versioned linked/checksummed exports and read-only tools match shared services; source-option policy enforced | Medium |
| 14 UI | 3/6–13 | Typed accessible complete offline journey with cancel/review/partial/unknown/no-AI views | Medium |
| 15 acceptance/local operation | 3–14 | Synthetic customer-validation end-to-end, malicious inputs, migrations/repeat analysis/cancel/restart, real REA adapter and truthful validation record | High |
| 16 LIVE design | 2/15 | Official matrix/access prerequisites, effects and real-target validation protocol; no support certification | High |
| 17 LANGUAGE design | Parser outcomes and 15 | Per-dialect grammar/IR/corpus/notices/prerequisite roadmap; no broader support certification | High |
| 18 ENTERPRISE design | 2/15–16 | Identity/source-access/secrets/audit/retention/storage/migration/security/operations gates | High |
| 19 PLATFORM design | 2/15–18 | Per-platform provider/namespace/artifact/authority/corpus proposals; no IBM i semantics assumed | High |

Sequencing remains the existing numbered plan. Design tasks may compare independent parser candidates early, but no analyzer implementation precedes its decision gate. UI fixtures may be prototyped against stable contracts; final acceptance must use actual services and persisted results. PostgreSQL—not transient API memory—determines progress, cancellation and outcome.

## MVP acceptance corpus and support limits

The step 15 synthetic customer-validation fixture must include a CL entry point, free-format RPG procedures, DDS PF/LF/display definitions, explicit source/object and binding/schema/job exports, a missing include, same-named objects in different libraries, a dynamic call and unsupported constructs. Expected artifacts cover inventory, dependencies, bounded CFG/DFG, validation/calculation/rule candidates, inferred workflow, immutable evidence/reviews, token-budgeted retrieval and linked exports. Scenarios must demonstrate that missing inputs/opaque constructs/unresolved dependencies cannot turn into verified runtime behavior.

Operational scenarios include interrupted import/analysis/export, worker restart/lease recovery, repeat analysis without duplicate entities, cancellation races and committed partial outcome, migration from clean DB, unavailable/incompatible REA, malicious archive paths/resource limits, prompt injection and unknown citations. Optional semantic/LLM endpoint tests must be reported as unrun when no approved endpoint is available; deterministic retrieval can still pass. Synthetic success is evidence of fixture behavior, not live IBM i release or enterprise validation.

## Open prerequisites and explicit gaps

- The original attachment is truncated. Additional user deliverables beyond the visible phase-1 text are unknown and must be added through a later scope decision.
- No live IBM i target, release/PTF, authority grant or real licensed source corpus has been supplied. LIVE/LANGUAGE compatibility remains conditional.
- The original RPGLE reuse spike was superseded by the user-selected Python/Lark implementation decision; see the [parser decision](rpgle-parser-decision.md) and bounded coverage in [step 15 validation](laip-step-15-validation.md). No upstream spike failure or full compiler support is claimed.
- REA package metadata is inventoried; exact shipped dependencies, vendored parser slices and optional engines still require distribution-specific SBOM/notice text review. No binary engine is needed for this MVP.
- Enterprise credential encryption/RBAC, per-user source authorization, S3-compatible implementation and Kubernetes operations are later implementations. Local audit/masking/retention/storage contracts remain mandatory for the prototype.
- Full fixed-format/RPG III/SQLRPGLE/COBOL/SQL stored-procedure semantics, runtime calls/bindings, rich cross-program field lineage, live queue/data-area content and interactive 5250 behavior are unsupported initially.
- z/OS/COBOL/CICS/JCL, Oracle Forms, PowerBuilder and other providers have contracts/roadmaps only until their platform-specific validation gates pass.

Step 2 acceptance is satisfied when the architecture document and versioned examples implement these invariants and boundaries, every component has an interface, and each requirement ID above has its bounded MVP or explicit later disposition. Implementation status must be tracked separately from this requirement allocation.
