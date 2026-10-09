# Validation and known limits

Historical reports record their original dates, environments and supported scopes. The current [packaging release record](../packaging/release.md) contains fresh checks; historical passes are not rerun claims.

## Current boundary

The runtime is a single-user localhost prototype with AI and embeddings off by default. Bounded offline IBM i extraction preserves evidence and explicit unknowns. Synthetic sources do not establish live release/PTF/authority, compiler compatibility, enterprise deployment, performance or screen-reader conformance. Export hashes and schemas establish package integrity, not complete business semantics.

## Historical offline evidence

| Report | Scope |
|---|---|
| [Step 2](../laip-step-2-validation.md) | architecture/contracts |
| [Step 3](../laip-step-3-validation.md) | local scaffold |
| [Step 4](../laip-step-4-validation.md) | persistence/recovery |
| [Step 5](../laip-step-5-validation.md) | pinned REA adapter |
| [Step 6](../laip-step-6-validation.md) | offline imports |
| [Step 7](../laip-step-7-validation.md) | qualified inventory/dependencies |
| [Step 8](../laip-step-8-validation.md) | bounded RPGLE |
| [Step 9](../laip-step-9-validation.md) | bounded CL/CLLE |
| [Step 10](../laip-step-10-validation.md) | positional DDS |
| [Step 11](../laip-step-11-validation.md) | rules/workflows/reviews |
| [Step 12](../laip-step-12-validation.md) | retrieval/context/private AI |
| [Step 13](../laip-step-13-validation.md) | linked exports/read-only MCP |
| [Step 15](../laip-step-15-validation.md) | synthetic MVP and recovery |

Browser/UI evidence: [analyst implementation](../laip-step-14-implementation.md), [step-14 browser checks](../laip-step-14-browser-validation.md), [synthetic MVP browser checks](../laip-step-15-browser-validation.md), [UI design review](ui-refresh/README.md).

Private banking corpus reports are available only in the local development workspace: [implementation ledger — private material withheld](../private-validation.md) and [browser record — private material withheld](../private-validation.md). Public source candidates replace these links with the [private-validation boundary](../private-validation.md) and withhold derived evidence.

## Assurance still required

Live IBM i compatibility, expanded language coverage, enterprise controls, actual approved private-model semantics, remote CI and assistive-technology tests require their own environments and measured evidence. See [delivery status](../laip-delivery-status.md).
