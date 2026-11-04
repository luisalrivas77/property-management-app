"""Pending until hausbuch.rules.iban exists; then these must pass unchanged."""

import pytest
from fixture_models import IbanFixture, load

rules = pytest.importorskip("hausbuch.rules.iban", reason="phase 1 rule not implemented")
FX = load("iban")
assert isinstance(FX, IbanFixture)
KEY = FX.hmac_key_utf8.encode()


@pytest.mark.rule
@pytest.mark.parametrize("case", FX.cases, ids=lambda c: c.id)
def test_iban(case):
    if case.expect.error:
        with pytest.raises(Exception) as excinfo:
            rules.normalize_iban(case.raw)
        assert excinfo.value.code == case.expect.error
        return
    normalized = rules.normalize_iban(case.raw)
    assert normalized == case.expect.normalized
    assert rules.mask_iban(normalized) == case.expect.masked
    assert rules.iban_hmac(normalized, KEY).hex() == case.expect.hmac_hex
