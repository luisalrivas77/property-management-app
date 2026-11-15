"""Phase 6 D162 amended 15 Nov 2026 under hausbuch_app and RLS: an exchange credits a SEPA
deposit up to 2 € short without a fee row in its export. Untied, the pair is only proposed,
its card naming the gap; the first link by hand books the bank line as the credited transfer
plus the gap as bank fees and records the line's IBAN as the exchange's deposit IBAN; then the
remaining deposits link by that IBAN (`rematch-waiting`, `relink-transfers`, the next import),
a withdrawal still links, and the rental account is never split. Invented names and IBANs
only."""

from datetime import date
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from db.matching_support import World
from db.test_cli_evals_report import _owner_url
from db.test_services_private_matching import NOW, Private, _answer_line, _deps
from sqlalchemy import select, text

from hausbuch import cli
from hausbuch.ai.fake import FakeModelClient
from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import matching_rule as rule_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.db.tables import transfer_link as link_table
from hausbuch.jobs import enqueue
from hausbuch.jobs.matching import run_rematch_waiting
from hausbuch.schemas.allocation import (
    AllocationCategory,
    AllocationDraft,
    AllocationMethod,
    AllocationSet,
)
from hausbuch.schemas.api import InboxItemKind
from hausbuch.schemas.bank import AccountCreate
from hausbuch.schemas.enums import AccountKind
from hausbuch.schemas.household import (
    MovementKind,
    OpenTransferCode,
    TransferLinkKind,
    TransferLinkRequest,
    TransferMatchedBy,
)
from hausbuch.services import allocations, matching, transfers
from hausbuch.services.accounts import create_account
from hausbuch.services.exchange import imports

pytestmark = pytest.mark.db

C = AllocationCategory
TODAY = NOW.date()
DEPOSIT_IBAN = "DE45100000000000000091"  # the exchange's deposit IBAN (test bank 10000000)
PAYOUT_IBAN = "DE18100000000000000092"  # where the exchange's payouts come from
HEADER = "User ID,Time,Account,Operation,Coin,Change,Remark\n"


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0], now=NOW)


@pytest.fixture
def bank(world) -> Private:
    return Private(world)


def _exchange(world: World):
    return world.cmd(
        create_account,
        AccountCreate(
            label="Binance",
            bank="Binance",
            kind=AccountKind.EXCHANGE,
            provider="Binance",
            opening_balance=Decimal("0.00"),
            opening_balance_on=date(2026, 1, 1),
        ),
        iban_key=TEST_IBAN_KEY,
    )


def _export(world: World, blob_store, account_id, *rows: str) -> None:
    """Binance's transaction history with these rows ("2026-09-08 08:00:00,Deposit,900")."""
    lines = []
    for row in rows:
        at, operation, change = row.split(",")
        lines.append(f"987654321,{at},Spot,{operation},EUR,{change},\n")
    world.cmd(
        imports.import_export,
        blob_store,
        account_id,
        (HEADER + "".join(lines)).encode(),
        media_type="text/csv",
    )


def _movements(world: World, account_id) -> dict[Decimal, object]:
    """The account's EUR movements by their signed euro amount."""
    found = world.query(imports.exchange_movements, account_id)
    return {m.eur_amount: m.id for m in found if m.eur_amount is not None}


def _propose(world: World, tx, amount: str, category: C = C.HH_INVESTMENT) -> None:
    """The model's guess on a deposit before the exchange's IBAN is known."""
    answer = AllocationSet(
        transaction_id=tx,
        method=AllocationMethod.LLM,
        confidence=Decimal("0.700"),
        reasoning="Vermutlich eine Geldanlage.",
        allocations=[AllocationDraft(amount=Decimal(amount), category=category)],
    )
    world.cmd(allocations.record_set, answer, confirm=False, auto=False)


def _rows(world: World, tx):
    return world.query(
        lambda c: c.execute(
            select(allocation_table)
            .where(
                allocation_table.c.transaction_id == tx,
                allocation_table.c.status != "superseded",
            )
            .order_by(allocation_table.c.amount)
        ).all()
    )


def _booked(world: World, tx):
    return [(r.category, r.amount, r.status, r.method) for r in _rows(world, tx)]


def _split(amount: str, fee: str, method: str):
    return [
        ("internal_transfer_out", Decimal(amount) + Decimal(fee), "confirmed", method),
        ("bank_fees", -Decimal(fee), "confirmed", method),
    ]


def _links(world: World):
    return world.query(lambda c: c.execute(select(link_table)).all())


def _pairs(world: World) -> set[tuple]:
    return {
        (r.out_transaction_id, r.in_movement_id, r.matched_by)
        for r in _links(world)
        if r.superseded_by is None
    }


def _open(world: World):
    return world.query(transfers.open_transfers, TODAY)


def _account(world: World, account_id):
    return world.query(
        lambda c: c.execute(select(account_table).where(account_table.c.id == account_id)).one()
    )


def _link(world: World, out, in_, *, rebook: bool = False):
    body = TransferLinkRequest(
        kind=TransferLinkKind.EXCHANGE_EUR,
        out_transaction_id=out,
        in_movement_id=in_,
        rebook=rebook,
    )
    return world.cmd(transfers.link_by_hand, body, iban_key=TEST_IBAN_KEY)


def _recheck(app_engine, tmp_path, world: World, trigger: str):
    client = FakeModelClient()  # any model request fails the test
    plans = run_rematch_waiting(
        _deps(app_engine, tmp_path, client), workspace_id=world.ws, trigger=trigger, attempt=0
    )
    assert client.requests == []
    return plans


def _queued(world: World) -> list[str]:
    return world.query(
        lambda c: list(
            c.execute(
                text(
                    "SELECT args->>'trigger' FROM procrastinate_jobs"
                    " WHERE task_name = 'rematch_waiting' AND status = 'todo'"
                )
            ).scalars()
        )
    )


# --- Untied: proposed with the gap, never linked ---------------------------------------------


def test_an_untied_deposit_credited_short_is_proposed_with_its_fee(world, bank, blob_store):
    out = bank.line(date(2026, 9, 7), "-901.00", name="Binance", iban=DEPOSIT_IBAN)
    _propose(world, out, "-901.00")
    exchange = _exchange(world)
    _export(world, blob_store, exchange.id, "2026-09-08 08:00:00,Deposit,900")
    deposit = _movements(world, exchange.id)[Decimal("900.00")]
    assert _links(world) == []
    [card] = _open(world)
    assert (card.code, card.leg.id) == (OpenTransferCode.PROPOSED, deposit)
    [candidate] = card.candidates
    assert (candidate.id, candidate.deposit_fee) == (out, Decimal("1.00"))
    assert candidate.deposit_fee_text == "1,00 € behält die Börse als Einzahlungsgebühr."
    [item] = [
        i
        for i in world.query(transfers.transfer_inbox_items, TODAY)
        if i.kind is InboxItemKind.TRANSFER_LINK
    ]
    assert "Einzahlungsgebühr" in item.reasoning


def test_untied_neither_side_links_even_when_both_are_booked_as_transfers(world, bank, blob_store):
    """The bank line a person booked as a transfer and the exchange's Deposit row (a transfer by
    its type) are 1 € apart: no link from either side, each card proposes the other."""
    out = bank.line(date(2026, 9, 7), "-901.00", name="Binance", iban=DEPOSIT_IBAN)
    _answer_line(world, out, "-901.00", "internal_transfer_out")
    exchange = _exchange(world)
    _export(world, blob_store, exchange.id, "2026-09-08 08:00:00,Deposit,900")
    assert world.cmd(transfers.relink_all) == 0
    assert _links(world) == []
    cards = {c.leg.id: c for c in _open(world)}
    deposit = _movements(world, exchange.id)[Decimal("900.00")]
    assert set(cards) == {out, deposit}
    assert [c.deposit_fee for c in cards[out].candidates] == [Decimal("1.00")]
    assert [c.deposit_fee for c in cards[deposit].candidates] == [Decimal("1.00")]


def test_two_short_fits_leave_the_unmatched_card_listing_both(world, bank, blob_store):
    a = bank.line(date(2026, 9, 7), "-901.00", name="Binance", iban=DEPOSIT_IBAN)
    b = bank.line(date(2026, 9, 7), "-901.50", name="Binance", iban=DEPOSIT_IBAN)
    exchange = _exchange(world)
    _export(world, blob_store, exchange.id, "2026-09-08 08:00:00,Deposit,900")
    [card] = _open(world)
    assert card.code is OpenTransferCode.UNMATCHED_LEG
    assert {(c.id, c.deposit_fee) for c in card.candidates} == {
        (a, Decimal("1.00")),
        (b, Decimal("1.50")),
    }


# --- The first link by hand -----------------------------------------------------------------


def _first_link(world, bank, blob_store, *, confirm: str | None = None):
    out = bank.line(date(2026, 9, 7), "-901.00", name="Binance", iban=DEPOSIT_IBAN)
    if confirm is None:
        _propose(world, out, "-901.00")
    else:
        _answer_line(world, out, "-901.00", confirm, learn=True)
    exchange = _exchange(world)
    _export(world, blob_store, exchange.id, "2026-09-08 08:00:00,Deposit,900")
    deposit = _movements(world, exchange.id)[Decimal("900.00")]
    world.query(lambda c: c.execute(text("DELETE FROM procrastinate_jobs")))
    link = _link(world, out, deposit)  # the card's button: no rebook asked
    return out, exchange, deposit, link


def _assert_learned(world, out, exchange, deposit, link) -> None:
    assert _booked(world, out) == _split("-901.00", "1.00", "user")
    assert (link.out_transaction_id, link.in_movement_id) == (out, deposit)
    assert link.matched_by is TransferMatchedBy.USER
    printed = world.query(
        lambda c: c.execute(
            select(transaction_table.c.counterparty_iban_masked).where(
                transaction_table.c.id == out
            )
        ).scalar_one()
    )
    account = _account(world, exchange.id)
    assert account.iban_masked == printed and account.iban_hmac is not None
    [updated] = world.query(
        lambda c: c.execute(
            select(event_table).where(
                event_table.c.type == "account.updated", event_table.c.entity_id == exchange.id
            )
        ).all()
    )
    assert updated.payload["changes"]["iban_masked"] == {"old": None, "new": printed}
    assert enqueue.waiting_trigger(enqueue.TRIGGER_ACCOUNT, exchange.id) in _queued(world)
    assert _open(world) == []


def test_the_first_link_by_hand_splits_a_proposed_line_and_learns_the_iban(world, bank, blob_store):
    _assert_learned(world, *_first_link(world, bank, blob_store))


def test_the_first_link_by_hand_replaces_a_persons_household_answer(world, bank, blob_store):
    """The person had answered the deposit as online shopping and learned a rule on the IBAN:
    linking splits the line as the person's new answer and switches the rule off."""
    out, exchange, deposit, link = _first_link(
        world, bank, blob_store, confirm="hh_online_shopping"
    )
    _assert_learned(world, out, exchange, deposit, link)
    rules = world.query(lambda c: c.execute(select(rule_table)).all())
    assert rules and not any(r.enabled for r in rules)


def test_a_second_click_returns_the_link_and_changes_nothing(world, bank, blob_store):
    out, exchange, deposit, link = _first_link(world, bank, blob_store)
    again = _link(world, out, deposit, rebook=True)
    assert again.id == link.id
    assert _booked(world, out) == _split("-901.00", "1.00", "user")
    assert len(_links(world)) == 1


# --- After the first link: the remaining deposits link by the IBAN ----------------------------

LATER = (
    ("2026-10-05", "-1001.00", "2026-10-06 08:00:00,Deposit,1000"),
    ("2026-11-02", "-1001.00", "2026-11-03 08:00:00,Deposit,999"),
)


def _remaining(world, bank, blob_store):
    """The first deposit and two later ones, all answered only by the model; the first linked
    by hand."""
    first = bank.line(date(2026, 9, 7), "-901.00", name="Binance", iban=DEPOSIT_IBAN)
    later = [
        bank.line(date.fromisoformat(day), amount, name="Binance", iban=DEPOSIT_IBAN)
        for day, amount, _ in LATER
    ]
    for tx in (first, *later):
        _propose(world, tx, "-901.00" if tx == first else "-1001.00")
    exchange = _exchange(world)
    rows = ["2026-09-08 08:00:00,Deposit,900", *(row for _, _, row in LATER)]
    _export(world, blob_store, exchange.id, *rows)
    moves = _movements(world, exchange.id)
    assert _links(world) == []
    world.query(lambda c: c.execute(text("DELETE FROM procrastinate_jobs")))
    _link(world, first, moves[Decimal("900.00")])
    return first, later, exchange, moves


def _assert_remaining_linked(world, first, later, moves) -> None:
    assert _booked(world, later[0]) == _split("-1001.00", "1.00", "matcher_transfer")
    assert _booked(world, later[1]) == _split("-1001.00", "2.00", "matcher_transfer")
    assert _pairs(world) == {
        (first, moves[Decimal("900.00")], TransferMatchedBy.USER.value),
        (later[0], moves[Decimal("1000.00")], TransferMatchedBy.MATCHER.value),
        (later[1], moves[Decimal("999.00")], TransferMatchedBy.MATCHER.value),
    }
    assert _open(world) == []


def test_the_learned_iban_links_the_waiting_deposits_by_the_queued_rematch(
    world, bank, blob_store, app_engine, tmp_path
):
    first, later, exchange, moves = _remaining(world, bank, blob_store)
    trigger = enqueue.waiting_trigger(enqueue.TRIGGER_ACCOUNT, exchange.id)
    assert _queued(world) == [trigger]
    plans = _recheck(app_engine, tmp_path, world, trigger)
    assert [p.outcome for p in plans] == ["written", "written"]
    _assert_remaining_linked(world, first, later, moves)


def test_rematch_waiting_links_the_waiting_deposits(
    dbname, world, bank, blob_store, app_engine, tmp_path
):
    first, later, _, moves = _remaining(world, bank, blob_store)
    world.query(lambda c: c.execute(text("DELETE FROM procrastinate_jobs")))
    environ = {"HAUSBUCH_ADMIN_DATABASE_URL": _owner_url(dbname)}
    assert cli.main(["rematch-waiting", "--workspace", str(world.ws)], environ) == cli.EXIT_OK
    trigger = enqueue.waiting_trigger(enqueue.TRIGGER_PRIVATE_LINES, bank.account_id)
    assert trigger in _queued(world)
    _recheck(app_engine, tmp_path, world, trigger)
    _assert_remaining_linked(world, first, later, moves)


def test_relink_transfers_links_the_waiting_deposits(dbname, world, bank, blob_store, capsys):
    first, later, _, moves = _remaining(world, bank, blob_store)
    environ = {"HAUSBUCH_ADMIN_DATABASE_URL": _owner_url(dbname)}
    argv = ["relink-transfers", "--workspace", str(world.ws)]
    assert cli.main(argv, environ) == cli.EXIT_OK
    assert "linked 2 transfer(s)" in capsys.readouterr().out
    _assert_remaining_linked(world, first, later, moves)


def test_a_new_deposit_links_whichever_import_comes_first(world, bank, blob_store):
    """After the IBAN is learned the bank line is a transfer by IBAN, booked whole by the
    matcher; the exchange's row arriving later (or earlier) splits it by code and links it."""
    _, exchange, _, _ = _first_link(world, bank, blob_store)
    bank_first = bank.line(date(2026, 10, 5), "-1001.00", name="Binance", iban=DEPOSIT_IBAN)
    world.cmd(matching.match_line, bank_first, iban_key=TEST_IBAN_KEY, ai_available=False)
    assert _booked(world, bank_first) == [
        ("internal_transfer_out", Decimal("-1001.00"), "confirmed", "matcher_transfer")
    ]
    _export(world, blob_store, exchange.id, "2026-10-06 08:00:00,Deposit,1000")
    assert _booked(world, bank_first) == _split("-1001.00", "1.00", "matcher_transfer")

    _export(world, blob_store, exchange.id, "2026-11-03 08:00:00,Deposit,999")
    exchange_first = bank.line(date(2026, 11, 2), "-1001.00", name="Binance", iban=DEPOSIT_IBAN)
    world.cmd(matching.match_line, exchange_first, iban_key=TEST_IBAN_KEY, ai_available=False)
    assert _booked(world, exchange_first) == _split("-1001.00", "2.00", "matcher_transfer")
    moves = _movements(world, exchange.id)
    assert {
        (out, move) for out, move, by in _pairs(world) if by == TransferMatchedBy.MATCHER.value
    } == {(bank_first, moves[Decimal("1000.00")]), (exchange_first, moves[Decimal("999.00")])}
    assert _open(world) == []


def test_more_than_two_euros_short_is_never_linked(world, bank, blob_store):
    _, exchange, _, _ = _first_link(world, bank, blob_store)
    out = bank.line(date(2026, 10, 5), "-1001.01", name="Binance", iban=DEPOSIT_IBAN)
    world.cmd(matching.match_line, out, iban_key=TEST_IBAN_KEY, ai_available=False)
    _export(world, blob_store, exchange.id, "2026-10-06 08:00:00,Deposit,999")
    assert _booked(world, out) == [
        ("internal_transfer_out", Decimal("-1001.01"), "confirmed", "matcher_transfer")
    ]
    assert len(_links(world)) == 1  # the first link only


def test_a_withdrawal_still_links_after_the_deposit_iban_is_learned(world, bank, blob_store):
    """The payout comes from another IBAN; the exchange's IBAN is on its deposits only, so
    the bank's credit is not told it names another account."""
    _, exchange, _, _ = _first_link(world, bank, blob_store)
    credit = bank.line(date(2026, 10, 13), "500.00", name="Binance", iban=PAYOUT_IBAN)
    _answer_line(world, credit, "500.00", "internal_transfer_in")
    _export(world, blob_store, exchange.id, "2026-10-12 08:00:00,Withdraw,-500")
    withdrawal = _movements(world, exchange.id)[Decimal("-500.00")]
    [link] = [r for r in _links(world) if r.in_transaction_id == credit]
    assert (link.out_movement_id, link.matched_by) == (withdrawal, TransferMatchedBy.MATCHER.value)
    assert _booked(world, credit) == [
        ("internal_transfer_in", Decimal("500.00"), "confirmed", "user")
    ]
    kinds = world.query(imports.exchange_movements, exchange.id)
    assert MovementKind.INTERNAL_TRANSFER_OUT in {m.kind for m in kinds}


def test_a_deposit_a_person_answered_otherwise_waits_for_one_click(
    world, bank, blob_store, app_engine, tmp_path
):
    """A later deposit the person had confirmed as spending is not replaced by the matcher once
    the IBAN is learned: its card offers the re-book with the fee, and one click splits it."""
    first = bank.line(date(2026, 9, 7), "-901.00", name="Binance", iban=DEPOSIT_IBAN)
    _propose(world, first, "-901.00")
    later = bank.line(date(2026, 10, 5), "-1001.00", name="Binance", iban=DEPOSIT_IBAN)
    _answer_line(world, later, "-1001.00", "hh_online_shopping")
    exchange = _exchange(world)
    _export(
        world,
        blob_store,
        exchange.id,
        "2026-09-08 08:00:00,Deposit,900",
        "2026-10-06 08:00:00,Deposit,1000",
    )
    moves = _movements(world, exchange.id)
    world.query(lambda c: c.execute(text("DELETE FROM procrastinate_jobs")))
    _link(world, first, moves[Decimal("900.00")])
    _recheck(
        app_engine, tmp_path, world, enqueue.waiting_trigger(enqueue.TRIGGER_ACCOUNT, exchange.id)
    )
    assert world.cmd(transfers.relink_all) == 0
    assert _booked(world, later) == [
        ("hh_online_shopping", Decimal("-1001.00"), "confirmed", "user")
    ]
    cards = {c.leg.id: c for c in _open(world)}
    [candidate] = cards[moves[Decimal("1000.00")]].candidates
    assert (candidate.id, candidate.deposit_fee, candidate.rebook_on_link) == (
        later,
        Decimal("1.00"),
        True,
    )
    link = _link(world, later, moves[Decimal("1000.00")], rebook=True)
    # The click books the split as the person's answer; the IBAN tie then lets the matcher
    # link the pair within the same command, and the click returns that link (as the re-book
    # of an exactly fitting leg does, D176 (4)).
    assert (link.out_transaction_id, link.in_movement_id) == (later, moves[Decimal("1000.00")])
    assert _booked(world, later) == _split("-1001.00", "1.00", "user")
    assert _open(world) == []


# --- Never the rental account -----------------------------------------------------------------


def test_a_rental_line_is_never_split_nor_teaches_the_iban(world, blob_store):
    rental = world.line(
        date(2026, 9, 7), "-901.00", name="Binance", iban=DEPOSIT_IBAN, purpose="Einzahlung"
    )
    exchange = _exchange(world)
    _export(world, blob_store, exchange.id, "2026-09-08 08:00:00,Deposit,900")
    deposit = _movements(world, exchange.id)[Decimal("900.00")]
    link = _link(world, rental, deposit)
    assert link.matched_by is TransferMatchedBy.USER
    assert _rows(world, rental) == []  # nothing booked on the rental line by the link
    assert _account(world, exchange.id).iban_hmac is None
