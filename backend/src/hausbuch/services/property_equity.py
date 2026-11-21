"""The own money put into each property, for Übersicht's property tile (Luis, 21 Nov 2026).

Read only, inside the caller's workspace transaction. Per property it loads the facts the
Anlage V draft reads (services.tax.draft_facts: the acquisition cost rows and every tax_input
row with its supersedes chain) and the property's loans (services.loans.list_loans), and hands
them to the pure rule rules.property_equity.paid_in. The net worth and its stored month-ends
are not touched.
"""

from datetime import date
from uuid import UUID

from sqlalchemy import Connection

from hausbuch.rules import property_equity
from hausbuch.schemas.property import PropertiesPaidIn
from hausbuch.schemas.tax import DraftFacts

from .loans import list_loans
from .properties import list_properties
from .tax import draft_facts


def properties_paid_in(conn: Connection, *, today: date) -> PropertiesPaidIn:
    """Every property of the workspace (by label) with its parts and paid_in, and the total
    over those that have one."""
    return paid_in_with_facts(conn, today=today)[0]


def paid_in_with_facts(
    conn: Connection, *, today: date
) -> tuple[PropertiesPaidIn, dict[UUID, DraftFacts]]:
    """properties_paid_in and the draft facts it read per property, the same as
    services.performance._facts(conn, id, today) reads them, so a caller in the same
    transaction need not read them again (phase 10 §7.3 (3): GET /properties/payback)."""
    out = []
    read: dict[UUID, DraftFacts] = {}
    for prop in list_properties(conn):
        facts = read[prop.id] = draft_facts(conn, prop.id, tax_year=today.year, today=today)
        out.append(
            property_equity.paid_in(
                property_id=prop.id,
                label=prop.label,
                purchase_price=prop.purchase_price,
                cost_rows=facts.cost_rows,
                tax_inputs=facts.tax_inputs,
                allocations=facts.allocations,
                loan_principals=[loan.terms.principal for loan in list_loans(conn, prop.id)],
                ownership_ended_on=prop.ownership_ended_on,
                today=today,
            )
        )
    return property_equity.total(out), read
