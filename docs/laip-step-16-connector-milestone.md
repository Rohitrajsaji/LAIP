# IBM i connector milestone

**Design version:** 0.1.0. **Date:** 2026-10-08. **Status:** documentation and implementation backlog; no live connector implemented or live IBM i target validated.

This milestone extends the [connector contract](laip-architecture.md#connector-contract) with explicitly authorized IBM i collection. The current product imports supplied offline artifacts. The [step 15 validation](laip-step-15-validation.md) establishes synthetic offline behavior and pinned REA adapter behavior, not IBM i JDBC, release/PTF, authority, encoding or object coverage compatibility. Application modernization and code translation remain outside scope.

## Boundary and transport decision

Use IBM Toolbox for Java JDBC in a separately packaged Java collection worker/adapter, invoked through a private typed job boundary. Python continues to own scope authorization, durable jobs, immutable artifacts, evidence validation and publication; Java owns JDBC connection/statement/result resources. A future Compose profile can run the Java worker on the private network without publishing a port. This is a design decision, not a new service in the current stack.

The alternative of embedding a JVM in the Python process adds lifecycle and dependency coupling. A generic HTTP SQL gateway adds an unnecessary remotely callable query surface. The separate worker adds packaging and interprocess cancellation work, but makes the required Java runtime explicit and keeps JDBC credentials and driver lifecycle outside the web process. Lock the Java runtime, IBM Toolbox artifact/version, dependency hashes and distribution notices before implementation; this document chooses no unverified driver/runtime version.

Browser requests select a configured connector reference and approved scope. They cannot provide a host, credential, arbitrary SQL, CL command or executable script. The existing REST/BFF mutation safeguards still apply. Existing MCP knowledge tools remain read-only and cannot start collection or gain direct JDBC access. Collection publishes through the same validated artifact/evidence pipeline as offline imports.

## Typed collection contract

Specify `describe()`, `probe(CollectionScope, context)` and `collect(CollectionRequest, context)` using architecture 0.1.0. These are proposed live-provider interfaces, not implemented live runtime methods. Extend the provider-owned capability profile, not the canonical schema silently. Any necessary persisted fields require a separately reviewed versioned schema change.

| Contract | Required information and behavior |
| --- | --- |
| Immutable `CollectionScope` | Authorized system namespace and configured connection reference; explicit permitted libraries, object types/names, source files/members and IFS roots; exclusions; approved operation IDs; source disclosure/masking policy; byte/row/member/time ceilings; configuration digest. An empty filter cannot mean all libraries. |
| `ProviderDescriptor` | Provider and driver/runtime versions; operation-specific capability, required release/PTF/authority and effects. Availability states distinguish available, unavailable, unsupported and insufficient authority. |
| `ProbeReport` | Target-reported release, observed PTF/service information, endpoint identity, tested operation/column signatures, authority outcomes, scope, timestamps and probe freshness. Missing or denied probes remain unknown; an empty result is not evidence of absence. |
| `CollectionItem` | Raw result/source artifact or typed metadata, logical qualified locator, collection method/time, source digest and transformation description; excluded or failed items have explicit status and diagnostic. |
| Durable job integration | Lease/fence, bounded checkpoint, cancellation token, attempt history and immutable effective scope. Publish only verified artifacts under the current fence. Restart resumes validated checkpoints or creates a new explicitly linked observation; it does not conceal recollection time. |
| Typed failure | Unavailable service, unsupported signature, insufficient authority, TLS/connection failure, timeout, cancellation, resource limit, source changed, decode failure and invalid provider output are distinct; errors redact secrets and raw source. |

Each successful operation declares `remote_read`; private staging also uses existing `local_write`. Read-only means no application object mutation, command execution or data queue consumption. Reads can still consume server resources and produce ordinary server connection/audit/job effects. No `remote_write` or `source_execution` capability is enabled. No automatic SSH/CL/API fallback follows a failed query.

## Official availability and effects matrix

The following is IBM's documented SQL-service availability, checked for the milestone, not an LAIP-tested compatibility claim. IBM's [Db2 for i SQL services matrix](https://www.ibm.com/support/pages/node/1119123) distinguishes base support from enabling Db2 group PTF levels. A base cell means the named service exists for that release; it does not promise every subsequently added column or parameter. Implementation must pin explicit column lists and probe their signatures.

| Service / operation | IBM i 7.3 | IBM i 7.4 | IBM i 7.5 | IBM i 7.6 | Planned scope, effects and limits |
| --- | --- | --- | --- | --- | --- |
| `OBJECT_STATISTICS` | Base | Base | Base | Base | Explicit libraries and object types; metadata remote read. Authority-filtered or failed results cannot certify a complete system inventory. |
| `PROGRAM_INFO`, `BOUND_MODULE_INFO`, `BOUND_SRVPGM_INFO`, `PROGRAM_EXPORT_IMPORT_INFO` | SF99703 level 16 | SF99704 level 4 | Base | Base | Explicit qualified program/service-program objects; metadata remote read. Binding facts do not establish runtime execution paths. |
| `PROGRAM_INFO` later enhancement | No claim | SF99704 level 25 | SF99950 level 4 | Probe required | Enhanced fields must have their own column/signature gate; baseline availability does not establish enhanced support. |
| `BINDING_DIRECTORY_INFO` | SF99703 level 28 | SF99704 level 20 | Base | Base | Explicit binding directories; remote metadata read; resolve qualified entries conservatively. |
| `IFS_READ`, `IFS_READ_BINARY` | SF99703 level 22 | SF99704 level 10 | Base | Base | Approved bounded IFS paths only; remote source read. Binary versus character conversion is a deliberate collection decision. |
| `GROUP_PTF_INFO`, `SERVICES_INFO`, `ENV_SYS_INFO` | Base | Base | Base | Base | Bounded capability probes; remote metadata read. Service discovery is not proof of per-object authority or successful collection. |
| Db2 catalog queries | Per-view and per-column documentation/probe required | Same | Same | Same | Explicit allowlisted views, columns and predicates; remote metadata read; no uniform catalog-version guarantee. |
| Authorized source-member read/export | Transport, record format and CCSID gate required | Same | Same | Same | Read only approved members or import supplied exports. Preserve source origin and conversion provenance. |
| Existing `DSPPGMREF` output import | Export producer version/format required | Same | Same | Same | Local inert import only. Historical program references are observations, not current resolved execution paths. Running outfile mode writes a database file and is excluded. |

Release/PTF minima do not substitute for service-specific authorities. This companion table records release-specific documented requirements; the target release's documentation and actual scoped probe remain gates.

| Operation / documented release | Authority and effects evidence |
| --- | --- |
| [`OBJECT_STATISTICS`, 7.5](https://www.ibm.com/docs/en/i/7.5.0?topic=services-object-statistics-table-function) | Full file information requires object `*OBJOPR` and library `*EXECUTE`; full ILE program/service-program information additionally requires `*READ`. Insufficient authority can yield partial rows, SQL warning 01548 or no rows; these are not absence proofs. |
| [`PROGRAM_INFO`, 7.3 SQL reference](https://www.ibm.com/docs/en/ssw_ibm_i_73/pdf/rzajqpdf.pdf) | Program/service-program `*READ` and library `*EXECUTE`. Later target releases require their own check. |
| [`BOUND_MODULE_INFO`, 7.6](https://www.ibm.com/docs/en/i/7.6.0?topic=services-bound-module-info-view) | Program/service-program `*USE` and library `*EXECUTE`; do not copy a blanket `*READ` requirement to every binding view. |
| [`BOUND_SRVPGM_INFO`, 7.4 SQL reference](https://www.ibm.com/docs/en/ssw_ibm_i_74/pdf/rzajqpdf.pdf) | Program/service-program `*READ` and library `*EXECUTE`. |
| [`PROGRAM_EXPORT_IMPORT_INFO`, 7.6](https://www.ibm.com/docs/en/i/7.6.0?topic=services-program-export-import-info-view) | Program `*READ` and library `*EXECUTE`. |
| [`BINDING_DIRECTORY_INFO`, 7.5](https://www.ibm.com/docs/en/i/7.5.0?topic=services-binding-directory-info-view) | Library `*USE`, directory `*OBJOPR` and `*READ`; entry timestamps need entry-library `*EXECUTE` and some authority to the entry. Missing timestamp availability remains explicit. |
| [`IFS_READ` family, 7.5](https://www.ibm.com/docs/en/i/7.5.0?topic=is-ifs-read-ifs-read-binary-ifs-read-utf8-table-functions) | Ancestor directories `*X`, stream file `*R`. Save-file access has stronger `*RWX` requirements and is excluded from this source scope. Binary output avoids character conversion; character/UTF-8 results have distinct conversion contracts. |
| [`GROUP_PTF_INFO`, 7.5](https://www.ibm.com/docs/en/i/7.5.0?topic=services-group-ptf-info-view) | `*USE` to `WRKPTFGRP`; denial leaves PTF compatibility unknown. |
| [`SERVICES_INFO`, 7.5](https://www.ibm.com/docs/ssw_ibm_i_75/rzajq/rzajqtableservices_info.htm) | QSYS2 `*EXECUTE`; QSYS2/SERV_INFO `*OBJOPR` and `*READ`. Returned example scripts are inert metadata and are never executed. |
| [`ENV_SYS_INFO`, 7.6](https://www.ibm.com/docs/ssw_ibm_i_76/rzajq/rzajqviewenvinfo.htm) | IBM i documentation lists no additional authorization. Do not substitute same-named Db2 LUW documentation. |
| [`DSPPGMREF`, 7.6](https://www.ibm.com/docs/en/i/7.6.0?topic=ssw_ibm_i_76%2Fcl%2Fdsppgmref.html) | Program `*OBJOPR`, library `*EXECUTE`; non-thread-safe command. Outfile mode writes a database file using QWHDRPPR format from QADSPPGM and is excluded from live collection. Historical entries can persist after references disappear. |

The implementation backlog must freeze official operation signatures, required object/path authorities, relevant restrictions and timeout/cancellation behavior alongside this availability matrix before enabling an operation. If authority documentation or a probe is unavailable, the operation remains blocked/unknown rather than guessed supported.

## Query and authority controls

Initial catalog candidates are `QSYS2.SYSTABLES`, `QSYS2.SYSCOLUMNS`, `QSYS2.SYSINDEXES`, `QSYS2.SYSKEYS` and `QSYS2.SYSPARTITIONSTAT`; the latter supplies partition/member metadata, not original source bytes. Each requires its own explicit columns, granted authorities, approved predicates and real-target signature probe. These are IBM i catalog candidates from [IBM’s catalog list](https://www.ibm.com/docs/en/i/7.6.0?topic=views-i-catalog-tables) and [IBM’s catalog landscape](https://www.ibm.com/support/pages/system/files/inline-files/Catalog_Views2025_with_i76base_i75TR6_14x8Landscape.pdf), not same-named LUW or Informix catalogs. Application data-table content queries are disabled by default.

Define a finite registry of reviewed SQL templates, each with a fixed service/view, explicit result columns and typed bind parameters. Query only approved library/object/path tuples; avoid whole-system enumeration followed by client-side filtering. Test schema/column availability using separately reviewed bounded probes. SQL identifiers requiring interpolation must originate from validated configured identifiers and use driver-safe quoting; ordinary values use bind parameters.

A string beginning with `SELECT` is not an effect policy: a query can invoke a user-defined function with writes or external effects. Only reviewed built-in/catalog operations and approved argument forms enter the registry. Deny procedures, arbitrary routines, dynamic user SQL, commands, outfile generation and queue reads that remove messages. JDBC read-only flags are defense in depth; they cannot replace operation review and independently tested least-privilege authorities.

The operator must supply the target endpoint and permitted network route, TLS trust material/hostname validation requirements, an approved credential reference, a read-only service account, explicit libraries and source paths, and an IBM i administrator able to confirm release/PTF and account/object/path authorities. Do not grant broad administrative authority to make probes pass. The connector requires only the authorities documented and demonstrated for enabled operations; denied objects remain accounted for as partial scope.

Credentials belong in a protected secret provider or private injected secret, never an import manifest, browser payload, exported knowledge or audit message. Verify encrypted transport and certificate/endpoint identity before authentication. IBM's [JDBC properties](https://www.ibm.com/docs/en/i/7.5.0?topic=ssw_ibm_i_75%2Frzahh%2Fjavadoc%2Fcom%2Fibm%2Fas400%2Faccess%2Fdoc-files%2FJDBCProperties.html) distinguish read-only access from read-call access, and secure mode must be explicitly enabled; [JTOpen's property reference](https://jt400.sourceforge.net/doc/com/ibm/as400/access/doc-files/JDBCProperties.html) is the driver reference. The [IBM JDBC/JSSE TLS guidance](https://www.ibm.com/support/pages/node/685737) supports the TLS setup investigation. Disable server tracing and verify extended-dynamic/package settings cannot create target SQL packages; no automatic QTEMP DDL or package creation is accepted. Required host-server endpoints/ports must come from the target owner; no command-server grant is implied.

The driver-property gate requires `extended dynamic=false` because enabling it can create a missing SQL package. Generic JDBC `DatabaseMetaData` discovery can invoke system stored procedures under its default metadata-source setting; use reviewed explicit catalog SQL for initial probes, and audit any broader metadata API separately. Socket timeouts are milliseconds, can leave the connection unusable, and have driver threading constraints. Query timeout modes and actual cancellation must be validated together on the selected driver/target pair; a configured timeout is not a guaranteed remote cancellation deadline. These are implementation requirements derived from the [JTOpen property reference](https://jt400.sourceforge.net/doc/com/ibm/as400/access/doc-files/JDBCProperties.html), not exercised runtime configuration.

Driver setup must explicitly bound login/socket/query durations, fetch size, result rows and aggregate bytes. Verify actual cancellation behavior on the target/driver pair; cancellation requests stop publication even if a remote statement cannot immediately terminate. Close result sets, statements and connections on all exit paths and apply a bounded forced worker shutdown when necessary.

## Source fidelity and consistency

Preserve original raw bytes whenever the selected transport supplies them, with byte digest, CCSID/encoding, source file/member identity, record length and any sequence/date columns retained separately from code columns. A JDBC character result may already have undergone host/driver conversion. Store that returned representation and its conversion metadata honestly; do not label it the original raw member bytes or invent byte-accurate source mappings. A raw-byte source export or validated byte-preserving transfer is required before claiming original-byte fidelity.

IFS character reads and binary reads have different fidelity contracts. Validate multibyte, EBCDIC, newline/record boundary and invalid-encoding fixtures against an authorized real source export. Unsupported conversion becomes explicit partial/decode failure, with bytes retained where available. Member maps include system/library/source-file/member and collection occurrence so same-named objects stay distinct.

A native source-member query may require an alias: [IBM documents alias creation and persistence](https://www.ibm.com/docs/en/i/7.5.0?topic=language-creating-using-alias-names). Creating even a QTEMP alias is DDL and is excluded from the read-only operation registry. Use an existing independently authorized alias or owner-prepared export until a bounded member-reader transport is implemented and validated.

A multi-query collection is not an atomic IBM i system snapshot. Record per-operation times, source/object change indicators and consistency limitations. Where a before/after comparison detects changes, retain observations and mark changed scope; never silently combine them into a complete contemporaneous graph. Application membership and unresolved dynamic/ambiguous references retain the existing certainty/resolution/review separation. New evidence changes invalidate affected approvals through the established fingerprint rules.

## Existing DSPPGMREF imports

Current offline ingestion accepts its declared [versioned metadata envelope](contracts/0.1.0/offline-metadata.md) and [normalized entity/reference profile](contracts/0.1.0/inventory-metadata.md); this does not establish arbitrary native outfile rows, printed reports or vendor JSON support. Implement a bounded explicit converter for agreed **existing** exports, with producer release/command options, export time, row layout/encoding, qualified program and referenced-object fields, raw artifact digest and conversion version retained. Reject an unknown format rather than infer columns. Map exported references to supplied observations with historical/out-of-scope/ambiguous/dynamic limitations.

No converter starts `DSPPGMREF`, creates an outfile or executes imported command text. If an operator separately produces an export, its production effects and authority sit outside this connector's read-only execution surface. Program reference output alone cannot prove current object existence, runtime targets, completeness or source binding; independently collected objects and qualified resolution evidence are required.

## Implementation backlog and release gates

| Order | Deliverable | Acceptance before progressing |
| --- | --- | --- |
| LIVE-01 | Target/access agreement and operation registry | Named authorized target, release/PTF evidence, explicit libraries/source roots, account/authority owner, TLS/network prerequisites and enabled operation/effects list recorded. Missing prerequisites leave collection disabled. |
| LIVE-02 | Driver/runtime and packaging decision | Pinned IBM Toolbox/runtime compatibility evidence, hashes/license notices, private worker boundary, credential injection and redacted logging reviewed. No unpinned runtime downloaded at job execution. |
| LIVE-03 | Probe adapter and schema profiles | Describe/probe tests distinguish denied, unsupported, unavailable, empty and successful outcomes; freshness and actual service/column signatures recorded on the authorized real target. |
| LIVE-04 | Scoped metadata collection | Qualified library/object filters, limits, cancellation and immutable evidence pass offline contract tests and authorized real-target comparisons. Denied/excluded/missing scope is counted. |
| LIVE-05 | Authorized source reads | CCSID/record/raw-byte fidelity comparisons, missing/changed source, escaping scope and resource limits pass with approved real source members/exports. Converted character data is labeled accurately. |
| LIVE-06 | Existing DSPPGMREF converter | Versioned real exported samples match expected qualified references; malformed layouts rejected; raw provenance and historical limitations retained; no command/outfile generation. |
| LIVE-07 | Recovery and failure validation | Disconnect, timeout, cancellation, lease loss, worker/database restart and repeat collection retain immutable history, avoid duplicate publication and cannot publish stale-fence evidence. |
| LIVE-08 | Support claim gate | Publish a validation record for each exact release/PTF, driver/runtime, enabled signature and authority/scope combination tested; list denied/unsupported paths and residual limits. No broader IBM i compatibility label derives from synthetic tests or IBM documentation alone. |

The first real validation must include at least two permitted libraries containing same-named objects, permitted and denied objects/members, missing and changed source, static and dynamic references, binding metadata where enabled, a known CCSID sample, and an existing DSPPGMREF export when its converter is enabled. An authorized administrator compares scoped collection with independently obtained expected records. Preserve sanitized proof of release/PTF, authority and operation outcomes; protect source and credentials. Support stays experimental until these gates pass, and a later release/PTF/driver change requires renewed affected-signature validation.

SSH/SFTP and IBM i system APIs remain later transport investigations. They need their own authentication, scope, authority, byte-fidelity, effect and cancellation profiles. [IBM OpenSSH guidance](https://www.ibm.com/support/pages/ibm-i-openssh-openssl-navigation) identifies product 5733-SC1 and release/PTF dependencies; [IBM's older transfer reference](https://www.redbooks.ibm.com/redbooks/pdfs/sg247680.pdf) does not establish native QSYS.LIB source-member fidelity for a future target. Validate binary transfer and path semantics independently.

The [QUSLOBJ list API](https://www.ibm.com/docs/en/i/7.5.0?topic=q-list-objects-quslobj) populates/replaces a user space and requires mutation authority/locking; exclude it from automatic read-only fallback. The [QCLRPGMI receiver-buffer API](https://www.ibm.com/docs/en/i/7.4.0?topic=ssw_ibm_i_74%2Fapis%2Fqclrpgmi.html) is a distinct deferred candidate with program `*READ`, library `*EXECUTE`, `*SHRRD` locking and non-thread-safe constraints; it needs separate implementation and validation. Do not classify every system API as either uniformly safe or uniformly mutating. Neither API is implemented by this milestone.

## Required live-access worksheet

As of 2026-10-08, no live target, account, credentials, authorized application library, source member, IFS root or export location has been supplied. Fill this worksheet through the target owner's authorization process before LIVE-01. It is a prerequisite record, not a request to send credentials into chat. `NOT SUPPLIED` never means a wildcard, a default library list or permission to discover an entire system.

| Prerequisite | Required recorded value | Current state |
| --- | --- | --- |
| Target and owner | Approved endpoint/system namespace, responsible IBM i administrator, collection authorization reference and allowed network route/host-server endpoints | NOT SUPPLIED |
| RDB/iASP and naming context | Approved relational database/iASP, effective job CCSID, explicit library list and SQL/system naming policy; namespace/qualified identity decision for duplicate library/object names across iASPs | NOT SUPPLIED / UNTESTED |
| Release/PTF | Target-reported IBM i release and relevant installed group PTF levels/status; operation/column profile | NOT SUPPLIED |
| Transport and account | TLS trust/hostname policy, least-privilege account identity, protected credential reference, account/object/path authority review; no secret value in this worksheet | NOT SUPPLIED |
| Java/driver | Pinned JDK and IBM Toolbox/JTOpen artifact version, hashes, license/notices and property profile | NOT SELECTED / UNTESTED |
| Budgets and retention | Approved rows, members, bytes, time, concurrency, exclusions, source disclosure/masking, proof retention and probe expiry policy | NOT SUPPLIED |
| Independent expected data | Owner-approved real source/metadata examples and independently obtained expected scoped records, with access and provenance permission | NOT SUPPLIED |

The driver’s database selection and effective RDB/iASP context must be retained in collection provenance. Do not merge identically named objects across iASPs into an existing namespace. Approve the identity/schema representation before implementation; unsupported iASP scope stays disabled until validated. This document introduces no persisted schema change.

Record each enabled library/path set explicitly; the following registry separates documented service locations from unknown application scope.

| Scope class | Library/path requirement | Present value and enablement |
| --- | --- | --- |
| SQL-service metadata | `QSYS2` for named services and approved catalog views; `SYSIBMADM` only for an individually selected documented view, with its own authority/signature gate | Service names are documented; target availability and grants UNTESTED. No blanket library access is implied. |
| Application objects | Exact approved APP library names, object names/types and exclusions; at least two authorized libraries with same-named validation objects | APP libraries NOT SUPPLIED; collection disabled |
| Source members | Exact library/source-file/member tuples, CCSID/record layout, source/object mapping and authorized transport | NOT SUPPLIED; no assumed `QRPGLESRC`, `QCLSRC` or other source-file name |
| Existing SQL aliases | Exact qualified owner-prepared alias names and underlying member scope, if alias transport is enabled | NOT SUPPLIED; connector creates no aliases |
| Optional bindings | Exact application/binding-directory libraries and program/service-program objects, only when binding operations are enabled | NOT SUPPLIED; optional profile disabled |
| IFS source | Approved absolute IFS roots/files, path authority and byte/conversion profile | NOT SUPPLIED; no root-wide access |
| Existing DSPPGMREF exports | Approved local import path or separately authorized read location; producer release/options, row format, timestamp and encoding | NOT SUPPLIED; no command/outfile production permission |

## Real-validation ledger

Create an immutable ledger row for every tested operation, release/PTF, driver/runtime, signature and authorized scope combination. Keep sanitized proof references and digests; source and credentials remain private. An observed denial is a distinct outcome, not compatible support or object absence. Changes to the driver, PTF, operation signature, authority or scope invalidate affected claims and require a new row. An absent expiry policy cannot imply indefinite probe validity.

| Validation field | Initial milestone record (2026-10-08) |
| --- | --- |
| Target identity and system namespace | UNTESTED — NOT SUPPLIED |
| IBM i release / installed group PTF levels and status | UNTESTED — NOT SUPPLIED |
| JDK / JDBC driver versions, artifact hashes and property profile | UNTESTED — NOT SELECTED |
| Operation/service and exact columns/parameters | UNTESTED — registry design only |
| Account / object-library-path authorities and denial outcomes | UNTESTED — NOT SUPPLIED |
| Authorized libraries, source tuples, IFS roots and scope digest | UNTESTED — NOT SUPPLIED |
| Result, completeness, resource/cancellation/recovery outcome | UNTESTED — no live collection executed |
| Independent expected-record comparison | UNTESTED — no licensed real corpus supplied |
| Sanitized evidence/proof references and hashes | UNTESTED — no live proof exists |
| Probe time / expiry and revalidation trigger | UNTESTED — target policy NOT SUPPLIED |
| Reviewer and approved support statement | UNTESTED — no live support claim authorized |

## Documentation validation status

Official IBM and JTOpen documentation was checked on 2026-10-08. Context7 tools were unavailable, so the documentation lookup used official web documentation, IBM's published SQL reference PDFs and primary-source indexed excerpts when individual IBM Docs pages returned access errors. This fallback establishes documented requirements; it supplies no live compatibility evidence.

The milestone records official-source availability/effects/authority evidence, typed design boundaries, explicit access prerequisites and implementation gates. Local documentation links are checked against repository files. No JDBC connection, live probe, SQL service, source read, driver property, TLS handshake or real DSPPGMREF conversion was executed for this documentation step. Offline validation remains the separate [step 15 record](laip-step-15-validation.md); it cannot satisfy this ledger.

## References and traceability

- [Execution plan step 16](plans/laip-execution.md#step-16), [requirements backlog](laip-backlog.md): LIVE scope and original connector requirements.
- [Architecture and provider interfaces](laip-architecture.md), [contracts 0.1.0](contracts/0.1.0/README.md): canonical namespace, provenance, capability and review constraints.
- [IBM Db2 for i SQL services availability matrix](https://www.ibm.com/support/pages/node/1119123): documented release/PTF baseline; not an LAIP compatibility certification.
- [IBM DSPPGMREF command documentation](https://www.ibm.com/docs/en/i/7.5.0?topic=d-display-program-references): existing-export investigation and outfile effect boundary; exact export layouts still require their own validation.

Operation-specific authorities and transport references above are documentation evidence. LIVE implementation acceptance additionally requires target-specific probes and real validation; documentation access and synthetic results cannot satisfy those gates.
