"""rules.bank_statements (docs/phases/phase-5.md D154 (2)-(4)) against bank_statements.yaml,
plus what the fixture does not pin: the join of parts cut off a base64 boundary, a raw PDF, the
size cap, and the request cap counting continuations. All data is invented."""

import base64
from datetime import date

import pytest
from fixture_models_phase5 import _key, load

from hausbuch.rules import bank_statements as rules
from hausbuch.rules.bank_sync import BankAnswer
from hausbuch.schemas.bank_sync import StatementKey, StatementOutcome

FIXTURE = load("bank_statements")
PDF = base64.b64decode(FIXTURE.pdf_base64)
OK = [BankAnswer("segment", "0020", "HKEKP:3")]


def _walk(plan, bank_has):
    answered: list[tuple[StatementKey, bool]] = []
    while (key := rules.next_probe(plan, answered)) is not None:
        answered.append((key, key in bank_has))
    return [k for k, _ in answered]


@pytest.mark.parametrize("case", FIXTURE.plan_cases, ids=lambda c: c.id)
def test_plan_and_walk_match_the_fixture(case):
    plan = rules.plan_probes(case.stored_keys, case.today, case.first_year)
    assert plan == [_key(k) for k in case.expect.probes]
    if case.bank_has is not None:
        assert _walk(plan, {_key(k) for k in case.bank_has}) == [
            _key(k) for k in case.expect.requests
        ]


@pytest.mark.parametrize("case", FIXTURE.answer_cases, ids=lambda c: c.id)
def test_read_answer_matches_the_fixture(case):
    answers = [BankAnswer(a.level, a.code, a.segment) for a in case.answers]
    got = rules.read_answer(
        answers,
        [p.encode("ascii") for p in case.parts],
        case.touchdown,
        [p.encode("ascii") for p in case.earlier],
    )
    want = case.expect
    assert (got.outcome, got.code, got.dialog_ended, got.reason) == (
        want.outcome,
        want.code,
        want.dialog_ended,
        want.reason,
    )
    if want.outcome is StatementOutcome.STATEMENT:
        assert got.pdf is not None and len(got.pdf) == want.size
    else:
        assert got.pdf is None


def test_parts_cut_off_a_base64_boundary_still_join():
    """D154 (2) decodes each part; a bank that cuts the text mid-quantum would break that, so
    the rule joins first and decodes once."""
    text = FIXTURE.pdf_base64.encode("ascii")
    reading = rules.read_answer(OK, [text[47:]], earlier=[text[:23], text[23:47]])
    assert reading.outcome is StatementOutcome.STATEMENT
    assert reading.pdf == PDF


def test_a_raw_pdf_is_taken_as_it_is():
    reading = rules.read_answer(OK, [PDF])
    assert reading.outcome is StatementOutcome.STATEMENT and reading.pdf == PDF


def test_base64_with_line_breaks_is_decoded():
    text = FIXTURE.pdf_base64.encode("ascii")
    wrapped = b"\r\n".join(text[i : i + 64] for i in range(0, len(text), 64))
    assert rules.read_answer(OK, [wrapped]).pdf == PDF


def test_trailing_whitespace_after_eof_is_allowed():
    padded = base64.b64encode(PDF + b"\r\n \n")
    assert rules.read_answer(OK, [padded]).outcome is StatementOutcome.STATEMENT


def test_a_file_over_20_mb_is_refused(monkeypatch):
    monkeypatch.setattr(rules, "MAX_STATEMENT_BYTES", len(PDF) - 1)
    reading = rules.read_answer(OK, [FIXTURE.pdf_base64.encode("ascii")])
    assert (reading.outcome, reading.reason) == (StatementOutcome.INVALID, rules.TOO_LARGE)


def test_an_oversized_answer_is_refused_before_decoding(monkeypatch):
    monkeypatch.setattr(rules, "_MAX_ENCODED_BYTES", 10)
    reading = rules.read_answer(OK, [FIXTURE.pdf_base64.encode("ascii")])
    assert (reading.outcome, reading.reason) == (StatementOutcome.INVALID, rules.TOO_LARGE)


def test_a_3040_without_touchdown_reads_the_data():
    """A 3040 whose Aufsetzpunkt could not be read is not followed: the data is checked as a
    whole file (and is refused when it is only a part)."""
    answers = [BankAnswer("segment", "3040", "HKEKP:3")]
    part = FIXTURE.pdf_base64.encode("ascii")[:48]
    reading = rules.read_answer(answers, [part], touchdown=False)
    assert reading.outcome is StatementOutcome.INVALID


def test_a_9xxx_on_another_segment_stops():
    answers = [BankAnswer("segment", "9942", "HNSHK:2")]
    reading = rules.read_answer(answers, [])
    assert (reading.outcome, reading.code) == (StatementOutcome.STOPPED, "9942")


def test_the_request_cap_counts_continuations():
    plan = [StatementKey(year=2026, number=1)]
    answered = [(StatementKey(year=2026, number=n), True) for n in range(1, 6)]
    assert rules.next_probe(plan, answered) == StatementKey(year=2026, number=6)
    assert rules.next_probe(plan, answered, requests=rules.MAX_STATEMENT_REQUESTS) is None


def test_a_refused_year_moves_to_the_next():
    plan = [StatementKey(year=2026, number=12), StatementKey(year=2027, number=1)]
    answered = [(StatementKey(year=2026, number=12), False)]
    assert rules.next_probe(plan, answered) == StatementKey(year=2027, number=1)


def test_no_plan_when_the_first_line_is_in_the_future():
    assert rules.plan_probes([], date(2026, 11, 12), 2027) == []


def test_title_and_filename():
    key = StatementKey(year=2026, number=7)
    assert rules.statement_title(key) == "Kontoauszug 7/2026"
    assert rules.statement_filename(key) == "Kontoauszug-2026-7.pdf"


def test_parts_encoded_one_by_one_are_decoded_one_by_one():
    """A bank that base64-encodes each 3040 part on its own puts padding inside the joined
    text; the file is then decoded part by part (review of PR #212)."""
    parts = [base64.b64encode(PDF[:10]), base64.b64encode(PDF[10:])]
    assert b"=" in parts[0]
    reading = rules.read_answer(OK, parts)
    assert reading.outcome is StatementOutcome.STATEMENT
    assert reading.pdf == PDF
