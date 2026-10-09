# LAIP step 8 — bounded Python/Lark free-format RPGLE

Implemented on 2026-10-07 for [execution-plan step 8](plans/laip-execution.md#step-8). The user explicitly selected Python/Lark; [the parser decision](rpgle-parser-decision.md) records how that changes the earlier reuse gate. No upstream parser spike result is claimed. Modernization and code translation remain outside scope.

## Supported analysis and limitations

`parse_statements` uses an LAIP-owned LALR grammar and locked Lark 1.3.1. The declared subset includes scalar/constants/file declarations, prototype/interface/parameter declarations, procedure/subroutine boundaries, nested arithmetic/comparison/boolean expressions, assignments/calculations, IF/ELSEIF/ELSE, DOW/DOU/FOR, SELECT/WHEN/OTHER, RETURN/LEAVE/ITER, calls/EXSR, supported file/error operations and MONITOR/ON-ERROR. Lexer handling preserves strings, doubled quotes, comments, original character offsets and standalone directives. Unsupported syntax, qualifiers or malformed constructs remain opaque statements with diagnostics; this is not full RPG compiler semantics.

`expand_source` uses only accepted in-memory source members. Qualified and scoped `/copy` and `/include` directives resolve against explicit canonical identities and declared libraries. Missing, ambiguous, cyclic or limited includes retain their original directives and barrier offsets. Comment/string lookalikes do not expand. Nested/repeated includes retain original artifact, Unicode line/column, raw byte range and decoded UTF-8 byte range through origin maps, including EBCDIC and UTF-16. Include identity spelling is exact; no implicit case repair merges members.

`build_ir` produces versioned statement nodes, control-flow edges, separate procedure/subroutine entries and intra-scope may-reaching definitions plus definite-definedness. Branch joins distinguish may from definite. DOU tests are evaluated after the body with condition provenance retained on the end node; ITER reaches the innermost loop end and LEAVE exits. These transfer semantics were checked against [IBM ITER documentation](https://www.ibm.com/docs/en/i/7.4.0?topic=codes-iter-iterate). Numeric loop values, call targets, runtime execution and complete error semantics are not proven.

Unknown expression invocations and opaque syntax create analysis barriers. File/call operations invalidate unsupported variable facts, including EXFMT input effects. Malformed branch sequences block conclusions. `conclusions_allowed` means the structural/declared-subset barrier gate only; it is not an assertion of compiler completeness or a verified business rule. Rule extraction and business verification remain later steps.

## Provenance, storage and resource boundaries

`analyze_rpgle` loads digest-verified decoded blobs before publication, expands includes, parses, builds IR and composes statement evidence with original spans. Source-member subjects remain explicitly identified. IR JSON is stored privately with the provider/profile, Lark version, root path and accepted input configuration. `register_analysis` freezes a distinct analysis run against a sealed/partial import's immutable basis. `persist_analysis` performs database-only writes through an existing fenced analysis checkpoint; exact replay preserves evidence and private IR identities.

Input/output ceilings are 32 MiB, with a shared 120-second analysis deadline. Include, parser and IR layers add independent depth/count/token/statement/work/time ceilings. Cancellation and resource exhaustion fail rather than assert complete conclusions. No imported code, Git hook, compiler, setup command, remote include or system collection is executed. Public analysis screens/routes and automatic worker dispatch remain later workflow work.

## Validation

- Tests first failed with missing modules; subsequent regressions exposed and fixed canonical identity composition, expression invocation effects, EXFMT stale facts, empty branch edges, DOU/ITER ordering and invalid terminal branch sequences.
- Independent code review approved after rechecking all findings. It independently ran 62 focused tests including PostgreSQL publication, then approved the final declaration grammar and branch-boundary transfer fixes after separately running 29 parser tests and 25 IR tests.
- Root full suite: 282 tests passed; combined statement/branch coverage 93.63%. Ruff, formatting, strict mypy, frontend type checks, three frontend tests and production build passed.
- `make rpgle-check` passed 70 tests across parser, includes, IR and real imported fixture publication, including source mappings, nested branch joins, LEAVE/ITER transfers and unknown constructs blocking conclusions.
- Docker rebuild and startup smoke passed. All four application services are healthy; page and health-route checks passed on loopback port 3030 with AI off.

The synthetic fixtures contain no enterprise source. [Dependency notices](dependency-notices.md) inventory the new MIT dependency and preserve the installed license; the grammar contains no copied upstream RPG source.

## Repeatable checks and implementation

`make rpgle-check` runs focused locked tests with Docker PostgreSQL. `make check` runs all backend/frontend checks. `make up smoke` rebuilds localhost services with AI off. The application remains available on port 3030.

- [Parser](../backend/src/laip/rpgle_parser.py)
- [Includes and span composition](../backend/src/laip/rpgle_source.py)
- [CFG and data flow](../backend/src/laip/rpgle_ir.py)
- [Private analysis and evidence publication](../backend/src/laip/rpgle_analysis.py)
- [Synthetic fixtures](../backend/tests/fixtures/rpgle)
