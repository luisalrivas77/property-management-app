"""S3BlobStore: the BlobStore contract against LocalBlobStore, a fake S3 and a real bucket.

The contract tests run against every store. In CI the S3 store talks to FakeS3, an in-process
S3 behind a real boto3 client: FakeS3 answers botocore's `before-send` event, so requests are
serialised, signed, retried and their errors parsed by botocore exactly as against a server;
only the socket is missing. It honours `If-None-Match: *` like AWS (or ignores it, to model an
endpoint that does not), verifies Content-MD5, refuses ACL headers, and can inject errors and a
concurrent writer.

The real-bucket run (Q16) is by hand: set HAUSBUCH_TEST_S3_BUCKET, HAUSBUCH_TEST_S3_ENDPOINT_URL
(e.g. https://s3.fr-par.scw.cloud), HAUSBUCH_TEST_S3_REGION (fr-par) and credentials for the
SDK chain (AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY), then
`uv run pytest tests/test_storage_s3.py -v -rsx`. Each test writes under a fresh workspace
prefix and deletes it afterwards. test_real_bucket_honours_if_none_match passes when the
endpoint honours the conditional PUT and xfails with the reason when it does not; record which
in deploy/scaleway.md.
"""

import base64
import hashlib
import io
import os
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import boto3
import pytest
from botocore.awsrequest import AWSResponse
from botocore.exceptions import ClientError, ConnectionClosedError

from hausbuch.settings import Settings, SettingsError
from hausbuch.storage import (
    BlobConflictError,
    BlobNotFoundError,
    BlobStore,
    BlobStoreError,
    InvalidBlobKeyError,
    LocalBlobStore,
    blob_key,
)
from hausbuch.storage_s3 import (
    CONNECT_TIMEOUT_S,
    MAX_ATTEMPTS,
    PUT_ROUNDS,
    READ_TIMEOUT_S,
    S3BlobStore,
    make_s3_client,
)

DATA = b"%PDF-1.7 Kaufvertrag"
PDF = "application/pdf"
BUCKET = "hausbuch-test"
ENDPOINT = "https://s3.fr-par.scw.cloud"
REGION = "fr-par"

REAL_BUCKET = os.environ.get("HAUSBUCH_TEST_S3_BUCKET", "").strip()


# --- FakeS3 ------------------------------------------------------------------


class _Raw:
    """The slice of urllib3's response that botocore reads: stream() and read()."""

    def __init__(self, data: bytes) -> None:
        self._buf = io.BytesIO(data)

    def stream(self, amt: int = 65536, decode_content: bool | None = None) -> Iterator[bytes]:
        while chunk := self._buf.read(amt):
            yield chunk

    def read(self, amt: int | None = None) -> bytes:
        return self._buf.read(amt)

    def close(self) -> None:
        pass


class FakeS3:
    def __init__(self, bucket: str = BUCKET, *, honour_if_none_match: bool = True) -> None:
        self.bucket = bucket
        self.honour_if_none_match = honour_if_none_match
        self.objects: dict[str, tuple[bytes, str]] = {}  # key -> (bytes, content type)
        self.requests: list[tuple[str, str, dict[str, str]]] = []  # (method, key, headers)
        self.errors: list[tuple[str, int, str]] = []  # queued (method, status, code)
        self.before_put: Callable[[str], None] | None = None  # a concurrent writer

    def attach(self, client) -> None:
        client.meta.events.register("before-send.s3", self._handle)

    def inject(self, method: str, status: int, code: str, times: int = 1) -> None:
        self.errors.extend([(method, status, code)] * times)

    def methods(self, method: str) -> list[tuple[str, str, dict[str, str]]]:
        return [r for r in self.requests if r[0] == method]

    def _handle(self, request, **_) -> AWSResponse:
        url = urlsplit(request.url)
        host_bucket = url.hostname.split(".", 1)[0] if url.hostname else ""
        path = url.path.lstrip("/")
        if url.hostname and url.hostname != urlsplit(ENDPOINT).hostname:
            bucket, key = host_bucket, unquote(path)  # virtual-hosted style
        else:
            bucket, _, key = path.partition("/")  # path style
            key = unquote(key)
        headers = {
            k.lower(): v.decode() if isinstance(v, bytes) else v for k, v in request.headers.items()
        }
        method = request.method
        self.requests.append((method, key, headers))
        for i, (m, status, code) in enumerate(self.errors):
            if m == method:
                del self.errors[i]
                return self._error(request, method, status, code)
        if bucket != self.bucket:
            return self._error(request, method, 404, "NoSuchBucket")
        if parse_qs(url.query):
            return self._error(request, method, 501, "NotImplemented")  # not used by the store
        if method == "PUT":
            return self._put(request, key, headers)
        if key not in self.objects:
            if method == "DELETE":
                return self._ok(request, 204)
            return self._error(request, method, 404, "NoSuchKey")
        data, content_type = self.objects[key]
        if method == "DELETE":
            del self.objects[key]
            return self._ok(request, 204)
        meta = {
            "Content-Length": str(len(data)),
            "Content-Type": content_type,
            "ETag": f'"{hashlib.md5(data).hexdigest()}"',
        }
        return self._ok(request, 200, meta, data if method == "GET" else b"")

    def _put(self, request, key: str, headers: dict[str, str]) -> AWSResponse:
        body = request.body
        data = body.read() if hasattr(body, "read") else bytes(body or b"")
        if any(h.startswith(("x-amz-acl", "x-amz-grant")) for h in headers):
            return self._error(request, "PUT", 400, "FakeRefusesAcls")
        md5 = base64.b64encode(hashlib.md5(data).digest()).decode()
        if headers.get("content-md5", md5) != md5:
            return self._error(request, "PUT", 400, "BadDigest")
        if self.before_put is not None:
            writer, self.before_put = self.before_put, None
            writer(key)
        if (
            self.honour_if_none_match
            and headers.get("if-none-match") == "*"
            and key in self.objects
        ):
            return self._error(request, "PUT", 412, "PreconditionFailed")
        self.objects[key] = (data, headers.get("content-type", "binary/octet-stream"))
        return self._ok(request, 200, {"ETag": f'"{hashlib.md5(data).hexdigest()}"'})

    @staticmethod
    def _ok(request, status: int, headers: dict | None = None, body: bytes = b"") -> AWSResponse:
        return AWSResponse(request.url, status, headers or {}, _Raw(body))

    @staticmethod
    def _error(request, method: str, status: int, code: str) -> AWSResponse:
        # HEAD responses have no body; S3 errors on other methods are an XML document.
        body = (
            b""
            if method == "HEAD"
            else f"<Error><Code>{code}</Code><Message>fake</Message></Error>".encode()
        )
        return AWSResponse(request.url, status, {"Content-Type": "application/xml"}, _Raw(body))


def _session() -> boto3.session.Session:
    return boto3.session.Session(
        aws_access_key_id="test-access-key",
        aws_secret_access_key="test-secret-key",
        region_name=REGION,
    )


def _fake_store(fake: FakeS3, bucket: str = BUCKET) -> S3BlobStore:
    client = make_s3_client(ENDPOINT, REGION, session=_session())
    fake.attach(client)
    return S3BlobStore(bucket, client)


# --- Fixtures ------------------------------------------------------------------


@pytest.fixture
def ws() -> uuid.UUID:
    """A fresh workspace per test, so keys never collide (a real bucket keeps past runs)."""
    return uuid.uuid4()


@pytest.fixture
def key_of(ws) -> Callable[[bytes], str]:
    return lambda data: blob_key(ws, hashlib.sha256(data).hexdigest())


def _real_store_or_skip(ws: uuid.UUID) -> Iterator[S3BlobStore]:
    if not REAL_BUCKET:
        pytest.skip("real-bucket test: set HAUSBUCH_TEST_S3_BUCKET (and credentials) to run")
    session = boto3.session.Session()
    if session.get_credentials() is None:
        pytest.skip("real-bucket test: no S3 credentials in the SDK chain")
    client = make_s3_client(
        os.environ.get("HAUSBUCH_TEST_S3_ENDPOINT_URL") or None,
        os.environ.get("HAUSBUCH_TEST_S3_REGION") or None,
        session=session,
    )
    store = S3BlobStore(REAL_BUCKET, client)
    try:
        yield store
    finally:
        _purge_prefix(client, REAL_BUCKET, f"{ws}/")


def _purge_prefix(client, bucket: str, prefix: str) -> None:
    for page in client.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            client.delete_object(Bucket=bucket, Key=obj["Key"])
    try:  # a versioned bucket keeps old versions and delete markers
        pages = client.get_paginator("list_object_versions").paginate(Bucket=bucket, Prefix=prefix)
        for page in pages:
            for v in page.get("Versions", []) + page.get("DeleteMarkers", []):
                client.delete_object(Bucket=bucket, Key=v["Key"], VersionId=v["VersionId"])
    except ClientError:
        pass  # no version permissions: leftovers are test bytes under a random prefix


@pytest.fixture(params=["local", "s3-fake", "s3-real"])
def store(request, tmp_path: Path, ws) -> Iterator[BlobStore]:
    if request.param == "local":
        yield LocalBlobStore(tmp_path / "blobs")
    elif request.param == "s3-fake":
        yield _fake_store(FakeS3())
    else:
        yield from _real_store_or_skip(ws)


@pytest.fixture
def fake() -> FakeS3:
    return FakeS3()


@pytest.fixture
def s3(fake) -> S3BlobStore:
    return _fake_store(fake)


@pytest.fixture
def real_s3(ws) -> Iterator[S3BlobStore]:
    yield from _real_store_or_skip(ws)


# --- Contract: every store ------------------------------------------------------


def test_satisfies_the_protocol(store):
    assert isinstance(store, BlobStore)


def test_put_get_exists_delete(store, key_of):
    key = key_of(DATA)
    assert not store.exists(key)
    store.put(key, DATA, PDF)
    assert store.exists(key)
    assert store.get(key) == DATA
    store.delete(key)
    assert not store.exists(key)
    store.delete(key)  # already gone: no-op


def test_get_of_a_missing_key_raises(store, key_of):
    with pytest.raises(BlobNotFoundError):
        store.get(key_of(DATA))


def test_put_is_idempotent_for_the_same_bytes(store, key_of):
    key = key_of(DATA)
    store.put(key, DATA, PDF)
    store.put(key, DATA, PDF)
    assert store.get(key) == DATA


@pytest.mark.parametrize(
    "forged",
    [b"forged", DATA[:-1] + b"X", DATA + b"\n", b""],
    ids=["shorter", "same-size", "longer", "empty"],
)
def test_put_refuses_different_bytes_under_an_existing_key(store, key_of, forged):
    key = key_of(DATA)
    store.put(key, DATA, PDF)
    with pytest.raises(BlobConflictError):
        store.put(key, forged, PDF)
    assert store.get(key) == DATA


def test_every_byte_value_round_trips(store, key_of):
    data = bytes(range(256)) * 4
    key = key_of(data)
    store.put(key, data, "application/octet-stream")
    assert store.get(key) == data


def test_an_empty_blob_is_stored_and_differs_from_a_missing_one(store, key_of):
    key = key_of(b"")
    store.put(key, b"", "text/plain")
    assert store.exists(key)
    assert store.get(key) == b""
    with pytest.raises(BlobConflictError):
        store.put(key, b"x", "text/plain")


@pytest.mark.parametrize(
    "key",
    [
        "",
        "/etc/passwd",
        "../outside",
        "6f1c1b2e-0000-4000-8000-000000000001/../../outside",
        "6f1c1b2e-0000-4000-8000-000000000001//double",
        "6f1c1b2e-0000-4000-8000-000000000001/./dot",
        "trailing/",
        "back\\slash",
        "nul\x00byte",
    ],
)
def test_malformed_keys_are_refused(store, key):
    for call in (
        lambda: store.put(key, DATA, PDF),
        lambda: store.get(key),
        lambda: store.exists(key),
        lambda: store.delete(key),
    ):
        with pytest.raises(InvalidBlobKeyError):
            call()


# --- S3 specifics, against FakeS3 ------------------------------------------------


def test_put_is_a_conditional_private_write_with_type_and_md5(s3, fake, key_of):
    key = key_of(DATA)
    s3.put(key, DATA, PDF)
    [(_, put_key, headers)] = fake.methods("PUT")
    assert put_key == key
    assert headers["if-none-match"] == "*"
    assert headers["content-type"] == PDF
    assert headers["content-md5"] == base64.b64encode(hashlib.md5(DATA).digest()).decode()
    assert not [h for h in headers if h.startswith(("x-amz-acl", "x-amz-grant"))]
    assert fake.objects[key] == (DATA, PDF)


def test_a_writer_that_lands_between_head_and_put_is_never_overwritten(s3, fake, key_of):
    key = key_of(DATA)
    fake.before_put = lambda k: fake.objects.__setitem__(k, (b"theirs", PDF))
    with pytest.raises(BlobConflictError):
        s3.put(key, DATA, PDF)
    assert fake.objects[key] == (b"theirs", PDF)


def test_a_racing_writer_with_the_same_bytes_makes_put_a_no_op(s3, fake, key_of):
    key = key_of(DATA)
    fake.before_put = lambda k: fake.objects.__setitem__(k, (DATA, PDF))
    s3.put(key, DATA, PDF)  # 412, then the bytes compare equal
    assert fake.objects[key] == (DATA, PDF)
    assert len(fake.methods("PUT")) == 1


def test_an_endpoint_that_ignores_if_none_match_still_refuses_a_later_conflict(key_of):
    fake = FakeS3(honour_if_none_match=False)
    s3 = _fake_store(fake)
    key = key_of(DATA)
    s3.put(key, DATA, PDF)
    with pytest.raises(BlobConflictError):
        s3.put(key, DATA[:-1] + b"X", PDF)  # same size: refused by the byte comparison
    assert fake.objects[key] == (DATA, PDF)
    assert len(fake.methods("PUT")) == 1


def test_a_reupload_of_equal_bytes_uploads_nothing(s3, fake, key_of):
    key = key_of(DATA)
    s3.put(key, DATA, PDF)
    s3.put(key, DATA, PDF)
    assert len(fake.methods("PUT")) == 1


def test_a_different_size_is_a_conflict_without_downloading(s3, fake, key_of):
    key = key_of(DATA)
    s3.put(key, DATA, PDF)
    with pytest.raises(BlobConflictError):
        s3.put(key, DATA + b"more", PDF)
    assert fake.methods("GET") == []


def test_a_conditional_request_conflict_is_tried_again(s3, fake, key_of):
    key = key_of(DATA)
    fake.inject("PUT", 409, "ConditionalRequestConflict")
    s3.put(key, DATA, PDF)
    assert fake.objects[key] == (DATA, PDF)


def test_put_gives_up_on_a_key_that_keeps_changing(s3, fake, key_of):
    fake.inject("PUT", 412, "PreconditionFailed", times=PUT_ROUNDS)
    with pytest.raises(BlobStoreError, match="kept changing"):
        s3.put(key_of(DATA), DATA, PDF)
    assert len(fake.methods("PUT")) == PUT_ROUNDS


@pytest.fixture
def no_backoff(monkeypatch):
    """botocore sleeps between retries; the tests count attempts, they need not wait."""
    monkeypatch.setattr("botocore.retries.standard.ExponentialBackoff.delay_amount", lambda *_: 0)


def test_a_put_whose_response_is_lost_is_retried_and_compares_equal(s3, fake, key_of, no_backoff):
    """The realistic route to a 412: the server stores the object, the response is lost,
    botocore retries the PUT with If-None-Match, gets 412, and put compares equal bytes."""
    key = key_of(DATA)
    real_put = fake._put
    calls = {"n": 0}

    def put_then_lose_the_response(request, key, headers):
        calls["n"] += 1
        response = real_put(request, key, headers)
        if calls["n"] == 1:
            raise ConnectionClosedError(endpoint_url=request.url)
        return response

    fake._put = put_then_lose_the_response
    s3.put(key, DATA, PDF)

    assert len(fake.methods("PUT")) == 2
    assert fake.objects == {key: (DATA, PDF)}


def test_transient_server_errors_are_retried(s3, fake, key_of, no_backoff):
    key = key_of(DATA)
    fake.objects[key] = (DATA, PDF)
    fake.inject("GET", 503, "SlowDown", times=MAX_ATTEMPTS - 1)
    assert s3.get(key) == DATA
    assert len(fake.methods("GET")) == MAX_ATTEMPTS


def test_retries_stop_after_max_attempts(s3, fake, key_of, no_backoff):
    fake.inject("PUT", 500, "InternalError", times=MAX_ATTEMPTS)
    with pytest.raises(ClientError, match="InternalError"):
        s3.put(key_of(DATA), DATA, PDF)
    assert len(fake.methods("PUT")) == MAX_ATTEMPTS


def test_access_denied_is_an_error_not_a_missing_blob(s3, fake, key_of):
    key = key_of(DATA)
    fake.inject("HEAD", 403, "AccessDenied")
    with pytest.raises(ClientError):
        s3.exists(key)
    fake.inject("GET", 403, "AccessDenied")
    with pytest.raises(ClientError) as raised:
        s3.get(key)
    assert not isinstance(raised.value, BlobNotFoundError)
    fake.inject("HEAD", 403, "AccessDenied")
    with pytest.raises(ClientError):
        s3.put(key, DATA, PDF)
    assert fake.methods("PUT") == []


def test_a_missing_bucket_is_an_error_not_a_missing_blob(fake, key_of):
    s3 = _fake_store(fake, bucket="no-such-bucket")
    with pytest.raises(ClientError, match="NoSuchBucket"):
        s3.get(key_of(DATA))
    with pytest.raises(ClientError, match="NoSuchBucket"):
        s3.put(key_of(DATA), DATA, PDF)


def test_malformed_keys_never_reach_the_server(s3, fake):
    for key in ("", "../x", "a//b", "a" * 1025):
        with pytest.raises(InvalidBlobKeyError):
            s3.put(key, DATA, PDF)
    assert fake.requests == []


def test_presigned_get_url_is_short_lived_and_signed_locally(s3, fake, key_of):
    key = key_of(DATA)
    url = urlsplit(s3.presigned_get_url(key))
    query = parse_qs(url.query)
    assert unquote(url.path).endswith(key)
    assert query["X-Amz-Expires"] == ["60"]
    assert query["X-Amz-Algorithm"] == ["AWS4-HMAC-SHA256"]
    assert fake.requests == []
    for ttl in (0, 3601, True):
        with pytest.raises(ValueError):
            s3.presigned_get_url(key, ttl=ttl)


def _settings(**overrides) -> Settings:
    values = {
        "database_url": "postgresql+psycopg://hausbuch_app@localhost/hausbuch",
        "iban_hmac_key": bytes([0x0F] * 32),
        "session_key": bytes([0x1E] * 32),
        "rp_id": "hausbuch.example",
        "origin": "https://hausbuch.example",
        "blob_store": "s3",
        "s3_bucket": "hausbuch-originals",
        "s3_endpoint_url": ENDPOINT,
        "s3_region": REGION,
    }
    return Settings(**(values | overrides))


def test_from_settings_builds_a_client_with_timeouts_and_retries():
    s3 = S3BlobStore.from_settings(_settings(), session=_session())
    meta = s3.client.meta
    assert s3.bucket == "hausbuch-originals"
    assert (meta.endpoint_url, meta.region_name) == (ENDPOINT, REGION)
    assert (meta.config.connect_timeout, meta.config.read_timeout) == (
        CONNECT_TIMEOUT_S,
        READ_TIMEOUT_S,
    )
    assert meta.config.retries == {"total_max_attempts": MAX_ATTEMPTS, "mode": "standard"}
    assert meta.config.request_checksum_calculation == "when_required"


def test_from_settings_refuses_a_local_store(tmp_path):
    with pytest.raises(SettingsError):
        S3BlobStore.from_settings(_settings(blob_store="local", blob_root=tmp_path, s3_bucket=None))


# --- Real bucket only (Q16) ------------------------------------------------------


def test_real_bucket_honours_if_none_match(real_s3, key_of):
    """Q16. Writes different bytes over an existing key directly, with If-None-Match: *."""
    key = key_of(DATA)
    real_s3.put(key, DATA, PDF)
    try:
        real_s3.client.put_object(
            Bucket=real_s3.bucket, Key=key, Body=b"overwrite", IfNoneMatch="*"
        )
    except ClientError as exc:
        assert exc.response["Error"]["Code"] == "PreconditionFailed"
        assert real_s3.get(key) == DATA
        return
    pytest.xfail(
        "Q16: the endpoint ignores If-None-Match: * and overwrote the object; S3BlobStore "
        "falls back to head-then-put (a small race window). Record this in deploy/scaleway.md."
    )


def test_real_bucket_objects_are_private(real_s3, key_of):
    key = key_of(DATA)
    real_s3.put(key, DATA, PDF)
    grants = real_s3.client.get_object_acl(Bucket=real_s3.bucket, Key=key)["Grants"]
    public = [g for g in grants if "URI" in g.get("Grantee", {})]  # AllUsers, AuthenticatedUsers
    assert public == []
