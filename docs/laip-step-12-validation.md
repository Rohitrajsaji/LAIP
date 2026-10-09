# LAIP step 12 — snapshot retrieval, bounded context and optional private AI

Implementation record for [execution-plan step 12](plans/laip-execution.md#step-12), checks on 2026-10-08. Modernization and code translation remain outside scope.

`RetrievalService` provides qualified exact lookup, PostgreSQL keyword search, incoming/outgoing graph traversal, selected chunks and evidence closure. Reads filter by authorized namespace and immutable snapshot membership. Review state uses the snapshot's sequence watermark rather than current review heads. Dynamic and ambiguous graph links remain boundaries; depth/node/edge limits are visible. Short read transactions bound SQL statements and restore prior timeout settings, including inside an existing transaction.

`build_context` deduplicates selected records and keeps complete supporting/contradicting evidence with whole chunks. The final serialized package, citations, omission accounting and output reserve are counted. Offline counts explicitly use a UTF-8 byte upper bound. Approved exact tokenizer callbacks have separate validation and cannot be replaced silently by that estimate. Conventional credential fields/assignments are masked in display projections; original evidence IDs and record hashes remain unchanged. This bounded local policy is not a general data-loss-prevention system.

Private model configuration requires explicit operator approval, exact endpoint allowlisting, model revision/policy/tokenizer/window and embedding dimensions. Supported addresses are numeric loopback/private IPs; HTTP is restricted to loopback and other private endpoints require HTTPS. The transport has no DNS, environment proxies, redirect following, downloads, tools or command execution. Request/response sizes and elapsed time are bounded; errors and representations omit credentials. No endpoint is configured or enabled in the running application.

Optional semantic/hybrid retrieval first matches the installed pgvector model revision/dimension/policy and selected namespace, then embeds the query at the approved endpoint. Vector operations are schema-qualified and snapshot-bound. Similarity never changes source certainty or review status. Vector installation remains an explicit migration-owner operation; normal startup does not install a model or vector profile.

Optional LLM output can select only exact source projections already present in selected context, with matching fact hashes and the full cited ID set. New prose, unsupported values, unknown IDs, changed hashes and extra assertion fields are rejected before returning an interpretation. Returned selections remain inferred and pending review. Retrieved instruction text is quoted data and cannot change permissions or endpoints. Publication of new natural-language business claims is deliberately unavailable through this seam.

These are internal application services. REST/MCP integration and the analyst UI follow the later plan steps. The private endpoint protocol uses version-pinned model metadata plus `embedding`/`explain` operations and strict responses; it does not assume arbitrary providers implement the same protocol.

## Validation status

Focused fixtures cover snapshot lookup/search/graph/evidence, review-watermark isolation, citation closure, masking, final-budget counting, exact tokenizers, disabled configuration, model profiles, injection and unsupported output. Separate semantic integration uses an inert loopback HTTP fixture and real Docker pgvector; it is not a quality benchmark or live enterprise/model validation. HTTP fixtures also test redirects, wrong response types and deadlines.

The delegated agents stopped at the account usage limit before their final database/code/security review stages. The primary agent completed integration and inspected parameterized SQL, namespace/snapshot filters, token/provenance validation and private transport boundaries. Independent review completion remains pending; no independent approval is claimed.

`make check` passed: 568 backend tests with 92.15% combined statement/branch coverage, Ruff lint/format checks, strict mypy across 39 modules, and web type checks, three tests and production build. A final focused run passed all 48 private-AI/semantic tests after integration.

`LAIP_WEB_PORT=3030 make up smoke` rebuilt the Docker services and passed Compose boundary validation and startup smoke: four healthy services, localhost page/BFF, and AI disabled. The application remains available at `http://127.0.0.1:3030/`; the temporary test database was removed after validation. Independent review stages remain pending as described above.

Repeatable checks: `make retrieval-check`, separately `make semantic-check`, `make check`, and `LAIP_WEB_PORT=3030 make up smoke`.
