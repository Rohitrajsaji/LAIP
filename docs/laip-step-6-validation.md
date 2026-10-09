# LAIP step 6 — offline ZIP and configured local imports

Implementation on 2026-10-07 for [execution-plan step 6](plans/laip-execution.md#step-6). This adds internal service interfaces for supplied ZIP streams and operator-configured local source directories/checkouts. Public REST upload routes, UI import flows and automatic provider dispatch are not wired by this step. Application modernization and code translation remain outside scope.

## Input and execution boundary

`prepare_zip` reads an inert archive stream. `prepare_local` accepts a source-reference key resolved through an operator-owned root mapping; imported manifests cannot select arbitrary filesystem roots. A checkout means an existing configured directory. Readers do not clone, fetch, install dependencies, invoke Git/hooks, run setup, import modules or execute source.

Readers reject traversal, absolute paths, conflicting file/directory entries, ambiguous case/Unicode spellings, escaping or internal symlinks, special files and unsafe ZIP path/link metadata. Files are read through no-follow descriptor-relative traversal. Excluded entries retain an explicit status without retaining their bytes; unsafe symlinks and special entries still fail when excluded. Default exclusions include Git/dependency directories, `.local`, environment files and common private-key files. Operator exclusions are added to these defaults.

Default ceilings are 10,000 entries, 256 MiB aggregate bytes, 16 MiB per file, 256 MiB compressed archive, compression ratio 1,000, path depth 64 and a shared 120-second preparation deadline. Directory entries count toward limits. Manifest `max_files` and `max_bytes` can lower operator ceilings, never increase them. Cancellation and deadline checks run during reading, between artifact preparation operations and after the final sealed-plan write. Bounded operations finish at a checkpoint; expiration prevents accepting the input. Decoded bytes, origin segments and metadata record counts have additional aggregate limits.

## Versioned manifest and evidence

The existing canonical `ImportManifest` version `0.1.0` supplies the authorized system namespace, display name, library list, artifact paths, language/dialect, encoding, source-member/object identities, collection provenance, application-membership evidence paths, exclusions and limits. Validation rejects duplicate JSON keys, invalid canonical numbers, unexpected properties, cross-namespace identities and dangling memberships. SourceMember identity is the supplied `[library, source_file, member]` tuple; names in different libraries remain distinct. Distinct source members may explicitly associate with the same compiled object. A library list describes resolution scope and is independent of inventory membership.

Raw artifact and manifest bytes are stored unchanged in private content-addressed storage. Strict decoding produces a separate UTF-8 view and an origin map using decoded UTF-8 byte offsets and original raw byte ranges. Newlines are preserved. UTF-16 matching BOM bytes remain in the raw artifact and are excluded from the decoded view with the correct raw offset. UTF-8, ASCII, explicit UTF-16 little/big endian, EBCDIC cp037/cp500 and bounded CCSID aliases are supported; unknown or invalid encodings remain explicit `unsupported` records without replacement decoding or invented coordinates. Empty views retain a valid zero-length map. Segment or decoded-size exhaustion returns `partial` without a misleading map.

[Metadata envelopes](contracts/0.1.0/offline-metadata.md) cover inventory, schema, binding, job and DSPPGMREF-style supplied records. These are descriptive versioned JSON exports with opaque canonical record objects. Original bytes and supplied fields remain available privately; no metadata record establishes live IBM i state or verified runtime behavior. SourceMember entities are created only from explicit manifest identities. Compiled-object and application associations remain supplied manifest data for later graph analysis.

Every encountered or declared input has an explicit processing status: `decoded`, `imported`, `unsupported`, `partial`, `failed` or `excluded`. Missing declared paths have `MISSING_SOURCE`; undeclared supplied paths have `UNMANIFESTED_ARTIFACT`. Invalid metadata retains its raw bytes with a failed status. Evidence records state offline authority and limitations; review is not automatically verified.

## Durable publication and replay

Preparation performs filesystem work before durable job publication locks. `register_import` records the immutable manifest, run basis and sealed input plan; source and decoded blobs are registered as durable input references. `restore_import` verifies and reloads that accepted plan and private bytes, so a retry does not reread a changed or removed caller directory. `persist_import` performs only database work inside the job's fenced checkpoint callback. Exact replay retains artifacts, evidence and status history without duplicates; conflicting replay fails.

Migrations `006`–`008` add namespace-enforced immutable import entries, one import-job owner with durable import lifecycle states, and immutable accepted input plans/blob references. These follow the existing transaction/checksum migration protocol. Applied SQL must not be edited; back up database and artifact volumes before an incompatible upgrade. No destructive downgrade is supplied. Preparation may leave unreferenced immutable blobs after rejection, following the existing storage/retention protocol.

## Recorded validation

- TDD baseline: manifest and decoder tests initially failed because the modules did not exist. The shared compiled-object regression failed before relaxing only object-identity duplicates.
- Focused manifest/decoder tests: 42 passed. The earlier 41-test focused run measured 95.51% combined branch coverage for those modules; Ruff and strict mypy checks passed after the final regression.
- Root-owned reader and persistence fixture tests exercise ZIP/local imports, raw bytes, exact mappings, explicit statuses, fenced publication, replay, restart from accepted private inputs and import lifecycle outcomes. Full suite: **185 passed**, **93.69%** combined statement/branch coverage; Ruff, formatting and strict mypy passed. Frontend type checks, all three tests and production build passed.
- Independent code review: **approved**, with no remaining findings after correcting ancestor exclusions, lifecycle readiness and frozen restart inputs.
- Independent security review reported no additional findings after the shared preparation deadline and final-write cancellation regressions passed. The reviewer’s subsequent final-summary turn hit the account usage limit; the completed review message and independently run regressions provide the recorded evidence.
- Docker PostgreSQL clean-schema migrations 001–008 passed through the full suite and acceptance runners. The existing application upgraded through 008, rebuilt successfully and passed the four-service health/BFF smoke check at `http://127.0.0.1:3030/`; loopback/private service boundaries and AI-off configuration passed.
- `make import-check` passed for real ZIP and configured local fixtures: exact raw bytes, lossless source maps, restore through a new database connection after removal of the original source, and unique fenced checkpoint/repository replay.
- `make restart-check` passed after restarting the isolated Docker database, preserving existing analysis history and checkpoint uniqueness.

No remote CI run or live IBM i compatibility is claimed. No new Python/npm dependency or lockfile change is required by these import modules. The fixture material is synthetic LAIP test data and contains no enterprise source.

## Repeatable checks and source locations

`make check` runs locked backend/frontend checks and starts the isolated Docker PostgreSQL instance. `make restart-check` exercises durable history across a real database restart; `make test-down` removes only that test stack. `make compose-check` verifies local deployment boundaries. `make import-check` runs the ZIP/local durable-input acceptance runner. The test stack was stopped after verification; application services remain running.

- [Manifest and metadata validation](../backend/src/laip/import_manifest.py)
- [ZIP/local readers](../backend/src/laip/import_readers.py)
- [Strict decoding and source maps](../backend/src/laip/source_decode.py)
- [Preparation, durable registration, restoration and persistence](../backend/src/laip/source_import.py)
- [Synthetic fixture documentation](../backend/tests/fixtures/import/README.md)
- [Manifest tests](../backend/tests/test_import_manifest.py), [decoder tests](../backend/tests/test_source_decode.py), [reader tests](../backend/tests/test_import_readers.py), [persistence tests](../backend/tests/test_source_import.py)
