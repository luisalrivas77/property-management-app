"""Transfers between own accounts and the Haushalt month (docs/phases/phase-6.md §12, D162).

- GET /transfers?month=: the current links with a leg booked in the month.
- GET /transfers/open: every leg that waits for a person (transfer.ambiguous, a fit not yet
  written, or transfer.unmatched_leg once its three banking days have passed).
- POST /transfers: a link by hand (matched_by user). 404 for an unknown leg, 409 when a leg
  is linked already, 422 transfer.legs_invalid for two legs that are no transfer. With
  rebook, a leg booked otherwise and tied to the other by an own IBAN is first re-booked as a
  transfer by the person's answer (D176 (4)); 422 transfer.legs_invalid for one not tied.
- POST /transfers/{id}/unlink: replace a wrong link by the one the user picks. 404, 409 when
  it was replaced already or a leg of the replacement is linked.
- POST /transfers/{id}/release: „Verknüpfung lösen“ (phase 7 D261, review fixes 16 Nov 2026):
  ends a coin link the app wrote from a learned exchange address or withdrawal pair, or a
  person's „An eigene Wallet“ (its written deposit ended), the legs back to proposed and the
  learned row forgotten. 404; 409 for a link proven by its hash, any
  other link, or one no longer current.
- POST /transfers/own-wallet-deposit: „An eigene Wallet“ (phase 7 D181 (c) amended 16 Nov
  2026): an exchange's withdrawal to BNB Smart Chain booked to an own address there, the
  deposit written by the app. 404, 409, 422 transfer.legs_invalid.
- POST /returns: „Als Rücküberweisung verbuchen“ (D162 amended (7)): a credit on a private
  account that returns an earlier debit of it, both booked as transfer parts by the person's
  answer and linked to each other. 404 for an unknown line, 409 when either is linked to
  another, 422 transfer.return_invalid for two lines that are no returned payment.
- GET /household/cashflow?month=: the Haushalt screen, confirmed allocations only.
- POST /transactions/{id}/private-costs: a property bill paid from a private account (D161
  path 2): the card's private_cost rows recorded; the bill's current rows unchanged when it
  has them already. 404 for a line that is no private property-cost payment with a bill.
- POST /transactions/{id}/private-costs/year: the bill's private_cost rows moved to the year
  the confirmed private line paid them (D161 (1), D143's rule). 404 as above.
- GET /household/recurring (phase 10 §7.9 (b), D296): the recurring payments as of today
  (Berlin), the shown ones and those marked „kein Abo“.
- POST /household/one-off-marks {allocation_id, answer}: „Als einmalig markieren“
  (`one_off`) or „Doch normal zählen“ (`regular`) on a payment's detail panel (phase 10
  §7.12 (b), D307; the candidate search of D297 and GET /household/one-off-candidates are
  gone): a confirmed private payment counted as spending, booked up to today; 201 with the
  stored answer; 404 for any other allocation. The newest answer counts.
- POST /household/recurring-marks {key, interval, answer}: „kein Abo“ (`hidden`) or „Wieder
  anzeigen“ (`shown`) on a series; 201 with the stored answer.
"""

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import Query, Response

from hausbuch.schemas.household import (
    HouseholdCashflow,
    OpenTransfer,
    OwnWalletDepositCreate,
    ReturnedPaymentCreate,
    TransferLink,
    TransferLinkRequest,
    TransferLinkView,
    TransferUnlinkBody,
)
from hausbuch.schemas.recurring import (
    OneOffMarkRequest,
    RecurringPayments,
    SeriesMarkRequest,
    SpendingMarkRecorded,
)
from hausbuch.schemas.tax import TaxInput
from hausbuch.services import (
    household,
    property_costs,
    recurring,
    returns,
    spending_marks,
    transfers,
)
from hausbuch.services.documents import LOCAL_TIME_ZONE

from ..deps import Cmd, SettingsDep, domain_router

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
def transfer_link(body: TransferLinkRequest, cmd: Cmd, settings: SettingsDep) -> TransferLink:
    return cmd.run(transfers.link_by_hand, body, iban_key=settings.iban_hmac_key)


@router.post("/transfers/{link_id}/release", status_code=204)
def transfer_release(link_id: UUID, cmd: Cmd) -> Response:
    """„Verknüpfung lösen“ (phase 7 D261): ends a coin link the app wrote from a learned
    exchange address or withdrawal pair, reopens both legs and forgets what produced it. 404,
    409 for a hash link, a person's link or any other link, or one no longer current."""
    cmd.run(transfers.release, link_id)
    return Response(status_code=204)


@router.post("/transfers/own-wallet-deposit", status_code=201)
def transfer_own_wallet_deposit(body: OwnWalletDepositCreate, cmd: Cmd) -> TransferLink:
    """„An eigene Wallet“ (phase 7 D181 (c) amended 16 Nov 2026): an exchange's withdrawal to a
    balance-only chain (BNB Smart Chain) booked to an own address there: the app writes the
    deposit (the withdrawal less its fee), links it (matched_by user) and confirms both legs;
    `learned` when the remember box taught the pair. 404, 409 when linked already or being
    answered, 422 transfer.legs_invalid for a withdrawal or address it does not apply to."""
    return cmd.run(transfers.own_wallet_deposit, body)


@router.post("/transfers/{link_id}/unlink", status_code=201)
def transfer_unlink(link_id: UUID, body: TransferUnlinkBody, cmd: Cmd) -> TransferLink:
    """The replacing link (the old row names it in superseded_by)."""
    return cmd.run(transfers.unlink, link_id, body.replacement)


@router.post("/returns", status_code=201)
def returned_payment(body: ReturnedPaymentCreate, cmd: Cmd, settings: SettingsDep) -> TransferLink:
    """The link joining the debit and the credit that returned it (matched_by user)."""
    return cmd.run(returns.book_return, body, iban_key=settings.iban_hmac_key)


@router.get("/household/cashflow")
def household_cashflow(month: Month, cmd: Cmd) -> HouseholdCashflow:
    return cmd.read(household.cashflow, month)


@router.get("/household/recurring")
def household_recurring(cmd: Cmd) -> RecurringPayments:
    return cmd.read(recurring.recurring_payments, datetime.now(LOCAL_TIME_ZONE).date())


@router.post("/household/one-off-marks", status_code=201)
def household_one_off_mark(body: OneOffMarkRequest, cmd: Cmd) -> SpendingMarkRecorded:
    return cmd.run(spending_marks.record_one_off, body, datetime.now(LOCAL_TIME_ZONE).date())


@router.post("/household/recurring-marks", status_code=201)
def household_recurring_mark(body: SeriesMarkRequest, cmd: Cmd) -> SpendingMarkRecorded:
    return cmd.run(spending_marks.record_series, body)


@router.post("/transactions/{transaction_id}/private-costs")
def private_costs_record(transaction_id: UUID, cmd: Cmd) -> list[TaxInput]:
    """The bill's current private_cost rows after the card's „Übernehmen“."""
    return cmd.run(property_costs.record_proposed, transaction_id)


@router.post("/transactions/{transaction_id}/private-costs/year")
def private_costs_year(transaction_id: UUID, cmd: Cmd) -> list[TaxInput]:
    """The bill's current private_cost rows, in the year the line paid them."""
    return cmd.run(property_costs.count_in_year_paid, transaction_id)
