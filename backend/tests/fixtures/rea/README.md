# Synthetic REA source-inventory fixture

`source/` contains five synthetic files, including a package preparation hook and a module that writes `IMPORTED_CODE_EXECUTED` if run. These are test data and must never be installed or executed. The pinned CLI only reads/hashes/parses them as historical source.

`inventory.json` contains the exact output from REA commit bc2cd8b874e115eee446860043758a80bd583ad0, CLI4.1.0, operation `import-reference-source <root> --format json`. Its upstream root commitment is c03e57b7a1f3e7935fd742cbde9e252bef989caddb29b8866e20d6dbcde15ca3. It preserves null upstream importer version and the real partial/pathname-race limitation. No upstream Evidence record is returned. The RPGLE file is inventory data; no RPG analysis is claimed.

Use `make rea-check` with an explicitly audited/built external checkout and Node to reproduce the real fixture. No automatic setup occurs. See docs/laip-step-5-validation.md.
