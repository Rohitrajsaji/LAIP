# Offline metadata envelopes — version 0.1.0

The internal offline-import adapters accept supplied JSON envelopes for `inventory`, `schema`, `binding`, `job` and `dsp_pgmref`. A canonical ImportManifest artifact's `kind` must match the envelope's `kind`.

```json
{
  "schema_version": "0.1.0",
  "kind": "binding",
  "records": [
    {
      "library": "LIB1",
      "object": "ORDER",
      "synthetic": true,
      "limitations": ["Supplied synthetic export only"]
    }
  ]
}
```

Only these three envelope properties are accepted. `records` is an array of nonempty opaque JSON objects; an empty array is allowed. Top-level record keys use 1–128 portable ASCII letters, digits, underscore, dot, space or hyphen. Nested values use LAIP's canonical JSON profile: strings, booleans, null, arrays, objects and safe integers; duplicate keys, nonfinite/fractional numbers and invalid Unicode fail. Defaults cap a decoded metadata envelope at 16 MiB and 100,000 records, with an additional aggregate import budget.

This version deliberately preserves supplied record fields rather than interpreting object names, relationships, binding semantics or live release compatibility. No SQL statement, command text, package metadata or imported field executes. Raw bytes are retained separately from the decoded view. An invalid envelope yields a failed artifact status and diagnostic while preserving raw bytes; metadata does not become a verified live-system fact.

The envelope's collection method/time, encoding, system authority and artifact identity come from the separately validated ImportManifest. These files are an explicit supplied-export format; raw textual DSPPGMREF output and arbitrary vendor JSON are not silently accepted or inferred into this envelope.

Examples for all five kinds are in [synthetic import fixtures](../../../backend/tests/fixtures/import/source/metadata).
