"""Phase 9 D236: `one_off` is a field of the household taxonomy, not a judgement at run time.

The completeness check runs at import (rules/household/taxonomy.py) and against phase 6's
fixture household_categories.yaml, whose rows carry the field (fixture_models_phase6)."""

import fixture_models_phase6 as phase6

from hausbuch.rules.household.taxonomy import ONE_OFF, TAXONOMY
from hausbuch.schemas.allocation import AllocationCategory as C
from hausbuch.schemas.household import Relevance


def test_only_the_car_purchase_is_one_off():
    assert {C.HH_CAR_PURCHASE} == ONE_OFF


def test_every_row_carries_the_field_and_one_off_is_spending():
    assert all(isinstance(row.one_off, bool) for row in TAXONOMY.values())
    assert all(TAXONOMY[c].relevance is Relevance.SPENDING for c in ONE_OFF)


def test_the_phase6_fixture_lists_the_same_one_off_codes():
    rows = phase6.load("household_categories").codes
    assert {row.category for row in rows if row.one_off} == ONE_OFF
