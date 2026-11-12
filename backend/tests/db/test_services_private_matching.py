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
from db.matching_support import TENANT_IBAN, World
from sqlalchemy import insert, select

from hausbuch.ai.fake import FakeModelClient, text_response
from hausbuch.db.session import workspace_transaction
from hausbuch.db.tables import ai_call as ai_call_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import matching_rule as rule_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.jobs.document_read import JobDeps
from hausbuch.jobs.matching import run_match_transaction
from hausbuch.rules import matching as matchers
from hausbuch.schemas.allocation import AllocationDraft, AllocationMethod, AllocationSet
from hausbuch.schemas.api import ConfirmAllocationsBody, InboxItemKind
from hausbuch.schemas.enums import ActorType, ImportStatus
from hausbuch.services import allocations, review, workspace_settings
from hausbuch.services.context import Actor
from hausbuch.services.errors import RuleViolationError
from hausbuch.services.iban import protect_iban
from hausbuch.settings import Settings

pytestmark = pytest.mark.db

NOW = datetime(2026, 11, 12, 8, 0, tzinfo=UTC)
PRIVATE_IBAN = "DE02100000000000000071"  # Bundesbank test bank code 10000000
OWNER_A = Actor(ActorType.USER, f"user:{uuid.UUID(int=0xA)}")
OWNER_B = Actor(ActorType.USER, f"user:{uuid.UUID(int=0xB)}")
NO_AI_TEXT = review._PRIVATE_NO_AI_REASON


class Private:
    """A private current account of the world, with a reconciled import of its own."""

    def __init__(self, world: World) -> None:
        self.world = world
        with workspace_transaction(world.engine, world.ws) as conn:
            self.account_id = world._account(conn, PRIVATE_IBAN, owner=True)
            doc = world._document_row(conn, type="statement", subtype=None, title=None)
            self.import_id = conn.execute(
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
                    created_at=world.now,
                    updated_at=world.now,
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
    ) -> uuid.UUID:
        protected = None if iban is None else protect_iban(iban, TEST_IBAN_KEY)
        with workspace_transaction(self.world.engine, self.world.ws) as conn:
            return conn.execute(
                insert(transaction_table)
                .values(
                    workspace_id=self.world.ws,
                    account_id=self.account_id,
                    import_id=self.import_id,
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


def test_one_consent_is_not_enough(world, private, app_engine, tmp_path):
    world.cmd(workspace_settings.update_settings, ai_private_accounts=True, actor=OWNER_A)
    assert world.query(workspace_settings.private_ai_allowed) is False
    tx = private.line(date(2026, 10, 2), "-23.80", name="Bäckerei Beispiel")
    plan = _run(_deps(app_engine, tmp_path, FakeModelClient()), world, tx)
    assert plan.reason == "routing.ai_disabled"


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


def test_switching_off_and_on_asks_both_again(world, private):
    _consent_both(world)
    world.cmd(workspace_settings.update_settings, ai_private_accounts=False, actor=OWNER_A)
    world.cmd(workspace_settings.update_settings, ai_private_accounts=True, actor=OWNER_B)
    assert world.query(workspace_settings.private_ai_allowed) is False


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
