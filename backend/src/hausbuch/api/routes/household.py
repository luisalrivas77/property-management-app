"""Transfers between own accounts and the Haushalt month (docs/phases/phase-6.md §12, D162).

- GET /transfers?month=: the current links with a leg booked in the month.
- GET /transfers/open: every leg that waits for a person (transfer.ambiguous, a fit not yet
  written, or transfer.unmatched_leg once its three banking days have passed).
- POST /transfers: a link by hand (matched_by user). 404 for an unknown leg, 409 when a leg
  is linked already, 422 transfer.legs_invalid for two legs that are no transfer.
- POST /transfers/{id}/unlink: replace a wrong link by the one the user picks. 404, 409 when
  it was replaced already or a leg of the replacement is linked.
- GET /household/cashflow?month=: the Haushalt screen, confirmed allocations only.
- POST /transactions/{id}/private-costs: a property bill paid from a private account (D161
  path 2): the card's private_cost rows recorded; the bill's current rows unchanged when it
  has them already. 404 for a line that is no private property-cost payment with a bill.
- POST /transactions/{id}/private-costs/year: the bill's private_cost rows moved to the year
  the confirmed private line paid them (D161 (1), D143's rule). 404 as above.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import Query

from hausbuch.schemas.household import (
    HouseholdCashflow,
    OpenTransfer,
    TransferLink,
    TransferLinkCreate,
    TransferLinkView,
    TransferUnlinkBody,
)
from hausbuch.schemas.tax import TaxInput
from hausbuch.services import household, property_costs, transfers
from hausbuch.services.documents import LOCAL_TIME_ZONE

from ..deps import Cmd, domain_router

router = domain_router()

Month = Annotated[str, Query(pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$")]


@router.get("/transfers")
def transfers_list(month: Month, cmd: Cmd) -> list[TransferLinkView]:
    start, end = household.month_bounds(month)
    return cmd.read(transfers.list_links, start=start, end=end)


@router.get("/transfers/open")
def transfers_open(cmd: Cmd) -> list[OpenTransfer]:
    return cmd.read(transfers.open_transfers, datetime.now(LOCAL_TIME_ZONE).date())


@router.post("/transfers", status_code=201)
def transfer_link(body: TransferLinkCreate, cmd: Cmd) -> TransferLink:
    return cmd.run(transfers.link_by_hand, body)


@router.post("/transfers/{link_id}/unlink", status_code=201)
def transfer_unlink(link_id: UUID, body: TransferUnlinkBody, cmd: Cmd) -> TransferLink:
    """The replacing link (the old row names it in superseded_by)."""
    return cmd.run(transfers.unlink, link_id, body.replacement)


@router.get("/household/cashflow")
def household_cashflow(month: Month, cmd: Cmd) -> HouseholdCashflow:
    return cmd.read(household.cashflow, month)


@router.post("/transactions/{transaction_id}/private-costs")
def private_costs_record(transaction_id: UUID, cmd: Cmd) -> list[TaxInput]:
    """The bill's current private_cost rows after the card's „Übernehmen“."""
    return cmd.run(property_costs.record_proposed, transaction_id)


@router.post("/transactions/{transaction_id}/private-costs/year")
def private_costs_year(transaction_id: UUID, cmd: Cmd) -> list[TaxInput]:
    """The bill's current private_cost rows, in the year the line paid them."""
    return cmd.run(property_costs.count_in_year_paid, transaction_id)
