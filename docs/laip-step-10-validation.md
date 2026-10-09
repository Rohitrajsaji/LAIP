# LAIP step 10 — positional DDS analysis

Implementation record for [execution-plan step 10](plans/laip-execution.md#step-10), completed checks recorded on 2026-10-08. Modernization and code translation remain outside scope.

The LAIP-owned positional reader and existing locked Lark keyword grammar inspect physical, logical and display DDS source without compiling or executing it. Fixed-column record formats, fields, keys, references, lengths/types/decimals, usage, display coordinates and simple positive/negated option indicators remain explicit. Keyword syntax covers a declared subset of references, display attributes and validation keywords. Unknown keywords, malformed positions and unavailable descriptions create diagnostic barriers.

Original physical columns and keyword continuation segments are preserved. Plus resumes at the first nonblank function character; minus resumes at column 45. Quoted strings and doubled quotes survive normalization while evidence cites the original raw-byte ranges, including encoded source. Conditional keyword rows retain their own indicators rather than becoming unconditional field attributes.

Qualified definitions include system namespace, source identity and record scope. PFILE/JFILE and REF/REFFLD relationships retain source spelling. External descriptions resolve only against supplied physical DDS artifacts with explicit imported Table identities; filenames are not treated as proof of object identity. Unqualified names, missing descriptions and ambiguity remain unresolved. Local references require preceding supplied fields. Inheritance is limited to available length/type/decimal source attributes; runtime access paths and screen behavior are not inferred.

Private versioned IR and source observations use provider `laip-dds-lark` version `0.1.0`. Imports, configurations and provider bases are frozen; durable publication uses existing fenced checkpoints and exact replay. Limits bound input/output bytes, physical lines, continued statements, keyword counts, semantic nodes/work and time. No dependencies, migrations, public analysis routes or automatic worker handlers were added.

Primary specifications checked:

- [IBM positional entries](https://www.ibm.com/docs/en/i/7.4.0?topic=dplfud-positional-entries-physical-logical-files-positions-1-through-44).
- [IBM DDS keyword and continuation rules](https://www.ibm.com/docs/en/i/7.5.0?topic=terms-rules-dds-keywords-parameter-values).
- [IBM PFILE](https://www.ibm.com/docs/en/i/7.5.0?topic=p-pfile).
- [IBM REFFLD](https://www.ibm.com/docs/en/i/7.6.0?topic=80-reffld-referenced-field-keywordphysical-files-only).
- [IBM display coordinates](https://www.ibm.com/docs/en/i/7.5.0?topic=44-location-display-files-positions-39-through).
- [IBM option indicators](https://www.ibm.com/docs/nl/ssw_ibm_i_74/rzakc/pos716.htm).

## Validation

Test-first module failures were captured before implementation. Regressions exposed incorrect association across opaque record boundaries, duplicate unnamed constants and malformed reference operands; all were corrected.

- Full backend suite: 416 passed, with 93.18% combined statement/branch coverage. Ruff, formatting and strict mypy passed.
- Focused DDS acceptance: 76 passed, including physical/logical/display fixtures, indicators, validation keywords, raw column mappings, quoted continuations, namespace distinctions, supplied-description resolution, missing-description diagnostics, encoded source and PostgreSQL fenced publication/replay.
- Independent code review approved after rechecking the scope and reference corrections. It independently ran all 76 DDS tests; no remaining findings.
- Web type checks, three web tests and production build passed. Final Docker build, Compose boundary validation and startup smoke passed: four healthy application services, localhost page/BFF and AI disabled.

The temporary test stack is removed after validation. Application services remain running on localhost port 3030. This is a declared syntax subset: join/select/omit specifications, complex indicator continuation conditions and other unsupported keywords remain explicit barriers; no full compiler conformance or runtime behavior is asserted.

Repeatable checks: `make dds-check`, `make check`, and `LAIP_WEB_PORT=3030 make up smoke`.

Implementation: [parser](../backend/src/laip/dds_parser.py), [definitions and relationships](../backend/src/laip/dds_ir.py), [private evidence publication](../backend/src/laip/dds_analysis.py), [synthetic fixtures](../backend/tests/fixtures/dds). Fixtures contain no enterprise source and are never executed.
