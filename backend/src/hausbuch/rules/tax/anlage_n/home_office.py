"""§5.3, D100 (2), D106: the Tagespauschale (§4 Abs. 5 S. 1 Nr. 6c EStG).

`6 × (Z58 days + Z59 days)`, at most 1,260. A Z58 day (another workplace available) cannot be a
commute day: `anlage_n.days_implausible` when commute days + Z58 days exceed the year's
weekdays. Not for the months of a work room (Nr. 6c S. 3): a 12-month room refuses the day
rate (`anlage_n.work_room_excludes_day_rate`); a room of 1–11 months with days typed keeps them
with the info warning `anlage_n.work_room_partial` (the day rate only outside those months).
"""

from dataclasses import dataclass, field
from decimal import Decimal

from hausbuch.rules.errors import ANLAGE_N_WORK_ROOM_EXCLUDES_DAY_RATE, RuleError
from hausbuch.rules.tax._common import warning
from hausbuch.schemas.employment import AnlageNRuleSet, HomeOfficeFact, WorkRoomFact
from hausbuch.schemas.tax import DraftWarning, WarningCode


@dataclass
class HomeOfficeResult:
    full: Decimal  # days × day rate, before the cap
    amount: Decimal
    warnings: list[DraftWarning] = field(default_factory=list)


def home_office_line(
    fact: HomeOfficeFact, commute_days: int, room: WorkRoomFact | None, rs: AnlageNRuleSet
) -> tuple[Decimal, list[DraftWarning]]:
    """(the deductible day rate, warnings). RuleError when a 12-month room excludes it."""
    result = home_office_result(fact, commute_days, room, rs)
    return result.amount, result.warnings


def home_office_result(
    fact: HomeOfficeFact, commute_days: int, room: WorkRoomFact | None, rs: AnlageNRuleSet
) -> HomeOfficeResult:
    """home_office_line with the uncapped amount kept (the draft's components)."""
    if room is not None and room.months == 12:
        raise RuleError(
            ANLAGE_N_WORK_ROOM_EXCLUDES_DAY_RATE,
            "a work room for the whole year excludes the Tagespauschale (§4 Abs. 5 Nr. 6c S. 3)",
        )
    days = fact.days_other_workplace + fact.days_no_other_workplace
    full = rs.home_office_day_rate * days
    warnings = []
    office_and_commute = commute_days + fact.days_other_workplace
    if office_and_commute > rs.weekdays_in_year:
        warnings.append(
            warning(WarningCode.ANLAGE_N_DAYS_IMPLAUSIBLE, days=str(office_and_commute))
        )
    if full > rs.home_office_cap:
        warnings.append(warning(WarningCode.ANLAGE_N_HOME_OFFICE_CAPPED, days=str(days)))
    if room is not None and days > 0:
        warnings.append(warning(WarningCode.ANLAGE_N_WORK_ROOM_PARTIAL, months=str(room.months)))
    return HomeOfficeResult(full=full, amount=min(full, rs.home_office_cap), warnings=warnings)
