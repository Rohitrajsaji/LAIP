# LAIP step 5 — pinned REA adapter

Implementation and local validation on 2026-10-07 for [execution-plan step 5](plans/laip-execution.md#step-5). REA is an optional external tool pinned to commit `bc2cd8b874e115eee446860043758a80bd583ad0`, package `rea-agents` 4.1.0. No application modernization or code translation is included.

## Adapter behavior

`ReaAdapter.describe()` returns a schema-validated provider descriptor. `inventory(SealedDirectoryRef, cancellation_event)` accepts a sealed relative-path/digest/length manifest and an operator-trusted executable profile. The profile pins the executable, dispatcher and entire loaded runtime/dependency bundle by SHA-256, with the required source revision and version. These trust settings must never come from imported manifests or API requests. The caller provisions and audits the external runtime; the adapter never downloads, installs or builds it.

The only invoked arguments are `--version` and `import-reference-source <private-stage> --format json`. The executable argument vector is fixed by trusted operator configuration; no shell, arbitrary CLI operation or input-controlled option is exposed. The child environment excludes inherited Node injection options, tokens and service credentials. Stdout and stderr share a byte limit; errors expose typed codes instead of stderr or paths. Cancellation and the shared deadline cover runtime hashing, staging and subprocess processing. Process-group cleanup includes descendants after the leader exits.

The adapter reads source files through no-follow descriptor-relative traversal, rejects symlinks/special files/traversal/unmanifested entries, checks exact input digests, and creates a private read-only staging tree. It verifies the private bytes again after execution and removes staging on success/failure/cancellation. Defaults: 120 seconds total, 16 MiB combined output, 10,000 files, 16 MiB per file and 256 MiB input. Portable read-only staging does not sandbox a trusted executable running as the same OS user; REA's own pathname-race limitation is retained.

Output validation checks the pinned HistoricalSourceGraph shape, strict properties/types, normalized paths, ordering/uniqueness, hashes, semantic root commitment, derived language/manifest indexes and relationship closure. Returned file hashes/lengths must match sealed inputs; omissions require explicit exclusions, and partial/unknown observations stay visible. Duplicate JSON keys and invalid encoding are rejected.

## Evidence and durable publication

The pinned operation returns a historical-reference graph with a `root_sha256` commitment, **not upstream Evidence records**. LAIP retains that real upstream identity and the exact raw stdout bytes, including whitespace and all upstream provenance/limitations. It creates its own observed historical-reference evidence wrapper that points to the private raw-output artifact. REA's CLI emits `importer_version: null`; this is preserved in raw output, while LAIP's provider reference independently records the checked package version and pinned source revision. Static relationships never establish runtime execution or RPG semantics.

`prepare_inventory` revalidates the result and publishes immutable bytes before database locks are acquired. `persist_inventory` performs DB-only artifact/evidence insertion inside the durable job's fenced checkpoint callback. Collection time and masking-policy version must remain frozen across retries. Exact retries retain artifact/evidence IDs without duplicate history; incompatible providers or conflicting payloads roll back the transaction. A failed publication may leave an unreferenced immutable blob, following the established storage protocol.

The adapter does not complete an analysis run, publish a snapshot, create IBM i entities from filenames, infer source-to-object relationships, execute providers automatically, or mark human review verified. Provider handlers and public import/analyst workflows follow later steps. The default application image does not install Node or REA; missing external runtime is explicit `PROVIDER_UNAVAILABLE`, never an automatic setup action.

## Recorded validation

- Tests were written before the new persistence wrapper and adapter; initial runs failed on missing modules. Runtime-hash cancellation/deadline regressions also failed before their fix.
- Full locked checks passed: 90 backend tests, 95.07% combined coverage, lint, formatting and strict typing; three frontend guard tests, TypeScript checking and production build.
- 30 adapter tests cover malformed graphs, changed input/runtime hashes, version mismatch, missing provider, unsafe paths/symlinks, private staging, cancellation, timeout, output flooding, environment injection and process-tree cleanup. Directory enumeration is bounded before sorting, with deadline/cancellation checks. The provider descriptor matches the canonical schema.
- Three Docker PostgreSQL integration tests verify exact-byte/upstream-ID preservation, duplicate-free replay, provider mismatch rollback, and filesystem work outside the DB transaction followed by fenced checkpoint publication.
- `make rea-check` executed the **real pinned CLI through the implemented adapter**, imported its actual result into a clean isolated Docker PostgreSQL schema, verified exact artifact bytes/upstream ID/replay, and removed the test schema afterward. The five-file synthetic fixture includes a package preparation hook and a module that would write an execution marker if run. The trap writes relative to its own module URL and then throws; read-only staging would reject such a write. Successful real import, sealed-stage post-verification, the fixed CLI operation, and independent audit of the upstream read/hash/Babel-parse path establish no execution. Marker absence alone is not used as proof; cleanup can remove working-directory markers. No setup operation was invoked.
- Docker application rebuild, Compose boundary validation and real localhost HTTP smoke passed at http://127.0.0.1:3030, with four healthy services and a successful migration service. AI remained off.
- Code review approved after correcting filesystem work under fenced DB locks and making runtime hash verification cancellable and deadline-aware. Security review approved after correcting unbounded directory enumeration; two regressions verify the entry cap and cancellation during scanning.

The existing Starlette test-client deprecation and Node module-type inference warnings remain nonblocking. No remote CI run is claimed; this directory has no Git metadata.

The external CLI was built from the exact clean checkout using its locked npm dependencies with lifecycle scripts disabled, followed by direct TypeScript compilation. REA setup/build hooks, optional binary engines, Git hooks and fixture package scripts were not run. This was test preparation by the operator; no build/install path exists in the adapter. The recorded graph is relocation-independent and labels the result partial, retaining REA's limitation. Tests establish offline boundary behavior, not live IBM i compatibility or enterprise readiness.

## Repeatable checks

```sh
make check
LAIP_REA_ROOT=/absolute/audited/rea-checkout \
LAIP_REA_NODE=/absolute/audited/node \
make rea-check
```

The external checkout must already be built, at the exact revision, with unchanged tracked files and audited compiled/dependency bytes. `scripts/test_rea.py` pins the supplied Node, dispatcher, package metadata, compiled runtime and regular dependency files, rejects symlinked modules, and uses only the bundled synthetic source fixture. It does not fetch/install/build/setup anything. Do not repurpose this acceptance runner to accept user-uploaded executable paths.

`make rea-check` starts the isolated test PostgreSQL instance on loopback port 55433, uses a unique temporary schema, and leaves application data alone. `make test-down` removes the isolated test stack. CI's ordinary checks validate the captured graph and adapter behavior; the real CLI acceptance command requires an explicitly provisioned external runtime and is not silently skipped or automatically installed by CI.

## Dependencies and notices

No new LAIP Python/npm dependencies or lockfile changes were required. REA source/runtime/dependencies remain external and are not vendored into the application or Docker image. The fixture source is synthetic LAIP test material; `inventory.json` is exact output from the pinned CLI. REA's MIT license and dependency/optional-engine inventory remain documented in [REA assessment](rea-assessment.md). Redistribution of an external runtime still requires an artifact-specific notice/SBOM review; this implementation adds no engine distribution.

## Source locations

- [CLI adapter and validator](../backend/src/laip/rea_adapter.py)
- [Artifact preparation and evidence persistence](../backend/src/laip/rea_import.py)
- [Adapter regressions](../backend/tests/test_rea_adapter.py), [persistence regressions](../backend/tests/test_rea_import.py)
- [Actual CLI acceptance runner](../scripts/test_rea.py), [captured fixture graph](../backend/tests/fixtures/rea/inventory.json)
