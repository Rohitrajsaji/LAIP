# LAIP backend runtime

Python 3.12 API and separate worker, with PostgreSQL persistence and private content-addressed storage. From the repository root run `make install check restart-check compose-check`. Checks use locked dependencies and an isolated Docker PostgreSQL instance; `make test-down` removes the test stack.

Configure exactly one of `LAIP_DATABASE_URL` / `LAIP_DATABASE_URL_FILE` and exactly one of `LAIP_SERVICE_TOKEN` / `LAIP_SERVICE_TOKEN_FILE`. The service token must contain at least 32 characters. Keep credentials outside source control. Compose initializes a migration-owner connection separately from the restricted `laip_runtime` connection used by API/worker.

The one-shot `python -m laip.bootstrap` applies ordered, transaction-protected SQL migrations, checks stored checksums, and provisions runtime privileges. Migration SQL lives in `src/laip/migrations/`; never edit an applied migration. Add a new numbered migration instead. Back up database and artifact volumes before future incompatible changes. No destructive downgrade command is provided; incompatible rollback requires a compatible backup and application version.

`Repository` receives a connection and an authorized system namespace. It validates canonical records, preserves immutable observations/evidence/reviews, and rejects conflicting replays. Snapshot publication checks citation closure and pins a serialized review watermark. Keyword retrieval is bounded by namespace and snapshot.

`JobRepository` provides durable claims, database-clock leases, fencing, retries, cancellation, recovery, and atomic checkpoint publication. Checkpoints must match the run's frozen provider and configuration. The worker recovers expired leases and executes durable source import, offline analysis and knowledge-export handlers. Health reports idle/busy state. Publication uses fenced checkpoints so cancelled or expired claims cannot publish results.

`LocalArtifactStore` hashes bounded streams into private storage, prevents symlink traversal, verifies existing content, and atomically publishes immutable blobs. `LAIP_ARTIFACT_ROOT` defaults to `/var/lib/laip/artifacts`; deployment supplies write access for UID 10001. Retention/garbage-collection workflows follow later.

Optional `VectorRepository.install(..., approved=True)` requires the migration owner and an explicitly approved model profile. It creates pgvector indexes without downloading or contacting models. Reapply runtime grants after optional installation. Vector reads require a selected snapshot and filter by namespace/model; normal startup leaves embeddings disabled.

API liveness is `GET /api/v1/health/live`; bearer-protected readiness at `/api/v1/health/ready` checks schema checksums, PostgreSQL and private storage. Container checks use `python -m laip.healthcheck api` and `python -m laip.healthcheck worker`. Compose exposes only the frontend/BFF on loopback; backend services use the internal network.

AI and embeddings default off. Enabling either requires an explicitly approved private endpoint and pinned model/tokenizer contract. No live enterprise connections, imported application execution, modernization, or code translation occur.

See [step 4 validation](../docs/laip-step-4-validation.md) for implementation evidence and limitations.

The optional `ReaAdapter` invokes only the pinned CLI version check and historical source-inventory operation, with a trusted runtime digest manifest and private sealed staging. `prepare_inventory` publishes raw bytes outside DB locks; `persist_inventory` is DB-only and runs inside a fenced job checkpoint callback. Default containers do not install or automatically set up REA. [Step 5 validation](../docs/laip-step-5-validation.md) records the real CLI/Docker fixture and operator acceptance command.

Step 6 provides internal `prepare_zip` / `prepare_local` services for inert ZIP streams and operator-configured directory references. Strict manifests preserve explicit identities and raw bytes; decoded views retain exact source maps. `register_import` seals durable accepted inputs, `restore_import` resumes from private blobs, and `persist_import` publishes database records inside a fenced job callback. Supplied metadata remains descriptive; no live IBM i facts or execution are inferred. The analyst API and UI now expose ZIP and labelled synthetic imports; configured local-directory references remain internal operator interfaces. [Step 6 validation](../docs/laip-step-6-validation.md) records the boundaries and fixture checks.

Step 7 provides pure `build_inventory` and DB-only `register_inventory` / `persist_inventory` interfaces for supplied metadata, qualified dependency resolution, application membership and immutable artifact accounting. Use `make inventory-check` from the workspace root. The normalized record profile is documented in [inventory-metadata.md](../docs/contracts/0.1.0/inventory-metadata.md). The bounded RPGLE, CL/CLLE and DDS parsers now feed the analyst workflow; snapshot-bound graph reads are available through REST and read-only MCP.

Step 8 provides Python/Lark `parse_statements`, in-memory `expand_source`, bounded `build_ir`, and private `analyze_rpgle` / fenced publication interfaces. Unknown constructs block unsupported conclusions. See the [parser decision](../docs/rpgle-parser-decision.md) and [Step 8 validation](../docs/laip-step-8-validation.md).

Step 9 provides `cl_parser.parse_statements`, CL-specific structural lowering in `cl_ir.build_ir`, and private `cl_analysis.analyze_cl` / fenced publication interfaces. Embedded commands retain parent source spans; submitted commands remain references in their separate job context. Dynamic targets, unknown commands and unsupported monitor scope block complete conclusions. Run `make cl-check`; see [Step 9 validation](../docs/laip-step-9-validation.md). The analyst analysis job dispatches these parsers without executing imported commands.

Step 10 provides `dds_parser.parse_statements`, `dds_ir.build_ir`, and private `dds_analysis.analyze_dds` / fenced publication interfaces. Fixed columns and continuation segments retain original coordinates; external relationships resolve only from explicitly identified supplied descriptions. Use `make dds-check`; see [Step 10 validation](../docs/laip-step-10-validation.md) for supported syntax and limits.

Step 11 provides deterministic `rules.extract_rules`, resolved-call `workflows.compose_workflows`, and `rule_store` publication/review services. Original analysis evidence and explicit program identities support immutable rule revisions; changed support needs fresh review. Approve/reject/correct operations preserve history and reject stale requests. Use `make rules-check`; see [Step 11 validation](../docs/laip-step-11-validation.md). The analyst UI now exposes immutable name/description corrections and inspectable review history; backend approve/reject services retain their explicit review checks.

Step 12 adds snapshot-bound `RetrievalService`, budgeted/masked `build_context`, optional `semantic_query`/`hybrid_query`, and strict private-model clients. AI defaults off. Operator enablement requires approved numeric private endpoints, pinned model contracts and exact tokenizer callbacks; source/model output has no tool permissions. Use `make retrieval-check` and the separate `make semantic-check`. See [Step 12 validation](../docs/laip-step-12-validation.md), including pending independent reviews.

Step 13 adds `exports.build_export` / `validate_bundle` / private `publish_export`, shared `ReadService` REST routes, and `python -m laip.mcp` with five deterministic read tools. Explicit operator namespaces are required; full source additionally needs an authorized source-reader capability and cannot be requested through MCP. Run `make export-mcp-check`; see [Step 13 validation](../docs/laip-step-13-validation.md) for contracts, configuration, reviews and limitations.


## Analyst operation (schema 0.1.0)

Start the API, worker, database and frontend from the repository root with `make up smoke`. Use `LAIP_WEB_PORT=3030 make up smoke` when port 3000 is occupied. The browser talks to a finite frontend/BFF route allowlist; the BFF supplies the service credential privately. Published ports remain loopback-only, and AI/embeddings remain off in Compose.

The default analyst/read namespace is `local:workspace`. You can select a separate synthetic validation namespace with `LAIP_ANALYST_NAMESPACE=synthetic:step15 LAIP_WEB_PORT=3030 make up smoke`. Restart with the namespace override omitted to return to the default workspace; this does not delete either namespace's history. Use `make analyst-check` against a running stack for the HTTP import-to-export smoke journey (set the same `LAIP_WEB_PORT` override).

Analysis can succeed partially. Missing includes, unknown effects, unresolved dependencies and supplied-artifact accounting appear in run diagnostics/limitations. Supported syntactic action candidates remain inferred and pending review; a partial source does not establish complete business behavior. Export jobs exclude full source by default, and the UI does not offer its inclusion.

See [step 14 implementation](../docs/laip-step-14-implementation.md), [step 15 validation](../docs/laip-step-15-validation.md) and [browser observations](../docs/laip-step-15-browser-validation.md). Validation covers synthetic offline sources and the pinned REA fixture; it does not establish live IBM i compatibility.
