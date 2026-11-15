"""Phase 6 D162 amended 15 Nov 2026 under hausbuch_app and RLS: a depot's cash line, which
carries no IBAN, is booked and linked as the transfer's leg when the bank's debit names the
depot's IBAN (whichever import comes first, the model's proposal replaced, a confirmed answer
never), the bank's direct debit credited by the depot before the bank books it, relink after a
depot statement import and `hausbuch relink-transfers`. Invented names and IBANs only."""

from datetime import date
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from db.matching_support import ACCOUNT_IBAN, World
from db.test_cli_evals_report import _owner_url
from db.test_services_depot_import import _depot as create_depot
from db.test_services_depot_import import _import as import_depot
from db.test_services_private_matching import NOW, Private
from sqlalchemy import select, update
from test_depot_pdf import IBAN as DEPOT_PDF_IBAN
from test_depot_pdf import KONTO, page

from hausbuch import cli
from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import transfer_link as link_table
from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.schemas.household import OpenTransferCode, TransferLinkKind, TransferMatchedBy
from hausbuch.services import allocations, matching, statements, transfers

pytestmark = pytest.mark.db

C = AllocationCategory
TODAY = NOW.date()
FRIDAY = date(2026, 8, 7)
WEDNESDAY = date(2026, 8, 5)  # two banking days before FRIDAY
DEPOT_IBAN = "DE24100000000000000081"  # Bundesbank test bank code 10000000
DEPOT_B_IBAN = "DE94100000000000000082"


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0], now=NOW)


@pytest.fixture
def bank(world) -> Private:
    return Private(world)


def _depot(world: World, iban: str = DEPOT_IBAN) -> Private:
    """A depot of the household whose IBAN is known, with a reconciled import of its own."""
    depot = Private.__new__(Private)
    depot.world = world
    with workspace_transaction(world.engine, world.ws) as conn:
        depot.account_id = world._account(conn, iban, owner=True)
        conn.execute(
            update(account_table)
            .where(account_table.c.id == depot.account_id)
            .values(kind="depot", provider="quirion")
        )
    depot.import_id = depot.add_import(world.now)
    return depot


def _match(world: World, tx, **kwargs):
    return world.cmd(matching.match_line, tx, iban_key=TEST_IBAN_KEY, ai_available=False, **kwargs)


def _answer(tx, amount: str, category: C, method: AllocationMethod) -> AllocationSet:
    return AllocationSet(
        transaction_id=tx,
        method=method,
        confidence=Decimal("0.700") if method is AllocationMethod.LLM else Decimal("1.000"),
        reasoning="Nicht eindeutig.",
        allocations=[AllocationDraft(amount=Decimal(amount), category=category)],
    )


def _propose(world: World, tx, amount: str, category: C = C.HH_OTHER) -> None:
    """The model's guess, as the categorize job leaves it on a line without an IBAN."""
    answer = _answer(tx, amount, category, AllocationMethod.LLM)
    world.cmd(allocations.record_set, answer, confirm=False, auto=False)


def _confirm(world: World, tx, amount: str, category: C) -> None:
    """A person's own answer."""
    answer = _answer(tx, amount, category, AllocationMethod.USER)
    world.cmd(allocations.record_set, answer, confirm=True, auto=False)


def _rows(world: World, tx):
    return world.query(
        lambda c: c.execute(
            select(allocation_table).where(
                allocation_table.c.transaction_id == tx,
                allocation_table.c.status != "superseded",
            )
        ).all()
    )


def _booked(world: World, tx):
    return [(r.category, r.status, r.method) for r in _rows(world, tx)]


def _links(world: World):
    return world.query(lambda c: c.execute(select(link_table)).all())


def _linked_events(world: World):
    return world.query(
        lambda c: c.execute(
            select(event_table).where(event_table.c.type == "transfer.linked")
        ).all()
    )


def _open(world: World):
    return world.query(transfers.open_transfers, TODAY)


NAMED = ("internal_transfer_in", "confirmed", "matcher_transfer")


def _assert_linked(world: World, out, in_) -> None:
    [link] = _links(world)
    assert (link.out_transaction_id, link.in_transaction_id) == (out, in_)
    assert (link.kind, link.matched_by) == (
        TransferLinkKind.DEPOT_CASH.value,
        TransferMatchedBy.MATCHER.value,
    )
    assert len(_linked_events(world)) == 1  # one link, one event: never a second row
    assert _open(world) == []


# --- T1: the leg the bank's debit names ------------------------------------------------------


def test_bank_first_then_the_depot_line_the_model_guessed(world, bank):
    depot = _depot(world)
    out = bank.line(FRIDAY, "-600.00", iban=DEPOT_IBAN, name="Beispiel Depot")
    _match(world, out)
    assert _booked(world, out) == [("internal_transfer_out", "confirmed", "matcher_transfer")]
    in_ = depot.line(FRIDAY, "600.00", purpose="Lastschrift Einzug")
    _propose(world, in_, "600.00")  # the categorize job's answer reaches record_set's hook
    assert _booked(world, in_) == [NAMED]
    [row] = _rows(world, in_)
    assert row.reasoning.startswith("Überweisung zwischen zwei eigenen Konten")
    _assert_linked(world, out, in_)


def test_depot_first_then_the_bank_line(world, bank):
    depot = _depot(world)
    in_ = depot.line(FRIDAY, "600.00", purpose="Lastschrift Einzug")
    _propose(world, in_, "600.00", C.UNCATEGORIZED)
    out = bank.line(FRIDAY, "-600.00", iban=DEPOT_IBAN)
    _match(world, out)
    assert _booked(world, in_) == [NAMED]
    _assert_linked(world, out, in_)


def test_a_later_model_answer_never_replaces_the_matchers_booking(world, bank):
    depot = _depot(world)
    out = bank.line(FRIDAY, "-600.00", iban=DEPOT_IBAN)
    _match(world, out)
    in_ = depot.line(FRIDAY, "600.00")  # no rows yet: the categorize job is still asking
    assert world.cmd(transfers.relink, depot.account_id) == 1
    assert _booked(world, in_) == [NAMED]
    late = _answer(in_, "600.00", C.HH_OTHER, AllocationMethod.LLM)
    plan = world.cmd(matching.finish_line, in_, late)
    assert (plan.outcome, plan.reason) == ("skipped", "matching.answered_meanwhile")
    assert world.cmd(matching.finish_line, in_, late, reask=True).outcome == "skipped"
    assert _match(world, in_, rematch=True).outcome == "skipped"
    assert _booked(world, in_) == [NAMED]
    _assert_linked(world, out, in_)


def test_a_depot_line_a_person_confirmed_as_something_else_keeps_its_card(world, bank):
    depot = _depot(world)
    in_ = depot.line(FRIDAY, "600.00")
    _confirm(world, in_, "600.00", C.HH_OTHER_INCOME)
    out = bank.line(FRIDAY, "-600.00", iban=DEPOT_IBAN)
    _match(world, out)
    world.cmd(transfers.relink, depot.account_id)
    assert _booked(world, in_) == [("hh_other_income", "confirmed", "user")]
    assert _links(world) == []
    [card] = _open(world)
    assert (card.code, card.leg.id) == (OpenTransferCode.PROPOSED, out)
    [candidate] = card.candidates
    assert (candidate.id, candidate.rebook_on_link) == (in_, True)


def test_a_different_amount_is_never_booked(world, bank):
    depot = _depot(world)
    out = bank.line(FRIDAY, "-600.00", iban=DEPOT_IBAN)
    _match(world, out)
    in_ = depot.line(FRIDAY, "599.00")
    _propose(world, in_, "599.00")
    assert _booked(world, in_) == [("hh_other", "proposed", "llm")]
    assert _links(world) == []


def test_a_rental_account_line_is_left_to_its_matchers(world, bank):
    """The private account's top-up names the rental account; the rental line (without an
    IBAN here) is never booked by this path."""
    rental_in = world.line(FRIDAY, "600.00", purpose="Einzahlung")
    out = bank.line(FRIDAY, "-600.00", iban=ACCOUNT_IBAN)
    _match(world, out)
    assert _booked(world, out) == [("internal_transfer_out", "confirmed", "matcher_transfer")]
    assert _rows(world, rental_in) == []
    assert _links(world) == []


def test_two_depots_paid_the_same_amount_on_one_day_each_link_their_own_debit(world, bank):
    depot_a, depot_b = _depot(world), _depot(world, DEPOT_B_IBAN)
    out_a = bank.line(FRIDAY, "-600.00", iban=DEPOT_IBAN)
    out_b = bank.line(FRIDAY, "-600.00", iban=DEPOT_B_IBAN)
    _match(world, out_a)
    _match(world, out_b)
    in_a = depot_a.line(FRIDAY, "600.00")
    in_b = depot_b.line(FRIDAY, "600.00")
    _propose(world, in_a, "600.00")
    _propose(world, in_b, "600.00")
    assert _booked(world, in_a) == [NAMED] and _booked(world, in_b) == [NAMED]
    pairs = {(r.out_transaction_id, r.in_transaction_id) for r in _links(world)}
    assert pairs == {(out_a, in_a), (out_b, in_b)}
    assert _open(world) == []


# --- T3: credited before the bank books the direct debit --------------------------------------


def test_a_direct_debit_credited_two_banking_days_before_the_debit_is_linked(world, bank):
    depot = _depot(world)
    in_ = depot.line(WEDNESDAY, "600.00", purpose="Lastschrift Einzug")
    _propose(world, in_, "600.00")
    out = bank.line(FRIDAY, "-600.00", iban=DEPOT_IBAN)
    _match(world, out)
    assert _booked(world, in_) == [NAMED]
    _assert_linked(world, out, in_)


def test_without_the_iban_tie_an_earlier_credit_is_no_counterpart(world, bank):
    """The depot's Einlage booked on Wednesday, a debit to a third party's IBAN on Friday:
    no link, and the Einlage's card past its window offers no candidate."""
    depot = _depot(world)
    in_ = depot.line(WEDNESDAY, "600.00")
    _confirm(world, in_, "600.00", C.INTERNAL_TRANSFER_IN)
    out = bank.line(FRIDAY, "-600.00", iban="DE67100000000000000083")
    _confirm(world, out, "-600.00", C.HH_OTHER)
    assert _links(world) == []


# --- T2: relink after a depot import, and the command -----------------------------------------


def test_relink_transfers_command_dry_run_then_run(dbname, world, bank, capsys):
    depot = _depot(world)
    out = bank.line(FRIDAY, "-600.00", iban=DEPOT_IBAN)
    _match(world, out)
    in_ = depot.line(FRIDAY, "600.00")  # imported with no model answer: nothing tried it
    environ = {"HAUSBUCH_ADMIN_DATABASE_URL": _owner_url(dbname)}
    argv = ["relink-transfers", "--workspace", str(world.ws)]

    assert cli.main([*argv, "--dry-run"], environ) == cli.EXIT_OK
    assert "would link 1 transfer(s)" in capsys.readouterr().out
    assert _rows(world, in_) == [] and _links(world) == []

    assert cli.main(argv, environ) == cli.EXIT_OK
    out_text = capsys.readouterr().out
    assert "linked 1 transfer(s)" in out_text and "postgresql" not in out_text
    assert _booked(world, in_) == [NAMED]
    _assert_linked(world, out, in_)
    assert cli.main(argv, environ) == cli.EXIT_OK  # a second run links nothing more
    assert "linked 0 transfer(s)" in capsys.readouterr().out


def test_a_depot_statement_import_books_and_links_the_debits_naming_it(
    run, blob_store, world, bank, monkeypatch
):
    """The depot's cash statement arrives after the bank's direct debit: the import relinks
    the depot, so its Lastschrift line (a headline no pattern knows) is booked and linked
    without the model; follow-accounts and „Auf Konto importieren“ import through the same
    import_statement."""
    monkeypatch.setattr(statements, "_on_reconciled", lambda ctx, import_id: None)
    depot = create_depot(run, world.ws, external_ref=KONTO, iban=DEPOT_PDF_IBAN)
    out = bank.line(date(2026, 3, 10), "-600.00", iban=DEPOT_PDF_IBAN)
    _match(world, out)
    rows = ["600,00 EUR10.03.202610.03.2026Lastschrift, Ref.: 5500000011"]
    statement = [page(rows=rows, total="600,00", closing="700,00")]
    import_depot(run, blob_store, world.ws, depot.id, statement)
    [in_] = [t.id for t in world.query(statements.list_transactions, depot.id)]
    assert _booked(world, in_) == [NAMED]
    _assert_linked(world, out, in_)
