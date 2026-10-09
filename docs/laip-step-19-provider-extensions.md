# Step 19 — additional legacy-platform provider proposals

**Date and official-source check:** 2026-10-08. **Status:** documentation proposal complete; providers unimplemented and platform validation unperformed. This delivers [plan step 19](plans/laip-execution.md#step-19). Application modernization, code translation and replacement application generation are outside scope.

## Shared contract and versioning decisions

Reuse the [canonical 0.1.0 contracts](contracts/0.1.0/README.md), [architecture interfaces](laip-architecture.md) and [linked export validation](laip-step-13-validation.md). Identity consists of configured `system_namespace`, entity `kind` and an ordered `qualified_identity` string array. Each provider supplies a versioned normalization profile, preserving original spelling and aliases. IBM i uppercase/library/member rules must not be applied to other platforms. A namespace represents an explicitly configured source system, not a tenant; [enterprise source policy](laip-step-18-enterprise-milestone.md) separately governs ownership and access.

Current entity kinds include `Program`, `Procedure`, `Module`, `Table`, `Field`, `Screen`, `Job`, `Application` and IBM-oriented `ServiceProgram`, `SourceMember`, `DataArea`, `DataQueue`. Generic kinds can be used only where semantics fit. Do not recast a CICS transaction as an IBM i object or invent enum values in 0.1.0. New kinds, relations, platform attributes and expanded IR must pass a versioned schema/provider-payload migration, generated-type/export validation and reader compatibility gate. The roadmap changes no current contracts or runtime schemas.

Every connector implements the proposed `describe/probe/collect` boundary; every analyzer accepts inert artifact occurrences plus a scoped metadata/include resolver and emits bounded facts/IR/evidence/diagnostics. Capabilities declare exact operation, availability, platforms, languages, dialects, effects and limitations using the existing schema. Provider-specific version, prerequisites and normalization details belong in approved descriptors/manifests and future versioned payloads, not undeclared fields. Declare only validated subsets as available. Collection and language analysis are separate providers: z/OS is an operating environment; COBOL, CICS commands/BMS and JCL require separate dialect/semantic profiles.

Retain raw bytes, checksums, collection provenance, encoding/record attributes and decoded origin maps. Every fact and rule condition/action cites an evidence span or supplied metadata record. Unsupported syntax, unavailable includes, missing catalog data, ambiguity and dynamic dispatch become explicit diagnostics/barriers. CFG/data-flow IR must preserve unknown effects and incompleteness; absence of evidence cannot establish absence of a dependency. Classification, dependency resolution and human review remain separate. New evidence invalidates stale approvals through the existing immutable revision model; an extension cannot auto-verify unknown paths.

Retrieval stays platform-neutral: exact identity, keyword, bounded graph, evidence and token-budgeted context use canonical records, authorized scope and snapshots. Adapter output is validated before publication. Exports retain schema version, stable links, hashes, coverage and uncertainty. Full source requires its explicit option and access policy. MCP remains read-only and uses the same service semantics. Platform parsers never invoke source programs, compilers, application runtimes, transactions or submitted jobs.

## Proposed identity profiles

These illustrative tuples use existing generic kinds and are not registered provider implementations. Stable entity IDs must use the canonical identity function, not hand-built IDs. Provider profile version and source aliases must be recorded so normalization migrations cannot silently rename history.

| Profile | Illustrative identity | Resolution rule |
| --- | --- | --- |
| z/OS COBOL | namespace `zos:training-a`, kind `Program`, tuple `["APP.SOURCE", "CUSTOMER", "CUSTOMER-PROGRAM"]` | Dataset/member occurrence and contained program identity are distinct; nested program identity adds containing program qualifiers |
| z/OS JCL | namespace `zos:training-a`, kind `Job`, tuple `["APP.JCL", "CUSTJOB", "CUSTJOB"]` | A supplied job definition is distinct from an executed job instance; step/PROC/INCLUDE references retain source qualification |
| Oracle Forms | namespace `forms:training-a`, kind `Screen`, tuple `["customer.fmb", "CUSTOMER_BLOCK", "CUSTOMER_ID"]` | Module/block/item qualifiers are proposals; database objects use separately supplied catalog/schema/object identities |
| PowerBuilder | namespace `pb:training-a`, kind `Module`, tuple `["customer-target", "ui-library", "w_customer"]` | Target/library/object scope and inheritance aliases are explicit; library search order cannot be guessed |

Case sensitivity, quoted database names, dataset aliases and platform search paths are profile-specific. Apply the existing identity NFC/canonicalization rules and reject normalization collisions; preserve original names separately. Do not infer a library, schema, tenant or runtime destination from a basename. If an identity profile cannot map faithfully to current kinds, delay publication pending the schema gate.

## Platform capability proposals and prerequisites

| Proposal | Initial allowed input and bounded analysis | Prerequisites and explicit limits |
| --- | --- | --- |
| ZOS-OFFLINE / ZOS-READ | Offline sequential/PDS/PDSE member and USS source exports; supplied dataset/member catalog attributes. Later separately approved bounded GET collection | Exact z/OS/z/OSMF version, dataset/USS scope, encoding, record format/length and source-owner authorization; no job submission, allocation, delete, update or arbitrary REST proxy |
| COBOL-ZOS | Version-pinned COBOL source/copybooks, directives and supplied compiler/binding metadata; declarations, procedures, conditions, calculations, file operations and bounded calls | Source format/dialect/compiler options and copybook search order; nested programs and SQL/precompiler dependencies explicit; no compiler execution; dynamic targets unresolved without evidence |
| CICS-STATIC | Supplied EXEC CICS source, BMS maps, resource/transaction/program/file definitions and routing metadata | Exact CICS TS version, region/application/resource provenance, owner-authorized exports; no transaction execution or administrative region command; missing routes/resources remain unresolved |
| JCL-STATIC | JOB/EXEC/DD, PROC/PEND, INCLUDE, substitutions, overrides and conditional paths from source plus supplied procedures/catalog metadata | Exact JCL profile, PROC/INCLUDE search order, symbols and execution environment metadata; no job submit, JES/internal-reader access or dataset changes |
| FORMS-EXPORT | Owner-prepared versioned Forms source/metadata exports including triggers, program units, blocks/items, menus and library references | Exact Forms/database/export-tool versions, source rights/licenses and export manifest; bounded PL/SQL analysis; binary source requires a separately validated extraction adapter; no Forms runtime/compiler/database code execution |
| PB-TEXT | Owner-prepared textual object/DataWindow exports and target/library/project metadata; declarations, scripts/events, inheritance and bounded calls | Exact PowerBuilder edition/version/export encoding and library resolution order; dynamic event/SQL dispatch explicit; binary libraries require separate tooling/license validation; no application/runtime execution |

Offline import declares `local_read` and controlled `local_write` for publication; a future collector separately declares `remote_read`. Existing effects also include `remote_write`, `source_execution` and `model_egress`, but their presence in an enum does not authorize them. These proposals do not provide remote write or source execution. Optional model egress is governed by the separate private-AI policy and remains off by default.

This proposed capability example uses the current 0.1.0 `Capability` definition. `unavailable` accurately describes an unimplemented provider; it is not a support declaration. Effects describe the planned operation, not an enabled capability.

```json
{
  "operation": "analyze.cobol.offline",
  "availability": "unavailable",
  "platforms": ["z/OS"],
  "languages": ["COBOL"],
  "dialects": ["Enterprise COBOL for z/OS: profile pending"],
  "effects": ["local_read", "local_write"],
  "limitations": ["Provider unimplemented; EXT-01 through EXT-08 required"]
}
```

### z/OS collection and COBOL

[IBM's z/OS data set/file REST interface](https://www.ibm.com/docs/en/zos/3.2.0?topic=services-zos-data-set-file-rest-interface) contains both read and modifying operations. A future connector must independently allowlist approved read operations, exact dataset/member names and bounded USS roots. Verify TLS, authenticated identity, SAF/RACF or site-equivalent read authority, network path and rate/resource limits with the system owner. Export preparation by an owner is outside the parser; no general-purpose command or dataset-writing interface is proposed. If the IBM web interface cannot render during a source audit, use the [official z/OSMF programming guide](https://www.ibm.com/docs/en/SSLTBW_3.1.0/pdf/izua700_v3r1.pdf) and record the actual target release separately.

Manifest fields must distinguish raw exported bytes from record-oriented data, including F/FB/V/VB where supplied, CCSID, record boundaries and any removed RDWs/sequence fields. A text export without those attributes has declared fidelity limits. Preserve dataset/member qualification and export timestamp; inaccessible or filtered members are not absent members. Unsupported record encodings fail explicitly rather than silently transcoding.

COBOL includes require ordered authorized copybook resolution and COPY/REPLACING origin mapping. Compiler directives, source columns and continuation rules are dialect-specific. [Enterprise COBOL CALL](https://www.ibm.com/docs/en/cobol-zos/6.3.0?topic=statements-call-statement) supports literal and identifier targets; literal text alone does not establish runtime binding. Supplied compiler options/binding metadata are prerequisites for stronger resolution. [IBM's programming guide](https://www.ibm.com/docs/en/SS6SG3_6.5/pdf/pgmvs.pdf) describes dynamic-call behavior; this is a versioned reference, not a claim of 6.5 parser support. Embedded SQL, generated code, external entries and unresolved copybooks remain analysis barriers until their own validated profiles exist.

### CICS and JCL

CICS analysis distinguishes a program call, transaction reference, file/map reference and resource/routing association. Record region/application provenance and time of supplied definitions. Dynamic PROGRAM values, autoinstall, routing and unavailable region definitions prevent confirmed runtime workflows. [IBM EXEC CICS XCTL documentation](https://www.ibm.com/docs/en/cics-ts/5.5.0?topic=summary-xctl) is a command-semantics reference for static evidence; it grants no authority to execute the command. BMS map/field coordinates require their own span-aware grammar; do not reuse DDS columns or screen semantics without a validated mapping.

JCL parsing needs column/continuation/comment fidelity and ordered procedure/include expansion with substitutions linked back to original spans. Dataset and executable references remain qualified and conditional. Utility names do not prove utility effects without a validated operation profile and supplied control cards. Symbolic/dynamic datasets, missing PROCs, catalog aliases, scheduling state and runtime return codes remain unknown. [IBM JCL Reference for z/OS 3.1](https://www.ibm.com/docs/en/SSLTBW_3.1.0/pdf/ieab600_v3r1.pdf) supplies a baseline grammar reference; exact supported dialect/version must be tested before advertisement.

### Oracle Forms

[Oracle Forms deployment documentation](https://docs.oracle.com/middleware/1221/formsandreports/deploy-forms/intro.htm) distinguishes source FMB from compiled FMX. The [official deployment guide](https://docs.oracle.com/middleware/1221/formsandreports/deploy-forms/FSDEP.pdf) also describes menu/library/source and runtime artifacts. Do not treat compiled runtime artifacts as complete source or presume raw FMB is textual. Start with source-owner-prepared textual/XML exports only after choosing and validating an exact vendor export format/tool version. Store original artifacts plus tool identity, hash and source-to-export mapping; if the transformation has no original byte coordinates, identify export-coordinate spans and report that limitation honestly.

Supplied triggers, PL/SQL program units, block/item properties, validation logic, LOV/record-group references and attached libraries can support bounded rules and dependencies. Database links, synonyms, schema/catalog definitions, package bodies and grants require separately authorized supplied metadata; no SQL execution is needed for offline analysis. Built-in-trigger order, dynamic SQL, inherited library behavior and missing database implementations block full workflow conclusions. License and source ownership approval precede any proprietary extraction tooling; a representative licensed corpus and exact tool/export version precede support claims. The cited 12.2.1 documentation is a format reference, not a current-version compatibility certification.

### PowerBuilder

Begin with owner-prepared text exports and supplied target/library order, object inheritance and database metadata. [Appeon's PowerBuilder 2025 LibraryExport reference](https://docs.appeon.com/pb2025/powerscript_reference/libraryExport_func.html) documents exporting source from libraries; LAIP will consume such exports, not run scripts embedded in an imported project. The vendor function is not an authorization to launch PowerBuilder or an imported application. Object-type suffixes and DataWindow formats require an exact versioned manifest and fixture corpus.

[Appeon's solution documentation](https://docs.appeon.com/pb/whats_new/Working_with_solution.html) describes version/edition-dependent solution/library layouts; do not assume all PBL, PBD or folder layouts contain equivalent readable source. Binary PBL extraction or PBD analysis remains unavailable pending a licensed, pinned, non-executing adapter decision and fidelity assessment. Bounded PowerScript/DataWindow analyzers may emit declaration, event, inheritance, SQL text and field facts. Dynamic invocation, event dispatch, runtime DataWindow expressions, transaction objects and dynamic SQL remain explicit unknowns unless supported by supplied evidence and tested semantics.

## Parser/metadata decisions and implementation sequence

No parser library has been selected or licensed by this roadmap. For each profile, first compare a pinned vendor-format grammar/reference, existing parser candidates and a bounded custom grammar against real authorized samples. Record upstream commit/version, license/NOTICE obligations, dependencies, execution/network behavior, supported dialects, resource limits and origin-map fidelity. Reject reuse that requires imported-code execution or obscures unsupported constructs. Proprietary tools stay owner-operated export prerequisites until a separately approved adapter exists. Record rejected candidates and measurable coverage before adopting a grammar; do not claim full language support from declaration parsing.

Implement offline collection manifests and identity profiles before analyzers; then bounded declarations/references, origin maps, IR/rules, retrieval/export parity and finally separately authorized live collection. z/OS COBOL shares canonical contracts with any future IBM i COBOL profile, but dialect/options/encoding and binding semantics require separate tests. Integration depends on [enterprise controls](laip-step-18-enterprise-milestone.md) before shared-user or remote operation. No platform milestone depends on IBM i commands or assumes IBM i library semantics.

## Conformance and real-validation gates

All provider runtime gates are **UNIMPLEMENTED / UNTESTED**. A platform owner must supply explicit rights, exact version/edition/toolchain, source/metadata corpus, scope/authority worksheet and an expected-fact oracle. Synthetic fixtures establish only the tested subset; licensed real artifacts and authorized environment validation precede support claims.

| Gate | Required evidence before capability publication |
| --- | --- |
| EXT-01 Contracts | Provider descriptor/effects and versioned normalization/payload profile validate; no unknown 0.1.0 fields/kinds; backward reader/API/MCP/export compatibility and migration tests |
| EXT-02 Identity/accounting | Same names across systems/datasets/libraries/schemas remain distinct; aliases and nested scopes explicit; stable IDs/reanalysis preserve history; every supplied artifact has an accounted status |
| EXT-03 Fidelity | Encodings, record/column/continuation rules and text exports tested; raw bytes/hashes preserved; included/replaced/generated spans map to declared coordinate space; inaccessible/missing material explicit |
| EXT-04 Bounded semantics | Platform-specific expected declarations/calls/conditions/actions/CFG/data flow; unsupported syntax/dynamic references/missing metadata prevent unsupported conclusions; confirmed/inferred/reviewed remain separate |
| EXT-05 Safety/authority | Traversal/escaping symlinks/oversized/decompression/deep-include inputs rejected; no commands/compiler/runtime/job/transaction execution; authorization failures and remote filtering distinguished from absence |
| EXT-06 Knowledge parity | Exact/keyword/graph/evidence/context results match canonical fixtures; token budget and citation validation; REST/MCP parity, linked exports/schema/hash checks and explicit full-source permission |
| EXT-07 Provenance/recovery | Cancellation/crash/restart, partial collection, duplicate import and changed source preserve immutable occurrences/revisions; invalidated approvals and source policy enforced |
| EXT-08 Real profile validation | Authorized representative source/metadata for each exact platform/dialect/tool release; target-owner expected facts and authority tests; parser/adapter licenses inventoried; documented remaining unsupported coverage |

Release acceptance requires a separate result for every advertised platform/dialect/operation. A pass for offline COBOL does not imply z/OS collection, CICS behavior, JCL evaluation or another vendor/version. Use an explicit capability ledger with operation/profile, fixture and real-corpus hashes, authority scope, tool pins, date, reviewer and limitations. Unrun gates stay unrun; unknowns cannot become verified through an LLM.

## Review record

ECC planner handoff completed. Delegated architect execution was blocked by the account usage limit; the primary agent prepared and checked this design against official references and canonical contracts. Independent ECC code review approved the documentation with zero required findings. The capability JSON and four proposed identity examples pass JSON Schema validation against canonical 0.1.0 definitions; all 70 local links across these milestones and shared pointers resolve. These checks establish documentation consistency only. No provider implementation, proprietary extraction, live access or platform compatibility validation occurred.
