"""Every rule fixture conforms to the spec schemas. Runs before any rule exists."""

import pytest
from fixture_models import FIXTURES, MODELS, RentFixture, load, raw

from hausbuch.schemas.events import EVENT_CATALOG, EventType


def test_every_fixture_file_has_a_model():
    assert {p.stem for p in FIXTURES.glob("*.yaml")} == set(MODELS)


@pytest.mark.parametrize("name", sorted(MODELS))
def test_fixture_matches_schemas(name):
    load(name)


@pytest.mark.parametrize("name", sorted(MODELS))
def test_case_ids_unique(name):
    data = raw(name)
    for key, value in data.items():
        if isinstance(value, list) and value and isinstance(value[0], dict) and "id" in value[0]:
            ids = [c["id"] for c in value if "seq" not in c]
            assert len(ids) == len(set(ids)), f"duplicate case id in {name}.{key}"


def test_rent_rows_validate():
    fx = load("rent_at")
    assert isinstance(fx, RentFixture)
    fx.entries(fx.rows)
    fx.entries(fx.tie_break.rows)


def test_event_catalog_is_complete():
    assert set(EVENT_CATALOG) == set(EventType)
