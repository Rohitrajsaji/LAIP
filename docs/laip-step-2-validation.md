# Step 2 design validation

Date: 2026-10-07. Scope: document and contract gate for the offline synthetic LAIP prototype. No runtime service, database migration, container deployment, parser, live IBM i connector, or enterprise security implementation was exercised.

## Artifact checks actually performed

- Validated `contracts/0.1.0/laip.schema.json` as JSON Schema Draft 2020-12 using `jsonschema` 4.26.0, with format checking enabled.
- Validated the complete `synthetic.example.json` bundle against the schema, including entities, artifacts/origin maps, evidence/views, dependencies, rule revisions, reviews, run/job records, provider/import/context/export examples.
- Recomputed entity identity hashes, artifact occurrence hashes, immutable evidence/dependency/rule revision hashes, rule support fingerprint, and review target fingerprints.
- Checked example entity parents, evidence/artifact/run references, dependency endpoints/candidates/evidence references, and rule/evidence links.
- Matched source and metadata byte hashes/lengths against `VALIDATE.rpgle` and `relations.example.json`, and export bytes against `export-rule.example.json`.
- Checked source range bounds, origin-map reference availability, coverage accounting and serialized offline context budget.
- Verified that an inferred rule can retain a verified human review without changing its classification.
- Verified schema rejection for invalid certainty (`verified` used as classification), a resolved dependency with a null target, and a malformed content digest.
- Verified Python/Node hash agreement for fixture entity/evidence/dependency/rule/view fingerprints and golden cases covering supplementary Unicode key ordering, typed decimal precision, large integer strings and the safe-integer boundary. Fractional and unsafe normalized numeric values are rejected; raw import bytes remain unchanged.

Validation dependencies were installed only in temporary storage; they are not part of LAIP’s runtime or selected project dependencies. These checks establish consistency of the supplied design examples, not the future correctness of providers, review invalidation, worker recovery, masking, authorization, or live-platform behavior. Future implementation gates and their acceptance corpus are defined in [the backlog](laip-backlog.md).

## Design review

The requested sequence is planner → architect → database reviewer → code reviewer. Each stage uses the prior artifacts and handoff. Planner wrote requirement traceability; architect wrote component/transport/persistence/deployment interfaces. Database review identified three concrete issues, corrected before final review:

| Finding | Correction |
| --- | --- |
| Export lifecycle could overwrite completed analysis state | Explicit job lifecycle owner and job-kind dispatch; export/retention outcomes never mutate their basis run; export-specific publication key and cancellation route |
| Queue claim/recovery lock order contradicted cancellation/finalization | Unlocked candidate discovery followed by lifecycle owner → stable job order and eligibility recheck; namespace-head ordering when applicable |
| Full-record hash rules admitted inconsistent numeric serialization | RFC 8785 JCS safe-integer profile, typed decimal/large-integer values, strict normalized numeric validation and cross-language golden examples |

Focused database rereview: all three issues resolved; no remaining concrete database-design blockers. No migrations, SQL execution or database runtime checks were performed.

Final code review: no critical/high findings; one medium mismatch between the documented operator expression and the fixture's compact assignment form. Corrected the architecture/contracts to define that assignment variant and scoped identifier interpretation explicitly, retaining the fixture hashes. Reviewer verdict: APPROVE with that correction. Review is of a design specification, not live/runtime certification.
