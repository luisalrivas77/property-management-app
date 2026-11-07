"""`hausbuch evals report`: pre-fill accuracy from the proposal.applied events (D41, §13).

The eval record is the `proposal.applied` event: per applied record and field it holds what
the user did with the proposed value (accepted, edited, cleared, skipped), never the value,
so it survives the D19 scrub. This module counts those decisions per extraction kind, record
type and field, and the accepted share over the money and date fields against the 90 %
target (§13: "≥ 90 % accepted on amounts and dates").

- proposed = every decision recorded for the field (skipped included); the accepted share
  is accepted / proposed, §13's "share of fields with decision accepted over fields
  proposed". The summary line also gives the share without the skipped ones: a skipped
  record (the user did not want it created) says nothing about the reading.
- Money and date fields are decided by the type of the field in the record model the apply
  step validates it with (PropertyCreate, LoanCreateRequest, ...), never by its name: money
  is one of the schemas.common money types (numeric(14,2)), a date is a `date`.
- Reading: the owner role (like bootstrap), which RLS limits to one workspace per
  transaction, so each workspace is read in a READ ONLY transaction of its own; the
  workspaces are hausbuch.auth.lookup.provisioned_workspace_ids, or the one named.
"""

import types
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal, Union, get_args, get_origin
from uuid import UUID

from pydantic import BaseModel
from pydantic.fields import FieldInfo
from sqlalchemy import Connection, Engine, select, text

from hausbuch.auth.lookup import provisioned_workspace_ids
from hausbuch.db.tables import event as event_table
from hausbuch.schemas.api import (
    AcquisitionCostPatch,
    DepositReceivedBody,
    DisbursementCorrection,
    LoanCreateRequest,
    LoanPatch,
    OwnershipBody,
    PropertyPatch,
    TenancyPatch,
    TenantPatch,
    UnitPatch,
)
from hausbuch.schemas.common import Money, NonNegativeMoney, PositiveMoney
from hausbuch.schemas.document import DocumentTyping
from hausbuch.schemas.events import EventType, ProposalApplied
from hausbuch.schemas.property import (
    AcquisitionCostCreate,
    PriceSplitCreate,
    PropertyCreate,
    UnitCreate,
)
from hausbuch.schemas.proposal import FieldDecision
from hausbuch.schemas.tenancy import InitialRent, TenancyCreate, TenantCreate

TARGET = Decimal("0.90")
UNKNOWN_KIND = "unknown"  # an applied proposal whose kind was never set

ValueClass = Literal["money", "date"]

# The models services.proposals validates each record type's values with (§6.5).
RECORD_MODELS: dict[str, tuple[type[BaseModel], ...]] = {
    "property": (PropertyCreate, PropertyPatch),
    "unit": (UnitCreate, UnitPatch),
    "ownership": (OwnershipBody,),
    "price_split": (PriceSplitCreate,),
    "acquisition_cost": (AcquisitionCostCreate, AcquisitionCostPatch),
    "loan": (LoanCreateRequest, LoanPatch, DisbursementCorrection),
    "tenant": (TenantCreate, TenantPatch),
    "tenancy": (TenancyCreate, TenancyPatch),
    "rent_schedule": (InitialRent,),
    "deposit_received": (DepositReceivedBody,),
    "typing": (DocumentTyping,),
}

_MONEY = [FieldInfo.from_annotation(t).metadata for t in (Money, NonNegativeMoney, PositiveMoney)]


def _class_of(annotation, metadata: list) -> ValueClass | None:
    if annotation is Decimal:
        return "money" if metadata in _MONEY else None
    if isinstance(annotation, type) and issubclass(annotation, date):
        return "date"
    origin = get_origin(annotation)
    if origin is Annotated:
        info = FieldInfo.from_annotation(annotation)
        return _class_of(info.annotation, info.metadata)
    if origin in (Union, types.UnionType):
        options = [a for a in get_args(annotation) if a is not type(None)]
        if len(options) == 1:
            return _class_of(options[0], [])
    return None


def _model_of(annotation) -> type[BaseModel] | None:
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    if get_origin(annotation) in (Union, types.UnionType, Annotated):
        found = [_model_of(a) for a in get_args(annotation)]
        models = [m for m in found if m is not None]
        return models[0] if len(models) == 1 else None
    return None


def value_class(entity_type: str, name: str) -> ValueClass | None:
    """ "money", "date" or None for a proposal record's field (dotted for a nested model,
    "address.city"), from the field's type in the record type's models."""
    for model in RECORD_MODELS.get(entity_type, ()):
        current: type[BaseModel] | None = model
        *path, last = name.split(".")
        for part in path:
            info = current.model_fields.get(part) if current else None
            current = None if info is None else _model_of(info.annotation)
        if current is None or last not in current.model_fields:
            continue
        info = current.model_fields[last]
        return _class_of(info.annotation, info.metadata)
    return None


# --- Counting --------------------------------------------------------------------------


@dataclass
class Tally:
    accepted: int = 0
    edited: int = 0
    cleared: int = 0
    skipped: int = 0

    def add(self, decision: FieldDecision) -> None:
        setattr(self, decision.value, getattr(self, decision.value) + 1)

    def merge(self, other: "Tally") -> None:
        for name in ("accepted", "edited", "cleared", "skipped"):
            setattr(self, name, getattr(self, name) + getattr(other, name))

    @property
    def proposed(self) -> int:
        return self.accepted + self.edited + self.cleared + self.skipped

    def share(self, *, without_skipped: bool = False) -> Decimal | None:
        total = self.proposed - (self.skipped if without_skipped else 0)
        return None if total == 0 else Decimal(self.accepted) / Decimal(total)


Row = tuple[str, str, str]  # (kind, record type, field)


@dataclass
class Report:
    proposals: int = 0
    rows: dict[Row, Tally] = field(default_factory=dict)
    money_and_dates: Tally = field(default_factory=Tally)


def aggregate(applied: Iterable[ProposalApplied]) -> Report:
    report = Report()
    for payload in applied:
        report.proposals += 1
        kind = payload.kind.value if payload.kind is not None else UNKNOWN_KIND
        for record in payload.records:
            entity = str(record.entity_type)
            for name, decision in record.decisions.items():
                report.rows.setdefault((kind, entity, name), Tally()).add(decision)
                if value_class(entity, name) is not None:
                    report.money_and_dates.add(decision)
    return report


def _percent(share: Decimal | None) -> str:
    return "-" if share is None else f"{share * 100:.1f} %"


def format_report(report: Report, *, workspaces: int, since: date | None) -> str:
    scope = f"{workspaces} workspace{'s' if workspaces != 1 else ''}"
    period = f", since {since.isoformat()}" if since else ""
    lines = [f"Pre-fill accuracy: {report.proposals} applied proposals ({scope}{period})", ""]
    if not report.rows:
        lines.append("No applied proposals yet.")
        return "\n".join(lines) + "\n"
    header = ("kind", "record", "field", "proposed", "accepted", "edited", "cleared", "skipped")
    table = [(*header, "accepted %")]
    for (kind, entity, name), tally in sorted(report.rows.items()):
        counts = (tally.proposed, tally.accepted, tally.edited, tally.cleared, tally.skipped)
        table.append((kind, entity, name, *map(str, counts), _percent(tally.share())))
    widths = [max(len(row[i]) for row in table) for i in range(len(header) + 1)]
    for row in table:
        cells = [
            cell.ljust(width) if i < 3 else cell.rjust(width)
            for i, (cell, width) in enumerate(zip(row, widths, strict=True))
        ]
        lines.append("  ".join(cells).rstrip())
    lines.append("")
    md = report.money_and_dates
    share = md.share()
    if share is None:
        lines.append("Money and date fields: none proposed yet.")
    else:
        verdict = "met" if share >= TARGET else "NOT met"
        lines.append(
            f"Money and date fields: {md.accepted} of {md.proposed} accepted = "
            f"{_percent(share)} (target {TARGET * 100:.0f} %: {verdict}); "
            f"without the {md.skipped} skipped: {_percent(md.share(without_skipped=True))}"
        )
    return "\n".join(lines) + "\n"


# --- Reading the events ----------------------------------------------------------------


@contextmanager
def read_only_workspace(engine: Engine, workspace_id: UUID) -> Iterator[Connection]:
    """A READ ONLY transaction scoped to one workspace (RLS), rolled back at the end."""
    with engine.connect() as conn, conn.begin() as transaction:
        conn.execute(text("SET TRANSACTION READ ONLY"))  # the first statement
        conn.execute(
            text("SELECT set_config('app.workspace_id', :ws, true)"), {"ws": str(workspace_id)}
        )
        yield conn
        transaction.rollback()


def applied_events(conn: Connection, since: datetime | None) -> list[ProposalApplied]:
    query = select(event_table.c.payload).where(
        event_table.c.type == EventType.PROPOSAL_APPLIED.value
    )
    if since is not None:
        query = query.where(event_table.c.recorded_at >= since)
    rows = conn.execute(query.order_by(event_table.c.seq)).scalars()
    return [ProposalApplied.model_validate(payload) for payload in rows]


def read_report(
    engine: Engine, *, workspace_id: UUID | None, since: datetime | None
) -> tuple[Report, int]:
    """The report over one workspace or every provisioned one, and how many were read."""
    workspaces = [workspace_id] if workspace_id else sorted(provisioned_workspace_ids(engine))
    applied: list[ProposalApplied] = []
    for ws in workspaces:
        with read_only_workspace(engine, ws) as conn:
            applied.extend(applied_events(conn, since))
    return aggregate(applied), len(workspaces)
