"""Pinned, inert historical-source CLI boundary. No setup or source execution."""

import hashlib
import os
import re
import selectors
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from laip.canonical import digest, loads

REA_REVISION = "bc2cd8b874e115eee446860043758a80bd583ad0"
REA_VERSION = "4.1.0"
PROVIDER_REF = {"id": "rea-cli", "version": REA_VERSION, "source_revision": REA_REVISION}


class ReaAdapterError(ValueError):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class TrustedFile:
    path: Path
    sha256: str


@dataclass(frozen=True)
class TrustedReaExecutable:
    command: tuple[str, ...]
    files: tuple[TrustedFile, ...]
    version: str = REA_VERSION
    source_revision: str = REA_REVISION


@dataclass(frozen=True)
class InputFile:
    path: str
    sha256: str
    size: int


@dataclass(frozen=True)
class SealedDirectoryRef:
    path: Path
    files: tuple[InputFile, ...]


@dataclass(frozen=True)
class ReferenceInventoryResult:
    graph: dict[str, Any]
    raw_output: bytes
    output_sha256: str
    upstream_id: str
    input_files: tuple[InputFile, ...]


def _check(condition: bool, code: str = "INVALID_PROVIDER_OUTPUT") -> None:
    if not condition:
        raise ReaAdapterError(code)


def _path(value: Any) -> bool:
    return (
        isinstance(value, str)
        and bool(value)
        and not value.startswith("/")
        and "\\" not in value
        and not re.match(r"^[A-Za-z]:", value)
        and all(part not in ("", ".", "..") for part in value.split("/"))
        and not any(ord(char) < 32 or ord(char) == 127 for char in value)
    )


def _hash(value: Any) -> bool:
    return isinstance(value, str) and re.fullmatch("[a-f0-9]{64}", value) is not None


def _text(value: Any) -> bool:
    return isinstance(value, str) and bool(value)


def _texts(value: Any, *, unique: bool = False) -> bool:
    return (
        isinstance(value, list)
        and all(_text(item) for item in value)
        and (not unique or value == sorted(set(value)))
    )


def _shape(value: Any, keys: str) -> None:
    _check(isinstance(value, dict) and set(value) == set(keys.split()))


def validate_graph(raw: bytes) -> dict[str, Any]:
    """Validate exact pinned HistoricalSourceGraph shape and semantic commitments."""
    try:
        graph = loads(raw.decode("utf-8"))
        _shape(
            graph,
            "schema authority root_alias root_sha256 inventory_state entries relationships "
            "parse_failures exclusions languages manifests vcs provenance limitations",
        )
        _check(
            graph["schema"] == "HistoricalSourceGraph"
            and graph["authority"] == "historical-reference"
            and graph["root_alias"] == "$REFERENCE_ROOT"
            and graph["inventory_state"] in ("complete", "partial", "unknown")
            and _hash(graph["root_sha256"])
            and _texts(graph["limitations"])
        )
        _shape(graph["provenance"], "importer importer_version caller")
        p = graph["provenance"]
        _check(
            _text(p["importer"])
            and (p["importer_version"] is None or _text(p["importer_version"]))
            and isinstance(p["caller"], str)
            and re.fullmatch(r"[\w .:@/+-]+", p["caller"]) is not None
        )
        if graph["vcs"] is not None:
            _shape(graph["vcs"], "kind head dirty")
            vcs = graph["vcs"]
            _check(
                (
                    vcs["kind"] in ("none", "unknown")
                    and vcs["head"] is None
                    and vcs["dirty"] is None
                )
                or (
                    vcs["kind"] == "git"
                    and isinstance(vcs["head"], str)
                    and re.fullmatch(r"(?:[a-f0-9]{40}|[a-f0-9]{64})", vcs["head"]) is not None
                    and (vcs["dirty"] is None or type(vcs["dirty"]) is bool)
                )
            )
        for field in ("entries", "relationships", "parse_failures", "exclusions"):
            _check(isinstance(graph[field], list))
        entries = graph["entries"]
        paths: list[str] = []
        files: dict[str, Any] = {}
        commitment: list[dict[str, Any]] = []
        partial = bool(graph["limitations"] or graph["parse_failures"] or graph["exclusions"])
        classes = {
            "source",
            "test",
            "config",
            "manifest",
            "generated",
            "vendor",
            "documentation",
            "unknown",
        }
        for entry in entries:
            _check(isinstance(entry, dict))
            kind = entry.get("kind")
            _check(kind in ("file", "directory"))  # Sealed LAIP inputs cannot contain symlinks.
            keys = "sha256 size language content_state" if kind == "file" else "tree_state"
            _shape(entry, "path kind classifications limitations " + keys)
            _check(
                _path(entry["path"])
                and _texts(entry["limitations"])
                and _texts(entry["classifications"], unique=True)
                and bool(entry["classifications"])
                and set(entry["classifications"]) <= classes
            )
            paths.append(entry["path"])
            partial |= bool(entry["limitations"])
            if kind == "file":
                _check(entry["language"] is None or _text(entry["language"]))
                size = entry["size"]
                _check(size is None or (type(size) is int and 0 <= size <= 9007199254740991))
                state = entry["content_state"]
                if state == "hashed":
                    _check(_hash(entry["sha256"]) and size is not None)
                else:
                    _check(
                        state in ("redacted-secret", "excluded", "unreadable", "unknown")
                        and entry["sha256"] is None
                    )
                    partial = True
                files[entry["path"]] = entry
                fields: tuple[str, ...] = ("kind", "path", "sha256", "size", "content_state")
            else:
                _check(
                    entry["tree_state"]
                    in ("enumerated", "partial", "excluded", "unreadable", "unknown")
                )
                partial |= entry["tree_state"] != "enumerated"
                fields = ("kind", "path", "tree_state")
            commitment.append({key: entry[key] for key in fields})
        _check(paths == sorted(set(paths)))
        for field, expected in (
            (
                "languages",
                sorted({e["language"] for e in files.values() if e["language"] is not None}),
            ),
            (
                "manifests",
                sorted(e["path"] for e in files.values() if "manifest" in e["classifications"]),
            ),
        ):
            _check(graph[field] == expected)
        relationships: list[str] = []
        for edge in graph["relationships"]:
            _shape(edge, "from_path to kind resolution parse_state")
            _check(
                edge["from_path"] in files
                and _text(edge["to"])
                and edge["kind"] in ("imports", "requires", "references", "declares-module")
                and edge["resolution"] in ("internal", "external", "unresolved", "unknown")
                and edge["parse_state"] in ("parsed", "partial", "unknown")
            )
            if edge["resolution"] == "internal":
                _check(_path(edge["to"]) and edge["to"] in paths)
            partial |= edge["parse_state"] != "parsed" or edge["resolution"] in (
                "unresolved",
                "unknown",
            )
            relationships.append(
                "\0".join(
                    edge[key] for key in ("from_path", "to", "kind", "resolution", "parse_state")
                )
            )
        _check(relationships == sorted(set(relationships)))
        for field, record_keys in (
            ("parse_failures", ("path", "parser", "reason")),
            ("exclusions", ("path", "reason")),
        ):
            order: list[str] = []
            for record in graph[field]:
                _shape(record, " ".join(record_keys))
                _check(_path(record["path"]) and all(_text(record[key]) for key in record_keys))
                if field == "exclusions":
                    _check(
                        record["reason"]
                        in (
                            "configured-secret",
                            "symlink-escape",
                            "size-limit",
                            "inventory-limit",
                            "unreadable",
                            "caller-excluded",
                        )
                    )
                order.append("\0".join(record[key] for key in record_keys))
            _check(order == sorted(set(order)))
        _check(graph["inventory_state"] != "complete" or not partial)
        _check(
            graph["root_sha256"]
            == digest({"entries": commitment, "exclusions": graph["exclusions"]})
        )
        return dict(graph)
    except (ValueError, TypeError, KeyError, RecursionError):
        raise ReaAdapterError("INVALID_PROVIDER_OUTPUT") from None


def validate_inventory(raw: bytes, inputs: tuple[InputFile, ...]) -> dict[str, Any]:
    graph = validate_graph(raw)
    expected = {item.path: item for item in inputs}
    _check(len(expected) == len(inputs))
    for entry in graph["entries"]:
        if entry["kind"] != "file":
            continue
        _check(entry["path"] in expected)
        if entry["content_state"] == "hashed":
            item = expected[entry["path"]]
            _check(entry["sha256"] == item.sha256 and entry["size"] == item.size)
    # REA's documented ignore/secret exclusions remain explicit partial observations.
    observed = {entry["path"] for entry in graph["entries"] if entry["kind"] == "file"}
    exclusions = {entry["path"] for entry in graph["exclusions"]}
    if graph["inventory_state"] == "complete":
        _check(observed == set(expected))
    else:
        _check(
            all(
                path in observed or any(path == e or path.startswith(e + "/") for e in exclusions)
                for path in expected
            )
        )
    return graph


class ReaAdapter:
    def __init__(
        self,
        executable: TrustedReaExecutable,
        staging_root: Path,
        *,
        timeout_seconds: float = 120,
        max_output_bytes: int = 16 * 1024 * 1024,
        max_files: int = 10000,
        max_input_bytes: int = 256 * 1024 * 1024,
    ) -> None:
        _check(
            0 < timeout_seconds <= 120
            and 0 < max_output_bytes <= 64 * 1024 * 1024
            and 0 < max_files <= 10000
            and 0 < max_input_bytes <= 256 * 1024 * 1024,
            "INVALID_CONFIGURATION",
        )
        self.executable = executable
        self.staging_root = staging_root
        self.timeout_seconds = timeout_seconds
        self.max_output_bytes = max_output_bytes
        self.max_files = max_files
        self.max_input_bytes = max_input_bytes

    def describe(self) -> dict[str, Any]:
        return {
            "schema_version": "0.1.0",
            "provider": dict(PROVIDER_REF),
            "kind": "rea_adapter",
            "capabilities": [
                {
                    "operation": "import-reference-source",
                    "availability": "partial",
                    "platforms": ["linux", "darwin"],
                    "languages": ["JavaScript", "TypeScript", "JSX", "TSX"],
                    "dialects": [],
                    "effects": ["local_read", "local_write"],
                    "limitations": [
                        "Inventory and import hints only; no IBM i analysis.",
                        "Private staging reduces pathname races; OS limits remain.",
                        "Operator must pin the complete trusted runtime dependency bundle.",
                    ],
                }
            ],
        }

    def _trust(self, cancel: threading.Event | None, deadline: float) -> None:
        trust = self.executable
        _check(
            trust.version == REA_VERSION
            and trust.source_revision == REA_REVISION
            and bool(trust.command)
            and bool(trust.files),
            "PROVIDER_INCOMPATIBLE",
        )
        pins = {str(item.path): item.sha256 for item in trust.files}
        _check(len(pins) == len(trust.files), "PROVIDER_INCOMPATIBLE")
        _check(
            all(Path(arg).is_absolute() and arg in pins for arg in trust.command),
            "PROVIDER_INCOMPATIBLE",
        )
        try:
            for item in trust.files:
                _check(cancel is None or not cancel.is_set(), "CANCELLED")
                _check(time.monotonic() < deadline, "EXECUTION_TIMEOUT")
                _check(_hash(item.sha256), "PROVIDER_INCOMPATIBLE")
                fd = os.open(item.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, "rb") as stream:
                    _check(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "PROVIDER_INCOMPATIBLE")
                    hasher = hashlib.sha256()
                    while True:
                        _check(cancel is None or not cancel.is_set(), "CANCELLED")
                        _check(time.monotonic() < deadline, "EXECUTION_TIMEOUT")
                        chunk = stream.read(65536)
                        if not chunk:
                            break
                        hasher.update(chunk)
                    actual = hasher.hexdigest()
                _check(actual == item.sha256, "PROVIDER_INCOMPATIBLE")
        except OSError:
            raise ReaAdapterError("PROVIDER_UNAVAILABLE") from None

    def _run(
        self, args: tuple[str, ...], cwd: Path, cancel: threading.Event | None, deadline: float
    ) -> bytes:
        env = {
            "HOME": str(cwd),
            "TMPDIR": str(cwd),
            "LANG": "C.UTF-8",
            "NO_COLOR": "1",
            "REA_LOG_LEVEL": "silent",
        }
        try:
            process = subprocess.Popen(
                (*self.executable.command, *args),
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except OSError:
            raise ReaAdapterError("PROVIDER_UNAVAILABLE") from None
        output = bytearray()
        total = 0
        selector = selectors.DefaultSelector()
        try:
            assert process.stdout is not None and process.stderr is not None
            for stream in (process.stdout, process.stderr):
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ)
            while selector.get_map():
                _check(cancel is None or not cancel.is_set(), "CANCELLED")
                _check(time.monotonic() < deadline, "EXECUTION_TIMEOUT")
                for key, _ in selector.select(0.01):
                    data = os.read(key.fd, min(65536, self.max_output_bytes + 1))
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    total += len(data)
                    _check(total <= self.max_output_bytes, "OUTPUT_LIMIT")
                    if key.fileobj is process.stdout:
                        output.extend(data)
            while process.poll() is None:
                _check(cancel is None or not cancel.is_set(), "CANCELLED")
                _check(time.monotonic() < deadline, "EXECUTION_TIMEOUT")
                time.sleep(0.01)
            _check(process.returncode == 0, "EXECUTION_FAILED")
            return bytes(output)
        finally:
            selector.close()
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()

    def _copy(
        self,
        source: SealedDirectoryRef,
        stage: Path,
        cancel: threading.Event | None,
        deadline: float,
    ) -> None:
        expected = {item.path: item for item in source.files}
        _check(
            len(expected) == len(source.files)
            and len(expected) <= self.max_files
            and all(
                _path(item.path)
                and _hash(item.sha256)
                and type(item.size) is int
                and 0 <= item.size <= 16 * 1024 * 1024
                for item in source.files
            )
            and sum(item.size for item in source.files) <= self.max_input_bytes,
            "INVALID_INPUT",
        )
        seen: set[str] = set()
        visited = 0

        def walk(directory: int, prefix: str) -> None:
            nonlocal visited
            _check(len(prefix.split("/")) <= 64, "INPUT_LIMIT")
            names: list[str] = []
            with os.scandir(directory) as entries:
                for entry in entries:
                    _check(cancel is None or not cancel.is_set(), "CANCELLED")
                    _check(time.monotonic() < deadline, "EXECUTION_TIMEOUT")
                    visited += 1
                    _check(visited <= self.max_files * 4, "INPUT_LIMIT")
                    names.append(entry.name)
            for name in sorted(names):
                _check(cancel is None or not cancel.is_set(), "CANCELLED")
                _check(time.monotonic() < deadline, "EXECUTION_TIMEOUT")
                relative = prefix + name
                _check(_path(relative), "INVALID_INPUT")
                info = os.stat(name, dir_fd=directory, follow_symlinks=False)
                destination = stage / relative
                if stat.S_ISDIR(info.st_mode):
                    destination.mkdir(mode=0o700)
                    child = os.open(
                        name, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY, dir_fd=directory
                    )
                    try:
                        walk(child, relative + "/")
                    finally:
                        os.close(child)
                    destination.chmod(0o500)
                else:
                    _check(stat.S_ISREG(info.st_mode), "INVALID_INPUT")
                    _check(relative in expected, "INPUT_CHANGED")
                    item = expected[relative]
                    fd = os.open(
                        name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
                    )
                    with os.fdopen(fd, "rb") as stream:
                        _check(stat.S_ISREG(os.fstat(stream.fileno()).st_mode), "INVALID_INPUT")
                        data = stream.read(item.size + 1)
                    _check(
                        len(data) == item.size and hashlib.sha256(data).hexdigest() == item.sha256,
                        "INPUT_CHANGED",
                    )
                    destination.write_bytes(data)
                    destination.chmod(0o400)
                    seen.add(relative)

        try:
            directory = os.open(source.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY)
            try:
                walk(directory, "")
            finally:
                os.close(directory)
        except OSError:
            raise ReaAdapterError("INVALID_INPUT") from None
        _check(seen == set(expected), "INPUT_CHANGED")
        stage.chmod(0o500)

    def inventory(
        self, source: SealedDirectoryRef, cancel: threading.Event | None = None
    ) -> ReferenceInventoryResult:
        _check(cancel is None or not cancel.is_set(), "CANCELLED")
        deadline = time.monotonic() + self.timeout_seconds
        self._trust(cancel, deadline)
        try:
            _check(
                not self.staging_root.is_symlink()
                and self.staging_root.is_dir()
                and not self.staging_root.stat().st_mode & 0o077,
                "INVALID_CONFIGURATION",
            )
            work = Path(tempfile.mkdtemp(prefix="rea-", dir=self.staging_root))
        except OSError:
            raise ReaAdapterError("PROVIDER_UNAVAILABLE") from None
        stage = work / "input"
        stage.mkdir(mode=0o700)
        try:
            self._copy(source, stage, cancel, deadline)
            version = self._run(("--version",), work, cancel, deadline)
            _check(version.strip() == REA_VERSION.encode(), "PROVIDER_INCOMPATIBLE")
            raw = self._run(
                ("import-reference-source", str(stage), "--format", "json"), work, cancel, deadline
            )
            self._trust(cancel, deadline)
            graph = validate_inventory(raw, source.files)
            # Recheck private bytes after provider execution, including unreported/excluded files.
            verify = work / "verify"
            verify.mkdir(mode=0o700)
            self._copy(SealedDirectoryRef(stage, source.files), verify, cancel, deadline)
            return ReferenceInventoryResult(
                graph, raw, hashlib.sha256(raw).hexdigest(), graph["root_sha256"], source.files
            )
        finally:
            for directory, _, _ in os.walk(work):
                Path(directory).chmod(0o700)
            shutil.rmtree(work)
