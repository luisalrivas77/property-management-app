"""One payee however a statement spells it, in matching and review (docs/phases/phase-6.md
D160 amended (4), 14 Nov 2026).

One „Künftig immer so zuordnen“ books the payee's lines already waiting: a payment service's
merchant (PayPal, Klarna), a longer spelling of a name, a card line with a per-purchase code,
and one payee across a shared clearing IBAN and a second private account. An old rule on
PayPal's name alone is ignored, and `hausbuch rematch-waiting` re-checks every private line
waiting since before the deploy. Invented names and well-known merchants only."""

import uuid
from datetime import date

import pytest
from db.db_support import TEST_IBAN_KEY
from db.matching_support import World
from db.test_cli_evals_report import _owner_url
from db.test_services_private_matching import (
    CLEARING_IBAN,
    DELIVERY,
    NOW,
    Private,
    _answer_line,
    _deps,
    _events,
    _propose,
    _rematch,
    _rows,
    _shared_world,
    _view,
)
from sqlalchemy import select, text

from hausbuch import cli
from hausbuch.ai.fake import FakeModelClient
from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import matching_rule as rule_table
from hausbuch.jobs import enqueue
from hausbuch.jobs.matching import run_rematch_waiting
from hausbuch.schemas.allocation import MatchingRuleCreate, RuleMatcher, SplitMethod
from hausbuch.services import matching, rules_learned
from hausbuch.services.iban import protect_iban

pytestmark = pytest.mark.db

PAYPAL_IBAN = "DE98100000000000000883"
CARD_ACCOUNT_IBAN = "DE46100000000000000073"
LONG = "PayPal Europe S.a.r.l. et Cie S.C.A 22-24 Boulevard Royal"
SHORT = "PayPal Europe S.a.r.l. et Cie S.C.A"


def _paypal(merchant: str, n: int) -> str:
    return f"10412345678{n:02d} PP.1234.PP . {merchant}, Ihr Einkauf bei {merchant}"


class SecondPrivate(Private):
    """Another private account of the world (a card account, say), with its own import."""

    def __init__(self, world: World, iban: str) -> None:
        self.world = world
        with workspace_transaction(world.engine, world.ws) as conn:
            self.account_id = world._account(conn, iban, owner=True)
        self.import_id = self.add_import(world.now)


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0], now=NOW)


@pytest.fixture
def private(world) -> Private:
    return Private(world)


def _recheck(app_engine, tmp_path, world: World, trigger: str):
    client = FakeModelClient()  # any model request fails the test
    plans = run_rematch_waiting(
        _deps(app_engine, tmp_path, client), workspace_id=world.ws, trigger=trigger, attempt=0
    )
    assert client.requests == []
    return plans


def _booked(world: World, tx) -> tuple[str, str, str]:
    (row,) = _rows(world, tx)
    return row.category, row.method, row.status


def _learn(world: World, tx, amount: str, category: str):
    result = _answer_line(world, tx, amount, category, learn=True)
    assert result.rule is not None
    return result.rule


# --- Item 2: a payment service's merchant ---------------------------------------------------


def test_disney_learned_on_the_short_name_books_the_long_names_waiting_lines(
    world, private, app_engine, tmp_path
):
    waiting = [
        private.line(
            date(2026, m, 5), "-9.99", name=LONG, iban=PAYPAL_IBAN, purpose=_paypal("DisneyPlus", m)
        )
        for m in (7, 8, 9)
    ]
    microsoft = private.line(
        date(2026, 9, 6),
        "-7.00",
        name=LONG,
        iban=PAYPAL_IBAN,
        purpose=_paypal("MicrosoftPay", 20),
    )
    now = private.line(
        date(2026, 10, 5),
        "-9.99",
        name=SHORT,
        iban=PAYPAL_IBAN,
        purpose=_paypal("DisneyPlus", 10),
    )
    view = _view(world, now)
    assert (view.payee_label, view.no_merchant, view.can_learn_rule) == (
        "PayPal: DisneyPlus",
        False,
        True,
    )

    rule = _learn(world, now, "-9.99", "hh_streaming")
    assert rule.matcher.counterparty_iban_hmac is None
    assert rule.matcher.counterparty_name_pattern == r"^PAYPAL\b"
    assert rule.matcher.purpose_pattern == r"Ihr Einkauf bei DisneyPlus(?!\w)"
    assert world.query(matching.waiting_lines, "rule", rule.id) == waiting

    plans = _recheck(app_engine, tmp_path, world, f"rule:{rule.id}")
    assert [p.outcome for p in plans] == ["written"] * 3
    for tx in waiting:
        assert _booked(world, tx) == ("hh_streaming", "rule", "confirmed")
    assert _rows(world, microsoft) == []  # another merchant of the same IBAN

    # The second merchant learns its own rule, beside Disney's.
    other = _learn(world, microsoft, "-7.00", "hh_subscriptions")
    assert other.matcher.purpose_pattern == r"Ihr Einkauf bei MicrosoftPay(?!\w)"
    later = private.line(
        date(2026, 11, 5),
        "-7.00",
        name=SHORT,
        iban=PAYPAL_IBAN,
        purpose=_paypal("MicrosoftPay", 21),
    )
    _rematch(app_engine, tmp_path, world, later)
    assert _booked(world, later) == ("hh_subscriptions", "rule", "confirmed")


def test_a_paypal_line_without_a_merchant_offers_no_rule(world, private):
    tx = private.line(
        date(2026, 10, 5), "-12.00", name=SHORT, iban=PAYPAL_IBAN, purpose="1041234567 PP.1234.PP"
    )
    view = _view(world, tx)
    assert (view.payee_label, view.no_merchant, view.can_learn_rule) == (None, True, False)


def test_a_klarna_line_books_the_same_merchant_only(world, private, app_engine, tmp_path):
    def klarna(day: int, shop: str) -> uuid.UUID:
        return private.line(
            date(2026, 9, day),
            "-39.95",
            name="Klarna Bank AB",
            iban=PAYPAL_IBAN,  # any IBAN: the rule names none
            purpose=f"Purchase at {shop} Gläubiger-ID: SE00ZZZ0000000000000000001",
        )

    same = klarna(3, "H&M")
    other = klarna(4, "Zalando SE")
    first = klarna(10, "H&M")
    rule = _learn(world, first, "-39.95", "hh_clothing")
    assert rule.matcher.counterparty_name_pattern == r"^KLARNA\b"
    _recheck(app_engine, tmp_path, world, f"rule:{rule.id}")
    assert _booked(world, same) == ("hh_clothing", "rule", "confirmed")
    assert _rows(world, other) == []


def test_an_old_rule_on_paypals_name_alone_books_nothing(world, private, app_engine, tmp_path):
    hmac = protect_iban(PAYPAL_IBAN, TEST_IBAN_KEY).hmac.hex()
    world.cmd(
        rules_learned.create_rule,
        MatchingRuleCreate(
            matcher=RuleMatcher(
                counterparty_iban_hmac=hmac,
                counterparty_name_pattern="^PAYPAL EUROPE S A R L ET CIE S C A",
                amount_sign="debit",
            ),
            split_method=SplitMethod.SINGLE_CATEGORY,
            category="hh_streaming",
        ),
    )
    tx = private.line(
        date(2026, 10, 5),
        "-7.00",
        name=SHORT,
        iban=PAYPAL_IBAN,
        purpose=_paypal("MicrosoftPay", 30),
    )
    plan = _rematch(app_engine, tmp_path, world, tx)
    assert plan.reason == "routing.no_match" and _rows(world, tx) == []


# --- Items 1 and 3: a longer spelling, a per-purchase code ----------------------------------


def test_a_longer_spelling_of_the_name_is_booked_by_the_short_names_rule(
    world, private, app_engine, tmp_path
):
    longer = [
        private.line(date(2026, m, 1), "-84.00", name="EPRIMO GMBH FLUGHAFENSTRASSE 20")
        for m in (8, 9)
    ]
    other = private.line(date(2026, 9, 2), "-84.00", name="Stadtwerke Beispiel")
    first = private.line(date(2026, 10, 1), "-84.00", name="EPRIMO GMBH")
    rule = _learn(world, first, "-84.00", "hh_energy")
    assert rule.matcher.counterparty_name_pattern == "^EPRIMO GMBH"
    _recheck(app_engine, tmp_path, world, f"rule:{rule.id}")
    for tx in longer:
        assert _booked(world, tx) == ("hh_energy", "rule", "confirmed")
    assert _rows(world, other) == []


def test_a_card_line_with_an_order_code_books_the_next_order(world, private, app_engine, tmp_path):
    waiting = private.line(date(2026, 9, 3), "-17.49", name="AMZN Mktp DE*AB7KK2Q11 5 rue plaetis")
    first = private.line(date(2026, 10, 3), "-23.90", name="AMZN Mktp DE*ZD2HG0G54 5 rue plaetis")
    rule = _learn(world, first, "-23.90", "hh_online_shopping")
    _recheck(app_engine, tmp_path, world, f"rule:{rule.id}")
    assert _booked(world, waiting) == ("hh_online_shopping", "rule", "confirmed")


# --- Item 4: one payee across a shared IBAN and a second account ----------------------------


def test_one_tick_books_lieferando_on_both_accounts(world, private, app_engine, tmp_path):
    card = SecondPrivate(world, CARD_ACCOUNT_IBAN)
    bank_waiting = private.line(date(2026, 9, 3), "-18.20", name="Lieferando", iban=CLEARING_IBAN)
    grocer = private.line(date(2026, 9, 4), "-61.15", name="KAUFLAND", iban=CLEARING_IBAN)
    private.line(date(2026, 9, 5), "-8.95", name="Drogerie Beispiel", iban=CLEARING_IBAN)
    card_waiting = [
        card.line(date(2026, 9, d), "-31.00", name="Lieferando.de lieferse Oosterdoksstraat 80")
        for d in (6, 20)
    ]
    first = private.line(date(2026, 10, 3), "-24.50", name="Lieferando", iban=CLEARING_IBAN)
    assert world.query(
        lambda c: matching.is_shared_iban(c, protect_iban(CLEARING_IBAN, TEST_IBAN_KEY).hmac)
    )

    rule = _learn(world, first, "-24.50", "hh_dining")
    assert rule.matcher.counterparty_iban_hmac is None  # name and sign: any account
    assert rule.matcher.counterparty_name_pattern == "^LIEFERANDO"
    assert set(world.query(matching.waiting_lines, "rule", rule.id)) == {
        bank_waiting,
        *card_waiting,
    }
    _recheck(app_engine, tmp_path, world, f"rule:{rule.id}")
    for tx in (bank_waiting, *card_waiting):
        assert _booked(world, tx) == ("hh_dining", "rule", "confirmed")
    assert _rows(world, grocer) == []


# --- `hausbuch rematch-waiting` --------------------------------------------------------------


def _queued_rematches(world: World) -> list[str]:
    return world.query(
        lambda c: list(
            c.execute(
                text(
                    "SELECT args->>'trigger' FROM procrastinate_jobs"
                    " WHERE task_name = 'rematch_waiting' AND status = 'todo'"
                    " ORDER BY args->>'trigger'"
                )
            ).scalars()
        )
    )


def test_rematch_waiting_counts_with_dry_run_then_queues_one_job_per_private_account(
    dbname, world, private, app_engine, tmp_path, capsys
):
    card = SecondPrivate(world, CARD_ACCOUNT_IBAN)
    # A rule learned before the deploy, on the short name: the longer spelling waited.
    world.cmd(
        rules_learned.create_rule,
        MatchingRuleCreate(
            matcher=RuleMatcher(counterparty_name_pattern="^EPRIMO GMBH", amount_sign="debit"),
            split_method=SplitMethod.SINGLE_CATEGORY,
            category="hh_energy",
        ),
    )
    longer = private.line(date(2026, 9, 1), "-84.00", name="EPRIMO GMBH FLUGHAFENSTRASSE 20")
    unknown = card.line(date(2026, 9, 2), "-12.00", name="Kiosk Beispiel")
    world.query(lambda c: c.execute(text("DELETE FROM procrastinate_jobs")))
    environ = {"HAUSBUCH_ADMIN_DATABASE_URL": _owner_url(dbname)}
    argv = ["rematch-waiting", "--workspace", str(world.ws)]

    assert cli.main([*argv, "--dry-run"], environ) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "would queue a rematch for 2 private account(s)" in out
    assert "2 private line(s) are waiting" in out
    assert _queued_rematches(world) == []

    assert cli.main(argv, environ) == cli.EXIT_OK
    out = capsys.readouterr().out
    assert "queued a rematch for 2 private account(s)" in out and "postgresql" not in out
    triggers = _queued_rematches(world)
    assert sorted(triggers) == sorted(
        enqueue.waiting_trigger(enqueue.TRIGGER_PRIVATE_LINES, account)
        for account in (private.account_id, card.account_id)
    )

    plans = _recheck(app_engine, tmp_path, world, f"private_lines:{private.account_id}")
    assert [p.outcome for p in plans] == ["written"]
    assert _booked(world, longer) == ("hh_energy", "rule", "confirmed")
    _recheck(app_engine, tmp_path, world, f"private_lines:{card.account_id}")
    assert _rows(world, unknown) == []  # no rule, no matcher, never the model


def test_the_private_lines_trigger_picks_nothing_on_a_rental_account(world, private):
    world.line(date(2026, 9, 1), "-12.00")  # a waiting line of the rental account
    kind = enqueue.TRIGGER_PRIVATE_LINES
    assert world.query(matching.waiting_lines, kind, world.account_id) == []
    assert world.query(matching.waiting_lines, kind, uuid.uuid4()) == []


def test_rematch_waiting_keeps_the_models_proposal_and_books_what_a_rule_now_hits(
    world, private, app_engine, tmp_path
):
    world.cmd(
        rules_learned.create_rule,
        MatchingRuleCreate(
            matcher=RuleMatcher(counterparty_name_pattern="^EPRIMO GMBH", amount_sign="debit"),
            split_method=SplitMethod.SINGLE_CATEGORY,
            category="hh_energy",
        ),
    )
    asked = private.line(date(2026, 9, 2), "-12.00", name="Kiosk Beispiel")
    _propose(world, asked, "-12.00", "hh_groceries")
    (before,) = _rows(world, asked)
    longer = private.line(date(2026, 9, 1), "-84.00", name="EPRIMO GMBH FLUGHAFENSTRASSE 20")
    _propose(world, longer, "-84.00", "hh_other")  # the model's guess, before the deploy

    _recheck(app_engine, tmp_path, world, f"private_lines:{private.account_id}")
    (after,) = _rows(world, asked)  # no rule hits: the proposal stays as it was
    assert (after.id, after.status, after.method) == (before.id, "proposed", "llm")
    assert _booked(world, longer) == ("hh_energy", "rule", "confirmed")


# --- Review F1: Google Pay's card form names its merchant ----------------------------------


def test_a_google_netflix_rule_books_netflix_and_leaves_supercell(
    world, private, app_engine, tmp_path
):
    netflix = private.line(date(2026, 9, 5), "-13.99", name="GOOGLE*Netflix")
    supercell = private.line(date(2026, 9, 6), "-4.99", name="GOOGLE *Supercell g.co/helppay#")
    first = private.line(date(2026, 10, 5), "-13.99", name="GOOGLE *Netflix g.co/helppay#")
    assert _view(world, first).payee_label == "Google: Netflix"
    rule = _learn(world, first, "-13.99", "hh_streaming")
    assert rule.matcher.counterparty_name_pattern == r"^GOOGLE \*NETFLIX"
    _recheck(app_engine, tmp_path, world, f"rule:{rule.id}")
    assert _booked(world, netflix) == ("hh_streaming", "rule", "confirmed")
    assert _rows(world, supercell) == []


# --- Review F2: a loose name match proposes, it does not book -------------------------------


def test_a_rule_on_one_shop_proposes_another_shop_of_the_same_first_word(
    world, private, app_engine, tmp_path
):
    apotheke = private.line(date(2026, 9, 3), "-19.90", name="ADLER APOTHEKE")
    asked = private.line(date(2026, 9, 4), "-8.40", name="ADLER APOTHEKE")
    _propose(world, asked, "-8.40", "hh_health")  # the model's answer of another category
    (before,) = _rows(world, asked)
    first = private.line(date(2026, 10, 3), "-59.90", name="ADLER MODEMARKT")
    rule = _learn(world, first, "-59.90", "hh_clothing")
    _recheck(app_engine, tmp_path, world, f"rule:{rule.id}")

    (row,) = _rows(world, apotheke)
    assert (row.category, row.method, row.status, row.rule_id) == (
        "hh_clothing",
        "rule",
        "proposed",
        rule.id,
    )
    assert row.question == "Gleicher Empfänger wie „ADLER MODEMARKT“? Bitte bestätigen."
    (after,) = _rows(world, asked)  # the model's proposal stays for the person
    assert (after.id, after.category, after.method) == (before.id, "hh_health", "llm")


# --- Review F3: a correction disables the IBAN+name rule that booked the line ---------------


def test_correcting_a_line_disables_the_iban_and_name_rule_that_booked_it(
    world, private, app_engine, tmp_path
):
    lines = _shared_world(private)
    hmac = protect_iban(CLEARING_IBAN, TEST_IBAN_KEY).hmac.hex()
    old = world.cmd(
        rules_learned.create_rule,
        MatchingRuleCreate(
            matcher=RuleMatcher(
                counterparty_iban_hmac=hmac,
                counterparty_name_pattern="^LIEFERDIENST BEISPIEL",
                amount_sign="debit",
            ),
            split_method=SplitMethod.SINGLE_CATEGORY,
            category="hh_dining",
        ),
    )
    tx = lines[DELIVERY]
    _rematch(app_engine, tmp_path, world, tx)
    assert _booked(world, tx) == ("hh_dining", "rule", "confirmed")

    new = _answer_line(world, tx, "-23.40", "hh_groceries", learn=True).rule
    assert new is not None
    assert new.matcher.counterparty_iban_hmac is None  # a name rule on a shared IBAN
    assert new.matcher.counterparty_name_pattern == old.matcher.counterparty_name_pattern
    enabled = world.query(
        lambda c: dict(c.execute(select(rule_table.c.id, rule_table.c.enabled)).all())
    )
    assert enabled == {old.id: False, new.id: True}
    assert ("rule.disabled", "user") in _events(world, old.id)

    later = private.line(date(2026, 11, 3), "-21.00", name=DELIVERY, iban=CLEARING_IBAN)
    _rematch(app_engine, tmp_path, world, later)
    (row,) = _rows(world, later)
    assert (row.category, row.method, row.status, row.rule_id) == (
        "hh_groceries",
        "rule",
        "confirmed",
        new.id,
    )
