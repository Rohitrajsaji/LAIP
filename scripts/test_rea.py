"""Explicit operator acceptance check using an already-built, pinned REA CLI.

Never fetches, installs, builds, sets up engines, or executes the fixture sources.
The operator supplies a trusted checkout and Node executable, outside import input.
"""

import hashlib
import os
import subprocess
import tempfile
import uuid
from pathlib import Path

import psycopg
from laip.artifacts import LocalArtifactStore
from laip.migrations import migrate
from laip.persistence import Repository
from laip.rea_adapter import (
    REA_REVISION,
    InputFile,
    ReaAdapter,
    ReferenceInventoryResult,
    SealedDirectoryRef,
    TrustedFile,
    TrustedReaExecutable,
    validate_inventory,
)
from laip.rea_import import REA_PROVIDER, persist_inventory, prepare_inventory
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    checkout_setting = os.environ.get("LAIP_REA_ROOT")
    node_setting = os.environ.get("LAIP_REA_NODE")
    if not checkout_setting or not node_setting:
        raise SystemExit(
            "Set LAIP_REA_ROOT and LAIP_REA_NODE to an audited, already-built CLI"
        )
    checkout = Path(checkout_setting).resolve(strict=True)
    node = Path(node_setting).resolve(strict=True)
    revision = subprocess.run(
        ["git", "-C", str(checkout), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    changed = subprocess.run(
        [
            "git",
            "-C",
            str(checkout),
            "diff",
            "--no-ext-diff",
            "--no-textconv",
            "--quiet",
            "HEAD",
            "--",
        ],
        check=False,
        capture_output=True,
    ).returncode
    if revision != REA_REVISION or changed != 0:
        raise SystemExit("REA source revision does not match the clean pinned checkout")
    runtime_paths = [node, checkout / "package.json", checkout / "scripts/rea.mjs"]
    for directory in (checkout / "dist", checkout / "node_modules"):
        if not directory.is_dir():
            raise SystemExit("REA compiled runtime or locked dependencies unavailable")
        for path in directory.rglob("*"):
            if path.is_symlink():
                if ".bin" in path.parts:
                    continue  # Not on PATH and never invoked by the allowlisted CLI.
                raise SystemExit("Audited runtime must not contain symlinked modules")
            if path.is_file():
                runtime_paths.append(path)
    pins = tuple(
        TrustedFile(path, hashlib.sha256(path.read_bytes()).hexdigest())
        for path in sorted(set(runtime_paths))
    )
    executable = TrustedReaExecutable(
        (str(node), str(checkout / "scripts/rea.mjs")), pins
    )
    fixture = ROOT / "backend/tests/fixtures/rea/source"
    inputs = tuple(
        InputFile(
            path.relative_to(fixture).as_posix(),
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_size,
        )
        for path in sorted(fixture.rglob("*"))
        if path.is_file()
    )
    with tempfile.TemporaryDirectory(prefix="laip-rea-acceptance-") as temporary:
        staging = Path(temporary)
        staging.chmod(0o700)
        result = ReaAdapter(executable, staging).inventory(
            SealedDirectoryRef(fixture, inputs)
        )
        expected = (ROOT / "backend/tests/fixtures/rea/inventory.json").read_bytes()
        # Semantic output is relocation-independent; whitespace remains provider-owned.
        if result.graph != validate_inventory(expected, inputs):
            raise SystemExit(
                "Pinned CLI output differs from the recorded acceptance graph"
            )
        if list(staging.iterdir()) or list(fixture.rglob("IMPORTED_CODE_EXECUTED")):
            raise SystemExit(
                "Unexpected execution marker or incomplete staging cleanup"
            )
        persist_fixture(result, staging)
        print(
            "Pinned REA fixture passed: source hashes, upstream ID and raw graph retained; "
            "PostgreSQL replay passed; setup/imported code were not executed."
        )


def persist_fixture(result: ReferenceInventoryResult, staging: Path) -> None:
    url = os.environ.get("LAIP_TEST_DATABASE_URL")
    if not url:
        raise SystemExit(
            "Use make rea-check to provide the isolated Docker test database"
        )
    artifact_root = staging / "artifacts"
    artifact_root.mkdir(mode=0o700)
    store = LocalArtifactStore(artifact_root)
    prepared = prepare_inventory(
        result,
        store,
        run_id="run_rea",
        import_id="import_rea",
        collected_at="2026-10-07T12:00:00Z",
        masking_policy_version="synthetic-v1",
        job_id="job_rea",
        fence=1,
    )
    schema = "rea_acceptance_" + uuid.uuid4().hex
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
        try:
            connection.execute(
                sql.SQL("SET search_path TO {}").format(sql.Identifier(schema))
            )
            migrate(connection)
            repository = Repository(connection, "synthetic:rea")
            repository.create_system()
            repository.create_import("import_rea", {})
            repository.create_run(
                "run_rea", "import_rea", {"providers": [REA_PROVIDER]}
            )
            first = persist_inventory(prepared, repository)
            if persist_inventory(prepared, repository) != first:
                raise SystemExit("Replay changed the imported graph/evidence IDs")
            record = connection.execute("SELECT payload FROM evidence").fetchone()[0]
            if record["upstream_record"]["upstream_id"] != result.upstream_id:
                raise SystemExit("Upstream graph identity was not preserved")
            with store.open(first.blob) as stream:
                if stream.read() != result.raw_output:
                    raise SystemExit("Upstream graph bytes changed during persistence")
            if connection.execute("SELECT count(*) FROM evidence").fetchone()[0] != 1:
                raise SystemExit("Replay duplicated evidence")
        finally:
            connection.execute(
                sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(schema))
            )


if __name__ == "__main__":
    main()
