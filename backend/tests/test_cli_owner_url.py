"""The admin commands never print the owner URL or its password (7 Nov 2026 leak class)."""

import pytest

from hausbuch import cli

SECRET = "S3CRET-pw"


@pytest.mark.parametrize(
    "url",
    [
        f"user:{SECRET}@host/db",  # no scheme: refused when the engine is created
        f"postgresql+psycopg://user:{SECRET}@host:abc/db",  # bad port
        f"postgresql+psycopg://user:{SECRET}@127.0.0.1:1/db",  # refused on connect
        f"postgresql+psycopg://user:p@ss{SECRET}@127.0.0.1:1/db",  # '@' inside the password
    ],
)
def test_a_bad_owner_url_prints_neither_url_nor_password(url, capsys):
    code = cli.main(["evals", "report"], environ={"HAUSBUCH_ADMIN_DATABASE_URL": url})
    out, err = capsys.readouterr()
    assert code == 1
    assert SECRET not in out and SECRET not in err
    assert "127.0.0.1" not in err and "host" not in out
