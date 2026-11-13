"""A depot document's plain-words explanation for its page and its suggestion card (phase 6
D177).

The kind is the one code recorded from the document's text layer (document.depot_kind); the
figures are the document's own, from its current reading, or the rows it was booked as. Read
only; nothing here reaches the model or an event.
"""

from collections.abc import Mapping
from datetime import date
from decimal import Decimal
from uuid import UUID

from sqlalchemy import Connection, select

from hausbuch.db.tables import account as account_table
from hausbuch.db.tables import asset_movement as movement_table
from hausbuch.db.tables import document as document_table
from hausbuch.db.tables import holding_statement as holding_table
from hausbuch.db.tables import statement_import as import_table
from hausbuch.rules import depot as depot_rules
from hausbuch.rules import depot_explain as rules
from hausbuch.rules.depot import DepotProposal
from hausbuch.schemas.ai import ExtractedValue
from hausbuch.schemas.document import DocumentExplanation
from hausbuch.schemas.enums import DepotDocumentKind as K
from hausbuch.schemas.enums import ExtractionKind

from .depot import depot_accounts

_TRADES = (K.WERTPAPIERABRECHNUNG, K.WERTPAPIERABRECHNUNG_KAUF, K.WERTPAPIERABRECHNUNG_VERKAUF)
# The reading whose figures fill a kind's sentences; the other kinds have only their date.
_READING = {
    K.WERTPAPIERABRECHNUNG_KAUF: ExtractionKind.TRADE_CONFIRMATION,
    K.WERTPAPIERABRECHNUNG_VERKAUF: ExtractionKind.TRADE_CONFIRMATION,
    K.WERTPAPIERABRECHNUNG: ExtractionKind.TRADE_CONFIRMATION,
    K.ERTRAEGNISABRECHNUNG: ExtractionKind.DISTRIBUTION_NOTE,
    K.PM_REPORTING: ExtractionKind.DEPOT_REPORT,
    K.EINBUCHUNG: ExtractionKind.SECURITIES_TRANSFER,
    K.AUSBUCHUNG: ExtractionKind.SECURITIES_TRANSFER,
}


def document_explanation(conn: Connection, document_id: UUID) -> DocumentExplanation | None:
    """The explanation of a depot document, None for any other document (no recorded kind,
    or a kind the catalogue has no entry for)."""
    d = document_table.c
    row = conn.execute(
        select(d.depot_kind, d.document_date).where(d.id == document_id)
    ).one_or_none()
    if row is None or row.depot_kind is None:
        return None
    kind = K(row.depot_kind)
    values = _reading(conn, document_id, _READING.get(kind))
    kind = rules.with_side(kind, _str(values.get("side")))
    facts = _facts(kind, values)
    if facts.get("datum") is None and row.document_date is not None:
        facts["datum"] = rules.day(row.document_date)
    facts["konto"] = _account(conn, document_id, kind)
    return rules.explain(kind, facts)


def _reading(
    conn: Connection, document_id: UUID, wanted: ExtractionKind | None
) -> dict[str, object]:
    """The current reading's values by field name, when it is the reader the kind expects."""
    if wanted is None:
        return {}
    from .documents import current_extraction  # it imports the job queue

    extraction = current_extraction(conn, document_id)
    if extraction is None or extraction.kind != wanted:
        return {}
    return {
        name: value.value
        for name, value in extraction.fields
        if isinstance(value, ExtractedValue) and value.value is not None
    }


def _str(value: object) -> str | None:
    return None if value is None else str(value)


def _money(values: Mapping[str, object], name: str) -> Decimal | None:
    value = values.get(name)
    try:
        return None if value is None else Decimal(str(value))
    except ArithmeticError:
        return None


def _day(values: Mapping[str, object], name: str) -> str | None:
    value = values.get(name)
    try:
        return None if value is None else rules.day(date.fromisoformat(str(value)))
    except ValueError:
        return None


def _euro(amount: Decimal | None) -> str | None:
    return None if amount is None else rules.money(amount)


def _units(amount: Decimal | None) -> str | None:
    return None if amount is None else rules.units(amount)


def _facts(kind: K, values: Mapping[str, object]) -> dict[str, str | None]:
    """The kind's figures, formatted; a figure the reading lacks stays None."""
    if kind in _TRADES:
        return {
            "fonds": _str(values.get("security_name")),
            "anteile": _units(_money(values, "units")),
            "datum": _day(values, "trade_date"),
            "betrag": _euro(_money(values, "net_amount")),
            "gebuehren": _euro(_money(values, "fees")),
            "steuern": _euro(_money(values, "taxes")),
        }
    if kind is K.ERTRAEGNISABRECHNUNG:
        gross, net = _money(values, "gross_amount_eur"), _money(values, "net_amount_eur")
        return {
            "fonds": _str(values.get("security_name")),
            "anteile": _units(_money(values, "units")),
            "datum": _day(values, "payment_date"),
            "brutto": _euro(gross),
            "netto": _euro(net),
            "steuern": None if gross is None or net is None else rules.money(gross - net),
        }
    if kind is K.PM_REPORTING:
        facts: dict[str, str | None] = {"stichtag": _day(values, "report_date")}
        report = _proposed(ExtractionKind.DEPOT_REPORT, values)
        if report is not None and report.holdings:
            facts["positionen"] = rules.count(len(report.holdings))
            facts["gesamtwert"] = rules.money(
                sum((h.declared_value_eur for h in report.holdings), Decimal(0))
            )
        if report is not None and report.trades:
            facts["umsaetze"] = rules.count(len(report.trades))
        return facts
    if kind in (K.EINBUCHUNG, K.AUSBUCHUNG):
        facts = {"datum": _day(values, "transfer_date")}
        transfer = _proposed(ExtractionKind.SECURITIES_TRANSFER, values)
        movements = [] if transfer is None else transfer.movements
        if len(movements) == 1:
            facts["fonds"] = movements[0].name
            facts["anteile"] = rules.units(movements[0].quantity)
        elif movements:
            facts["positionen"] = rules.count(len(movements))
        return facts
    return {}


def _proposed(kind: ExtractionKind, values: Mapping[str, object]) -> DepotProposal | None:
    """The reader's own parse of a report's or a transfer's rows (rules.depot.propose)."""
    try:
        return depot_rules.propose(kind.value, values)
    except (ArithmeticError, ValueError):
        return None


def _account(conn: Connection, document_id: UUID, kind: K) -> str | None:
    """The account the document was booked on: its movements' or positions' account, or for
    a cash statement the account it was imported on; before that the only depot account the
    workspace has. None when it is not known for sure (several depot accounts, nothing booked
    yet): a sentence naming it is then left out."""
    if kind is K.KONTOAUSZUG:
        ids = _ids(conn, import_table, document_id)
    else:
        ids = _ids(conn, movement_table, document_id) | _ids(conn, holding_table, document_id)
    if len(ids) == 1:
        (account_id,) = ids
        a = account_table.c
        row = conn.execute(
            select(a.label, a.external_ref_masked).where(a.id == account_id)
        ).one_or_none()
        if row is None:
            return None
        return f"{row.label} ({row.external_ref_masked})" if row.external_ref_masked else row.label
    if ids or kind is K.KONTOAUSZUG:
        return None
    accounts = depot_accounts(conn)
    return accounts[0].label if len(accounts) == 1 else None


def _ids(conn: Connection, table, document_id: UUID) -> set[UUID]:
    query = select(table.c.account_id).where(table.c.document_id == document_id)
    if "superseded_by" in table.c:
        query = query.where(table.c.superseded_by.is_(None))
    return set(conn.execute(query).scalars().all())
