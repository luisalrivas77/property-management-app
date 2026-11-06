"""Every rule fixture conforms to the spec schemas. Runs before any rule exists."""

from decimal import Decimal

import fixture_models as phase1
import fixture_models_phase2 as phase2
import pytest
from fixture_models import RentFixture

from hausbuch.schemas.allocation import (
    MatchingRule,
    MatchingRuleCreate,
    RoutingDecision,
    RuleMatcher,
)
from hausbuch.schemas.events import EVENT_CATALOG, EventType

PHASES = {"phase1": phase1, "phase2": phase2}
FIXTURE_FILES = [(p, name) for p, mod in PHASES.items() for name in sorted(mod.MODELS)]


@pytest.mark.parametrize("phase", sorted(PHASES))
def test_every_fixture_file_has_a_model(phase):
    mod = PHASES[phase]
    assert {p.stem for p in mod.FIXTURES.glob("*.yaml")} == set(mod.MODELS)


@pytest.mark.parametrize(("phase", "name"), FIXTURE_FILES)
def test_fixture_matches_schemas(phase, name):
    PHASES[phase].load(name)


@pytest.mark.parametrize(("phase", "name"), FIXTURE_FILES)
def test_case_ids_unique(phase, name):
    data = PHASES[phase].raw(name)
    for key, value in data.items():
        if isinstance(value, list) and value and isinstance(value[0], dict) and "id" in value[0]:
            ids = [c["id"] for c in value if "seq" not in c]
            assert len(ids) == len(set(ids)), f"duplicate case id in {name}.{key}"


def test_rent_rows_validate():
    fx = phase1.load("rent_at")
    assert isinstance(fx, RentFixture)
    fx.entries(fx.rows)
    fx.entries(fx.tie_break.rows)


def test_event_catalog_is_complete():
    assert set(EVENT_CATALOG) == set(EventType)


def test_routing_table_agrees_with_thresholds():
    """review_routing.yaml's per-category decisions at confidence 0.9 follow thresholds.yaml."""
    table = phase2.load("thresholds").table()
    for row in phase2.load("review_routing").per_category:
        threshold = table[row.category]
        auto = threshold is not None and Decimal("0.9") >= threshold
        assert (row.decision is RoutingDecision.AUTO_CONFIRM) == auto, row.category


def test_rule_stores_no_amount():
    """D25: no field of a learned rule, its matcher or its read shape can hold money."""
    for model in (RuleMatcher, MatchingRuleCreate, MatchingRule):
        for name, field in model.model_fields.items():
            assert "amount" not in name or name == "amount_sign", (model.__name__, name)
            assert field.annotation not in (Decimal, Decimal | None), (model.__name__, name)
