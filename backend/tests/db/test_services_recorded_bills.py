"""Phase 6 D161 widened (14 Nov 2026) under hausbuch_app and RLS: a private line paying a
bill the property already records, as a Nebenkosten row (property_acquisition_cost) or a
private_cost row, is proposed as hh_property_cost with the property and the bill, becomes
the record's proof of payment and never a second record; the Anlage V figures stay the same
to the cent. Three shapes: a Grunderwerbsteuer notice of two buyers paid by two equal lines
(D150's parts), a Landesjustizkasse bill split into a Nebenkosten row and a financing row paid
by one line, a notary's financing bill paid weeks after its date. Also: a private payment to
the seller is proposed as hh_investment. Invented names, IBANs and amounts only."""

from datetime import date
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from db.matching_support import World
from db.test_cli_evals_report import _owner_url
from db.test_services_private_matching import NOW, Private
from sqlalchemy import select, text

from hausbuch import cli
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import property_acquisition_cost as cost_table
from hausbuch.db.tables import tax_input as tax_input_table
from hausbuch.rules import matching as matchers
from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.schemas.api import InboxItemKind
from hausbuch.schemas.common import Fraction
from hausbuch.schemas.enums import AccountPurpose, AcquisitionCostKind, PriceSplitSource
from hausbuch.schemas.property import AcquisitionCostCreate, PriceSplitCreate
from hausbuch.schemas.tax import PrivateCostValue, TaxInputCreate, TaxInputKind, WarningCode
from hausbuch.services import allocations, matching, property_costs, review, tax_inputs
from hausbuch.services import tax as tax_service
from hausbuch.services.properties import (
    add_acquisition_cost,
    correct_purchase_date,
    record_price_split,
    select_price_split,
)

pytestmark = pytest.mark.db

C = AllocationCategory
TODAY = NOW.date()
YEAR = 2026
SELLER_IBAN = "DE19100000000000000074"  # Bundesbank test bank code 10000000
LJK_IBAN = "DE89100000000000000075"


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    w = World(app_engine, fresh_workspaces[0], now=NOW)
    # The handover in 2026, so the Nebenkosten rows need their proof in the 2026 draft (D143),
    # and a split, so the AfA basis (which the Nebenkosten rows join) is a figure.
    w.cmd(correct_purchase_date, w.property_id, date(2026, 5, 1))
    split = w.cmd(
        record_price_split,
        PriceSplitCreate(
            property_id=w.property_id,
            source=PriceSplitSource.APPRAISAL,
            building_share=Decimal("0.750000"),
            evidence_document_id=w.document("invoice", {}, type="other"),
        ),
    )
    w.cmd(select_price_split, w.property_id, split.id)
    return w


@pytest.fixture
def private(world) -> Private:
    return Private(world)


# --- helpers ----------------------------------------------------------------------------------


def _match(world: World, tx):
    return world.cmd(matching.match_line, tx, iban_key=TEST_IBAN_KEY, ai_available=False)


def _rematch(world: World, doc):
    """What the queued rematch_on_evidence job does (jobs.matching.run_rematch_on_evidence)."""
    ids = world.query(matching.lines_for_evidence, doc, iban_key=TEST_IBAN_KEY, today=TODAY)
    for tx in ids:
        world.cmd(matching.match_line, tx, iban_key=TEST_IBAN_KEY, ai_available=False, rematch=True)
    return ids


def _queued(world: World, doc) -> int:
    return world.query(
        lambda c: c.execute(
            text(
                "SELECT count(*) FROM procrastinate_jobs WHERE task_name = 'rematch_on_evidence'"
                " AND args->>'document_id' = :doc AND status = 'todo'"
            ),
            {"doc": str(doc)},
        ).scalar_one()
    )


def _book(world: World, tx, amount: str, category: C, *, confirm: bool, **scope):
    return world.cmd(
        allocations.record_set,
        AllocationSet(
            transaction_id=tx,
            allocations=[AllocationDraft(amount=Decimal(amount), category=category, **scope)],
            method=AllocationMethod.USER,
            confidence=Decimal("1.000"),
        ),
        confirm=confirm,
        auto=False,
    )


def _rows(world: World, tx):
    return world.query(
        lambda c: c.execute(
            select(allocation_table)
            .where(
                allocation_table.c.transaction_id == tx,
                allocation_table.c.status != "superseded",
            )
            .order_by(allocation_table.c.id)
        ).all()
    )


def _acquisition(world: World, kind: AcquisitionCostKind, amount: str, doc):
    return world.cmd(
        add_acquisition_cost,
        AcquisitionCostCreate(
            property_id=world.property_id, kind=kind, amount=Decimal(amount), document_id=doc
        ),
    )


def _financing(world: World, amount: str, doc, label: str):
    return world.cmd(
        tax_inputs.create_tax_input,
        world.property_id,
        TaxInputCreate(
            property_id=world.property_id,
            kind=TaxInputKind.PRIVATE_COST,
            tax_year=YEAR,
            document_id=doc,
            value=PrivateCostValue(
                cost_class="financing",
                label=label,
                amount=Decimal(amount),
                share=Fraction(numerator=1, denominator=1),
                category=C.LOAN_FEES,
            ),
        ),
    )


def _record_counts(world: World) -> tuple[int, int]:
    """(Nebenkosten rows, private_cost rows): a linked line never adds either."""
    return world.query(
        lambda c: (
            len(c.execute(select(cost_table.c.id)).all()),
            len(
                c.execute(
                    select(tax_input_table.c.id).where(
                        tax_input_table.c.kind == TaxInputKind.PRIVATE_COST.value
                    )
                ).all()
            ),
        )
    )


def _draft(world: World):
    return world.query(tax_service.anlage_v_draft, world.property_id, tax_year=YEAR, today=TODAY)


def _figures(world: World):
    """Every euro figure of the year's draft (not its warnings or sources)."""
    d = _draft(world)
    return (
        sorted((line.key, line.amount, line.ticked) for line in d.lines),
        d.totals.model_dump(),
        None if d.afa is None else (d.afa.amount, d.afa.basis, d.afa.additions),
        [(o.owner_id, o.amount) for o in d.owners],
        None if d.limit_15 is None else (d.limit_15.used_net, d.limit_15.limit),
    )


def _missing(world: World) -> list[str]:
    return [
        w.params.get("document")
        for w in _draft(world).warnings
        if w.code is WarningCode.PAYMENT_PROOF_MISSING
    ]


def _cards(world: World, kind: InboxItemKind):
    return [i for i in world.query(review.inbox, TODAY) if i.kind is kind]


def _grest(world: World):
    """One document holding both buyers' Grunderwerbsteuer notices (D145): two cost lines
    that sum to the total."""
    return world.document(
        "bescheid",
        {
            "sender": "Finanzamt Beispielstadt",
            "document_date": "2026-04-02",
            "due_on": "2026-05-04",
            "amount_total": "9000.00",
            "reference": "GrESt 0815/26",
            "cost_lines": [
                "Grunderwerbsteuer Käufer A | 4.500,00 EUR | acquisition_cost",
                "Grunderwerbsteuer Käufer B | 4.500,00 EUR | acquisition_cost",
            ],
        },
        type="bescheid",
        title="Grunderwerbsteuerbescheid",
    )


def _ljk(world: World, *, iban: str | None = None):
    fields = {
        "sender": "Landesjustizkasse Beispielland",
        "document_date": "2026-05-10",
        "amount_total": "600.00",
        "reference": "KR 4711/26",
        "cost_lines": [
            "Eintragung des Eigentümers | 450,00 EUR | acquisition_cost",
            "Eintragung der Grundschuld | 150,00 EUR | loan_fees",
        ],
    }
    if iban is not None:
        fields["iban"] = iban
    return world.document("invoice", fields, title="Kostenrechnung Grundbuch")


def _notary(world: World):
    return world.document(
        "invoice",
        {
            "sender": "Notar Dr. Beispiel",
            "document_date": "2026-03-20",
            "amount_total": "714.00",
            "reference": "UR 123/2026",
        },
        title="Kostenberechnung Grundschuld",
    )


# --- shape 1: two buyers' Grunderwerbsteuer, two equal lines ----------------------------------


def test_two_equal_lines_each_pay_one_buyers_grunderwerbsteuer(world, private):
    doc = _grest(world)
    row = _acquisition(world, AcquisitionCostKind.REAL_ESTATE_TRANSFER_TAX, "9000.00", doc)
    assert _missing(world) == [str(doc)]
    before, counts = _figures(world), _record_counts(world)

    # Paid on one day, 24 days after the due date: outside D161's ± 10 days of the notice.
    first, second = (
        private.line(date(2026, 5, 28), "-4500.00", name="Finanzamt Beispielstadt", purpose=p)
        for p in ("Grunderwerbsteuer Käufer A", "Grunderwerbsteuer Käufer B")
    )
    for tx in (first, second):
        _match(world, tx)
        [booked] = _rows(world, tx)
        assert (booked.category, booked.status, booked.property_id, booked.document_id) == (
            "hh_property_cost",
            "proposed",  # principle 3: a person confirms
            world.property_id,
            doc,
        )
        assert booked.question == matchers.RECORDED_PART_QUESTION
    # A third equal line would pay more than the bill: no proposal for it.
    third = private.line(date(2026, 5, 28), "-4500.00", name="Finanzamt Beispielstadt")
    _match(world, third)
    assert all(r.document_id is None for r in _rows(world, third))

    world.cmd(allocations.confirm_proposed, first)
    assert _missing(world) == [str(doc)]  # one buyer's share proven, the other still open
    world.cmd(allocations.confirm_proposed, second)
    assert _missing(world) == []  # „Zahlungsnachweis hochladen“ is done

    assert _figures(world) == before
    assert _record_counts(world) == counts
    paid_on = world.query(
        lambda c: c.execute(select(cost_table.c.paid_on).where(cost_table.c.id == row.id))
    ).scalar_one()
    assert paid_on == date(2026, 5, 28)
    assert _cards(world, InboxItemKind.PROPERTY_COST_ROW) == []
    assert _cards(world, InboxItemKind.PROPERTY_COST_BILL) == []


def test_a_nebenkosten_row_recorded_later_picks_up_the_waiting_lines(world, private):
    """The lines wait in Zu prüfen (a model's hh_other proposal); recording the row queues a
    rematch of its bill, which proposes them."""
    doc = _grest(world)
    lines = [
        private.line(date(2026, 5, 28), "-4500.00", name="Finanzamt Beispielstadt")
        for _ in range(2)
    ]
    for tx in lines:
        _book(world, tx, "-4500.00", C.HH_OTHER, confirm=False)
    _acquisition(world, AcquisitionCostKind.REAL_ESTATE_TRANSFER_TAX, "9000.00", doc)
    assert _queued(world, doc) == 1
    assert set(lines) <= set(_rematch(world, doc))
    for tx in lines:
        [booked] = _rows(world, tx)
        assert (booked.category, booked.status, booked.document_id) == (
            "hh_property_cost",
            "proposed",
            doc,
        )


# --- shape 2: a Landesjustizkasse bill split into two records, one line ----------------------


def test_one_line_pays_a_bill_split_into_a_nebenkosten_row_and_a_financing_row(world, private):
    doc = _ljk(world)
    _acquisition(world, AcquisitionCostKind.LAND_REGISTRY, "450.00", doc)
    financing = _financing(world, "150.00", doc, "Grundschuld (Grundbuch)")
    assert _missing(world) == [str(doc)] * 2  # one to-do per part (D143)
    before, counts = _figures(world), _record_counts(world)

    tx = private.line(
        date(2026, 6, 25), "-600.00", name="Landesjustizkasse Beispielland", purpose="Kosten"
    )
    _match(world, tx)
    [booked] = _rows(world, tx)
    assert (booked.category, booked.status, booked.property_id, booked.document_id) == (
        "hh_property_cost",
        "proposed",
        world.property_id,
        doc,
    )
    assert booked.question == matchers.RECORDED_WHOLE_QUESTION
    world.cmd(allocations.confirm_proposed, tx)

    assert _missing(world) == []
    assert _figures(world) == before
    assert _record_counts(world) == counts
    # Path 2's „Übernehmen“ would write nothing: the bill's rows exist.
    assert [r.id for r in world.cmd(property_costs.record_proposed, tx)] == [financing.id]
    assert _record_counts(world) == counts
    assert _cards(world, InboxItemKind.PROPERTY_COST_ROW) == []


def test_a_bill_whose_nebenkosten_row_is_recorded_proposes_only_its_other_part(world, private):
    """The gap D159 amended named (§14.1): the bill has its Nebenkosten row but not yet its
    financing row; the paying line's card proposes the financing part only, never the
    Nebenkosten part a second time."""
    doc = _ljk(world, iban=LJK_IBAN)
    _acquisition(world, AcquisitionCostKind.LAND_REGISTRY, "450.00", doc)
    tx = private.line(
        date(2026, 5, 15), "-600.00", iban=LJK_IBAN, name="Landesjustizkasse", purpose="KR"
    )
    _match(world, tx)
    [card] = _cards(world, InboxItemKind.PROPERTY_COST_ROW)
    [proposed] = card.private_costs
    assert (proposed.value.amount, proposed.value.category, proposed.value.cost_class) == (
        Decimal("150.00"),
        C.LOAN_FEES,
        "financing",
    )
    nebenkosten, _ = _record_counts(world)
    world.cmd(allocations.confirm_proposed, tx)
    [written] = world.cmd(property_costs.record_proposed, tx)
    assert written.value.amount == Decimal("150.00")
    assert _record_counts(world) == (nebenkosten, 1)


# --- shape 3: a notary's financing bill paid weeks later -------------------------------------


def test_a_notary_bill_paid_weeks_after_its_date_is_proposed(world, private):
    """The cause of the real line sitting as the model's hh_other: D161's document match
    looks ± 10 days around the bill's date and needs its IBAN or reference; a notary's bill
    is paid weeks after it is dated, from a transfer naming neither. The recorded-bill match
    takes the payee and the wider window, since the amount equals a recorded row."""
    doc = _notary(world)
    tx = private.line(
        date(2026, 4, 28), "-714.00", name="Notar Dr. Beispiel", purpose="Kostenberechnung"
    )
    _match(world, tx)  # no record yet: nothing deterministic, the model's turn
    assert all(r.document_id is None for r in _rows(world, tx))
    _book(world, tx, "-714.00", C.HH_OTHER, confirm=False)  # the model's proposal

    _financing(world, "714.00", doc, "Notar Grundschuld")
    assert _missing(world) == [str(doc)]
    before, counts = _figures(world), _record_counts(world)
    assert _queued(world, doc) == 1
    _rematch(world, doc)
    [booked] = _rows(world, tx)
    assert (booked.category, booked.status, booked.property_id, booked.document_id) == (
        "hh_property_cost",
        "proposed",
        world.property_id,
        doc,
    )
    world.cmd(allocations.confirm_proposed, tx)
    assert _missing(world) == []
    assert _figures(world) == before
    assert _record_counts(world) == counts


def test_a_line_naming_no_payee_of_the_bill_is_not_proposed(world, private):
    doc = _notary(world)
    _financing(world, "714.00", doc, "Notar Grundschuld")
    tx = private.line(date(2026, 4, 28), "-714.00", name="Möbelhaus", purpose="Sofa")
    _match(world, tx)
    assert all(r.document_id is None for r in _rows(world, tx))


def test_a_line_outside_the_window_is_not_proposed(world, private):
    doc = _notary(world)
    _financing(world, "714.00", doc, "Notar Grundschuld")
    tx = private.line(date(2026, 6, 1), "-714.00", name="Notar Dr. Beispiel")  # day 73
    _match(world, tx)
    assert all(r.document_id is None for r in _rows(world, tx))


# --- what is never touched ---------------------------------------------------------------------


def test_a_line_a_person_confirmed_is_never_changed(world, private):
    doc = _notary(world)
    tx = private.line(date(2026, 4, 28), "-714.00", name="Notar Dr. Beispiel")
    _book(world, tx, "-714.00", C.HH_OTHER, confirm=True)
    [before] = _rows(world, tx)
    _financing(world, "714.00", doc, "Notar Grundschuld")
    _rematch(world, doc)
    [after] = _rows(world, tx)
    assert (after.id, after.category, after.status) == (before.id, "hh_other", "confirmed")


def test_the_rental_accounts_own_matching_is_unchanged(world, private):
    doc = _ljk(world)
    _acquisition(world, AcquisitionCostKind.LAND_REGISTRY, "450.00", doc)
    _financing(world, "150.00", doc, "Grundschuld (Grundbuch)")
    context = world.query(
        matching.build_context,
        iban_key=TEST_IBAN_KEY,
        today=TODAY,
        account_purpose=AccountPurpose.RENTAL,
    )
    assert (context.recorded_bills, context.purchase_payees) == ([], [])
    tx = world.line(date(2026, 6, 25), "-600.00", purpose="Landesjustizkasse Kosten")
    _match(world, tx)
    assert all(r.category != "hh_property_cost" for r in _rows(world, tx))


# --- a private payment to the seller -------------------------------------------------------


def test_a_private_payment_to_the_seller_is_a_down_payment(world, private):
    """The seller's IBAN received the purchase price from the rental account; a private
    line to it is proposed as hh_investment with the property, and the draft never reads it
    (the AfA basis comes from the property record, phase 3 D55)."""
    paid = world.line(date(2026, 4, 30), "-200000.00", iban=SELLER_IBAN, purpose="Kaufpreis")
    _book(world, paid, "-200000.00", C.PURCHASE_PRICE, confirm=True, property_id=world.property_id)
    before, counts = _figures(world), _record_counts(world)

    tx = private.line(date(2026, 3, 2), "-25000.00", iban=SELLER_IBAN, name="Verkäufer")
    _match(world, tx)
    [booked] = _rows(world, tx)
    assert (booked.category, booked.status, booked.property_id, booked.document_id) == (
        "hh_investment",
        "proposed",
        world.property_id,
        None,
    )
    assert booked.question == matchers.DOWN_PAYMENT_QUESTION
    world.cmd(allocations.confirm_proposed, tx)

    assert _figures(world) == before
    assert _record_counts(world) == counts
    for kind in (
        InboxItemKind.PROPERTY_COST_ROW,
        InboxItemKind.PROPERTY_COST_BILL,
        InboxItemKind.PROPERTY_COST_DOCUMENT,
    ):
        assert _cards(world, kind) == []
    # A credit from the seller is no payment to them.
    back = private.line(date(2026, 3, 9), "25.00", iban=SELLER_IBAN, name="Verkäufer")
    _match(world, back)
    assert all(r.category != "hh_investment" for r in _rows(world, back))


# --- the one-off command for lines already waiting -----------------------------------------


def test_the_command_counts_with_dry_run_and_then_queues_a_rematch_per_bill(
    dbname, world, private, capsys
):
    doc = _notary(world)
    _financing(world, "714.00", doc, "Notar Grundschuld")
    world.query(lambda c: c.execute(text("DELETE FROM procrastinate_jobs")))
    tx = private.line(date(2026, 4, 28), "-714.00", name="Notar Dr. Beispiel")
    _book(world, tx, "-714.00", C.HH_OTHER, confirm=False)  # waiting since before the deploy
    environ = {"HAUSBUCH_ADMIN_DATABASE_URL": _owner_url(dbname)}
    argv = ["rematch-bills", "--workspace", str(world.ws)]

    assert cli.main([*argv, "--dry-run"], environ) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "would queue a rematch for 1 recorded bill(s)" in out
    assert "1 waiting private line(s)" in out
    assert _queued(world, doc) == 0

    assert cli.main(argv, environ) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "queued a rematch for 1 recorded bill(s)" in out and "postgresql" not in out
    assert _queued(world, doc) == 1
    _rematch(world, doc)
    [booked] = _rows(world, tx)
    assert (booked.category, booked.status, booked.document_id) == (
        "hh_property_cost",
        "proposed",
        doc,
    )
