# LAIP step 3 validation

Scope: runtime scaffold only. FastAPI/Pydantic API, Python worker, Next.js/TypeScript/Tailwind web application, PostgreSQL, local deployment, locks, and repeatable checks. No application modernization or code translation.

## Implemented boundary

The browser calls a Next.js BFF; only the frontend is published on loopback. The API uses a private bearer token for readiness, PostgreSQL uses a generated password, and credentials are mounted through Compose secrets. The artifact volume belongs only to API/worker. The application services run as nonroot users with read-only root filesystems. Backend services use the internal Compose network. The frontend also has an ingress bridge so Docker Desktop can publish its loopback port.

AI and embeddings default off and fail closed on enablement or configured model endpoints. No model packages, downloads, imported-code execution, or live IBM i connection is part of startup.

API readiness checks PostgreSQL and actual private storage read/write access. The worker checks dependencies and publishes a bounded-age heartbeat. It reports `waiting_for_schema` before step 4. It cannot claim or complete jobs; if a jobs table is present it reports `queue_executor_unavailable`. The scaffold makes no persistence/schema completion claim.

## Recorded checks

- Backend tests were written before implementation and initially failed on missing modules. Final unit checks: 19 passing tests, 92.37% combined branch coverage; lint, formatting, strict typing passed with locked dependencies.
- Frontend runtime tests initially failed on the missing runtime module. Final guard checks: 3 passing tests; TypeScript and production webpack build passed.
- Compose configuration and boundary validation passed. Secret contents were excluded from check output.
- Fresh-state frontend typechecking passed without generated `.next` files.
- The requested sequence ran as TDD implementation → build-error-resolver → code-reviewer. Build review required no fixes; final code review approved with no actionable findings.
- Actual Docker backend image builds and frontend production compilation/typechecking/page generation completed. Docker then failed during frontend image export/unpack with a BuildKit RPC EOF. The Docker host log recorded a nil-pointer panic in `llbsolver.filterHistoryEvents`; Docker subsequently reported it could not start.
- **Initial startup attempt was blocked:** at that point no four-service healthy result or HTTP smoke pass was claimed. The Docker daemon/build-history failure was subsequently resolved as recorded below.

## Reproduction

`make install check compose-check` verifies both dependency locks, code checks, and rendered Compose boundaries. `make up smoke` builds and checks actual service startup. Use `LAIP_WEB_PORT=3030 make up smoke` when port 3000 is occupied. `make down` preserves private volumes.

The CI workflow uses pinned action revisions and the same checks. Remote CI execution is not verified: the supplied directory has no Git repository metadata. The checks in this report were run locally on Docker Desktop.

## Follow-on work

Step 4 owns schema migrations, durable jobs, safe claims, progress/cancellation, recovery, and entity/evidence repositories. The remaining provider, import, analyzer, retrieval, export, MCP, and analyst workflows follow the execution plan. No enterprise or live-environment validation is claimed.

## Startup resolution — 2026-10-07

The user authorized restarting Docker and using Docker for validation. After restart, the actual application stack built and started successfully: database, API, worker, and frontend were healthy; the new migration service exited successfully. `LAIP_WEB_PORT=3030 make smoke` passed against real HTTP endpoints at http://127.0.0.1:3030. A separate frontend ingress bridge corrected Docker Desktop port publication while backend services remained on the private network. Step 3 startup acceptance is now satisfied. Step 4 replaces the initial schema/worker status descriptions above; see its validation report for current behavior.
