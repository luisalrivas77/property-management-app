"""The rule set of a tax year, loaded from `rulesets/<year>.yaml` (phase 3 §4.1).

A year without a file raises RuleError `tax.no_rule_set`. A file that does not validate as a
RuleSet (a category without a line or an explicit null, a line key the draft writes missing,
AfA rates with a gap) fails to load with the Pydantic error, whose message carries
`form_mapping.incomplete` for the mapping checks. The files ship inside the package, so the
API image reads them with importlib.resources.
"""

from functools import cache
from importlib.resources import files

import yaml

from hausbuch.rules.errors import TAX_NO_RULE_SET, RuleError
from hausbuch.schemas.tax import RuleSet

_RULESETS = files("hausbuch.rules.tax") / "rulesets"


def available_years() -> tuple[int, ...]:
    """The tax years that have a rule set file, ascending."""
    years = []
    for entry in _RULESETS.iterdir():
        stem, dot, ext = entry.name.partition(".")
        if dot and ext == "yaml" and stem.isdigit():
            years.append(int(stem))
    return tuple(sorted(years))


@cache
def rule_set(tax_year: int) -> RuleSet:
    """The validated rule set for `tax_year`. Cached: the files are immutable at runtime."""
    resource = _RULESETS / f"{tax_year}.yaml"
    if not resource.is_file():
        raise RuleError(TAX_NO_RULE_SET, f"no rule set for tax year {tax_year}")
    data = yaml.safe_load(resource.read_text(encoding="utf-8"))
    loaded = RuleSet.model_validate(data)
    if loaded.tax_year != tax_year:
        raise ValueError(f"rulesets/{tax_year}.yaml declares tax_year {loaded.tax_year}")
    return loaded
