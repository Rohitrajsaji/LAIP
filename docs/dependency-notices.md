# LAIP dependency notices

Recorded 9 October 2026 from the exact [backend lockfile](../backend/uv.lock) and [web lockfile](../web/package-lock.json). Locked versions are authoritative; locally installed metadata was used only when the version matched. The [machine-readable inventory](packaging/dependencies.json) records lock hashes, dependency edges, platform selectors, license declarations, retained notice paths and SHA-256 hashes. License declarations are supplier metadata, not legal approval.

## Coverage and distribution boundary

The conservative runtime inventory contains **79 packages: 25 Python and 54 npm**, including platform-specific optional candidates. **48 installed versions match; 31 are absent locally; none mismatch.** A total of **175 license/NOTICE/COPYING files** are retained in [licenses/runtime](licenses/runtime/README.md). The Next.js collection includes 132 compiled/vendor license files. Metadata hashes record the installed evidence used for declarations.

Four matching npm packages contain no standalone license/notice file in the local installation: `@img/sharp-libvips-darwin-arm64`, `@next/env`, `@next/swc-darwin-arm64`, and `client-only`. Their declared license remains recorded, but notice completeness is unresolved. Available READMEs and libvips `versions.json` are retained as supporting metadata, without counting them as license files.

Python traversal starts at `laip-backend` runtime dependencies and requested `psycopg[binary]`; it conservatively includes dependencies behind platform markers, including absent Windows `tzdata`. npm candidates are every non-development entry in its lockfile. These candidates may exceed the packages traced into the production Next.js standalone image. This inventory does not establish the final Linux image contents.

## Python runtime candidates

| Package | Locked version | Declared license | Local notices |
|---|---|---|---|
| annotated-doc | 0.0.5 | MIT | 1 |
| annotated-types | 0.8.0 | MIT | 1 |
| anyio | 4.15.1 | MIT | 1 |
| attrs | 26.1.0 | MIT | 1 |
| click | 8.5.0 | BSD-3-Clause | 1 |
| fastapi | 0.142.2 | MIT | 1 |
| h11 | 0.16.0 | MIT | 1 |
| idna | 3.20 | BSD-3-Clause | 1 |
| jsonschema | 4.26.0 | MIT | 1 |
| jsonschema-specifications | 2025.9.1 | MIT | 1 |
| lark | 1.3.1 | MIT | 1 |
| opentelemetry-api | 1.45.1 | Apache-2.0 | 1 |
| psycopg | 3.3.6 | LGPL-3.0-only | 1 |
| psycopg-binary | 3.3.6 | LGPL-3.0-only | 1 |
| pydantic | 2.13.5 | MIT | 1 |
| pydantic-core | 2.46.5 | MIT | 1 |
| pydantic-settings | 2.15.0 | MIT | 1 |
| python-dotenv | 1.2.4 | BSD-3-Clause | 1 |
| referencing | 0.37.0 | MIT | 1 |
| rpds-py | 2026.9.1 | MIT | 1 |
| starlette | 1.7.0 | BSD-3-Clause | 1 |
| typing-extensions | 4.16.0 | PSF-2.0 | 1 |
| typing-inspection | 0.4.4 | MIT | 1 |
| tzdata | 2026.5 | Unknown: not installed locally | Not installed |
| uvicorn | 0.54.0 | BSD-3-Clause | 1 |

## npm runtime candidates

| Package | Locked version | Lockfile license | Local notices |
|---|---|---|---|
| @emnapi/runtime | 1.11.3 | MIT | 1 |
| @img/colour | 1.1.0 | MIT | 1 |
| @img/sharp-darwin-arm64 | 0.35.5 | Apache-2.0 | 1 |
| @img/sharp-darwin-x64 | 0.35.5 | Apache-2.0 | Not installed |
| @img/sharp-freebsd-wasm32 | 0.35.5 | Apache-2.0 | Not installed |
| @img/sharp-libvips-darwin-arm64 | 1.3.4 | LGPL-3.0-or-later | 0 |
| @img/sharp-libvips-darwin-x64 | 1.3.4 | LGPL-3.0-or-later | Not installed |
| @img/sharp-libvips-linux-arm | 1.3.4 | LGPL-3.0-or-later | Not installed |
| @img/sharp-libvips-linux-arm64 | 1.3.4 | LGPL-3.0-or-later | Not installed |
| @img/sharp-libvips-linux-ppc64 | 1.3.4 | LGPL-3.0-or-later | Not installed |
| @img/sharp-libvips-linux-riscv64 | 1.3.4 | LGPL-3.0-or-later | Not installed |
| @img/sharp-libvips-linux-s390x | 1.3.4 | LGPL-3.0-or-later | Not installed |
| @img/sharp-libvips-linux-x64 | 1.3.4 | LGPL-3.0-or-later | Not installed |
| @img/sharp-libvips-linuxmusl-arm64 | 1.3.4 | LGPL-3.0-or-later | Not installed |
| @img/sharp-libvips-linuxmusl-x64 | 1.3.4 | LGPL-3.0-or-later | Not installed |
| @img/sharp-linux-arm | 0.35.5 | Apache-2.0 | Not installed |
| @img/sharp-linux-arm64 | 0.35.5 | Apache-2.0 | Not installed |
| @img/sharp-linux-ppc64 | 0.35.5 | Apache-2.0 | Not installed |
| @img/sharp-linux-riscv64 | 0.35.5 | Apache-2.0 | Not installed |
| @img/sharp-linux-s390x | 0.35.5 | Apache-2.0 | Not installed |
| @img/sharp-linux-x64 | 0.35.5 | Apache-2.0 | Not installed |
| @img/sharp-linuxmusl-arm64 | 0.35.5 | Apache-2.0 | Not installed |
| @img/sharp-linuxmusl-x64 | 0.35.5 | Apache-2.0 | Not installed |
| @img/sharp-wasm32 | 0.35.5 | Apache-2.0 AND LGPL-3.0-or-later AND MIT | 1 |
| @img/sharp-webcontainers-wasm32 | 0.35.5 | Apache-2.0 | Not installed |
| @img/sharp-win32-arm64 | 0.35.5 | Apache-2.0 AND LGPL-3.0-or-later | Not installed |
| @img/sharp-win32-ia32 | 0.35.5 | Apache-2.0 AND LGPL-3.0-or-later | Not installed |
| @img/sharp-win32-x64 | 0.35.5 | Apache-2.0 AND LGPL-3.0-or-later | Not installed |
| @next/env | 16.4.0 | MIT | 0 |
| @next/swc-darwin-arm64 | 16.4.0 | MIT | 0 |
| @next/swc-darwin-x64 | 16.4.0 | MIT | Not installed |
| @next/swc-linux-arm64-gnu | 16.4.0 | MIT | Not installed |
| @next/swc-linux-arm64-musl | 16.4.0 | MIT | Not installed |
| @next/swc-linux-x64-gnu | 16.4.0 | MIT | Not installed |
| @next/swc-linux-x64-musl | 16.4.0 | MIT | Not installed |
| @next/swc-win32-arm64-msvc | 16.4.0 | MIT | Not installed |
| @next/swc-win32-x64-msvc | 16.4.0 | MIT | Not installed |
| @swc/helpers | 0.5.23 | Apache-2.0 | 1 |
| baseline-browser-mapping | 2.11.27 | Apache-2.0 | 1 |
| caniuse-lite | 1.0.30001814 | CC-BY-4.0 | 1 |
| client-only | 0.0.1 | MIT | 0 |
| detect-libc | 2.1.2 | Apache-2.0 | 1 |
| nanoid | 3.3.20 | MIT | 1 |
| next | 16.4.0 | MIT | 132 |
| postcss | 8.5.23 | MIT | 1 |
| picocolors | 1.1.1 | ISC | 1 |
| react | 19.3.0 | MIT | 1 |
| react-dom | 19.3.0 | MIT | 1 |
| scheduler | 0.28.0 | MIT | 1 |
| semver | 7.8.5 | ISC | 1 |
| sharp | 0.35.5 | Apache-2.0 | 1 |
| source-map-js | 1.2.2 | BSD-3-Clause | 1 |
| styled-jsx | 5.1.6 | MIT | 1 |
| tslib | 2.8.1 | 0BSD | 1 |

## Retained historical notices

The original [Lark 1.3.1 MIT notice](licenses/lark-1.3.1-LICENSE.txt) remains intact. The LAIP-owned grammar is project code; no IBM source grammar or upstream RPG parser was copied. The [REA dependency assessment](rea-assessment.md) remains the reference for the optional, separately installed pinned REA adapter; REA engines are not included by this runtime inventory.

## Exclusions and release gates

- Development and build-only Python/npm packages are listed explicitly in the JSON inventory. They are excluded from this runtime notice collection. Source release consumers obtain them via the lockfiles; redistributing a development bundle needs a separate audit.
- Docker base images, OS/native libraries, the copied `uv` executable and the PostgreSQL/pgvector image need their own final-image inventory and notices. Version/digest pins alone do not supply these notices.
- Optional npm platform packages absent on macOS need notices collected for the actual Linux architecture/libc distribution before shipping containers. Unused platform packages are candidates rather than confirmed bundled content.
- `psycopg` and `psycopg-binary` declare **LGPL-3.0-only**. Binary wheels can bundle native libraries. Distribution-specific source/relinking, notice and other license obligations must be evaluated before redistributing binaries. Optional sharp/libvips declarations also include LGPL; retained local metadata does not discharge target-image obligations.
- Retained Next.js compiled/vendor notices do not prove that every vendored component in a final image is inventoried. Produce and review a final-image SBOM before binary distribution.
- Video project dependencies and asset/font notices belong to its separate media distribution. They are outside the backend/web package inventory.
- The project license remains undecided. This file does not grant rights to LAIP project code or private banking inputs and does not declare release readiness.

No dependency versions or execution settings were changed; AI remains off by default.
