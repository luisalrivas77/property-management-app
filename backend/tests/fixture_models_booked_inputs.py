"""Shape of tests/fixtures/phase10/booked_inputs.yaml (phase 10 §7.8 (a), D293), registered in
fixture_models_phase10.MODELS. Labels (A, B, l1, r1, c1, ...) become fixed UUIDs through
`ident`; the rows are built as ReturnInputFact, so a fixture row that the write would refuse by
its shape fails on load."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import Field, model_validator

from hausbuch.schemas.allocation import AllocationCategory
from hausbuch.schemas.booked_inputs import BookedGroup, BookedLine, BookedNeed
from hausbuch.schemas.common import Money, Schema
from hausbuch.schemas.employment import OwnerReturnFacts, ReturnInputFact
from hausbuch.schemas.enums import HOUSEHOLD_KINDS, ReturnInputKind


def ident(label: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"hausbuch:fixture:phase10:booked_inputs:{label}")


class LineIn(Schema):
    id: str
    category: AllocationCategory
    booked_on: date
    amount: Money
    owners: list[str] = Field(default_factory=list)
    payee: str | None = None
    purpose: str | None = None

    def line(self) -> BookedLine:
        return BookedLine(
            allocation_id=ident(self.id),
            booking_date=self.booked_on,
            amount=self.amount,
            text=" ".join(t for t in (self.payee, self.purpose) if t),
            owner_ids=[ident(o) for o in self.owners],
            category=self.category,
            payee=self.payee,
            purpose=self.purpose,
        )


class RowIn(Schema):
    id: str
    owner: str | None = None
    kind: ReturnInputKind
    tax_year: int
    value: dict[str, Any]
    supersedes: str | None = None
    withdrawn: bool = False

    def row(self) -> ReturnInputFact:
        value = dict(self.value)
        if "child_id" in value and value["child_id"] is not None:
            value["child_id"] = str(ident(value["child_id"]))
        return ReturnInputFact(
            id=ident(self.id),
            owner_id=None if self.owner is None else ident(self.owner),
            kind=self.kind,
            tax_year=self.tax_year,
            value=value,
            supersedes_id=None if self.supersedes is None else ident(self.supersedes),
            withdrawn=self.withdrawn,
        )


class ProposalExpect(Schema):
    group: BookedGroup
    owner: str | None = None
    payee: str | None = None
    booked: Money
    typed: Money
    difference: Money
    allocations: list[str]
    needs: list[BookedNeed] = Field(default_factory=list)
    benefit: str | None = None
    receipt_needed: bool = False
    ask_party: bool = False
    blocking: bool = False
    child: str | None = None
    choices: list[str] = Field(default_factory=list)
    value: dict[str, Any]


class BookedCase(Schema):
    name: str
    tax_year: int
    owners: list[str]
    lines: list[LineIn]
    rows: list[RowIn] = Field(default_factory=list)
    expect: list[ProposalExpect]
    gap: dict[BookedGroup, Decimal] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _shape(self) -> BookedCase:
        labels = [x.id for x in self.lines] + [r.id for r in self.rows]
        if len(labels) != len(set(labels)):
            raise ValueError(f"{self.name}: a label is used twice")
        for r in self.rows:
            r.row()  # the write's own shape check
            if (r.owner is None) != (r.kind in HOUSEHOLD_KINDS):
                raise ValueError(f"{self.name}: {r.id} owner set exactly for owner kinds")
            if r.owner is not None and r.owner not in self.owners:
                raise ValueError(f"{self.name}: {r.id} names an owner the case does not list")
        for group, gap in self.gap.items():
            got = sum((e.difference for e in self.expect if e.group == group), Decimal(0))
            if got != gap:
                raise ValueError(f"{self.name}: {group} differences {got} != gap {gap}")
        return self

    def owner_facts(self) -> list[OwnerReturnFacts]:
        rows = [r.row() for r in self.rows if r.owner is not None]
        return [
            OwnerReturnFacts(owner_id=ident(o), inputs=[r for r in rows if r.owner_id == ident(o)])
            for o in self.owners
        ]

    def household_rows(self) -> list[ReturnInputFact]:
        return [r.row() for r in self.rows if r.owner is None]

    def booked_lines(self) -> list[BookedLine]:
        return [x.line() for x in self.lines]


class BookedInputsFixture(Schema):
    cases: list[BookedCase]
