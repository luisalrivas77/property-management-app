"""Private accounts in matching and review (docs/phases/phase-6.md D158, D159, D160, D168).

A private account of the landlord world (matching_support.World) with its own reconciled
import. The model is a FakeModelClient: a test that scripts no answer fails if a request is
sent. Invented names and IBANs only."""

import hashlib
import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from db.db_support import TEST_IBAN_KEY
from db.matching_support import PAINTER_IBAN, TENANT_IBAN, World
from sqlalchemy import insert, select

from hausbuch.ai.fake import FakeModelClient, text_response
from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import ai_call as ai_call_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import event as event_table
from hausbuch.db.tables import matching_rule as rule_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.db.tables import transfer_link as transfer_link_table
from hausbuch.jobs.document_read import JobDeps
from hausbuch.jobs.matching import run_match_transaction, run_rematch_waiting
from hausbuch.rules import matching as matchers
from hausbuch.schemas.allocation import AllocationDraft, AllocationMethod, AllocationSet
from hausbuch.schemas.api import ConfirmAllocationsBody, InboxItemKind
from hausbuch.schemas.enums import AccountPurpose, ActorType, ImportStatus
from hausbuch.services import allocations, matching, review, workspace_settings
from hausbuch.services.context import Actor
from hausbuch.services.errors import RuleViolationError
from hausbuch.services.iban import protect_iban
from hausbuch.settings import Settings

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 12, 8, 0, tzinfo=UTC)
PRIVATE_IBAN = "DE03100000000000000071"  # Bundesbank test bank code 10000000
OWNER_A = Actor(ActorType.USER, f"user:{uuid.UUID(int=0xA)}")
OWNER_B = Actor(ActorType.USER, f"user:{uuid.UUID(int=0xB)}")
NO_AI_TEXT = review._PRIVATE_NO_AI_REASON


class Private:
    """A private current account of the world, with a reconciled import of its own."""

    def __init__(self, world: World) -> None:
        self.world = world
        with workspace_transaction(world.engine, world.ws) as conn:
            self.account_id = world._account(conn, PRIVATE_IBAN, owner=True)
        self.import_id = self.add_import(world.now)

    def add_import(self, created_at: datetime) -> uuid.UUID:
        """A reconciled import of the account, created at `created_at`."""
        world = self.world
        with workspace_transaction(world.engine, world.ws) as conn:
            doc = world._document_row(conn, type="statement", subtype=None, title=None)
            return conn.execute(
                insert(import_table)
                .values(
                    workspace_id=world.ws,
                    account_id=self.account_id,
                    document_id=doc,
                    format="camt",
                    status=ImportStatus.RECONCILED.value,
                    balance_source="file",
                    declared_closing=Decimal("0.00"),
                    computed_closing=Decimal("0.00"),
                    created_at=created_at,
                    updated_at=created_at,
                )
                .returning(import_table.c.id)
            ).scalar_one()

    def line(
        self,
        booking_date: date,
        amount: str,
        *,
        name: str | None = None,
        iban: str | None = None,
        purpose: str = "",
        import_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        protected = None if iban is None else protect_iban(iban, TEST_IBAN_KEY)
        with workspace_transaction(self.world.engine, self.world.ws) as conn:
            return conn.execute(
                insert(transaction_table)
                .values(
                    workspace_id=self.world.ws,
                    account_id=self.account_id,
                    import_id=import_id or self.import_id,
                    line_hash=hashlib.sha256(uuid.uuid4().bytes).digest(),
                    booking_date=booking_date,
                    value_date=booking_date,
                    amount=Decimal(amount),
                    counterparty_name=name,
                    counterparty_iban_masked=None if protected is None else protected.masked,
                    counterparty_iban_hmac=None if protected is None else protected.hmac,
                    purpose=purpose,
                    created_at=self.world.now,
                )
                .returning(transaction_table.c.id)
            ).scalar_one()


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0], now=NOW)


@pytest.fixture
def private(world) -> Private:
    return Private(world)


def _deps(app_engine, tmp_path, client) -> JobDeps:
    return JobDeps(
        engine=app_engine,
        settings=Settings(
            database_url="postgresql+psycopg://hausbuch_app@localhost/unused",
            iban_hmac_key=TEST_IBAN_KEY,
            session_key=bytes(range(1, 33)),
            rp_id="testserver",
            origin="https://testserver",
            blob_store="local",
            blob_root=tmp_path / "blobs",
            ai_enabled=True,
            anthropic_api_key="sk-ant-test-not-a-key",
        ),
        blob_store=None,  # type: ignore[arg-type]  # matching reads no original
        model_client=client,
        clock=lambda: NOW,
    )


def _run(deps, world: World, tx):
    return run_match_transaction(deps, workspace_id=world.ws, transaction_id=tx, attempt=0)


def _rows(world: World, tx):
    return world.query(
        lambda c: c.execute(
            select(allocation_table).where(
                allocation_table.c.transaction_id == tx,
                allocation_table.c.status != "superseded",
            )
        ).all()
    )


def _consent_both(world: World) -> None:
    world.cmd(workspace_settings.update_settings, ai_private_accounts=True, actor=OWNER_A)
    world.cmd(workspace_settings.record_ai_consent, actor=OWNER_B)


def _answer(category: str, amount: str) -> str:
    return json.dumps(
        {
            "allocations": [
                {"category": category, "amount": amount, "service_period": None, "recurring": True}
            ],
            "confidence": "high",
            "reasoning": "Einkauf im Supermarkt.",
            "question": None,
            "ambiguous": False,
        }
    )


def _user_set(tx, amount: str, category: str, **scope) -> AllocationSet:
    return AllocationSet(
        transaction_id=tx,
        method=AllocationMethod.USER,
        confidence=Decimal("1.000"),
        allocations=[AllocationDraft(amount=Decimal(amount), category=category, **scope)],
    )


def _answer_line(world: World, tx, amount: str, category: str, *, learn=None):
    return world.cmd(
        review.allocate_transaction,
        tx,
        ConfirmAllocationsBody(allocation_set=_user_set(tx, amount, category), learn_rule=learn),
        iban_key=TEST_IBAN_KEY,
    )


# --- D168: the switch and the household request ---------------------------------------------


def test_a_private_line_waits_for_a_person_while_the_switch_is_off(
    world, private, app_engine, tmp_path
):
    client = FakeModelClient()  # any request fails the test
    tx = private.line(date(2026, 10, 2), "-23.80", name="Bäckerei Beispiel", purpose="Karte")
    plan = _run(_deps(app_engine, tmp_path, client), world, tx)
    assert (plan.outcome, plan.reason) == ("skipped", "routing.ai_disabled")
    assert client.requests == [] and _rows(world, tx) == []
    (item,) = world.query(review.inbox)
    assert item.kind is InboxItemKind.UNMATCHED
    assert item.reasoning == NO_AI_TEXT
    assert item.account_id == private.account_id


def test_the_switching_members_consent_is_enough(world, private):
    """D168 as amended 12 Nov 2026 (Luis): the one member who switches it on consents for the
    household; switched off again, the lines wait for a person once more."""
    world.cmd(workspace_settings.update_settings, ai_private_accounts=True, actor=OWNER_A)
    assert world.query(workspace_settings.private_ai_allowed) is True
    world.cmd(workspace_settings.update_settings, ai_private_accounts=False, actor=OWNER_A)
    assert world.query(workspace_settings.private_ai_allowed) is False


def test_the_retry_button_counts_the_private_lines_it_holds_back(world, private):
    """D50 with D168: while the switch is off, „KI-Vorschläge holen“ sends no private line and
    says how many it held back, so the screen never shows a bare 0."""
    private.line(date(2026, 10, 2), "-23.80", name="Bäckerei Beispiel", purpose="Karte")
    private.line(date(2026, 10, 3), "-9.10", name="Kiosk Beispiel", purpose="Karte")
    held = world.cmd(review.request_ai_suggestions, ai_enabled=True)
    assert (held.queued, held.private_held_back) == (0, 2)
    world.cmd(workspace_settings.update_settings, ai_private_accounts=True, actor=OWNER_A)
    sent = world.cmd(review.request_ai_suggestions, ai_enabled=True)
    assert (sent.queued, sent.private_held_back) == (2, 0)


def test_with_both_consents_the_household_prompt_sees_only_the_line(
    world, private, app_engine, tmp_path
):
    """D168: the household prompt, the account's kind, no property, tenancy or loan; the
    rental account's line keeps the rental prompt."""
    world.tenancy(name="Erika Mustermieterin")
    world.loan()
    _consent_both(world)
    assert world.query(workspace_settings.private_ai_allowed) is True
    client = FakeModelClient([text_response(_answer("hh_groceries", "-42.17"))])
    tx = private.line(
        date(2026, 10, 2),
        "-42.17",
        name="Supermarkt Beispiel",
        purpose="Einkauf, Gruss an Erika Mustermieterin DE89370400440532013000",
    )
    plan = _run(_deps(app_engine, tmp_path, client), world, tx)
    assert plan.outcome == "written"
    (request,) = client.requests
    assert request.system.startswith("You categorize one line of a German household's private")
    sent = request.content[0]["text"]
    assert '"account_kind": "bank_current"' in sent
    for absent in ("properties", "tenancies", "loans", "Lindenstraße", "Erika", "DE89370400"):
        assert absent not in sent, absent
    (row,) = _rows(world, tx)
    assert (row.category, row.method, row.status, row.property_id) == (
        "hh_groceries",
        "llm",
        "proposed",
        None,
    )
    (call,) = world.query(
        lambda c: c.execute(select(ai_call_table).where(ai_call_table.c.transaction_id == tx)).all()
    )
    assert call.prompt_version == request.prompt_version


def test_the_first_import_of_a_private_account_never_auto_accepts_the_model(
    world, private, app_engine, tmp_path
):
    """D26 per account (D168, D172): with auto_accept_llm on (as in production since the
    rental account's first month) and both consents, a model answer on the private account's
    first import goes to a person; the same answer on a later import is confirmed."""
    world.set_auto_accept_llm(True)
    _consent_both(world)
    answers = [text_response(_answer("hh_groceries", "-42.17")) for _ in range(2)]
    deps = _deps(app_engine, tmp_path, FakeModelClient(answers))

    first = private.line(date(2026, 3, 2), "-42.17", name="Supermarkt Beispiel")
    _run(deps, world, first)
    (row,) = _rows(world, first)
    assert (row.method, row.status) == ("llm", "proposed")

    later_import = private.add_import(NOW.replace(day=13))
    later = private.line(
        date(2026, 11, 12), "-42.17", name="Supermarkt Beispiel", import_id=later_import
    )
    _run(deps, world, later)
    (row,) = _rows(world, later)
    assert (row.method, row.status) == ("llm", "confirmed")


def test_switching_off_and_on_records_the_consent_afresh(world, private):
    _consent_both(world)
    world.cmd(workspace_settings.update_settings, ai_private_accounts=False, actor=OWNER_A)
    world.cmd(workspace_settings.update_settings, ai_private_accounts=True, actor=OWNER_B)
    assert world.query(workspace_settings.private_ai_consents) == [uuid.UUID(int=0xB)]
    assert world.query(workspace_settings.private_ai_allowed) is True


def test_a_job_never_consents(world):
    with pytest.raises(RuleViolationError) as refused:
        world.cmd(workspace_settings.update_settings, ai_private_accounts=True)  # "user:test"
    assert refused.value.code == "settings.consent_not_a_member"


# --- D158: a tenant's IBAN on a private line ----------------------------------------------------


def test_a_tenant_paying_into_a_private_account_is_asked_never_booked_as_rent(
    world, private, app_engine, tmp_path
):
    world.tenancy()
    _consent_both(world)
    client = FakeModelClient()  # the model never sees such a line
    tx = private.line(date(2026, 10, 3), "120.00", name="E. Mustermieterin", iban=TENANT_IBAN)
    plan = _run(_deps(app_engine, tmp_path, client), world, tx)
    assert plan.outcome == "written" and client.requests == []
    (row,) = _rows(world, tx)
    assert (row.category, row.status, row.property_id, row.tenancy_id) == (
        "hh_other_income",
        "proposed",
        world.property_id,
        None,
    )
    assert row.question == matchers.TENANT_QUESTION


# --- D159: the categories per purpose -------------------------------------------------------------


def test_a_private_line_refuses_a_rental_category_and_the_rental_line_a_household_one(
    world, private
):
    tx = private.line(date(2026, 10, 4), "-80.00", name="Handwerk Beispiel")
    with pytest.raises(RuleViolationError) as refused:
        _answer_line(world, tx, "-80.00", "repairs")
    assert refused.value.code == "allocation.category_not_for_account"
    rental = world.line(date(2026, 10, 4), "-80.00", name="Handwerk Beispiel")
    with pytest.raises(RuleViolationError) as refused:
        _answer_line(world, rental, "-80.00", "hh_household_services")
    assert refused.value.code == "allocation.category_not_for_account"
    # The model's set is refused by the same command (services.allocations.record_set).
    model_set = _user_set(tx, "-80.00", "repairs").model_copy(
        update={"method": AllocationMethod.LLM}
    )
    with pytest.raises(RuleViolationError):
        world.cmd(allocations.record_set, model_set, confirm=False, auto=False)
    assert _rows(world, tx) == [] and _rows(world, rental) == []


# --- D160: name rules ----------------------------------------------------------------------------


def test_a_name_rule_books_the_next_line_of_the_merchant_on_a_private_account_only(
    world, private, app_engine, tmp_path
):
    first = private.line(date(2026, 10, 5), "-31.20", name="SUPERMARKT BEISPIEL SAGT DANKE 123")
    result = _answer_line(world, first, "-31.20", "hh_groceries", learn=True)
    assert result.rule is not None
    assert result.rule.matcher.counterparty_name_pattern == "^SUPERMARKT BEISPIEL SAGT DANKE"
    assert result.rule.matcher.counterparty_iban_hmac is None

    deps = _deps(app_engine, tmp_path, FakeModelClient())
    second = private.line(date(2026, 10, 9), "-12.05", name="SUPERMARKT BEISPIEL SAGT DANKE 77")
    plan = _run(deps, world, second)
    assert (plan.outcome, plan.reason) == ("written", "routing.auto_confirm")
    (row,) = _rows(world, second)
    assert (row.category, row.method, row.status) == ("hh_groceries", "rule", "confirmed")

    # The rule is the workspace's, but never books a rental line (D159).
    rental = world.line(date(2026, 10, 9), "-12.05", name="SUPERMARKT BEISPIEL SAGT DANKE 77")
    plan = run_match_transaction(  # a re-match never calls the model
        _deps(app_engine, tmp_path, FakeModelClient()),
        workspace_id=world.ws,
        transaction_id=rental,
        attempt=0,
        rematch=True,
    )
    assert plan.reason == "routing.no_match" and _rows(world, rental) == []


def test_a_name_confirmed_under_two_categories_is_too_broad(world, private):
    one = private.line(date(2026, 10, 6), "-9.99", name="PayPal Europe S.a.r.l. et Cie S.C.A")
    two = private.line(date(2026, 10, 7), "-54.00", name="PayPal Europe S.a.r.l. et Cie S.C.A")
    _answer_line(world, one, "-9.99", "hh_subscriptions", learn=False)
    _answer_line(world, two, "-54.00", "hh_leisure_travel", learn=False)
    third = private.line(date(2026, 10, 8), "-20.00", name="PayPal Europe S.a.r.l. et Cie S.C.A")
    with pytest.raises(RuleViolationError) as refused:
        _answer_line(world, third, "-20.00", "hh_groceries", learn=True)
    assert refused.value.code == "rule.too_broad"
    # Without the checkbox the answer is booked and no rule is offered by default.
    result = _answer_line(world, third, "-20.00", "hh_groceries")
    assert result.rule is None and result.can_learn_rule is False
    assert world.query(lambda c: c.execute(select(rule_table)).all()) == []


def _answer_with_pattern(world: World, tx, amount: str, category: str, pattern: str):
    return world.cmd(
        review.allocate_transaction,
        tx,
        ConfirmAllocationsBody(
            allocation_set=_user_set(tx, amount, category),
            learn_rule=True,
            purpose_pattern=pattern,
        ),
        iban_key=TEST_IBAN_KEY,
    )


def test_a_private_fee_line_without_counterparty_learns_a_purpose_pattern_rule(
    world, private, app_engine, tmp_path
):
    """The account's own fee lines carry no name and no IBAN, so the card offers the purpose
    pattern only; bank_fees is learnable on a private account like a household code."""
    first = private.line(date(2026, 10, 30), "-4.95", purpose="Entgelt Kontoführung 10/2026")
    view = world.query(
        review.transaction_allocations, first, iban_key=TEST_IBAN_KEY, today=NOW.date()
    )
    assert view.can_learn_rule is False  # no counterparty: the purpose pattern field
    result = _answer_with_pattern(world, first, "-4.95", "bank_fees", "Entgelt")
    assert result.rule is not None
    assert result.rule.category == "bank_fees"
    assert result.rule.matcher.purpose_pattern == "Entgelt"
    assert result.rule.matcher.counterparty_iban_hmac is None

    deps = _deps(app_engine, tmp_path, FakeModelClient())
    second = private.line(date(2026, 11, 30), "-4.95", purpose="Entgelt Kontoführung 11/2026")
    plan = _run(deps, world, second)
    assert (plan.outcome, plan.reason) == ("written", "routing.auto_confirm")
    (row,) = _rows(world, second)
    assert (row.category, row.method, row.status) == ("bank_fees", "rule", "confirmed")

    # bank_fees is a code both purposes take, so the rule stays with the purpose it was
    # learned on: the rental account's fee line is not booked by the private rule.
    rental = world.line(date(2026, 11, 30), "-7.50", purpose="Entgelt Kontoführung 11/2026")
    plan = run_match_transaction(  # a re-match never calls the model
        _deps(app_engine, tmp_path, FakeModelClient()),
        workspace_id=world.ws,
        transaction_id=rental,
        attempt=0,
        rematch=True,
    )
    assert plan.reason == "routing.no_match" and _rows(world, rental) == []


def test_the_rental_and_a_private_account_each_keep_their_fee_rule(world, private):
    """The same typed pattern learned on both accounts is two answers, not one given twice:
    neither rule disables the other, and each books its own account's fee lines only."""
    mine = private.line(date(2026, 10, 30), "-4.95", purpose="Entgelt Kontoführung 10/2026")
    private_rule = _answer_with_pattern(world, mine, "-4.95", "bank_fees", "Entgelt").rule
    rental = world.line(date(2026, 10, 30), "-7.50", purpose="Entgelt Kontoführung 10/2026")
    rental_rule = _answer_with_pattern(world, rental, "-7.50", "bank_fees", "Entgelt").rule
    assert private_rule is not None and rental_rule is not None
    assert private_rule.matcher == rental_rule.matcher
    enabled = world.query(
        lambda c: (
            c.execute(select(rule_table.c.id).where(rule_table.c.enabled.is_(True))).scalars().all()
        )
    )
    assert set(enabled) == {private_rule.id, rental_rule.id}
    # The same account learning it again still replaces its own rule (review F4).
    again = private.line(date(2026, 11, 30), "-4.95", purpose="Entgelt Kontoführung 11/2026")
    newer = _answer_with_pattern(world, again, "-4.95", "bank_fees", "Entgelt").rule
    enabled = world.query(
        lambda c: (
            c.execute(select(rule_table.c.id).where(rule_table.c.enabled.is_(True))).scalars().all()
        )
    )
    assert set(enabled) == {newer.id, rental_rule.id}


def test_the_line_view_names_the_categories_no_rule_is_learned_for(world, private):
    """The box is greyed for these instead of rule.not_learnable refusing the answer."""
    tx = private.line(date(2026, 10, 3), "-250.00", name="Kartenabrechnung Beispiel")
    view = world.query(review.transaction_allocations, tx, iban_key=TEST_IBAN_KEY, today=NOW.date())
    unlearnable = set(view.unlearnable_categories)
    assert {
        "hh_credit_card",
        "internal_transfer_in",
        "internal_transfer_out",
        "hh_donation",
        "hh_property_cost",
        "capital_income_tax",
        "hh_other",
        "uncategorized",
    } <= unlearnable
    assert not {"hh_groceries", "hh_salary", "bank_fees"} & unlearnable
    # Each one is refused by the PUT, so the list says what the server does.
    for category in unlearnable:
        assert not matchers.learnable_household(category)
    rental = world.line(date(2026, 10, 3), "-250.00", name="Kartenabrechnung Beispiel")
    rental_view = world.query(
        review.transaction_allocations, rental, iban_key=TEST_IBAN_KEY, today=NOW.date()
    )
    assert rental_view.unlearnable_categories == []


def test_uncategorized_on_a_private_line_still_learns_no_rule(world, private):
    tx = private.line(date(2026, 10, 30), "-4.95", purpose="Entgelt Kontoführung 10/2026")
    with pytest.raises(RuleViolationError) as refused:
        _answer_with_pattern(world, tx, "-4.95", "uncategorized", "Entgelt")
    assert refused.value.code == "rule.not_learnable"
    assert _rows(world, tx) == []


# --- Review of PR #221 (F1, F2): a payee both accounts pay ---------------------------------


def test_a_private_line_of_the_same_payee_neither_breaks_a_rental_rule_nor_reaches_its_prompt(
    world, private, app_engine, tmp_path
):
    """The rental rule's D49 amount test reads rental lines only, and a rental prompt's
    similar answers hold no private line (D158, D168), whatever the switch says."""
    name = "Versicherung Beispiel AG"
    first = world.line(date(2026, 9, 1), "-100.00", iban=PAINTER_IBAN, name=name)
    assert _answer_line(world, first, "-100.00", "insurance", learn=True).rule is not None
    mine = private.line(date(2026, 9, 15), "-60.00", iban=PAINTER_IBAN, name=name)
    _answer_line(world, mine, "-60.00", "hh_insurance", learn=False)

    rental = world.line(date(2026, 10, 1), "-100.00", iban=PAINTER_IBAN, name=name)
    run_match_transaction(  # a re-match never calls the model
        _deps(app_engine, tmp_path, FakeModelClient()),
        workspace_id=world.ws,
        transaction_id=rental,
        attempt=0,
        rematch=True,
    )
    history = world.query(
        lambda c: matching.rule_history(c, matching.load_line(c, rental).tx, AccountPurpose.RENTAL)
    )
    assert {h.transaction_id for h in history} == {first}
    (row,) = _rows(world, rental)
    assert (row.category, row.method, row.status, row.confidence) == (
        "insurance",
        "rule",
        "confirmed",
        Decimal("1.000"),
    )

    other = world.line(date(2026, 10, 5), "-77.00", iban=PAINTER_IBAN, name=name)
    similar = world.query(
        lambda c: matching.categorize_input(c, matching.load_line(c, other)).similar
    )
    assert similar, "the rental lines of the payee are still offered"
    assert all(not cat.startswith("hh_") for s in similar for cat in s.categories)
    assert Decimal("-60.00") not in {s.amount for s in similar}


# --- D160 amended: a shared counterparty IBAN ------------------------------------------------

# One clearing IBAN on every card payment, as DKB's export writes it; invented.
CLEARING_IBAN = "DE55100000000000000881"
SINGLE_IBAN = "DE28100000000000000882"
DELIVERY = "Lieferdienst Beispiel"
GROCER = "Supermarkt Beispiel"
CHEMIST = "Drogerie Beispiel"


def _rematch(app_engine, tmp_path, world: World, tx):
    return run_match_transaction(  # a re-match never calls the model
        _deps(app_engine, tmp_path, FakeModelClient()),
        workspace_id=world.ws,
        transaction_id=tx,
        attempt=0,
        rematch=True,
    )


def _shared_world(private: Private) -> dict[str, uuid.UUID]:
    """Three payees on the clearing IBAN: the IBAN is shared."""
    return {
        name: private.line(date(2026, 9, day), amount, name=name, iban=CLEARING_IBAN)
        for name, day, amount in (
            (DELIVERY, 3, "-23.40"),
            (GROCER, 4, "-61.15"),
            (CHEMIST, 5, "-8.95"),
        )
    }


def test_a_shared_iban_learns_the_iban_and_the_name_and_books_that_payee_only(
    world, private, app_engine, tmp_path
):
    lines = _shared_world(private)
    assert world.query(
        lambda c: matching.is_shared_iban(c, protect_iban(CLEARING_IBAN, TEST_IBAN_KEY).hmac)
    )
    # Another payee of the IBAN confirmed under another category: unfiltered, the IBAN's
    # history would look like two kinds of payment and the rule would stand at one amount only.
    _answer_line(world, lines[GROCER], "-61.15", "hh_groceries", learn=False)
    result = _answer_line(world, lines[DELIVERY], "-23.40", "hh_dining", learn=True)
    assert result.rule is not None
    matcher = result.rule.matcher
    assert matcher.counterparty_iban_hmac == protect_iban(CLEARING_IBAN, TEST_IBAN_KEY).hmac.hex()
    assert matcher.counterparty_name_pattern == "^LIEFERDIENST BEISPIEL"

    # The same payee at another amount: booked by the rule at once.
    again = private.line(date(2026, 10, 2), "-31.70", name=DELIVERY, iban=CLEARING_IBAN)
    _rematch(app_engine, tmp_path, world, again)
    (row,) = _rows(world, again)
    assert (row.category, row.method, row.status, row.confidence) == (
        "hh_dining",
        "rule",
        "confirmed",
        Decimal("1.000"),
    )
    # Another payee of the same IBAN: never booked by it.
    grocer = private.line(date(2026, 10, 2), "-31.70", name=GROCER, iban=CLEARING_IBAN)
    plan = _rematch(app_engine, tmp_path, world, grocer)
    assert plan.reason == "routing.no_match" and _rows(world, grocer) == []
    # The waiting chemist line was not touched by the new rule's re-check either.
    assert _rows(world, lines[CHEMIST]) == []


def test_an_iban_only_rule_learned_before_the_iban_was_shared_no_longer_applies(
    world, private, app_engine, tmp_path
):
    first = private.line(date(2026, 8, 3), "-23.40", name=DELIVERY, iban=CLEARING_IBAN)
    result = _answer_line(world, first, "-23.40", "hh_dining", learn=True)
    assert result.rule is not None
    assert result.rule.matcher.counterparty_name_pattern is None  # one payee: the IBAN alone

    _shared_world(private)  # two more payees arrive: now the IBAN is shared
    grocer = private.line(date(2026, 10, 2), "-44.10", name=GROCER, iban=CLEARING_IBAN)
    plan = _rematch(app_engine, tmp_path, world, grocer)
    assert plan.reason == "routing.no_match" and _rows(world, grocer) == []
    # Not even its own payee: the rule on the IBAN alone is off on a shared IBAN.
    delivery = private.line(date(2026, 10, 3), "-23.40", name=DELIVERY, iban=CLEARING_IBAN)
    _rematch(app_engine, tmp_path, world, delivery)
    assert _rows(world, delivery) == []
    # The new rule's re-check of waiting lines would pick none of them.
    assert (
        world.query(lambda c: matching.waiting_lines(c, "rule", result.rule.id)) == []  # type: ignore[union-attr]
    )


def test_the_waiting_amounts_on_a_shared_iban_are_the_payees_own(world, private):
    lines = _shared_world(private)
    private.line(date(2026, 9, 10), "-18.90", name=DELIVERY, iban=CLEARING_IBAN)
    private.line(date(2026, 9, 12), "-18.90", name="LIEFERDIENST BEISPIEL", iban=CLEARING_IBAN)
    private.line(date(2026, 9, 14), "-12.00", name=GROCER, iban=CLEARING_IBAN)
    view = world.query(
        lambda c: review.transaction_allocations(
            c, lines[DELIVERY], iban_key=TEST_IBAN_KEY, today=NOW.date()
        )
    )
    assert view.can_learn_rule is True
    assert [(w.amount, w.count) for w in view.waiting_other_amounts] == [(Decimal("-18.90"), 2)]
    # The totals count the payee's own lines only, never the grocer's on the same IBAN.
    assert view.waiting_other_count == 2
    # The model's similar answers for the line: only its own payee's.
    _answer_line(world, lines[GROCER], "-61.15", "hh_groceries", learn=False)
    similar = world.query(
        lambda c: (
            matching.household_categorize_input(c, matching.load_line(c, lines[DELIVERY])).similar
        )
    )
    assert similar == []


def test_a_single_payee_iban_behaves_as_before(world, private, app_engine, tmp_path):
    first = private.line(
        date(2026, 9, 3), "-29.99", name="Fitnessstudio Beispiel", iban=SINGLE_IBAN
    )
    private.line(date(2026, 9, 10), "-35.00", name="Fitnessstudio Beispiel", iban=SINGLE_IBAN)
    private.line(date(2026, 9, 17), "-35.00", name="FITNESSSTUDIO BEISPIEL", iban=SINGLE_IBAN)
    view = world.query(
        lambda c: review.transaction_allocations(c, first, iban_key=TEST_IBAN_KEY, today=NOW.date())
    )
    assert [(w.amount, w.count) for w in view.waiting_other_amounts] == [(Decimal("-35.00"), 2)]
    result = _answer_line(world, first, "-29.99", "hh_sport", learn=True)
    assert result.rule is not None
    assert result.rule.matcher.counterparty_name_pattern is None
    assert result.rule.matcher.counterparty_iban_hmac is not None
    later = private.line(
        date(2026, 10, 3), "-29.99", name="FITNESSSTUDIO BEISPIEL", iban=SINGLE_IBAN
    )
    _rematch(app_engine, tmp_path, world, later)
    (row,) = _rows(world, later)
    assert (row.category, row.method, row.status) == ("hh_sport", "rule", "confirmed")
    # A second payee on the IBAN makes it shared (D160 amended, two names): the rule on the
    # IBAN alone stops, so the other payee's line is never booked by it and is asked again.
    other = private.line(date(2026, 10, 4), "-29.99", name="Anderer Name", iban=SINGLE_IBAN)
    plan = _rematch(app_engine, tmp_path, world, other)
    assert plan.reason == "routing.no_match" and _rows(world, other) == []


# --- D160, 14 Nov 2026: re-book the name's earlier lines when learning its rule --------------


def _answer_rebook(world: World, tx, amount: str, category: str, *, rebook=True, learn=True):
    return world.cmd(
        review.allocate_transaction,
        tx,
        ConfirmAllocationsBody(
            allocation_set=_user_set(tx, amount, category),
            learn_rule=learn,
            rebook_earlier=rebook,
        ),
        iban_key=TEST_IBAN_KEY,
    )


def _all_rows(world: World, tx):
    return world.query(
        lambda c: c.execute(
            select(allocation_table)
            .where(allocation_table.c.transaction_id == tx)
            # the commands share one clock: a re-booked row is the one that supersedes
            .order_by(allocation_table.c.supersedes_id.is_not(None), allocation_table.c.id)
        ).all()
    )


def _events(world: World, tx) -> list[tuple[str, str]]:
    return world.query(
        lambda c: [
            tuple(row)
            for row in c.execute(
                select(event_table.c.type, event_table.c.actor_type)
                .where(event_table.c.entity_id == tx)
                .order_by(event_table.c.seq)
            )
        ]
    )


def _earlier(private: Private, category: str, n: int = 4, iban: str | None = None):
    """`n` earlier delivery lines, each confirmed by a person as `category`."""
    lines = []
    for day in range(1, n + 1):
        amount = f"-{18 + day}.90"
        tx = private.line(date(2026, 9, day), amount, name=DELIVERY, iban=iban)
        _answer_line(private.world, tx, amount, category, learn=False)
        lines.append(tx)
    return lines


def _view(world: World, tx):
    return world.query(
        lambda c: review.transaction_allocations(c, tx, iban_key=TEST_IBAN_KEY, today=NOW.date())
    )


def test_learning_a_name_rule_re_books_the_names_earlier_lines(
    world, private, app_engine, tmp_path
):
    earlier = _earlier(private, "hh_leisure_travel")
    now = private.line(date(2026, 11, 2), "-27.50", name="LIEFERDIENST BEISPIEL 4711")
    view = _view(world, now)
    assert view.can_learn_rule is True
    assert [(h.category, h.count) for h in view.name_history] == [("hh_leisure_travel", 4)]

    # Without the flag: refused as before, and nothing is written.
    with pytest.raises(RuleViolationError) as refused:
        _answer_rebook(world, now, "-27.50", "hh_dining", rebook=False)
    assert refused.value.code == "rule.conflicts"
    assert _rows(world, now) == []

    result = _answer_rebook(world, now, "-27.50", "hh_dining")
    assert result.rebooked == 4
    assert result.rule is not None and result.rule.category == "hh_dining"
    assert result.rule.matcher.counterparty_name_pattern == "^LIEFERDIENST BEISPIEL"
    assert [(h.category, h.count) for h in result.name_history] == [("hh_dining", 4)]
    for tx in earlier:
        old, new = _all_rows(world, tx)
        assert (old.category, old.status) == ("hh_leisure_travel", "superseded")
        assert (new.category, new.status, new.method) == ("hh_dining", "confirmed", "user")
        assert new.supersedes_id == old.id and new.confirmed_at is not None
        assert new.amount == old.amount
        assert _events(world, tx) == [
            ("transaction.allocated", "user"),
            ("transaction.reallocated", "user"),
        ]

    # The rule books the payee's next line.
    later = private.line(date(2026, 11, 9), "-31.00", name="Lieferdienst Beispiel")
    _rematch(app_engine, tmp_path, world, later)
    (row,) = _rows(world, later)
    assert (row.category, row.method, row.status) == ("hh_dining", "rule", "confirmed")


def test_a_rule_whose_bookings_are_re_booked_is_replaced(world, private, app_engine, tmp_path):
    first = private.line(date(2026, 9, 1), "-19.90", name=DELIVERY)
    old_rule = _answer_line(world, first, "-19.90", "hh_leisure_travel", learn=True).rule
    assert old_rule is not None
    booked = private.line(date(2026, 9, 8), "-21.90", name=DELIVERY)
    _rematch(app_engine, tmp_path, world, booked)
    assert _rows(world, booked)[0].rule_id == old_rule.id

    now = private.line(date(2026, 11, 2), "-27.50", name=DELIVERY)
    result = _answer_rebook(world, now, "-27.50", "hh_dining")
    assert result.rebooked == 2 and result.rule is not None
    enabled = world.query(
        lambda c: (
            c.execute(select(rule_table.c.id).where(rule_table.c.enabled.is_(True))).scalars().all()
        )
    )
    assert enabled == [result.rule.id]  # the old rule on the same name is switched off


def test_a_waiting_or_already_matching_line_is_not_re_booked(world, private):
    (same,) = _earlier(private, "hh_dining", n=1)
    other = _earlier(private, "hh_leisure_travel", n=2)
    waiting = private.line(date(2026, 10, 1), "-15.00", name=DELIVERY)  # no rows
    refund = private.line(date(2026, 10, 2), "12.00", name=DELIVERY)
    _answer_line(world, refund, "12.00", "hh_refund", learn=False)
    now = private.line(date(2026, 11, 2), "-27.50", name=DELIVERY)
    result = _answer_rebook(world, now, "-27.50", "hh_dining")
    assert result.rebooked == 2
    assert len(_all_rows(world, same)) == 1  # already dining: untouched
    assert all(_rows(world, tx)[0].category == "hh_dining" for tx in other)
    assert _rows(world, waiting) == []
    assert [r.category for r in _rows(world, refund)] == ["hh_refund"]  # the other sign


def _odd_line(world: World, private: Private, blocker: str) -> uuid.UUID:
    """An earlier delivery line, confirmed by a person, that the re-book must refuse."""
    odd = private.line(date(2026, 10, 3), "-40.00", name=DELIVERY)
    if blocker == "split":
        answer = AllocationSet(
            transaction_id=odd,
            method=AllocationMethod.USER,
            confidence=Decimal("1.000"),
            allocations=[
                AllocationDraft(amount=Decimal("-25.00"), category="hh_dining"),
                AllocationDraft(amount=Decimal("-15.00"), category="hh_groceries"),
            ],
        )
        world.cmd(
            review.allocate_transaction,
            odd,
            ConfirmAllocationsBody(allocation_set=answer, learn_rule=False),
            iban_key=TEST_IBAN_KEY,
        )
    elif blocker == "category":
        _answer_line(world, odd, "-40.00", "internal_transfer_out", learn=False)
    elif blocker == "document":
        doc = world.query(
            lambda c: world._document_row(c, type="invoice", subtype=None, title=None)
        )
        answer = AllocationSet(
            transaction_id=odd,
            method=AllocationMethod.USER,
            confidence=Decimal("1.000"),
            allocations=[
                AllocationDraft(
                    amount=Decimal("-40.00"), category="hh_leisure_travel", document_id=doc
                )
            ],
        )
        world.cmd(
            review.allocate_transaction,
            odd,
            ConfirmAllocationsBody(allocation_set=answer, learn_rule=False),
            iban_key=TEST_IBAN_KEY,
        )
    else:  # linked: a learnable code, but the line is a leg of a transfer link (D162)
        _answer_line(world, odd, "-40.00", "hh_leisure_travel", learn=False)
        other = private.line(date(2026, 10, 3), "40.00", name="Eigenes Konto Beispiel")
        world.query(
            lambda c: c.execute(
                insert(transfer_link_table).values(
                    workspace_id=world.ws,
                    kind="bank",
                    out_transaction_id=odd,
                    in_transaction_id=other,
                    matched_by="user",
                )
            )
        )
    return odd


# The reason each blocker gives, and the category its one row carries (None for a split).
_BLOCKERS = {
    "split": ("split", None),
    "category": ("category", "internal_transfer_out"),
    "document": ("document", "hh_leisure_travel"),
    "linked": ("linked", "hh_leisure_travel"),
}


@pytest.mark.parametrize("blocker", list(_BLOCKERS))
def test_an_earlier_line_that_cannot_be_re_booked_refuses_all(world, private, blocker):
    """rule.rebook_blocked (review fix, 14 Nov 2026): this PR's own test, updated from the
    name rule's refusal (rule.conflicts / rule.too_broad) to the code that says the re-book
    itself is blocked, naming the kind of block and the line's date and amount."""
    earlier = _earlier(private, "hh_leisure_travel", n=2)
    odd = _odd_line(world, private, blocker)
    now = private.line(date(2026, 11, 2), "-27.50", name=DELIVERY)
    with pytest.raises(RuleViolationError) as refused:
        _answer_rebook(world, now, "-27.50", "hh_dining")
    reason, _ = _BLOCKERS[blocker]
    assert refused.value.code == "rule.rebook_blocked"
    message = str(refused.value)
    assert f"({reason})" in message and str(odd) in message
    assert "2026-10-03" in message and "-40.00" in message
    assert "LIEFERDIENST" not in message.upper()  # never the counterparty
    for tx in earlier:
        assert [r.category for r in _all_rows(world, tx)] == ["hh_leisure_travel"]
    assert _rows(world, now) == []
    assert world.query(lambda c: c.execute(select(rule_table)).all()) == []


@pytest.mark.parametrize("blocker", list(_BLOCKERS))
def test_the_line_view_names_the_earlier_line_that_blocks_the_re_book(world, private, blocker):
    _earlier(private, "hh_leisure_travel", n=2)
    odd = _odd_line(world, private, blocker)
    now = private.line(date(2026, 11, 2), "-27.50", name=DELIVERY)
    view = _view(world, now)
    assert view.name_history  # the box is about to be offered
    reason, category = _BLOCKERS[blocker]
    assert [
        (b.transaction_id, b.booking_date, b.amount, b.reason, b.category)
        for b in view.rebook_blockers
    ] == [(odd, date(2026, 10, 3), Decimal("-40.00"), reason, category)]


def test_the_line_view_has_no_blocker_when_every_earlier_line_can_be_re_booked(world, private):
    _earlier(private, "hh_leisure_travel", n=2)
    waiting = private.line(date(2026, 10, 1), "-15.00", name=DELIVERY)  # no rows: not a blocker
    now = private.line(date(2026, 11, 2), "-27.50", name=DELIVERY)
    assert _view(world, now).rebook_blockers == []
    assert _view(world, waiting).rebook_blockers == []


def test_too_broad_is_re_booked_with_the_flag(world, private):
    _earlier(private, "hh_leisure_travel", n=2)
    other = private.line(date(2026, 10, 3), "-40.00", name=DELIVERY)
    _answer_line(world, other, "-40.00", "hh_groceries", learn=False)
    now = private.line(date(2026, 11, 2), "-27.50", name=DELIVERY)
    with pytest.raises(RuleViolationError) as refused:
        _answer_rebook(world, now, "-27.50", "hh_dining", rebook=False)
    assert refused.value.code == "rule.too_broad"
    result = _answer_rebook(world, now, "-27.50", "hh_dining")
    assert result.rebooked == 3 and result.rule is not None


def test_the_flag_changes_nothing_without_a_refusal_or_a_rule(world, private):
    earlier = _earlier(private, "hh_leisure_travel", n=2)
    now = private.line(date(2026, 11, 2), "-27.50", name=DELIVERY)
    result = _answer_rebook(world, now, "-27.50", "hh_dining", learn=False)
    assert result.rebooked == 0 and result.rule is None
    for tx in earlier:
        assert len(_all_rows(world, tx)) == 1
    fresh = private.line(date(2026, 11, 3), "-9.00", name="Kiosk Beispiel")
    result = _answer_rebook(world, fresh, "-9.00", "hh_dining")
    assert result.rebooked == 0 and result.rule is not None


def test_on_a_shared_iban_the_payees_lines_are_re_booked(world, private):
    lines = _shared_world(private)
    _answer_line(world, lines[GROCER], "-61.15", "hh_groceries", learn=False)
    earlier = _earlier(private, "hh_leisure_travel", n=2, iban=CLEARING_IBAN)
    now = private.line(date(2026, 11, 2), "-27.50", name=DELIVERY, iban=CLEARING_IBAN)
    assert [(h.category, h.count) for h in _view(world, now).name_history] == [
        ("hh_leisure_travel", 2)
    ]
    result = _answer_rebook(world, now, "-27.50", "hh_dining")
    assert result.rebooked == 2
    assert result.rule is not None
    assert result.rule.matcher.counterparty_iban_hmac is not None
    assert result.rule.matcher.counterparty_name_pattern == "^LIEFERDIENST BEISPIEL"
    assert all(_rows(world, tx)[0].category == "hh_dining" for tx in earlier)
    assert [r.category for r in _rows(world, lines[GROCER])] == ["hh_groceries"]


def test_a_single_payee_iban_shows_no_name_history(world, private):
    first = private.line(date(2026, 9, 3), "-29.99", name="Fitness Beispiel", iban=SINGLE_IBAN)
    _answer_line(world, first, "-29.99", "hh_sport", learn=False)
    now = private.line(date(2026, 10, 3), "-29.99", name="Fitness Beispiel", iban=SINGLE_IBAN)
    assert _view(world, now).name_history == []  # the rule is on the IBAN, not the name


# --- A suggested purpose pattern for a line without an IBAN (14 Nov 2026) ------------------

FEE_PURPOSE = "Entgeltabrechnung siehe Anlage Abrechnung {n}"


def _propose(world: World, tx, amount: str, category: str, **scope) -> None:
    """A proposed row, as the model or a rule's near miss leaves it (no model call here)."""
    found = AllocationSet(
        transaction_id=tx,
        method=AllocationMethod.LLM,
        confidence=Decimal("0.700"),
        reasoning="Kontoentgelt der Bank.",
        allocations=[AllocationDraft(amount=Decimal(amount), category=category, **scope)],
    )
    world.cmd(allocations.record_set, found, confirm=False, auto=False)


def _fee(private: Private, month: int, amount: str = "-4.95", **kw) -> uuid.UUID:
    return private.line(date(2026, month, 28), amount, purpose=FEE_PURPOSE.format(n=month), **kw)


def test_a_fee_line_without_an_iban_gets_the_purposes_first_word(world, private):
    """The panel proposes „Entgeltabrechnung“ so the person need not know what to type, and
    says how many waiting lines the rule would book: the private ones only (a rule learned on
    a private line never books a rental line)."""
    first = _fee(private, 3)
    _propose(world, first, "-4.95", "bank_fees")
    waiting = [_fee(private, 4), _fee(private, 5, "-9.90")]  # no rows
    proposed_other = _fee(private, 6)
    _propose(world, proposed_other, "-4.95", "hh_other")  # proposed only: still waiting
    confirmed_same = _fee(private, 7)
    _answer_line(world, confirmed_same, "-4.95", "bank_fees", learn=False)
    world.line(date(2026, 8, 28), "-7.50", purpose="Entgeltabrechnung siehe Anlage")  # rental
    private.line(date(2026, 8, 28), "4.95", purpose="Entgeltabrechnung Erstattung")  # a credit

    view = _view(world, first)
    assert view.can_learn_rule is False
    assert view.suggested_purpose_pattern == "^Entgeltabrechnung"
    assert view.suggested_pattern_waiting == len(waiting) + 1  # the proposed hh_other line
    assert view.learn_rule_default is True  # bank_fees has a threshold: the box starts ticked


def test_no_suggestion_when_a_line_with_an_iban_starts_with_the_word(world, private):
    first = _fee(private, 3)
    _propose(world, first, "-4.95", "bank_fees")
    private.line(
        date(2026, 4, 2),
        "-30.00",
        name="Partei Beispiel",
        iban=PAINTER_IBAN,
        purpose="Entgeltabrechnung Rechnung 17",
    )
    view = _view(world, first)
    assert view.suggested_purpose_pattern is None
    assert view.suggested_pattern_waiting == 0
    assert view.learn_rule_default is False  # as before: nothing to learn without a pattern


def test_no_suggestion_when_a_line_with_the_word_is_confirmed_otherwise(world, private):
    first = _fee(private, 3)
    _propose(world, first, "-4.95", "bank_fees")
    other = _fee(private, 4)
    _answer_line(world, other, "-4.95", "hh_other", learn=False)
    assert _view(world, first).suggested_purpose_pattern is None


def test_a_rental_line_confirmed_otherwise_also_refuses_the_private_suggestion(world, private):
    """Every account is checked, as rule_hits reads every line: stricter than the rule's
    reach (a private fee rule never books a rental line), so it can only over-refuse."""
    first = _fee(private, 3)
    _propose(world, first, "-4.95", "bank_fees")
    rental = world.line(date(2026, 4, 28), "-7.50", purpose="Entgeltabrechnung siehe Anlage")
    world.cmd(
        review.allocate_transaction,
        rental,
        ConfirmAllocationsBody(
            allocation_set=_user_set(rental, "-7.50", "other_expense"), learn_rule=False
        ),
        iban_key=TEST_IBAN_KEY,
    )
    assert _view(world, first).suggested_purpose_pattern is None


def test_an_iban_line_never_gets_a_suggested_pattern(world, private):
    tx = private.line(
        date(2026, 4, 2),
        "-4.95",
        name="Bank Beispiel",
        iban=PAINTER_IBAN,
        purpose="Entgeltabrechnung siehe Anlage",
    )
    _propose(world, tx, "-4.95", "bank_fees")
    view = _view(world, tx)
    assert view.can_learn_rule is True
    assert view.suggested_purpose_pattern is None


@pytest.mark.parametrize(
    ("purpose", "expected"),
    [
        ("", None),
        ("Karte 10/26", None),
        ("10/2026 Entgeltabrechnung", None),
        ("Entgelt Kontoführung 10/2026", "^Entgelt"),  # seven letters
        ("A" * 250, None),  # longer than a rule's pattern may be: no suggestion, no error
    ],
)
def test_the_suggestion_needs_a_long_first_word(world, private, purpose, expected):
    tx = private.line(date(2026, 10, 28), "-4.95", purpose=purpose)
    _propose(world, tx, "-4.95", "bank_fees")
    assert _view(world, tx).suggested_purpose_pattern == expected


def test_no_suggestion_without_rows_or_for_a_category_no_rule_learns(world, private):
    bare = _fee(private, 3)
    assert _view(world, bare).suggested_purpose_pattern is None  # nothing to learn yet
    card = private.line(date(2026, 4, 28), "-4.95", purpose="Kreditkartenabrechnung 04")
    _propose(world, card, "-4.95", "hh_credit_card")  # no threshold: never a rule (D160)
    assert _view(world, card).suggested_purpose_pattern is None


def test_a_rental_fee_line_without_an_iban_gets_one_too(world, private):
    first = world.line(date(2026, 3, 31), "-7.50", purpose="Entgeltabrechnung siehe Anlage")
    _propose(world, first, "-7.50", "bank_fees")
    world.line(date(2026, 4, 30), "-7.50", purpose="Entgeltabrechnung siehe Anlage")
    _fee(private, 4)  # waiting on a private account: a rental rule never books it
    view = _view(world, first)
    assert view.suggested_purpose_pattern == "^Entgeltabrechnung"
    assert view.suggested_pattern_waiting == 1


@pytest.mark.parametrize(
    ("purpose", "category", "amount"),
    [
        ("Bareinzahlung Filiale", "owner_transfer_in", "500.00"),  # a matcher's (D40)
        ("Darlehenszinsen 10/2026", "loan_interest", "-310.00"),  # a matcher's (D40)
        ("Handwerkerrechnung 17", "repairs", "-310.00"),  # never auto-confirmed
    ],
)
def test_no_suggestion_for_a_matchers_category_or_one_without_a_threshold(
    world, private, purpose, category, amount
):
    """Without an IBAN no amount check guards the rule, so a one-click rule booking any
    „Bareinzahlung …“ as an owner transfer is never offered; a typed pattern still may."""
    scope = {"loan_id": world.loan()} if category == "loan_interest" else {}
    tx = world.line(date(2026, 10, 28), amount, purpose=purpose)
    _propose(world, tx, amount, category, property_id=world.property_id, **scope)
    view = _view(world, tx)
    assert view.can_learn_rule is False
    assert view.suggested_purpose_pattern is None
    assert view.suggested_pattern_waiting == 0
    assert view.learn_rule_default is False


def test_answering_with_the_suggested_pattern_books_the_waiting_lines(
    world, private, app_engine, tmp_path
):
    first = _fee(private, 3)
    _propose(world, first, "-4.95", "bank_fees")
    waiting = [_fee(private, m) for m in (4, 5, 6)]
    view = _view(world, first)
    assert view.suggested_pattern_waiting == len(waiting)

    pattern = view.suggested_purpose_pattern
    result = _answer_with_pattern(world, first, "-4.95", "bank_fees", pattern)
    rule = result.rule
    assert rule is not None
    assert rule.matcher.purpose_pattern == "^Entgeltabrechnung"
    assert rule.matcher.counterparty_iban_hmac is None
    assert world.query(matching.waiting_lines, "rule", rule.id) == waiting

    client = FakeModelClient()  # any model request fails the test
    plans = run_rematch_waiting(
        _deps(app_engine, tmp_path, client),
        workspace_id=world.ws,
        trigger=f"rule:{rule.id}",
        attempt=0,
    )
    assert [p.outcome for p in plans] == ["written"] * view.suggested_pattern_waiting
    for tx in waiting:
        (row,) = _rows(world, tx)
        assert (row.category, row.method, row.status, row.rule_id) == (
            "bank_fees",
            "rule",
            "confirmed",
            rule.id,
        )
    assert client.requests == []
