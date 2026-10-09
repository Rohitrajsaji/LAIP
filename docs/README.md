# LAIP documentation

LAIP extracts evidence-linked business rules and system context for downstream AI-assisted modernization. Start with the [product README](../README.md) and [developer/operator guide](development.md).

| Need | Canonical reference |
|---|---|
| Extraction flow, components and interfaces | [Architecture](laip-architecture.md) |
| Current facts, generic subjects and version gates | [Schema 0.2.0](contracts/0.2.0/README.md), [contract ADR](adr/0010-banking-extraction-contracts.md) |
| Historical compatibility and source manifests | [Schema 0.1.0](contracts/0.1.0/README.md), [offline manifests](contracts/0.1.0/offline-metadata.md), [inventory metadata](contracts/0.1.0/inventory-metadata.md) |
| Parser and adapter decisions | [RPGLE decision](rpgle-parser-decision.md), [REA assessment](rea-assessment.md) |
| What was tested and its limits | [Validation index](validation/README.md) |
| Dependency notices and distribution gaps | [Dependency notices](dependency-notices.md), [release record](packaging/release.md) |
| Requirements and future work | [Backlog](laip-backlog.md), [delivery status](laip-delivery-status.md) |

## Future capabilities

These documents specify future work; they do not add runtime support: [live IBM i connector](laip-step-16-connector-milestone.md), [expanded language analysis](laip-step-17-language-roadmap.md), [enterprise operations](laip-step-18-enterprise-milestone.md), and [other platform providers](laip-step-19-provider-extensions.md).

## Implementation history

Keep the canonical [execution plan](plans/laip-execution.md), the [packaging plan](plans/laip-product-packaging.md), and the inventory/release decisions under `packaging/`. Validation reports retain their original scope and results. Private regression material is described by the [public boundary note](private-validation.md) and excluded from the public candidate. Generated execution guides and earlier media outputs are privately archived; their unique generation metadata has not been discarded.
