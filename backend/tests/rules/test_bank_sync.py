"""rules.bank_sync (docs/phases/phase-5.md §4, D115-D120, D127) against its four fixtures,
plus the pairs of the state machine step 0 left to step 1 (§12.1) and the small helpers.

Every IBAN and bank code is synthetic."""

from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest
from fixture_models_phase5 import (
    _sample_members,
    load,
    reference_archive,
)

from hausbuch.rules import bank_sync as rules
from hausbuch.rules.errors import RuleError
from hausbuch.schemas.bank_sync import SyncEvent, SyncStatus, SyncTransition, SyncTrigger
from hausbuch.schemas.enums import BalanceSource
from hausbuch.services.bank.camt import read_camt

S, V, T = SyncStatus, SyncEvent, SyncTrigger

# --- sync_window.yaml ----------------------------------------------------------------------


@pytest.mark.parametrize("case", load("sync_window").cases, ids=lambda c: c.id)
def test_request_window_matches_the_fixture(case):
    if case.expect.error is not None:
        with pytest.raises(RuleError) as raised:
            rules.request_window(case.facts, case.today)
        assert raised.value.code == case.expect.error.code
    else:
        assert rules.request_window(case.facts, case.today) == case.expect.window


# --- tan_flow.yaml -------------------------------------------------------------------------


@pytest.mark.parametrize("case", load("tan_flow").cases, ids=lambda c: c.id)
def test_next_state_replays_the_fixture(case):
    status = case.start
    for step in case.steps:
        if step.error is not None:
            with pytest.raises(RuleError) as raised:
                rules.next_state(status, step.event, case.trigger)
            assert raised.value.code == step.error.code
            continue
        assert rules.next_state(status, step.event, case.trigger) == step.expect
        status = step.expect.status


@pytest.mark.parametrize("trigger", list(T))
def test_a_lost_worker_restarts_a_queued_or_running_row(trigger):
    for status in (S.QUEUED, S.RUNNING):
        assert rules.next_state(status, V.WORKER_LOST, trigger) == SyncTransition(status=S.QUEUED)


def test_an_unreachable_bank_queues_the_run_again():
    assert rules.next_state(S.RUNNING, V.RETRY, T.MANUAL) == SyncTransition(status=S.QUEUED)


@pytest.mark.parametrize("status", sorted(rules.LIVE_SYNC_STATUSES))
def test_an_error_fails_every_live_row(status):
    assert rules.next_state(status, V.ERROR, T.MANUAL).status is S.FAILED


@pytest.mark.parametrize(
    ("status", "event"),
    [
        (S.QUEUED, V.TAN_DEMANDED),
        (S.QUEUED, V.NEW_LINES),
        (S.IMPORTING, V.WORKER_LOST),
        (S.NEEDS_APPROVAL, V.RETRY),
        *[(final, event) for final in (S.DONE, S.NOTHING_NEW, S.FAILED) for event in V],
    ],
)
def test_undefined_pairs_raise(status, event):
    with pytest.raises(ValueError):
        rules.next_state(status, event, T.MANUAL)


# --- sync_archive.yaml ---------------------------------------------------------------------


def _streams(case) -> list[bytes]:
    out: list[bytes] = []
    for stream in case.streams:
        data = _sample_members()[stream.sample] if stream.sample else b"x" * stream.bytes
        out.extend([data] * stream.count)
    return out


@pytest.mark.parametrize("case", load("sync_archive").cases, ids=lambda c: c.id)
def test_build_archive_matches_the_fixture(case):
    streams = _streams(case)
    if case.expect.error is not None:
        with pytest.raises(RuleError) as raised:
            rules.build_archive(streams)
        assert raised.value.code == case.expect.error.code
        return
    built = rules.build_archive(streams)
    assert built == reference_archive(streams)
    assert built == rules.build_archive(list(streams))
    result = read_camt(built)
    assert result.balance_error is None
    assert len(result.statement.lines) == case.expect.lines


# --- sync_balance.yaml ---------------------------------------------------------------------


@pytest.mark.parametrize("case", load("sync_balance").cases, ids=lambda c: c.id)
def test_balance_rules_match_the_fixture(case):
    if case.expect.lines_new == 0:
        assert rules.balance_drift(case.hksal, case.last_reconciled_closing) == case.expect.drift
    else:
        declared = rules.declared_closing(case.file_closing, case.hksal)
        assert declared == (case.expect.declared_closing, case.expect.balance_source)


def test_no_balance_from_file_or_bank_declares_nothing():
    assert rules.declared_closing(None, None) is None
    assert rules.declared_closing(None, Decimal("1.00")) == (Decimal("1.00"), BalanceSource.BANK)


# --- Helpers -------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("iban", "blz"),
    [
        ("DE50100000000000000001", "10000000"),
        ("de50 1000 0000 0000 0000 01", "10000000"),
        ("AT611904300234573201", None),
        ("DE5010000000", None),
        (None, None),
        ("", None),
    ],
)
def test_blz_from_iban(iban, blz):
    assert rules.blz_from_iban(iban) == blz


@pytest.mark.parametrize(
    ("code", "kept"),
    [
        ("9800", "9800"),
        (9010, "9010"),
        ("3955", None),
        ("98000", None),
        ("98a0", None),
        (None, None),
    ],
)
def test_refused_code_keeps_four_digits_starting_9_only(code, kept):
    assert rules.refused_code(code) == kept


@pytest.mark.parametrize(
    ("value", "usable"),
    [
        ("A" * 25, True),
        ("A" * 26, False),
        ("", False),
        (" ", False),
        (" " + "A" * 10, False),
        (None, False),
        ("X1", True),
    ],
)
def test_product_id_usable(value, usable):
    assert rules.product_id_usable(value) is usable


def test_is_stale_after_the_approval_wait_plus_grace():
    now = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
    edge = now - timedelta(seconds=300 + rules.STALE_GRACE_S)
    assert not rules.is_stale(edge, now, 300)
    assert rules.is_stale(edge - timedelta(seconds=1), now, 300)


def test_window_starts_after_the_anchor_even_with_an_old_last_booking():
    from hausbuch.schemas.bank_sync import SyncWindowFacts

    facts = SyncWindowFacts(opening_balance_on=date(2026, 10, 1), last_booking_on=date(2026, 10, 5))
    assert rules.request_window(facts, date(2026, 11, 11)).start == date(2026, 10, 2)


@pytest.mark.parametrize(
    ("codes", "deciding"),
    [
        (["9050", "9942"], "9942"),  # the message-level code first, the reason after it
        (["9800", "9050", "9931", "9010"], "9931"),
        (["9050"], "9050"),  # nothing more specific: the general code is still shown
        (["9942"], "9942"),
        ([], None),
    ],
)
def test_the_deciding_code_skips_the_general_ones(codes, deciding):
    assert rules.deciding_code(codes) == deciding
