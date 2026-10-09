# REA reuse assessment for LAIP

**Assessment date:** 2026-10-07  
**Assessed source:** [`morluto/rea` commit `bc2cd8b874e115eee446860043758a80bd583ad0`](https://github.com/morluto/rea/tree/bc2cd8b874e115eee446860043758a80bd583ad0)  
**Package metadata:** `rea-agents` 4.1.0, Node.js ESM, MIT  
**Scope:** Reuse opportunities and constraints for LAIP’s IBM i inventory and evidence workflows. Application modernization and code translation are out of scope.

## Decision summary

Treat REA as a reference architecture and, where useful, an independently run analysis tool. Do not make LAIP depend on REA source internals or put IBM i source parsing inside REA’s binary-analysis provider model. REA’s supported integration surfaces are its CLI and MCP server; package metadata does not declare a stable library export. Its reference-source import command is CLI-only and returns a hashed inventory graph, not file contents or parsed RPG structures.

For LAIP, use a small, typed adapter around explicitly selected IBM i collection methods and source parsers. Keep the adapter responsible for translating source/system observations into LAIP’s own records, preserving the original method, object/member identity, digest, authority context, and limitations. Reuse the REA evidence concepts (authority, confidence, provider identity, limitations, and links) as design input, while LAIP owns its source-aware schema and durable storage.

Historical step 1 parser decision: do not implement an RPG grammar from scratch yet, and do not adopt the old ANTLR/Java `rpgleparser` as the default. Run a focused compatibility spike against the parser modules in Code for i’s `vscode-rpgle` at pinned commit `b357b356fbcc598de7f7e049d4f114f6b6f18408` (MIT). Its RPGLE/OPM parser and source positions make it the leading candidate, but it is part of a VS Code extension rather than a published parser API. Isolate or wrap the smallest parser slice and validate it against LAIP fixtures before choosing vendoring, a sidecar, or a fallback grammar. Treat DDS as a separate parser decision; the reviewed material does not establish a full semantic DDS parser.

Current decision update (2026-10-08): the user's step-8 Python/Lark selection superseded that prerequisite for the bounded free-format implementation; see the [recorded decision](rpgle-parser-decision.md). No upstream spike failure is claimed. Candidate findings below remain historical research, and the [step 17 roadmap](laip-step-17-language-roadmap.md) governs any future extension evaluation; no upstream grammar has been adopted by this update.

## What REA provides

REA is a layered TypeScript application. Its architecture separates domain types, public contracts, application workflows, MCP/server adapters, and provider integrations. CLI and MCP share application operations; binary-bound provider selection and session lifecycle are handled above the provider-neutral evidence records. Provider adapters can represent different analysis engines, capabilities, execution effects, and target-bound profiles.

This is useful as a boundary and provenance pattern. LAIP can follow the same separation between collection/parser implementations and normalized analysis records, but IBM i source, libraries, members, system catalog queries, and RPG/DDS syntax are not binary targets in REA’s current model. REA has no IBM i or RPG analysis implementation at the assessed commit. Its architecture should inform LAIP rather than become LAIP’s core dependency.

The package exposes `rea` and `rea-agents` executable bins and an MCP entry point. `package.json` has no `exports` field, so there is no declared stable Node library API for LAIP to import. Prefer a process adapter to a documented CLI operation where REA functionality is needed; use MCP when LAIP wants the same tool contract exposed to an agent. Avoid imports from `src/` or `dist/` internals.

### Reference-source import boundary

`import-reference-source` is implemented as a CLI utility command and is not part of the MCP tool registry. Its application workflow hashes and classifies a local source tree, records manifests, relationships/import hints, exclusions, errors, and limitations, and labels the resulting graph as historical reference evidence. Parseable language labels are currently JavaScript, TypeScript, JSX, and TSX. The graph does not return source bytes or source text, so LAIP still needs its own controlled file reader and parser pipeline.

The importer’s handling of symlinks, exclusions, and non-execution is useful input for LAIP’s file-ingestion policy. Its documented limitation is that portable Node APIs cannot fully eliminate pathname races between validation and open; treat this as a known boundary rather than a guarantee of descriptor-relative traversal. The importer is documented for Linux and macOS, not native Windows. It does not run source hooks or invoke network access during the import operation.

## Adapter and parser decisions

### Adapter boundary

Implement a LAIP-owned adapter with two sides:

1. **Collection side:** explicit providers for supported IBM i sources (for example, IBM i services/SQL and controlled source-member export). Each reports its method, release/context, required authorities, returned fields, and coverage/limitations. Do not assume a query is available or authorized on every IBM i release.
2. **Normalization side:** convert provider output and parser observations into LAIP records. Preserve provider/version, collection time, object/library/member identity, source hash, source coordinates, authority context where known, and raw-result linkage. Fail or mark partial when a source is unavailable; never silently fill gaps from another method.

Keep CLI and any future MCP interface as thin transports over this application boundary. REA can be invoked as an optional external process for compatible reference/binary analyses, but it is not the IBM i provider. Do not couple LAIP’s schema to REA `BinarySession`, `BinaryTarget`, or private application modules.

### Evidence mapping

REA’s strict `Evidence` record provides useful fields for provider identity, subject digest, predicate, operation/parameters, raw and normalized results, confidence (`observed`, `derived`, `inferred`), authority (`shipped-artifact`, `controlled-replay`, `historical-reference`, `external-service`, `analyst-inference`), execution environment, limitations, locations, and evidence links. Evidence IDs are content-derived and validated. The subject’s semantic projection avoids treating a local path as identity.

LAIP should preserve these distinctions, but define an IBM i/source-aware schema rather than forcing records into REA’s current binary envelope. Required LAIP additions include an explicit `collected_at`, IBM i system/release and library/object/member identity, source path or source-member locator, source digest, line/column or statement span, collection/parser run ID, and review/verification state. REA’s current evidence locations cover artifact paths and binary addresses/offsets, not source line/column spans; its core record has no collection timestamp. Keep evidence certainty distinct from human review status.

### Parser choice

| Candidate | Assessment | Decision |
| --- | --- | --- |
| Code for i `vscode-rpgle` parser, pinned at `b357b356fbcc598de7f7e049d4f114f6b6f18408`, package `0.33.10-dev.0`, MIT | Source contains RPGLE and OPM parser modules, token/statement handling, include/external-fetch seams, and line positions. The project is a VS Code extension with `main: out/extension`, no dedicated published parser export, and IDE-oriented dependencies/cache. A parser tree is not by itself a business-rule or control/data-flow IR. | **Lead candidate; spike before adoption.** Test fixed/free formats, continuation, embedded SQL, includes, error recovery, and coordinates using representative LAIP fixtures. If feasible, wrap an audited minimal source slice behind a LAIP parser interface; retain upstream MIT notices and pin the exact commit. |
| `rpgleparser` ANTLR grammar, pinned at `1cd596ded2edff26f7f82639d5ca7ee51ebbfeb9`, Apache-2.0 | Older Java grammar with ANTLR 4.5.3, Java 1.6 target and jt400 8.7 dependency. Lexer grammar includes Java actions and output calls. This introduces legacy runtime/tooling and isolation work. | **Do not select as default.** Reconsider only if the maintained candidate fails a documented conformance requirement and a bounded port proves maintainable. |
| Custom grammar (including a Python/Lark fallback) | Full RPGLE fixed/free-format behavior is a large compatibility surface; a narrow grammar risks false confidence and lost source coordinates. | **Defer.** Use only for a declared subset after parser reuse fails the spike; report unsupported constructs explicitly. |
| DDS | The reviewed candidates do not provide evidence of full semantic DDS coverage. Syntax-highlighting grammars or narrow screen-file parsers are not equivalent to a repository-wide analyzer. | **Separate decision and search.** Inventory the required DDS types and evaluate a parser against fixtures before implementation. |

The parser spike is a decision gate, not an assumption that the VS Code extension can be linked directly as a library. It should record test corpus provenance, supported constructs, diagnostics, coordinates, include resolution behavior, performance, and notices. LAIP should produce its own stable intermediate representation after parsing, with provenance on every derived finding.

## IBM i source and metadata coverage

REA’s current provider portfolio targets native binaries and application artifacts, not IBM i objects, source members, RPGLE, CL, DDS, Db2 for i catalogs, or IBM i authorization semantics. LAIP therefore needs new providers and fixtures for these areas. At minimum, make support explicit for system inventory, source retrieval, program/object references, binding/module metadata, and source parsing. Define a release matrix and least-privilege requirements before relying on a system service.

IBM documentation reviewed during this assessment shows why IBM i observations need method-specific authority and freshness labels:

- [`DSPPGMREF` on IBM i 7.5](https://www.ibm.com/docs/en/i/7.5.0?topic=d-display-program-references) reports references recorded when an object is created or updated. The resulting relationship data can be stale, including references to objects that have since been deleted, and is not equivalent to a fresh source parse.
- [`OBJECT_STATISTICS`](https://www.ibm.com/docs/en/i/7.4.0?topic=services-object-statistics-table-function) has authority requirements for complete details, including object/read authority and the applicable function usage authority.
- [`BOUND_MODULE_INFO`](https://www.ibm.com/docs/en/i/7.4.0?topic=services-bound-module-info-view) documents library/object authority requirements. Availability and behavior must be checked against the target release.
- IBM i program information is also release- and authority-scoped; consult the target release documentation and verify access in the intended collection context before adding it as a required source.

These services are complementary observations, not substitutes for source parsing. Store the collection method, release, query/service identity, collection time, authority/coverage status, and known staleness semantics with each result. This assessment has not established an exhaustive IBM i release compatibility matrix or run queries against a live IBM i system.

## Dependency and notice inventory

REA’s own project license is MIT. Its `package-lock.json` is lockfile version 3 with 380 package entries; every entry declares a `license` metadata value. Metadata-family counts in this lockfile are: 308 MIT, 30 Apache-2.0, 12 MPL-2.0, 15 ISC, 7 BSD-3-Clause, 5 BlueOak-1.0.0, 1 0BSD, 1 `(MIT AND BSD-3-Clause)`, and 1 `(MIT AND Zlib)`. These counts total 380. They are an inventory of declared package metadata, not a legal determination or proof that all required notice texts are present.

Direct runtime dependencies identified in `package.json` include:

- MIT: `@babel/parser`, `@babel/types`, `@clack/prompts`, `@electron/asar`, `@jridgewell/trace-mapping`, `@lydell/node-pty`, `@xterm/addon-serialize`, `@xterm/headless`, `cross-spawn`, `ignore`, `incur`, `isomorphic-git`, `jsonc-parser`, `pino`, `plist`, `write-file-atomic`, `ws`, and `zod`.
- Apache-2.0: `@modelcontextprotocol/client`, `@modelcontextprotocol/server`, `canonicalize`, and `playwright-core`.
- BSD-3-Clause: `@zip.js/zip.js`, `diff`, and `smol-toml`.
- ISC: `semver`.

The lockfile also covers development dependencies; LAIP should decide whether its own distribution includes development tooling before aggregating notices. REA’s `third_party/README.md` separately records these optional upstream analyses and their source licenses: jadx-headless-mcp v0.7.1 (Apache-2.0), Binwalk v3.1.0 (MIT), and Unblob 26.6.4 (MIT). The repository also has a `ghidra-nativeaot` submodule whose license is not recorded in that inventory; verify it at the pinned upstream source before any redistribution. These submodules are not ordinary bundled npm runtime dependencies; REA documents that its normal package/build does not require the optional engine source repositories.

**Notice work remaining before LAIP redistribution:** produce a distribution-specific SBOM/notice bundle from the exact artifacts LAIP will ship, verify license texts and attribution obligations for included direct and transitive packages, and separately verify any vendored parser slice and optional upstream engines. Re-run the inventory whenever versions or packaging change. This report does not authorize or certify third-party redistribution.

## Reuse disposition and follow-up

| Area | Disposition |
| --- | --- |
| CLI/MCP split and provider-neutral application boundary | Reuse as architecture guidance. Keep LAIP’s CLI/MCP transports thin. |
| REA CLI/MCP as an optional tool | Integrate only through documented external contracts for compatible operations; pin/version the executable and capture its identity and output provenance. |
| REA internal TypeScript modules or binary-session/provider contracts | Do not import into LAIP’s core; these are not a stable public library surface and encode binary-target assumptions. |
| REA reference importer | Reuse its inventory and safe-ingestion lessons; do not treat it as source retrieval or an RPG parser. |
| Evidence semantics | Adapt the provenance vocabulary; extend it for IBM i identity, collection time, and source coordinates. |
| RPGLE parser | Run the pinned Code for i parser spike before choosing vendoring/sidecar/fallback. |
| DDS parser | Investigate and validate separately against the target DDS types. |
| Dependency notices | Inventory is source-backed at metadata level; exact shipped artifacts still need SBOM and notice-text review. |

This assessment meets step 1’s acceptance criteria: it records the adapter and parser decisions, cites primary REA/IBM source facts, and inventories dependency license metadata and known notice gaps. No application modernization or source translation is proposed.

## Source trail

Primary REA sources inspected at the pinned commit include `AGENTS.md`, `docs/architecture.mermaid`, `package.json`, `package-lock.json`, `src/domain/evidence.ts`, `src/application/AnalysisProvider.ts`, `src/application/ReferenceSourceImportTypes.ts`, `src/application/ReferenceSourceReader.ts`, `src/cli/utilityCommands.ts`, `src/server/createServer.ts`, `README.md`, and `third_party/README.md`. Parser candidates were inspected at the exact commits listed above, including the Code for i package metadata/parser modules and the ANTLR grammar repository’s license/build/grammar files.
