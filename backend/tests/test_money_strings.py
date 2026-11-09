"""Spec amendment 2: money, rates, shares, areas and rooms are decimal strings on the wire.

A float (JSON 1400.0) or an int (JSON 1400) is refused with a fixed message, a string or a
Decimal is accepted, and every such type publishes `type: string` in its JSON schema, so the
generated TypeScript types say `string` too.
"""

import importlib
import inspect
import json
import pkgutil
from decimal import Decimal

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

import hausbuch.schemas
from hausbuch.schemas.common import (
    Area,
    BuildingShare,
    Money,
    NonNegativeMoney,
    PositiveMoney,
    Rate,
    Rooms,
    TaxRate,
)
from hausbuch.schemas.property import OwnerCreate, PropertyCreate

# alias, a valid decimal string for it, its decimal places
ALIASES = {
    "Money": (Money, "-1400.50", 2),
    "NonNegativeMoney": (NonNegativeMoney, "0.00", 2),
    "PositiveMoney": (PositiveMoney, "289000.00", 2),
    "Rate": (Rate, "0.036000", 6),
    "TaxRate": (TaxRate, "0.4431", 4),
    "BuildingShare": (BuildingShare, "0.720000", 6),
    "Area": (Area, "68.40", 2),
    "Rooms": (Rooms, "2.5", 1),
}
NUMBERS = [1400.0, 0.5, 1400, 0, True]
MESSAGE = "must be a decimal string, not a number"


@pytest.fixture(params=sorted(ALIASES), name="alias")
def _alias(request):
    annotation, valid, places = ALIASES[request.param]
    return TypeAdapter(annotation), valid, places


def test_accepts_a_decimal_string_and_a_decimal(alias):
    adapter, valid, _ = alias
    assert adapter.validate_python(valid) == Decimal(valid)
    assert adapter.validate_python(Decimal(valid)) == Decimal(valid)
    assert adapter.validate_json(json.dumps(valid)) == Decimal(valid)


@pytest.mark.parametrize("number", NUMBERS, ids=repr)
def test_rejects_a_number_in_python_and_json_mode(alias, number):
    adapter, _, _ = alias
    for validate in (adapter.validate_python, lambda v: adapter.validate_json(json.dumps(v))):
        with pytest.raises(ValidationError) as excinfo:
            validate(number)
        assert [e["msg"] for e in excinfo.value.errors()] == [f"Value error, {MESSAGE}"]


def test_publishes_a_string_schema_in_both_modes(alias):
    adapter, valid, places = alias
    for mode in ("validation", "serialization"):
        schema = adapter.json_schema(mode=mode)
        assert schema["type"] == "string", mode
        assert schema["pattern"].endswith(rf"(\.[0-9]{{1,{places}}})?$")
    assert adapter.dump_json(Decimal(valid)) == json.dumps(valid).encode()


def test_the_bounds_still_apply_to_strings():
    with pytest.raises(ValidationError):
        TypeAdapter(PositiveMoney).validate_python("0.00")
    with pytest.raises(ValidationError):
        TypeAdapter(Rate).validate_python("1")
    with pytest.raises(ValidationError):
        TypeAdapter(Money).validate_python("1.234")


def test_a_float_inside_a_model_is_refused_without_echoing_it():
    address = {"street": "a", "house_number": "1", "postal_code": "10115", "city": "Berlin"}
    body = {
        "label": "Wohnung",
        "kind": "etw",
        "address": address,
        "build_year": 1994,
        "notarized_on": "2024-03-15",
        "purchase_date": "2024-05-01",
        "purchase_price": 289123.45,
    }
    with pytest.raises(ValidationError) as excinfo:
        PropertyCreate.model_validate_json(json.dumps(body))
    assert [e["loc"] for e in excinfo.value.errors()] == [("purchase_price",)]
    with pytest.raises(ValidationError) as excinfo:
        OwnerCreate.model_validate({"full_name": "Luis", "tax_id_ref": "IdNr. 12345678901"})
    assert "12345678901" not in str(excinfo.value)  # hide_input_in_errors still holds


def _schema_models() -> list[type[BaseModel]]:
    models = []
    for info in pkgutil.iter_modules(hausbuch.schemas.__path__):
        module = importlib.import_module(f"hausbuch.schemas.{info.name}")
        models += [
            obj
            for _, obj in inspect.getmembers(module, inspect.isclass)
            if issubclass(obj, BaseModel) and obj.__module__ == module.__name__
        ]
    return models


def _number_paths(node, path="") -> list[str]:
    if isinstance(node, dict):
        found = [path] if node.get("type") == "number" else []
        return found + [p for k, v in node.items() for p in _number_paths(v, f"{path}/{k}")]
    if isinstance(node, list):
        return [p for i, v in enumerate(node) for p in _number_paths(v, f"{path}/{i}")]
    return []


@pytest.mark.parametrize("model", _schema_models(), ids=lambda m: f"{m.__module__}.{m.__name__}")
def test_no_schema_publishes_a_json_number(model):
    """Every decimal field in the contract goes through the amended aliases: a bare Decimal
    (anyOf number/string) or a float would show up here as `type: number`."""
    for mode in ("validation", "serialization"):
        assert _number_paths(model.model_json_schema(mode=mode)) == [], mode
