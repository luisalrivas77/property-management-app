"""The year-end holdings statement (docs/phases/phase-7.md D190, §8); built in step 6.

`statement(session, on)` computes, on read, per wallet and exchange account the holdings at
the end of `on` in Europe/Berlin: the addresses' units per asset, the open lots with
`tax_free_from` (rules/lots), the value where a price exists, the spam tokens in an appendix
and an address whose last sync did not reconcile marked so. The export writes it as an
immutable `holdings_export` row (PDF and CSV). Fixture
tests/fixtures/phase7/holdings_statement.yaml.
"""

from datetime import date

from sqlalchemy.orm import Session

from hausbuch.schemas.chain import HoldingsStatement


def statement(session: Session, on: date) -> HoldingsStatement:
    raise NotImplementedError("phase 7 step 6")
