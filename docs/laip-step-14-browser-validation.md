# LAIP step 14 browser validation

Status: real HTTP-to-worker acceptance passed. The actual browser import-to-export journey subsequently passed during step 15 on 2026-10-08; see the linked report below.

## Curated offline fixture

`laip.analyst_fixture.fixture_payload(namespace)` supplies a deterministic inert ZIP and a schema 0.1.0 manifest. No caller paths or live systems are accessed. The fixture contains qualified `DEMO` CLLE and RPGLE programs, physical/logical/display DDS members, and inventory metadata. Its CL dynamic call deliberately remains unresolved. Its free RPGLE conditions and assignments yield reviewable deterministic rule revisions. All source is synthetic UTF-8; ZIP timestamps are fixed.

Two fixture tests pass: repeatable archive/manifest validation and actual ZIP import through inventory, CL, RPGLE, DDS, and rule providers. Ruff and strict fixture type checking pass.

## Browser journey to verify

- Empty workspace and AI-disabled messaging.
- Load the synthetic fixture, import, analyse, and inspect progress and partial coverage.
- Inventory, qualified dependencies, program/source, and evidence views.
- Correct a rule and inspect retained revisions and evidence.
- Retrieve facts and export/download linked knowledge.
- Inspect accessible control labels, keyboard focus, empty/partial/error states.

## HTTP integration acceptance

`backend/tests/test_analyst_api_journey.py` passed against the temporary Docker PostgreSQL service with real authenticated FastAPI routes and durable worker execution (25.10 seconds). It covers empty workspace, AI disabled, synthetic fixture import, partial CL/RPGLE/DDS analysis, inventory, unresolved dynamic dependency, program/source/evidence reads, rule correction retaining two immutable revisions, the latest corrected snapshot, keyword retrieval, knowledge export job/download, export schema validation, and SHA-256/length validation for every archive member. No mocked analyst flow was used.

## Interactive browser blocker

After the rebuilt localhost UI became ready, CUA could not create an in-app browser tab (`Browser is not available: iab`). Its surface inventory returned no apps or browsers and reported: “The Mac is locked and automatic unlock could not unlock it. Ask the user to unlock the Mac manually before continuing.” No UI action occurred. A manual unlock is required to run the browser journey; browser, visual, keyboard, and assistive-technology acceptance remain unconfirmed. HTTP integration results do not substitute for those checks.

## Deployed Docker HTTP workflow

The rebuilt deployment passed `LAIP_WEB_PORT=3030 make smoke`: four healthy services, real localhost page and browser-proxy workspace retrieval, AI disabled. `LAIP_WEB_PORT=3030 make analyst-check` then passed with the restricted production runtime role and the live worker: synthetic import, partial analysis, dynamic dependency boundary, entity/source/evidence reads, immutable correction, keyword retrieval, and a downloaded source-excluded ZIP whose manifest schema and all member hashes/lengths validate. This check adds labelled synthetic history to the local analyst namespace.

Deployment validation caught and fixed the Next standalone proxy's internal-address/public-Host mismatch. Independent review confirmed exact loopback Host and mutation Origin checks still reject remote hosts and cross-origin requests. A later CUA state check still reported the Mac locked; no interactive browser action has been claimed.

## Subsequent interactive acceptance

Step 15 resolved the earlier browser-tool blocker: the parent agent's in-app browser was available despite the native Mac lock. The actual UI journey passed empty workspace → synthetic fixture import → partial analysis → qualified inventory/dependencies → entity/source/evidence inspection → immutable correction/revisions → keyword retrieval → source-excluded export and browser download. Actual cancellation retained the prior snapshot. The downloaded ZIP independently passed schema, internal-link, SHA-256 and byte-length validation. Screenshots and exact scope are recorded in [step-15 browser validation](laip-step-15-browser-validation.md). Keyboard/error checks retain their separately stated status there; no screen-reader claim is made.
