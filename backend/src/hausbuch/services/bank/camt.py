"""camt.052 / camt.053 reader: one XML file, or a ZIP of daily reports (D33, D34, §7.1).

Safety, in this order, before any XML is parsed:
- A ZIP's central directory is checked first (rules.statements.check_archive_members: flat
  .xml names, member count, declared uncompressed size); each member is then read with a
  bound, so a directory that lies about sizes cannot exceed the limit; a member that is
  itself an archive is refused. Members are read in memory, never extracted to disk.
- XML is parsed with entity resolution, DTD loading and network access off, and a document
  that carries any DOCTYPE (where entities are declared) is refused.

Reading: one report per Rpt (camt.052) or Stmt (camt.053). Only entries with status BOOK
become lines; the sign comes from CdtDbtInd (DBIT negative, for balances too). The
counterparty is Cdtr on a debit and Dbtr on a credit. Bank transaction codes are read from
TxDtls (the real export leaves the entry-level BkTxCd empty). Several Ustrd lines are joined
with a space. The statement's opening is the first report's OPBD, its closing the last
report's CLBD, reports ordered by their CLBD date.

`raw` holds only fields that are not columns and never an IBAN: bank transaction codes, the
counterparty BIC, the entry status and the report's id.
"""

import io
import re
import zipfile
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from lxml import etree

from hausbuch.rules.errors import RuleError
from hausbuch.rules.statements import (
    ARCHIVE_REJECTED,
    ENTITY_DECLARED,
    MAX_ARCHIVE_BYTES,
    UNPARSABLE,
    UNSUPPORTED_CURRENCY,
    ArchiveMember,
    ReportBalances,
    check_archive_members,
    check_reports,
)
from hausbuch.schemas.bank import ParsedStatement, StatementFormat, StatementLine

_ZIP_MAGIC = b"PK\x03\x04"
_NAMESPACE = re.compile(r"^urn:iso:std:iso:20022:tech:xsd:camt\.05[23]\.001\.\d{2}$")
_AMOUNT = re.compile(r"^[0-9]{1,12}(\.[0-9]{1,2})?$")
_BOOKED = "BOOK"
_OPENING_CODES = ("OPBD", "PRCD")  # PRCD: previous closing booked, used by some banks
_CLOSING_CODE = "CLBD"


@dataclass(frozen=True)
class CamtResult:
    statement: ParsedStatement
    reports: list[ReportBalances]
    balance_error: str | None  # check_reports' first break, or None


class CamtSource:
    """BankSource for camt.052 / camt.053 (one XML file, or a ZIP of reports)."""

    def parse(self, data: bytes, media_type: str) -> ParsedStatement:
        return read_camt(data).statement


def read_camt(data: bytes) -> CamtResult:
    documents = _archive_members(data) if data.startswith(_ZIP_MAGIC) else [data]
    reports: list[_Report] = []
    for document in documents:
        reports.extend(_reports(_parse_xml(document)))
    if not reports:
        raise RuleError(UNPARSABLE, "no camt report in the file")

    ibans = {r.account_iban for r in reports}
    if len(ibans) > 1:
        raise RuleError(UNPARSABLE, "the reports belong to more than one account")
    with_balances = [r for r in reports if r.balances is not None]
    if with_balances and len(with_balances) != len(reports):
        raise RuleError(UNPARSABLE, "some reports carry booked balances and some do not")

    if with_balances:
        # Stable: two reports of one day keep their file order (check_reports flags them).
        ordered = sorted(reports, key=lambda r: r.balances.closing_on)
        opening, closing = ordered[0].balances.opening, ordered[-1].balances.closing
        balance_error = check_reports([r.balances for r in ordered])
    else:
        ordered = sorted(reports, key=_first_booking)
        opening = closing = None
        balance_error = None

    statement = ParsedStatement(
        format=StatementFormat.CAMT,
        account_iban=ibans.pop(),
        opening_balance=opening,
        closing_balance=closing,
        lines=[line for report in ordered for line in report.lines],
        warnings=[w for report in ordered for w in report.warnings],
    )
    return CamtResult(statement, [r.balances for r in ordered if r.balances], balance_error)


def _first_booking(report: "_Report") -> date:
    return min((line.booking_date for line in report.lines), default=date.min)


# --- ZIP -----------------------------------------------------------------------------


def _archive_members(data: bytes) -> list[bytes]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, zipfile.LargeZipFile, ValueError):
        raise RuleError(ARCHIVE_REJECTED, "not a readable ZIP archive") from None
    with archive:
        infos = archive.infolist()
        check_archive_members(
            [
                ArchiveMember(
                    name=info.filename,
                    size=info.file_size,
                    is_dir=info.is_dir(),
                    encrypted=bool(info.flag_bits & 0x1),
                )
                for info in infos
            ]
        )
        members = []
        budget = MAX_ARCHIVE_BYTES
        for info in sorted(infos, key=lambda i: i.filename):
            try:
                with archive.open(info) as handle:
                    content = handle.read(budget + 1)
            except (zipfile.BadZipFile, NotImplementedError, RuntimeError, OSError, EOFError):
                raise RuleError(ARCHIVE_REJECTED, "a member could not be read") from None
            if len(content) > budget:
                raise RuleError(
                    ARCHIVE_REJECTED, f"more than {MAX_ARCHIVE_BYTES} bytes uncompressed"
                )
            budget -= len(content)
            if content.startswith(_ZIP_MAGIC):
                raise RuleError(ARCHIVE_REJECTED, "nested archives are not read")
            members.append(content)
    return members


# --- XML -----------------------------------------------------------------------------


def _parser() -> etree.XMLParser:
    return etree.XMLParser(
        resolve_entities=False,
        no_network=True,
        load_dtd=False,
        dtd_validation=False,
        huge_tree=False,
        remove_comments=True,
        remove_pis=True,
    )


def _parse_xml(data: bytes) -> etree._Element:
    if data.startswith(_ZIP_MAGIC):
        raise RuleError(ARCHIVE_REJECTED, "nested archives are not read")
    try:
        root = etree.fromstring(data, _parser())
    except etree.XMLSyntaxError:
        raise RuleError(UNPARSABLE, "the XML is not well-formed") from None
    docinfo = root.getroottree().docinfo
    if docinfo.doctype or docinfo.internalDTD is not None or docinfo.externalDTD is not None:
        raise RuleError(ENTITY_DECLARED, "XML with a DOCTYPE or entity declarations is refused")
    return root


class _Ns:
    """Child lookup in the document's own camt namespace (camt.052.001.08, .053.001.02, …)."""

    def __init__(self, root: etree._Element) -> None:
        tag = etree.QName(root)
        if tag.localname != "Document" or not _NAMESPACE.match(tag.namespace or ""):
            raise RuleError(UNPARSABLE, "not a camt.052 / camt.053 document")
        self.ns = tag.namespace

    def path(self, path: str) -> str:
        return "/".join(f"{{{self.ns}}}{part}" for part in path.split("/"))

    def find(self, el: etree._Element | None, path: str) -> etree._Element | None:
        return None if el is None else el.find(self.path(path))

    def findall(self, el: etree._Element, path: str) -> list[etree._Element]:
        return el.findall(self.path(path))

    def text(self, el: etree._Element | None, path: str) -> str | None:
        found = self.find(el, path)
        if found is None or found.text is None:
            return None
        value = " ".join(found.text.split())
        return value or None


@dataclass
class _Report:
    account_iban: str | None
    balances: ReportBalances | None
    lines: list[StatementLine]
    warnings: list[str]


def _reports(root: etree._Element) -> list[_Report]:
    ns = _Ns(root)
    containers = [("BkToCstmrAcctRpt", "Rpt"), ("BkToCstmrStmt", "Stmt")]
    for message, report in containers:
        body = ns.find(root, message)
        if body is not None:
            return [_report(ns, el) for el in ns.findall(body, report)]
    raise RuleError(UNPARSABLE, "neither BkToCstmrAcctRpt nor BkToCstmrStmt")


def _report(ns: _Ns, rpt: etree._Element) -> _Report:
    acct = ns.find(rpt, "Acct")
    currency = ns.text(acct, "Ccy")
    if currency is not None and currency != "EUR":
        raise RuleError(UNSUPPORTED_CURRENCY, "only EUR accounts are supported")
    report_id = ns.text(rpt, "Id")

    lines, warnings, booked_sum = [], [], Decimal("0.00")
    for entry in ns.findall(rpt, "Ntry"):
        status = ns.text(entry, "Sts/Cd") or ns.text(entry, "Sts")
        if status != _BOOKED:
            warnings.append("an entry that is not booked was skipped")
            continue
        line = _line(ns, entry, report_id)
        booked_sum += line.amount
        lines.append(line)

    return _Report(
        account_iban=ns.text(acct, "Id/IBAN"),
        balances=_balances(ns, rpt, booked_sum),
        lines=lines,
        warnings=warnings,
    )


def _balances(ns: _Ns, rpt: etree._Element, booked_sum: Decimal) -> ReportBalances | None:
    found: dict[str, tuple[Decimal, date]] = {}
    for bal in ns.findall(rpt, "Bal"):
        code = ns.text(bal, "Tp/CdOrPrtry/Cd")
        if code in (*_OPENING_CODES, _CLOSING_CODE) and code not in found:
            found[code] = (_signed(ns, bal), _date(ns, bal, "Dt"))
    opening = next((found[c] for c in _OPENING_CODES if c in found), None)
    closing = found.get(_CLOSING_CODE)
    if opening is None and closing is None:
        return None
    if opening is None or closing is None:
        raise RuleError(UNPARSABLE, "a report has an opening or a closing balance, not both")
    return ReportBalances(
        opening=opening[0], closing=closing[0], closing_on=closing[1], entries_sum=booked_sum
    )


def _line(ns: _Ns, entry: etree._Element, report_id: str | None) -> StatementLine:
    amount = _signed(ns, entry)
    credit = ns.text(entry, "CdtDbtInd") == "CRDT"
    details = ns.findall(entry, "NtryDtls/TxDtls")
    tx = details[0] if len(details) == 1 else None
    side = "Dbtr" if credit else "Cdtr"

    purpose_parts = [
        " ".join(u.text.split())
        for d in details
        for u in ns.findall(d, "RmtInf/Ustrd")
        if u.text and u.text.strip()
    ]
    name = None
    iban = None
    if tx is not None:
        name = ns.text(tx, f"RltdPties/{side}/Pty/Nm") or ns.text(tx, f"RltdPties/{side}/Nm")
        iban = ns.text(tx, f"RltdPties/{side}Acct/Id/IBAN")

    code_el = ns.find(tx, "BkTxCd") if tx is not None else None
    if code_el is None or len(code_el) == 0:
        code_el = ns.find(entry, "BkTxCd")
    raw: dict = {"status": _BOOKED}
    codes = {
        "domain": ns.text(code_el, "Domn/Cd"),
        "family": ns.text(code_el, "Domn/Fmly/Cd"),
        "sub_family": ns.text(code_el, "Domn/Fmly/SubFmlyCd"),
        "proprietary": ns.text(code_el, "Prtry/Cd"),
    }
    if any(codes.values()):
        raw["bank_transaction_code"] = {k: v for k, v in codes.items() if v is not None}
    if tx is not None:
        agent = "DbtrAgt" if credit else "CdtrAgt"
        bic = ns.text(tx, f"RltdAgts/{agent}/FinInstnId/BICFI") or ns.text(
            tx, f"RltdAgts/{agent}/FinInstnId/BIC"
        )
        if bic:
            raw["counterparty_bic"] = bic
        # A direct debit's SEPA creditor id (CSV: Glaeubiger ID); not an IBAN.
        creditor_id = ns.text(tx, "RltdPties/Cdtr/Pty/Id/PrvtId/Othr/Id") or ns.text(
            tx, "RltdPties/Cdtr/Id/PrvtId/Othr/Id"
        )
        if creditor_id and not credit:
            raw["creditor_id"] = creditor_id
    if len(details) > 1:
        raw["transaction_count"] = len(details)
    if report_id:
        raw["report_id"] = report_id

    return StatementLine(
        booking_date=_date(ns, entry, "BookgDt"),
        value_date=_date(ns, entry, "ValDt", required=False),
        amount=amount,
        counterparty_name=name,
        counterparty_iban=iban,
        purpose=" ".join(purpose_parts),
        booking_text=ns.text(entry, "AddtlNtryInf"),
        bank_reference=ns.text(entry, "AcctSvcrRef") or ns.text(entry, "NtryRef"),
        end_to_end_ref=ns.text(tx, "Refs/EndToEndId") if tx is not None else None,
        mandate_ref=ns.text(tx, "Refs/MndtId") if tx is not None else None,
        raw=raw,
    )


def _signed(ns: _Ns, el: etree._Element) -> Decimal:
    """Amt (a decimal string, never a float) signed by CdtDbtInd."""
    amt = ns.find(el, "Amt")
    if amt is None or amt.text is None or not _AMOUNT.match(amt.text.strip()):
        raise RuleError(UNPARSABLE, "an amount is missing or malformed")
    if amt.get("Ccy", "EUR") != "EUR":
        raise RuleError(UNSUPPORTED_CURRENCY, "only EUR amounts are supported")
    value = Decimal(amt.text.strip()).quantize(Decimal("0.01"))
    indicator = ns.text(el, "CdtDbtInd")
    if indicator == "DBIT":
        return -value
    if indicator == "CRDT":
        return value
    raise RuleError(UNPARSABLE, "a credit/debit indicator is missing")


def _date(ns: _Ns, el: etree._Element, path: str, *, required: bool = True) -> date | None:
    """<X><Dt>YYYY-MM-DD</Dt></X> or <X><DtTm>…</DtTm></X> (the date part)."""
    text = ns.text(el, f"{path}/Dt") or ns.text(el, f"{path}/DtTm")
    if text is None:
        if required:
            raise RuleError(UNPARSABLE, f"a {path} date is missing")
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        raise RuleError(UNPARSABLE, f"a {path} date is malformed") from None
