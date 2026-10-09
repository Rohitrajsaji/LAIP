"""Bounded, inert source readers. No checkout, setup, hook, or source execution."""

from __future__ import annotations

import fnmatch
import io
import os
import stat
import struct
import time
import unicodedata
import zipfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import IO, BinaryIO

DEFAULT_EXCLUSIONS = (
    ".git",
    ".git/*",
    "node_modules",
    "node_modules/*",
    "*/.git",
    "*/.git/*",
    "*/node_modules",
    "*/node_modules/*",
    ".local",
    ".local/*",
    "*/.local",
    "*/.local/*",
    ".env",
    ".env.*",
    "*/.env",
    "*/.env.*",
    "*.pem",
    "*.key",
    "*.p12",
)


class ImportReadError(ValueError):
    """Unsafe, inconsistent, cancelled, or over-budget input; do not seal it."""


@dataclass(frozen=True)
class ImportLimits:
    max_files: int = 10000
    max_bytes: int = 256 * 1024 * 1024
    max_file_bytes: int = 16 * 1024 * 1024
    max_archive_bytes: int = 256 * 1024 * 1024
    max_ratio: int = 1000
    max_depth: int = 64
    timeout_seconds: float = 120

    def __post_init__(self) -> None:
        for value in (
            self.max_files,
            self.max_bytes,
            self.max_file_bytes,
            self.max_archive_bytes,
            self.max_ratio,
            self.max_depth,
        ):
            if type(value) is not int or value < 1:
                raise ValueError("Limits must be positive integers")
        if not 0 < self.timeout_seconds <= 86400:
            raise ValueError("Invalid import timeout")


DEFAULT_LIMITS = ImportLimits()


@dataclass(frozen=True)
class InputRecord:
    path: str
    raw: bytes | None
    status: str
    diagnostics: tuple[str, ...] = ()


class _Budget:
    def __init__(self, limits: ImportLimits, cancelled: Callable[[], bool] | None):
        self.limits = limits
        self.cancelled = cancelled
        self.deadline = time.monotonic() + limits.timeout_seconds
        self.entries = 0
        self.bytes = 0
        self.names: dict[str, bool] = {}
        self.spellings: dict[str, str] = {}
        self.ancestors: set[str] = set()

    def check(self) -> None:
        if self.cancelled is not None and self.cancelled():
            raise ImportReadError("Import cancelled")
        if time.monotonic() >= self.deadline:
            raise ImportReadError("Import time limit exceeded")

    def entry(self, path: str, directory: bool, size: int) -> None:
        self.check()
        self.entries += 1
        if self.entries > self.limits.max_files:
            raise ImportReadError("Entry limit exceeded")
        if len(path.split("/")) > self.limits.max_depth:
            raise ImportReadError("Path depth limit exceeded")
        if size > self.limits.max_file_bytes or size < 0:
            raise ImportReadError("File size limit exceeded")
        self.bytes += size
        if self.bytes > self.limits.max_bytes:
            raise ImportReadError("Total size limit exceeded")
        parts = path.split("/")
        for index in range(1, len(parts) + 1):
            prefix = "/".join(parts[:index])
            folded = prefix.casefold()
            if folded in self.spellings and self.spellings[folded] != prefix:
                raise ImportReadError("Ambiguous path alias")
            self.spellings[folded] = prefix
            if index < len(parts):
                self.ancestors.add(folded)
            if index < len(parts) and self.names.get(folded) is False:
                raise ImportReadError("File/directory conflict")
        key = path.casefold()
        if key in self.names:
            raise ImportReadError("Duplicate path")
        if not directory and key in self.ancestors:
            raise ImportReadError("File/directory conflict")
        self.names[key] = directory


def _path(name: str, *, directory: bool = False) -> str:
    if directory and name.endswith("/"):
        name = name[:-1]
    if not name or name.startswith("/") or "\\" in name or ":" in name:
        raise ImportReadError("Invalid source path")
    if unicodedata.normalize("NFKC", name) != name:
        raise ImportReadError("Non-canonical Unicode path")
    if any(unicodedata.category(char).startswith("C") for char in name):
        raise ImportReadError("Control or invisible path character")
    for part in name.split("/"):
        if part in ("", ".", "..") or part.endswith((" ", ".")):
            raise ImportReadError("Ambiguous source path")
    return name


def _excluded(path: str, exclusions: Sequence[str]) -> bool:
    components = path.split("/")
    return any(
        fnmatch.fnmatchcase("/".join(components[:depth]), pattern)
        for depth in range(1, len(components) + 1)
        for pattern in exclusions
    )


def _central_directory(data: bytes, budget: _Budget) -> int:
    """Bound directory parsing before ZipFile creates its per-entry object list."""
    start = max(0, len(data) - 65557)
    marker = data.rfind(b"PK\x05\x06", start)
    if marker < 0 or len(data) - marker < 22:
        raise ImportReadError("Missing ZIP directory")
    _, disk, cd_disk, disk_count, count, size, offset, comment = struct.unpack_from(
        "<4s4H2LH", data, marker
    )
    if (
        disk
        or cd_disk
        or disk_count != count
        or count == 65535
        or size == 0xFFFFFFFF
        or offset == 0xFFFFFFFF
        or marker + 22 + comment != len(data)
        or offset + size != marker
        or count > budget.limits.max_files
    ):
        raise ImportReadError("Unsupported or excessive ZIP directory")
    position = offset
    seen = 0
    while position < marker:
        budget.check()
        if position + 46 > marker or data[position : position + 4] != b"PK\x01\x02":
            raise ImportReadError("Invalid ZIP directory")
        name_len, extra_len, comment_len, entry_disk = struct.unpack_from(
            "<4H", data, position + 28
        )
        if entry_disk:
            raise ImportReadError("Multipart ZIP is unsupported")
        position += 46 + name_len + extra_len + comment_len
        seen += 1
        if seen > budget.limits.max_files or position > marker:
            raise ImportReadError("ZIP directory limit exceeded")
    if seen != count:
        raise ImportReadError("ZIP directory count mismatch")
    return int(count)


def _read_bounded(source: IO[bytes], maximum: int, budget: _Budget) -> bytes:
    chunks: list[bytes] = []
    total = 0
    while True:
        budget.check()
        chunk = source.read(min(65536, maximum - total + 1))
        if not chunk:
            return b"".join(chunks)
        total += len(chunk)
        if total > maximum:
            raise ImportReadError("Stream size limit exceeded")
        chunks.append(chunk)


def _validate_extra(extra: bytes) -> None:
    position = 0
    while position < len(extra):
        if position + 4 > len(extra):
            raise ImportReadError("Malformed ZIP extra metadata")
        identifier, length = struct.unpack_from("<HH", extra, position)
        position += 4 + length
        if position > len(extra) or identifier in (0x0001, 0x000D, 0x756E, 0x7075):
            raise ImportReadError("ZIP link, secondary path, or ZIP64 metadata unsupported")


def read_zip(
    source: BinaryIO,
    *,
    limits: ImportLimits = DEFAULT_LIMITS,
    exclusions: Sequence[str] = DEFAULT_EXCLUSIONS,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[InputRecord, ...]:
    budget = _Budget(limits, cancelled)
    data = _read_bounded(source, limits.max_archive_bytes, budget)
    _central_directory(data, budget)
    records: list[InputRecord] = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for info in archive.infolist():
                _validate_extra(info.extra)
                directory = info.is_dir()
                path = _path(info.orig_filename, directory=directory)
                mode = info.external_attr >> 16
                kind = stat.S_IFMT(mode)
                if (
                    (kind and kind not in (stat.S_IFREG, stat.S_IFDIR))
                    or bool(kind == stat.S_IFDIR) != directory
                    and kind != 0
                    or info.flag_bits & 1
                    or info.compress_type not in (0, 8)
                    or info.file_size > max(1, info.compress_size) * limits.max_ratio
                    or directory
                    and info.file_size
                ):
                    raise ImportReadError("Unsupported or unsafe ZIP member")
                budget.entry(path, directory, info.file_size)
                # Validate excluded bytes too: CRC/limits must not be bypassed by exclusions.
                with archive.open(info) as member:
                    raw = _read_bounded(member, limits.max_file_bytes, budget)
                if len(raw) != info.file_size:
                    raise ImportReadError("ZIP size mismatch")
                if directory and _excluded(path, exclusions):
                    records.append(InputRecord(path, None, "excluded", ("SUBTREE_EXCLUDED",)))
                if not directory:
                    excluded = _excluded(path, exclusions)
                    records.append(
                        InputRecord(
                            path,
                            None if excluded else raw,
                            "excluded" if excluded else "imported",
                            ("excluded_by_policy",) if excluded else (),
                        )
                    )
    except (zipfile.BadZipFile, RuntimeError, NotImplementedError, EOFError, OSError) as exc:
        raise ImportReadError("Invalid ZIP content") from exc
    return tuple(sorted(records, key=lambda item: item.path))


def _open_root(path: Path) -> int:
    if not path.is_absolute():
        raise ImportReadError("Configured root must be absolute")
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for component in path.parts[1:]:
            if component in ("", ".", ".."):
                raise ImportReadError("Invalid configured root")
            child = os.open(
                component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=descriptor
            )
            os.close(descriptor)
            descriptor = child
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _fingerprint(info: os.stat_result) -> tuple[int, int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def read_local(
    root_id: str,
    configured_roots: Mapping[str, Path],
    *,
    limits: ImportLimits = DEFAULT_LIMITS,
    exclusions: Sequence[str] = DEFAULT_EXCLUSIONS,
    cancelled: Callable[[], bool] | None = None,
) -> tuple[InputRecord, ...]:
    if root_id not in configured_roots:
        raise ImportReadError("Unknown configured source root")
    budget = _Budget(limits, cancelled)
    records: list[InputRecord] = []

    def walk(descriptor: int, prefix: str) -> None:
        before_directory = os.fstat(descriptor)
        with os.scandir(descriptor) as entries:
            for entry in entries:
                budget.check()
                path = _path(prefix + entry.name)
                try:
                    before = os.stat(entry.name, dir_fd=descriptor, follow_symlinks=False)
                except OSError as exc:
                    # Missing/unreadable candidates still count toward the entry budget.
                    budget.entry(path, False, 0)
                    records.append(InputRecord(path, None, "failed", ("source_unavailable",)))
                    if not isinstance(exc, (FileNotFoundError, PermissionError)):
                        raise ImportReadError("Source inspection failed") from exc
                    continue
                directory = stat.S_ISDIR(before.st_mode)
                if not directory and not stat.S_ISREG(before.st_mode):
                    raise ImportReadError("Symlink or special source entry")
                budget.entry(path, directory, 0 if directory else before.st_size)
                child: int | None = None
                try:
                    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                    if directory:
                        flags |= os.O_DIRECTORY
                    child = os.open(entry.name, flags, dir_fd=descriptor)
                    opened = os.fstat(child)
                    if _fingerprint(before) != _fingerprint(opened):
                        raise ImportReadError("Source changed during opening")
                    if directory:
                        if _excluded(path, exclusions):
                            records.append(
                                InputRecord(path, None, "excluded", ("SUBTREE_EXCLUDED",))
                            )
                        walk(child, path + "/")
                        after = os.stat(entry.name, dir_fd=descriptor, follow_symlinks=False)
                        if _fingerprint(opened) != _fingerprint(after):
                            raise ImportReadError("Directory changed during reading")
                    else:
                        excluded = _excluded(path, exclusions)
                        # Read even exclusions to enforce consistency and bounded total bytes.
                        with os.fdopen(os.dup(child), "rb") as stream:
                            raw = _read_bounded(stream, limits.max_file_bytes, budget)
                        after = os.stat(entry.name, dir_fd=descriptor, follow_symlinks=False)
                        if (
                            _fingerprint(opened) != _fingerprint(os.fstat(child))
                            or _fingerprint(opened) != _fingerprint(after)
                            or len(raw) != opened.st_size
                        ):
                            raise ImportReadError("Source changed during reading")
                        records.append(
                            InputRecord(
                                path,
                                None if excluded else raw,
                                "excluded" if excluded else "imported",
                                ("excluded_by_policy",) if excluded else (),
                            )
                        )
                except (FileNotFoundError, PermissionError) as exc:
                    if directory:
                        raise ImportReadError(
                            "Directory unavailable; scope cannot be enumerated"
                        ) from exc
                    records.append(InputRecord(path, None, "failed", ("source_unavailable",)))
                except OSError as exc:
                    raise ImportReadError("Unsafe or unreadable source entry") from exc
                finally:
                    if child is not None:
                        os.close(child)
        if _fingerprint(before_directory) != _fingerprint(os.fstat(descriptor)):
            raise ImportReadError("Source directory changed during reading")

    root: int | None = None
    try:
        root = _open_root(configured_roots[root_id])
        walk(root, "")
    except OSError as exc:
        raise ImportReadError("Configured source root unavailable or unsafe") from exc
    finally:
        if root is not None:
            os.close(root)
    return tuple(sorted(records, key=lambda item: item.path))
