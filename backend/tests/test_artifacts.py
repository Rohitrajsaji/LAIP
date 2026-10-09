import hashlib
import io
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from laip.artifacts import ArtifactError, BlobRef, LocalArtifactStore


def test_private_cas_roundtrip_and_concurrent_deduplication(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    content = b"inert source\n" * 100
    with ThreadPoolExecutor(max_workers=4) as pool:
        refs = list(
            pool.map(
                lambda _: store.put(
                    io.BytesIO(content), max_bytes=4096, job_id="job_fixture", fence=1
                ),
                range(12),
            )
        )
    assert len(set(refs)) == 1
    assert refs[0].sha256 == hashlib.sha256(content).hexdigest()
    assert refs[0].byte_length == len(content)
    with store.open(refs[0]) as stream:
        assert stream.read() == content
    assert store.verify(refs[0])
    assert len(list((tmp_path / "objects").rglob(refs[0].sha256))) == 1
    assert not list((tmp_path / "staging").rglob("*.part"))
    assert (tmp_path / refs[0].storage_key).stat().st_mode & 0o777 == 0o400


def test_limits_and_stream_failures_leave_no_partial_objects(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    with pytest.raises(ArtifactError, match="limit"):
        store.put(io.BytesIO(b"too long"), max_bytes=3, job_id="job", fence=1)

    class Broken(io.BytesIO):
        def read(self, size: int = -1) -> bytes:
            raise OSError("private error")

    with pytest.raises(ArtifactError):
        store.put(Broken(), max_bytes=10, job_id="job", fence=1)
    assert not list((tmp_path / "staging").rglob("*.part"))
    assert not list((tmp_path / "objects").glob("*/*"))


@pytest.mark.parametrize(
    "job_id,fence,limit", [("../escape", 1, 10), ("x", 0, 10), ("x", 1, 0), ("", 1, 10)]
)
def test_staging_identity_validation(tmp_path: Path, job_id: str, fence: int, limit: int) -> None:
    with pytest.raises(ArtifactError):
        LocalArtifactStore(tmp_path).put(
            io.BytesIO(b"x"), max_bytes=limit, job_id=job_id, fence=fence
        )


@pytest.mark.parametrize(
    "digest,key,length",
    [("x", "objects/x/x", 1), ("a" * 64, "../escape", 1), ("a" * 64, "objects/aa/" + "a" * 64, -1)],
)
def test_arbitrary_paths_and_invalid_refs_rejected(
    tmp_path: Path, digest: str, key: str, length: int
) -> None:
    with pytest.raises(ArtifactError):
        LocalArtifactStore(tmp_path).open(BlobRef(digest, length, key))


def test_symlinks_and_corruption_fail_closed(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "link"
    link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(ArtifactError):
        LocalArtifactStore(link)
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    store = LocalArtifactStore(root)
    (root / "objects").symlink_to(outside, target_is_directory=True)
    with pytest.raises(ArtifactError):
        store.put(io.BytesIO(b"x"), max_bytes=3, job_id="job", fence=1)
    (root / "objects").unlink()
    ref = store.put(io.BytesIO(b"x"), max_bytes=3, job_id="job", fence=1)
    target = root / ref.storage_key
    target.chmod(0o600)
    target.write_bytes(b"corrupt")
    with pytest.raises(ArtifactError):
        store.open(ref)
    with pytest.raises(ArtifactError):
        store.put(io.BytesIO(b"x"), max_bytes=3, job_id="job", fence=2)


def test_missing_blob_and_empty_bytes(tmp_path: Path) -> None:
    store = LocalArtifactStore(tmp_path)
    ref = store.put(io.BytesIO(b""), max_bytes=1, job_id="job", fence=1)
    assert store.verify(ref)
    (tmp_path / ref.storage_key).unlink()
    with pytest.raises(ArtifactError):
        store.open(ref)


def test_nonregular_objects_and_open_root_permissions_rejected(tmp_path: Path) -> None:
    import os

    root = tmp_path / "not_private"
    root.mkdir(mode=0o755)
    with pytest.raises(ArtifactError, match="private"):
        LocalArtifactStore(root)
    store = LocalArtifactStore(tmp_path)
    ref = store.put(io.BytesIO(b"x"), max_bytes=1, job_id="job", fence=1)
    target = tmp_path / ref.storage_key
    target.unlink()
    os.mkfifo(target)
    with pytest.raises(ArtifactError, match="regular"):
        store.open(ref)
