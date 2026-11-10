"""Pending until hausbuch.rules.statements exists (phase 2); then these must pass unchanged."""

import pytest
from fixture_models_phase2 import DuplicatesFixture, LineHashFixture, ReconcileFixture, load

rules = pytest.importorskip("hausbuch.rules.statements", reason="phase 2 rule not implemented")
HASH = load("line_hash")
assert isinstance(HASH, LineHashFixture)
DUP = load("duplicates")
assert isinstance(DUP, DuplicatesFixture)
REC = load("reconcile")
assert isinstance(REC, ReconcileFixture)


@pytest.mark.rule
@pytest.mark.parametrize("case", HASH.cases, ids=lambda c: c.id)
def test_line_hashes(case):
    assert [rules.line_hashes(HASH.account_id, f) for f in case.files] == case.expect


@pytest.mark.rule
def test_line_hash_ordinal_is_explicit():
    line = HASH.cases[0].files[0][0]
    assert rules.line_hash(HASH.account_id, line, 0) == HASH.cases[0].expect[0][0]


@pytest.mark.rule
@pytest.mark.parametrize("case", DUP.likely, ids=lambda c: c.id)
def test_likely_duplicates(case):
    assert rules.likely_duplicates(case.new_line, case.existing) == case.expect


@pytest.mark.rule
@pytest.mark.parametrize("case", DUP.resolve, ids=lambda c: c.id)
def test_resolve_duplicate(case):
    got = rules.resolve_duplicate(case.transaction_id, case.other_transaction_id, case.status)
    if case.expect is None:
        assert got is None
    else:
        assert (got.excluded_id, got.excluded_as_duplicate_of) == (
            case.expect.excluded_id,
            case.expect.excluded_as_duplicate_of,
        )


@pytest.mark.rule
@pytest.mark.parametrize("case", REC.cases, ids=lambda c: c.id)
def test_reconcile(case):
    sums = rules.window_sums(
        [line.stored() for line in case.lines],
        anchor_on=case.anchor.on,
        first_booking_on=case.file.first_booking_on,
        last_booking_on=case.file.last_booking_on,
    )
    assert sums == case.expect.sums
    result = rules.reconcile(
        case.anchor.balance,
        sums.stored_before,
        sums.stored_through,
        case.file.declared_closing,
        case.file.file_opening,
        anchor_on=case.anchor.on,
        first_booking_on=case.file.first_booking_on,
    )
    assert result == case.expect.result


# --- is_direct_debit (phase 5 D126): the code shapes seen on the real account on 10 Nov 2026,
# with synthetic numbers in the proprietary code


def _code(domain=None, family=None, sub_family=None, proprietary=None) -> dict:
    codes = {
        "domain": domain,
        "family": family,
        "sub_family": sub_family,
        "proprietary": proprietary,
    }
    return {"bank_transaction_code": {k: v for k, v in codes.items() if v is not None}}


@pytest.mark.rule
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ({**_code("PMNT", "RDDT", "ESDD", "NDDT+105+0001+000"), "amount": "-100.00"}, True),
        (_code("PMNT", "IDDT"), True),
        (_code("PMNT", "ICDT", "ESCT", "NTRF+116+0002+000"), False),  # owner's transfer
        (_code("PMNT", "RCDT", "ESCT"), False),  # incoming rent
        (_code("PMNT", "RRCT", "ESCT"), False),  # incoming rent, the other shape
        (_code("ACMT", "RDDT"), False),
        (_code(proprietary="NDDT+105+0001+000"), False),
        ({"purpose": "LASTSCHRIFT Grundsteuer"}, False),  # a CSV line carries no code
        ({}, False),
        (None, False),
    ],
)
def test_is_direct_debit(raw, expected):
    assert rules.is_direct_debit(raw) is expected
