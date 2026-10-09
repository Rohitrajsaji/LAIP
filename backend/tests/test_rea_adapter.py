import hashlib
import json
import sys
import threading
from pathlib import Path

import pytest

from laip.canonical import digest
from laip.rea_adapter import (
    InputFile,
    ReaAdapter,
    ReaAdapterError,
    SealedDirectoryRef,
    TrustedFile,
    TrustedReaExecutable,
    validate_graph,
)


def graph(content: bytes = b"inert source") -> dict:
    entries = [
        {
            "path": "main.py",
            "kind": "file",
            "language": "Python",
            "classifications": ["source"],
            "limitations": [],
            "size": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
            "content_state": "hashed",
        }
    ]
    return {
        "schema": "HistoricalSourceGraph",
        "authority": "historical-reference",
        "root_alias": "$REFERENCE_ROOT",
        "inventory_state": "complete",
        "entries": entries,
        "root_sha256": digest(
            {
                "entries": [
                    {
                        key: entries[0][key]
                        for key in ("kind", "path", "sha256", "size", "content_state")
                    }
                ],
                "exclusions": [],
            }
        ),
        "relationships": [],
        "parse_failures": [],
        "exclusions": [],
        "languages": ["Python"],
        "manifests": [],
        "vcs": {"kind": "none", "head": None, "dirty": None},
        "provenance": {"importer": "rea-agents", "importer_version": None, "caller": "rea-cli"},
        "limitations": [],
    }


def setup(tmp_path: Path, behavior: str = "good") -> tuple[ReaAdapter, SealedDirectoryRef]:
    source = tmp_path / "source"
    source.mkdir()
    content = b"inert source"
    (source / "main.py").write_bytes(content)
    stage = tmp_path / "stage"
    stage.mkdir(mode=0o700)
    script = tmp_path / "cli.py"
    script.write_text(
        "import sys, time, json, os\n"
        "if sys.argv[1] == '--version': print('4.1.0')\n"
        "else:\n"
        " assert sys.argv[1] == 'import-reference-source'\n"
        " assert sys.argv[-2:] == ['--format', 'json']\n"
        + (
            {
                "good": f" print({json.dumps(json.dumps(graph()))})\n",
                "malformed": " print('{}')\n",
                "sleep": " time.sleep(30)\n",
                "flood": " print('x' * 100000)\n",
                "failure": " print('PRIVATE PATH', file=sys.stderr); sys.exit(1)\n",
                "env": " assert 'NODE_OPTIONS' not in os.environ\n"
                + f" print({json.dumps(json.dumps(graph()))})\n",
            }[behavior]
        )
    )
    binary = Path(sys.executable).resolve()
    trust = TrustedReaExecutable(
        (str(binary), str(script)),
        tuple(
            TrustedFile(path, hashlib.sha256(path.read_bytes()).hexdigest())
            for path in (binary, script)
        ),
    )
    return ReaAdapter(trust, stage, timeout_seconds=0.3, max_output_bytes=4096), SealedDirectoryRef(
        source, (InputFile("main.py", hashlib.sha256(content).hexdigest(), len(content)),)
    )


def test_valid_inventory_preserves_exact_raw_bytes_and_upstream_identity(tmp_path: Path) -> None:
    adapter, source = setup(tmp_path)
    result = adapter.inventory(source)
    assert result.graph == graph()
    assert result.raw_output == (json.dumps(graph()) + "\n").encode()
    assert result.output_sha256 == hashlib.sha256(result.raw_output).hexdigest()
    assert result.upstream_id == graph()["root_sha256"]
    assert result.input_files == source.files
    assert not list(adapter.staging_root.iterdir())
    assert (
        adapter.describe()["provider"]["source_revision"]
        == "bc2cd8b874e115eee446860043758a80bd583ad0"
    )


@pytest.mark.parametrize(
    "behavior,code",
    [
        ("malformed", "INVALID_PROVIDER_OUTPUT"),
        ("sleep", "EXECUTION_TIMEOUT"),
        ("flood", "OUTPUT_LIMIT"),
        ("failure", "EXECUTION_FAILED"),
    ],
)
def test_failures_are_bounded_redacted_and_cleaned(
    tmp_path: Path, behavior: str, code: str
) -> None:
    adapter, source = setup(tmp_path, behavior)
    with pytest.raises(ReaAdapterError) as caught:
        adapter.inventory(source)
    assert caught.value.code == code
    assert "PRIVATE PATH" not in str(caught.value)
    assert not list(adapter.staging_root.iterdir())


def test_cancellation_and_input_hash_mismatch_fail_closed(tmp_path: Path) -> None:
    adapter, source = setup(tmp_path)
    event = threading.Event()
    event.set()
    with pytest.raises(ReaAdapterError, match="CANCELLED"):
        adapter.inventory(source, event)
    (source.path / "main.py").write_bytes(b"changed")
    with pytest.raises(ReaAdapterError, match="INPUT_CHANGED"):
        adapter.inventory(source)


def test_symlink_and_unmanifested_files_are_rejected(tmp_path: Path) -> None:
    adapter, source = setup(tmp_path)
    (source.path / "link").symlink_to("/etc/passwd")
    with pytest.raises(ReaAdapterError, match="INVALID_INPUT"):
        adapter.inventory(source)
    (source.path / "link").unlink()
    (source.path / "extra").write_bytes(b"x")
    with pytest.raises(ReaAdapterError, match="INPUT_CHANGED"):
        adapter.inventory(source)


def test_trusted_runtime_changed_and_injected_environment_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapter, source = setup(tmp_path, "env")
    monkeypatch.setenv("NODE_OPTIONS", "--require evil.js")
    adapter.inventory(source)
    Path(adapter.executable.command[1]).write_bytes(b"changed")
    with pytest.raises(ReaAdapterError, match="PROVIDER_INCOMPATIBLE"):
        adapter.inventory(source)


@pytest.mark.parametrize(
    "mutation",
    [
        "root",
        "unknown",
        "path",
        "hash",
        "language",
        "duplicate",
        "relationship",
        "complete",
        "type",
    ],
)
def test_malformed_graph_contract_and_commitment_rejected(mutation: str) -> None:
    value = graph()
    if mutation == "root":
        value["root_sha256"] = "a" * 64
    elif mutation == "unknown":
        value["evidence"] = [{"id": "fake"}]
    elif mutation == "path":
        value["entries"][0]["path"] = "../escape"
    elif mutation == "hash":
        value["entries"][0]["sha256"] = "wrong"
    elif mutation == "language":
        value["languages"] = []
    elif mutation == "duplicate":
        value["entries"] *= 2
    elif mutation == "relationship":
        value["relationships"] = [
            {
                "from_path": "absent",
                "to": "x",
                "kind": "imports",
                "resolution": "internal",
                "parse_state": "parsed",
            }
        ]
    elif mutation == "complete":
        value["limitations"] = ["partial"]
    else:
        value["entries"][0]["size"] = True
    with pytest.raises(ReaAdapterError, match="INVALID_PROVIDER_OUTPUT"):
        validate_graph(json.dumps(value).encode())


def test_duplicate_json_keys_and_invalid_utf8_rejected() -> None:
    for value in (b'{"schema":1,"schema":2}', b"\xff", b"[]"):
        with pytest.raises(ReaAdapterError, match="INVALID_PROVIDER_OUTPUT"):
            validate_graph(value)


def test_inflight_cancellation_terminates_and_cleans_process(tmp_path: Path) -> None:
    adapter, source = setup(tmp_path, "sleep")
    cancellation = threading.Event()
    timer = threading.Timer(0.1, cancellation.set)
    timer.start()
    try:
        with pytest.raises(ReaAdapterError, match="CANCELLED"):
            adapter.inventory(source, cancellation)
    finally:
        timer.cancel()
    assert not list(adapter.staging_root.iterdir())


def test_unavailable_provider_and_untrusted_command_fail_closed(tmp_path: Path) -> None:
    adapter, source = setup(tmp_path)
    script = Path(adapter.executable.command[1])
    script.unlink()
    with pytest.raises(ReaAdapterError, match="PROVIDER_UNAVAILABLE"):
        adapter.inventory(source)
    adapter.executable = TrustedReaExecutable(("setup",), adapter.executable.files)
    with pytest.raises(ReaAdapterError, match="PROVIDER_INCOMPATIBLE"):
        adapter.inventory(source)


def test_wrong_version_is_rejected_before_inventory(tmp_path: Path) -> None:
    adapter, source = setup(tmp_path)
    script = Path(adapter.executable.command[1])
    script.write_text("print('4.2.0')\n")
    binary = Path(adapter.executable.command[0])
    adapter.executable = TrustedReaExecutable(
        adapter.executable.command,
        tuple(
            TrustedFile(path, hashlib.sha256(path.read_bytes()).hexdigest())
            for path in (binary, script)
        ),
    )
    with pytest.raises(ReaAdapterError, match="PROVIDER_INCOMPATIBLE"):
        adapter.inventory(source)


def test_private_staging_and_input_limits(tmp_path: Path) -> None:
    adapter, source = setup(tmp_path)
    adapter.staging_root.chmod(0o755)
    with pytest.raises(ReaAdapterError, match="INVALID_CONFIGURATION"):
        adapter.inventory(source)
    adapter.staging_root.chmod(0o700)
    source = SealedDirectoryRef(source.path, (*source.files, *source.files))
    with pytest.raises(ReaAdapterError, match="INVALID_INPUT"):
        adapter.inventory(source)
    with pytest.raises(ReaAdapterError, match="INVALID_CONFIGURATION"):
        ReaAdapter(adapter.executable, adapter.staging_root, timeout_seconds=0)


def test_real_captured_graph_validated_against_fixture_hashes() -> None:
    from laip.rea_adapter import validate_inventory

    root = Path(__file__).parent / "fixtures" / "rea"
    raw_path = root / "inventory.json"
    inputs = tuple(
        InputFile(
            path.relative_to(root / "source").as_posix(),
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_size,
        )
        for path in sorted((root / "source").rglob("*"))
        if path.is_file()
    )
    value = validate_inventory(raw_path.read_bytes(), inputs)
    assert value["inventory_state"] == "partial"
    assert (
        value["root_sha256"] == "c03e57b7a1f3e7935fd742cbde9e252bef989caddb29b8866e20d6dbcde15ca3"
    )
    assert not list((root / "source").rglob("IMPORTED_CODE_EXECUTED"))


def test_provider_descriptor_matches_versioned_contract(tmp_path: Path) -> None:
    from laip.persistence import validate

    adapter, _ = setup(tmp_path)
    descriptor = validate("ProviderDescriptor", adapter.describe())
    capability = descriptor["capabilities"][0]
    assert capability["operation"] == "import-reference-source"
    assert capability["effects"] == ["local_read", "local_write"]


def test_process_group_cleanup_includes_child_after_leader_exits(tmp_path: Path) -> None:
    import time

    adapter, source = setup(tmp_path)
    script = Path(adapter.executable.command[1])
    marker = tmp_path / "child-survived"
    script.write_text(
        "import os, sys, time\n"
        "if sys.argv[1] == '--version': print('4.1.0')\n"
        "else:\n"
        " child = os.fork()\n"
        " if child == 0:\n"
        "  os.close(1); os.close(2)\n"
        "  time.sleep(0.4)\n"
        f"  open({str(marker)!r}, 'w').write('survived')\n"
        "  os._exit(0)\n"
        f" print({json.dumps(json.dumps(graph()))})\n"
    )
    binary = Path(adapter.executable.command[0])
    adapter.executable = TrustedReaExecutable(
        adapter.executable.command,
        tuple(
            TrustedFile(path, hashlib.sha256(path.read_bytes()).hexdigest())
            for path in (binary, script)
        ),
    )
    adapter.inventory(source)
    time.sleep(0.5)
    assert not marker.exists()


def test_partial_exclusions_are_preserved_without_fabricating_evidence() -> None:
    from laip.rea_adapter import validate_inventory

    value = graph()
    value["inventory_state"] = "partial"
    value["entries"] = []
    value["languages"] = []
    value["exclusions"] = [{"path": "main.py", "reason": "configured-secret"}]
    value["root_sha256"] = digest({"entries": [], "exclusions": value["exclusions"]})
    item = InputFile("main.py", hashlib.sha256(b"inert source").hexdigest(), 12)
    accepted = validate_inventory(json.dumps(value).encode(), (item,))
    assert accepted["exclusions"] == value["exclusions"]
    assert "evidence" not in accepted
    assert "evidence_id" not in accepted


def test_runtime_hash_cancellation_is_checked_between_bounded_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import time
    from contextlib import contextmanager

    adapter, _ = setup(tmp_path)
    cancellation = threading.Event()
    real_fdopen = os.fdopen
    reads: list[int] = []

    class CancelAfterChunk:
        def __init__(self, stream):
            self.stream = stream

        def fileno(self):
            return self.stream.fileno()

        def read(self, size):
            reads.append(size)
            result = self.stream.read(size)
            cancellation.set()
            return result

    @contextmanager
    def cancelled_fdopen(fd, mode):
        with real_fdopen(fd, mode) as stream:
            yield CancelAfterChunk(stream)

    monkeypatch.setattr(os, "fdopen", cancelled_fdopen)
    with pytest.raises(ReaAdapterError, match="CANCELLED"):
        adapter._trust(cancellation, time.monotonic() + 2)
    assert reads == [65536]


def test_expired_runtime_hash_budget_fails_before_opening_files(tmp_path: Path) -> None:
    import time

    adapter, _ = setup(tmp_path)
    with pytest.raises(ReaAdapterError, match="EXECUTION_TIMEOUT"):
        adapter._trust(None, time.monotonic() - 1)


def test_directory_enumeration_stops_at_bound_before_collecting_all_entries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import time
    from contextlib import contextmanager
    from types import SimpleNamespace

    adapter, source = setup(tmp_path)
    adapter.max_files = 1
    target = tmp_path / "bounded-copy"
    target.mkdir()
    consumed: list[int] = []

    @contextmanager
    def enormous_directory(_directory):
        def entries():
            for index in range(100000):
                consumed.append(index)
                yield SimpleNamespace(name=f"entry-{index}")

        yield entries()

    monkeypatch.setattr(os, "scandir", enormous_directory)
    with pytest.raises(ReaAdapterError, match="INPUT_LIMIT"):
        adapter._copy(source, target, None, time.monotonic() + 2)
    assert len(consumed) == 5
    assert not list(target.iterdir())


def test_directory_enumeration_observes_cancellation_while_scanning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os
    import time
    from contextlib import contextmanager
    from types import SimpleNamespace

    adapter, source = setup(tmp_path)
    target = tmp_path / "cancel-copy"
    target.mkdir()
    cancellation = threading.Event()
    consumed: list[int] = []

    @contextmanager
    def cancelled_directory(_directory):
        def entries():
            for index in range(100000):
                consumed.append(index)
                cancellation.set()
                yield SimpleNamespace(name=f"entry-{index}")

        yield entries()

    monkeypatch.setattr(os, "scandir", cancelled_directory)
    with pytest.raises(ReaAdapterError, match="CANCELLED"):
        adapter._copy(source, target, cancellation, time.monotonic() + 2)
    assert len(consumed) == 1
