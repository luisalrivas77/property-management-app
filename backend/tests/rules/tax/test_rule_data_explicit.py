"""Q-O29 (c): every year's rule-data file states every field of its model, and of its
fixed-shape nested blocks (the tariff's children, childcare, §35a and social-insurance figures,
the form header), so a figure that changed in law cannot fall back to a schema default
unnoticed (AnlageNRuleSet and the tariff, whose phase 4b additions default for older files)."""

from importlib.resources import files
from typing import Any, get_args, get_origin

import pytest
import yaml
from pydantic import BaseModel

from hausbuch.schemas.employment import AnlageNRuleSet
from hausbuch.schemas.performance import Tariff

TAX = files("hausbuch.rules.tax")
# Set by the registry when a year reuses an earlier file, never written in a file.
NOT_IN_FILES = {(Tariff, "reused_from")}


def _year_files(folder: str) -> list[Any]:
    found = sorted(p for p in TAX.joinpath(folder).iterdir() if p.name.endswith(".yaml"))
    assert found, folder
    return found


def _missing(model: type[BaseModel], data: Any, path: str = "") -> list[str]:
    """The fields of `model` (and of its nested models) the raw `data` leaves out."""
    if not isinstance(data, dict):
        return []
    out: list[str] = []
    for name, field in model.model_fields.items():
        if (model, name) in NOT_IN_FILES:
            continue
        if name not in data:
            out.append(f"{path}{name}")
            continue
        out += _nested(field.annotation, data[name], f"{path}{name}.")
    return out


def _is_model(a: Any) -> bool:
    return isinstance(a, type) and issubclass(a, BaseModel)


def _nested(annotation: Any, value: Any, path: str) -> list[str]:
    if _is_model(annotation):
        return _missing(annotation, value, path)
    # Lists and maps (tariff zones, useful lives, form lines) are tables whose rows state only
    # what applies to them (a zone without a quadratic term, a line without a Kennzahl); the
    # guard is for the year's scalar figures and the fixed-shape blocks.
    if get_origin(annotation) in (list, dict):
        return []
    args = get_args(annotation)
    if args and value is not None:  # X | None
        inner = [a for a in args if a is not type(None)]
        if len(inner) == 1:
            return _nested(inner[0], value, path)
    return []


@pytest.mark.rule
@pytest.mark.parametrize(
    ("folder", "model"),
    [("anlage_n", AnlageNRuleSet), ("tariffs", Tariff)],
)
def test_each_year_file_sets_every_field(folder, model):
    for path in _year_files(folder):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        assert _missing(model, data) == [], f"{folder}/{path.name}"


@pytest.mark.rule
def test_the_guard_sees_a_left_out_figure():
    """A key left out at the top or inside a fixed-shape block is reported."""
    data = yaml.safe_load(_year_files("tariffs")[0].read_text(encoding="utf-8"))
    del data["donation_cap_share"]
    first = next(iter(data["childcare"]))
    del data["childcare"][first]
    assert sorted(_missing(Tariff, data)) == sorted([f"childcare.{first}", "donation_cap_share"])
