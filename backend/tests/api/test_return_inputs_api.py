"""The income tax return's read routes over HTTP (docs/phases/phase-4b.md §9, §10): the years an
Anlage N exists for, and the follow-up fields of the whole-return estimate."""

import pytest
from api_support import ok

from hausbuch.rules.tax import registry

pytestmark = pytest.mark.db


def test_the_anlage_n_years_are_the_rule_set_files(client_a):
    got = ok(client_a.get("/api/tax/anlage-n/years"))
    assert got == {"years": list(registry.anlage_n_years())}
    assert 2026 in got["years"]


def test_the_anlage_n_years_need_a_session(api_app):
    from api_support import client_for

    with client_for(api_app, None) as anonymous:
        assert anonymous.get("/api/tax/anlage-n/years").status_code == 401
