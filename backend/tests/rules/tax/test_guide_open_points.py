"""Wave 2's open points in the Anlage N and children rules (phase 10 §7.6): two overlapping pairs
of wage certificates are two warnings, each naming its periods and rows; the birth year's
Kindergeld to-do names its child and ends once `kindergeld_from` is typed. Invented figures."""

from datetime import date
from uuid import uuid4

import pytest

from hausbuch.rules.tax import registry
from hausbuch.rules.tax.anlage_n._base import rules_for
from hausbuch.rules.tax.anlage_n.children import child_months
from hausbuch.rules.tax.anlage_n.draft import draft_n
from hausbuch.schemas.employment import OwnerReturnFacts, ReturnInputFact
from hausbuch.schemas.performance import ChildFact
from hausbuch.schemas.tax import WarningCode

pytestmark = pytest.mark.rule

YEAR = 2026


def _certificate(owner, period_from: date, period_to: date, employer: str) -> ReturnInputFact:
    return ReturnInputFact.model_validate(
        {
            "id": uuid4(),
            "owner_id": owner,
            "kind": "wage_certificate",
            "tax_year": YEAR,
            "document_id": uuid4(),
            "value": {
                "period_from": period_from.isoformat(),
                "period_to": period_to.isoformat(),
                "employer": employer,
                "tax_class": 4,
                "gross": "20000.00",
            },
        }
    )


def test_each_overlapping_pair_is_its_own_warning_with_its_rows():
    owner = uuid4()
    rows = [
        _certificate(owner, date(YEAR, 1, 1), date(YEAR, 6, 30), "Arbeitgeber Eins"),
        _certificate(owner, date(YEAR, 3, 1), date(YEAR, 12, 31), "Arbeitgeber Zwei"),
        _certificate(owner, date(YEAR, 5, 1), date(YEAR, 8, 31), "Arbeitgeber Drei"),
    ]
    got = draft_n(
        OwnerReturnFacts(owner_id=owner, inputs=rows),
        YEAR,
        rules_for(YEAR),
        registry.tariff(YEAR),
    )
    overlaps = [w for w in got.warnings if w.code is WarningCode.ANLAGE_N_PERIODS_OVERLAP]
    assert len(overlaps) == 3  # 1–2, 1–3, 2–3
    assert len({(w.params["first"], w.params["second"]) for w in overlaps}) == 3
    ids = {r.id for r in rows}
    for w in overlaps:
        assert len({r.id for r in w.refs if r.id in ids}) == 2  # the pair's rows (and documents)


def test_the_kindergeld_to_do_names_its_child_and_ends_once_typed():
    born = date(YEAR, 5, 12)
    _, warnings = child_months(ChildFact(born_on=born), YEAR, index=2)
    (todo,) = [w for w in warnings if w.code is WarningCode.RETURN_KINDERGELD_APPLY]
    assert todo.params == {"born_month": "2026-05", "child": "2"}
    applied = ChildFact(born_on=born, kindergeld_from=date(YEAR, 6, 1))
    months, warnings = child_months(applied, YEAR, index=2)
    assert months == 8  # the entitlement still runs from the birth month
    assert not [w for w in warnings if w.code is WarningCode.RETURN_KINDERGELD_APPLY]
