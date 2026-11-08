"""Matching, allocations, learned rules and the review answer as hausbuch_app under RLS
(phase 2 step 4, docs/phases/phase-2.md §8, D23, D25, D26, D35, D38, D39, review F3, F6).

No model here: match_line runs with ai_available=False (jobs.matching, tested in
test_jobs_matching.py, adds the model calls)."""

import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from db.matching_support import (
    LOAN_IBAN,
    OTHER_TENANT_IBAN,
    OWNER_IBAN,
    PAINTER_IBAN,
    STADT_IBAN,
    TENANT_IBAN,
    WEG_IBAN,
    World,
)
from sqlalchemy import select

from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import matching_rule as rule_table
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.schemas.api import ConfirmAllocationsBody, InboxItemKind
from hausbuch.schemas.enums import AllocationStatus, ImportStatus
from hausbuch.schemas.property import AcquisitionCostCreate, UnitCreate
from hausbuch.services import allocations, matching, review, rules_learned
from hausbuch.services.properties import add_acquisition_cost, list_acquisition_costs
from hausbuch.services.units import create_unit

pytestmark = pytest.mark.db

C = AllocationCategory


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0])


def _match(world: World, tx_id, *, rematch=False):
    return world.cmd(
        matching.match_line, tx_id, iban_key=TEST_IBAN_KEY, ai_available=False, rematch=rematch
    )


def _rows(world: World, tx_id, *, all_rows=False):
    def q(conn):
        query = select(allocation_table).where(allocation_table.c.transaction_id == tx_id)
        if not all_rows:
            query = query.where(allocation_table.c.status != "superseded")
        return conn.execute(query.order_by(allocation_table.c.recorded_at)).all()

    return world.query(q)


def _events(world: World, entity_id):
    def q(conn):
        return conn.execute(
            select(event_table.c.type, event_table.c.payload, event_table.c.effective_date)
            .where(event_table.c.entity_id == entity_id)
            .order_by(event_table.c.seq)
        ).all()

    return world.query(q)


def _answer(tx_id, *parts: tuple[str, AllocationCategory], **scope) -> AllocationSet:
    return AllocationSet(
        transaction_id=tx_id,
        allocations=[
            AllocationDraft(amount=Decimal(amount), category=category, **scope)
            for amount, category in parts
        ],
        method=AllocationMethod.USER,
        confidence=Decimal("1.000"),
    )


def _put(world: World, tx_id, answer: AllocationSet, **body):
    return world.cmd(
        review.allocate_transaction,
        tx_id,
        ConfirmAllocationsBody(allocation_set=answer, **body),
        iban_key=TEST_IBAN_KEY,
    )


def _sum(rows) -> Decimal:
    return sum((r.amount for r in rows), Decimal("0.00"))


# --- Deterministic matches and routing -----------------------------------------------


def test_owner_transfer_is_auto_confirmed_with_an_event_without_tax_year(world):
    world.owner_account()
    tx = world.line(date(2024, 6, 3), "500.00", iban=OWNER_IBAN, purpose="Einlage")
    plan = _match(world, tx)
    assert plan.outcome == "written"
    rows = _rows(world, tx)
    assert [(r.category, r.status, r.method) for r in rows] == [
        ("owner_transfer_in", "confirmed", "matcher_owner")
    ]
    assert rows[0].confirmed_at is not None and rows[0].confirmed_by is None
    (event,) = _events(world, tx)
    assert event.type == "transaction.allocated"
    assert event.effective_date == date(2024, 6, 3)
    assert event.payload["auto"] is True
    assert "tax_year" not in str(event.payload)
    assert all("tax_year" not in part for part in event.payload["allocations"])


def test_exact_rent_auto_confirms_split(world):
    tenancy = world.tenancy()
    tx = world.line(date(2025, 1, 3), "1280.00", iban=TENANT_IBAN, purpose="Miete 01/2025")
    _match(world, tx)
    rows = _rows(world, tx)
    assert sorted((r.category, r.amount) for r in rows) == [
        ("operating_costs_prepayment", Decimal("230.00")),
        ("rent_cold", Decimal("1050.00")),
    ]
    assert {r.tenancy_id for r in rows} == {tenancy}
    assert {r.status for r in rows} == {"confirmed"}
    assert _sum(rows) == Decimal("1280.00")


def test_short_rent_goes_to_review_with_the_matchers_question(world):
    world.tenancy()
    tx = world.line(date(2025, 1, 3), "1260.00", iban=TENANT_IBAN, purpose="Miete 01/2025")
    _match(world, tx)
    rows = _rows(world, tx)
    assert {r.status for r in rows} == {"proposed"}
    assert "statt" in rows[0].question
    assert _events(world, tx) == []  # proposed rows are review items, no event
    items = world.query(review.inbox)
    assert [(i.kind, i.transaction_id) for i in items] == [(InboxItemKind.ALLOCATION_PROPOSED, tx)]


def test_rent_for_a_period_already_booked_goes_to_review(world):
    """Review F3: a December rent paid in January with no period in the purpose would get
    the booking month; when that month's rent is already confirmed, it is not auto-booked."""
    world.tenancy()
    first = world.line(date(2025, 1, 2), "1280.00", iban=TENANT_IBAN, purpose="Miete 01/2025")
    _match(world, first)
    second = world.line(date(2025, 1, 9), "1280.00", iban=TENANT_IBAN, purpose="Miete")
    _match(world, second)
    rows = _rows(world, second)
    assert {r.status for r in rows} == {"proposed"}
    assert rows[0].question == matching.RENT_BOOKED_QUESTION


def test_two_equal_rents_matched_at_once_book_the_period_only_once(world, monkeypatch):
    """Review F3 across lines: two jobs matching two equal rent lines at the same time each
    lock only their own line, so both would read "nothing booked yet" and both auto-confirm.
    The advisory lock on the tenancy in _guarded makes the second wait for the first's
    commit; it then sees the booked period and goes to review."""
    world.tenancy()
    lines = [
        world.line(date(2025, 1, 2), "1280.00", iban=TENANT_IBAN, purpose="Miete 01/2025"),
        world.line(date(2025, 1, 3), "1280.00", iban=TENANT_IBAN, purpose="Miete 01/2025"),
    ]
    barrier = threading.Barrier(2, timeout=20)
    original = matching.load_line

    def load_then_wait(conn, transaction_id, **kwargs):
        line = original(conn, transaction_id, **kwargs)
        barrier.wait()  # both lines are loaded (and row-locked) before either job commits
        return line

    monkeypatch.setattr(matching, "load_line", load_then_wait)
    with ThreadPoolExecutor(max_workers=2) as pool:
        plans = [f.result(timeout=60) for f in [pool.submit(_match, world, tx) for tx in lines]]
    assert [p.outcome for p in plans] == ["written", "written"]
    statuses = sorted(",".join(sorted({r.status for r in _rows(world, tx)})) for tx in lines)
    assert statuses == ["confirmed", "proposed"]
    (proposed,) = [tx for tx in lines if {r.status for r in _rows(world, tx)} == {"proposed"}]
    assert {r.question for r in _rows(world, proposed)} == {matching.RENT_BOOKED_QUESTION}


def test_loan_annuity_splits_and_a_second_debit_for_the_same_instalment_is_reviewed(world):
    loan = world.loan()
    first = world.line(date(2024, 5, 2), "-1400.00", iban=LOAN_IBAN, purpose="Rate")
    _match(world, first)
    rows = _rows(world, first)
    assert sorted((r.category, r.amount) for r in rows) == [
        ("loan_interest", Decimal("-900.00")),
        ("loan_principal", Decimal("-500.00")),
    ]
    assert {r.loan_id for r in rows} == {loan} and {r.status for r in rows} == {"confirmed"}
    second = world.line(date(2024, 5, 6), "-1400.00", iban=LOAN_IBAN, purpose="Rate")
    _match(world, second)
    rows = _rows(world, second)
    assert {r.status for r in rows} == {"proposed"}
    assert rows[0].question == matching.LOAN_BOOKED_QUESTION


def test_unreconciled_import_is_refused_by_matching_and_by_the_person(world):
    world.owner_account()
    tx = world.line(date(2024, 6, 3), "500.00", iban=OWNER_IBAN)
    world.set_import_status(ImportStatus.UNRECONCILED)
    plan = _match(world, tx)
    assert (plan.outcome, plan.reason) == ("skipped", "routing.import_unreconciled")
    assert _rows(world, tx) == []
    with pytest.raises(RuleError) as err:
        _put(world, tx, _answer(tx, ("500.00", C.OWNER_TRANSFER_IN)))
    assert err.value.code == review.IMPORT_NOT_RECONCILED
    assert _rows(world, tx) == []


def test_a_matched_line_is_not_matched_again(world):
    world.owner_account()
    tx = world.line(date(2024, 6, 3), "500.00", iban=OWNER_IBAN)
    _match(world, tx)
    assert _match(world, tx).reason == "matching.already_allocated"
    assert len(_rows(world, tx, all_rows=True)) == 1


def test_unmatched_line_without_ai_stays_without_rows_and_is_listed(world):
    tx = world.line(date(2024, 6, 3), "-17.50", name="Unbekannt GmbH")
    plan = _match(world, tx)
    assert plan.outcome == "skipped" and plan.reason == "routing.no_match"
    assert _rows(world, tx) == []
    (item,) = world.query(review.inbox)
    assert (item.kind, item.transaction_id) == (InboxItemKind.UNMATCHED, tx)


# --- Documents ---------------------------------------------------------------------------


def _invoice(world: World, *, subject="Kaufvertrag", subtype=None, amount="1234.56", **kw):
    fields = {
        "title": kw.pop("title", "Notarrechnung"),
        "document_date": "2026-03-10",
        "sender": "Notarin Dr. Beispiel",
        "subject": subject,
        "amount_total": amount,
        "reference": "R 8817",
        "iban": PAINTER_IBAN,
    }
    return world.document("invoice", fields, subtype=subtype, **kw)


def test_financing_invoice_payment_books_loan_fees_on_the_loan(world):
    loan = world.loan()
    doc = _invoice(world, subject="Grundschuldbestellung")
    tx = world.line(date(2026, 3, 16), "-1234.56", iban=PAINTER_IBAN, purpose="R 8817")
    _match(world, tx)
    (row,) = _rows(world, tx)
    assert (row.category, row.loan_id, row.document_id, row.status) == (
        "loan_fees",
        loan,
        doc,
        "proposed",  # loan_fees never auto-confirms
    )
    result = _put(
        world,
        tx,
        _answer(
            tx,
            ("-1234.56", C.LOAN_FEES),
            property_id=world.property_id,
            loan_id=loan,
            document_id=doc,
        ),
    )
    assert {a.status for a in result.allocations} == {AllocationStatus.CONFIRMED}
    assert result.allocations[0].method is AllocationMethod.DOCUMENT_MATCH  # unchanged: kept
    assert world.query(list_acquisition_costs, world.property_id) == []


def test_nebenkosten_invoice_confirmation_sets_paid_on(world):
    doc = _invoice(world, subtype="cost_basis_evidence")
    cost = world.cmd(
        add_acquisition_cost,
        AcquisitionCostCreate(
            property_id=world.property_id,
            kind="notary",
            amount=Decimal("1234.56"),
            document_id=doc,
        ),
    )
    tx = world.line(date(2026, 3, 18), "-1234.56", name="Notarin", purpose="Rechnung R 8817")
    _match(world, tx)
    (row,) = _rows(world, tx)
    assert (row.category, row.status) == ("acquisition_cost", "proposed")
    _put(
        world,
        tx,
        _answer(
            tx, ("-1234.56", C.ACQUISITION_COST), property_id=world.property_id, document_id=doc
        ),
    )
    (stored,) = world.query(list_acquisition_costs, world.property_id)
    assert stored.id == cost.id and stored.paid_on == date(2026, 3, 18)
    assert "property.acquisition_cost_recorded" in [
        e.type for e in _events(world, world.property_id)
    ]


def test_other_invoice_is_a_beleg_without_category_and_is_used_once(world):
    doc = _invoice(world, subject="Malerarbeiten", title="Malerrechnung")
    first = world.line(date(2026, 3, 12), "-1234.56", iban=PAINTER_IBAN)
    _match(world, first)
    (row,) = _rows(world, first)
    assert (row.category, row.document_id, row.status) == ("uncategorized", doc, "proposed")
    _put(
        world,
        first,
        _answer(
            tx_id := first, ("-1234.56", C.REPAIRS), property_id=world.property_id, document_id=doc
        ),
    )
    assert tx_id == first
    second = world.line(date(2026, 3, 14), "-1234.56", iban=PAINTER_IBAN)
    _match(world, second)
    assert all(r.document_id != doc for r in _rows(world, second))


def test_grundsteuer_installment_is_matched_and_auto_confirmed(world):
    doc = world.document(
        "grundsteuerbescheid",
        {
            "tax_year": 2024,
            "reference": "47110815",
            "yearly_amount": "493.80",
            "due_dates": ["15.02.", "15.05.", "15.08.", "15.11."],
            "iban": STADT_IBAN,
        },
        type="bescheid",
        subtype="grundsteuerbescheid",
    )
    tx = world.line(date(2025, 5, 15), "-123.45", iban=STADT_IBAN, purpose="Grundsteuer")
    _match(world, tx)
    (row,) = _rows(world, tx)
    assert (row.category, row.document_id, row.status, row.recurring) == (
        "grundsteuer",
        doc,
        "confirmed",
        True,
    )


def test_grundsteuer_without_due_dates_gives_no_document_facts(world):
    world.document(
        "grundsteuerbescheid",
        {"tax_year": 2024, "installment_amount": "123.45", "iban": STADT_IBAN},
        type="bescheid",
    )
    context = world.query(matching.build_context, iban_key=TEST_IBAN_KEY, today=date(2026, 11, 7))
    assert context.documents == []


# --- The person's answer -------------------------------------------------------------------


def test_sum_is_checked_on_the_users_path_and_on_record_set(world):
    tx = world.line(date(2024, 6, 3), "-50.00", name="Versicherung")
    with pytest.raises(RuleError) as err:
        _put(world, tx, _answer(tx, ("-49.99", C.INSURANCE)))
    assert err.value.code == "allocation.sum_mismatch"
    machine = _answer(tx, ("-25.00", C.INSURANCE), ("-25.01", C.BANK_FEES)).model_copy(
        update={"method": AllocationMethod.DOCUMENT_MATCH}
    )
    with pytest.raises(RuleError):
        world.cmd(allocations.record_set, machine, confirm=False, auto=False)
    assert _rows(world, tx, all_rows=True) == []


def test_a_correction_supersedes_and_records_reallocated(world):
    tx = world.line(date(2024, 6, 3), "-50.00", name="Versicherung")
    first = _put(world, tx, _answer(tx, ("-50.00", C.INSURANCE)), learn_rule=False)
    old = first.allocations[0]
    second = _put(
        world,
        tx,
        _answer(tx, ("-40.00", C.INSURANCE), ("-10.00", C.BANK_FEES)),
        learn_rule=False,
    )
    rows = _rows(world, tx, all_rows=True)
    superseded = [r for r in rows if r.status == "superseded"]
    assert [r.id for r in superseded] == [old.id]
    assert superseded[0].confirmed_at == old.confirmed_at  # never restamped
    current = [r for r in rows if r.status != "superseded"]
    assert _sum(current) == Decimal("-50.00")
    assert {r.supersedes_id for r in current} == {old.id}
    assert {a.method for a in second.allocations} == {AllocationMethod.USER}
    types = [e.type for e in _events(world, tx)]
    assert types == ["transaction.allocated", "transaction.reallocated"]
    assert _events(world, tx)[1].payload["supersedes"] == [str(old.id)]


def test_the_bodys_provenance_is_not_trusted(world):
    tx = world.line(date(2024, 6, 3), "-50.00", name="Versicherung")
    claimed = _answer(tx, ("-50.00", C.INSURANCE)).model_copy(
        update={"method": AllocationMethod.MATCHER_OWNER, "confidence": Decimal("0.100")}
    )
    result = _put(world, tx, claimed, learn_rule=False)
    (row,) = result.allocations
    assert (row.method, row.confidence) == (AllocationMethod.USER, Decimal("1.000"))


# --- Learned rules -------------------------------------------------------------------------


def test_an_answer_becomes_a_rule_and_the_next_line_is_never_asked(world):
    tx = world.line(date(2024, 6, 3), "-312.40", iban=WEG_IBAN, purpose="Gebäudeversicherung")
    view = world.query(
        review.transaction_allocations, tx, iban_key=TEST_IBAN_KEY, today=date(2026, 11, 7)
    )
    assert view.can_learn_rule and view.allocations == []
    result = _put(world, tx, _answer(tx, ("-312.40", C.INSURANCE)))  # learn_rule: default
    assert result.rule is not None and result.rule.created_from_allocation_id is not None
    (created,) = [e for e in _events(world, result.rule.id) if e.type == "rule.created"]
    assert created.payload["has_purpose_pattern"] is False
    assert "pattern" not in {k for k in created.payload if not k.startswith("has_")}

    later = world.line(date(2024, 7, 3), "-312.40", iban=WEG_IBAN, purpose="Versicherung")
    _match(world, later)
    rows = _rows(world, later)
    assert [(r.category, r.method, r.status) for r in rows] == [("insurance", "rule", "confirmed")]
    assert world.query(review.inbox) == []
    (stored,) = world.query(lambda c: c.execute(select(rule_table)).all())
    assert stored.hit_count == 1


def test_never_auto_category_defaults_off_but_can_be_asked_for(world):
    tx = world.line(date(2024, 6, 3), "-450.00", iban=PAINTER_IBAN)
    off = _put(world, tx, _answer(tx, ("-450.00", C.REPAIRS)))
    assert off.rule is None and off.learn_rule_default is False
    tx2 = world.line(date(2024, 6, 4), "-80.00", iban=PAINTER_IBAN)
    on = _put(world, tx2, _answer(tx2, ("-80.00", C.REPAIRS)), learn_rule=True)
    assert on.rule is not None


def test_no_iban_only_rule_for_a_tenants_counterparty(world):
    """D38: a Kaution from the tenant must not become an IBAN-only rule; a typed pattern can
    narrow it, and asking for a rule that cannot be learned is refused, not ignored."""
    world.tenancy()
    tx = world.line(date(2024, 1, 2), "3000.00", iban=TENANT_IBAN, purpose="Kaution")
    view = world.query(
        review.transaction_allocations, tx, iban_key=TEST_IBAN_KEY, today=date(2026, 11, 7)
    )
    assert view.can_learn_rule is False
    answer = _answer(tx, ("3000.00", C.DEPOSIT_IN))
    with pytest.raises(RuleError) as err:
        _put(world, tx, answer, learn_rule=True)
    assert err.value.code == rules_learned.NOT_LEARNABLE
    assert _rows(world, tx) == []  # the whole answer was refused
    default = _put(world, tx, answer)
    assert default.rule is None and default.learn_rule_default is False
    tx2 = world.line(date(2024, 1, 3), "100.00", iban=TENANT_IBAN, purpose="Kaution Rest")
    narrowed = _put(
        world,
        tx2,
        _answer(tx2, ("100.00", C.DEPOSIT_IN)),
        learn_rule=True,
        purpose_pattern="kaution",
    )
    assert narrowed.rule is not None
    assert narrowed.rule.matcher.purpose_pattern == "kaution"


def test_a_name_pattern_alone_does_not_learn_a_matcher_owned_counterparty(world):
    """D38, review round 2: a counterparty-name pattern does not narrow a tenant's or the
    loan account's line (the name is the same on the rent), so learn_rule needs a purpose
    pattern there; asked with a name pattern only, the whole answer is refused."""
    world.tenancy()
    world.loan()
    tenant_tx = world.line(
        date(2024, 1, 2),
        "3000.00",
        iban=TENANT_IBAN,
        name="Erika Mustermieterin",
        purpose="Kaution",
    )
    loan_tx = world.line(
        date(2024, 6, 3), "-25.00", iban=LOAN_IBAN, name="Beispielbank AG", purpose="Kontoführung"
    )
    for tx, amount, category, name in [
        (tenant_tx, "3000.00", C.DEPOSIT_IN, "Mustermieterin"),
        (loan_tx, "-25.00", C.BANK_FEES, "Beispielbank"),
    ]:
        with pytest.raises(RuleError) as err:
            _put(
                world,
                tx,
                _answer(tx, (amount, category)),
                learn_rule=True,
                counterparty_name_pattern=name,
            )
        assert err.value.code == rules_learned.NOT_LEARNABLE
        assert _rows(world, tx) == []
    learned = _put(
        world,
        loan_tx,
        _answer(loan_tx, ("-25.00", C.BANK_FEES)),
        learn_rule=True,
        purpose_pattern="kontoführung",
    )
    assert learned.rule is not None
    assert learned.rule.matcher.purpose_pattern == "kontoführung"
    assert world.query(lambda conn: conn.execute(select(rule_table.c.id)).all()) != []


def test_an_invalid_pattern_is_refused(world):
    tx = world.line(date(2024, 6, 3), "-9.90", name="Sparkasse", purpose="Entgelt")
    with pytest.raises(RuleError) as err:
        _put(
            world,
            tx,
            _answer(tx, ("-9.90", C.BANK_FEES)),
            learn_rule=True,
            purpose_pattern="entgelt(",
        )
    assert err.value.code == rules_learned.INVALID_PATTERN
    assert world.query(lambda c: c.execute(select(rule_table)).all()) == []


def test_a_disabled_rule_is_not_applied(world):
    tx = world.line(date(2024, 6, 3), "-312.40", iban=WEG_IBAN)
    rule = _put(world, tx, _answer(tx, ("-312.40", C.INSURANCE)), learn_rule=True).rule
    world.cmd(rules_learned.disable_rule, rule.id)
    assert [e.type for e in _events(world, rule.id)] == ["rule.created", "rule.disabled"]
    later = world.line(date(2024, 7, 3), "-312.40", iban=WEG_IBAN)
    _match(world, later)
    assert _rows(world, later) == []


def test_a_newer_answer_replaces_the_rule_with_the_same_split(world):
    """The same answer ticked again on one matcher replaces the rule (the newer wins)."""
    tx = world.line(date(2024, 6, 3), "-312.40", iban=WEG_IBAN)
    first = _put(world, tx, _answer(tx, ("-312.40", C.INSURANCE)), learn_rule=True).rule
    tx2 = world.line(date(2025, 6, 3), "-330.10", iban=WEG_IBAN)
    second = _put(world, tx2, _answer(tx2, ("-330.10", C.INSURANCE)), learn_rule=True).rule
    enabled = world.query(rules_learned.enabled_rules)
    assert [r.id for r in enabled] == [second.id] and first.id != second.id
    assert [e.type for e in _events(world, first.id)] == ["rule.created", "rule.disabled"]


def test_the_same_category_with_another_scope_replaces_the_rule(world):
    """Review F4: "same split" is split method + category; a second rule of one category on
    one matcher (here with a property) replaces the first, so the two never cancel out."""
    tx = world.line(date(2024, 6, 3), "-312.40", iban=WEG_IBAN)
    first = _put(world, tx, _answer(tx, ("-312.40", C.INSURANCE)), learn_rule=True).rule
    tx2 = world.line(date(2025, 6, 3), "-330.10", iban=WEG_IBAN)
    second = _put(
        world,
        tx2,
        _answer(tx2, ("-330.10", C.INSURANCE), property_id=world.property_id),
        learn_rule=True,
    ).rule
    assert second.property_id == world.property_id
    assert [r.id for r in world.query(rules_learned.enabled_rules)] == [second.id]
    assert first.id != second.id


def test_correcting_a_line_a_rule_booked_retires_that_rule(world):
    """Review F2: the rule booked the line and the person answers it with another split and
    ticks the box: a correction, so the old rule goes, as before D49."""
    tx = world.line(date(2024, 6, 3), "-312.40", iban=WEG_IBAN)
    wrong = _put(world, tx, _answer(tx, ("-312.40", C.INSURANCE)), learn_rule=True).rule
    later = world.line(date(2024, 7, 3), "-50.00", iban=WEG_IBAN)
    _match(world, later)
    (row,) = _rows(world, later)
    assert (row.status, row.rule_id) == ("confirmed", wrong.id)
    right = _put(world, later, _answer(later, ("-50.00", C.OTHER_EXPENSE)), learn_rule=True).rule
    assert [r.id for r in world.query(rules_learned.enabled_rules)] == [right.id]
    assert [e.type for e in _events(world, wrong.id)] == ["rule.created", "rule.disabled"]


def test_answering_a_proposal_with_another_kind_keeps_the_rule(world):
    """Review F2: a line that held only a proposal (here the model's) was not booked by the
    rule, so an answer with another category is a second kind; both rules stay."""
    tx = world.line(date(2024, 6, 3), "-312.40", iban=WEG_IBAN)
    first = _put(world, tx, _answer(tx, ("-312.40", C.INSURANCE)), learn_rule=True).rule
    other = world.line(date(2024, 7, 3), "-50.00", iban=WEG_IBAN)
    world.cmd(
        allocations.record_set,
        _answer(other, ("-50.00", C.INSURANCE)).model_copy(
            update={"method": AllocationMethod.LLM, "confidence": Decimal("0.600")}
        ),
        confirm=False,
        auto=False,
    )
    second = _put(world, other, _answer(other, ("-50.00", C.OTHER_EXPENSE)), learn_rule=True).rule
    enabled = world.query(rules_learned.enabled_rules)
    assert {r.id for r in enabled} == {first.id, second.id} and len(enabled) == 2


def test_a_second_kind_on_the_same_matcher_keeps_both_rules(world):
    """D49: another category ticked on the same IBAN and sign is a second kind of payment,
    not a correction: both rules stay. Before D49 the second tick disabled the first rule
    (this test then asserted that), so two kinds could never both have a rule."""
    tx = world.line(date(2024, 6, 3), "-312.40", iban=WEG_IBAN)
    first = _put(world, tx, _answer(tx, ("-312.40", C.INSURANCE)), learn_rule=True).rule
    tx2 = world.line(date(2024, 7, 3), "-50.00", iban=WEG_IBAN)
    second = _put(world, tx2, _answer(tx2, ("-50.00", C.OTHER_EXPENSE)), learn_rule=True).rule
    enabled = world.query(rules_learned.enabled_rules)
    # One clock here: created_at ties and the id decides the order, so compare as a set.
    assert {r.id for r in enabled} == {first.id, second.id} and len(enabled) == 2


# --- Rules checked against the payer's history (D49) -----------------------------------------

FEE_QUESTION = (
    "Der Betrag weicht von der letzten Buchung ab (zuletzt 35,70 € als „Hausgeld, nicht "
    "umlagefähig (Verwaltung)“ gebucht). Stimmt die Zuordnung?"
)


def _hv(world: World, day: date, amount: str, purpose: str = "", **kw):
    """A line from the Hausverwaltung (WEG_IBAN here), which debits two kinds."""
    return world.line(day, amount, iban=WEG_IBAN, name="Muster Hausverwaltung", purpose=purpose)


def _tx(world: World, tx_id):
    return world.query(matching.load_line, tx_id).tx


def test_rule_history_reads_the_current_rows_of_the_same_payer_and_sign(world):
    corrected = _hv(world, date(2026, 2, 3), "-35.70")
    _put(
        world, corrected, _answer(corrected, ("-35.70", C.HAUSGELD_UMLAGEFAEHIG)), learn_rule=False
    )
    _put(world, corrected, _answer(corrected, ("-35.70", C.HAUSGELD_VERWALTUNG)), learn_rule=False)
    proposed = _hv(world, date(2026, 3, 2), "-380.00")
    world.cmd(
        allocations.record_set,
        _answer(proposed, ("-380.00", C.HAUSGELD_UMLAGEFAEHIG)).model_copy(
            update={"method": AllocationMethod.LLM, "confidence": Decimal("0.600")}
        ),
        confirm=False,
        auto=False,
    )
    refund = _hv(world, date(2026, 3, 5), "20.00")
    _put(world, refund, _answer(refund, ("20.00", C.OTHER_INCOME)), learn_rule=False)
    other = world.line(date(2026, 3, 6), "-35.70", iban=PAINTER_IBAN)
    _put(world, other, _answer(other, ("-35.70", C.OTHER_EXPENSE)), learn_rule=False)
    _hv(world, date(2026, 3, 7), "-12.00")  # no rows yet
    tx = _hv(world, date(2026, 4, 3), "-36.90")
    _put(world, tx, _answer(tx, ("-36.90", C.HAUSGELD_VERWALTUNG)), learn_rule=False)  # itself

    history = world.query(matching.rule_history, _tx(world, tx))
    assert [
        (h.transaction_id, h.amount, [(r.category, r.status) for r in h.rows]) for h in history
    ] == [
        (corrected, Decimal("-35.70"), [(C.HAUSGELD_VERWALTUNG, AllocationStatus.CONFIRMED)]),
        (proposed, Decimal("-380.00"), [(C.HAUSGELD_UMLAGEFAEHIG, AllocationStatus.PROPOSED)]),
    ]


def test_two_kinds_from_one_iban_each_book_their_own_amount(world):
    """Luis's case (D49): the Hausverwaltung debits the Hausgeld and its fee from one IBAN.
    With a rule per kind, each line goes to the rule whose last amount it has; a raised fee
    is proposed with a question, and once confirmed the new amount books itself."""
    hg = _hv(world, date(2026, 2, 2), "-380.00", "Wohngeld 02/2026")
    fee = _hv(world, date(2026, 2, 3), "-35.70", "Verwaltergebuehr 02/2026")
    hausgeld = _put(
        world, hg, _answer(hg, ("-380.00", C.HAUSGELD_UMLAGEFAEHIG)), learn_rule=True
    ).rule
    fee_rule = _put(
        world, fee, _answer(fee, ("-35.70", C.HAUSGELD_VERWALTUNG)), learn_rule=True
    ).rule
    assert {r.id for r in world.query(rules_learned.enabled_rules)} == {hausgeld.id, fee_rule.id}

    hg2 = _hv(world, date(2026, 3, 2), "-380.00", "Wohngeld 03/2026")
    fee2 = _hv(world, date(2026, 3, 3), "-35.70", "Verwaltergebuehr 03/2026")
    for tx, rule, category in (
        (hg2, hausgeld, "hausgeld_umlagefaehig"),
        (fee2, fee_rule, "hausgeld_verwaltung"),
    ):
        _match(world, tx)
        (row,) = _rows(world, tx)
        assert (row.category, row.status, row.method, row.rule_id) == (
            category,
            "confirmed",
            "rule",
            rule.id,
        )

    raised = _hv(world, date(2026, 4, 3), "-36.90", "Verwaltergebuehr 04/2026")
    plan = _match(world, raised)
    assert (plan.outcome, plan.reason) == ("written", "routing.review")
    (row,) = _rows(world, raised)
    assert (row.category, row.status, row.method, row.rule_id, row.confidence) == (
        "hausgeld_verwaltung",
        "proposed",
        "rule",
        fee_rule.id,
        Decimal("0.500"),
    )
    assert row.question == FEE_QUESTION
    hits = world.query(lambda c: c.execute(select(rule_table.c.id, rule_table.c.hit_count)).all())
    assert dict(hits) == {hausgeld.id: 1, fee_rule.id: 1}  # a proposal is not a booking

    _put(world, raised, _answer(raised, ("-36.90", C.HAUSGELD_VERWALTUNG)), learn_rule=False)
    may = _hv(world, date(2026, 5, 4), "-36.90", "Verwaltergebuehr 05/2026")
    _match(world, may)
    (row,) = _rows(world, may)
    assert (row.category, row.status, row.rule_id) == (
        "hausgeld_verwaltung",
        "confirmed",
        fee_rule.id,
    )


def test_a_rules_near_miss_does_not_preempt_a_matcher(world):
    """D49: the order of §8.1 stays; a document match wins over a rule's proposal."""
    painted = world.line(date(2026, 1, 12), "-450.00", iban=PAINTER_IBAN)
    rule = _put(world, painted, _answer(painted, ("-450.00", C.REPAIRS)), learn_rule=True).rule
    other = world.line(date(2026, 2, 12), "-80.00", iban=PAINTER_IBAN)
    _put(world, other, _answer(other, ("-80.00", C.OTHER_EXPENSE)), learn_rule=False)
    doc = _invoice(world, subject="Malerarbeiten")
    tx = world.line(date(2026, 3, 16), "-1234.56", iban=PAINTER_IBAN, purpose="R 8817")
    _match(world, tx)
    (row,) = _rows(world, tx)
    assert (row.method, row.document_id, row.rule_id) == ("document_match", doc, None)
    assert rule is not None


def test_the_box_asks_about_other_waiting_amounts_and_the_flag_books_only_this_one(world):
    """D49 (8): the panel lists the other amounts waiting from the IBAN; answering "Nein,
    nur Zahlungen über 35,70 €" learns a same_amount_only rule, which books that amount
    and leaves any other alone (single-kind history: no proposal, D49 (5))."""
    fee = _hv(world, date(2026, 2, 3), "-35.70", "Verwaltergebuehr 02/2026")
    hg = _hv(world, date(2026, 2, 2), "-380.00", "Wohngeld 02/2026")
    _hv(world, date(2026, 3, 2), "-380.00", "Wohngeld 03/2026")
    fee2 = _hv(world, date(2026, 3, 3), "-35.70", "Verwaltergebuehr 03/2026")
    _hv(world, date(2026, 3, 10), "-12.00")
    _hv(world, date(2026, 3, 11), "50.00")  # the other sign
    booked = _hv(world, date(2026, 1, 5), "-99.00")  # confirmed: not waiting
    _put(world, booked, _answer(booked, ("-99.00", C.HAUSGELD_VERWALTUNG)), learn_rule=False)
    # The history is single-kind (only hausgeld_verwaltung), so without the flag the rule
    # would book any amount; with it, only the latest one (35,70 €) stands.

    view = world.query(
        review.transaction_allocations, fee, iban_key=TEST_IBAN_KEY, today=date(2026, 11, 8)
    )
    assert view.can_learn_rule
    assert [(w.amount, w.count) for w in view.waiting_other_amounts] == [
        (Decimal("-380.00"), 2),
        (Decimal("-12.00"), 1),
    ]
    result = _put(
        world,
        fee,
        _answer(fee, ("-35.70", C.HAUSGELD_VERWALTUNG)),
        learn_rule=True,
        same_amount_only=True,
    )
    assert result.rule is not None and result.rule.same_amount_only is True
    (created,) = [e for e in _events(world, result.rule.id) if e.type == "rule.created"]
    assert created.payload["same_amount_only"] is True

    _match(world, fee2)
    (row,) = _rows(world, fee2)
    assert (row.status, row.rule_id) == ("confirmed", result.rule.id)
    plan = _match(world, hg)
    assert (plan.outcome, plan.reason) == ("skipped", "routing.no_match")
    assert _rows(world, hg) == []


def test_no_other_amounts_are_listed_without_a_learnable_rule(world):
    world.tenancy()
    tx = world.line(date(2024, 1, 2), "3000.00", iban=TENANT_IBAN, purpose="Kaution")
    world.line(date(2024, 2, 2), "1280.00", iban=TENANT_IBAN, purpose="Miete")
    view = world.query(
        review.transaction_allocations, tx, iban_key=TEST_IBAN_KEY, today=date(2026, 11, 8)
    )
    assert view.can_learn_rule is False and view.waiting_other_amounts == []


# --- Re-match on new evidence ---------------------------------------------------------------


def test_rematch_matches_an_open_line_once_the_invoice_arrives(world):
    tx = world.line(date(2026, 3, 12), "-1234.56", iban=PAINTER_IBAN)
    _match(world, tx)
    assert _rows(world, tx) == []
    doc = _invoice(world, subject="Malerarbeiten")
    ids = world.query(
        matching.lines_for_evidence, doc, iban_key=TEST_IBAN_KEY, today=date(2026, 11, 7)
    )
    assert ids == [tx]
    _match(world, tx, rematch=True)
    (row,) = _rows(world, tx)
    assert row.document_id == doc


def test_rematch_never_touches_a_persons_answer(world):
    tx = world.line(date(2026, 3, 12), "-1234.56", iban=PAINTER_IBAN)
    _put(world, tx, _answer(tx, ("-1234.56", C.REPAIRS)), learn_rule=False)
    _invoice(world, subject="Malerarbeiten")
    plan = _match(world, tx, rematch=True)
    assert plan.outcome == "skipped"
    assert len(_rows(world, tx, all_rows=True)) == 1


def test_a_new_wirtschaftsplan_resplits_rule_rows_as_a_supersession(world):
    old_plan = {
        "tax_year": 2024,
        "valid_from": "2024-01-01",
        "hausgeld_total_monthly": "380.00",
        "hausgeld_umlagefaehig_monthly": "210.00",
        "hausgeld_verwaltung_monthly": "50.00",
        "hausgeld_ruecklage_monthly": "120.00",
    }
    world.document("wirtschaftsplan", old_plan, type="letter")
    first = world.line(date(2024, 3, 1), "-380.00", iban=WEG_IBAN, purpose="Hausgeld 03/2024")
    answer = _answer(
        first,
        ("-210.00", C.HAUSGELD_UMLAGEFAEHIG),
        ("-50.00", C.HAUSGELD_VERWALTUNG),
        ("-120.00", C.HAUSGELD_RUECKLAGE),
        property_id=world.property_id,
    )
    learned = _put(world, first, answer, learn_rule=True).rule
    assert learned.split_method.value == "by_wirtschaftsplan"
    april = world.line(date(2024, 4, 1), "-380.00", iban=WEG_IBAN, purpose="Hausgeld 04/2024")
    _match(world, april)
    assert {r.method for r in _rows(world, april)} == {"rule"}

    new_plan = old_plan | {
        "valid_from": "2024-04-01",
        "hausgeld_umlagefaehig_monthly": "200.00",
        "hausgeld_ruecklage_monthly": "130.00",
    }
    doc = world.document("wirtschaftsplan", new_plan, type="letter")
    ids = world.query(
        matching.lines_for_evidence, doc, iban_key=TEST_IBAN_KEY, today=date(2026, 11, 7)
    )
    assert april in ids and first not in ids
    _match(world, april, rematch=True)
    rows = _rows(world, april)
    assert sorted((r.category, r.amount) for r in rows) == [
        ("hausgeld_ruecklage", Decimal("-130.00")),
        ("hausgeld_umlagefaehig", Decimal("-200.00")),
        ("hausgeld_verwaltung", Decimal("-50.00")),
    ]
    assert len(_rows(world, april, all_rows=True)) == 6
    assert [e.type for e in _events(world, april)] == [
        "transaction.allocated",
        "transaction.reallocated",
    ]


def test_other_workspace_cannot_see_or_answer_a_line(world, app_engine, fresh_workspaces):
    tx = world.line(date(2024, 6, 3), "-50.00", name="Versicherung")
    other = World(app_engine, fresh_workspaces[1])
    assert other.query(matching.load_line, tx) is None
    from hausbuch.services.errors import NotFoundError

    with pytest.raises(NotFoundError):
        _put(other, tx, _answer(tx, ("-50.00", C.INSURANCE)))


def test_other_tenants_name_is_not_in_the_categorize_input(world):
    world.tenancy(name="Erika Mustermieterin")
    second_unit = world.cmd(
        create_unit,
        UnitCreate(property_id=world.property_id, label="WE 4", floor_area_m2=Decimal("50.00")),
    ).id
    world.tenancy(name="Otto Zweitmieter", iban=OTHER_TENANT_IBAN, unit_id=second_unit)
    tx = world.line(
        date(2024, 6, 3),
        "-77.00",
        name="Hausmeister Kunz",
        purpose=f"Rechnung 12 IBAN {PAINTER_IBAN}",
    )
    line = world.query(matching.load_line, tx)
    data = world.query(matching.categorize_input, line)
    text = repr(data)
    for secret in ("Mustermieterin", "Zweitmieter", PAINTER_IBAN, TENANT_IBAN, "DE12 5001"):
        assert secret not in text
    assert data.purpose == "Rechnung 12 IBAN [IBAN]"
