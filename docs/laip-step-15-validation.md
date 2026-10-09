# LAIP step 15 validation

Last validated: 2026-10-08. Contract/schema version: 0.1.0.

Status: offline MVP acceptance passed. The actual browser journey, security/recovery checks and pinned REA fixture validation are recorded below; live IBM i validation remains outside these results.

## Synthetic application and expected behavior

The labelled customer-validation fixture supplies eight artifacts: CLLE entry source, free-format RPGLE sources, physical/logical/display DDS definitions, a same-named program in another library, and metadata. It retains the order application fixture and adds customer validation with a deliberately missing include. Explicit metadata and source references exercise confirmed versus inferred application membership, qualified `DEMO/ORDER` versus `OTHER/ORDER` identities, ambiguous unqualified calls and dynamic calls.

The resulting analysis is partial. Dynamic and ambiguous targets remain unresolved with evidence. Missing customer include support sets `conclusions_allowed: false` and prevents complete business conclusions. Supported customer assignment actions still appear as inferred, pending-review candidates with empty conditions and explicit limitations. Partial analysis must not be described as either fully verified behavior or an absence of all supported facts.

Raw artifacts, source spans and include-origin mappings remain available through evidence inspection. Repeated analysis compares canonical entity identities, preserves immutable observations and review history, and does not duplicate a run checkpoint. Corrections create revisions rather than rewriting prior rules; changed evidence requires new review.

## Checks performed

| Check | Recorded result |
| --- | --- |
| Synthetic fixture and durable HTTP workflow | Four focused tests passed, including import, analysis, retrieval, export validation and repeated analysis. |
| Security/recovery regressions | 118 tests passed across archive/source import, context/private semantics, rule history, jobs, read API, admission and analyst jobs. |
| Actual database restart | `scripts/test_restart.py` passed after restarting the isolated Docker PostgreSQL service: history preserved, one checkpoint, stable entities, new support awaiting review. |
| Real pinned REA CLI | `scripts/test_rea.py` passed against clean commit `bc2cd8b874e115eee446860043758a80bd583ad0`; actual source inventory retained hashes, upstream IDs and raw graph, and PostgreSQL replay passed. |
| Partial run summary | A browser-discovered omission received a failing acceptance assertion, a bounded diagnostic/snapshot-summary fix and passing focused regression checks. |
| Browser workflow | Actual import, partial analysis, cancellation, repeat analysis, source/evidence inspection, immutable correction, keyword retrieval and source-excluded export download passed. See the [observed browser record](laip-step-15-browser-validation.md) for artifact verification and screenshots. |
| Full backend suite | 619 tests passed with two warnings in 450.46 seconds; coverage was 90.94%, before the final entity-status correction. |
| Entity-status correction | Ten focused MVP/durable-job tests passed after the correction in 104.58 seconds; Ruff and mypy passed. The full suite was not rerun after this final correction. |
| Final static/frontend checks | Ruff formatting checked 103 files; mypy checked 48 modules; generated API types were current. All eight web tests, TypeScript checks and the production build passed. |
| Downloaded package | Independent schema 0.1.0, links, hashes and byte-length checks passed for 263 files (1,057,641 bytes); full source was excluded. |
| Browser keyboard/error states | Enter activated the skip link and focused the content target; a visible 404 alert recovered to valid source inspection. This is limited keyboard/error evidence, not screen-reader certification. |

The real REA check used the previously built trusted runtime at `/private/tmp/laip-rea-upstream` and Node 22.23.2. It did not install dependencies, set up REA engines, run application code or contact IBM i. This proves adapter behavior for its actual pinned source-inventory fixture, not native IBM i discovery.

## Browser and status corrections

The browser cancelled an actual queued analysis run and retained its history, then completed a new partial run. Run inspection exposed the snapshot identity, `INCLUDE_MISSING`, `UNKNOWN_EFFECTS` and evidence-linked unresolved dependencies. A rule correction produced a new pending-review immutable revision; its history exposed three revisions and two review records. A `CUSTOMER` keyword search returned four results. Export `exp_fbac8ce92cdb489b8f87f89ef20f3fbc` succeeded, excluded full source and downloaded through the browser as `laip-knowledge.zip`.

The final status correction derives analyzed/partial source and program status from explicit `SOURCE_OF` associations and parser evidence. It does not rewrite historical snapshots. The separate browser report records the scope of interactive and package checks. The independently checked [downloaded knowledge package](artifacts/step15-knowledge.zip) has SHA-256 `8f20097dcb89595c010f811d0e27e10216bcd0d07ee36433eef2553365cfee29`.

After rebuilding, browser inspection confirmed `DEMO/ORDER` as `analyzed` and `DEMO/CUSTOMER` as `partial` in a new immutable snapshot. The default `local:workspace` namespace was restored with healthy Docker services; validation history remains under `synthetic:step15`. Compose boundary verification passed and the isolated test stack was stopped.

## Reproduce local operation

Install Docker with Compose v2, Python 3.12, uv 0.12.23 and Node 22.23.2. From the workspace root:

```sh
make install
LAIP_WEB_PORT=3030 make up smoke
```

Open http://127.0.0.1:3030/. Omit the port override to use port 3000. Compose generates/preserves private `.local/` credentials, applies migrations, then waits for healthy services. Only the frontend/BFF publishes a loopback port. AI and embeddings are disabled. `make down` preserves named volumes; keep existing credentials with the database volume.

Use the labelled synthetic fixture for the browser workflow. A fresh validation namespace can be selected with `LAIP_ANALYST_NAMESPACE=synthetic:step15 LAIP_WEB_PORT=3030 make up smoke`. Return to the default `local:workspace` by restarting with `LAIP_ANALYST_NAMESPACE` omitted; both namespaces retain history.

Repeat locked checks and actual restart acceptance:

```sh
make check
make restart-check
make compose-check
LAIP_WEB_PORT=3030 make analyst-check
```

`make check` starts an isolated test database on loopback port 55433; `make test-down` stops only that test stack. `make analyst-check` adds synthetic history to the selected running analyst workspace.

For the optional real REA fixture, supply an audited, clean, already-built checkout at the pinned commit and a trusted Node executable through `LAIP_REA_ROOT` and `LAIP_REA_NODE`, then run `make rea-check`. The script verifies these prerequisites and refuses a missing or changed runtime; it performs no automatic installation/setup.

## Limits of this validation

This is a single-user localhost prototype tested with synthetic offline sources and supplied metadata. No live IBM i connector, release/PTF matrix, production source corpus, authority check, EBCDIC compatibility survey or runtime behavior validation was performed. Bounded parsers preserve unknown constructs and partial coverage; supplied metadata does not establish live object facts. Screen-reader validation and remote CI execution are not claimed. AI/embedding checks use separate controlled fixtures, while normal deployment remains AI-disabled.

Application modernization and code translation remain outside scope. Next milestones require explicit real-environment access and compatibility validation before live IBM i support claims.
