"""camt.yaml and csv_camt.yaml: the statement parsers (docs/phases/phase-2.md §7.1, D33, D34).

The inputs are built here from the anonymised samples in fixtures/phase2/bank_samples/ or
from the fixture's inline text, so the archive-limit cases are generated, not committed.
"""

import ast
import inspect
import io
import zipfile
from decimal import Decimal

import fixture_models_phase6 as phase6
import pytest
from fixture_models_phase2 import BANK_SAMPLES, BankInput, ParserFixture, ZipMember, load

from hausbuch.rules import statements as rules
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.bank import StatementFormat
from hausbuch.services.bank import camt, csv_camt, detect_format, dkb_csv, parse_statement

CAMT = load("camt")
CSV = load("csv_camt")
DKB = phase6.load("dkb_csv")
assert isinstance(CAMT, ParserFixture) and isinstance(CSV, ParserFixture)
assert isinstance(DKB, phase6.DkbFixture)


def _sample_member(name: str) -> bytes:
    with zipfile.ZipFile(BANK_SAMPLES / "camt052.zip") as archive:
        return archive.read(name)


def _zip(members: list[ZipMember]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for member in members:
            if member.member is not None:
                content = _sample_member(member.member)
            elif member.text is not None:
                content = member.text.encode("utf-8")
            elif member.zeros is not None:
                content = bytes(member.zeros)
            else:
                content = _zip(member.zip_of)
            if member.repeat == 1:
                archive.writestr(member.name, content)
                continue
            stem, _, suffix = member.name.rpartition(".")
            for i in range(member.repeat):
                archive.writestr(f"{stem}-{i}.{suffix}", content)
    return buffer.getvalue()


def _replace(data: bytes, pairs: list[tuple[str, str]], encoding: str) -> bytes:
    text = data.decode(encoding)
    for old, new in pairs:
        assert old in text, f"replace: {old!r} does not occur"
        text = text.replace(old, new)
    return text.encode(encoding)


def build(spec: BankInput) -> bytes:
    if spec.zip is not None:
        return _zip(spec.zip)
    if spec.text is not None:
        return _replace(spec.text.encode(spec.encoding), spec.replace, spec.encoding)
    if spec.member is not None:
        return _replace(_sample_member(spec.member), spec.replace, spec.encoding)
    data = (BANK_SAMPLES / spec.file).read_bytes()
    if not spec.replace:
        return data
    if not data.startswith(b"PK\x03\x04"):
        return _replace(data, spec.replace, spec.encoding)
    # A ZIP sample: the replacements apply to every member.
    with zipfile.ZipFile(io.BytesIO(data)) as source:
        members = [(info, source.read(info)) for info in source.infolist()]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for info, content in members:
            archive.writestr(info.filename, _replace(content, spec.replace, spec.encoding))
    return buffer.getvalue()


def _check(case):
    data = build(case.input)
    if case.error is not None:
        with pytest.raises(RuleError) as caught:
            parse_statement(data, case.input.media_type)
        assert caught.value.code == case.error
        return
    outcome = parse_statement(data, case.input.media_type)
    got, want = outcome.statement, case.expect
    assert got.format is want.format
    assert outcome.balance_error == want.balance_error
    assert (got.account_iban, got.opening_balance, got.closing_balance) == (
        want.account_iban,
        want.opening_balance,
        want.closing_balance,
    )
    assert len(got.lines) == want.line_count
    assert sum((line.amount for line in got.lines), start=0) == want.total
    dates = [line.booking_date for line in got.lines]
    assert (min(dates, default=None), max(dates, default=None)) == (
        want.first_booking_on,
        want.last_booking_on,
    )
    assert got.warnings == want.warnings
    for pinned in want.lines:
        assert got.lines[pinned.index] == pinned.line, f"line {pinned.index}"


@pytest.mark.rule
@pytest.mark.parametrize("case", CAMT.cases, ids=lambda c: c.id)
def test_camt(case):
    _check(case)


@pytest.mark.rule
@pytest.mark.parametrize("case", CSV.cases, ids=lambda c: c.id)
def test_csv_camt(case):
    _check(case)


@pytest.mark.rule
@pytest.mark.parametrize("case", DKB.cases, ids=lambda c: c.id)
def test_dkb_csv(case):
    """dkb_csv.yaml (phase 6 D173 (3)): the parse, and the stated „Kontostand vom“ that
    pre-fills the typed closing (the import's half is in tests/db/test_services_dkb_import.py)."""
    if case.pending and not case.pending.startswith("phase 6 step 1"):
        pytest.skip(case.pending)
    _check(case)
    statement = parse_statement(build(case.input), case.input.media_type).statement
    if case.import_.prefilled_closing is not None:
        assert (statement.stated_balance, statement.stated_balance_on) == (
            case.import_.prefilled_closing,
            case.import_.prefilled_on,
        )
    assert all(line.raw == {} and line.bank_reference is None for line in statement.lines)


def test_a_dkb_file_is_not_a_sparkasse_csv_and_back():
    dkb = build(DKB.cases[0].input)
    sparkasse = build(next(c for c in CSV.cases if c.id == "sparkasse_export").input)
    assert detect_format(dkb, "text/csv") is StatementFormat.DKB_CSV
    assert not csv_camt.looks_like(dkb[:4096])
    assert not dkb_csv.looks_like(sparkasse[:4096])


def test_a_dkb_header_without_its_columns_is_the_files_error():
    data = '\ufeff"Girokonto";"DE61 1000 0000 0000 0030 04"\n"Buchungsdatum";"Umsatztyp"\n'
    with pytest.raises(RuleError) as caught:
        dkb_csv.parse_dkb_csv(data.encode())
    assert caught.value.code == rules.UNPARSABLE


def test_formats_are_sniffed_from_the_bytes_not_the_media_type():
    zip_bytes = (BANK_SAMPLES / "camt052.zip").read_bytes()
    csv_bytes = (BANK_SAMPLES / "csv_camt.csv").read_bytes()
    assert detect_format(zip_bytes, "application/octet-stream") is StatementFormat.CAMT
    assert detect_format(_sample_member("2026.04.01.xml"), "text/plain") is StatementFormat.CAMT
    assert detect_format(csv_bytes, "application/octet-stream") is StatementFormat.CSV_CAMT


def test_no_amount_passes_through_float():
    """Amounts are Decimal from their text (§14 step 3 FOCUS): no parser or rule module
    names float at all, and every parsed amount is a Decimal."""
    for module in (camt, csv_camt, rules):
        tree = ast.parse(inspect.getsource(module))
        names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
        assert "float" not in names, module.__name__
    for sample in ("camt052.zip", "csv_camt.csv"):
        for line in parse_statement((BANK_SAMPLES / sample).read_bytes()).statement.lines:
            assert type(line.amount) is Decimal


def test_lying_central_directory_is_bounded(monkeypatch):
    """The member sizes come from the file: a directory that understates them must not let
    the reader inflate past the limit. Simulated by lowering the limit below what the member
    really holds while the declared-size check is skipped."""
    monkeypatch.setattr(camt, "check_archive_members", lambda members: None)
    monkeypatch.setattr(camt, "MAX_ARCHIVE_BYTES", 1000)
    data = _zip([ZipMember(name="2026.04.01.xml", member="2026.04.01.xml")])
    with pytest.raises(RuleError) as caught:
        camt.read_camt(data)
    assert caught.value.code == rules.ARCHIVE_REJECTED


def test_parser_classes_follow_the_bank_source_protocol():
    data = (BANK_SAMPLES / "csv_camt.csv").read_bytes()
    assert len(csv_camt.CsvCamtSource().parse(data, "text/csv").lines) == 22
    zip_bytes = (BANK_SAMPLES / "camt052.zip").read_bytes()
    assert camt.CamtSource().parse(zip_bytes, "application/zip").closing_balance is not None


def test_check_archive_members_limits():
    member = rules.ArchiveMember
    rules.check_archive_members([member("a.xml", 10), member("b.xml", 10)])
    for bad in (
        [],
        [member("a.xml", 10), member("a.xml", 10)],
        [member("a/", 0, is_dir=True)],
        [member("a.xml", 10, encrypted=True)],
        [member(".xml", 1)],
        [member("a..b.xml", 1)],
    ):
        with pytest.raises(RuleError) as caught:
            rules.check_archive_members(bad)
        assert caught.value.code == rules.ARCHIVE_REJECTED


def _corrupted(method: int) -> bytes:
    """A one-member archive whose compressed stream is damaged after the local header."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", method) as archive:
        archive.writestr("2026.04.01.xml", b"<Document>" + b"<x/>" * 400 + b"</Document>")
    data = bytearray(buffer.getvalue())
    start = 30 + len("2026.04.01.xml")  # the local header, then the compressed data
    for i in range(start + 4, start + 24):
        data[i] ^= 0xFF
    return bytes(data)


@pytest.mark.parametrize(
    "method", [zipfile.ZIP_DEFLATED, zipfile.ZIP_LZMA, zipfile.ZIP_BZIP2], ids=str
)
def test_a_corrupt_member_stream_is_the_files_error(method):
    """zlib.error and lzma.LZMAError are not OSError subclasses: uncaught, they were a 500."""
    with pytest.raises(RuleError) as caught:
        camt.read_camt(_corrupted(method))
    assert caught.value.code == rules.ARCHIVE_REJECTED
