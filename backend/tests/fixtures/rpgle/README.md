# Synthetic RPGLE acceptance sources

These files are new LAIP fixture text, contain no enterprise code, and are never compiled or executed.

- `MAIN.rpgle`: qualified include, packed scalar and keyed input/output file declarations, procedure, nested IF/DOW calculations, file operations, monitor handler, call and return.
- `CONSTANTS.rpgle`: included constant and key-variable declarations. Analysis assertions require its original source artifact and offsets to survive expansion.
- `UNSUPPORTED.rpgle`: embedded SQL remains an opaque barrier and prevents unsupported complete conclusions.

The integration tests wrap these bytes in a versioned manifest/ZIP, retain raw bytes and mappings, and publish private IR/evidence through a durable fenced job. Parser/source/IR tests separately cover EBCDIC/UTF-16 origins, malformed and missing includes, branch joins, loop transfers, unknown effects, and resource limits. No fixture is claimed to provide full IBM compiler conformance.
