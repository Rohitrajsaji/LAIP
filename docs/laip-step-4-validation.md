# LAIP step 4 — persistence and validation

Completed locally on 2026-10-07 against Docker PostgreSQL 16 with pgvector 0.8.7. This delivers the persistence foundation in [execution-plan step 4](plans/laip-execution.md#step-4). Application modernization and code translation remain outside scope.

## Implemented

Five ordered SQL migrations create namespace-aware canonical entities and observations, artifacts/origin mappings, runs and frozen providers, evidence and dependencies, immutable rule/review history, snapshots, chunks, keyword indexes, durable jobs/attempts/events/checkpoints, and optional-vector model profiles. Migrations are transaction-protected, advisory-locked, and checksum-tracked. Reapplying migrations is safe; changed or unknown migration checksums fail closed.

Repository inputs use the versioned canonical schema and deterministic hashes. Same names in different libraries or systems remain distinct; exact replay reuses records, while conflicting immutable payloads fail. Composite foreign keys enforce namespace and run provenance. Snapshot publication requires complete entity/evidence citations and serializes review writes before pinning its review watermark. Certainty classifications remain separate from human review decisions; changed rule support requires a new review.

Jobs use database-clock leases, row locking, fencing, bounded retries, cancellation, and recovery. Atomic checkpoint publication checks the frozen provider/revision/configuration and deduplicates the complete logical result key. One job owns each run/export/retention lifecycle. Separate owner types prevent export jobs from modifying completed analysis runs.

Private content-addressed artifact storage uses bounded streaming, hashing, private staging, atomic publication, filesystem synchronization, symlink-resistant traversal, and content verification. Keyword reads filter namespace and snapshot. Optional pgvector installation requires explicit approval and the migration owner; vector reads filter namespace, model and selected snapshot. Tests use synthetic vectors; no model is contacted or downloaded.

A one-shot migration service provisions `laip_runtime` with restricted table/sequence privileges. API and worker use that role; schema mutation, deletion, registry mutation and immutable-history rewriting are denied. Readiness verifies schema checksums and private storage. The worker recovers expired leases and reports `awaiting_handlers`; provider execution handlers follow in subsequent steps.

## Validation evidence

`UV_CACHE_DIR=/private/tmp/laip-uv-cache make check restart-check compose-check` passed:

- 57 backend tests passed with 95.37% combined statement/branch coverage. Lint, formatting and strict typing passed.
- Database integration tests apply all five migrations to clean isolated schemas, verify replay/checksum guards, namespace isolation, immutable history, privilege boundaries, snapshot closure, review-watermark concurrency, lease fencing/cancellation/recovery, and checkpoint provider/configuration guards.
- Artifact tests verify replay, bounded streams, corruption, traversal and symlink rejection. Optional pgvector tests verify approved installation, dimension/policy constraints, model isolation and snapshot-scoped retrieval.
- The restart script restarts the actual isolated Docker database, reconnects, verifies preserved history, recovers/reclaims an expired job, retains exactly one checkpoint, and repeats analysis with stable entities. New support remains pending review while the previous verified review remains intact.
- Three frontend guard tests, TypeScript checking and the production build passed.
- Compose syntax and port/network/storage/AI boundary checks passed. Actual application startup produced four healthy services and a successful one-shot migration service; real localhost HTTP smoke passed on port 3030.

The requested TDD → database-reviewer → code-reviewer sequence identified and resolved review-watermark ordering, citation closure, duplicate lifecycle ownership, upstream-artifact provenance, frozen checkpoint configuration and snapshot vector scope. Regression tests cover these fixes. The final code reviewer approved the source with no remaining actionable findings. The database reviewer identified issues that were fixed; its final independent revalidation was unavailable due to a usage limit. Root-agent Docker integration tests supplied the runtime validation above.

Nonblocking check warnings: Starlette deprecated its current httpx test-client integration, and Node reported module-type inference for the frontend runtime test. Neither affected test or build success. Remote CI has not run because the supplied directory has no Git metadata.

## Reproduce and operate

From the repository root:

```sh
make install
make check restart-check compose-check
LAIP_WEB_PORT=3030 make up smoke
```

`make check` starts the isolated test database on loopback port 55433; `LAIP_TEST_PORT` can select another port. The restart test operates only on its test Compose database. `make test-down` removes that isolated stack. `make down` stops the application while preserving database and artifact volumes. Existing credentials in ignored `.local/` are preserved by initialization.

Only the frontend/BFF publishes a loopback port. Backend services use the internal private network; the frontend additionally uses an ingress bridge for Docker Desktop port publication. AI and embeddings remain off, with configured model endpoints or enablement rejected by application guards.

## Migration and delivery limits

Never edit an applied migration: add a new numbered migration. Back up database and artifact volumes before incompatible changes. There is no destructive downgrade routine; incompatible rollback requires a compatible backup and application version. Optional vector installation is an owner-only operation; reapply runtime grants after installation.

Provider handlers, public import/analysis/retrieval/export/MCP workflows, and retention garbage collection belong to later execution-plan steps. The synthetic fixtures validate repository and queue behavior; they establish neither live IBM i compatibility nor enterprise readiness. No imported source is executed.

## Source locations

- [Canonical schema](../backend/src/laip/contracts/laip.schema.json), [canonical hashing](../backend/src/laip/canonical.py)
- [Migrations](../backend/src/laip/migrations.py), [core SQL](../backend/src/laip/migrations/001_core.sql), [provenance SQL](../backend/src/laip/migrations/005_provenance.sql)
- [Repositories](../backend/src/laip/persistence.py), [durable jobs](../backend/src/laip/jobs.py)
- [Artifact storage](../backend/src/laip/artifacts.py), [vector indexes](../backend/src/laip/vectors.py), [runtime privileges](../backend/src/laip/bootstrap.py)
- [Restart integration](../scripts/test_restart.py), [test Compose](../compose.test.yaml), [application Compose](../compose.yaml)
