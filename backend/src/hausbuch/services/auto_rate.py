"""GET /accounts/auto-rate?month=YYYY-MM: phase 6's done criterion measured from the stored
rows (docs/phases/phase-6.md D172; phase 6 step 6).

Per private bank account (current and savings; the card's and the depot's lines are booked by
their own matchers and are not in the criterion), the confirmed allocations of the lines
booked in the month, lines excluded as duplicates left out. „Auto-categorised“ is D172's
wording: the method is rule, matcher_transfer or document_match, or llm with no person's
confirmation (confirmed_by null: the routing auto-confirmed it). A line of the account's
first import (the earliest by creation, services.matching.in_first_import's definition, the
history D26 never auto-confirms) counts in the totals but not in the criterion.
"""

from collections import Counter
from decimal import ROUND_HALF_UP, Decimal
from uuid import UUID

from sqlalchemy import Connection, func, select

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import allocation as allocation_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.db.tables import transaction as transaction_table
from hausbuch.schemas.allocation import AllocationMethod
from hausbuch.schemas.enums import BANK_ACCOUNT_KINDS, AccountPurpose
from hausbuch.schemas.net_worth import AutoRate, AutoRateAccount

from .household import month_bounds

M = AllocationMethod
AUTO_METHODS = frozenset({M.RULE.value, M.MATCHER_TRANSFER.value, M.DOCUMENT_MATCH.value})
PLACES = Decimal("0.0001")


def _ratio(auto: int, confirmed: int) -> Decimal | None:
    if not confirmed:
        return None
    return (Decimal(auto) / Decimal(confirmed)).quantize(PLACES, rounding=ROUND_HALF_UP)


def _auto(method: str, confirmed_by: UUID | None) -> bool:
    return method in AUTO_METHODS or (method == M.LLM.value and confirmed_by is None)


def auto_rate(conn: Connection, month: str) -> AutoRate:
    start, end = month_bounds(month)
    acc, al, t, imp = account_table.c, allocation_table.c, transaction_table.c, import_table.c
    accounts = conn.execute(
        select(acc.id, acc.label)
        .where(
            acc.purpose == AccountPurpose.PRIVATE.value,
            acc.kind.in_([k.value for k in BANK_ACCOUNT_KINDS]),
        )
        .order_by(acc.label, acc.id)
    ).all()
    ids = [a.id for a in accounts]
    first = (
        select(imp.account_id, func.min(imp.created_at).label("created_at"))
        .where(imp.account_id.in_(ids))
        .group_by(imp.account_id)
        .subquery()
    )
    first_ids = {
        r.account_id: r.id
        for r in conn.execute(
            select(imp.account_id, imp.id)
            .join(
                first,
                (first.c.account_id == imp.account_id) & (first.c.created_at == imp.created_at),
            )
            .order_by(imp.account_id, imp.id.desc())
        )
    }  # the earliest by creation, ties by id as in_first_import orders them (last wins here)
    rows = conn.execute(
        select(t.account_id, t.import_id, al.method, al.confirmed_by)
        .select_from(allocation_table)
        .join(transaction_table, t.id == al.transaction_id)
        .where(
            t.account_id.in_(ids),
            t.booking_date >= start,
            t.booking_date <= end,
            t.excluded_as_duplicate_of.is_(None),
            al.status == "confirmed",
        )
    ).all()
    per: dict[UUID, list] = {a.id: [] for a in accounts}
    for r in rows:
        per[r.account_id].append(r)
    results: list[AutoRateAccount] = []
    for a in accounts:
        mine = per[a.id]
        auto = [r for r in mine if _auto(r.method, r.confirmed_by)]
        history = [r for r in mine if r.import_id == first_ids.get(a.id)]
        results.append(
            AutoRateAccount(
                account_id=a.id,
                label=a.label,
                confirmed=len(mine),
                auto=len(auto),
                by_method=dict(sorted(Counter(r.method for r in mine).items())),
                by_routing=dict(
                    sorted(
                        Counter(
                            "auto_confirm" if r.confirmed_by is None else "person" for r in mine
                        ).items()
                    )
                ),
                ratio=_ratio(len(auto), len(mine)),
                first_import=len(history),
                first_import_auto=sum(1 for r in history if _auto(r.method, r.confirmed_by)),
            )
        )
    confirmed = sum(r.confirmed for r in results)
    auto_total = sum(r.auto for r in results)
    criterion_confirmed = confirmed - sum(r.first_import for r in results)
    criterion_auto = auto_total - sum(r.first_import_auto for r in results)
    criterion = _ratio(criterion_auto, criterion_confirmed)
    result = AutoRate(
        month=month,
        accounts=results,
        confirmed=confirmed,
        auto=auto_total,
        ratio=_ratio(auto_total, confirmed),
        criterion_confirmed=criterion_confirmed,
        criterion_auto=criterion_auto,
        criterion_ratio=criterion,
    )
    if criterion is not None:
        result = result.model_copy(update={"met": criterion >= result.target})
    return result


__all__ = ["AUTO_METHODS", "auto_rate"]
