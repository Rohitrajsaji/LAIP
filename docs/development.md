# Development and operator guide

LAIP extracts business rules and evidence-linked context for downstream AI-assisted modernization. The current runtime is a single-user localhost prototype; full-source access is policy-controlled and AI/embeddings are disabled by default.

## Local runtime

Install Docker with Compose v2, Python 3, and Make. Run `make up smoke`; open http://127.0.0.1:3000. For another port, use `LAIP_WEB_PORT=3030 make up smoke` and the same override for HTTP checks. `make down` stops services while retaining named database/artifact volumes.

`make init` creates credentials once under `.local/`. Keep that directory private and preserve its identity alongside the existing database volume. Reinitialization does not rotate database credentials. Never commit or bundle it. A one-shot migration service prepares the schema and restricted runtime role. API readiness checks migration checksums and private artifact access; the durable worker handles imports, analysis, exports, recovery and cancellation.

Only the web/BFF service publishes a loopback port. API, worker, PostgreSQL and artifact storage remain private. See the [backend operator reference](../backend/README.md) for interfaces, jobs, migrations, source policy, REST and read-only MCP configuration.

## Locked development setup

Use Python 3.12, Node 22.23.2 and uv 0.12.23 (matching CI). Python 3 is sufficient for initial local-secret generation; uv selects Python 3.12 for backend development.

```sh
make install
make check
make restart-check
make compose-check
LAIP_WEB_PORT=3030 make analyst-check
```

`make check` starts an isolated Docker PostgreSQL on loopback port 55433, then runs Ruff lint/format, strict Mypy, backend tests with an 80% coverage gate, generated API type consistency, frontend type/policy tests and production build. `make restart-check` exercises actual isolated database restart and deduplication. `make test-down` stops only the test stack. `make analyst-check` adds synthetic history to the configured local workspace and checks HTTP import-to-export; it does not certify visual or screen-reader behavior.

Focused targets: `import-check`, `inventory-check`, `rpgle-check`, `cl-check`, `dds-check`, `rules-check`, `retrieval-check`, `semantic-check`, and `export-mcp-check`. Real private-model semantics need an approved endpoint and are separate from deterministic retrieval.

The pinned [CI workflow](../.github/workflows/ci.yml) runs install/check/restart/Compose/startup checks. Local checks do not establish that remote GitHub CI has run.

## Synthetic demonstration

In the UI, load **Synthetic fixture**, import its eight artifacts, analyze the sealed import, and inspect evidence, rules, revision history and retrieval before exporting. Missing includes, dynamic calls and name collisions intentionally produce partial conclusions. Same-named qualified objects remain distinct. Candidate business rules require review; a valid export does not establish complete extraction.

New analyses use schema `0.2.0`; read/export consumers must explicitly negotiate that version. Historical `0.1.0` IDs, hashes and reviews remain intact. Full source is excluded from ordinary exports; attributed evidence snippets may still contain source-derived information. Review context before sending it to another AI system.

## Optional private regression

The local banking regression uses frozen, owner-supplied files. Originals, derived oracle material, screenshots and source-dependent assertions are not public sample inputs. Public packaging excludes that regression material until redistribution rights are established. The local development checkout retains it.

```sh
LAIP_BANKING_FIXTURE_DIR=/path/to/private/sources make banking-check
```

A private acceptance run verifies original SHA-256 values and uses disposable schemas/private artifact directories. Missing corpus-dependent tests skip; a skip is not a private-corpus pass. See the local [banking fixture guide](../backend/tests/fixtures/banking/README.md) and [historical validation index](validation/README.md) for boundaries. Public candidates must retain synthetic tests and explicitly state their excluded private subset.

## Optional REA adapter

A real REA check requires a separately audited, clean, already-built checkout at `bc2cd8b874e115eee446860043758a80bd583ad0`, compiled dependencies and a trusted Node executable. Set `LAIP_REA_ROOT` and `LAIP_REA_NODE`, then run `make rea-check`. It does not fetch/install dependencies, run engine setup or execute imported fixture code. Default containers do not install REA. See [adapter validation](laip-step-5-validation.md).

## Runtime troubleshooting

If Docker is unavailable, start Docker Desktop/the daemon and retry. If startup fails, inspect `docker compose ps` and the failed service's logs locally; do not share credentials. Use a free loopback port consistently. Preserve `.local` and volumes during updates. Never use `down --volumes` to troubleshoot a workspace whose history must survive.
