"""Address-poisoning dust, read from `dust.yaml` (docs/phases/phase-7.md D262).

A stranger sends a tiny amount from an address that looks like one the household uses,
hoping it is later copied from the history. Such a receipt is proposed as a Kleinstbetrag and
booked by „Bestätigen“ for that movement only, never by flagging the asset (that would hide
every unit of a chain's own coin, D187). Pure: the caller (services.wallet_groups) decides
that the leg is a stranger's incoming transfer and finds the price of its day.
"""

from decimal import Decimal, localcontext
from functools import cache
from importlib.resources import files

import yaml
from pydantic import Field

from hausbuch.schemas.common import Schema

# numeric(38,18) units times a price: exact, never rounded before the comparison.
_PRECISION = 60


class DustRule(Schema):
    below_eur: Decimal = Field(gt=0)


def parse(text: str) -> DustRule:
    return DustRule.model_validate(yaml.safe_load(text))


@cache
def dust_rule() -> DustRule:
    """The validated rule. Cached: the file is immutable at runtime."""
    return parse((files("hausbuch.rules.chain") / "dust.yaml").read_text(encoding="utf-8"))


def is_dust(units: Decimal, price: Decimal | None, rule: DustRule | None = None) -> bool:
    """A receipt of `units` worth less than the limit at `price` (the EUR price of one unit
    on its Berlin day). No price → False: never dust by a guessed value."""
    if price is None or units <= 0 or price < 0:
        return False
    limit = (rule or dust_rule()).below_eur
    with localcontext() as exact:
        exact.prec = _PRECISION
        return units * price < limit
