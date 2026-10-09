# LAIP contracts 0.1.0

Design specification only. No API, parser, worker, or migration is implemented by these artifacts. `laip.schema.json` is a JSON Schema 2020-12 document with reusable `$defs`; its root validates the linked synthetic example bundle. Production records use the relevant definition directly and do not require `synthetic: true`. `synthetic.example.json` demonstrates every definition. `VALIDATE.rpgle`, `relations.example.json`, and `export-rule.example.json` are exact bytes used for the fixture digests; their logical import/export names are recorded in the bundle.

## Version and ownership

Use `schema_version: "0.1.0"` on persisted/public envelopes and knowledge exports. During implementation, Pydantic models become the single authored runtime contracts; their generated JSON Schema/OpenAPI and generated TypeScript types must remain equivalent to this gate. Reject unknown versions and unknown envelope fields. Schema migrations are explicit, additive changes are minor versions, breaking changes are major versions after 1.0; pre-1.0 minor releases may break compatibility only with migration notes. Do not silently rewrite stored records. Generic `attributes` and `metadata` are extension maps, not executable commands or an escape from provider validation.

## Stable identities and revisions

For every hashed record, identity and fingerprint, `canonical(value)` means UTF-8 [RFC 8785 JCS](https://www.rfc-editor.org/rfc/rfc8785) with LAIP's restricted numeric profile: only integers in JavaScript's safe range −9007199254740991 through 9007199254740991. Reject noninteger/nonfinite numeric values and unsafe integers before hashing or JSONB persistence; exact decimal values use typed `{decimal:"123.40"}` and large integers `{integer:"18446744073709551615"}` strings. Provider normalization preserves the original number representation in its raw artifact and emits explicit typed values; it must not round an enterprise numeric value into a JavaScript number. Normalize integer numeric input such as 1.0 to the canonical integer 1 only when parsed exactly and known within bounds; never hash Python's default float rendering.

JCS sorts object keys by UTF-16 code units (including supplementary-character keys), preserves array order and string values, uses compact ECMAScript string escaping, and rejects invalid Unicode/lone surrogates. Reject duplicate JSON object keys before decoding/JSONB storage. Do not normalize arbitrary evidence strings/metadata keys after hashing. Identity names are Unicode NFC **before** hashing; reject collisions introduced by identity case/NFC normalization if they represent distinct qualified objects, and retain original names. IBM i system object/library names normalize to uppercase; procedure/SQL quoted identifiers retain dialect case behavior. Alias strings never establish identity or resolve references by themselves. Python's default key sort and numeric JSON rendering are not the general algorithm; the ASCII-key integer-only examples happen to have identical bytes. `canonicalization.example.json` supplies Unicode-key/decimal/safe-integer golden cases.

- Entity ID: `ent_` plus lowercase SHA-256 of canonical `Identity`. This tuple is `{system_namespace, kind, qualified_identity}`. A configured namespace is assigned once per real/synthetic system, independent of hostname/display-name changes. Qualified identity is an ordered segment array; encode parent identity segments for nested declarations. Procedure identity includes qualified owning program/module and declaration scope; overloaded signatures need a provider-declared discriminator. Same-named objects in different libraries or kinds remain distinct.
- Source members use kind `SourceMember` and `[library, source_file, member]`; compiled programs use `Program` and `[library, "*PGM", name]`. No source/object equality is assumed. `SOURCE_OF` needs explicit manifest/metadata evidence; missing mappings remain unknown.
- Artifact ID: `art_` plus SHA-256 of canonical `{import_id, locator, sha256}`. This is an import occurrence, while `sha256` identifies the raw content-addressed blob. Multiple occurrences may share bytes without sharing identity/provenance. Imports are immutable snapshots; repeat processing of an import reuses its IDs.
- Evidence ID: `ev_` plus SHA-256 of the complete immutable Evidence object excluding `evidence_id`. The recorded `content_sha256` is the referenced raw artifact hash (or a canonical metadata-payload hash for artifact-free evidence). Repeated publication within the same run is idempotent; a new run is a new collection observation even for identical source bytes. Independently index semantic fingerprints for comparison across runs.
- Dependency ID: `dep_` plus SHA-256 of its immutable payload excluding `dependency_id`. Rule revision ID: `rev_` plus SHA-256 of its payload excluding `revision_id`. The stable rule entity is separate from its changing interpretation/revision. Cross-run matching of rules uses the qualified rule anchor, with ambiguous moves reported for review.
- Review events use unique durable IDs and include SHA-256 of the complete target revision/record as `subject_fingerprint`. `support_fingerprint` hashes canonical `{evidence, providers, resolution_decisions, configuration_sha256}`: evidence objects contain `evidence_id` and `content_sha256`, sorted by evidence ID; providers are sorted by ID/version; resolution decisions by dependency ID. The example contains two supporting evidence records, one provider, no additional resolution decisions, and the run configuration digest.

Identity algorithms are implemented identically in Python and TypeScript using cross-language golden examples. Do not use a display label as a foreign key. Entity records are snapshot projections; changed attributes/aliases/analysis status are stored as append-only entity observations/revisions and selected by snapshot, not overwritten as historical fact.

## Entity attribute contracts

All catalog entities share `Entity`. `attributes` is provider-normalized JSON whose recognized keys below are validated in the service layer; unsupported extension keys remain metadata and cannot drive resolution. `Dependency`, `Evidence`, and `AnalysisRun` have dedicated definitions instead of being disguised as catalog entities.

| Kind | Required meaning / typed attributes when supplied |
| --- | --- |
| System | Platform, release/PTF (nullable), scope, synthetic flag; namespace is immutable |
| Application | Membership comes from `MEMBER_OF` edges; observed/manifest membership is confirmed, reachability-derived membership inferred; shared members allowed |
| Program | Object type, language/dialect, entry points, source availability, parameters/I/O and responsibility findings with evidence |
| Procedure | Owning program/module, declaration signature, procedure/subroutine distinction, parameters and declaration spans |
| Module / ServiceProgram | Qualified object identity, binding metadata/provenance; incomplete binding is explicit |
| Database / Table | Catalog/schema or library aliases, object subtype (physical/logical file, SQL table/view), keys and record formats; imported metadata is required for unavailable external definitions |
| Field | Qualified owner and record format, datatype/length/precision/nullability (unknown nullable); original system/SQL names retained |
| Screen | Qualified display-file record format, fields/coordinates/indicator expressions; static definitions do not establish screen execution |
| Job | Job-description/scheduler/batch-entry subtype and schedule metadata as available; no scheduling/execution operation |
| DataArea / DataQueue | Qualified object identity and supplied metadata/reference observations only; no content reads or queue operations in MVP |
| BusinessRule | Stable rule anchor; conditions/actions/classification/evidence belong to `RuleRevision` |
| Workflow | Entry point, contributing procedure IDs and resolved edge IDs; composed conclusions inferred and missing paths explicit |
| SourceMember | Library/source-file/member, language/encoding metadata and occurrence locators; distinct from compiled object |

`attributes` availability is not a claim of MVP analysis support. The backlog supplies the actual bounded coverage for each subtype.

`Expression.value` is a canonical normalized value with additional service validation by `node`. Ordinary operator values use `{operator,operands:[{reference:string}|{literal:CanonicalValue}|{expression:Expression},...]}`; assignments may use the explicit compact `{operator:"assign",target:string,literal:CanonicalValue}` variant shown in the examples. Resolve source-spelled reference/target names in the owning rule's procedure/program scope and declarations. The example describes source syntax, not an established database-field binding; unresolved declarations retain diagnostics and block type/alias/database-dependent claims. Generic JSON value validation alone does not establish these semantics.

## Certainty, resolution, review, and provenance

`classification` means `observed`, `inferred`, or `unresolved`. `resolution` independently means `resolved`, `ambiguous`, `dynamic`, or `missing`. `Review.status` independently means `pending_review`, `verified`, or `rejected`. The bundle includes an **inferred + verified** rule and **observed + pending_review** evidence. Human approval never converts static inference into observed execution. Rejected observations remain in history and are excluded from default accepted summaries; expose them when inspecting disagreements.

Evidence stores provider/version, collection method/time, artifact hash/location, excerpt or metadata, authority, supporting/contradicting links, run ID, and limitations. Review status is a computed projection from append-only Review events, not a mutable Evidence field. Public/export views attach `{status, review_id, stale}` separately. If no event exists, effective status is `pending_review`. If a revision or support fingerprint changes, the new projection is `pending_review`, while the prior approval remains in history. Concurrent reviews require the expected latest review ID (or null); a mismatch returns conflict.

`EvidenceView` is a read projection with `evidence_id`, canonical `record_sha256`, certainty, provider/time/spans, `display_excerpt`/`display_metadata`, limitations/support links, masking policy, content availability, and review projection. Its display data may be masked; it is not a canonical Evidence payload and is never rehashed to replace the original ID. Internal canonical Evidence remains immutable; apply masking before REST/MCP/context/export serialization. The harmless synthetic example contains unmasked fixture text only.

Artifact-free observations require nonempty metadata and a verifiable canonical payload hash. Upstream REA records, when returned, are stored as immutable payload artifacts and linked via `upstream_record`; preserve upstream IDs and bytes. A reference-source graph without upstream Evidence must not be invented into a REA Evidence record. Its LAIP wrapper cites the raw graph hash and method.

`Job.run_id` is provenance/basis; `lifecycle_owner` explicitly identifies the row whose lifecycle may change (`run`, `export`, or internal `retention` operation). Import/analysis own their run; export/retention never reopen, cancel or finalize their completed basis analysis. Worker ownership and fencing stay private even when JobView exposes this operation identity.

## Source and graph invariants

Spans use one-based lines/columns measured in decoded Unicode codepoints, end-exclusive. Raw byte ranges are zero-based/end-exclusive into the original artifact. Decoding retains an origin map (including CCSID/encoding, newline transformations, and expanded include-to-original mappings); unsupported or lossy decoding yields diagnostics and cannot imply precise coordinates. Require non-reversed spans, byte ranges within the referenced artifact, and a matching artifact/hash. Source excerpts are masked read projections; stored raw bytes remain private. Masked text has a separate policy/digest and never replaces the raw content digest.

`OriginMap` records encoding, lossiness, newline policy, decoded digest and decoded-byte intervals mapped back to raw byte intervals/artifacts. The fixture is an ASCII/UTF-8 identity map over one artifact. Nonidentity decodings require fine-grained segments at transformation boundaries; providers must not infer a linear byte mapping across multibyte or length-changing transformations. Expanded includes retain separate origin segments and original artifact identities.

Every dependency and rule revision has evidence. A resolved dependency has exactly one target and no candidates. An ambiguous dependency has at least two candidate IDs and a null target. Dynamic/missing targets remain null with no authoritative candidates. Graph traversals include only resolved targets by default; an opt-in unresolved overlay never invents graph paths. Static `CALLS` or file-access edges do not assert runtime occurrence.

All referenced entity, artifact, evidence, run, snapshot, and revision IDs must exist in the same namespace/snapshot lineage. No arbitrary ID accepted by a JSON shape can bypass this service check. Supporting evidence and contradiction references are bounded; derivation support is a DAG. A contradiction link does not automatically reject either observation. Runs bind immutable input/configuration/provider snapshots. Coverage partitions supplied artifacts into analyzed/partial/unsupported/failed/excluded; `missing` counts declared-but-absent artifacts outside that partition. Completeness only concerns supplied/declared scope.

## Context and export invariants

Context selection reserves model instructions, query overhead, output tokens, and protocol envelope before assigning `max_context_tokens`. Enforce the budget on the final serialized context including citations, not only chunk text. With a configured private model, use its exact tokenizer. The UTF-8 byte upper bound is allowed only as an explicitly labeled deterministic/offline estimate (the example shows ASCII text), and must not authorize an unknown model’s context window. Chunk links preserve entity/evidence/snapshot IDs, certainty, unresolved relationships, and omissions. Never truncate a claim away from its supporting citation.

Exports are snapshot-bound and asynchronous. Hash the exact bytes of each listed file; the manifest excludes itself from its own file digest list. Include run/provider/schema/masking versions and limitations. `source_included: false` excludes full raw source files, while short masked supporting excerpts may remain. A true value must be explicitly requested through the REST export option and separately authorized by the source-access policy; MCP retrieval cannot toggle it. Use safe generated relative filenames, escape Markdown/Mermaid, and validate all links/checksums before atomic publication.

## Validation boundary

JSON Schema checks shape/enums/required fields and resolved/ambiguous target combinations. Separate acceptance checks must verify identity hashes, foreign keys, namespace/snapshot consistency, source spans, byte/metadata digests, coverage accounting, support/review fingerprints, origin mappings, expression node types, and context/export limits. Do not equate a passing example-schema check with parser, IBM i, queue, or enterprise validation.

References: [JSON Schema generation in Pydantic](https://docs.pydantic.dev/latest/concepts/json_schema/), [REA assessment](../../rea-assessment.md).
