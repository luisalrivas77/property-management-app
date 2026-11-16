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


def test_ai_enabled_without_the_key_variable_is_valid():
    """Phase 6 D260 (this was refused until 16 Nov 2026): the key may be typed in the app."""
    settings = load_settings(ENV | {"HAUSBUCH_AI_ENABLED": "true"})
    assert settings.ai_enabled is True and settings.anthropic_api_key is None


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


def test_phase_5_defaults_leave_every_feature_off():
    """Phase 5 §9: every new variable is optional; without them no sync, poll or mail runs."""
    settings = load_settings(ENV)
    assert (settings.fints_pin, settings.fints_product_id) == (None, None)
    assert settings.sync_approval_timeout_s == 300  # D118: five minutes
    assert (settings.inbox_imap_user, settings.inbox_imap_password) == (None, None)
    assert (settings.inbox_address, settings.inbox_label) == (None, None)
    assert (settings.inbox_imap_host, settings.inbox_authserv_id) == (
        "imap.gmail.com",
        "mx.google.com",
    )
    assert (settings.tem_secret_key, settings.tem_project_id, settings.mail_from) == (
        None,
        None,
        None,
    )
    assert settings.tem_region == "fr-par"


@pytest.mark.parametrize(
    "extra",
    [{}, {"HAUSBUCH_FINTS_PIN_2ND": "12345"}],
    ids=["product_id_alone", "with_an_ignored_variable"],
)
def test_the_product_id_alone_is_valid(extra):
    """Phase 6 D196: the PINs may all be typed in the app (bank_credential), so the container
    starts with the product id and no PIN variable; an ignored name adds no PIN."""
    settings = load_settings(ENV | {"HAUSBUCH_FINTS_PRODUCT_ID": "P1"} | extra)
    assert settings.fints_product_id == "P1" and dict(settings.fints_pins) == {}


@pytest.mark.parametrize(
    "partial",
    [
        {"HAUSBUCH_INBOX_IMAP_USER": "a@mail.example"},
        {"HAUSBUCH_INBOX_IMAP_PASSWORD": "pw"},
        {"HAUSBUCH_INBOX_IMAP_USER": "a@mail.example", "HAUSBUCH_INBOX_IMAP_PASSWORD": "pw"},
        {"HAUSBUCH_TEM_SECRET_KEY": "k"},
    ],
    ids=["imap_user", "imap_password", "imap_pair_without_address", "tem_key"],
)
def test_part_of_the_mail_settings_is_valid_at_start_up(partial):
    """Phase 6 D260 (refused until 16 Nov 2026): the rest may be typed in the app
    (app_secret); the poll and the digest check the workspace's whole set when they run."""
    settings = load_settings(ENV | partial)
    assert settings.inbox_address is None and settings.tem_project_id is None


@pytest.mark.parametrize("pin", ["HAUSBUCH_FINTS_PIN", "HAUSBUCH_FINTS_PIN_OWNER_B"])
def test_a_pin_without_the_product_id_is_valid(pin):
    """Phase 6 D260 (refused until 16 Nov 2026): the product id may be typed in the app
    (app_secret), so the container starts with a PIN and no product id; a sync without one
    from either source fails with bank.not_configured."""
    settings = load_settings(ENV | {pin: "12345"})
    assert settings.fints_product_id is None and len(dict(settings.fints_pins)) == 1


# Synthetic values only (public repo).
PHASE_5_SECRETS = {
    "HAUSBUCH_FINTS_PIN": "pin-24680-synthetic",
    "HAUSBUCH_FINTS_PRODUCT_ID": "PRODUCTID0SYNTHETIC",
    "HAUSBUCH_INBOX_IMAP_USER": "owner.a@mail.example",
    "HAUSBUCH_INBOX_IMAP_PASSWORD": "abcd efgh ijkl mnop",
    "HAUSBUCH_INBOX_ADDRESS": "owner.a+k7f3q2xd@mail.example",
    "HAUSBUCH_TEM_SECRET_KEY": "tem-secret-0123456789",
}


def test_phase_5_values_load_and_the_secret_ones_stay_out_of_repr():
    settings = load_settings(
        ENV
        | PHASE_5_SECRETS
        | {
            "HAUSBUCH_SYNC_APPROVAL_TIMEOUT_S": "120",
            "HAUSBUCH_INBOX_LABEL": "Hausbuch",
            "HAUSBUCH_INBOX_IMAP_HOST": "imap.mail.example",
            "HAUSBUCH_INBOX_AUTHSERV_ID": "mx.mail.example",
            "HAUSBUCH_TEM_PROJECT_ID": "project-0001",
            "HAUSBUCH_TEM_REGION": "nl-ams",
            "HAUSBUCH_MAIL_FROM": "erinnerung@hausbuch.example",
        }
    )
    assert settings.fints_pin == PHASE_5_SECRETS["HAUSBUCH_FINTS_PIN"]
    assert settings.fints_product_id == PHASE_5_SECRETS["HAUSBUCH_FINTS_PRODUCT_ID"]
    assert settings.inbox_imap_user == PHASE_5_SECRETS["HAUSBUCH_INBOX_IMAP_USER"]
    assert settings.inbox_address == PHASE_5_SECRETS["HAUSBUCH_INBOX_ADDRESS"]
    assert (settings.sync_approval_timeout_s, settings.inbox_label) == (120, "Hausbuch")
    assert (settings.inbox_imap_host, settings.inbox_authserv_id) == (
        "imap.mail.example",
        "mx.mail.example",
    )
    assert (settings.tem_project_id, settings.tem_region) == ("project-0001", "nl-ams")
    text = repr(settings)
    for value in PHASE_5_SECRETS.values():
        assert value not in text
    assert "owner.a" not in text  # neither the account nor the token address


def test_the_elster_hersteller_id_loads_and_stays_out_of_repr():
    """Phase 6 D260 amended (10): the container variable of the app secret of the same name.
    An invented number."""
    assert load_settings(ENV).elster_hersteller_id is None
    settings = load_settings(ENV | {"HAUSBUCH_ELSTER_HERSTELLER_ID": "98765431"})
    assert settings.elster_hersteller_id == "98765431"
    assert "98765431" not in repr(settings)


# Phase 6 D163: one PIN per named slot. Synthetic values only (public repo).
SLOT_PINS = {
    "HAUSBUCH_FINTS_PIN": "pin-primary-synthetic",
    "HAUSBUCH_FINTS_PIN_OWNER_B": "pin-owner-b-synthetic",
    "HAUSBUCH_FINTS_PIN_JOINT_BANK": "pin-joint-synthetic",
    "HAUSBUCH_FINTS_PIN_DKB2": "pin-dkb2-synthetic",
    "HAUSBUCH_FINTS_PRODUCT_ID": "PRODUCTID0SYNTHETIC",
}


def test_every_slot_pin_loads_by_its_slot_and_none_appears_in_repr():
    settings = load_settings(ENV | SLOT_PINS)
    assert dict(settings.fints_pins) == {
        "primary": SLOT_PINS["HAUSBUCH_FINTS_PIN"],
        "owner_b": SLOT_PINS["HAUSBUCH_FINTS_PIN_OWNER_B"],
        "joint_bank": SLOT_PINS["HAUSBUCH_FINTS_PIN_JOINT_BANK"],
        "dkb2": SLOT_PINS["HAUSBUCH_FINTS_PIN_DKB2"],
    }
    assert settings.fints_pin == SLOT_PINS["HAUSBUCH_FINTS_PIN"]
    text = repr(settings)
    for value in SLOT_PINS.values():
        assert value not in text


def test_a_slot_pin_alone_needs_no_primary_pin():
    settings = load_settings(
        ENV
        | {
            "HAUSBUCH_FINTS_PIN_OWNER_B": "pin-owner-b-synthetic",
            "HAUSBUCH_FINTS_PRODUCT_ID": "PRODUCTID0SYNTHETIC",
        }
    )
    assert dict(settings.fints_pins) == {"owner_b": "pin-owner-b-synthetic"}
    assert settings.fints_pin is None


def test_names_that_are_no_slot_and_empty_values_are_ignored():
    settings = load_settings(
        ENV
        | SLOT_PINS
        | {
            "HAUSBUCH_FINTS_PIN_PRIMARY": "ignored-synthetic",  # primary reads HAUSBUCH_FINTS_PIN
            "HAUSBUCH_FINTS_PIN_2ND": "ignored-synthetic",  # a slot starts with a letter
            "HAUSBUCH_FINTS_PIN_owner_c": "ignored-synthetic",  # the suffix is upper-cased
            "HAUSBUCH_FINTS_PIN_": "ignored-synthetic",
            "HAUSBUCH_FINTS_PIN_" + "A" * 33: "ignored-synthetic",
            "HAUSBUCH_FINTS_PIN_EMPTY": "  ",
        }
    )
    assert set(settings.fints_pins) == {"primary", "owner_b", "joint_bank", "dkb2"}
    assert settings.fints_pins["primary"] == SLOT_PINS["HAUSBUCH_FINTS_PIN"]


def test_fints_pin_and_the_primary_slot_are_one_value():
    from hausbuch.settings import Settings

    base = {
        "database_url": URL,
        "iban_hmac_key": bytes.fromhex(KEY),
        "session_key": bytes.fromhex(SESSION_KEY),
        "rp_id": "hausbuch.example",
        "origin": "https://hausbuch.example",
        "blob_store": "local",
        "blob_root": Path("/nonexistent"),
        "fints_product_id": "PRODUCTID0SYNTHETIC",
    }
    assert Settings(**base, fints_pins={"primary": "p-1"}).fints_pin == "p-1"
    assert dict(Settings(**base, fints_pin="p-1").fints_pins) == {"primary": "p-1"}
    with pytest.raises(SettingsError, match="different values") as raised:
        Settings(**base, fints_pin="p-1", fints_pins={"primary": "p-2"})
    assert "p-1" not in str(raised.value) and "p-2" not in str(raised.value)
    with pytest.raises(SettingsError, match=r"\[a-z\]"):
        Settings(**base, fints_pins={"Owner_B": "p-1"})
    with pytest.raises(SettingsError, match="HAUSBUCH_FINTS_PIN_OWNER_B must not be empty"):
        Settings(**base, fints_pins={"owner_b": ""})


def test_the_slot_variable_names_agree_with_the_rules():
    from hausbuch.rules.bank_sync import pin_variable
    from hausbuch.settings import fints_pin_variable

    for slot in ("primary", "owner_b", "joint_bank", "dkb2", "a" * 32):
        assert fints_pin_variable(slot) == pin_variable(slot)


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
        ({"HAUSBUCH_AI_ENABLED": "yes"}, "true or false"),
        ({"HAUSBUCH_AI_INFERENCE_GEO": "eu"}, "must be one of us, global"),
        ({"HAUSBUCH_AI_MAX_PAGES": "0"}, "positive integer"),
        ({"HAUSBUCH_AI_MAX_PAGES": "601"}, "must not exceed 600"),
        ({"HAUSBUCH_AI_TIMEOUT_S": "-5"}, "positive integer"),
        # Phase 5 §9: a secret without its partner is refused, naming variables only. (Phase 6
        # D260 dropped a PIN without the product id: test_a_pin_without_the_product_id_is_valid.)
        ({"HAUSBUCH_INBOX_IMAP_HOST": "imap.gmail.com:993"}, "lower-case host name"),
        ({"HAUSBUCH_INBOX_AUTHSERV_ID": "MX.google.com"}, "lower-case host name"),
        ({"HAUSBUCH_TEM_REGION": "fr-par/x"}, "must be a Scaleway region"),
        ({"HAUSBUCH_SYNC_APPROVAL_TIMEOUT_S": "0"}, "positive integer"),
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


def test_the_pin_mapping_hides_its_values_and_hashes():
    settings = load_settings(ENV | SLOT_PINS)
    hash(settings)  # Settings stays hashable with the slot mapping
    text = repr(settings.fints_pins)
    assert "owner_b" in text
    for value in SLOT_PINS.values():
        assert value not in text


# --- Phase 7 chain sources (docs/phases/phase-7.md D181, D192, §17 runbook step 19) ----------

CHAIN_KEY = "etherscan-test-key-0001"
SOLANA_URL = "https://solana.example.invalid/?api-key=solana-test-key-0002"
HELIUS_KEY = "helius-test-key-0003"
BSC_URL = "https://bsc.example.invalid/v1/bsc-test-key-0004"  # a provider's key in the path


def test_chain_source_defaults():
    settings = load_settings(ENV)
    assert (settings.etherscan_key, settings.helius_key) == (None, None)
    # D181 (d): Solana works without a secret, over the public endpoint.
    assert settings.solana_rpc_url.get_secret_value() == "https://api.mainnet-beta.solana.com"
    assert settings.esplora_urls == ("https://mempool.space/api", "https://blockstream.info/api")
    assert settings.etherscan_url == "https://api.etherscan.io/v2/api"
    assert settings.bsc_rpc_url.get_secret_value() == "https://bsc-dataseed.bnbchain.org"
    assert settings.solana_rpc_rps == 4


def test_chain_source_settings_are_read_and_secrets_never_shown():
    settings = load_settings(
        ENV
        | {
            "HAUSBUCH_ETHERSCAN_KEY": CHAIN_KEY,
            "HAUSBUCH_SOLANA_RPC_URL": SOLANA_URL,
            "HAUSBUCH_HELIUS_KEY": HELIUS_KEY,
            "HAUSBUCH_ESPLORA_URL": "https://esplora.example.invalid/api, https://b.invalid/api",
            "HAUSBUCH_ETHERSCAN_URL": "https://indexer.example.invalid/api",
            "HAUSBUCH_BSC_RPC_URL": BSC_URL,
            "HAUSBUCH_SOLANA_RPC_RPS": "40",
        }
    )
    assert settings.etherscan_key.get_secret_value() == CHAIN_KEY
    assert settings.solana_rpc_url.get_secret_value() == SOLANA_URL
    assert settings.helius_key.get_secret_value() == HELIUS_KEY
    assert settings.esplora_urls == ("https://esplora.example.invalid/api", "https://b.invalid/api")
    assert settings.etherscan_url == "https://indexer.example.invalid/api"
    assert settings.bsc_rpc_url.get_secret_value() == BSC_URL
    assert settings.solana_rpc_rps == 40
    for secret in (CHAIN_KEY, SOLANA_URL, "solana-test-key-0002", HELIUS_KEY, BSC_URL):
        assert secret not in repr(settings) and secret not in str(settings)


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("HAUSBUCH_SOLANA_RPC_URL", "solana.example.invalid/?api-key=solana-test-key-0002"),
        ("HAUSBUCH_ESPLORA_URL", "ftp://esplora.example.invalid"),
        ("HAUSBUCH_ESPLORA_URL", " , "),
        ("HAUSBUCH_ETHERSCAN_URL", "https://"),
        ("HAUSBUCH_BSC_RPC_URL", "https://bsc.example.invalid:port"),
        ("HAUSBUCH_SOLANA_RPC_RPS", "0"),
        ("HAUSBUCH_SOLANA_RPC_RPS", "four"),
    ],
)
def test_a_bad_chain_source_setting_is_named_without_its_value(name, value):
    with pytest.raises(SettingsError) as caught:
        load_settings(ENV | {name: value})
    assert name in str(caught.value)
    assert "solana-test-key-0002" not in str(caught.value)
