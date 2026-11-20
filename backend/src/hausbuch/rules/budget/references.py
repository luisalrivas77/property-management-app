"""The published reference points (docs/phases/phase-10.md D275, D276, §3). Data:
references.yaml beside this module; fixture: tests/fixtures/phase10/budget_references.yaml.

`references()` loads them; `fill(reference, targets)` is the goals sheet's one tap: it writes a
split's three numbers into the form and leaves the housing target as it was. It stores nothing
(the household saves with „Ziele speichern“), and a housing reference fills nothing.
"""

from functools import cache
from importlib.resources import files

import yaml

from hausbuch.schemas.budget import BudgetReference, BudgetReferenceKind, BudgetTargets


@cache
def references() -> tuple[int, tuple[BudgetReference, ...]]:
    """references.yaml: its version and the references in file order, each key once."""
    data = yaml.safe_load((files("hausbuch.rules.budget") / "references.yaml").read_text("utf-8"))
    rows = tuple(BudgetReference.model_validate(row) for row in data["references"])
    keys = [row.key for row in rows]
    if len(keys) != len(set(keys)):
        raise ValueError("references.yaml: a key twice")
    return data["version"], rows


def reference(key: str) -> BudgetReference:
    for row in references()[1]:
        if row.key == key:
            return row
    raise KeyError(key)


def fill(ref: BudgetReference, targets: BudgetTargets) -> BudgetTargets:
    """The form after a tap on a split: its needs, wants and saving; housing untouched (D275:
    no reference fills the housing target)."""
    if ref.kind is not BudgetReferenceKind.SPLIT:
        raise ValueError(f"{ref.key}: only a split fills the form")
    return targets.model_copy(
        update={"needs_max": ref.needs, "wants_max": ref.wants, "saving_min": ref.saving}
    )
