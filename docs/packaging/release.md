# Source release candidate and product packaging

Updated 9 October 2026. LAIP extracts evidence-linked business-rule candidates and system context from supported source/metadata and exports that context for downstream AI-assisted modernization. The current runtime is a bounded offline, single-user localhost prototype; AI/embeddings default off. Package integrity is distinct from complete extraction and human review.

## Retention and cleanup

The pre-cleanup inventory and independent review passed. Canonical architecture, contracts, ADR, plans, source, tests, synthetic fixtures, licenses and unique historical validation remain. Three generated execution guides were hash-verified into a private archive before removal from the main docs path. Older media work was preserved privately; optional current editable film/assets are in `media/demo/`. Installed media dependencies and 14 regenerable cache/OS outputs were removed. Application dependency installs remain local and are excluded from source packaging. Credentials and database/artifact volumes were preserved.

Private banking source-dependent tests, golden metadata, reports, source-derived plans and screenshots stay local and are excluded from the public candidate. Synthetic contract/persistence/consumer/version tests remain. Public links to withheld material point to the [boundary summary](../private-validation.md). Optional media links in the source candidate point to the [demo description](../demo.md). These public projections record original and projected hashes; they do not rewrite evidence history.

## Repeatable packaging

```sh
make docs-check
make package-release
```

The candidate is written beneath `.local/release-candidate/`, with a deterministic ZIP, per-file manifest and SHA-256 checksum. It uses an explicit public allowlist/exclusion policy, rejects symlinks and environment files, excludes generated dependencies/caches and leaves local private state untouched. Read the manifest before distribution. No repository was initialized, pushed or published.

## Verification

The clean extracted source candidate completed `make install check`: **676 backend tests passed**, with **86.95% aggregate branch-aware coverage**, above the unchanged 80% gate. Ruff lint/format, strict Mypy across 56 modules, generated API types, all 8 frontend policy tests, TypeScript and the production build passed. No private source-dependent tests were included or counted as private acceptance.

The initial clean-run gate exposed 20 pre-existing typing errors in schema/API helpers. Minimal return annotations/casts fixed them without weakening checks; the complete clean run above includes those changes. An earlier attempt also lacked npm on the escalated shell PATH; the successful run explicitly selected the installed Node 22.23.2 toolchain. Backend development used Python 3.12.11; Docker pins Python 3.12.15. Neither earlier attempt is recorded as a pass.

Public authored Markdown links pass with zero errors; withheld private documents and retained vendor notice Markdown are outside that navigational check. Packaging boundary tests pass, and repeated generation from unchanged inputs yields byte-identical archives. Independent review verified file hashes, private exclusions, preserved schema/notices and hashed historical archives. One private-derived ADR example was replaced by independent synthetic names; the original remains privately hash-verified.

Actual test-database restart/repeat-analysis passed: immutable history, stable entity identities and checkpoint deduplication survived. Fresh Compose build/startup in an isolated project on loopback port 3032 passed all four service health checks and the web/BFF smoke; private services/storage and AI-off configuration passed rendered Compose checks. Build contexts transferred approximately 3.00 MB backend and 151 KB frontend, excluding installed dependencies/caches and private test data.

The updated HTTP harness passed synthetic import, partial analysis, source/evidence, immutable correction, retrieval and hashed export. The initial workflow attempt correctly rejected a correction sent with the wrong transport version: correction commands remain 0.1.0 while read/export negotiation and persisted new revisions use 0.2.0. The harness now preserves that distinction. No production validation was weakened; the failed attempt is not counted as a pass.

The browser separately loaded/imported eight synthetic artifacts, analyzed them to a partial snapshot, inspected a field-validation rule and its exact original source line, and created/downloaded a source-excluded export. The downloaded schema-0.2.0 package passed independent schema, cross-link and hash validation (301 files; SHA-256 `a39bc96be36ffafabcf245812425762c19318cec75992a703ebdb4cce36a2b87`). AI remained disabled. This is browser workflow acceptance, not screen-reader, visual accessibility or performance certification.

A separate HTTP-produced [synthetic AI-context example](../examples/README.md) ships with the source candidate: 264 records, 301 package files, original support/uncertainty and immutable correction history retained. Full source is excluded. This sample is generated from public synthetic inputs, not private banking files or live objects.

All executable/runtime inputs in the final source candidate are compared against the tested extraction. Subsequent updates are documentation/examples, the guarded harness correction, and CI naming; the final harness is verified against the running validated services. The final source manifest records exact selected/transformed hashes and exclusion policy. No clean-run claim is made for excluded private corpus tests. The validated local images were also applied to the existing app on port 3030; startup smoke passed, with existing credentials and database/artifact volumes preserved.

## Distribution gates

- The owner has not selected a project license. Do not describe this candidate as open source or grant reuse rights by implication.
- [Dependency notices](../dependency-notices.md) inventory locked runtime candidates and locally available notices. Missing platform packages, LGPL/bundled native library obligations and exact Linux container/base-image contents need review before their distribution. This source candidate is not a container release or final-image SBOM.
- Private corpus redistribution rights remain unconfirmed; candidate exclusions are mandatory. Evidence snippets must be reviewed before AI ingestion/sharing.
- Remote GitHub CI, actual private-model semantics, screen-reader conformance, live IBM i release/PTF/authority/compiler compatibility, load performance and enterprise controls are not certified by this packaging exercise.

These gates concern publication/support claims. Local source cleanup and a reviewable source candidate can be completed while they remain open.
