"""LocalBlobStore and blob_key: immutable, content-addressed originals (principle 6)."""

import hashlib
import stat
import uuid

import pytest

from hausbuch.storage import (
    BlobConflictError,
    BlobNotFoundError,
    BlobStore,
    InvalidBlobKeyError,
    LocalBlobStore,
    blob_key,
)

WS = uuid.UUID("6f1c1b2e-0000-4000-8000-000000000001")
DATA = b"%PDF-1.7 Kaufvertrag"
KEY = blob_key(WS, hashlib.sha256(DATA).hexdigest())


@pytest.fixture
def store(tmp_path) -> LocalBlobStore:
    return LocalBlobStore(tmp_path / "blobs")


def test_local_store_satisfies_the_protocol(store):
    assert isinstance(store, BlobStore)


def test_blob_key_shape():
    hex_ = hashlib.sha256(DATA).hexdigest()
    assert blob_key(WS, hex_) == f"{WS}/sha256/{hex_}"


@pytest.mark.parametrize("bad", ["", "ab" * 31, "AB" * 32, "g" * 64, "../" + "a" * 61])
def test_blob_key_rejects_anything_but_a_sha256_hex(bad):
    with pytest.raises(ValueError):
        blob_key(WS, bad)


def test_blob_key_needs_a_uuid():
    with pytest.raises(TypeError):
        blob_key(str(WS), "a" * 64)


def test_put_get_exists_delete(store):
    assert not store.exists(KEY)
    store.put(KEY, DATA, "application/pdf")
    assert store.exists(KEY)
    assert store.get(KEY) == DATA
    store.delete(KEY)
    assert not store.exists(KEY)
    store.delete(KEY)  # already gone: no-op


def test_get_of_a_missing_key_raises(store):
    with pytest.raises(BlobNotFoundError):
        store.get(KEY)


def test_put_is_idempotent_for_the_same_bytes(store):
    store.put(KEY, DATA, "application/pdf")
    store.put(KEY, DATA, "application/pdf")
    assert store.get(KEY) == DATA


def test_put_refuses_different_bytes_under_an_existing_key(store):
    store.put(KEY, DATA, "application/pdf")
    with pytest.raises(BlobConflictError):
        store.put(KEY, b"forged", "application/pdf")
    assert store.get(KEY) == DATA


def _mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_stored_file_is_private_read_only_and_no_temp_file_is_left(store):
    store.put(KEY, DATA, "application/pdf")
    path = store.root / KEY
    assert _mode(path) == 0o400
    workspace_dir, sha_dir = store.root / str(WS), store.root / str(WS) / "sha256"
    assert [_mode(d) for d in (store.root, workspace_dir, sha_dir)] == [0o700] * 3
    assert sorted(p.name for p in path.parent.iterdir()) == [path.name]


@pytest.mark.parametrize(
    "key",
    [
        "",
        "/etc/passwd",
        "../outside",
        f"{WS}/../../outside",
        f"{WS}/sha256/../../../outside",
        f"{WS}//double",
        f"{WS}/./dot",
        "trailing/",
        "back\\slash",
        "nul\x00byte",
    ],
)
def test_keys_that_escape_or_are_malformed_are_refused(store, key):
    for call in (
        lambda: store.put(key, DATA, "application/pdf"),
        lambda: store.get(key),
        lambda: store.exists(key),
        lambda: store.delete(key),
    ):
        with pytest.raises(InvalidBlobKeyError):
            call()


def test_a_symlink_inside_root_cannot_lead_out(store, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (store.root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(InvalidBlobKeyError):
        store.put("link/x", DATA, "application/pdf")
    assert list(outside.iterdir()) == []
