"""The rule set of a tax year, loaded from `rulesets/<year>.yaml` (phase 3 §4.1).

A year without a file raises RuleError `tax.no_rule_set`. A file that does not validate as a
RuleSet (a category without a line or an explicit null, a line key the draft writes missing,
AfA rates with a gap) fails to load with the Pydantic error, whose message carries
`form_mapping.incomplete` for the mapping checks. The files ship inside the package, so the
API image reads them with importlib.resources.

Phase 4 (docs/phases/phase-4.md D81, §4): the income tax tariff of a year, from
`tariffs/<year>.yaml`. Unlike the rule set, a year without its own file reuses the latest
earlier one, marked `reused_from`, so a projected year always has a tariff; only a year before
every file raises `tariff.no_file`. `rule_set_or_latest` does the same fallback for the rule set
and is for the projection only (the Steuern screen keeps raising `tax.no_rule_set`).

Phase 4b (docs/phases/phase-4b.md §4, D99, D110): the Anlage N rule set of a year, from
`anlage_n/<year>.yaml`, loaded like the rule set (a year without a file raises
`tax.no_rule_set`); and the yearly questions, from `questionnaire/<year>.yaml`, with the
tariff's latest-year fallback marked `reused_from` (the screen then shows
`questionnaire_provisional`).
"""

from functools import cache
from importlib.resources import files

import yaml

from hausbuch.rules.errors import PROJECTION_NO_RULE_SET, TARIFF_NO_FILE, TAX_NO_RULE_SET, RuleError
from hausbuch.schemas.employment import AnlageNRuleSet, Questionnaire
from hausbuch.schemas.performance import Tariff
from hausbuch.schemas.tax import RuleSet

_RULESETS = files("hausbuch.rules.tax") / "rulesets"
_TARIFFS = files("hausbuch.rules.tax") / "tariffs"
_ANLAGE_N = files("hausbuch.rules.tax") / "anlage_n"
_QUESTIONNAIRES = files("hausbuch.rules.tax") / "questionnaire"


def _years(directory) -> tuple[int, ...]:
    years = []
    for entry in directory.iterdir():
        stem, dot, ext = entry.name.partition(".")
        if dot and ext == "yaml" and stem.isdigit():
            years.append(int(stem))
    return tuple(sorted(years))


def available_years() -> tuple[int, ...]:
    """The tax years that have a rule set file, ascending."""
    return _years(_RULESETS)


def tariff_years() -> tuple[int, ...]:
    """The tax years that have a tariff file, ascending."""
    return _years(_TARIFFS)


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


@cache
def rule_set_or_latest(tax_year: int) -> RuleSet:
    """The rule set of `tax_year`, or the latest earlier one unchanged (its own tax_year) for a
    projected year without a file (D86). Before every file: RuleError projection.no_rule_set."""
    earlier = [y for y in available_years() if y <= tax_year]
    if not earlier:
        raise RuleError(PROJECTION_NO_RULE_SET, f"no rule set for {tax_year} or any earlier year")
    return rule_set(earlier[-1])


def afa_rule_set(tax_year: int) -> RuleSet:
    """The rule set whose AfA rates judge a useful life from `tax_year` (phase 3 D79; the
    service's check and the pre-fill of a Gutachten, phase 2 D147): that year's, else the
    latest earlier one, else the earliest there is (the rates by build year are the same in
    every rule set so far)."""
    years = available_years()
    earlier = [y for y in years if y <= tax_year]
    return rule_set(max(earlier) if earlier else min(years))


@cache
def tariff(tax_year: int) -> Tariff:
    """The validated tariff of `tax_year`; for a year without a file, the latest earlier file
    with tax_year set to the year asked for and reused_from to the file's year (D81). Before
    every file: RuleError tariff.no_file. Cached: the files are immutable at runtime."""
    earlier = [y for y in tariff_years() if y <= tax_year]
    if not earlier:
        raise RuleError(TARIFF_NO_FILE, f"no tariff for {tax_year} or any earlier year")
    own = _tariff_file(earlier[-1])
    if own.tax_year == tax_year:
        return own
    reused = {"tax_year": tax_year, "reused_from": own.tax_year}
    return Tariff.model_validate(own.model_dump() | reused)


def _tariff_file(year: int) -> Tariff:
    data = yaml.safe_load((_TARIFFS / f"{year}.yaml").read_text(encoding="utf-8"))
    loaded = Tariff.model_validate(data)
    if loaded.tax_year != year or loaded.reused_from is not None:
        raise ValueError(f"tariffs/{year}.yaml declares tax_year {loaded.tax_year}")
    return loaded


def anlage_n_years() -> tuple[int, ...]:
    """The tax years that have an Anlage N rule set file, ascending."""
    return _years(_ANLAGE_N)


def questionnaire_years() -> tuple[int, ...]:
    """The tax years that have a questionnaire file, ascending."""
    return _years(_QUESTIONNAIRES)


@cache
def anlage_n(tax_year: int) -> AnlageNRuleSet:
    """The validated Anlage N rule set for `tax_year` (D99). A year without a file raises
    RuleError tax.no_rule_set, as the Anlage V rule set does. Cached."""
    resource = _ANLAGE_N / f"{tax_year}.yaml"
    if not resource.is_file():
        raise RuleError(TAX_NO_RULE_SET, f"no Anlage N rule set for tax year {tax_year}")
    loaded = AnlageNRuleSet.model_validate(yaml.safe_load(resource.read_text(encoding="utf-8")))
    if loaded.tax_year != tax_year:
        raise ValueError(f"anlage_n/{tax_year}.yaml declares tax_year {loaded.tax_year}")
    return loaded


def anlage_n_or_latest(tax_year: int) -> AnlageNRuleSet:
    """The Anlage N rule set for `tax_year`, else the latest earlier one (a projected or
    not-yet-published year; phase 4b §5.8 `childcare_2027`). Before every file: RuleError
    tax.no_rule_set (the API's 422)."""
    earlier = [y for y in anlage_n_years() if y <= tax_year]
    if not earlier:
        raise RuleError(TAX_NO_RULE_SET, f"no Anlage N rule set for {tax_year} or any earlier year")
    return anlage_n(earlier[-1])


@cache
def questionnaire(tax_year: int) -> Questionnaire:
    """The year's questions (D110); for a year without a file, the latest earlier file with
    tax_year set to the year asked for and reused_from to the file's year. Before every file:
    RuleError tax.no_rule_set. Cached."""
    earlier = [y for y in questionnaire_years() if y <= tax_year]
    if not earlier:
        raise RuleError(TAX_NO_RULE_SET, f"no questionnaire for {tax_year} or any earlier year")
    year = earlier[-1]
    data = yaml.safe_load((_QUESTIONNAIRES / f"{year}.yaml").read_text(encoding="utf-8"))
    own = Questionnaire.model_validate(data)
    if own.tax_year != year or own.reused_from is not None:
        raise ValueError(f"questionnaire/{year}.yaml declares tax_year {own.tax_year}")
    if year == tax_year:
        return own
    return Questionnaire.model_validate(
        own.model_dump() | {"tax_year": tax_year, "reused_from": year}
    )
