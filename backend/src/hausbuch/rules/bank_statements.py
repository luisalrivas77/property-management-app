"""The bank's own PDF statements over FinTS: which to ask for and what an answer means
(docs/phases/phase-5.md D154 (2)-(4), step 2b).

No I/O, no network, no database, no logging. Fixture: tests/fixtures/phase5/bank_statements.yaml
(loaded through tests/fixture_models_phase5.py, whose reference functions these must equal).

- `plan_probes(stored, today, first_year)`: where each year's walk starts, in asking order.
- `next_probe(plan, answered, requests)`: the next statement to ask for, or None.
- `read_answer(answers, parts, touchdown, earlier)`: one HKEKP answer, never read by D151's
  `deciding_code` (a refused number is not a failed sync).

A refused number is never remembered: the next run asks the same number again, since a
statement the bank has not cut yet is refused today and delivered tomorrow. A refusal that says
the access may not fetch statements at all (NOT_PERMITTED, 14 Nov 2026) ends the fetch for the
run; the job records it on the account so the Bankabruf card can say what to do, and the next
run asks again.
"""

import base64
import binascii
from collections.abc import Iterable, Sequence
from datetime import date

from hausbuch.schemas.bank_sync import StatementKey, StatementOutcome, StatementReading

from .bank_sync import LOCK_CODES, MESSAGE_LEVEL, SEGMENT_LEVEL, BankAnswer

FIRST_STATEMENT_YEAR = 2026  # bank accounts from 1 Jan 2026 (decided 6 Nov 2026)
MAX_STATEMENT_REQUESTS = 15  # D154 (3): HKEKP messages per run, continuations included
MAX_STATEMENT_PARTS = 10  # D154 (2): answers (3040 continuations) of one statement
MAX_STATEMENT_BYTES = 20 * 1024 * 1024  # D154 (2): the decoded file
# The base64 text of a file of MAX_STATEMENT_BYTES, with room for line breaks: anything longer
# is refused before it is decoded, so a runaway answer never doubles in memory.
_MAX_ENCODED_BYTES = MAX_STATEMENT_BYTES * 4 // 3 + 64 * 1024

NO_DATA = "3010"  # Keine Einträge vorhanden (DK Rückmeldungscodes)
TOUCHDOWN = "3040"  # Es liegen weitere Informationen vor (an Aufsetzpunkt follows)
DIALOG_ENDED = "9800"  # Dialog abgebrochen
STATEMENT_SEGMENT = "HKEKP"
# Codes on the HKEKP segment that say this access may not fetch statements at all, rather
# than that one number does not exist (14 Nov 2026, the first real fetch: 9050, then 9010 and
# 9370 „Es liegt keine Auftragsberechtigung im Institut vor.“ on HKEKP). Checked against the
# DK's Rückmeldungscodes (version of 3 Feb 2026, read from fints.org, not committed): on an
# order, 9370 is „Signaturberechtigung reicht nicht aus“ and 9380 „Benutzer hat keine
# Auftragsberechtigung“; 9380's other meanings (TAN or signature procedure not allowed) are
# answers to the whole message, never to the HKEKP segment, which is all this set is read on.
NOT_PERMITTED_CODES = frozenset({"9370", "9380"})

# Why a delivered file is not stored (StatementReading.reason; logged, never the bytes).
TOO_MANY_PARTS = "too_many_parts"
NOT_BASE64 = "not_base64"
NOT_PDF = "not_pdf"
TOO_LARGE = "too_large"
NO_EOF = "no_eof"


def _start(stored: Iterable[StatementKey], year: int) -> StatementKey:
    numbers = [k.number for k in stored if k.year == year]
    return StatementKey(year=year, number=max(numbers, default=0) + 1)


def plan_probes(stored: Iterable[StatementKey], today: date, first_year: int) -> list[StatementKey]:
    """Where each year's walk starts, the previous year first (D154 (3)):

    - the current year: the highest stored number + 1, else 1;
    - the previous year the same, but only while statement 1 of the current year is not
      stored (the bank has then closed the previous year) and the year is not before
      `first_year`;
    - `first_year` (the year of the account's earliest booked line) never counts before
      FIRST_STATEMENT_YEAR. A gap below the highest stored number is not asked again."""
    stored = list(stored)
    first = max(first_year, FIRST_STATEMENT_YEAR)
    current, previous = today.year, today.year - 1
    plan: list[StatementKey] = []
    if previous >= first and StatementKey(year=current, number=1) not in stored:
        plan.append(_start(stored, previous))
    if current >= first:
        plan.append(_start(stored, current))
    return plan


def next_probe(
    plan: Sequence[StatementKey],
    answered: Sequence[tuple[StatementKey, bool]],
    requests: int | None = None,
) -> StatementKey | None:
    """The next statement to ask for: within a year, one number after the other from the
    plan's start until one is not delivered (`answered` holds each asked key and whether its
    file was stored), then the next year of the plan. None once every year has ended or
    `requests` (the HKEKP messages sent, continuations included; by default one per answered
    key) reached MAX_STATEMENT_REQUESTS."""
    sent = len(answered) if requests is None else requests
    if sent >= MAX_STATEMENT_REQUESTS:
        return None
    for begin in plan:
        mine = [(k, ok) for k, ok in answered if k.year == begin.year]
        if any(not ok for _, ok in mine):
            continue  # a number of this year was not delivered: the year ends for the run
        return StatementKey(year=begin.year, number=begin.number + len(mine))
    return None


def _file(parts: Sequence[bytes]) -> tuple[bytes | None, str | None]:
    """The parts joined and decoded once (D154 (2)); when that fails (a bank that encodes each
    3040 part on its own, with padding inside the joined text), each part decoded and the bytes
    joined (review of PR #212). Raw bytes that already start with %PDF- are taken as they are.
    Returns the file or why it is refused."""
    joined = b"".join(parts)
    if len(joined) > _MAX_ENCODED_BYTES:
        return None, TOO_LARGE
    if joined.startswith(b"%PDF-"):
        data = joined
    else:
        try:
            data = base64.b64decode(b"".join(joined.split()), validate=True)
        except (binascii.Error, ValueError):
            try:
                data = b"".join(
                    base64.b64decode(b"".join(part.split()), validate=True) for part in parts
                )
            except (binascii.Error, ValueError):
                return None, NOT_BASE64
    if not data.startswith(b"%PDF-"):
        return None, NOT_PDF
    if len(data) > MAX_STATEMENT_BYTES:
        return None, TOO_LARGE
    if not data.rstrip().endswith(b"%%EOF"):
        return None, NO_EOF
    return data, None


def _refuses(code: str) -> bool:
    return len(code) == 4 and code.startswith("9")


def read_answer(
    answers: Sequence[BankAnswer],
    parts: Sequence[bytes],
    touchdown: bool = False,
    earlier: Sequence[bytes] = (),
) -> StatementReading:
    """One answer to one HKEKP (D154 (4)). `answers` are the message's answer codes of both
    levels, `parts` the „Gebuchte Umsätze“ of each HIEKP in it, `touchdown` whether its 3040
    carried an Aufsetzpunkt, `earlier` the parts of this statement's previous answers. In
    this order:

    1. a lock code anywhere → LOCKED (the run fails as D151 says);
    2. a NOT_PERMITTED_CODES code on the HKEKP segment, wherever it stands among the
       segment's answers (the bank sends the general 9010 before it) → NOT_PERMITTED;
    3. 3010 or any other 9xxx on the HKEKP segment → NOT_AVAILABLE;
    4. 3040 with an Aufsetzpunkt → CONTINUE (INVALID once a 11th part would be needed);
    5. HIEKP data → STATEMENT with the file, else INVALID with the reason;
    6. a 9xxx at the message level or on another segment → STOPPED;
    7. otherwise NOT_AVAILABLE.

    `dialog_ended` is set whenever the message carries 9800."""
    ended = any(a.level == MESSAGE_LEVEL and a.code == DIALOG_ENDED for a in answers)
    lock = next((a.code for a in answers if a.code in LOCK_CODES), None)
    if lock is not None:
        return StatementReading(outcome=StatementOutcome.LOCKED, code=lock, dialog_ended=ended)
    own = [
        a
        for a in answers
        if a.level == SEGMENT_LEVEL and (a.segment or "").startswith(STATEMENT_SEGMENT)
    ]
    denied = next((a.code for a in own if a.code in NOT_PERMITTED_CODES), None)
    if denied is not None:
        return StatementReading(
            outcome=StatementOutcome.NOT_PERMITTED, code=denied, dialog_ended=ended
        )
    refusal = next((a.code for a in own if a.code == NO_DATA or _refuses(a.code)), None)
    if refusal is not None:
        return StatementReading(
            outcome=StatementOutcome.NOT_AVAILABLE, code=refusal, dialog_ended=ended
        )
    if touchdown and any(a.code == TOUCHDOWN for a in own):
        if len(earlier) + 1 >= MAX_STATEMENT_PARTS:
            return StatementReading(
                outcome=StatementOutcome.INVALID, reason=TOO_MANY_PARTS, dialog_ended=ended
            )
        return StatementReading(outcome=StatementOutcome.CONTINUE, dialog_ended=ended)
    if parts:
        data, reason = _file([*earlier, *parts])
        if data is None:
            return StatementReading(
                outcome=StatementOutcome.INVALID, reason=reason, dialog_ended=ended
            )
        return StatementReading(outcome=StatementOutcome.STATEMENT, pdf=data, dialog_ended=ended)
    failed = next((a.code for a in answers if _refuses(a.code)), None)
    if failed is not None:
        return StatementReading(outcome=StatementOutcome.STOPPED, code=failed, dialog_ended=ended)
    return StatementReading(outcome=StatementOutcome.NOT_AVAILABLE, dialog_ended=ended)


def statement_title(key: StatementKey) -> str:
    """The document's title (D154 (5)): „Kontoauszug <Nummer>/<Jahr>“."""
    return f"Kontoauszug {key.number}/{key.year}"


def statement_filename(key: StatementKey) -> str:
    """The stored original's filename (D154 (5)): Kontoauszug-<Jahr>-<Nummer>.pdf."""
    return f"Kontoauszug-{key.year}-{key.number}.pdf"
