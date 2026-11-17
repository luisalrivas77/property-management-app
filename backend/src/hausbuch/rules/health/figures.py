"""The key figures (docs/phases/phase-9.md §5.1–§5.4). Fixtures: savings_rate.yaml,
reserve.yaml, debt.yaml, net_worth_series.yaml under tests/fixtures/phase9/."""

from decimal import Decimal

from hausbuch.schemas.health import Debt, NetWorthPoint, Reserve, SavingsRate
from hausbuch.schemas.household import NetWorthClass


def savings_rate(*args: object, **kwargs: object) -> SavingsRate:
    """Σ saved ÷ Σ income over the window, with and without one-offs and principal (D235)."""
    raise NotImplementedError("phase 9 step 1")


def reserve(*args: object, **kwargs: object) -> Reserve:
    """The reserve in euros and in months of the need, against the target (D237, D238)."""
    raise NotImplementedError("phase 9 step 1")


def debt(*args: object, **kwargs: object) -> Debt:
    """The four debt figures, annualised from the window (D239)."""
    raise NotImplementedError("phase 9 step 1")


def split(*args: object, **kwargs: object) -> dict[NetWorthClass, Decimal]:
    """Each class's share of the gross assets, never marked (D240)."""
    raise NotImplementedError("phase 9 step 1")


def series(*args: object, **kwargs: object) -> list[NetWorthPoint]:
    """The net worth per month-end from the stored runs, the change split into saved and
    other (D241)."""
    raise NotImplementedError("phase 9 step 1")
