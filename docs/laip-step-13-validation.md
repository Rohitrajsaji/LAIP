# LAIP step 13 — linked knowledge exports and read-only MCP

Implementation record for [execution-plan step 13](plans/laip-execution.md#step-13), checked on 2026-10-08. Application modernization and code translation remain outside scope.

`exports.build_export` constructs a bounded snapshot bundle with schema `0.1.0`. The bundle contains a manifest, snapshot knowledge index, linked system/application/program/database/rule/workflow JSON and Markdown, evidence JSONL/index, linked context JSONL, graph JSON and Mermaid. Empty categories retain an index rather than inventing records. Wrapper records preserve original IDs/hashes, certainty and snapshot review projections; masked display records are not replacement canonical observations. Evidence links retain support and contradiction provenance. Snapshot/run/provider versions, review watermark, policy and static-analysis limitations remain explicit.

The manifest lists the exact byte length, media type and SHA256 of every file except itself. `validate_bundle` checks versioned structures, paths, references and hashes before deterministic ZIP construction. Generated filenames use hashes rather than source names. Markdown names and Mermaid labels are escaped as inert text; unresolved/dynamic targets never become resolved diagram paths. `publish_export` atomically writes the validated archive to existing private content-addressed storage. This returns a blob reference, not a public download. Durable export acceptance/fencing/checkpoint ownership and UI/download orchestration use existing jobs/storage seams in the analyst-workflow step; this module does not mutate its basis analysis run or publish an incomplete download.

Full source defaults absent. `source_included=True` additionally requires a separately authorized trusted source-reader capability; a caller-supplied path is never accepted. Source artifacts belong to the selected run/namespace, and exact raw byte hashes/lengths must match. Source inclusion is unavailable through MCP. Supporting display excerpts are bounded and masked; the credential policy is not general DLP.

`ReadService` is the shared deterministic read boundary for REST and MCP. Requests use strict typed versioned schemas, an operator-authorized namespace and explicit immutable snapshot. Exact entity reads, policy-filtered keyword search, bounded graph traversal, evidence closure and deterministic context use the existing retrieval service. Review state uses the snapshot watermark. Display masking keeps original record hashes separately. Context counts its canonical serialized package and output reserve; its offline tokenizer is explicitly a UTF-8 byte upper bound, not authorization for a model window. It performs no AI calls, jobs, reviews or other writes.

Private REST routes are `GET /api/v1/entities/{entity_id}`, `GET /api/v1/evidence/{evidence_id}`, and `POST /api/v1/retrieval/query`, `/api/v1/graph/query`, `/api/v1/context/build`. Entity/evidence GET requests require `snapshot_id`; POST inputs include `schema_version:"0.1.0"`. The retrieval POST currently exposes keyword mode; exact lookup and graph reads have their dedicated routes. Bearer authentication, trusted hosts and explicit Origin allowlisting apply. Bodies, concurrent reads, query work, statement deadlines and outputs are bounded. Errors omit request data, database internals and credentials. Production reads open PostgreSQL sessions with `default_transaction_read_only=on`. No public backend port was added.

`python -m laip.mcp` implements the newline-delimited JSON-RPC stdio transport with initialization, ping and typed tools/list/tools/call. The only exposed tools are `laip_entity`, `laip_search`, `laip_graph`, `laip_evidence` and `laip_context`; no resources, command, mutation, raw-source, export, AI or configuration tools are offered. Successful structured content matches the shared REST success envelope. Domain failures use `isError:true` and a redacted versioned error envelope in text; malformed arguments use protocol errors. One active tool read, frame/output limits and transport cancellation bound work without cancelling durable analysis. Only protocol messages appear on stdout. The implementation follows the [MCP stdio transport](https://modelcontextprotocol.io/specification/2025-11-25/basic/transports), [lifecycle](https://modelcontextprotocol.io/specification/2025-11-25/basic/lifecycle) and [tools](https://modelcontextprotocol.io/specification/2025-11-25/server/tools) specifications; no SDK dependency was added.

## Operator configuration

REST reads require an explicit `LAIP_READ_NAMESPACE`; MCP requires an explicit `LAIP_MCP_NAMESPACE`. Both default unset and fail closed for knowledge reads. Use a namespace already imported/analyzed/published locally. Database/service-token configuration continues to use the existing private environment or secret files. Origin policy defaults to `http://127.0.0.1:3030` and `http://localhost:3030`; other approved local ports need an explicit `LAIP_ALLOWED_READ_ORIGINS` JSON array. Configure these operator settings in a private Compose override or the local process environment; they cannot be supplied as tool arguments. AI remains off.

For the Docker deployment, an operator can launch stdio MCP with the existing restricted API-container configuration:

```sh
docker compose exec -T -e LAIP_MCP_NAMESPACE=your-authorized-namespace api python -m laip.mcp
```

The MCP client owns stdin/stdout; do not enable a TTY or redirect credentials into protocol output. This launch does not open a listener or change application data. A local non-Docker process uses `uv run --project backend --locked python -m laip.mcp` with the same operator settings.

## Validation and handoffs

Repeatable focused acceptance: `make export-mcp-check`. All schemas for read tool inputs/outputs are recorded in [read-tools.json](contracts/0.1.0/read-tools.json); export projections use [export.schema.json](contracts/0.1.0/export.schema.json) alongside the existing canonical manifest/evidence contracts.

Tests cover deterministic bytes/checksums, self-excluding manifest, schema tampering, dangling links, safe paths/labels, masking, explicit exact raw-source inclusion, workflow records, cancellation/resource limits, authenticated REST, shared MCP parity, and real stdio-to-PostgreSQL execution. Fixtures are synthetic supplied-artifact examples, not live IBM i discovery or enterprise validation.

The ECC TDD implementation handoff preceded independent code review and security review. Code review identified export structural/link validation and entity label handling; both were corrected and the follow-up verdict was APPROVE with no remaining findings. Final independent security recheck passed with no outstanding findings. Unchanged locked dependencies introduce no new package notice.

`make check` passed: 600 backend tests, 91.78% combined statement/branch coverage, Ruff lint/format checks across 91 files, strict mypy across 43 modules, and web type checks, three tests and production build. The 14 export fixtures and 11 MCP fixtures passed, with additional real stdio PostgreSQL, shared-read and REST acceptance fixtures. Recorded read input/output schemas validate and match runtime contracts; the two export-schema copies match exactly.

`LAIP_WEB_PORT=3030 make up smoke` rebuilt the deployment and passed Compose boundary validation plus startup smoke: four healthy services, localhost page/BFF and AI disabled. A container import check also confirmed the packaged export schema and all five MCP read operations. The application remains at `http://127.0.0.1:3030/`; the isolated test database was removed after validation.
