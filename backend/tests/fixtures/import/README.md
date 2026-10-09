# Synthetic offline import fixture

This fixture contains seven inert inputs: two source members named `ORDER` in different libraries and five supplied metadata envelopes. All material is synthetic LAIP test data; it does not represent a live IBM i system or an actual collection command's output.

`manifest.json` is canonical ImportManifest version `0.1.0` in namespace `synthetic:import`. It explicitly identifies `LIB1/QRPGLESRC/ORDER` and `LIB2/QRPGLESRC/ORDER`; compiled-object identities are intentionally unknown (`null`). The first source is encoded as EBCDIC cp037 (`ccsid:37`); the second is UTF-8. Read them as bytes. Do not rewrite the cp037 fixture using a text editor or normalize source newlines.

The five JSON files under `source/metadata` cover `inventory`, `schema`, `binding`, `job` and `dsp_pgmref`. Each has the exact envelope keys `schema_version`, `kind`, `records` and retains supplied descriptive fields. See [envelope contract](../../../../docs/contracts/0.1.0/offline-metadata.md).

Tests build ZIPs in memory from the exact fixture bytes and compare ZIP/local preparation and persisted artifacts. Separate generated cases exercise missing/undeclared/excluded inputs, malformed metadata, unsafe paths/symlinks, limits, cancellation, checkpoint replay and restoration from sealed private input after the original directory changes. Fixtures never run source, setup or Git hooks.
