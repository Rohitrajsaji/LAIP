# LAIP — business rules from code to AI context

LAIP (Legacy Application Intelligence Platform) extracts business rules and system context from supported legacy code and metadata, then packages them for AI-assisted modernization.

It connects rule conditions and actions to their source evidence, preserves relationships and uncertainty, and lets an analyst review or correct findings before exporting context for another AI tool.

**Legacy source → rule extraction → evidence and review → AI-ingestible context → downstream modernization**

## What you get

- Business-rule candidates with cited conditions, actions, supporting evidence and explicit barriers.
- Qualified inventory, record formats, fields, procedures, dependencies and bounded workflows.
- Inspectable source mappings, immutable corrections and review history.
- Linked JSON, JSONL, Markdown and Mermaid exports, plus read-only REST/MCP retrieval and token-aware context.

The current prototype analyzes a bounded IBM i subset: free-format RPGLE, CL/CLLE, positional DDS and supplied offline metadata. Missing, ambiguous, dynamic or unsupported inputs remain explicit. Export validation checks schemas, links and hashes; it does not imply complete extraction.

## Start locally

Install Docker with Compose v2, Python 3 and Make, then run:

```sh
make up smoke
```

Open http://127.0.0.1:3000. If that port is occupied:

```sh
LAIP_WEB_PORT=3030 make up smoke
```

Only the web interface is exposed, on localhost. Generated credentials stay in `.local/`; keep that directory private. `make down` stops services and preserves database/artifact volumes.

## Try the workflow

1. Load the synthetic fixture, or select a source ZIP and its metadata manifest.
2. Import the sources and analyze the sealed import.
3. Inspect inventory, dependencies, source evidence and extraction limitations.
4. Review rule candidates and record corrections with their reasons.
5. Create a knowledge export and supply its linked context to your chosen downstream AI workflow.

The synthetic sample deliberately includes missing includes, name collisions and dynamic calls. A partial result is expected. Full source is excluded from ordinary exports; evidence snippets can still contain source-derived content, so review the package before sharing it.

## Demo

The [65-second guided demonstration](docs/demo.md) explains Extract → Ground → Review → Prepare Context. Its diagrams are illustrations and its UI captures are synthetic. The final video and editable source are preserved as optional media, separate from the application runtime.

## Current scope

LAIP prepares context for modernization; transformation and code translation happen in the downstream workflow. AI and embeddings are **off by default**. The current deployment is a single-user localhost prototype. Live IBM i collection, expanded dialect coverage, enterprise deployment and other platform providers are documented future work.

## Develop and verify

With Python 3.12, Node 22.23.2 and uv 0.12.23:

```sh
make install
make check
```

See the [developer/operator guide](docs/development.md), [documentation index](docs/README.md), [architecture](docs/laip-architecture.md), [schema 0.2.0](docs/contracts/0.2.0/README.md), and [validation and known limits](docs/validation/README.md).

## Distribution

Source packaging excludes credentials, installed dependencies, generated caches and private banking regression material. See [dependency notices](docs/dependency-notices.md) and the [release record](docs/packaging/release.md). Project reuse terms and any outstanding distribution gates are stated there; no enterprise or live-system support certification is implied.
