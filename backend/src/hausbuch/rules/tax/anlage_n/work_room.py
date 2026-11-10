"""§5.4, D106: the häusliches Arbeitszimmer (§4 Abs. 5 S. 1 Nr. 6b EStG via §9 Abs. 5).

`actual = cents((rent + running costs) × room m² × months ÷ (home m² × 12)) + room-only costs`;
`flat = cents(1,260 × months ÷ 12)` (S. 4: a twelfth less per month without the conditions).
The user's `prefer` decides; else the higher, the flat rate on a tie. Always the info warning
`anlage_n.work_room_centre` (the room must be the centre of the whole working activity); with
actual costs also `anlage_n.work_room_records` (§4 Abs. 7).
"""

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Literal

from hausbuch.rules.tax._common import warning
from hausbuch.schemas.employment import AnlageNRuleSet, WorkRoomFact
from hausbuch.schemas.tax import DraftWarning, WarningCode

from ._base import cents


@dataclass
class WorkRoomResult:
    actual: Decimal
    flat: Decimal
    method: Literal["actual", "flat"]
    amount: Decimal
    warnings: list[DraftWarning] = field(default_factory=list)


def work_room_line(fact: WorkRoomFact, rs: AnlageNRuleSet) -> WorkRoomResult:
    shared = (fact.rent_year + fact.running_costs_year) * fact.room_m2 * fact.months
    actual = cents(shared / (fact.home_m2 * 12)) + fact.room_only_costs_year
    flat = cents(rs.work_room_flat * fact.months / 12)
    if fact.prefer == "actual" or (fact.prefer is None and actual > flat):
        method, amount = "actual", actual
    else:
        method, amount = "flat", flat
    warnings = [warning(WarningCode.ANLAGE_N_WORK_ROOM_CENTRE)]
    if method == "actual":
        warnings.append(warning(WarningCode.ANLAGE_N_WORK_ROOM_RECORDS))
    return WorkRoomResult(actual=actual, flat=flat, method=method, amount=amount, warnings=warnings)
