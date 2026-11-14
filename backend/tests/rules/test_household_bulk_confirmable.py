"""rules.household.taxonomy.BULK_CONFIRMABLE: the codes „Alle bestätigen“ in Zu prüfen may
confirm as a group (docs/phases/phase-6.md D159 amended 13 Nov 2026). Pinned, so a new code
joins it only by a decision."""

import pytest

from hausbuch.rules.household.taxonomy import BULK_CONFIRMABLE, TAX_RELEVANT
from hausbuch.schemas.allocation import AllocationCategory as C

pytestmark = pytest.mark.rule


def test_the_plain_household_income_and_spending_codes():
    assert {c.value for c in BULK_CONFIRMABLE} == {
        "hh_salary",
        "hh_child_benefit",
        "hh_other_income",
        "hh_housing",
        "hh_groceries",
        "hh_family_child",
        "hh_mobility",
        "hh_insurance",
        "hh_subscriptions",
        "hh_leisure_travel",
        "hh_health",
        "hh_other",
        "hh_interest_fees",
        "hh_personal_care",
        "hh_clothing",
        "hh_dining",
        "hh_home",
        "hh_electronics",
        "hh_online_shopping",
        "hh_fees",
        "hh_gifts",
        "hh_telecom",
        "hh_sport",
        "hh_travel",
        "hh_refund",
        "hh_fuel",
        "hh_streaming",
        "hh_pay_tv",
        "hh_music_audio",
        "hh_software",
        "hh_cloud",
        "hh_gaming",
        "hh_news",
        "hh_memberships",
        "hh_public_transport",
        "hh_taxi",
        "hh_parking",
        "hh_car_service",
        "hh_car_tax",
        "hh_supermarket",
        "hh_drugstore",
        "hh_bakery",
        "hh_restaurant",
        "hh_cafe",
        "hh_sweets",
        "hh_fast_food",
        "hh_delivery",
        "hh_rent",
        "hh_energy",
        "hh_broadcast_fee",
        "hh_flat_search",
        "hh_ins_car",
        "hh_ins_liability_contents",
        "hh_ins_health",
        "hh_ins_life",
        "hh_pharmacy",
        "hh_doctor",
        "hh_dental_optical",
        "hh_outings",
        "hh_books_media",
        "hh_hobbies",
        "hh_lodging",
        "hh_flights",
        "hh_kids_gear",
        "hh_toys",
        "hh_pregnancy_birth",
        "hh_mobile_phone",
        "hh_internet",
        "hh_postage",
    }


def test_never_a_code_a_person_confirms_line_by_line():
    one_by_one = {
        C.HH_CAPITAL_INCOME,
        C.HH_TAX_REFUND,
        C.HH_FAMILY_TRANSFERS,
        C.HH_WORK_EDUCATION,
        C.HH_INVESTMENT,
        C.HH_TAX_PAYMENT,
        C.UNCATEGORIZED,
        C.INTERNAL_TRANSFER_IN,
        C.INTERNAL_TRANSFER_OUT,
        C.HH_CASH,
        C.HH_CREDIT_CARD,
        C.SECURITY_PURCHASE,
    }
    assert not (BULK_CONFIRMABLE & (one_by_one | TAX_RELEVANT))
    assert {C.HH_DONATION, C.HH_HOUSEHOLD_SERVICES, C.HH_CHILDCARE, C.HH_PROPERTY_COST} <= (
        TAX_RELEVANT
    )
