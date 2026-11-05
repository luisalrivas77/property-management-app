"""Blob storage for document originals (principle 6: originals are immutable).

Originals are stored content-addressed under blob_key(workspace_id, sha256). A key, once
written, always holds the same bytes: put is idempotent for equal bytes and refuses different
ones, so nothing ever overwrites an original.

LocalBlobStore serves dev and tests. An S3BlobStore (private EU bucket, served through
short-lived signed URLs) lands with deployment, behind the same BlobStore protocol.
"""

import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Protocol, runtime_checkable
from uuid import UUID

_SHA256_HEX = re.compile(r"[0-9a-f]{64}")
_READ_ONLY = stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH


class BlobStoreError(Exception):
    """Base for blob storage errors."""


class InvalidBlobKeyError(BlobStoreError, ValueError):
    """The key is empty, absolute or would escape the store's root."""


class BlobConflictError(BlobStoreError):
    """Different bytes were offered for a key that already holds an original."""


class BlobNotFoundError(BlobStoreError, LookupError):
    """No blob is stored under the key."""


@runtime_checkable
class BlobStore(Protocol):
    def put(self, key: str, data: bytes, media_type: str) -> None:
        """Store `data` under `key`. A no-op if the key already holds exactly these bytes;
        BlobConflictError if it holds different ones. Never overwrites."""
        ...

    def get(self, key: str) -> bytes:
        """The bytes under `key`; BlobNotFoundError if there are none."""
        ...

    def exists(self, key: str) -> bool: ...

    def delete(self, key: str) -> None:
        """Remove the blob (retention only). A no-op if it is already gone."""
        ...


def blob_key(workspace_id: UUID, sha256_hex: str) -> str:
    """The storage key of an original: "{workspace_id}/sha256/{hex}"."""
    if not isinstance(workspace_id, UUID):
        raise TypeError(f"workspace_id must be a UUID, got {type(workspace_id).__name__}")
    if not _SHA256_HEX.fullmatch(sha256_hex):
        raise ValueError("sha256_hex must be 64 lowercase hex characters")
    return f"{workspace_id}/sha256/{sha256_hex}"


class LocalBlobStore:
    """Blobs as read-only files under `root`. For dev and tests, not for production data."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, data: bytes, media_type: str) -> None:
        # media_type is metadata for an object store's Content-Type; a local file has none.
        path = self._path(key)
        if path.exists():
            self._check_same(path, key, data)
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".tmp-")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            tmp.chmod(_READ_ONLY)
            try:
                # Publish atomically. Unlike rename, link refuses an existing target, so a
                # concurrent writer can never be overwritten: the loser compares bytes instead.
                os.link(tmp, path)
            except FileExistsError:
                self._check_same(path, key, data)
        finally:
            tmp.unlink(missing_ok=True)

    def get(self, key: str) -> bytes:
        try:
            return self._path(key).read_bytes()
        except FileNotFoundError:
            raise BlobNotFoundError(f"no blob under {key!r}") from None

    def exists(self, key: str) -> bool:
        return self._path(key).is_file()

    def delete(self, key: str) -> None:
        self._path(key).unlink(missing_ok=True)

    def _path(self, key: str) -> Path:
        if not key or "\x00" in key or "\\" in key or key.startswith("/"):
            raise InvalidBlobKeyError(f"invalid blob key {key!r}")
        parts = key.split("/")
        if any(part in ("", ".", "..") for part in parts):
            raise InvalidBlobKeyError(f"invalid blob key {key!r}")
        path = (self.root / key).resolve()
        # resolve() follows symlinks, so a link planted inside root cannot lead out of it.
        if path == self.root or not path.is_relative_to(self.root):
            raise InvalidBlobKeyError(f"blob key {key!r} escapes the store root")
        return path

    @staticmethod
    def _check_same(path: Path, key: str, data: bytes) -> None:
        if path.read_bytes() != data:
            raise BlobConflictError(f"{key!r} holds different bytes; originals are immutable")
