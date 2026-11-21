"""The way back to the own money put into each property, for Übersicht's property tile (Luis,
21 Nov 2026: „Both“). Read only, inside the caller's workspace transaction; nothing stored.

Per property it composes what exists, re-deriving nothing:

- the own money put in: services.property_equity (rules.property_equity.paid_in);
- the actual years: phase 4's actual_years over the same facts, owners, income inputs and
  tax contexts as GET /properties/{id}/performance/actuals;
- the projected years: phase 4's project() with the levers GET /properties/{id}/performance/
  projection uses without a scenario id (the default scenario, else the defaults), so the
  break-even year reads the same years as Planung;
- today's value and loan balance: the net worth's own lines for the property on today
  (services.net_worth._from_handover: estimate_on_day's estimate, each loan by its schedule
  with the default scenario's follow-up rate), so the value equals NetWorth.property_estimate
  and the balance NetWorth.loan_balances for one property on the same day;
- the sale today: rules.performance.projection.sale_today, phase 4's sale() on those figures.

A property is left out of the totals, with a reason, when it has no paid_in (no price, sold),
is not handed over yet (phase 4 has no actual year and the net worth no estimate for it), has
a loan whose schedule cannot be computed, or when the projection or the sale raises a rule
error (no rule set or tariff for a year, a default scenario whose sale year is not after
today): one stale scenario must not blank the tile for every property.
"""

from datetime import date

from sqlalchemy import Connection

from hausbuch.rules.errors import RuleError
from hausbuch.rules.performance import payback as rules
from hausbuch.rules.performance.projection import project, sale_today
from hausbuch.schemas.net_worth import NetWorthLineKind as L
from hausbuch.schemas.performance import PropertiesPayback

from .income_inputs import income_facts, owner_profiles
from .loans import list_loans
from .net_worth import _from_handover as net_worth_lines
from .performance import _facts, _levers
from .properties import list_properties
from .property_equity import properties_paid_in
from .return_inputs import tax_contexts


def properties_payback(conn: Connection, *, today: date) -> PropertiesPayback:
    """Every property of the workspace (by label) with its payback, and the totals."""
    paid = properties_paid_in(conn, today=today)
    props = {p.id: p for p in list_properties(conn)}
    owners = owner_profiles(conn)
    inputs = income_facts(conn)
    contexts = tax_contexts(conn)
    out = []
    for row in paid.properties:
        prop = props[row.property_id]
        if row.paid_in is None:
            out.append(rules.left_out(row, row.reason or "no_purchase_price"))
            continue
        if prop.purchase_date > today:
            out.append(rules.left_out(row, "before_handover"))
            continue
        lines = net_worth_lines(conn, prop, today, [])
        if any(line.kind is L.LOAN and line.value_eur is None for line in lines):
            out.append(rules.left_out(row, "loan_not_valued"))
            continue
        value = sum((line.value_eur for line in lines if line.kind is L.PROPERTY), rules.ZERO)
        balance = -sum((line.value_eur for line in lines if line.kind is L.LOAN), rules.ZERO)
        try:
            facts = _facts(conn, prop.id, today)
            levers, scenario_id = _levers(conn, facts, today, None, {})
            loans = {loan.id: loan.terms for loan in list_loans(conn, prop.id)}
            projection = project(
                facts,
                levers,
                owners,
                inputs,
                today,
                loans,
                scenario_id=scenario_id,
                contexts=contexts,
            )
            sold, actual = sale_today(
                facts,
                owners,
                inputs,
                today,
                loans,
                value=value,
                loan_balance=balance,
                selling_costs_rate=levers.selling_costs_rate,
                contexts=contexts,
            )
        except RuleError as exc:  # RuleViolationError is one too
            out.append(rules.left_out(row, "not_computable", exc.code))
            continue
        out.append(
            rules.payback(
                row,
                actual,
                projection.years,
                levers.sale_year,
                sold,
                levers.selling_costs_rate,
            )
        )
    return rules.total(out)
