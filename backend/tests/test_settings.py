import re
from pathlib import Path

import pytest

from hausbuch.settings import (
    DEFAULT_UPLOAD_MEDIA_TYPES,
    SettingsError,
    load_admin_database_url,
    load_settings,
)

URL = "postgresql+psycopg://hausbuch_app@localhost/hausbuch"
KEY = "0f" * 32
SESSION_KEY = "1e" * 32
# Every required setting (phase 1 step 3 added the session key, relying party and blob store).
ENV = {
    "HAUSBUCH_DATABASE_URL": URL,
    "HAUSBUCH_IBAN_HMAC_KEY": KEY,
    "HAUSBUCH_SESSION_KEY": SESSION_KEY,
    "HAUSBUCH_RP_ID": "hausbuch.example",
    "HAUSBUCH_ORIGIN": "https://hausbuch.example",
    "HAUSBUCH_BLOB_STORE": "s3",
    "HAUSBUCH_S3_BUCKET": "hausbuch-originals",
}


def test_loads_url_and_decodes_key():
    settings = load_settings(ENV)
    assert settings.database_url == URL
    assert settings.iban_hmac_key == bytes([0x0F] * 32)
    assert settings.session_key == bytes([0x1E] * 32)


def test_defaults():
    settings = load_settings(ENV)
    assert (settings.rp_id, settings.origin) == ("hausbuch.example", "https://hausbuch.example")
    assert settings.cookie_secure is True
    assert (settings.blob_store, settings.s3_bucket, settings.blob_root) == (
        "s3",
        "hausbuch-originals",
        None,
    )
    assert settings.max_upload_bytes == 25 * 1024 * 1024
    assert settings.upload_media_types == DEFAULT_UPLOAD_MEDIA_TYPES
    assert "application/pdf" in settings.upload_media_types
    assert (settings.session_max_age_s, settings.session_idle_s) == (12 * 3600, 2 * 3600)
    assert (settings.run_worker, settings.worker_concurrency, settings.shutdown_grace_s) == (
        True,
        2,
        20,
    )


def test_ai_defaults_from_the_phase_2_spec():
    settings = load_settings(ENV)
    assert settings.ai_enabled is False and settings.anthropic_api_key is None
    assert (settings.ai_model_classify, settings.ai_model_extract, settings.ai_model_hard) == (
        "claude-haiku-5-5",
        "claude-sonnet-5-5",
        "claude-opus-5-5",
    )
    assert settings.ai_inference_geo == "us"
    assert (settings.ai_max_pages, settings.ai_timeout_s, settings.eval_dir) == (100, 120, None)


def test_ai_enabled_with_a_key_and_the_key_stays_out_of_repr():
    api_key = "sk-ant-test-0123456789"
    settings = load_settings(
        ENV
        | {
            "HAUSBUCH_AI_ENABLED": "true",
            "HAUSBUCH_ANTHROPIC_API_KEY": api_key,
            "HAUSBUCH_AI_INFERENCE_GEO": "global",
            "HAUSBUCH_EVAL_DIR": "/srv/evals",
        }
    )
    assert settings.ai_enabled is True and settings.anthropic_api_key == api_key
    assert settings.ai_inference_geo == "global"
    assert settings.eval_dir == Path("/srv/evals")
    assert api_key not in repr(settings)


def test_dev_environment_from_the_step_3_doc():
    settings = load_settings(
        ENV
        | {
            "HAUSBUCH_RP_ID": "localhost",
            "HAUSBUCH_ORIGIN": "http://localhost:3000",
            "HAUSBUCH_COOKIE_SECURE": "false",
            "HAUSBUCH_BLOB_STORE": "local",
            "HAUSBUCH_BLOB_ROOT": "./var/blobs",
            "HAUSBUCH_RUN_WORKER": "false",
            "HAUSBUCH_UPLOAD_MEDIA_TYPES": "application/pdf, image/png",
            "HAUSBUCH_MAX_UPLOAD_BYTES": "1048576",
            "HAUSBUCH_SESSION_MAX_AGE_S": "14400",
            "HAUSBUCH_SESSION_IDLE_S": "1800",
            "HAUSBUCH_WORKER_CONCURRENCY": "1",
            "HAUSBUCH_SHUTDOWN_GRACE_S": "5",
        }
    )
    assert settings.cookie_secure is False and settings.run_worker is False
    assert (settings.blob_store, settings.blob_root) == ("local", Path("./var/blobs"))
    assert settings.upload_media_types == {"application/pdf", "image/png"}
    assert settings.max_upload_bytes == 1048576
    assert (settings.session_max_age_s, settings.session_idle_s) == (14400, 1800)
    assert (settings.worker_concurrency, settings.shutdown_grace_s) == (1, 5)


def test_origin_may_be_a_subdomain_of_the_rp_id():
    settings = load_settings(ENV | {"HAUSBUCH_ORIGIN": "https://app.hausbuch.example:8443"})
    assert settings.origin == "https://app.hausbuch.example:8443"


def test_repr_hides_secrets():
    settings = load_settings(ENV)
    text = repr(settings)
    assert KEY not in text and SESSION_KEY not in text and URL not in text
    assert "hausbuch_app" not in text


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"HAUSBUCH_IBAN_HMAC_KEY": None}, "not set"),
        ({"HAUSBUCH_IBAN_HMAC_KEY": ""}, "not set"),
        ({"HAUSBUCH_IBAN_HMAC_KEY": "0f" * 31}, "at least 32"),
        ({"HAUSBUCH_IBAN_HMAC_KEY": "zz" * 32}, "hex"),
        ({"HAUSBUCH_DATABASE_URL": None}, "HAUSBUCH_DATABASE_URL"),
        ({"HAUSBUCH_SESSION_KEY": None}, "HAUSBUCH_SESSION_KEY is not set"),
        ({"HAUSBUCH_SESSION_KEY": "1e" * 31}, "HAUSBUCH_SESSION_KEY must be at least 32"),
        ({"HAUSBUCH_SESSION_KEY": "x"}, "HAUSBUCH_SESSION_KEY must be hex"),
        ({"HAUSBUCH_SESSION_KEY": KEY}, "must differ"),
        ({"HAUSBUCH_RP_ID": None}, "HAUSBUCH_RP_ID is not set"),
        ({"HAUSBUCH_RP_ID": "https://hausbuch.example"}, "HAUSBUCH_RP_ID must be"),
        ({"HAUSBUCH_RP_ID": "Hausbuch.example"}, "HAUSBUCH_RP_ID must be"),
        ({"HAUSBUCH_RP_ID": "hausbuch.example:443"}, "HAUSBUCH_RP_ID must be"),
        ({"HAUSBUCH_RP_ID": "127.0.0.1"}, "HAUSBUCH_RP_ID must be"),
        ({"HAUSBUCH_ORIGIN": None}, "HAUSBUCH_ORIGIN is not set"),
        ({"HAUSBUCH_ORIGIN": "https://hausbuch.example/"}, "no path"),
        ({"HAUSBUCH_ORIGIN": "https://hausbuch.example/app"}, "no path"),
        ({"HAUSBUCH_ORIGIN": "hausbuch.example"}, "scheme://host"),
        ({"HAUSBUCH_ORIGIN": "ftp://hausbuch.example"}, "scheme://host"),
        ({"HAUSBUCH_ORIGIN": "https://HAUSBUCH.example"}, "lower case"),
        ({"HAUSBUCH_ORIGIN": "https://user@hausbuch.example"}, "scheme://host"),
        ({"HAUSBUCH_ORIGIN": "https://hausbuch.example:x"}, "malformed port"),
        ({"HAUSBUCH_ORIGIN": "http://hausbuch.example"}, "plain http only for localhost"),
        ({"HAUSBUCH_ORIGIN": "https://other.example"}, "host of HAUSBUCH_ORIGIN"),
        ({"HAUSBUCH_ORIGIN": "https://nothausbuch.example"}, "host of HAUSBUCH_ORIGIN"),
        ({"HAUSBUCH_COOKIE_SECURE": "false"}, "only for an http://localhost origin"),
        ({"HAUSBUCH_COOKIE_SECURE": "no"}, "true or false"),
        ({"HAUSBUCH_BLOB_STORE": None}, "HAUSBUCH_BLOB_STORE is not set"),
        ({"HAUSBUCH_BLOB_STORE": "disk"}, "must be one of local, s3"),
        ({"HAUSBUCH_BLOB_STORE": "local"}, "needs HAUSBUCH_BLOB_ROOT"),
        ({"HAUSBUCH_S3_BUCKET": None}, "needs HAUSBUCH_S3_BUCKET"),
        ({"HAUSBUCH_S3_ENDPOINT_URL": "s3.fr-par.scw.cloud"}, "http(s) URL"),
        ({"HAUSBUCH_MAX_UPLOAD_BYTES": "0"}, "positive integer"),
        ({"HAUSBUCH_MAX_UPLOAD_BYTES": "25MiB"}, "positive integer"),
        ({"HAUSBUCH_MAX_UPLOAD_BYTES": "-1"}, "positive integer"),
        ({"HAUSBUCH_UPLOAD_MEDIA_TYPES": " , "}, "non-empty list"),
        ({"HAUSBUCH_UPLOAD_MEDIA_TYPES": "application/pdf,PDF"}, "lower-case media types"),
        ({"HAUSBUCH_SESSION_IDLE_S": "50000"}, "must not exceed"),
        ({"HAUSBUCH_SESSION_MAX_AGE_S": "0"}, "positive integer"),
        ({"HAUSBUCH_WORKER_CONCURRENCY": "0"}, "positive integer"),
        ({"HAUSBUCH_SHUTDOWN_GRACE_S": "2.5"}, "positive integer"),
        ({"HAUSBUCH_RUN_WORKER": "1"}, "true or false"),
        ({"HAUSBUCH_AI_ENABLED": "true"}, "needs HAUSBUCH_ANTHROPIC_API_KEY"),
        ({"HAUSBUCH_AI_ENABLED": "yes"}, "true or false"),
        ({"HAUSBUCH_AI_INFERENCE_GEO": "eu"}, "must be one of us, global"),
        ({"HAUSBUCH_AI_MAX_PAGES": "0"}, "positive integer"),
        ({"HAUSBUCH_AI_MAX_PAGES": "601"}, "must not exceed 600"),
        ({"HAUSBUCH_AI_TIMEOUT_S": "-5"}, "positive integer"),
    ],
    ids=lambda v: str(v) if isinstance(v, str) else None,
)
def test_rejects_bad_environment(changes, message):
    environ = {k: v for k, v in (ENV | changes).items() if v is not None}
    with pytest.raises(SettingsError, match=re.escape(message)):
        load_settings(environ)


def test_admin_url_is_separate_from_the_api_settings():
    owner = "postgresql+psycopg://hausbuch_owner@localhost/hausbuch"
    assert load_admin_database_url({"HAUSBUCH_ADMIN_DATABASE_URL": owner}) == owner
    with pytest.raises(SettingsError, match="HAUSBUCH_ADMIN_DATABASE_URL is not set"):
        load_admin_database_url({})
    settings = load_settings(ENV | {"HAUSBUCH_ADMIN_DATABASE_URL": owner})
    assert owner not in {getattr(settings, f) for f in settings.__dataclass_fields__}


def test_the_s3_blob_store_gets_the_stores_own_client_config():
    """Integration of chunks 2 and 5: main.create_blob_store must build the S3 store through
    S3BlobStore.from_settings, so the client keeps the store's timeouts, retries and the
    Content-MD5-only checksum setting. A plain boto3.client lost all three."""
    from hausbuch.api.main import create_blob_store
    from hausbuch.storage_s3 import (
        CONNECT_TIMEOUT_S,
        MAX_ATTEMPTS,
        READ_TIMEOUT_S,
        S3BlobStore,
    )

    store = create_blob_store(load_settings(ENV))
    assert isinstance(store, S3BlobStore)
    config = store.client.meta.config
    assert (config.connect_timeout, config.read_timeout) == (CONNECT_TIMEOUT_S, READ_TIMEOUT_S)
    assert config.retries["total_max_attempts"] == MAX_ATTEMPTS
    assert config.request_checksum_calculation == "when_required"
