"""rules.property_equity: the own money put into a property (Übersicht, Luis 21 Nov 2026).
Pure; invented values only."""

from datetime import date
from decimal import Decimal
from uuid import uuid4

from hausbuch.rules.property_equity import own_loan_costs, paid_in, total
from hausbuch.schemas.enums import AcquisitionCostKind
from hausbuch.schemas.tax import AfaCostRow, TaxInputFact, TaxInputKind

TODAY = date(2026, 11, 21)
PID = uuid4()


def _row(amount: str) -> AfaCostRow:
    return AfaCostRow(id=uuid4(), kind=AcquisitionCostKind.NOTARY, amount=Decimal(amount))


def _private(
    amount: str,
    *,
    cost_class: str = "financing",
    share: tuple[int, int] = (1, 1),
    supersedes=None,
    withdrawn: bool = False,
    row_id=None,
) -> TaxInputFact:
    return TaxInputFact.model_validate(
        {
            "id": row_id or uuid4(),
            "property_id": PID,
            "kind": TaxInputKind.PRIVATE_COST,
            "tax_year": 2026,
            "value": {
                "cost_class": cost_class,
                "label": "beleg",
                "amount": amount,
                "share": {"numerator": share[0], "denominator": share[1]},
            },
            "supersedes_id": supersedes,
            "withdrawn": withdrawn,
        }
    )


def _paid_in(**kw):
    args = {
        "property_id": PID,
        "label": "Wohnung A",
        "purchase_price": Decimal("325000.00"),
        "cost_rows": [_row("11374.00"), _row("347.50")],
        "tax_inputs": [_private("1530.97"), _private("635.00")],
        "allocations": [],
        "loan_principals": [Decimal("308700.00")],
        "ownership_ended_on": None,
        "today": TODAY,
    }
    return paid_in(**(args | kw))


def test_the_worked_example():
    result = _paid_in()
    assert (
        result.purchase_price,
        result.acquisition_costs,
        result.own_loan_costs,
        result.loan_principal,
        result.paid_in,
        result.reason,
    ) == (
        Decimal("325000.00"),
        Decimal("11721.50"),
        Decimal("2165.97"),
        Decimal("308700.00"),
        Decimal("30187.47"),
        None,
    )


def test_only_financing_rows_in_force_count_at_their_share():
    old, ended = uuid4(), uuid4()
    rows = [
        _private("700.00", row_id=old),
        _private("635.00", supersedes=old),  # the correction replaces 700.00
        _private("80.00", row_id=ended),
        _private("80.00", supersedes=ended, withdrawn=True),
        _private("99.00", cost_class="postage"),
        _private("200.00", share=(1, 2)),
    ]
    assert own_loan_costs(rows) == Decimal("735.00")


def test_without_a_price_the_property_has_no_paid_in_and_is_left_out():
    result = _paid_in(purchase_price=None)
    assert (result.paid_in, result.reason) == (None, "no_purchase_price")
    assert result.acquisition_costs == Decimal("11721.50")  # the parts that are known stay
    other = _paid_in(property_id=uuid4())
    assert total([result, other]).total_paid_in == Decimal("30187.47")


def test_a_sold_property_is_left_out_as_the_net_worth_leaves_it():
    result = _paid_in(ownership_ended_on=TODAY)
    assert (result.paid_in, result.reason) == (None, "ownership_ended")
    assert _paid_in(ownership_ended_on=date(2026, 11, 22)).paid_in == Decimal("30187.47")


def test_a_loan_above_price_and_costs_gives_a_negative_figure():
    result = _paid_in(loan_principals=[Decimal("300000.00"), Decimal("50000.00")])
    assert result.loan_principal == Decimal("350000.00")
    assert result.paid_in == Decimal("-11112.53")


def test_no_property_totals_zero():
    assert total([]).total_paid_in == Decimal("0.00")
