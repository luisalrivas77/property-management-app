"""§5.1, D105: the projected certificate from one employer's payslips of the year.

`figure = ytd + month × months_remaining_paid` from the latest payslip, for every figure the
certificate needs; a payslip whose Jahreswerte were not read projects `month × (month number +
remaining)`, with warning `anlage_n.payslip_no_ytd`. `period_from` is January unless the
earliest payslip's ytd equals its month (pay started that month); `period_to` is the last paid
month's end. The projection carries no employer pension share (Nr. 22a): a payslip does not
print it, and below the cap the pension deduction is the employee share either way.
`tax_free_benefits` is informational (a Zuschuss is on the certificate's Nr. 15 once it
arrives). A month whose ytd ≠ the previous ytd + its month raises `anlage_n.payslip_gap`.
"""

import calendar
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from hausbuch.rules.tax._common import warning
from hausbuch.schemas.employment import (
    Payslip,
    ReturnInputFact,
    WageCertificate,
    employer_key,
)
from hausbuch.schemas.enums import ReturnInputKind
from hausbuch.schemas.tax import DraftWarning, WarningCode

from ._base import row_sources

# The payslip figures a projected certificate carries (the certificate field of the same name).
_PROJECTED = (
    "gross",
    "income_tax",
    "soli",
    "church_tax",
    "pension_employee",
    "health_employee",
    "care_employee",
    "unemployment_employee",
)


def projected_certificate(
    payslips: list[Payslip], tax_year: int
) -> tuple[WageCertificate, list[DraftWarning]]:
    """The projected certificate of one owner and employer (D105)."""
    if not payslips:
        raise ValueError("a projection needs at least one payslip")
    slips = sorted(payslips, key=lambda p: p.period_month)
    first, last = slips[0], slips[-1]
    m = last.period_month.month
    warnings: list[DraftWarning] = []
    if last.gross.ytd is None:
        warnings.append(warning(WarningCode.ANLAGE_N_PAYSLIP_NO_YTD, month=f"{tax_year}-{m:02d}"))
    for before, after in zip(slips, slips[1:], strict=False):
        if before.gross.ytd is None or after.gross.ytd is None:
            continue
        if after.gross.ytd != before.gross.ytd + after.gross.month:
            month = after.period_month.month
            warnings.append(
                warning(WarningCode.ANLAGE_N_PAYSLIP_GAP, month=f"{tax_year}-{month:02d}")
            )
    figures: dict[str, Decimal] = {}
    for name in _PROJECTED:
        pair = getattr(last, name)
        if pair.ytd is None:
            figures[name] = pair.month * (m + last.months_remaining_paid)
        else:
            figures[name] = pair.ytd + pair.month * last.months_remaining_paid
    starts_here = first.gross.ytd is not None and first.gross.ytd == first.gross.month
    end_month = m + last.months_remaining_paid
    cert = WageCertificate(
        period_from=first.period_month if starts_here else date(tax_year, 1, 1),
        period_to=date(tax_year, end_month, calendar.monthrange(tax_year, end_month)[1]),
        employer=last.employer,
        tax_class=last.tax_class,
        tax_class_factor=last.tax_class_factor,
        **figures,
    )
    return cert, warnings


@dataclass
class PaySources:
    """The year's certificates (`real`), the projections of the employers without one
    (`projected`, sorted by employer), the warnings, and the rows each set came from."""

    real: list[WageCertificate] = field(default_factory=list)
    projected: list[WageCertificate] = field(default_factory=list)
    warnings: list[DraftWarning] = field(default_factory=list)
    real_rows: list[ReturnInputFact] = field(default_factory=list)
    projected_rows: list[ReturnInputFact] = field(default_factory=list)

    @property
    def certificates(self) -> list[WageCertificate]:
        return self.real + self.projected

    @property
    def gross_source(self) -> str | None:
        if self.projected:
            return "payslip"
        return "certificate" if self.real else None

    def sources(self):
        return row_sources(self.real_rows + self.projected_rows)


def pay_certificates(
    certificates: list[WageCertificate], payslips: list[Payslip], tax_year: int
) -> PaySources:
    """A payslip is projected only for an employer without a certificate of the year (a real
    certificate replaces the projection, D98)."""
    return _pay([(None, c) for c in certificates], [(None, p) for p in payslips], tax_year)


def pay_of_rows(rows: list[ReturnInputFact], tax_year: int) -> PaySources:
    """pay_certificates over the year's current certificate and payslip rows."""
    year_rows = [r for r in rows if r.tax_year == tax_year]
    return _pay(
        [(r, r.value) for r in year_rows if r.kind is ReturnInputKind.WAGE_CERTIFICATE],
        [(r, r.value) for r in year_rows if r.kind is ReturnInputKind.PAYSLIP],
        tax_year,
    )


def _pay(certificates: list[tuple], payslips: list[tuple], tax_year: int) -> PaySources:
    out = PaySources(
        real=[c for _, c in certificates],
        real_rows=[r for r, _ in certificates if r is not None],
    )
    # Grouped and compared by employer_key; the projection keeps the latest slip's spelling.
    certified = {employer_key(c.employer) for _, c in certificates}
    by_employer: dict[str, list[tuple]] = defaultdict(list)
    for row, slip in payslips:
        key = employer_key(slip.employer)
        if key not in certified:
            by_employer[key].append((row, slip))
    for employer in sorted(by_employer):
        pairs = by_employer[employer]
        cert, warnings = projected_certificate([p for _, p in pairs], tax_year)
        out.projected.append(cert)
        out.warnings += warnings
        out.projected_rows += [r for r, _ in pairs if r is not None]
    return out
