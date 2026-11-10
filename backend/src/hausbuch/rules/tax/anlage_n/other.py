"""§5.6: dues (Z53), training (Z60) and the other Werbungskosten (Z61–64).

`dues` = Σ professional_association amounts, `union` = Σ of those marked `is_union` (beside the
Pauschbetrag from 2026, §9a S. 3). `training` = Σ training amounts, each without a receipt
raising `anlage_n.receipt_missing {kind: training}`. `other` = Σ other_expense amounts, an
account fee without an amount counting the flat 16.00.
"""

from dataclasses import dataclass, field
from decimal import Decimal

from hausbuch.rules.tax._common import warning
from hausbuch.schemas.employment import AnlageNRuleSet, ReturnInputFact
from hausbuch.schemas.enums import ReturnInputKind as K
from hausbuch.schemas.tax import DraftWarning, WarningCode

from ._base import ZERO, row_sources


@dataclass
class Dues:
    total: Decimal
    union: Decimal
    rows: list[ReturnInputFact] = field(default_factory=list)


def dues_lines(rows: list[ReturnInputFact]) -> Dues:
    mine = [r for r in rows if r.kind is K.PROFESSIONAL_ASSOCIATION]
    return Dues(
        total=sum((r.value.amount for r in mine), ZERO),
        union=sum((r.value.amount for r in mine if r.value.is_union), ZERO),
        rows=mine,
    )


def training_lines(rows: list[ReturnInputFact]) -> tuple[Decimal, list[DraftWarning]]:
    mine = [r for r in rows if r.kind is K.TRAINING]
    warnings = [
        warning(WarningCode.ANLAGE_N_RECEIPT_MISSING, row_sources([r]), kind="training")
        for r in mine
        if r.document_id is None
    ]
    return sum((r.value.amount for r in mine), ZERO), warnings


def other_expense_amount(rows: list[ReturnInputFact], rs: AnlageNRuleSet) -> Decimal:
    return sum(
        (
            r.value.amount if r.value.amount is not None else rs.account_fee_flat
            for r in rows
            if r.kind is K.OTHER_EXPENSE
        ),
        ZERO,
    )
