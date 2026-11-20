"""Provider keys and URLs typed in the app, stored encrypted (docs/phases/phase-6.md D260):
services.app_secrets against Postgres as hausbuch_app under RLS. Every key, URL and session
key here is invented."""

import dataclasses
import logging
from datetime import UTC, datetime

import pytest
from db.db_support import TEST_IBAN_KEY
from pydantic import SecretStr
from sqlalchemy import select, text, update

from hausbuch import i18n
from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import app_secret as secret_table
from hausbuch.db.tables import event as event_table
from hausbuch.rules import errors as E
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.api import InboxItemKind
from hausbuch.schemas.app_secrets import AppSecretName as N
from hausbuch.schemas.app_secrets import AppSecretSource as Src
from hausbuch.schemas.events import EventType
from hausbuch.services import app_secrets as secrets
from hausbuch.services import review
from hausbuch.settings import SOLANA_PUBLIC_RPC_URL, Settings

pytestmark = pytest.mark.db

T0 = datetime(2026, 11, 16, 9, 0, tzinfo=UTC)
APP_KEY = "app-typed-etherscan-KEY-0042"
ENV_KEY = "container-etherscan-KEY-7777"
APP_URL = "https://solana.rpc.example/v1/app-typed-path-secret"
APP_AI = "sk-ant-app-typed-0123456789"
OTHER_KEY = bytes(range(101, 133))  # a rotated session key
SECRETS = (APP_KEY, ENV_KEY, APP_URL, APP_AI)


def _settings(tmp_path, **overrides) -> Settings:
    values = {
        "database_url": "postgresql+psycopg://hausbuch_app@localhost/unused",
        "iban_hmac_key": TEST_IBAN_KEY,
        "session_key": bytes(range(1, 33)),
        "rp_id": "testserver",
        "origin": "https://testserver",
        "blob_store": "local",
        "blob_root": tmp_path / "blobs",
    } | overrides
    return Settings(**values)


@pytest.fixture
def settings(tmp_path):
    return _settings(tmp_path)


@pytest.fixture
def env_settings(tmp_path):
    return _settings(tmp_path, etherscan_key=SecretStr(ENV_KEY))


def _set(run, ws, name, value, settings):
    return run(ws, secrets.set_secret, name, SecretStr(value), settings=settings, now=T0)


def _rows(app_engine, ws):
    with workspace_transaction(app_engine, ws) as conn:
        return conn.execute(select(secret_table)).all()


def _all_text(app_engine, ws) -> bytes:
    with workspace_transaction(app_engine, ws) as conn:
        rows = [
            *conn.execute(select(secret_table)).all(),
            *conn.execute(select(event_table)).all(),
        ]
        jobs = conn.execute(text("SELECT args FROM procrastinate_jobs")).scalars().all()
    out = bytearray(repr([tuple(r) for r in rows]).encode() + repr(jobs).encode())
    for row in rows:
        for value in row:
            if isinstance(value, bytes | memoryview):
                out += bytes(value)
    return bytes(out)


def test_a_key_round_trips_encrypted_and_its_event_names_the_name_only(
    run, read, app_engine, fresh_workspaces, settings, caplog
):
    caplog.set_level(logging.DEBUG)
    ws, _ = fresh_workspaces
    state = _set(run, ws, N.ETHERSCAN_KEY, f"  {APP_KEY}\n", settings)
    assert (state.source, state.updated_at) == (Src.APP, T0)
    (row,) = _rows(app_engine, ws)
    assert APP_KEY.encode() not in bytes(row.ciphertext)
    assert len(bytes(row.nonce)) == 12 and row.key_version == secrets.KEY_VERSION
    value = read(ws, secrets.resolve, settings, N.ETHERSCAN_KEY)
    assert value.get_secret_value() == APP_KEY  # trimmed
    with workspace_transaction(app_engine, ws) as conn:
        (event,) = conn.execute(select(event_table)).all()
    assert event.type == EventType.WORKSPACE_APP_SECRET_SET.value
    assert event.payload == {"name": "etherscan_key"}
    for secret in SECRETS:
        assert secret.encode() not in _all_text(app_engine, ws)
        assert secret not in caplog.text
    assert APP_KEY not in repr(read(ws, secrets.lookup, settings, N.ETHERSCAN_KEY))


def test_a_typed_value_wins_over_the_container_and_delete_falls_back(
    run, read, fresh_workspaces, env_settings
):
    ws, _ = fresh_workspaces
    found = read(ws, secrets.lookup, env_settings, N.ETHERSCAN_KEY)
    assert found.source is Src.CONTAINER and found.value.get_secret_value() == ENV_KEY
    _set(run, ws, N.ETHERSCAN_KEY, APP_KEY, env_settings)
    found = read(ws, secrets.lookup, env_settings, N.ETHERSCAN_KEY)
    assert found.source is Src.APP and found.value.get_secret_value() == APP_KEY
    replaced = _set(run, ws, N.ETHERSCAN_KEY, "second-typed-key", env_settings)
    assert replaced.source is Src.APP
    assert read(ws, secrets.resolve, env_settings, N.ETHERSCAN_KEY).get_secret_value() == (
        "second-typed-key"
    )
    assert run(ws, secrets.delete_secret, N.ETHERSCAN_KEY, now=T0) is True
    found = read(ws, secrets.lookup, env_settings, N.ETHERSCAN_KEY)
    assert found.source is Src.CONTAINER and found.value.get_secret_value() == ENV_KEY
    assert run(ws, secrets.delete_secret, N.ETHERSCAN_KEY, now=T0) is False


def test_the_card_lists_every_name_by_its_source_never_a_value(
    run, read, fresh_workspaces, env_settings
):
    ws, _ = fresh_workspaces
    _set(run, ws, N.ANTHROPIC_API_KEY, APP_AI, env_settings)
    states = {s.name: s for s in read(ws, secrets.secret_states, env_settings)}
    assert list(states) == list(N)
    assert states[N.ANTHROPIC_API_KEY].source is Src.APP
    assert states[N.ETHERSCAN_KEY].source is Src.CONTAINER
    # A URL at its public default reads as not set: the public endpoint is in use.
    assert states[N.SOLANA_RPC_URL].source is Src.NONE
    assert states[N.COINGECKO_KEY].source is Src.NONE
    assert states[N.ETHERSCAN_KEY].variable == "HAUSBUCH_ETHERSCAN_KEY"
    dumped = repr([s.model_dump() for s in states.values()])
    assert all(secret not in dumped for secret in SECRETS)


def test_a_rotated_session_key_leaves_the_row_unreadable_without_falling_back(
    run, read, fresh_workspaces, env_settings
):
    ws, _ = fresh_workspaces
    _set(run, ws, N.ETHERSCAN_KEY, APP_KEY, env_settings)
    rotated = dataclasses.replace(env_settings, session_key=OTHER_KEY)
    found = read(ws, secrets.lookup, rotated, N.ETHERSCAN_KEY)
    assert (found.source, found.value, found.updated_at) == (Src.UNREADABLE, None, T0)
    assert read(ws, secrets.resolve, rotated, N.ETHERSCAN_KEY) is None  # never ENV_KEY
    states = {s.name: s for s in read(ws, secrets.secret_states, rotated)}
    assert states[N.ETHERSCAN_KEY].source is Src.UNREADABLE
    with pytest.raises(secrets.SecretUnreadable):
        read(ws, secrets.workspace_settings, rotated, [N.ETHERSCAN_KEY])
    # Typing it again under the new key makes it readable.
    _set(run, ws, N.ETHERSCAN_KEY, APP_KEY, rotated)
    assert read(ws, secrets.lookup, rotated, N.ETHERSCAN_KEY).source is Src.APP


def test_an_unknown_key_version_is_unreadable(run, read, app_engine, fresh_workspaces, settings):
    ws, _ = fresh_workspaces
    _set(run, ws, N.COINGECKO_KEY, APP_KEY, settings)
    with workspace_transaction(app_engine, ws) as conn:
        conn.execute(update(secret_table).values(key_version=2))
    assert read(ws, secrets.lookup, settings, N.COINGECKO_KEY).source is Src.UNREADABLE


def test_a_ciphertext_moved_to_another_name_or_workspace_does_not_open(
    run, read, app_engine, fresh_workspaces, settings
):
    a, b = fresh_workspaces
    _set(run, a, N.ETHERSCAN_KEY, APP_KEY, settings)
    (row,) = _rows(app_engine, a)
    with workspace_transaction(app_engine, a) as conn:
        conn.execute(
            secret_table.insert().values(
                workspace_id=a,
                name=N.HELIUS_KEY.value,
                nonce=row.nonce,
                ciphertext=row.ciphertext,
                key_version=row.key_version,
            )
        )
    with workspace_transaction(app_engine, b) as conn:
        conn.execute(
            secret_table.insert().values(
                workspace_id=b,
                name=N.ETHERSCAN_KEY.value,
                nonce=row.nonce,
                ciphertext=row.ciphertext,
                key_version=row.key_version,
            )
        )
    assert read(a, secrets.lookup, settings, N.ETHERSCAN_KEY).source is Src.APP
    assert read(a, secrets.lookup, settings, N.HELIUS_KEY).source is Src.UNREADABLE
    assert read(b, secrets.lookup, settings, N.ETHERSCAN_KEY).source is Src.UNREADABLE


def test_a_workspace_never_sees_another_workspaces_value(run, read, fresh_workspaces, settings):
    a, b = fresh_workspaces
    _set(run, a, N.ETHERSCAN_KEY, APP_KEY, settings)
    assert read(b, secrets.lookup, settings, N.ETHERSCAN_KEY).source is Src.NONE
    assert run(b, secrets.delete_secret, N.ETHERSCAN_KEY, now=T0) is False
    assert read(a, secrets.lookup, settings, N.ETHERSCAN_KEY).source is Src.APP


@pytest.mark.parametrize(
    ("name", "value", "code"),
    [
        (N.ETHERSCAN_KEY, "   ", E.APP_SECRET_INVALID),
        (N.ETHERSCAN_KEY, "two words", E.APP_SECRET_INVALID),
        (N.ETHERSCAN_KEY, "tab\tinside", E.APP_SECRET_INVALID),
        (N.ETHERSCAN_KEY, "ctrl\x00char", E.APP_SECRET_INVALID),
        (N.ETHERSCAN_KEY, "schlüssel", E.APP_SECRET_INVALID),
        (N.ETHERSCAN_KEY, "k" * 513, E.APP_SECRET_INVALID),
        (N.SOLANA_RPC_URL, "solana.rpc.example/v1", E.APP_SECRET_URL_INVALID),
        (N.SOLANA_RPC_URL, "ftp://solana.rpc.example", E.APP_SECRET_URL_INVALID),
        (N.BSC_RPC_URL, "https://", E.APP_SECRET_URL_INVALID),
        (N.FINTS_PRODUCT_ID, "P" * 26, E.APP_SECRET_INVALID),
        (N.TEM_PROJECT_ID, "not-a-project", E.APP_SECRET_INVALID),
        (N.MAIL_FROM, "erinnerung.mail.example", E.APP_SECRET_INVALID),
        (N.INBOX_ADDRESS, "a b@gmail.example", E.APP_SECRET_INVALID),
        (N.INBOX_LABEL, "Bel\x07ege", E.APP_SECRET_INVALID),
        (N.INBOX_LABEL, "ü" * 257, E.APP_SECRET_INVALID),
        (N.ELSTER_HERSTELLER_ID, "HID-74931", E.APP_SECRET_INVALID),
        (N.ELSTER_HERSTELLER_ID, "749 31", E.APP_SECRET_INVALID),
        (N.ELSTER_HERSTELLER_ID, "7" * 11, E.APP_SECRET_INVALID),
        (N.ELSTER_HERSTELLER_ID, "\u0667\u0664\u0669", E.APP_SECRET_INVALID),
        (N.ELSTER_HERSTELLER_ID, "-74931", E.APP_SECRET_INVALID),
    ],
    ids=[
        "blank",
        "space",
        "tab",
        "nul",
        "non_ascii",
        "too_long",
        "no_scheme",
        "ftp",
        "no_host",
        "product_id_too_long",
        "project_not_uuid",
        "sender_no_at",
        "address_blank",
        "label_control",
        "label_over_512_bytes",
        "hersteller_id_letters",
        "hersteller_id_space",
        "hersteller_id_too_long",
        "hersteller_id_non_ascii_digits",
        "hersteller_id_sign",
    ],
)
def test_a_value_outside_its_checks_is_refused_without_quoting_it(
    run, app_engine, fresh_workspaces, settings, name, value, code
):
    ws, _ = fresh_workspaces
    with pytest.raises(RuleError) as refused:
        _set(run, ws, name, value, settings)
    assert refused.value.code == code
    assert value.strip() == "" or value not in str(refused.value)
    assert _rows(app_engine, ws) == []


def test_each_name_takes_a_value_of_its_form(run, read, fresh_workspaces, settings):
    ws, _ = fresh_workspaces
    accepted = {
        N.HELIUS_KEY: "k" * 512,
        N.SOLANA_RPC_URL: APP_URL,
        N.FINTS_PRODUCT_ID: "PRODUCTID0123456789ABCDEF",
        N.TEM_SECRET_KEY: "tem-secret-0123",
        N.TEM_PROJECT_ID: "11111111-2222-4333-8444-555555555555",
        N.MAIL_FROM: "erinnerung@mail.example",
        N.INBOX_IMAP_USER: "haushalt@gmail.example",
        N.INBOX_IMAP_PASSWORD: "abcd efgh ijkl mnop",  # Google prints it in four groups
        N.INBOX_ADDRESS: "haushalt+t0ken@gmail.example",
        N.INBOX_LABEL: "Hausbuch Belege/Häuser",
        N.ELSTER_HERSTELLER_ID: "40123",
    }
    for name, value in accepted.items():
        _set(run, ws, name, value, settings)
        assert read(ws, secrets.resolve, settings, name).get_secret_value() == value


def test_only_a_setting_that_is_not_secret_is_shown_back(
    run, read, app_engine, fresh_workspaces, settings
):
    ws, _ = fresh_workspaces
    _set(run, ws, N.INBOX_LABEL, "Hausbuch", settings)
    _set(run, ws, N.INBOX_IMAP_PASSWORD, "abcd efgh ijkl mnop", settings)
    _set(run, ws, N.TEM_SECRET_KEY, "tem-secret-0123", settings)
    container = dataclasses.replace(settings, mail_from="erinnerung@mail.example")
    states = {s.name: s for s in read(ws, secrets.secret_states, container)}
    assert (states[N.INBOX_LABEL].secret, states[N.INBOX_LABEL].value) == (False, "Hausbuch")
    assert states[N.MAIL_FROM].source is Src.CONTAINER
    assert states[N.MAIL_FROM].value == "erinnerung@mail.example"
    assert states[N.INBOX_IMAP_PASSWORD].secret is True
    assert states[N.INBOX_IMAP_PASSWORD].value is None
    assert states[N.TEM_SECRET_KEY].value is None
    dumped = repr([s.model_dump() for s in states.values()])
    assert "abcd efgh" not in dumped and "tem-secret-0123" not in dumped
    with workspace_transaction(app_engine, ws) as conn:
        payloads = [r.payload for r in conn.execute(select(event_table)).all()]
    assert payloads == [
        {"name": "inbox_label"},
        {"name": "inbox_imap_password"},
        {"name": "tem_secret_key"},
    ]
    assert b"Hausbuch" not in _all_text(app_engine, ws).replace(b"hausbuch", b"")


def test_an_unreadable_plain_setting_is_unset_never_the_containers(
    run, read, fresh_workspaces, settings
):
    ws, _ = fresh_workspaces
    _set(run, ws, N.FINTS_PRODUCT_ID, "PRODUCTID0123456789ABCDEF", settings)
    container = dataclasses.replace(settings, fints_product_id="CONTAINERPRODUCT0000")
    rotated = dataclasses.replace(container, session_key=OTHER_KEY)
    names = [N.FINTS_PRODUCT_ID]
    assert read(ws, secrets.workspace_settings, container, names).fints_product_id == (
        "PRODUCTID0123456789ABCDEF"
    )
    unset = read(ws, secrets.workspace_settings, rotated, names, unreadable="unset")
    assert unset.fints_product_id is None
    with pytest.raises(secrets.SecretUnreadable):
        read(ws, secrets.workspace_settings, rotated, names)


def test_workspace_settings_puts_the_typed_values_in_place(
    run, read, fresh_workspaces, env_settings
):
    a, b = fresh_workspaces
    _set(run, a, N.SOLANA_RPC_URL, APP_URL, env_settings)
    _set(run, a, N.ANTHROPIC_API_KEY, APP_AI, env_settings)
    names = [N.SOLANA_RPC_URL, N.ETHERSCAN_KEY, N.ANTHROPIC_API_KEY]
    typed = read(a, secrets.workspace_settings, env_settings, names)
    assert typed.solana_rpc_url.get_secret_value() == APP_URL
    assert typed.etherscan_key.get_secret_value() == ENV_KEY  # the container's still
    assert typed.anthropic_api_key == APP_AI  # a plain str field stays a str
    assert APP_URL not in repr(typed)
    other = read(b, secrets.workspace_settings, env_settings, names)
    assert other is env_settings
    assert other.solana_rpc_url.get_secret_value() == SOLANA_PUBLIC_RPC_URL


# --- D260 amended (9): the Zu prüfen card for a value that no longer decrypts ---------------


def _card(read, ws, settings):
    return [
        item
        for item in read(ws, review.inbox, settings=settings)
        if item.kind is InboxItemKind.APP_SECRET_UNREADABLE
    ]


def test_no_card_with_nothing_typed_or_only_the_containers_value(
    read, fresh_workspaces, env_settings
):
    ws, _ = fresh_workspaces
    rotated = dataclasses.replace(env_settings, session_key=OTHER_KEY)
    assert _card(read, ws, rotated) == []  # ENV_KEY is set, nothing typed
    assert read(ws, secrets.unreadable_names, rotated, list(N)) == []


def test_the_card_names_each_group_and_what_stopped_never_a_value(
    run, read, fresh_workspaces, env_settings
):
    a, b = fresh_workspaces
    _set(run, a, N.ETHERSCAN_KEY, APP_KEY, env_settings)
    _set(run, a, N.ANTHROPIC_API_KEY, APP_AI, env_settings)
    _set(run, a, N.HELIUS_KEY, "typed-helius-0001", env_settings)
    later = datetime(2026, 11, 16, 10, 0, tzinfo=UTC)
    run(
        a,
        secrets.set_secret,
        N.SOLANA_RPC_URL,
        SecretStr(APP_URL),
        settings=env_settings,
        now=later,
    )
    assert _card(read, a, env_settings) == []  # all readable
    rotated = dataclasses.replace(env_settings, session_key=OTHER_KEY)
    (card,) = _card(read, a, rotated)
    assert card.created_at == later  # the newest of the unreadable values
    assert card.reasoning.startswith("In der App gespeicherte Zugangsschlüssel lassen sich nicht")
    assert (
        "Betroffen – KI: Anthropic-API-Schlüssel (Claude); Blockchains & Kurse: Etherscan, "
        "Solana-RPC-Adresse, Helius." in card.reasoning
    )
    assert "Dokumente werden nicht automatisch ausgelesen" in card.reasoning
    assert "Ethereum- und Arbitrum-Adressen werden nicht gelesen." in card.reasoning
    assert "Solana-Adressen werden nicht gelesen." in card.reasoning
    assert "nicht den Wert aus der Server-Konfiguration" in card.reasoning
    assert card.question == "Unter Einstellungen › Zugangsschlüssel neu eintragen oder löschen."
    with i18n.use("en"):  # phase 10 D284
        (en,) = _card(read, a, rotated)
        [state] = [s for s in read(a, secrets.secret_states, rotated) if s.name is N.HELIUS_KEY]
    assert en.reasoning.startswith("Access keys stored in the app can no longer be read")
    assert (
        "Affected – AI: Anthropic API key (Claude); Blockchains & prices: Etherscan, "
        "Solana RPC address, Helius." in en.reasoning
    )
    assert "Solana addresses are not read." in en.reasoning
    assert en.question == "Enter again or delete under Settings › Access keys."
    assert state.label == "Helius" and state.purpose.startswith("Short descriptions")
    dumped = card.model_dump_json()
    assert not any(s in dumped for s in SECRETS)
    assert "etherscan_key" not in dumped and "HAUSBUCH_" not in dumped
    assert _card(read, b, rotated) == []  # the other workspace typed nothing
    # Typed again under the new key, or deleted: the card goes once none is left.
    _set(run, a, N.ETHERSCAN_KEY, APP_KEY, rotated)
    (card,) = _card(read, a, rotated)
    assert "Etherscan" not in card.reasoning
    for name in (N.ANTHROPIC_API_KEY, N.SOLANA_RPC_URL, N.HELIUS_KEY):
        assert run(a, secrets.delete_secret, name) is True
    assert _card(read, a, rotated) == []
