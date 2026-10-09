# RPGLE parser decision — Python/Lark declared subset

Recorded 2026-10-07 for LAIP execution-plan step 8.

The earlier REA assessment selected an isolation spike of Code for i's `vscode-rpgle` parser before adopting a parser. The user's step-8 instruction explicitly selected Python/Lark. That instruction supersedes the earlier selection gate for this implementation. No upstream-parser spike failure or conformance result is claimed, and no upstream RPG grammar or VS Code code is vendored.

The implementation uses locked `lark==1.3.1` and an LAIP-owned bounded LALR statement/expression grammar. Lark's supported position propagation is documented in its [official API](https://lark-parser.readthedocs.io/en/stable/classes.html); the [PyPI release](https://pypi.org/project/lark/1.3.1/) identifies the MIT license. The installed wheel's license notice is preserved in [lark-1.3.1-LICENSE.txt](licenses/lark-1.3.1-LICENSE.txt). No additional runtime dependencies were introduced transitively.

This choice supports only the documented free-format subset. It does not establish equivalence with an IBM i compiler, fixed-format RPG, embedded SQL or arbitrary preprocessor evaluation. Unknown statements remain opaque barriers, unresolved includes retain their original directives, and file/call effects invalidate unsupported variable facts. Parser recognition alone never verifies runtime behavior or business-rule correctness.

The native Python service remains inert: no source execution, include filesystem access, compiler invocation, setup hooks or network collection occur. Imported raw bytes and decoded origin maps remain the authority for evidence. Private IR artifacts carry the provider/profile, accepted input configuration, root path and dependency version; publication uses the existing durable analysis job fence.
