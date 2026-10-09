# Banking extraction contracts 0.2.0

Authored JSON Schema lives in `laip.schema.json`; the backend packaged mirror is byte-identical. This additive version supports RecordFormat entities, generic rule subjects, explicit claim-local support and bounded analyzer facts. Historical 0.1.0 schemas are unchanged. See [ADR 0010](../../adr/0010-banking-extraction-contracts.md).

`banking.example.json` is a structural contract example using synthetic evidence identifiers, not an imported banking result or a semantically verified rule. Full namespace/run/evidence/span closure and support fingerprints are repository responsibilities. Neither valid JSON nor supported-within-profile status confers an analyst approval.

Fact `data` shapes are closed by kind. New parser facts must extend these authored definitions explicitly and supply tests; arbitrary provider dictionaries or executable payloads are rejected. Rule conditions/actions retain the existing source-location contract. Every outward 0.2.0 consumer must pass the compatibility gate; step 7 wires complete retrieval/export/UI negotiation.
