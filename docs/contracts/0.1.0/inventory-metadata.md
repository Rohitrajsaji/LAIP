# Offline inventory interpretation profile — 0.1.0

Step 7 interprets an explicit normalized record profile inside the [step 6 metadata envelopes](offline-metadata.md). The outer envelope still has exactly `schema_version: "0.1.0"`, `kind` and `records`. The profile applies equally to inventory, schema, binding, job and DSPPGMREF-style supplied envelopes. Records without `record_type` remain opaque supplied data and retain accounting evidence. An unknown or malformed explicit record type rejects graph construction; it is never guessed from field names, filenames or source contents.

## Entity record

```json
{
  "record_type": "entity",
  "identity": {
    "system_namespace": "synthetic:import",
    "kind": "Program",
    "qualified_identity": ["LIB1", "*PGM", "ENTRY"]
  },
  "source_availability": "unknown",
  "attributes": {"supplied_description": "Synthetic entry point"}
}
```

Only `record_type`, `identity`, optional `source_availability` and optional `attributes` are accepted. Availability is `available`, `missing`, `unknown` or `not_applicable`; attributes are a canonical JSON object. The namespace must match the accepted manifest. Identities retain exact case and NFC spelling; no case conversion, truncation or alias repair occurs. SourceMember identity has exactly `[library, source_file, member]`. Program, Module, ServiceProgram and Table have exactly `[library, type_tag, object_name]`, with tags `*PGM`, `*MODULE`, `*SRVPGM` and `*FILE` respectively. Other canonical entity kinds retain their supplied bounded qualified segments; nested declarations are not flattened. Conflicting concrete availability observations yield `unknown`; conflicting attribute objects fail rather than overwrite.

## Reference record

```json
{
  "record_type": "reference",
  "from_identity": {
    "system_namespace": "synthetic:import",
    "kind": "Program",
    "qualified_identity": ["LIB1", "*PGM", "ENTRY"]
  },
  "relationship": "CALLS",
  "target": {
    "kind": "Program",
    "name": "TARGET",
    "library": null,
    "dynamic": false
  },
  "library_list": ["LIB1", "LIB2"]
}
```

Required record fields are `record_type`, `from_identity`, `relationship` and `target`. Relationships use the existing canonical Dependency enum. Optional fields are `library_list` and `ordered_library_list`; lists contain bounded distinct strings. The target requires `kind`, `name`, nullable `library` and boolean `dynamic`. Its optional `qualified_identity` supplies the complete exact tuple; the last segment must match `name`, and any supplied library must match the first segment. Unexpected fields fail.

Short-name library lookup is supported for the four tagged object kinds above. Other kinds require an exact target tuple for resolution; Application/System logical names resolve independently of library scope. A Screen identity may include a display-file identity plus record-format segment, and a Job may use `*JOBD`; those identities are not silently reduced to compiled object lookups.

A qualified target resolves only against supplied entities of the same namespace and kind. Without an explicit library, `library_list` narrows scope; it does not declare precedence. One matching entity resolves, multiple matches remain `ambiguous` with all candidates, and no matches remain `missing`. The explicitly named `ordered_library_list` declares precedence: the earliest matching library is used, while multiple candidates in that same library remain ambiguous. An empty ordered list permits no short library lookup. `dynamic: true` remains dynamic even if the expression resembles an existing name. Unresolved edges retain evidence and never manufacture a target entity.

## Associations, membership and provenance

Manifest source/object pairs generate observed `SOURCE_OF` dependencies; matching names alone never establishes a source/object association. Manifest application membership and explicit supplied `MEMBER_OF` references generate observed membership. These confirmed supplied declarations remain separate from inferred static reachability membership. A shared object may belong to several applications. Resolved static edges propagate membership with fresh inferred evidence containing the confirmed seed and path evidence. Dynamic, missing and ambiguous edges do not propagate membership; cycles terminate. Inference never verifies human review or runtime execution.

Every PreparedImport input receives accounting evidence, including excluded, unsupported and missing material. Metadata evidence points to the raw artifact and record index; manifest-only/missing-input evidence points to the preserved manifest. No artificial line/column spans are invented. Evidence and dependency IDs are analysis-run scoped; entity IDs remain namespace-qualified and stable across analyses. All support evidence belongs to the new analysis run. Existing imported SourceMember entities retain their immutable null parent.

Defaults bound entities and dependencies to 100,000 each, evidence to 200,000, metadata records to 100,000, traversal work to 1,000,000 checkpoints, supporting reachability paths to 128 evidence IDs and the analysis deadline to 120 seconds. Cancellation and work/deadline checks cover construction, lookup, membership indexing/traversal, validation and completion. The module reads no filesystem and executes no supplied code. Its configuration digest includes the provider/profile, accepted input configuration, manifest, supplied metadata and limits.
