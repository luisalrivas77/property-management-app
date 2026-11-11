"""Runtime settings, read from the environment by an explicit load_settings() call.

Nothing is read at import time, so importing hausbuch never depends on the environment.
Names and defaults: docs/phases/phase-1-step-3.md (§3.3, §3.7, §4.7, §6.1, §6.3, §6.4, §8), for
the AI layer docs/phases/phase-2.md §4.5, and for the bank sync, email-in and reminder mail
docs/phases/phase-5.md §4.1, §5.2 and §6.4 (D118, D119, D121, D123): all optional, each feature
off while its values are absent.

Settings validates itself on construction, so values built directly (tests, the CLI) get the
same checks as values read from the environment.
"""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

MIN_IBAN_HMAC_KEY_BYTES = 32
MIN_SESSION_KEY_BYTES = 32

BlobStoreKind = Literal["local", "s3"]
BLOB_STORES: tuple[BlobStoreKind, ...] = ("local", "s3")

DEFAULT_MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # 25 MiB (step 3 Q13: may be lowered on deploy)
# §4.7: what POST /documents accepts (Q8). Anything else is 415.
DEFAULT_UPLOAD_MEDIA_TYPES = frozenset(
    {
        "application/pdf",
        "image/jpeg",
        "image/png",
        "image/heic",
        "image/webp",
        "text/csv",
        "text/plain",
        "application/xml",
        "text/xml",
        "message/rfc822",
        # A Sparkasse camt export is a ZIP of daily reports (phase 2 D34).
        "application/zip",
        "application/x-zip-compressed",
    }
)
DEFAULT_SESSION_MAX_AGE_S = 12 * 3600  # absolute lifetime (§6.4, Q2)
DEFAULT_SESSION_IDLE_S = 2 * 3600  # idle lifetime, slid forward on use
DEFAULT_WORKER_CONCURRENCY = 2
DEFAULT_SHUTDOWN_GRACE_S = 20  # below uvicorn's --timeout-graceful-shutdown 25 (§3.7, Q15)

# Phase 2 AI layer (phase-2.md §4.5, D17). Model ids are settings so a tier can change without
# a release; inference_geo is `us` (Luis, 6 Nov 2026: one named processing location).
DEFAULT_AI_MODEL_CLASSIFY = "claude-haiku-5-5"
DEFAULT_AI_MODEL_EXTRACT = "claude-sonnet-5-5"
DEFAULT_AI_MODEL_HARD = "claude-opus-5-5"
InferenceGeo = Literal["us", "global"]
INFERENCE_GEOS: tuple[InferenceGeo, ...] = ("us", "global")
DEFAULT_AI_INFERENCE_GEO: InferenceGeo = "us"
DEFAULT_AI_MAX_PAGES = 100  # pages per request; longer PDFs are split (§5.2)
MAX_AI_PAGES = 600  # the API's limit per request with a 1M context window
DEFAULT_AI_TIMEOUT_S = 120

# Phase 5 (phase-5.md §4.1, §5.2, §6.4).
DEFAULT_SYNC_APPROVAL_TIMEOUT_S = 300  # D118: wait at most 5 minutes for a pushTAN approval
DEFAULT_INBOX_IMAP_HOST = "imap.gmail.com"  # D121
DEFAULT_INBOX_AUTHSERV_ID = "mx.google.com"  # D121: the only trusted Authentication-Results
DEFAULT_TEM_REGION = "fr-par"  # D123: Scaleway Transactional Email

# The document_file.media_type CHECK.
_MEDIA_TYPE = re.compile(r"[a-z]+/[a-z0-9.+-]+")
_LABEL = r"[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?"
_HOST = re.compile(rf"{_LABEL}(\.{_LABEL})*")
_REGION = re.compile(r"[a-z]{2}-[a-z]{3}")  # a Scaleway region (fr-par), part of the TEM URL


class SettingsError(ValueError):
    """A required setting is missing or malformed."""


@dataclass(frozen=True)
class Settings:
    # Secrets (the URL may carry a password): none of them appears in repr().
    database_url: str = field(repr=False)
    iban_hmac_key: bytes = field(repr=False)
    session_key: bytes = field(repr=False)  # HMAC key of the ceremony cookie (§6.3)
    # WebAuthn relying party (§6.1): the web host, and the browser origin the API sits behind.
    rp_id: str
    origin: str
    blob_store: BlobStoreKind
    cookie_secure: bool = True  # false only for http://localhost (drops the __Host- prefix)
    blob_root: Path | None = None  # blob_store = "local" (dev and tests only)
    s3_bucket: str | None = None  # blob_store = "s3"; credentials via the SDK's chain
    s3_endpoint_url: str | None = None
    s3_region: str | None = None
    max_upload_bytes: int = DEFAULT_MAX_UPLOAD_BYTES
    upload_media_types: frozenset[str] = DEFAULT_UPLOAD_MEDIA_TYPES
    session_max_age_s: int = DEFAULT_SESSION_MAX_AGE_S
    session_idle_s: int = DEFAULT_SESSION_IDLE_S
    run_worker: bool = True  # the in-process job worker (§3.7)
    worker_concurrency: int = DEFAULT_WORKER_CONCURRENCY
    shutdown_grace_s: int = DEFAULT_SHUTDOWN_GRACE_S
    # AI layer (phase 2). Off by default: production turns it on with the key.
    ai_enabled: bool = False
    anthropic_api_key: str | None = field(default=None, repr=False)
    ai_model_classify: str = DEFAULT_AI_MODEL_CLASSIFY
    ai_model_extract: str = DEFAULT_AI_MODEL_EXTRACT
    ai_model_hard: str = DEFAULT_AI_MODEL_HARD
    ai_inference_geo: InferenceGeo = DEFAULT_AI_INFERENCE_GEO
    ai_max_pages: int = DEFAULT_AI_MAX_PAGES
    ai_timeout_s: int = DEFAULT_AI_TIMEOUT_S
    eval_dir: Path | None = None  # Luis's real eval documents, outside the repo (D29)
    # Phase 5 FinTS sync (D118, D119): container secrets, never a column, payload or log line.
    fints_pin: str | None = field(default=None, repr=False)
    fints_product_id: str | None = field(default=None, repr=False)  # the DK registration
    sync_approval_timeout_s: int = DEFAULT_SYNC_APPROVAL_TIMEOUT_S
    # Phase 5 email-in (D121): the Gmail account, its app password and the token address are
    # personal or secret; the label and the hosts are not.
    inbox_imap_user: str | None = field(default=None, repr=False)
    inbox_imap_password: str | None = field(default=None, repr=False)
    inbox_address: str | None = field(default=None, repr=False)  # <user>+<token>@gmail.com
    inbox_label: str | None = None
    inbox_imap_host: str = DEFAULT_INBOX_IMAP_HOST
    inbox_authserv_id: str = DEFAULT_INBOX_AUTHSERV_ID
    # Phase 5 reminder mail (D123): Scaleway Transactional Email.
    tem_secret_key: str | None = field(default=None, repr=False)
    tem_project_id: str | None = None
    tem_region: str = DEFAULT_TEM_REGION
    mail_from: str | None = None  # the sending address on the verified domain

    def __post_init__(self) -> None:
        if not self.database_url:
            raise SettingsError("HAUSBUCH_DATABASE_URL is not set")
        _check_key("HAUSBUCH_IBAN_HMAC_KEY", self.iban_hmac_key, MIN_IBAN_HMAC_KEY_BYTES)
        _check_key("HAUSBUCH_SESSION_KEY", self.session_key, MIN_SESSION_KEY_BYTES)
        if self.session_key == self.iban_hmac_key:
            raise SettingsError("HAUSBUCH_SESSION_KEY must differ from HAUSBUCH_IBAN_HMAC_KEY")
        self._check_relying_party()
        self._check_blob_store()
        if not self.upload_media_types or not all(
            isinstance(t, str) and _MEDIA_TYPE.fullmatch(t) for t in self.upload_media_types
        ):
            raise SettingsError(
                "HAUSBUCH_UPLOAD_MEDIA_TYPES must be a non-empty list of lower-case media "
                "types such as application/pdf"
            )
        for name, value in (
            ("HAUSBUCH_MAX_UPLOAD_BYTES", self.max_upload_bytes),
            ("HAUSBUCH_SESSION_MAX_AGE_S", self.session_max_age_s),
            ("HAUSBUCH_SESSION_IDLE_S", self.session_idle_s),
            ("HAUSBUCH_WORKER_CONCURRENCY", self.worker_concurrency),
            ("HAUSBUCH_SHUTDOWN_GRACE_S", self.shutdown_grace_s),
            ("HAUSBUCH_AI_MAX_PAGES", self.ai_max_pages),
            ("HAUSBUCH_AI_TIMEOUT_S", self.ai_timeout_s),
            ("HAUSBUCH_SYNC_APPROVAL_TIMEOUT_S", self.sync_approval_timeout_s),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise SettingsError(f"{name} must be a positive integer")
        if self.session_idle_s > self.session_max_age_s:
            raise SettingsError(
                "HAUSBUCH_SESSION_IDLE_S must not exceed HAUSBUCH_SESSION_MAX_AGE_S"
            )
        self._check_ai()
        self._check_automation()

    def _check_automation(self) -> None:
        """Phase 5 (§9): each secret comes with its partner, or neither is set. The messages
        name the variables, never their values."""
        if (self.fints_pin is None) != (self.fints_product_id is None):
            raise SettingsError(
                "HAUSBUCH_FINTS_PIN and HAUSBUCH_FINTS_PRODUCT_ID are set together or not at all"
            )
        if (self.inbox_imap_user is None) != (self.inbox_imap_password is None):
            raise SettingsError(
                "HAUSBUCH_INBOX_IMAP_USER and HAUSBUCH_INBOX_IMAP_PASSWORD are set together or "
                "not at all"
            )
        if self.inbox_imap_user is not None and (
            self.inbox_address is None or self.inbox_label is None
        ):
            raise SettingsError(
                "email-in (HAUSBUCH_INBOX_IMAP_USER) needs HAUSBUCH_INBOX_ADDRESS and "
                "HAUSBUCH_INBOX_LABEL"
            )
        for name, host in (
            ("HAUSBUCH_INBOX_IMAP_HOST", self.inbox_imap_host),
            ("HAUSBUCH_INBOX_AUTHSERV_ID", self.inbox_authserv_id),
        ):
            if not _HOST.fullmatch(host):
                raise SettingsError(f"{name} must be a lower-case host name")
        if self.tem_secret_key is not None and (
            self.tem_project_id is None or self.mail_from is None
        ):
            raise SettingsError(
                "HAUSBUCH_TEM_SECRET_KEY needs HAUSBUCH_TEM_PROJECT_ID and HAUSBUCH_MAIL_FROM"
            )
        if not _REGION.fullmatch(self.tem_region):
            raise SettingsError("HAUSBUCH_TEM_REGION must be a Scaleway region such as fr-par")

    def _check_ai(self) -> None:
        if self.ai_enabled and not self.anthropic_api_key:
            raise SettingsError("HAUSBUCH_AI_ENABLED=true needs HAUSBUCH_ANTHROPIC_API_KEY")
        if self.ai_inference_geo not in INFERENCE_GEOS:
            raise SettingsError(
                f"HAUSBUCH_AI_INFERENCE_GEO must be one of {', '.join(INFERENCE_GEOS)}"
            )
        if self.ai_max_pages > MAX_AI_PAGES:
            raise SettingsError(f"HAUSBUCH_AI_MAX_PAGES must not exceed {MAX_AI_PAGES}")
        for name, model in (
            ("HAUSBUCH_AI_MODEL_CLASSIFY", self.ai_model_classify),
            ("HAUSBUCH_AI_MODEL_EXTRACT", self.ai_model_extract),
            ("HAUSBUCH_AI_MODEL_HARD", self.ai_model_hard),
        ):
            if not model or model != model.strip():
                raise SettingsError(f"{name} must be a model id")

    def _check_relying_party(self) -> None:
        # WebAuthn needs a domain: an IP address is not a valid RP id.
        if not _HOST.fullmatch(self.rp_id) or re.fullmatch(r"[0-9.]+", self.rp_id):
            raise SettingsError(
                "HAUSBUCH_RP_ID must be a lower-case host name without scheme or port, "
                "e.g. hausbuch.example or localhost"
            )
        parts = urlsplit(self.origin)
        try:
            parts.port  # noqa: B018 - raises ValueError on a malformed port
        except ValueError:
            raise SettingsError("HAUSBUCH_ORIGIN has a malformed port") from None
        host = parts.hostname or ""
        if (
            parts.scheme not in ("https", "http")
            or not _HOST.fullmatch(host)
            or parts.netloc.lower() != parts.netloc
            or "@" in parts.netloc
            or parts.path
            or parts.query
            or parts.fragment
        ):
            raise SettingsError(
                "HAUSBUCH_ORIGIN must be scheme://host[:port] in lower case, with no path or "
                "trailing slash, e.g. https://hausbuch.example"
            )
        if parts.scheme == "http" and host != "localhost":
            raise SettingsError("HAUSBUCH_ORIGIN may use plain http only for localhost")
        if host != self.rp_id and not host.endswith("." + self.rp_id):
            raise SettingsError(
                "HAUSBUCH_RP_ID must be the host of HAUSBUCH_ORIGIN or a parent domain of it"
            )
        if not self.cookie_secure and parts.scheme == "https":
            raise SettingsError(
                "HAUSBUCH_COOKIE_SECURE=false is only for an http://localhost origin"
            )

    def _check_blob_store(self) -> None:
        if self.blob_store not in BLOB_STORES:
            raise SettingsError(f"HAUSBUCH_BLOB_STORE must be one of {', '.join(BLOB_STORES)}")
        if self.blob_store == "local" and self.blob_root is None:
            raise SettingsError("HAUSBUCH_BLOB_STORE=local needs HAUSBUCH_BLOB_ROOT")
        if self.blob_store == "s3" and not self.s3_bucket:
            raise SettingsError("HAUSBUCH_BLOB_STORE=s3 needs HAUSBUCH_S3_BUCKET")
        if self.s3_endpoint_url is not None:
            endpoint = urlsplit(self.s3_endpoint_url)
            if endpoint.scheme not in ("https", "http") or not endpoint.hostname:
                raise SettingsError("HAUSBUCH_S3_ENDPOINT_URL must be an http(s) URL")


def load_settings(environ: Mapping[str, str] = os.environ) -> Settings:
    """The API's settings. Required: HAUSBUCH_DATABASE_URL (the app role), the two keys, the
    relying party (HAUSBUCH_RP_ID, HAUSBUCH_ORIGIN) and HAUSBUCH_BLOB_STORE, which has no
    default because a container has no persistent disk: production must say s3 explicitly."""
    blob_store = _required(environ, "HAUSBUCH_BLOB_STORE")
    blob_root = _optional(environ, "HAUSBUCH_BLOB_ROOT")
    media_types = _optional(environ, "HAUSBUCH_UPLOAD_MEDIA_TYPES")
    eval_dir = _optional(environ, "HAUSBUCH_EVAL_DIR")
    return Settings(
        database_url=_required(environ, "HAUSBUCH_DATABASE_URL"),
        iban_hmac_key=_hex_key(environ, "HAUSBUCH_IBAN_HMAC_KEY"),
        session_key=_hex_key(environ, "HAUSBUCH_SESSION_KEY"),
        rp_id=_required(environ, "HAUSBUCH_RP_ID"),
        origin=_required(environ, "HAUSBUCH_ORIGIN"),
        blob_store=blob_store,  # type: ignore[arg-type]  # checked by Settings
        cookie_secure=_bool(environ, "HAUSBUCH_COOKIE_SECURE", True),
        blob_root=None if blob_root is None else Path(blob_root),
        s3_bucket=_optional(environ, "HAUSBUCH_S3_BUCKET"),
        s3_endpoint_url=_optional(environ, "HAUSBUCH_S3_ENDPOINT_URL"),
        s3_region=_optional(environ, "HAUSBUCH_S3_REGION"),
        max_upload_bytes=_int(environ, "HAUSBUCH_MAX_UPLOAD_BYTES", DEFAULT_MAX_UPLOAD_BYTES),
        upload_media_types=(
            DEFAULT_UPLOAD_MEDIA_TYPES
            if media_types is None
            else frozenset(t.strip() for t in media_types.split(",") if t.strip())
        ),
        session_max_age_s=_int(environ, "HAUSBUCH_SESSION_MAX_AGE_S", DEFAULT_SESSION_MAX_AGE_S),
        session_idle_s=_int(environ, "HAUSBUCH_SESSION_IDLE_S", DEFAULT_SESSION_IDLE_S),
        run_worker=_bool(environ, "HAUSBUCH_RUN_WORKER", True),
        worker_concurrency=_int(environ, "HAUSBUCH_WORKER_CONCURRENCY", DEFAULT_WORKER_CONCURRENCY),
        shutdown_grace_s=_int(environ, "HAUSBUCH_SHUTDOWN_GRACE_S", DEFAULT_SHUTDOWN_GRACE_S),
        ai_enabled=_bool(environ, "HAUSBUCH_AI_ENABLED", False),
        anthropic_api_key=_optional(environ, "HAUSBUCH_ANTHROPIC_API_KEY"),
        ai_model_classify=_optional(environ, "HAUSBUCH_AI_MODEL_CLASSIFY")
        or DEFAULT_AI_MODEL_CLASSIFY,
        ai_model_extract=_optional(environ, "HAUSBUCH_AI_MODEL_EXTRACT")
        or DEFAULT_AI_MODEL_EXTRACT,
        ai_model_hard=_optional(environ, "HAUSBUCH_AI_MODEL_HARD") or DEFAULT_AI_MODEL_HARD,
        ai_inference_geo=(
            _optional(environ, "HAUSBUCH_AI_INFERENCE_GEO") or DEFAULT_AI_INFERENCE_GEO
        ),  # type: ignore[arg-type]  # checked by Settings
        ai_max_pages=_int(environ, "HAUSBUCH_AI_MAX_PAGES", DEFAULT_AI_MAX_PAGES),
        ai_timeout_s=_int(environ, "HAUSBUCH_AI_TIMEOUT_S", DEFAULT_AI_TIMEOUT_S),
        eval_dir=None if eval_dir is None else Path(eval_dir),
        fints_pin=_optional(environ, "HAUSBUCH_FINTS_PIN"),
        fints_product_id=_optional(environ, "HAUSBUCH_FINTS_PRODUCT_ID"),
        sync_approval_timeout_s=_int(
            environ, "HAUSBUCH_SYNC_APPROVAL_TIMEOUT_S", DEFAULT_SYNC_APPROVAL_TIMEOUT_S
        ),
        inbox_imap_user=_optional(environ, "HAUSBUCH_INBOX_IMAP_USER"),
        inbox_imap_password=_optional(environ, "HAUSBUCH_INBOX_IMAP_PASSWORD"),
        inbox_address=_optional(environ, "HAUSBUCH_INBOX_ADDRESS"),
        inbox_label=_optional(environ, "HAUSBUCH_INBOX_LABEL"),
        inbox_imap_host=_optional(environ, "HAUSBUCH_INBOX_IMAP_HOST") or DEFAULT_INBOX_IMAP_HOST,
        inbox_authserv_id=(
            _optional(environ, "HAUSBUCH_INBOX_AUTHSERV_ID") or DEFAULT_INBOX_AUTHSERV_ID
        ),
        tem_secret_key=_optional(environ, "HAUSBUCH_TEM_SECRET_KEY"),
        tem_project_id=_optional(environ, "HAUSBUCH_TEM_PROJECT_ID"),
        tem_region=_optional(environ, "HAUSBUCH_TEM_REGION") or DEFAULT_TEM_REGION,
        mail_from=_optional(environ, "HAUSBUCH_MAIL_FROM"),
    )


def load_admin_database_url(environ: Mapping[str, str] = os.environ) -> str:
    """The schema-owner URL for the CLI (bootstrap, invite, migrations) only, never part of
    Settings: the API process must not hold it (§6.5, §14)."""
    return _required(environ, "HAUSBUCH_ADMIN_DATABASE_URL")


# --- Parsing -----------------------------------------------------------------


def _optional(environ: Mapping[str, str], name: str) -> str | None:
    value = environ.get(name, "").strip()
    return value or None


def _required(environ: Mapping[str, str], name: str) -> str:
    value = _optional(environ, name)
    if value is None:
        raise SettingsError(f"{name} is not set")
    return value


def _hex_key(environ: Mapping[str, str], name: str) -> bytes:
    value = _required(environ, name)  # outside the try: SettingsError is a ValueError too
    try:
        return bytes.fromhex(value)
    except ValueError:
        raise SettingsError(f"{name} must be hex-encoded") from None


def _check_key(name: str, key: bytes, minimum: int) -> None:
    if len(key) < minimum:
        raise SettingsError(
            f"{name} must be at least {minimum} bytes ({2 * minimum} hex characters), "
            f"got {len(key)}"
        )


def _bool(environ: Mapping[str, str], name: str, default: bool) -> bool:
    value = _optional(environ, name)
    if value is None:
        return default
    if value.lower() not in ("true", "false"):
        raise SettingsError(f"{name} must be true or false")
    return value.lower() == "true"


def _int(environ: Mapping[str, str], name: str, default: int) -> int:
    value = _optional(environ, name)
    if value is None:
        return default
    if not value.isascii() or not value.isdigit():
        raise SettingsError(f"{name} must be a positive integer")
    return int(value)
