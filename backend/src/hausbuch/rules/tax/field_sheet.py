"""The field sheet's rows from a draft (phase 3 §12, D60): what to type into ELSTER, line by
line. Pure; the export service lays them out and writes the PDF and the CSV.

The Z13 rows come first: the income.rent_units block prints its first row on zeile_from (13,
"Bezeichnung der Wohneinheit") and rows 2..n on zeile (14, "weiterer Wohneinheiten"), each
rounded down. Then every draft line with an amount, in form order; the header lines are
printed separately and a ticked box has no amount.
"""

from hausbuch.schemas.common import Money, Schema
from hausbuch.schemas.tax import AnlageVDraft, RuleSet

from .rounding import whole_euros

UNIT_ROWS = "income.rent_units"


class FieldSheetRow(Schema):
    key: str
    zeile: int | None
    kennzahl: str | None
    amount: Money
    amount_rounded: int


def field_sheet(draft: AnlageVDraft, rule_set: RuleSet) -> list[FieldSheetRow]:
    block = rule_set.form.lines[UNIT_ROWS]
    rows = [
        FieldSheetRow(
            key=UNIT_ROWS,
            zeile=block.zeile_from if i == 0 and block.zeile_from is not None else block.zeile,
            kennzahl=block.kennzahl,
            amount=row.amount,
            amount_rounded=whole_euros(row.amount, block.rounding),
        )
        for i, row in enumerate(draft.unit_rows)
    ]
    rows += [
        FieldSheetRow(
            key=line.key,
            zeile=line.zeile,
            kennzahl=line.kennzahl,
            amount=line.amount,
            amount_rounded=line.amount_rounded,
        )
        for line in draft.lines
        if line.amount is not None
        and line.amount_rounded is not None
        and (
            line.amount != 0
            or line.key in ("income.total", "wk.total")
            or line.key.startswith("result.")
        )
    ]
    return rows
