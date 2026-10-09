# LAIP step 14 analyst workflow

The Next.js analyst workspace connects the existing bounded offline import, CL/RPGLE/DDS analysis, immutable rules, retrieval and export services through authenticated private REST operations. Docker publishes the web UI only on loopback, uses the server-side service credential, and explicitly selects the operator namespace. AI and embeddings remain disabled.

## Interfaces and operation

- `/api/v1/workspace` shows namespace-scoped imports, durable runs, progress events, export state and the latest analyst snapshot. Partial snapshots remain inspectable without promotion to a complete namespace head.
- `/api/v1/fixtures/analyst` supplies a labelled synthetic ZIP/manifest. `/imports` accepts at most 8 MiB ZIP content plus bounded metadata; no caller filesystem paths are accepted.
- `/runs` explicitly queues analysis of a completed import. `/runs/{id}/cancel` requests fenced cancellation. Immutable job plans persist across worker restarts. Worker readiness remains observable during work.
- `/inventory`, `/dependencies`, `/rules` read a selected frozen snapshot. Entity/evidence/search/context reads reuse the step 13 interfaces. Source windows require an artifact attached to that snapshot's run, preserve mapping identifiers, redact credentials and allow at most 200 lines/64 KiB per window.
- `/rules/{id}/revisions` requires the expected current revision, cited evidence, and a correction reason. Corrections create a new revision and snapshot in one transaction; review history respects each snapshot's frozen review watermark. New masked keyword chunks support retrieval of corrected interpretations.
- `/exports` queues a linked source-excluded package; `/exports/{id}/download` streams a verified private ZIP. The UI does not authorize full-source exports.

All routes require a service bearer credential, namespace authority is server-side, supplied origins are allowlisted, and the browser proxy requires a matching local host and exact same-origin mutations. Input/output sizes and active work admission are bounded. Timed-out HTTP waiters retain admission until their background operation actually finishes.

## Repeatable checks

`make check` runs isolated PostgreSQL migrations, the backend tests and static checks, plus web type checks, tests and production build. `python3 scripts/generate_api_types.py --check` checks generated schema types. `make up` builds and starts the localhost services; `make smoke` checks readiness. `LAIP_WEB_PORT=3030 make analyst-check` performs the real Docker HTTP import-to-export journey and adds labelled synthetic history to the selected local namespace; it does not substitute for browser acceptance. Backend tests `test_analyst_api_journey.py`, `test_analyst_jobs.py`, `test_analyst_service.py`, `test_analyst_capacity.py` and `test_analyst_fixture.py` cover the integrated workflow, immutable history, scoped cancellation, worker recovery, malformed archives and request admission.

Independent code review identified four issues (stale view responses, retained invalid upload selection, delayed cancellation acknowledgement, premature timeout admission release); all were fixed and re-reviewed with no outstanding findings. Browser results are recorded separately in `laip-step-14-browser-validation.md`.

## Scope and limitations

This is offline supplied-artifact analysis. Synthetic fixtures do not establish live IBM i discovery or runtime equivalence. Dynamic calls remain unresolved; unsupported constructs retain limitations. Source inspection is explicit and bounded. UI corrections edit a rule's name/description with a reason while retaining its condition/action citations. Application modernization and code translation are outside this step.

## Validation on 2026-10-08

- Configured full backend suite: 617 passed; statement/branch coverage 91.45%, exceeding the 80% gate.
- Backend and new script lint/format checks passed; strict typing passed for 48 backend modules; generated TypeScript contracts are current.
- Web: eight tests, type checking and production build passed.
- Docker Compose boundaries, four-service health, localhost startup smoke and the actual deployed HTTP import-to-export journey passed on port 3030.
- Independent code review: approved after fixes, including the deployment proxy address correction.
- Interactive browser, visual and keyboard acceptance remains pending because the Mac is locked. Step 14's browser acceptance is not marked complete.
