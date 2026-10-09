# LAIP step 9 — bounded CL/CLLE analysis

Implementation and acceptance record for [execution-plan step 9](plans/laip-execution.md#step-9), 2026-10-07. Modernization and code translation remain outside scope.

## Source semantics and boundaries

The declared syntax subset uses the existing locked Lark 1.3.1 dependency and an LAIP-owned grammar. No commands or imported programs are executed. Unknown syntax and dynamic references remain explicit; static references describe source spelling and do not establish runtime object identity.

`cl_parser.parse_statements` recognizes keyword-form PGM/ENDPGM, DCL, CHGVAR, IF/ELSE, DO/DOWHILE/DOUNTIL/DOFOR/ENDDO, unlabelled LEAVE/ITERATE, RETURN, CALL/CALLPRC, SBMJOB, OVRDBF/DLTOVR, MONMSG, SNDPGMMSG/SNDUSRMSG and RCVMSG. Expressions have validated arithmetic, comparison, logical and concatenation ASTs. Command keywords and required operands are allowlisted; duplicates, unsupported qualifiers, labels, positional forms, unknown commands and malformed expressions remain opaque with diagnostics.

Quotes, doubled quote escapes and comments are tokenized as source. Continued statements retain their complete original character ranges while normalized tokens reflect the plus/minus distinction. Embedded THEN/CMD/EXEC commands are validated recursively. Their source spans conservatively cover the enclosing original command rather than claiming exact child coordinates after normalization.

`cl_ir.build_ir` lowers CL groups and embedded commands into separate nodes for the existing bounded flow engine. IF/ELSE arms join, inline assignments stay conditional, DOWHILE retests at its header, DOUNTIL tests after its body, and ITERATE/LEAVE target the enclosing loop. Simple DO groups do not become loops. Intra-scope may-reaching definitions and definite-definedness describe syntactic data flow, not runtime values.

Command-level MONMSG handlers have possible exception paths and are skipped on normal fall-through. Program-level monitoring and monitors attached to complex groups are explicit barriers in this version. Error matching, runtime message delivery, labelled loop transfers and complete compiler semantics are not established. SBMJOB retains submitted command references in a separate job context; those commands are not inserted into the current job's flow. OVRDBF/DLTOVR retain file, target, member and scope while leaving actual opened file identity unresolved. Calls and external effects invalidate unsupported variable facts. Dynamic targets and opaque constructs block complete conclusions.

## Evidence and resource boundaries

`cl_analysis.analyze_cl` accepts only manifest-declared, decoded CL/CLLE source roots and verifies private blobs. Statement evidence retains original raw-byte and Unicode coordinates through existing origin maps, including EBCDIC. Private versioned IR and source observations use provider `laip-cl-lark` version `0.1.0` and profile `cl-subset-0.1.0`. Frozen analysis bases preserve namespace, input configuration and provider; publication uses durable fenced job checkpoints with exact replay.

The analyzer bounds input/output at 32 MiB and shares a 120-second deadline. Parser limits additionally bound bytes, statements, statement length, tokens, expression/embedded nesting and operator chains; IR limits bound nodes, depth, work and time. Cancellation and resource exhaustion fail explicitly. No RPG include expansion, remote collection, setup, compiler, command execution or AI call occurs. Public routes/screens and automatic worker dispatch remain later workflow work. No dependency or database migration was added.

Primary references used to check the implementation:

- [IBM CL command coding rules](https://www.ibm.com/docs/en/i/7.6.0?topic=commands-cl-command-coding-rules): comments, doubled quotes and keyword/list syntax.
- [IBM CL command continuation](https://www.ibm.com/docs/vi/ssw_ibm_i_74/rbam6/rbam6commandcont.htm): plus removes next-record indentation; minus preserves it, including inside strings.
- [IBM DOUNTIL](https://www.ibm.com/docs/en/i/7.4.0?topic=d-do-until): body executes before the condition test.
- [IBM MONMSG](https://www.ibm.com/docs/en/i/7.4.0?topic=ssw_ibm_i_74%2Fcl%2Fmonmsg.html): command-level and program-level monitoring differ.
- [IBM OVRDBF](https://www.ibm.com/docs/en/i/7.4.0?topic=ssw_ibm_i_74%2Fcl%2Fovrdbf.html): overrides retain scope and do not prove actual opened file identity.
- [IBM SBMJOB](https://www.ibm.com/docs/en/i/7.4.0?topic=ssw_ibm_i_74%2Fcl%2Fsbmjob.html): submitted CMD belongs to the batch job.

## Validation

- Tests first failed on absent modules and then on incorrect branch, loop and handler behavior; the corresponding supported-path regressions now pass.
- Full backend suite: 340 passed, with 93.40% combined statement/branch coverage. Ruff, formatting and strict mypy passed. Web type checks, three web tests and production build passed.
- `make cl-check`: 58 passed, covering supported fixture facts, quotes/continuations, expression reads, nested branches, loop transfers, exception handlers, submitted-job isolation, unresolved overrides, cancellation/resource limits, source mappings and real PostgreSQL fenced publication/replay.
- Independent review approved private evidence publication, then the complete parser/IR after fixing false reads of assignment targets. The reviewer independently ran all 55 final parser/IR tests; no remaining findings.
- Final Docker build, Compose boundary validation and startup smoke passed: four healthy application services, localhost page/BFF and AI disabled. The temporary test database was removed after validation; application services remain running at port 3030.

Repeatable acceptance: `make cl-check`. Complete checks: `make check`. Docker startup: `LAIP_WEB_PORT=3030 make up smoke`.

Implementation: [parser](../backend/src/laip/cl_parser.py), [CL IR](../backend/src/laip/cl_ir.py), [private evidence publication](../backend/src/laip/cl_analysis.py), and [synthetic fixtures](../backend/tests/fixtures/cl). Fixtures contain no enterprise source and are never executed.
