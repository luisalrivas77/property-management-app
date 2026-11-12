"""private_cost_link.yaml's step 1 cases (phase 6 D159): the allocation command refuses a
category outside the account purpose's set, whoever books it, with
allocation.category_not_for_account. D161's three paths are step 2's and stay pending.

The fixture's property id stands for the world's property."""

from decimal import Decimal

import fixture_models_phase6 as phase6
import pytest
from db.matching_support import World
from db.test_services_private_matching import Private

from hausbuch.schemas.allocation import AllocationDraft, AllocationMethod, AllocationSet
from hausbuch.schemas.enums import AccountPurpose
from hausbuch.services import allocations
from hausbuch.services.errors import RuleViolationError

pytestmark = pytest.mark.db

LINK = phase6.load("private_cost_link")
BUILT = [c for c in LINK.cases if c.pending and c.pending.startswith("phase 6 step 1")]
_METHOD = {"user": AllocationMethod.USER, "model": AllocationMethod.LLM}


@pytest.fixture
def world(app_engine, fresh_workspaces) -> World:
    return World(app_engine, fresh_workspaces[0])


@pytest.mark.parametrize("case", BUILT, ids=lambda c: c.id)
def test_a_category_outside_the_purposes_set_is_refused(case, world):
    line, booking = case.line, case.booking
    if case.purpose is AccountPurpose.PRIVATE:
        tx = Private(world).line(
            line.booking_date, str(line.amount), name=line.counterparty_name, purpose=line.purpose
        )
    else:
        tx = world.line(
            line.booking_date, str(line.amount), name=line.counterparty_name, purpose=line.purpose
        )
    part = {"amount": Decimal(line.amount), "category": booking.category}
    if booking.property_id is not None:
        part["property_id"] = world.property_id
    answer = AllocationSet(
        transaction_id=tx,
        allocations=[AllocationDraft(**part)],
        method=_METHOD[booking.by],
        confidence=Decimal("1.000"),
    )
    with pytest.raises(RuleViolationError) as refused:
        world.cmd(allocations.record_set, answer, confirm=booking.by == "user", auto=False)
    assert refused.value.code == case.expect.error.code


def test_the_step_1_cases_are_the_refusals():
    assert BUILT and all(c.expect.error is not None for c in BUILT)
