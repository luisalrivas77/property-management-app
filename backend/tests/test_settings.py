import pytest

from hausbuch.settings import SettingsError, load_settings

URL = "postgresql+psycopg://hausbuch_app@localhost/hausbuch"
KEY = "0f" * 32


def test_loads_url_and_decodes_key():
    settings = load_settings({"HAUSBUCH_DATABASE_URL": URL, "HAUSBUCH_IBAN_HMAC_KEY": KEY})
    assert settings.database_url == URL
    assert settings.iban_hmac_key == bytes([0x0F] * 32)


def test_repr_hides_secrets():
    settings = load_settings({"HAUSBUCH_DATABASE_URL": URL, "HAUSBUCH_IBAN_HMAC_KEY": KEY})
    assert KEY not in repr(settings) and "localhost" not in repr(settings)


@pytest.mark.parametrize(
    ("environ", "message"),
    [
        ({"HAUSBUCH_DATABASE_URL": URL}, "not set"),
        ({"HAUSBUCH_DATABASE_URL": URL, "HAUSBUCH_IBAN_HMAC_KEY": ""}, "not set"),
        ({"HAUSBUCH_DATABASE_URL": URL, "HAUSBUCH_IBAN_HMAC_KEY": "0f" * 31}, "at least 32"),
        ({"HAUSBUCH_DATABASE_URL": URL, "HAUSBUCH_IBAN_HMAC_KEY": "zz" * 32}, "hex"),
        ({"HAUSBUCH_IBAN_HMAC_KEY": KEY}, "HAUSBUCH_DATABASE_URL"),
    ],
    ids=["missing-key", "empty-key", "short-key", "not-hex", "missing-url"],
)
def test_rejects_bad_environment(environ, message):
    with pytest.raises(SettingsError, match=message):
        load_settings(environ)
