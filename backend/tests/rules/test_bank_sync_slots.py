"""rules.bank_sync's phase 6 step 3 helpers (docs/phases/phase-6.md D163, D173 (1), (5))
against tests/fixtures/phase6/fints_slots.yaml.

Slot names are examples; no PIN, login or product id appears here."""

import pytest
from fixture_models_phase6 import load

from hausbuch.rules import bank_sync as rules
from hausbuch.rules.errors import RuleError

FIXTURE = load("fints_slots")


def _live(case) -> None:
    if case.pending:
        pytest.skip(case.pending)


@pytest.mark.parametrize("case", FIXTURE.slots, ids=lambda c: c.id)
def test_pin_variable_matches_the_fixture(case):
    _live(case)
    assert rules.valid_slot(case.slot) is (case.variable is not None)
    if case.variable is None:
        with pytest.raises(ValueError) as raised:
            rules.pin_variable(case.slot)
        # The refusal never echoes the name it refused.
        assert case.slot == "" or case.slot not in str(raised.value)
    else:
        assert rules.pin_variable(case.slot) == case.variable


@pytest.mark.parametrize("case", FIXTURE.sync_checks, ids=lambda c: c.id)
def test_the_sync_slot_check_matches_the_fixture(case):
    _live(case)
    if case.expect_error is None:
        rules.check_slot(set(case.secrets), case.slot)
        return
    with pytest.raises(RuleError) as raised:
        rules.check_slot(set(case.secrets), case.slot)
    assert raised.value.code == case.expect_error.code
    assert case.names_variable in str(raised.value)


@pytest.mark.parametrize("case", FIXTURE.approval_texts, ids=lambda c: c.id)
def test_the_approval_text_matches_the_fixture(case):
    _live(case)
    assert rules.approval_text(case.bank_name) == case.text


@pytest.mark.parametrize("case", FIXTURE.auto_sync, ids=lambda c: c.id)
def test_the_auto_sync_guard_matches_the_fixture(case):
    _live(case)
    assert rules.auto_sync_allowed(case.bank_name, case.manual_syncs) is case.allowed


@pytest.mark.parametrize("name", [None, "", "  ", "Sparkasse Musterstadt", "Volksbank DKB-Weg"])
def test_an_unknown_or_other_bank_is_not_dkb(name):
    assert not rules.is_dkb(name)
    assert rules.approval_text(name) is None
    assert rules.auto_sync_allowed(name, [])


def test_a_dkb_name_in_any_case_with_spaces_is_dkb():
    assert rules.is_dkb("  dkb ag")
    assert rules.is_dkb("Deutsche Kreditbank AG")
    assert rules.is_dkb("  deutsche\n  KREDITBANK  Aktiengesellschaft")
    assert not rules.is_dkb("Sparkasse Musterstadt")
    assert not rules.auto_sync_allowed(" Dkb AG ", [False])
