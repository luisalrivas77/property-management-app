"""jobs/bank_sync's statement fetch without a database (docs/phases/phase-5.md D154 (3)-(5)):
the walk over the real FintsSession and the scripted fake client, with the three reads of
services.bank_sync replaced and `write` recording what would be stored.

What it pins: the order of the requests, the 3040 continuation, a refusal that ends the dialog
(one new dialog, for the other year only, never with a pushTAN), the request cap, a stop that
is never the run's failure, a lock that is (D151), and a log line without the PDF. The
end-to-end run with the switch, the rows and the event is tests/db/test_jobs_bank_sync.py.
All data is invented."""

import base64
import logging
from contextlib import contextmanager
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from fints.exceptions import FinTSConnectionError
from fints_fake import Script, factory, fake_account
from fixture_models_phase5 import load

from hausbuch.jobs import bank_sync as job
from hausbuch.jobs.document_read import JobDeps
from hausbuch.rules import bank_statements as rules
from hausbuch.rules import errors as E
from hausbuch.schemas.bank_sync import StatementKey, SyncTrigger
from hausbuch.services import bank_sync as service
from hausbuch.services.bank import fints as F

FIXTURE = load("bank_statements")
TEXT = FIXTURE.pdf_base64.encode("ascii")  # what HIEKP carries: the PDF in base64
PDF = base64.b64decode(TEXT)
NOVEMBER = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)
JANUARY = datetime(2027, 1, 8, 9, 0, tzinfo=UTC)


def _plan() -> service.RunPlan:
    return service.RunPlan(
        sync_id=uuid4(),
        account_id=uuid4(),
        connection_id=uuid4(),
        trigger=SyncTrigger.MANUAL,
        token=NOVEMBER,
        requested_from=date(2026, 10, 1),
        requested_to=date(2026, 11, 12),
        bank_code="10000000",
        fints_url="https://fints.example.org/fints",
        login_name="loginname-xyz",
        client_data=None,
        tan_mechanism=None,
        iban_hmac=b"x" * 32,
        created_by=None,
        fetch_statements=True,
    )


class Walk:
    """One statement fetch: the session over a fake client, what `write` stored, the log."""

    def __init__(self, monkeypatch, script: Script, *, stored=(), first_year=2026, owned=True):
        made: list = []
        self.session = F.open_session(
            bank_code="10000000",
            login_name="loginname-xyz",
            pin="PIN-4711-secret",
            url="https://fints.example.org/fints",
            product_id="PRODUCTID0123456789ABCDEF",
            client_data=None,
            tan_mechanism=None,
            client_factory=factory(script, made),
        )
        self.session.open()
        self.client = made[0]
        self.stored: list[tuple[StatementKey, bytes]] = []
        self.store_error: Exception | None = None
        self.recorded: list = []

        @contextmanager
        def no_database(engine, workspace_id):
            yield None

        monkeypatch.setattr(job, "workspace_transaction", no_database)
        monkeypatch.setattr(service, "stored_statement_keys", lambda conn, a: list(stored))
        monkeypatch.setattr(service, "statements_first_year", lambda conn, a, today: first_year)
        monkeypatch.setattr(service, "still_owned", lambda conn, plan: owned)

    def write(self, step, *args, **kwargs):
        if step is service.record_statements_answer:  # 14 Nov 2026: what the account records
            _plan_, refusal = args
            self.recorded.append(refusal)
            return True
        assert step is service.store_statement
        _store, _plan_, key, pdf = args
        if self.store_error is not None:
            raise self.store_error
        self.stored.append((key, pdf))
        return uuid4()

    def run(self, at=NOVEMBER):
        deps = JobDeps(None, None, None, None, clock=lambda: at)
        run = job._Run(_plan(), log={"sync_id": "s"})
        job._fetch_statements(deps, self.session, run, fake_account(), self.write, uuid4())

    def asked(self) -> list[tuple[int, int]]:
        return [(year, number) for year, number, touchdown in self.client.statement_requests]


def _key(year, number):
    return StatementKey(year=year, number=number)


def _record(caplog):
    [record] = [r for r in caplog.records if r.getMessage() == "bank statements"]
    return record.fields


def test_asks_from_one_until_the_bank_refuses_and_stores_each_file(monkeypatch, caplog):
    walk = Walk(
        monkeypatch,
        Script(statements_offered=True, statements={(2026, n): [TEXT] for n in (1, 2, 3)}),
    )
    with caplog.at_level(logging.INFO, logger="hausbuch.jobs.bank_sync"):
        walk.run()
    assert walk.asked() == [(2026, 1), (2026, 2), (2026, 3), (2026, 4)]
    assert walk.stored == [(_key(2026, n), PDF) for n in (1, 2, 3)]
    fields = _record(caplog)
    assert fields["statements_end"] == "done"
    assert fields["statements_asked"] == ["1/2026", "2/2026", "3/2026", "4/2026"]
    assert (fields["statements_requests"], fields["statements_stored"]) == (4, 3)
    assert TEXT.decode() not in caplog.text and "%PDF" not in caplog.text


def test_continues_after_the_highest_stored_number(monkeypatch):
    stored = [_key(2026, n) for n in range(1, 11)]
    walk = Walk(monkeypatch, Script(statements_offered=True), stored=stored)
    walk.run()
    assert walk.asked() == [(2026, 11)]
    assert walk.stored == []


def test_nothing_is_asked_when_the_bank_does_not_offer_it(monkeypatch, caplog):
    walk = Walk(monkeypatch, Script(statements_offered=False))
    with caplog.at_level(logging.INFO, logger="hausbuch.jobs.bank_sync"):
        walk.run()
    assert walk.asked() == []
    assert _record(caplog)["statements_end"] == "not_offered"


def test_a_3040_continuation_is_followed_and_the_parts_joined(monkeypatch, caplog):
    parts = [TEXT[:40], TEXT[40:90], TEXT[90:]]
    walk = Walk(monkeypatch, Script(statements_offered=True, statements={(2026, 1): parts}))
    with caplog.at_level(logging.INFO, logger="hausbuch.jobs.bank_sync"):
        walk.run()
    assert walk.client.statement_requests[:3] == [
        (2026, 1, None),
        (2026, 1, "part-1"),
        (2026, 1, "part-2"),
    ]
    assert walk.stored == [(_key(2026, 1), PDF)]
    assert "part-1" not in caplog.text  # the Aufsetzpunkt is never logged


def test_a_statement_of_more_than_ten_parts_is_not_stored(monkeypatch):
    parts = [TEXT[i : i + 12] for i in range(0, 12 * 11, 12)]
    walk = Walk(monkeypatch, Script(statements_offered=True, statements={(2026, 1): parts}))
    walk.run()
    assert len(walk.asked()) == rules.MAX_STATEMENT_PARTS
    assert walk.stored == []


def test_a_refusal_that_ends_the_dialog_opens_one_new_dialog_for_the_other_year(monkeypatch):
    """January: the previous year's next number is refused with 9800, so the bank ended the
    dialog; one new dialog is opened for the current year's request."""
    stored = [_key(2026, n) for n in range(1, 12)]
    script = Script(
        statements_offered=True,
        statements={(2027, 1): [TEXT]},
        statement_codes={(2026, 12): "m9800,9210"},
    )
    walk = Walk(monkeypatch, script, stored=stored)
    walk.run(at=JANUARY)
    assert walk.asked() == [(2026, 12), (2027, 1), (2027, 2)]
    assert walk.client.calls.count("reopen") == 1
    assert walk.stored == [(_key(2027, 1), PDF)]


def test_a_reopening_that_asks_for_a_pushtan_stops_without_asking(monkeypatch, caplog):
    stored = [_key(2026, n) for n in range(1, 12)]
    script = Script(
        statements_offered=True,
        statements={(2027, 1): [TEXT]},
        statement_codes={(2026, 12): "m9800,9210"},
        reopen_asks_approval=True,
    )
    walk = Walk(monkeypatch, script, stored=stored)
    with caplog.at_level(logging.INFO, logger="hausbuch.jobs.bank_sync"):
        walk.run(at=JANUARY)
    assert walk.asked() == [(2026, 12)]
    assert "poll" not in walk.client.calls
    assert walk.stored == []
    fields = _record(caplog)
    assert (fields["statements_end"], fields["statements_exc_chain"]) == (
        "reopen_failed",
        "approval_required",
    )


def test_a_dialog_ended_with_no_other_year_to_ask_is_not_reopened(monkeypatch, caplog):
    script = Script(statements_offered=True, statement_codes={(2026, 1): "m9800,9210"})
    walk = Walk(monkeypatch, script)
    with caplog.at_level(logging.INFO, logger="hausbuch.jobs.bank_sync"):
        walk.run()
    assert walk.asked() == [(2026, 1)]
    assert "reopen" not in walk.client.calls
    assert _record(caplog)["statements_end"] == "dialog_ended"


def test_a_second_dialog_end_is_not_reopened(monkeypatch):
    """At most one new dialog per run, even when the other year's request ends it again."""
    stored = [_key(2026, n) for n in range(1, 12)]
    script = Script(
        statements_offered=True,
        statement_codes={(2026, 12): "m9800,9210", (2027, 1): "m9800,9210"},
    )
    walk = Walk(monkeypatch, script, stored=stored)
    walk.run(at=JANUARY)
    assert walk.asked() == [(2026, 12), (2027, 1)]
    assert walk.client.calls.count("reopen") == 1


def test_a_refused_number_in_an_open_dialog_moves_to_the_next_year(monkeypatch):
    stored = [_key(2026, n) for n in range(1, 12)]
    script = Script(statements_offered=True, statements={(2027, 1): [TEXT]})
    walk = Walk(monkeypatch, script, stored=stored)
    walk.run(at=JANUARY)
    assert walk.asked() == [(2026, 12), (2027, 1), (2027, 2)]
    assert "reopen" not in walk.client.calls


def test_at_most_fifteen_requests_per_run(monkeypatch):
    script = Script(statements_offered=True, statements={(2026, n): [TEXT] for n in range(1, 30)})
    walk = Walk(monkeypatch, script)
    walk.run()
    assert len(walk.asked()) == rules.MAX_STATEMENT_REQUESTS == 15
    assert len(walk.stored) == 15


def test_the_cap_inside_a_continuation_says_so(monkeypatch, caplog):
    statements = {(2026, n): [TEXT] for n in range(1, 14)}
    statements[(2026, 14)] = [TEXT[:40], TEXT[40:80], TEXT[80:]]
    walk = Walk(monkeypatch, Script(statements_offered=True, statements=statements))
    with caplog.at_level(logging.INFO, logger="hausbuch.jobs.bank_sync"):
        walk.run()
    assert len(walk.asked()) == 15
    assert len(walk.stored) == 13  # 14/2026 was cut off by the cap: asked again next run
    assert _record(caplog)["statements_end"] == "request_cap"


def test_an_invalid_file_is_not_stored_and_ends_its_year(monkeypatch, caplog):
    not_a_pdf = base64.b64encode(b"<html>not a statement</html>")
    script = Script(statements_offered=True, statements={(2026, 1): [not_a_pdf]})
    walk = Walk(monkeypatch, script)
    with caplog.at_level(logging.INFO, logger="hausbuch.jobs.bank_sync"):
        walk.run()
    assert walk.asked() == [(2026, 1)]
    assert walk.stored == []
    assert _record(caplog)["statements_reason"] == rules.NOT_PDF


def test_a_transport_failure_stops_the_fetch_and_is_not_the_runs_failure(monkeypatch, caplog):
    script = Script(
        statements_offered=True,
        statements={(2026, 1): [TEXT], (2026, 3): [TEXT]},
        statement_fail_at={(2026, 2): FinTSConnectionError("down")},
    )
    walk = Walk(monkeypatch, script)
    with caplog.at_level(logging.INFO, logger="hausbuch.jobs.bank_sync"):
        walk.run()  # no exception
    assert walk.asked() == [(2026, 1), (2026, 2)]
    assert walk.stored == [(_key(2026, 1), PDF)]
    fields = _record(caplog)
    assert fields["statements_end"] == "stopped"
    assert "FinTSConnectionError" in fields["statements_exc_chain"]


def test_a_general_refusal_of_the_message_stops_the_fetch(monkeypatch, caplog):
    script = Script(statements_offered=True, statement_codes={(2026, 1): "m9010"})
    walk = Walk(monkeypatch, script)
    with caplog.at_level(logging.INFO, logger="hausbuch.jobs.bank_sync"):
        walk.run()
    fields = _record(caplog)
    assert (fields["statements_end"], fields["statements_code"]) == ("stopped", "9010")


def test_a_lock_code_fails_the_run_as_d151_says(monkeypatch):
    script = Script(
        statements_offered=True,
        statements={(2026, 1): [TEXT]},
        statement_codes={(2026, 2): "m9050,9930"},
    )
    walk = Walk(monkeypatch, script)
    with pytest.raises(F.BankError) as raised:
        walk.run()
    assert (raised.value.code, raised.value.response_code) == (E.BANK_REFUSED, "9930")
    assert walk.stored == [(_key(2026, 1), PDF)]  # what came before the lock is kept


def test_a_lock_code_on_reopening_fails_the_run(monkeypatch):
    stored = [_key(2026, n) for n in range(1, 12)]
    script = Script(
        statements_offered=True,
        statement_codes={(2026, 12): "m9800,9210"},
        reopen_codes="3938",
    )
    walk = Walk(monkeypatch, script, stored=stored)
    with pytest.raises(F.BankError) as raised:
        walk.run(at=JANUARY)
    assert raised.value.response_code == "3938"


def test_an_error_while_storing_ends_the_fetch_quietly(monkeypatch, caplog):
    walk = Walk(monkeypatch, Script(statements_offered=True, statements={(2026, 1): [TEXT]}))
    walk.store_error = RuntimeError(f"cannot store {TEXT.decode()}")
    with caplog.at_level(logging.INFO, logger="hausbuch.jobs.bank_sync"):
        walk.run()  # never the run's failure
    fields = _record(caplog)
    assert (fields["statements_end"], fields["statements_exc_chain"]) == ("error", "RuntimeError")
    assert TEXT.decode() not in caplog.text


def test_a_restarted_run_stops_before_asking(monkeypatch):
    walk = Walk(
        monkeypatch, Script(statements_offered=True, statements={(2026, 1): [TEXT]}), owned=False
    )
    with pytest.raises(service.Superseded):
        walk.run()
    assert walk.asked() == []


# --- 14 Nov 2026: the access may not fetch statements (9370) ----------------------------------

PRODUCTION_9370 = "m9050,9010,9370"  # the first real fetch's answer, as the trace logged it


def test_a_permission_refusal_ends_the_fetch_logs_its_code_and_is_recorded(monkeypatch, caplog):
    walk = Walk(monkeypatch, Script(statements_offered=True, statement_refusal=PRODUCTION_9370))
    with caplog.at_level(logging.INFO, logger="hausbuch.jobs.bank_sync"):
        walk.run()  # never the run's failure
    assert walk.asked() == [(2026, 1)]
    assert walk.stored == []
    assert walk.recorded == [("9370", _key(2026, 1))]
    fields = _record(caplog)
    assert (fields["statements_end"], fields["statements_code"]) == ("not_permitted", "9370")
    assert fields["statements_recorded"] is True


def test_a_permission_refusal_asks_no_other_year(monkeypatch):
    """January: the previous year's refusal for lack of permission also ends the current
    year's walk, and no new dialog is opened."""
    stored = [_key(2026, n) for n in range(1, 12)]
    script = Script(
        statements_offered=True,
        statements={(2027, 1): [TEXT]},
        statement_codes={(2026, 12): "m9800,9370"},
    )
    walk = Walk(monkeypatch, script, stored=stored)
    walk.run(at=JANUARY)
    assert walk.asked() == [(2026, 12)]
    assert "reopen" not in walk.client.calls
    assert walk.recorded == [("9370", _key(2026, 12))]


def test_a_missing_number_is_logged_with_its_code_and_recorded_as_answered(monkeypatch, caplog):
    script = Script(statements_offered=True, statements={(2026, 1): [TEXT]})
    walk = Walk(monkeypatch, script)
    with caplog.at_level(logging.INFO, logger="hausbuch.jobs.bank_sync"):
        walk.run()
    assert walk.asked() == [(2026, 1), (2026, 2)]
    fields = _record(caplog)
    assert (fields["statements_end"], fields["statements_code"]) == ("done", "3010")
    assert walk.recorded == [None]  # the bank answered the order: a refusal would end


def test_nothing_is_recorded_when_the_bank_never_answered_an_order(monkeypatch):
    walk = Walk(monkeypatch, Script(statements_offered=False))
    walk.run()
    walk_stopped = Walk(monkeypatch, Script(statements_offered=True, statement_refusal="m9010"))
    walk_stopped.run()
    assert walk.recorded == walk_stopped.recorded == []


def test_a_failed_record_is_logged_and_never_the_runs_failure(monkeypatch, caplog):
    walk = Walk(monkeypatch, Script(statements_offered=True, statement_refusal=PRODUCTION_9370))

    def failing(step, *args, **kwargs):
        raise RuntimeError("database gone")

    walk.write = failing
    with caplog.at_level(logging.INFO, logger="hausbuch.jobs.bank_sync"):
        walk.run()
    fields = _record(caplog)
    assert fields["statements_end"] == "not_permitted"
    assert fields["statements_record_error"] == "RuntimeError"
    assert "statements_recorded" not in fields


def test_a_write_refusal_is_a_bug_and_still_raises(monkeypatch):
    script = Script(statements_offered=True, statement_fail_at={(2026, 1): F.WriteRefused("x")})
    walk = Walk(monkeypatch, script)
    with pytest.raises(F.WriteRefused):
        walk.run()
