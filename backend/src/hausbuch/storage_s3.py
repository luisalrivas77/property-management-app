"""S3BlobStore: document originals in S3-compatible object storage (Scaleway in production).

Same contract as LocalBlobStore (hausbuch.storage, principle 6): a key, once written, always
holds the same bytes. put is a no-op for equal bytes, raises BlobConflictError for different
ones, and never overwrites. Spec: docs/phases/phase-1-step-3.md §8, Q16.

How put keeps that promise
    1. HEAD the key. If it exists, compare: a different size is a conflict at once, otherwise
       GET and compare the bytes. Equal bytes: done, nothing is uploaded again.
    2. Otherwise PUT with `If-None-Match: *`. A server that honours it refuses the write with
       412 when another writer got there between our HEAD and our PUT; put then goes back to 1
       and compares bytes, so the loser of a race never overwrites the winner. The same holds
       for a botocore retry after a lost response: the retry gets 412, compares equal bytes,
       and succeeds.
    A server that ignores the header degrades this to head-then-put: sequential writes are
    still refused, but two writers racing on one key can both write. The window runs from our
    HEAD's 404 until our PUT completes, i.e. the whole upload (up to READ_TIMEOUT_S on a slow
    link), not milliseconds.
    Content addressing makes that race harmless in practice (equal keys mean equal SHA-256,
    so honest writers send equal bytes), and read_original's SHA-256 check catches the rest.
    Whether Scaleway honours the header is Q16: run this module's tests against a real bucket
    (tests/test_storage_s3.py, HAUSBUCH_TEST_S3_BUCKET) and record the result.

Integrity
    put sends Content-MD5, so the server rejects bytes damaged in transit. get verifies
    nothing itself: hausbuch.services.documents.read_original checks size and SHA-256 of every
    original it serves, so a check here would only repeat it.

Scaleway bucket configuration (one-time, by hand; deploy/scaleway.md)
    - Region: an EU region, the same one as the database (fr-par, nl-ams or pl-waw).
      Endpoint URL: https://s3.<region>.scw.cloud, e.g. HAUSBUCH_S3_ENDPOINT_URL=
      https://s3.fr-par.scw.cloud and HAUSBUCH_S3_REGION=fr-par.
    - Private: no public ACL, no bucket policy that grants anonymous read. This store never
      sends an ACL, so every object inherits the bucket's private default. Browsers get bytes
      through the authenticated API (or, later, a 60-second presigned URL).
    - Encryption at rest on the bucket side (§14); the store sends no SSE headers.
    - Credentials: an IAM application whose API key can only reach this bucket, with read,
      write and delete on objects and list on the bucket. Without list permission S3 answers a
      missing key with 403 instead of 404, and get/exists would raise instead of reporting it
      missing. The key reaches boto3 through the SDK's standard chain (AWS_ACCESS_KEY_ID /
      AWS_SECRET_ACCESS_KEY from Secret Manager), never through HAUSBUCH_* settings.
    - Versioning: optional. On, it keeps an old version if an overwrite ever slipped through
      (only possible where If-None-Match is ignored); but delete then only adds a delete
      marker, so a retention delete must also remove the object's versions. Phase 1 never
      deletes.
    - Lifecycle rules: off. No expiration (retention is the app's decision, never a timer's)
      and no transition to Glacier (an archived object cannot be read until restored).
    - Object Lock: off, so retention deletes stay possible.
"""

import base64
import hashlib
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from hausbuch.settings import Settings, SettingsError
from hausbuch.storage import (
    BlobConflictError,
    BlobNotFoundError,
    BlobStoreError,
    InvalidBlobKeyError,
)

# Network behaviour. Connect fails fast; reads allow a 25 MiB upload on a slow link. botocore's
# "standard" retry mode retries throttling, 5xx and connection errors with backoff; a retried
# conditional PUT is safe (see the module docstring).
CONNECT_TIMEOUT_S = 5
READ_TIMEOUT_S = 60
MAX_ATTEMPTS = 4  # per request, the first try included
# Rounds of HEAD/PUT in put before giving up on a key that keeps changing under it (412 on a
# key that is gone by the next HEAD, or 409 ConditionalRequestConflict from AWS).
PUT_ROUNDS = 3
MAX_PRESIGNED_TTL_S = 3600
_MAX_KEY_BYTES = 1024  # S3's limit

_NOT_FOUND = frozenset({"404", "NoSuchKey", "NotFound"})  # never NoSuchBucket: that must raise
_PUT_LOST_RACE = frozenset({"PreconditionFailed", "ConditionalRequestConflict"})


def client_config() -> Config:
    return Config(
        connect_timeout=CONNECT_TIMEOUT_S,
        read_timeout=READ_TIMEOUT_S,
        retries={"total_max_attempts": MAX_ATTEMPTS, "mode": "standard"},
        signature_version="s3v4",
        # botocore's default CRC32 trailer (aws-chunked uploads) is not understood by every
        # S3-compatible endpoint; send checksums only where an operation requires them. put
        # sends Content-MD5 itself, which every S3 implementation verifies.
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
    )


def make_s3_client(
    endpoint_url: str | None,
    region: str | None,
    *,
    session: boto3.session.Session | None = None,
) -> Any:
    """A boto3 S3 client with this module's timeouts and retries. Credentials come from the
    session (default: the SDK's standard chain)."""
    session = session or boto3.session.Session()
    return session.client(
        "s3", endpoint_url=endpoint_url, region_name=region, config=client_config()
    )


class S3BlobStore:
    """Blobs as private objects in one bucket, keyed by blob_key(...). Thread-safe: boto3
    clients are, and the store holds no other state."""

    def __init__(self, bucket: str, client: Any) -> None:
        if not bucket:
            raise ValueError("bucket must not be empty")
        self.bucket = bucket
        self.client = client

    @classmethod
    def from_settings(
        cls, settings: Settings, *, session: boto3.session.Session | None = None
    ) -> "S3BlobStore":
        """The production store: HAUSBUCH_S3_BUCKET, HAUSBUCH_S3_ENDPOINT_URL,
        HAUSBUCH_S3_REGION (§8). Builds the client; makes no network call."""
        if settings.blob_store != "s3" or not settings.s3_bucket:
            raise SettingsError("S3BlobStore needs HAUSBUCH_BLOB_STORE=s3 and HAUSBUCH_S3_BUCKET")
        client = make_s3_client(settings.s3_endpoint_url, settings.s3_region, session=session)
        return cls(settings.s3_bucket, client)

    def put(self, key: str, data: bytes, media_type: str) -> None:
        _check_key(key)
        content_md5 = base64.b64encode(hashlib.md5(data, usedforsecurity=False).digest()).decode()
        for _ in range(PUT_ROUNDS):
            size = self._size(key)
            if size is not None:
                try:
                    self._check_same(key, data, size)
                    return
                except BlobNotFoundError:
                    continue  # deleted between HEAD and GET (retention): start over
            try:
                self.client.put_object(
                    Bucket=self.bucket,
                    Key=key,
                    Body=data,
                    ContentType=media_type,
                    ContentMD5=content_md5,
                    IfNoneMatch="*",  # never overwrite: 412 if the key exists by now
                )
                return
            except ClientError as exc:
                if _code(exc) not in _PUT_LOST_RACE:
                    raise
                # Another writer won between our HEAD and our PUT: compare with what it wrote.
        raise BlobStoreError(f"{key!r} kept changing during put; gave up after {PUT_ROUNDS} rounds")

    def get(self, key: str) -> bytes:
        _check_key(key)
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _code(exc) in _NOT_FOUND:
                raise BlobNotFoundError(f"no blob under {key!r}") from None
            raise
        body = response["Body"]
        try:
            return body.read()
        finally:
            body.close()

    def exists(self, key: str) -> bool:
        """False for a missing key, and also for a missing or misnamed bucket: S3 answers a
        HEAD with a bodiless 404 either way. put, get and delete raise NoSuchBucket instead,
        so no write or read can mistake a missing bucket for a missing original."""
        _check_key(key)
        return self._size(key) is not None

    def delete(self, key: str) -> None:
        _check_key(key)
        self.client.delete_object(Bucket=self.bucket, Key=key)  # 204 for a missing key too

    def presigned_get_url(self, key: str, ttl: int = 60) -> str:
        """A short-lived GET URL for the download endpoint's future 302 (§8, Q7). Signed
        locally; makes no network call and does not check that the key exists."""
        _check_key(key)
        if isinstance(ttl, bool) or not isinstance(ttl, int) or not 1 <= ttl <= MAX_PRESIGNED_TTL_S:
            raise ValueError(f"ttl must be 1 to {MAX_PRESIGNED_TTL_S} seconds")
        return self.client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=ttl
        )

    def _size(self, key: str) -> int | None:
        """The stored object's size, or None if there is none. A HEAD 404 cannot tell a
        missing key from a missing bucket; put then fails loudly on NoSuchBucket."""
        try:
            response = self.client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as exc:
            if _code(exc) in _NOT_FOUND:
                return None
            raise
        return response["ContentLength"]

    def _check_same(self, key: str, data: bytes, size: int) -> None:
        if size != len(data) or self.get(key) != data:
            raise BlobConflictError(f"{key!r} holds different bytes; originals are immutable")


def _code(exc: ClientError) -> str:
    return str(exc.response.get("Error", {}).get("Code", ""))


def _check_key(key: str) -> None:
    """The keys LocalBlobStore accepts, limited to ASCII (blob_key always is) and S3's
    1024-byte limit. "." and ".." segments are refused even though S3 keys are opaque:
    proxies and SDKs may normalise them in URLs."""
    if (
        not isinstance(key, str)
        or not key
        or not key.isascii()
        or "\x00" in key
        or "\\" in key
        or key.startswith("/")
        or any(part in ("", ".", "..") for part in key.split("/"))
        or len(key) > _MAX_KEY_BYTES
    ):
        raise InvalidBlobKeyError(f"invalid blob key {key!r}")
