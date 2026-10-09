# LAIP product packaging and documentation cleanup plan

## Goal

Package LAIP clearly as a business-rules extraction tool: it analyzes legacy source and metadata, preserves evidence and uncertainty, and exports linked, structured context that downstream AI tools can ingest to support modernization. Keep the public description aligned with what the prototype actually does today. LAIP prepares context; it does not itself modernize or translate applications. AI and embeddings remain opt-in and off by default.

Prepare the source tree for a credible GitHub release by making the essential files easy to find and keeping private inputs, caches, and bulky generated media out of the distributable source package.

## Guardrails

- Do not delete a file solely because it is old, repetitive, or generated-looking. First check links, provenance, validation value, licenses, and whether it is the only record of a decision or test result.
- Preserve API/schema compatibility notes, architecture decisions, dependency notices, parser/source references, and evidence needed to reproduce claims about extraction.
- Keep the frozen banking corpus private unless its owner confirms redistribution rights. Use the synthetic corpus for public examples and tests; do not put bank sources or derived sensitive output in a public demo package.
- Do not imply broad IBM i or enterprise support based on the current bounded parser and synthetic validation. State supported inputs, known gaps, and current validation honestly.
- Do not enable AI by default, claim that exported context guarantees correct modernization, or expand this work into modernization/code translation.
- This is a plan only. No files have been removed, relocated, or published. The current workspace does not appear to have Git metadata; establish repository/remote details separately before a push.

## Target repository shape

Keep the root focused on the product entry point and reproducible local run: `README.md`, `Makefile`, Compose files, `.gitignore`, `.dockerignore`, and the `backend/`, `web/`, `scripts/`, and `docs/` source trees. Keep required lockfiles and license notices.

Keep `backend/README.md` as the operator/developer reference. Keep canonical contracts under `docs/contracts/`, the banking-contract ADR, dependency notices and license texts, parser decision/source references, and the current extraction-fix plan. Keep validation reports when they provide unique reproducible evidence; link them from a concise validation index instead of listing every milestone in the public README.

Treat `docs/plans/laip-orchestrate.md` and `docs/plans/laip-banking-extraction-orchestration.md` as likely generated execution copies of the canonical plans. Compare them against `laip-execution.md` and `laip-banking-extraction-fix.md`; if they contain no unique decisions or status, remove them from the eventual public tree or retain them in a clearly labeled historical archive outside the main docs path. Do not delete them before that comparison.

The `brag-output*` folders and `laip-explainer-*` folder are generated/presentation work rather than runtime source. Their current presence includes large dependency/render output. After checking for any unique editable source, final video, or provenance needed for the release, move reusable media source to a dedicated optional `media/` or release-assets package and keep dependency installs, caches, preview renders, and duplicate exports outside the GitHub source tree. Retain the final approved demo video only if the repository is intended to host it; otherwise link to a separately hosted video. The existing `.gitignore` excludes application caches and `.local`, but does not currently exclude these root media/output folders.

## Work plan

<a id="step-1"></a>

### 1. Inventory and classify files

Create a file inventory with four classes: required to run/build; canonical product/developer documentation; unique test, provenance, or license evidence; and generated/local/private output. Record size, references, and retention rationale for large files and each Markdown document. Inspect hidden files and nested package manifests, but never print or copy secret values from `.env`, `.local`, or other local configuration.

**Acceptance:** Every proposed removal or relocation has a recorded reason, inbound-link search, and destination or archival decision. Private fixtures and generated outputs are identified before any public packaging step.

<a id="step-2"></a>

### 2. Define the public product message and supported boundary

Use consistent language across the root README, web app about/help text if present, and release notes:

> LAIP extracts evidence-linked business rules and system context from supported legacy code and metadata, then exports that context in structured formats for downstream AI-assisted modernization.

Pair that with an honest current-state note: the current prototype supports bounded IBM i source/metadata workflows, requires human review for uncertain findings, exports context for another AI workflow, and does not perform modernization. State that AI assistance is optional and disabled by default. Avoid describing the tool as a generic code converter.

**Acceptance:** A reader can distinguish extraction, evidence/review, export, and downstream modernization at a glance; claims map to implemented workflows or are explicitly labeled as roadmap.

<a id="step-3"></a>

### 3. Simplify the root README

Rewrite `README.md` as the public landing page with this order: product purpose and short demo; what it extracts and what gets exported; a 3–5 step user workflow; quick start with Docker Compose; one-command checks; supported inputs and limitations; privacy/AI defaults; links to architecture, contracts, validation, and contributor docs. Move detailed milestone histories, banking-corpus instructions, repeated test listings, and future roadmap detail to their canonical docs. Keep the command needed to start the app and the minimum troubleshooting users need.

**Acceptance:** The README is concise enough to scan, quick-start commands work from a clean checkout, every linked document exists, and private corpus details are not exposed as public onboarding instructions.

<a id="step-4"></a>

### 4. Establish a small canonical documentation map

Add `docs/README.md` as the index for:

- architecture and supported extraction flow;
- current schemas and compatibility/migration policy;
- parser decisions and source references;
- dependency/license notices;
- current validation evidence and known limits;
- future IBM i connector, language, enterprise, and provider roadmaps;
- contributor and operator instructions.

Retain one canonical status/validation summary. Preserve milestone reports that prove unique behavior or contain artifact hashes, but group them under a clearly labeled validation/history section. Consolidate repeated prose only after checking each report for unique command output, measured results, or artifact references. Keep the two canonical implementation plans and current ADR. Archive or remove redundant orchestration prompt copies only after step 1 proves they add no unique information.

**Acceptance:** The documentation index resolves to canonical current material; each retained historical report is clearly labeled with date/scope and does not masquerade as current capability.

<a id="step-5"></a>

### 5. Make the source package clean and safe to publish

Review `.gitignore` and `.dockerignore` against the inventory. Keep application source, lockfiles, synthetic fixtures, tests, documentation, and required notices. Ignore/exclude Python/Node caches, build output, local databases and artifacts, `.local`, private fixture paths, generated brag outputs, video render intermediates, and installed media dependencies. Avoid ignoring source files needed to reproduce the optional demo. Check Docker build context exclusions separately from Git ignore rules.

Add a release checklist that checks for secrets, local absolute paths, private banking data, large generated files, missing third-party notices, and accidental test identities. Ensure dependency licenses and notices remain available in source and any distributable image/package.

**Acceptance:** A clean source archive contains only reproducible source and intentional assets; a Compose build does not copy caches/media output into service images; no secret, private corpus, or unlicensed asset appears in the release candidate.

<a id="step-6"></a>

### 6. Prepare the GitHub release package

Document a clean-clone workflow that starts the app, imports the public synthetic sample, reviews evidence/uncertainty, and exports the linked AI-ready package. Include a small sanitized example export and its schema/version information if it can be generated from the synthetic fixture. Include checksums only for intentionally shipped artifacts. Keep the demo video either as a small, approved release asset or link to a separately hosted location; do not check in `node_modules` or intermediate renders. Preserve the demo's editable source only if there is a maintainable, licensed, reproducible package for it.

Before release, establish the intended Git repository and remote, check the complete candidate diff/file list, and confirm rights for all non-synthetic assets. This plan does not initialize, push, or publish a repository.

**Acceptance:** A new user can reproduce the demonstrated extraction-to-export flow using public/synthetic inputs; the export is labeled for downstream AI ingestion and does not overstate completeness; release contents have provenance and license coverage.

## Verification gates

- Confirm all README and docs-index links resolve and all renamed/moved files have updated references.
- Validate Compose configuration and follow the documented clean-start/health workflow.
- Run the repository's documented backend and frontend checks from a clean environment; record actual results and any known failures.
- Compare the synthetic sample's extracted facts and evidence links with its existing oracle/validation expectations.
- Inspect the source archive and Docker build context for caches, `.local`, private banking corpus material, secrets, and oversized generated artifacts.
- Review product copy against shipped behavior: extraction and export are LAIP's job; downstream AI modernization is the intended use of the exported context.

## Out of scope

Application modernization or code translation, enabling AI by default, broadening parser support, publishing private banking inputs, pushing to GitHub, and deleting source/evidence artifacts before the inventory and link audit pass.
