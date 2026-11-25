"""Phase 9 D236, phase 10 D307: `one_off` is a field of the household taxonomy, not a judgement
at run time.

The completeness check runs at import (rules/household/taxonomy.py) and against phase 6's
fixture household_categories.yaml, whose rows carry the field (fixture_models_phase6)."""

import fixture_models_phase6 as phase6

from hausbuch.rules.household.taxonomy import ONE_OFF, TAXONOMY
from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.household import Relevance


def test_the_six_one_off_codes():
    """D236's car purchase and the five of phase 10 D307 (§7.12 (b))."""
    assert {
        C.HH_CAR_PURCHASE,
        C.HH_FLAT_SEARCH,
        C.HH_PREGNANCY_BIRTH,
        C.HH_ELECTRONICS,
        C.HH_WORK_EDUCATION,
        C.HH_DENTAL_OPTICAL,
    } == ONE_OFF


def test_every_row_carries_the_field_and_one_off_is_spending():
    assert all(isinstance(row.one_off, bool) for row in TAXONOMY.values())
    assert all(TAXONOMY[c].relevance is Relevance.SPENDING for c in ONE_OFF)


def test_the_phase6_fixture_lists_the_same_one_off_codes():
    rows = phase6.load("household_categories").codes
    assert {row.category for row in rows if row.one_off} == ONE_OFF
