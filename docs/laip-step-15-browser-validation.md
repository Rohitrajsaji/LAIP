# LAIP step 15 browser validation

Status: actual browser import-to-export journey passed on 2026-10-08, with a separately verified downloaded archive. A limited keyboard skip-link check and visible error-state check passed; no screen-reader claim is made.

## Synthetic application

The deterministic fixture contains CLLE, free RPGLE, and positional physical/logical/display DDS sources. It exercises qualified `DEMO/ORDER` and `OTHER/ORDER` name collisions, supplied metadata, a missing customer include, a dynamic call, and an ambiguous call. Supported order rules remain reviewable. Customer source still yields supported assignment action candidates marked inferred and pending review; the missing include keeps `conclusions_allowed` false, conditions empty, and unknown-construct/effect limitations explicit. Unsupported customer conclusions remain blocked. This is an offline synthetic application, not a live IBM i validation.

## Interactive journey

The intended browser journey covers an empty workspace and AI-disabled notice; fixture loading, import, analysis and partial coverage; inventory and unresolved dependencies; entity, source and evidence inspection; an immutable rule correction and revision inspection; keyword retrieval; and an export download. Progress, cancellation, visible labels, focus, and recoverable errors require their own observed evidence before being marked passed.

## Tool boundary

The browser-validation subagent's CUA inventory returned no enabled browsers and reported a locked native Mac. The parent agent independently has an available in-app browser binding and is performing interactive checks there. Therefore this subagent cannot attest to UI actions directly, and the native-lock message is not treated as a blocker for the parent's in-app browser. The actual observations below come from that parent browser runtime; the downloaded ZIP was independently validated by the browser-validation subagent.

No screen-reader, live IBM i, application modernization, or code-translation validation is claimed.

## Observed browser results

The parent agent observed the fresh `synthetic:step15` workspace with zero imports, runs, and exports and an AI-disabled notice. The synthetic customer-validation fixture loaded and imported eight artifacts. An analysis job displayed its cancellation control and completed with a partial snapshot. These are actual UI observations, not substitutions from API tests.

- Empty workspace: [screenshot](artifacts/step15-empty.jpg).
- Analysis progress: [screenshot](artifacts/step15-progress.jpg).

The inventory showed distinct `DEMO/*PGM/ORDER` and `OTHER/*PGM/ORDER` rows, one of each. Application membership displayed confirmed and inferred rows with separate evidence. Selecting the DEMO order entity exposed its source condition/action candidates. Its source view showed `balance = 100;` at line 6 with a source range, mappings disclosure, and origin-map identity.

Dependencies displayed 18 rows, including an unresolved dynamic call and two ambiguous calls. Dynamic-call evidence showed its barrier flag, dynamic `TARGET`, an explicit unresolved-target diagnostic, `conclusions_allowed: false`, and its line-8 artifact/span mapping.

- Source inspection: [screenshot](artifacts/step15-source.jpg).
- Dependencies: [screenshot](artifacts/step15-dependencies.jpg).

The browser identified a missing run-summary diagnostic display. After a test-first fix, rebuild, and repeated analysis, the UI displayed `INCLUDE_MISSING`, `UNKNOWN_EFFECTS`, and three `UNRESOLVED_DEPENDENCY` diagnostics covering dynamic/ambiguous targets with evidence. [Diagnostic screenshot](artifacts/step15-diagnostics.jpg).

An actual cancellation completed as `cancelled` with 0/1 progress; the previous published snapshot remained selected. A repeated analysis completed as partial.

The first balance rule was corrected to “Synthetic balance cap candidate” with an explicit synthetic-validation reason. The corrected snapshot retained the conditions, actions, and evidence. Its new inferred revision remained `pending_review`, referenced its previous revision, and exposed three retained revisions and two review records. [Revision screenshot](artifacts/step15-revisions.jpg).

Keyword retrieval for `CUSTOMER` returned four hits: two qualified entity chunks and two evidence chunks. [Retrieval screenshot](artifacts/step15-retrieval.jpg).

The source-excluded export completed successfully from the corrected snapshot. Clicking the actual browser download saved a ZIP to Downloads. [Export screenshot](artifacts/step15-export.jpg).

## Download validation

The exact browser-downloaded archive was independently checked with the export bundle validator: schema 0.1.0, safe internal paths, internal links, every listed member SHA-256, and every listed byte length passed. The archive contains 263 files, totals 1,057,641 bytes, references corrected snapshot `snap_3602b3b0792c406eb1d6da0660bc2b05`, and declares `source_included: false`; it contains no `sources/` paths.

Archive SHA-256: `8f20097dcb89595c010f811d0e27e10216bcd0d07ee36433eef2553365cfee29`. The exact reviewed copy is [step15-knowledge.zip](artifacts/step15-knowledge.zip).

## Remaining acceptance limits

Visible control labels and state messaging were observed through the browser DOM. Keyboard activation of “Skip to workspace” focused the main element (`id=content`, `#content`). Submitting an unknown source-artifact identity produced a visible alert with HTTP 404, recovery guidance, and a Retry control. [Error screenshot](artifacts/step15-error.jpg). Selecting a valid attributed source reference recovered successfully: the inspection heading read “Original source excerpt”, rendered `balance = 100;`, and exposed source range and mappings. This is a limited keyboard/control-state check, not a full accessibility audit. No assistive-technology audit, live IBM i execution, or production-corpus result is claimed. The application remains an offline synthetic validation with AI disabled.

## Final rebuilt status check

A final browser analysis after the status-display rebuild completed as partial. Entity inspection correctly showed `DEMO/*PGM/ORDER` as `analyzed` and `DEMO/*PGM/CUSTOMER` as `partial`. Its snapshot was `snap_9c0cf5e19f20e4d36166a279a533d273dbe944d858341341a62d16cb5f6f7444`. The previously downloaded export remains the immutable export of the earlier corrected snapshot; it is not presented as an export of this later run. Compose boundary validation also passed: published frontend loopback binding, private backend services, and AI disabled.
