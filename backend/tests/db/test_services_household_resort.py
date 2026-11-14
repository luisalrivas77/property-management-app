"""The one-off re-sort into the subcategories (docs/phases/phase-6.md D159 amended 14 Nov 2026,
migration 0032): services.household_resort and the `hausbuch household-resort` command.

Every private line booked in a spending group's fallback („Sonstige Lebensmittel“, …),
proposed or confirmed, is asked once more; a finer spending answer goes back to Zu prüfen as a
proposal, and on a confirmed line it supersedes the confirmed row with
transaction.resort_proposed. The same answer, or one of another relevance, leaves a confirmed
line as it is. Split, scoped and possible purchase-cost lines are left alone, a rule learned on
a fallback is skipped while another rule still answers, nothing is ever auto-confirmed by the
model, and a second run asks nothing again. Then, for the person: confirming a re-sorted line
with „Künftig immer so zuordnen“ replaces the payee's fallback rule (D49), and „Diese auch auf
<Kategorie> umbuchen“ (D160 amended (2)) re-books the payee's lines the re-sort left confirmed.
Invented names and amounts only; the model is a FakeModelClient."""

import uuid
from datetime import date
from decimal import Decimal

import pytest
from db.matching_support import World
from db.test_cli_evals_report import _owner_url
from db.test_services_household_reask import _all_rows, _jobs, _propose
from db.test_services_private_matching import (
    DELIVERY,
    NOW,
    OWNER_A,
    Private,
    _answer,
    _answer_line,
    _answer_rebook,
    _consent_both,
    _deps,
    _events,
    _rematch,
    _rows,
)
from sqlalchemy import select, text

from hausbuch import cli
from hausbuch.ai.fake import FakeModelClient, text_response
from hausbuch.db.tables import matching_rule as rule_table
from hausbuch.jobs.matching import run_match_transaction
from hausbuch.rules.household.taxonomy import FALLBACK_CODES, HOUSEHOLD_SPENDING, fallback_of
from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.schemas.enums import AllocationStatus
from hausbuch.services import allocations, household_resort, matching, workspace_settings
from hausbuch.services.errors import RuleViolationError
from hausbuch.services.household_resort import ResortResult, resort_household

pytestmark = pytest.mark.db

C = AllocationCategory
GROCER_IBAN = "DE89370400440532013000"


@pytest.fixture(autouse=True)
def _no_leftover_jobs(app_engine):
    """These tests read procrastinate_jobs; the queue is session-wide (test_jobs_queue)."""

    def clear():
        with app_engine.begin() as conn:
            conn.execute(text("DELETE FROM procrastinate_jobs"))

    clear()
    yield
    clear()


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0], now=NOW)


@pytest.fixture
def private(world) -> Private:
    return Private(world)


def _result(**counts: int) -> ResortResult:
    zero = dict.fromkeys(ResortResult.__dataclass_fields__, 0)
    return ResortResult(**(zero | counts))


def _confirmed(world: World, tx, amount: str, category: str) -> None:
    """A line a person confirmed in `category` (without learning a rule)."""
    _answer_line(world, tx, amount, category, learn=False)


def _resort(world: World, tx, deps, *, attempt: int = 0):
    return run_match_transaction(
        deps, workspace_id=world.ws, transaction_id=tx, attempt=attempt, resort=True
    )


def _client(*answers: tuple[str, str]) -> FakeModelClient:
    return FakeModelClient([text_response(_answer(c, a)) for c, a in answers])


# --- The codes ------------------------------------------------------------------------------


def test_the_resort_targets_and_the_fallbacks():
    """HOUSEHOLD_SPENDING: the codes a confirmed line may be re-sorted into. Every fallback
    and subcategory is in; movements, income, tax-relevant codes, the card's charges and a
    payment to an investment are out. fallback_of names a subcategory's fallback only."""
    assert len(FALLBACK_CODES) == 12
    assert FALLBACK_CODES <= HOUSEHOLD_SPENDING
    for code in (C.HH_SUPERMARKET, C.HH_STREAMING, C.HH_POSTAGE, C.HH_FUEL, C.HH_CLOTHING):
        assert code in HOUSEHOLD_SPENDING
    assert C.HH_REFUND in HOUSEHOLD_SPENDING  # lowers spending, never income
    for code in (
        C.HH_CASH,
        C.HH_CREDIT_CARD,
        C.HH_INVESTMENT,
        C.HH_INTEREST_FEES,
        C.HH_SALARY,
        C.HH_DONATION,
        C.HH_PROPERTY_COST,
        C.INTERNAL_TRANSFER_OUT,
        C.BANK_FEES,
        C.UNCATEGORIZED,
        C.SECURITY_PURCHASE,
        C.REPAIRS,
    ):
        assert code not in HOUSEHOLD_SPENDING, code
    assert fallback_of(C.HH_SUPERMARKET) is C.HH_GROCERIES
    assert fallback_of(C.HH_FUEL) is C.HH_MOBILITY
    assert fallback_of(C.HH_POSTAGE) is C.HH_OTHER
    for code in (C.HH_GROCERIES, C.HH_CLOTHING, C.HH_INTEREST_FEES, C.HH_DONATION, C.REPAIRS):
        assert fallback_of(code) is None, code


# --- A confirmed line -----------------------------------------------------------------------


def test_a_confirmed_line_is_re_proposed_in_its_subcategory(world, private, app_engine, tmp_path):
    _consent_both(world)
    tx = private.line(date(2026, 10, 4), "-42.10", name="Supermarkt Beispiel", purpose="Karte")
    _confirmed(world, tx, "-42.10", "hh_groceries")

    assert world.cmd(resort_household) == _result(confirmed=1, queued=1)
    ((task, args),) = _jobs(world)
    assert task == "match_transaction"
    assert args == {"workspace_id": str(world.ws), "transaction_id": str(tx), "resort": "1"}

    client = _client(("hh_supermarket", "-42.10"))
    plan = _resort(world, tx, _deps(app_engine, tmp_path, client))
    assert (plan.outcome, plan.reason) == ("written", "routing.review")
    (request,) = client.requests
    assert request.prompt_version == matching.household_prompt_version()
    (row,) = _rows(world, tx)
    assert (row.category, row.method, row.status) == ("hh_supermarket", "llm", "proposed")
    old, new = _all_rows(world, tx)
    assert (old.category, old.status) == ("hh_groceries", "superseded")
    assert new.supersedes_id == old.id
    assert _events(world, tx) == [
        ("transaction.allocated", "user"),
        ("transaction.resort_proposed", "ai"),
    ]
    payload = world.query(
        lambda c: c.execute(
            text("SELECT payload FROM event WHERE entity_id = :id AND type = :t"),
            {"id": tx, "t": "transaction.resort_proposed"},
        ).scalar_one()
    )
    assert payload["supersedes"] == [str(old.id)]
    assert payload["auto"] is False
    assert payload["prompt_version"] == matching.household_prompt_version()
    assert [p["category"] for p in payload["allocations"]] == ["hh_supermarket"]

    # The person confirms it: transaction.allocated, as for any proposal.
    _confirmed(world, tx, "-42.10", "hh_supermarket")
    (row,) = _rows(world, tx)
    assert (row.category, row.status) == ("hh_supermarket", "confirmed")
    assert _events(world, tx)[-1] == ("transaction.allocated", "user")

    # Re-run safe: the line is no longer in a fallback; a job queued twice is a no-op.
    assert world.cmd(resort_household) == _result()
    plan = _resort(world, tx, _deps(app_engine, tmp_path, client))
    assert (plan.outcome, plan.reason) == ("skipped", household_resort.NOT_RESORTABLE)
    assert len(client.requests) == 1


def test_the_same_answer_leaves_a_confirmed_line_and_a_re_run_asks_nothing(
    world, private, app_engine, tmp_path
):
    _consent_both(world)
    tx = private.line(date(2026, 10, 5), "-18.00", name="Wochenmarkt Beispiel")
    _confirmed(world, tx, "-18.00", "hh_groceries")
    world.cmd(resort_household)
    client = _client(("hh_groceries", "-18.00"))
    deps = _deps(app_engine, tmp_path, client)
    plan = _resort(world, tx, deps)
    assert (plan.outcome, plan.reason) == ("skipped", household_resort.KEEPS_CONFIRMED)
    (row,) = _rows(world, tx)
    assert (row.category, row.status, row.method) == ("hh_groceries", "confirmed", "user")
    assert _events(world, tx) == [("transaction.allocated", "user")]

    assert world.cmd(resort_household) == _result(already_asked=1)
    assert _resort(world, tx, deps).reason == "matching.asked_already"
    assert len(client.requests) == 1


@pytest.mark.parametrize(
    "answer",
    ["hh_donation", "hh_investment", "internal_transfer_out", "hh_cash", "uncategorized"],
)
def test_an_answer_of_another_relevance_leaves_the_confirmed_line(
    world, private, app_engine, tmp_path, answer
):
    _consent_both(world)
    tx = private.line(date(2026, 10, 6), "-50.00", name="Empfänger Beispiel")
    _confirmed(world, tx, "-50.00", "hh_other")
    world.cmd(resort_household)
    client = _client((answer, "-50.00"))
    plan = _resort(world, tx, _deps(app_engine, tmp_path, client))
    assert (plan.outcome, plan.reason) == ("skipped", household_resort.KEEPS_CONFIRMED)
    (row,) = _rows(world, tx)
    assert (row.category, row.status) == ("hh_other", "confirmed")
    assert len(client.requests) == 1


def test_the_model_never_books_even_with_auto_confirm_on(world, private, app_engine, tmp_path):
    """auto_accept_llm on and a later import (not the account's first): a new line's
    hh_postage answer would be booked; the re-sort's answer, on a waiting and on a confirmed
    line, stays a proposal."""
    _consent_both(world)
    world.set_auto_accept_llm(True)
    later = private.add_import(NOW.replace(day=13))
    waiting = private.line(date(2026, 10, 7), "-3.70", name="Post Beispiel", import_id=later)
    _propose(world, waiting, "-3.70", "hh_other")
    booked = private.line(date(2026, 10, 8), "-4.80", name="Post Beispiel", import_id=later)
    _confirmed(world, booked, "-4.80", "hh_other")
    assert world.cmd(resort_household) == _result(proposed=1, confirmed=1, queued=2)

    client = _client(("hh_postage", "-3.70"), ("hh_postage", "-4.80"))
    deps = _deps(app_engine, tmp_path, client)
    assert _resort(world, waiting, deps).outcome == "written"
    assert _resort(world, booked, deps).outcome == "written"
    for tx in (waiting, booked):
        (row,) = _rows(world, tx)
        assert (row.category, row.status) == ("hh_postage", "proposed")
    # A new line of the same import is booked by the same answer: the guard is the re-sort's.
    fresh = private.line(date(2026, 10, 9), "-2.10", name="Post Beispiel", import_id=later)
    client = _client(("hh_postage", "-2.10"))
    run_match_transaction(
        _deps(app_engine, tmp_path, client), workspace_id=world.ws, transaction_id=fresh, attempt=0
    )
    (row,) = _rows(world, fresh)
    assert (row.category, row.status) == ("hh_postage", "confirmed")


def test_a_person_answering_before_the_model_returns_wins(world, private):
    """The job saw the rows under the line's lock; if they changed before its answer is
    written (a person confirmed or re-booked the line), the answer is dropped."""
    _consent_both(world)
    tx = private.line(date(2026, 10, 10), "-9.99", name="Händler Beispiel")
    _propose(world, tx, "-9.99", "hh_subscriptions")
    plan = world.cmd(household_resort.resort_line, tx, iban_key=b"k" * 32, ai_available=True)
    assert plan.outcome == "categorize"
    line = world.query(matching.load_line, tx)
    expected = household_resort.seen(line)
    world.cmd(allocations.confirm_proposed, tx)
    found = AllocationSet(
        transaction_id=tx,
        method=AllocationMethod.LLM,
        confidence=Decimal("0.950"),
        allocations=[AllocationDraft(amount=Decimal("-9.99"), category="hh_streaming")],
    )
    plan = world.cmd(household_resort.finish_resort, tx, found, expected)
    assert (plan.outcome, plan.reason) == ("skipped", household_resort.ANSWERED_MEANWHILE)
    (row,) = _rows(world, tx)
    assert (row.category, row.status) == ("hh_subscriptions", "confirmed")
    assert expected == ((row.id, AllocationStatus.PROPOSED),)


# --- What is left alone ----------------------------------------------------------------------


def test_split_tax_transfer_and_purchase_cost_lines_are_left_alone(world, private):
    _consent_both(world)
    split = private.line(date(2026, 10, 11), "-80.00", name="Kaufhaus Beispiel")
    two_parts = AllocationSet(
        transaction_id=split,
        method=AllocationMethod.USER,
        confidence=Decimal("1.000"),
        allocations=[
            AllocationDraft(amount=Decimal("-50.00"), category="hh_groceries"),
            AllocationDraft(amount=Decimal("-30.00"), category="hh_home"),
        ],
    )
    world.cmd(allocations.record_set, two_parts, confirm=True, auto=False)
    tax = private.line(date(2026, 10, 12), "-25.00", name="Verein Beispiel")
    _confirmed(world, tax, "-25.00", "hh_donation")
    transfer = private.line(date(2026, 10, 13), "-200.00", name="Eigenes Konto")
    _confirmed(world, transfer, "-200.00", "internal_transfer_out")
    finer = private.line(date(2026, 10, 14), "-12.00", name="Bäckerei Beispiel")
    _confirmed(world, finer, "-12.00", "hh_bakery")
    notary = private.line(date(2026, 10, 15), "-412.34", name="Notariat Dr. Beispiel")
    _confirmed(world, notary, "-412.34", "hh_other")
    rental = world.line(date(2026, 10, 16), "-12.00", name="Händler Beispiel")
    _propose(world, rental, "-12.00", "uncategorized")

    assert world.cmd(resort_household) == _result(split=1, purchase_costs=1)
    assert _jobs(world) == []


def test_nothing_is_queued_while_the_switch_is_off(world, private, app_engine, tmp_path):
    tx = private.line(date(2026, 10, 17), "-60.00", name="Händler Beispiel")
    _propose(world, tx, "-60.00", "hh_leisure_travel")
    assert world.cmd(resort_household) == _result(proposed=1, held_back=1)
    assert _jobs(world) == []
    # A job queued before the switch went off never reaches the model either.
    client = FakeModelClient()  # any request fails the test
    plan = _resort(world, tx, _deps(app_engine, tmp_path, client))
    assert (plan.outcome, plan.reason) == ("skipped", "routing.ai_disabled")
    world.cmd(workspace_settings.update_settings, ai_private_accounts=True, actor=OWNER_A)
    assert world.cmd(resort_household).queued == 1


# --- Rules -----------------------------------------------------------------------------------


def test_a_fallback_rule_is_skipped_so_the_model_is_asked(world, private, app_engine, tmp_path):
    """The payee's rule books hh_groceries; asked again it would book the line back where it
    is, so the re-sort leaves it out and asks the model."""
    _consent_both(world)
    first = private.line(date(2026, 10, 1), "-35.00", name="Supermarkt Beispiel", iban=GROCER_IBAN)
    rule = _answer_line(world, first, "-35.00", "hh_groceries", learn=True).rule
    assert rule is not None and rule.category == "hh_groceries"
    booked = private.line(date(2026, 10, 8), "-41.00", name="Supermarkt Beispiel", iban=GROCER_IBAN)
    _rematch(app_engine, tmp_path, world, booked)
    (row,) = _rows(world, booked)
    assert (row.category, row.method, row.status) == ("hh_groceries", "rule", "confirmed")

    assert world.cmd(resort_household) == _result(confirmed=2, queued=2)
    client = _client(("hh_supermarket", "-41.00"))
    plan = _resort(world, booked, _deps(app_engine, tmp_path, client))
    assert plan.outcome == "written" and len(client.requests) == 1
    (row,) = _rows(world, booked)
    assert (row.category, row.method, row.status) == ("hh_supermarket", "llm", "proposed")


def test_another_rule_still_answers_first_and_only_proposes(world, private, app_engine, tmp_path):
    """A rule on a subcategory answers before the model: on a waiting line as for any re-ask
    (a standing hit books it), on a confirmed line only as a proposal."""
    _consent_both(world)
    iban = GROCER_IBAN
    learned = private.line(date(2026, 10, 1), "-30.00", name="Supermarkt Beispiel", iban=iban)
    rule = _answer_line(world, learned, "-30.00", "hh_supermarket", learn=True).rule
    assert rule is not None
    waiting = private.line(date(2026, 10, 8), "-30.00", name="Supermarkt Beispiel", iban=iban)
    _propose(world, waiting, "-30.00", "hh_groceries")
    booked = private.line(date(2026, 10, 9), "-30.00", name="Supermarkt Beispiel", iban=iban)
    _confirmed(world, booked, "-30.00", "hh_groceries")
    assert world.cmd(resort_household) == _result(proposed=1, confirmed=1, queued=2)

    client = FakeModelClient()  # any request fails the test
    deps = _deps(app_engine, tmp_path, client)
    assert _resort(world, waiting, deps).outcome == "written"
    (row,) = _rows(world, waiting)
    assert (row.category, row.method, row.status) == ("hh_supermarket", "rule", "confirmed")
    assert _resort(world, booked, deps).outcome == "written"
    (row,) = _rows(world, booked)
    assert (row.category, row.method, row.status) == ("hh_supermarket", "rule", "proposed")
    assert row.rule_id == rule.id
    payload = world.query(
        lambda c: c.execute(
            text("SELECT payload FROM event WHERE entity_id = :id AND type = :t"),
            {"id": booked, "t": "transaction.resort_proposed"},
        ).scalar_one()
    )
    assert payload["rule_id"] == str(rule.id) and payload["prompt_version"] is None
    assert client.requests == []


def test_a_near_miss_on_a_confirmed_line_asks_the_model(world, private, app_engine, tmp_path):
    """D49's near miss („Der Betrag weicht … ab“, a rule of another kind guessed at 0.5) is no
    answer for a confirmed line: the model is asked, and no rule proposal is written."""
    _consent_both(world)
    iban = GROCER_IBAN
    one = private.line(date(2026, 10, 1), "-50.00", name="Kaufhaus Beispiel", iban=iban)
    assert _answer_line(world, one, "-50.00", "hh_groceries", learn=True).rule is not None
    two = private.line(date(2026, 10, 2), "-120.00", name="Kaufhaus Beispiel", iban=iban)
    assert _answer_line(world, two, "-120.00", "hh_home", learn=True).rule is not None
    third = private.line(date(2026, 10, 3), "-110.00", name="Kaufhaus Beispiel", iban=iban)
    _confirmed(world, third, "-110.00", "hh_groceries")
    world.cmd(resort_household)

    client = _client(("hh_supermarket", "-110.00"))
    plan = _resort(world, third, _deps(app_engine, tmp_path, client))
    assert plan.outcome == "written"
    assert len(client.requests) == 1
    (row,) = _rows(world, third)
    assert (row.category, row.method, row.status) == ("hh_supermarket", "llm", "proposed")
    assert row.rule_id is None
    payloads = world.query(
        lambda c: (
            c.execute(
                text("SELECT payload FROM event WHERE entity_id = :id AND type = :t"),
                {"id": third, "t": "transaction.resort_proposed"},
            )
            .scalars()
            .all()
        )
    )
    assert [(p["method"], p["rule_id"]) for p in payloads] == [("llm", None)]


def test_a_line_re_sorted_once_is_left_alone_after_a_person_answers(
    world, private, app_engine, tmp_path
):
    """A rule answered the re-sort (no ai_call); the person keeps hh_groceries. Later runs
    count the line as already asked and queue nothing, and a stale job asks nothing."""
    _consent_both(world)
    learned = private.line(
        date(2026, 10, 1), "-30.00", name="Supermarkt Beispiel", iban=GROCER_IBAN
    )
    assert _answer_line(world, learned, "-30.00", "hh_supermarket", learn=True).rule is not None
    tx = private.line(date(2026, 10, 2), "-30.00", name="Supermarkt Beispiel", iban=GROCER_IBAN)
    _confirmed(world, tx, "-30.00", "hh_groceries")

    assert world.cmd(resort_household) == _result(confirmed=1, queued=1)
    client = FakeModelClient()  # any request fails the test
    deps = _deps(app_engine, tmp_path, client)
    assert _resort(world, tx, deps).outcome == "written"
    (row,) = _rows(world, tx)
    assert (row.category, row.method, row.status) == ("hh_supermarket", "rule", "proposed")
    _confirmed(world, tx, "-30.00", "hh_groceries")
    (row,) = _rows(world, tx)
    assert (row.category, row.status) == ("hh_groceries", "confirmed")

    for _ in range(2):
        assert world.cmd(resort_household) == _result(already_asked=1)
    assert _resort(world, tx, deps).reason == "matching.asked_already"
    count = world.query(
        lambda c: c.execute(
            text("SELECT count(*) FROM event WHERE entity_id = :id AND type = :t"),
            {"id": tx, "t": "transaction.resort_proposed"},
        ).scalar_one()
    )
    assert count == 1
    assert client.requests == []


def test_confirming_a_re_sorted_line_with_a_rule_replaces_the_fallback_rule(
    world, private, app_engine, tmp_path
):
    """D49: „Künftig immer so zuordnen“ on hh_supermarket switches off the payee's
    hh_groceries rule (same matcher) instead of keeping it as a second kind beside it; the
    re-sorted proposal has no rule booking to correct, so this is create_rule's own check."""
    _consent_both(world)
    first = private.line(date(2026, 10, 1), "-35.00", name="Supermarkt Beispiel", iban=GROCER_IBAN)
    old = _answer_line(world, first, "-35.00", "hh_groceries", learn=True).rule
    assert old is not None
    world.cmd(resort_household)
    client = _client(("hh_supermarket", "-35.00"))
    assert _resort(world, first, _deps(app_engine, tmp_path, client)).outcome == "written"

    new = _answer_line(world, first, "-35.00", "hh_supermarket", learn=True).rule
    assert new is not None and new.category == "hh_supermarket"
    assert new.matcher == old.matcher
    rules = world.query(
        lambda c: c.execute(
            select(rule_table.c.id, rule_table.c.category, rule_table.c.enabled).order_by(
                rule_table.c.created_at, rule_table.c.id
            )
        ).all()
    )
    assert {(r.category, r.enabled) for r in rules} == {
        ("hh_groceries", False),
        ("hh_supermarket", True),
    }
    assert ("rule.disabled", "user") in _events(world, old.id)

    # The payee's next line is booked by the finer rule alone.
    later = private.line(date(2026, 11, 3), "-52.00", name="Supermarkt Beispiel", iban=GROCER_IBAN)
    _rematch(app_engine, tmp_path, world, later)
    (row,) = _rows(world, later)
    assert (row.category, row.method, row.status) == ("hh_supermarket", "rule", "confirmed")


def test_a_fallback_rule_stays_beside_a_subcategory_of_another_group(world, private):
    """Only the fallback of the new code's own group is replaced: a rule on hh_dining stays
    when hh_supermarket is learned on the same matcher."""
    first = private.line(date(2026, 10, 1), "-35.00", name="Laden Beispiel", iban=GROCER_IBAN)
    old = _answer_line(world, first, "-35.00", "hh_dining", learn=True).rule
    second = private.line(date(2026, 10, 2), "-36.00", name="Laden Beispiel", iban=GROCER_IBAN)
    new = _answer_line(world, second, "-36.00", "hh_supermarket", learn=True).rule
    assert old is not None and new is not None
    enabled = world.query(
        lambda c: set(
            c.execute(select(rule_table.c.category).where(rule_table.c.enabled.is_(True)))
            .scalars()
            .all()
        )
    )
    assert enabled == {"hh_dining", "hh_supermarket"}


def test_rebooking_the_payees_earlier_lines_after_a_re_sort(world, private, app_engine, tmp_path):
    """D160 amended (2): three lines of a payee without an IBAN, all confirmed „Sonstiges
    Essen gehen“. The re-sort proposes „Lieferdienst“ for two and keeps the third (the model
    answered the same). Confirming one with a rule conflicts with the third; „Diese auch auf
    Lieferdienst umbuchen“ re-books it, and the new rule books the other proposal."""
    _consent_both(world)
    lines = []
    for day, amount in ((1, "-19.90"), (2, "-20.90"), (3, "-21.90")):
        tx = private.line(date(2026, 9, day), amount, name=DELIVERY)
        _confirmed(world, tx, amount, "hh_dining")
        lines.append((tx, amount))
    assert world.cmd(resort_household) == _result(confirmed=3, queued=3)
    client = _client(("hh_delivery", "-19.90"), ("hh_dining", "-20.90"), ("hh_delivery", "-21.90"))
    deps = _deps(app_engine, tmp_path, client)
    for tx, _ in lines:
        _resort(world, tx, deps)
    (one, a1), (kept, _), (other, _) = lines
    assert [_rows(world, tx)[0].status for tx, _ in lines] == ["proposed", "confirmed", "proposed"]

    with pytest.raises(RuleViolationError) as refused:
        _answer_rebook(world, one, a1, "hh_delivery", rebook=False)
    assert refused.value.code == "rule.conflicts"

    result = _answer_rebook(world, one, a1, "hh_delivery")
    assert result.rebooked == 1 and result.rule is not None
    assert result.rule.category == "hh_delivery"
    (row,) = _rows(world, kept)
    assert (row.category, row.status, row.method) == ("hh_delivery", "confirmed", "user")
    # The proposal the re-sort left waiting is booked by the new rule (rematch_waiting).
    assert any(task == "rematch_waiting" for task, _ in _jobs(world))
    _rematch(app_engine, tmp_path, world, other)
    (row,) = _rows(world, other)
    assert (row.category, row.status, row.method) == ("hh_delivery", "confirmed", "rule")


# --- The command -------------------------------------------------------------------------------


def test_the_command_counts_with_dry_run_and_then_queues(dbname, world, private, capsys):
    _consent_both(world)
    waiting = private.line(date(2026, 10, 18), "-19.00", name="Händler Beispiel")
    _propose(world, waiting, "-19.00", "hh_other")
    booked = private.line(date(2026, 10, 19), "-7.50", name="Kino Beispiel")
    _confirmed(world, booked, "-7.50", "hh_leisure_travel")
    notary = private.line(date(2026, 10, 20), "-99.00", name="Notariat Dr. Beispiel")
    _confirmed(world, notary, "-99.00", "hh_other")
    environ = {"HAUSBUCH_ADMIN_DATABASE_URL": _owner_url(dbname)}
    argv = ["household-resort", "--workspace", str(world.ws)]

    assert cli.main([*argv, "--dry-run"], environ) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "taken 2 line(s) in a group's fallback: 1 proposed, 1 confirmed" in out
    assert "would queue 2 line(s) for the model" in out
    assert "1 line(s) that may be a cost of buying the flat" in out
    assert _jobs(world) == []

    assert cli.main(argv, environ) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "queued 2 line(s)" in out and "postgresql" not in out
    queued = {uuid.UUID(job.args["transaction_id"]) for job in _jobs(world)}
    assert queued == {waiting, booked}
    assert all(job.args["resort"] == "1" for job in _jobs(world))
