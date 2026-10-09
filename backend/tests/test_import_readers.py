from __future__ import annotations

import io
import stat
import zipfile
from pathlib import Path

import pytest

from laip.import_readers import ImportLimits, ImportReadError, read_local, read_zip


def archive(entries: list[tuple[str, bytes]], *, compression: int = 0) -> io.BytesIO:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=compression) as output:
        for name, raw in entries:
            output.writestr(name, raw)
    stream.seek(0)
    return stream


def test_zip_preserves_bytes_and_excluded_status() -> None:
    records = read_zip(archive([("LIB/member.rpgle", b"\xff\x00"), (".git/hooks/run", b"evil")]))
    assert [(r.path, r.raw, r.status) for r in records] == [
        (".git/hooks/run", None, "excluded"),
        ("LIB/member.rpgle", b"\xff\x00", "imported"),
    ]


@pytest.mark.parametrize(
    "name",
    [
        "../escape",
        "/absolute",
        "C:/drive",
        "a\\b",
        "a/../b",
        "a\x00b",
        "a\u200bb",
        "a//b",
        "./b",
        "e\u0301",
    ],
)
def test_zip_rejects_unsafe_names(name: str) -> None:
    # zipfile truncates NUL itself; patch the central/local name to represent hostile input.
    if "\x00" in name:
        data = archive([("axb", b"x")]).getvalue().replace(b"axb", b"a\x00b")
        source = io.BytesIO(data)
    else:
        source = archive([(name, b"x")])
    with pytest.raises(ImportReadError):
        read_zip(source)


@pytest.mark.parametrize(
    "entries",
    [[("A", b"x"), ("a", b"y")], [("a", b"x"), ("a/b", b"y")], [("a", b"x"), ("a", b"y")]],
)
def test_zip_rejects_collisions(entries: list[tuple[str, bytes]]) -> None:
    with pytest.raises(ImportReadError):
        read_zip(archive(entries))


def test_zip_rejects_symlinks_even_when_excluded() -> None:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as output:
        entry = zipfile.ZipInfo(".git/link")
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        output.writestr(entry, "../../escape")
    stream.seek(0)
    with pytest.raises(ImportReadError):
        read_zip(stream)


@pytest.mark.parametrize(
    "limits",
    [
        ImportLimits(max_files=1),
        ImportLimits(max_bytes=1),
        ImportLimits(max_file_bytes=1),
        ImportLimits(max_archive_bytes=10),
        ImportLimits(max_depth=1),
    ],
)
def test_zip_limits(limits: ImportLimits) -> None:
    with pytest.raises(ImportReadError):
        read_zip(archive([("a/b", b"xx"), ("excluded/c", b"xx")]), limits=limits)


def test_zip_ratio_crc_unsupported_and_cancelled() -> None:
    with pytest.raises(ImportReadError):
        read_zip(
            archive([("bomb", b"0" * 10000)], compression=zipfile.ZIP_DEFLATED),
            limits=ImportLimits(max_ratio=2),
        )
    raw = archive([("file", b"content")]).getvalue().replace(b"content", b"corrupt")
    with pytest.raises(ImportReadError):
        read_zip(io.BytesIO(raw))
    with pytest.raises(ImportReadError):
        read_zip(archive([("file", b"content")], compression=zipfile.ZIP_BZIP2))
    with pytest.raises(ImportReadError):
        read_zip(archive([("file", b"content")]), cancelled=lambda: True)


def test_local_configured_bytes_exclusions_and_no_execution(tmp_path: Path) -> None:
    (tmp_path / "member").write_bytes(b"\xff\x00")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "hooks").mkdir()
    (tmp_path / ".git" / "hooks" / "run").write_text("exit 1")
    records = read_local("source", {"source": tmp_path})
    assert [(r.path, r.raw, r.status) for r in records] == [
        (".git", None, "excluded"),
        (".git/hooks", None, "excluded"),
        (".git/hooks/run", None, "excluded"),
        ("member", b"\xff\x00", "imported"),
    ]
    with pytest.raises(ImportReadError):
        read_local(str(tmp_path), {"source": tmp_path})


def test_local_symlink_special_and_limit_rejection(tmp_path: Path) -> None:
    (tmp_path / "member").write_bytes(b"xx")
    with pytest.raises(ImportReadError):
        read_local("s", {"s": tmp_path}, limits=ImportLimits(max_file_bytes=1))
    (tmp_path / ".git").symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(ImportReadError):
        read_local("s", {"s": tmp_path})
    with pytest.raises(ImportReadError):
        read_local("s", {"s": tmp_path}, cancelled=lambda: True)


@pytest.mark.parametrize("extra_id", [0x0001, 0x000D, 0x756E, 0x7075])
def test_zip_rejects_link_and_secondary_path_metadata(extra_id: int) -> None:
    import struct

    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as output:
        entry = zipfile.ZipInfo("member")
        entry.extra = struct.pack("<HH", extra_id, 2) + b"xx"
        output.writestr(entry, b"inert")
    stream.seek(0)
    with pytest.raises(ImportReadError):
        read_zip(stream)


def test_local_rejects_excluded_child_symlinks_and_special_files(tmp_path: Path) -> None:
    import os

    excluded = tmp_path / ".git"
    excluded.mkdir()
    (excluded / "escape").symlink_to(tmp_path.parent)
    with pytest.raises(ImportReadError):
        read_local("s", {"s": tmp_path})
    (excluded / "escape").unlink()
    os.mkfifo(tmp_path / "pipe")
    with pytest.raises(ImportReadError):
        read_local("s", {"s": tmp_path})


def test_zip_directory_counts_and_collision_reverse() -> None:
    records = read_zip(archive([(".git/", b""), (".git/hooks/run", b"inert")]))
    assert records[0].diagnostics == ("SUBTREE_EXCLUDED",)
    with pytest.raises(ImportReadError):
        read_zip(archive([("a/b", b"content"), ("a", b"other")]))
    with pytest.raises(ImportReadError):
        read_zip(archive([("empty/", b""), ("a", b"")]), limits=ImportLimits(max_files=1))


def test_corrupt_directory_and_limits_validation() -> None:
    for data in (b"not zip", archive([("a", b"b")]).getvalue()[:-10]):
        with pytest.raises(ImportReadError):
            read_zip(io.BytesIO(data))
    with pytest.raises(ValueError):
        ImportLimits(max_files=0)
    with pytest.raises(ValueError):
        ImportLimits(timeout_seconds=float("nan"))
    with pytest.raises(ImportReadError):
        read_zip(archive([("a", b"b")]), limits=ImportLimits(timeout_seconds=1e-12))


@pytest.mark.parametrize(
    "name",
    [
        ".local/token",
        "nested/.local/token",
        ".env",
        ".env.production",
        "nested/.env",
        "nested/.env.production",
        "secret.pem",
        "nested/private.key",
        "keystore.p12",
    ],
)
def test_default_secret_exclusions_preserve_no_raw(name: str, tmp_path: Path) -> None:
    records = read_zip(archive([(name, b"private fixture")]))
    assert records[0].status == "excluded"
    assert records[0].raw is None
    target = tmp_path / name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"private fixture")
    local = read_local("fixture", {"fixture": tmp_path})
    file_record = next(record for record in local if record.path == name)
    assert file_record.status == "excluded"
    assert file_record.raw is None


def test_directory_exclusion_covers_descendants_and_implicit_zip_directory(tmp_path: Path) -> None:
    secret = tmp_path / "secret"
    secret.mkdir()
    (secret / "key.txt").write_bytes(b"private fixture")
    for records in (
        read_local("s", {"s": tmp_path}, exclusions=("secret",)),
        read_zip(
            archive([("secret/", b""), ("secret/key.txt", b"private fixture")]),
            exclusions=("secret",),
        ),
        read_zip(archive([("secret/key.txt", b"private fixture")]), exclusions=("secret",)),
    ):
        record = next(item for item in records if item.path == "secret/key.txt")
        assert record.status == "excluded"
        assert record.raw is None
