import uuid
from datetime import date
from decimal import Decimal

from hausbuch.schemas.events import FieldChange, Updated
from hausbuch.services.events import diff_changes


def test_only_changed_fields_with_json_safe_values():
    uid = uuid.uuid4()
    old = {
        "full_name": "Erika",
        "rate": Decimal("0.4200"),
        "ends_on": None,
        "ref": "a",
        "link": None,
    }
    new = {
        "full_name": "Erika",
        "rate": Decimal("0.3500"),
        "ends_on": date(2027, 6, 30),
        "ref": None,
        "link": uid,
    }
    assert diff_changes(old, new) == {
        "rate": FieldChange(old="0.4200", new="0.3500"),
        "ends_on": FieldChange(old=None, new="2027-06-30"),
        "ref": FieldChange(old="a", new=None),
        "link": FieldChange(old=None, new=str(uid)),
    }


def test_numerically_equal_decimals_are_not_a_change():
    assert diff_changes({"rate": Decimal("0.42")}, {"rate": Decimal("0.4200")}) == {}


def test_key_missing_from_old_counts_as_none():
    assert diff_changes({}, {"a": None}) == {}
    assert diff_changes({}, {"a": 1}) == {"a": FieldChange(old=None, new=1)}


def test_result_fits_the_updated_payload():
    changes = diff_changes({"amount": Decimal("1.00")}, {"amount": Decimal("2.50")})
    assert Updated(changes=changes).model_dump(mode="json") == {
        "changes": {"amount": {"old": "1.00", "new": "2.50"}},
        "reason": None,
    }
