# LAIP step 11 — deterministic rules, workflows and review history

Implementation record for [execution-plan step 11](plans/laip-execution.md#step-11), completed checks recorded on 2026-10-08. Modernization and code translation remain outside scope.

`rules.extract_rules` consumes the bounded RPGLE, CL and DDS analysis IR. It produces source-cited calculation/action and validation candidates, with conditions derived from control-flow branches and explicit positive/negative polarity. Candidates are interpretations: classification stays inferred and review starts pending. Unknown effects and missing support remain limitations. Explicit supplied program associations are required for canonical RuleRevision records; source filenames do not establish compiled program identity.

Loop body guards do not become claimed post-loop conditions. Partial control-flow analysis suppresses unsupported condition attribution. Indicator-conditioned DDS validation remains diagnostic rather than becoming an unconditional business rule. Call/action candidates without a resolved target are not eligible for approval.

Rule identities use namespace, source/scope and statement anchors. Revisions retain original analysis run, provider/configuration basis and supporting evidence fingerprint. Conditions and actions cite original source spans, including includes. Repeated publication is checked for exact replay; changed support creates a new revision linked to the preceding revision.

`workflows.compose_workflows` composes possible call paths only through resolved canonical dependencies between explicitly identified Programs. Qualification may come from a supplied library scope. Dynamic, ambiguous, missing and unsupported edges remain diagnostic boundaries. Paths are inferred, pending review, bounded and do not establish runtime order or execution.

`rule_store` registers an immutable publication basis and exposes database-only bundle publication, current-rule lookup, approve/reject/correct and chronological review history. Publication belongs inside the existing fenced job checkpoint. Approval checks supporting evidence and rejects unresolved/barrier support. Review operations use revision and review compare-and-swap checks. Corrections preserve prior immutable revisions and review events; new heads need fresh review. Workflow revisions and rule publication bases use additive migration 010 and immutable-history triggers.

For a declared reanalysis scope, `invalidate_scope` records pending-review transitions for expected old heads whose candidates disappear. `persist_bundle(..., expected_missing_heads=...)` includes those transitions in the publication transaction. The caller must supply that scope explicitly; absence from a partial bundle is not proof that an old rule disappeared. Reanalysis orchestration must publish new heads and invalidate missing candidates within its declared complete scope.

These are internal application services. Public REST/MCP and the review UI are scheduled in later plan steps. AI remains disabled; imported application source is never executed.

## Validation

Test-first failures and review findings led to regressions for branch polarity, loop-exit predicate leakage, conditioned DDS validation, concurrent registration/reviews/publication, forged workflow payloads, unsupported evidence, citation coverage and opaque/reversed expressions. All focused checks pass.

- Rule extraction: 17 tests; focused coverage 98.03%.
- Workflow composition: 35 tests; focused coverage 93.67%.
- PostgreSQL publication/review: 18 tests; focused coverage 84.86%.
- Actual RPG source pipeline: import and analyze, publish source-cited rules, reanalyze into a second run, preserve original review history and leave changed support pending.
- Independent database review passed after registration concurrency, lock ordering and workflow-validation fixes. Independent code reviews approved all three new modules after citation/opaque-expression and resource-limit corrections.

The final backend suite passed all 487 tests with 92.91% combined statement/branch coverage. Ruff, formatting and strict mypy passed. Web type checks, three web tests and the production build passed. The isolated Docker database restart/repeat-analysis check passed: history survived, entities remained stable, checkpoints deduplicated and changed support needed review.

The final Docker rebuild, migration/startup and HTTP smoke check passed: four healthy application services, frontend/BFF bound to localhost port 3030, private database/artifact storage and AI disabled. The temporary test stack was removed; application services remain running. These synthetic tests establish the local declared subset, not live IBM i compatibility or runtime business behavior.

Repeatable checks: `make rules-check`, `make check`, `make restart-check`, and `LAIP_WEB_PORT=3030 make up smoke`.
