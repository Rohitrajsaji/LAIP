# Analyst UI cleanup

Design direction: a quiet, compact workspace for analysts inspecting offline source, relationships and evidence. The existing forest navigation rail and ordinal view index provide orientation; neutral surfaces keep the records primary, and amber marks incomplete results and limitations.

The overview uses a single compact metrics strip and divided activity rows. Inventory evidence is grouped behind citation counts. Snapshot identifiers and complete records remain available through disclosures. Source excerpts, correction controls, import limits, API contracts and AI-disabled behavior retain their existing policies. No dependencies were added.

Screenshots accompany the production deployment and desktop/mobile checks below.

## Checks on 9 October 2026

- TypeScript validation passes after the presentation changes.
- All eight existing frontend policy tests pass.
- The final Docker production image builds successfully and its frontend health check passes on port 3030.
- Browser checks on the existing synthetic workspace cover navigation, loading feedback, matching and empty keyword results, citation disclosures, evidence inspection, original source access, and existing export controls.
- Desktop 1280 × 900 and mobile 390 × 844 / 320 × 740 checks show no document-level horizontal overflow. At 390 pixels, the inventory table scrolls within its 356-pixel region.
- These checks are visual and functional spot checks, not a full accessibility certification. No imported data, rule revisions or exports were created or changed for this cleanup.

Final deployed checks confirm compact identities, keyboard-focusable table regions, explicit pending-review badges, separate conditions/actions with expandable source locations, and immediate evidence summaries with their limitations. API, worker, database and frontend are healthy; only the frontend image was redeployed. AI remains disabled and the published frontend port remains on `127.0.0.1:3030`.
