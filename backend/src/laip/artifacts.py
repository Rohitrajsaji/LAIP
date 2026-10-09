"""Private, immutable content-addressed bytes; publication never implies a DB reference."""

import hashlib
import os
import re
import stat
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO
from uuid import uuid4


class ArtifactError(ValueError):
    pass


@dataclass(frozen=True)
class BlobRef:
    sha256: str
    byte_length: int
    storage_key: str


class LocalArtifactStore:
    def __init__(self, root: Path) -> None:
        self.root = root
        try:
            with self._directory() as directory:
                if os.fstat(directory).st_mode & 0o077:
                    raise ArtifactError("Artifact root must be private")
        except OSError:
            raise ArtifactError("Private artifact root unavailable") from None

    @contextmanager
    def _directory(self) -> Iterator[int]:
        descriptor = os.open(self.root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            yield descriptor
        finally:
            os.close(descriptor)

    @contextmanager
    def _child(self, parent: int, name: str, *, create: bool = False) -> Iterator[int]:
        if create:
            try:
                os.mkdir(name, mode=0o700, dir_fd=parent)
                os.fsync(parent)
            except FileExistsError:
                pass
        descriptor = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        try:
            yield descriptor
        finally:
            os.close(descriptor)

    @staticmethod
    def _validate(ref: BlobRef) -> None:
        if not re.fullmatch(r"[a-f0-9]{64}", ref.sha256) or ref.byte_length < 0:
            raise ArtifactError("Invalid blob reference")
        if ref.storage_key != f"objects/{ref.sha256[:2]}/{ref.sha256}":
            raise ArtifactError("Invalid blob reference")

    @staticmethod
    def _checked_stream(directory: int, ref: BlobRef) -> BinaryIO:
        descriptor = os.open(
            ref.sha256, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
        )
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            os.close(descriptor)
            raise ArtifactError("Artifact must be a regular file")
        stream = os.fdopen(descriptor, "rb")
        try:
            digest = hashlib.sha256()
            length = 0
            while chunk := stream.read(65536):
                digest.update(chunk)
                length += len(chunk)
            if digest.hexdigest() != ref.sha256 or length != ref.byte_length:
                raise ArtifactError("Artifact digest or length mismatch")
            stream.seek(0)
            return stream
        except BaseException:
            stream.close()
            raise

    def put(self, stream: BinaryIO, *, max_bytes: int, job_id: str, fence: int) -> BlobRef:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", job_id) or fence < 1 or max_bytes < 1:
            raise ArtifactError("Invalid staging identity or byte limit")
        try:
            with self._directory() as root, self._child(root, "staging", create=True) as staging:
                with self._child(staging, f"{job_id}-{fence}", create=True) as job:
                    name = f"{uuid4().hex}.part"
                    descriptor = os.open(
                        name,
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                        0o600,
                        dir_fd=job,
                    )
                    try:
                        digest = hashlib.sha256()
                        length = 0
                        with os.fdopen(descriptor, "wb") as output:
                            while chunk := stream.read(65536):
                                length += len(chunk)
                                if length > max_bytes:
                                    raise ArtifactError("Artifact byte limit exceeded")
                                digest.update(chunk)
                                output.write(chunk)
                            output.flush()
                            os.fchmod(output.fileno(), 0o400)
                            os.fsync(output.fileno())
                        sha = digest.hexdigest()
                        ref = BlobRef(sha, length, f"objects/{sha[:2]}/{sha}")
                        with self._child(root, "objects", create=True) as objects:
                            with self._child(objects, sha[:2], create=True) as bucket:
                                try:
                                    os.link(
                                        name,
                                        sha,
                                        src_dir_fd=job,
                                        dst_dir_fd=bucket,
                                        follow_symlinks=False,
                                    )
                                except FileExistsError:
                                    self._checked_stream(bucket, ref).close()
                                os.fsync(bucket)
                        return ref
                    finally:
                        os.unlink(name, dir_fd=job)
        except OSError:
            raise ArtifactError("Artifact publication unavailable") from None

    def open(self, ref: BlobRef) -> BinaryIO:
        self._validate(ref)
        try:
            with self._directory() as root, self._child(root, "objects") as objects:
                with self._child(objects, ref.sha256[:2]) as bucket:
                    return self._checked_stream(bucket, ref)
        except OSError:
            raise ArtifactError("Artifact unavailable") from None

    def verify(self, ref: BlobRef) -> bool:
        with self.open(ref):
            return True
