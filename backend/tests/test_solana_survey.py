"""`hausbuch survey-solana`'s service (services/chain/survey.py) over the invented fake chain
of the Solana source tests: the listing it reads (paging, open and closed token accounts), the
cache a second run answers from without a request, the throttling hook, the retries, and that
neither the report nor the command's output names the address, a signature, a token account
or the RPC URL. Every key and signature is invented."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import SecretStr
from solana_chain_fake import FakeChain, Records, signature, token_account

from hausbuch import cli
from hausbuch.rules.chain import survey as rules
from hausbuch.rules.chain.classify import ClassifyContext
from hausbuch.schemas.chain import Chain
from hausbuch.services.chain import survey
from hausbuch.services.chain.solana_rpc import SolanaRpc

A = "BU8oH89B6z89SV3HfU1FBa5p8aE43HhZz3F6BQBtjsJP"  # invented
T1 = "T1c1osedTokenAccountInventedA"  # an own token account closed before the survey
T2 = "T2openTokenAccountInventedAaa"  # an own token account still open
URL = "https://rpc.invented.example/?api-key=k3yInv3nt3dF0rTh1sT3st"


def _chain() -> FakeChain:
    r = Records(A)
    records = [r.sol_in(1), r.sol_out(2), r.sol_in(5), r.sol_out(8)]
    records += [r.open(3, T1), r.token_in(4, T1), r.token_out(6, T1), r.close(7, T1)]
    records += [r.open(9, T2), r.token_in(10, T2), r.token_in(11, T2, block_time=False)]
    chain = FakeChain(records)
    chain.tokens[A] = [token_account(A, T2, Records.MINT, 10, Records.RENT)]
    chain.block_times[11] = 1_770_000_011
    return chain


class Clock:
    """A clock that never moves on its own: every gap between two requests is slept."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def _run(chain: FakeChain, root: Path, *, max_tx: int | None = None, rps: float = 2.0):
    clock = Clock()
    transport = survey.CachedPost(root, chain, rps=rps, sleep=clock.sleep, clock=clock)
    rpc = SolanaRpc(url=SecretStr(URL), post=transport, sleep=lambda _: None, signature_page=2)
    ctx = ClassifyContext(chain=Chain.SOLANA, address=A, own_addresses=frozenset({A}))
    result = survey.run_survey(
        A,
        rpc=rpc,
        transport=transport,
        ctx=ctx,
        sync_budget=2400,
        max_tx=max_tx,
        rpc_source="an invented endpoint",
        context_label="the public registry and decoders",
    )
    return result, transport, clock


def _secrets(chain: FakeChain) -> list[str]:
    return [
        A,
        T1,
        T2,
        URL,
        "k3yInv3nt3dF0rTh1sT3st",
        *(signature(r) for r in chain.records.values()),
    ]


def test_survey_lists_the_address_and_every_token_account(tmp_path):
    chain = _chain()
    result, transport, _ = _run(chain, tmp_path)
    assert result.listing.total == len(chain.records)
    assert result.summary.observed == len(chain.records)
    listed = {params[0] for params in chain.calls("getSignaturesForAddress")}
    assert listed == {A, T1, T2}  # T1 closed: found through the records that name it
    # paged two at a time, each listing ending on an empty page
    address_pages = [p for p in chain.calls("getSignaturesForAddress") if p[0] == A]
    assert len(address_pages) > 2
    assert result.estimate.open_accounts == 1
    assert result.estimate.closed_accounts == 1
    assert result.summary.lookups == 1  # the record without blockTime
    assert len(chain.calls("getBlockTime")) == 1
    assert not result.run.not_read
    assert result.run.requests == transport.network == len(chain.requests)


def test_outcomes_follow_the_sync_routing(tmp_path):
    """The three opens and closes pay a fee alone and are booked by code; a plain transfer
    is never linked here, so it waits on a later sync too: the sends with answer buttons,
    the receipts from a stranger as dust-shaped cards."""
    result, _, _ = _run(_chain(), tmp_path)
    expected = {
        ("transfer", rules.Outcome.AUTO): 3,
        ("transfer", rules.Outcome.ANSWERS): 3,
        ("transfer", rules.Outcome.DUST_SHAPE): 5,
    }
    assert dict(result.summary.first) == expected
    assert dict(result.summary.later) == expected
    assert "Need a person: 8 of 11 groups (73 %)" in result.report


def test_a_second_run_answers_from_the_cache(tmp_path):
    chain = _chain()
    first, _, _ = _run(chain, tmp_path)
    sent = len(chain.requests)
    again, transport, clock = _run(chain, tmp_path)
    assert len(chain.requests) == sent  # no request sent again
    assert transport.network == 0
    assert clock.slept == []  # nothing to throttle
    assert again.report.split("## Requests")[0] == first.report.split("## Requests")[0]
    assert again.run.requests == first.run.requests


def test_requests_are_throttled_to_the_pace(tmp_path):
    _, transport, clock = _run(_chain(), tmp_path, rps=4.0)
    assert transport.network > 1
    assert clock.slept == [0.25] * (transport.network - 1)


def test_a_throttled_answer_is_retried_and_not_cached(tmp_path):
    chain = _chain()
    answers = iter([429, 503])

    def flaky(url, headers, body, timeout, max_bytes):
        status = next(answers, None)
        if status is not None:
            return status, b""
        return chain(url, headers, body, timeout, max_bytes)

    clock = Clock()
    transport = survey.CachedPost(tmp_path, flaky, rps=1000.0, sleep=clock.sleep, clock=clock)
    rpc = SolanaRpc(url=SecretStr(URL), post=transport, sleep=lambda _: None)
    assert isinstance(rpc.finalized_slot(), int)
    assert transport.network == 3
    assert [s for s in clock.slept if s >= 1] == [2.0, 4.0]  # the back-off
    cached = list(tmp_path.rglob("*.json"))
    assert len(cached) == 1
    assert json.loads(cached[0].read_bytes())["result"] == 11


def test_a_null_transaction_is_counted_never_cached(tmp_path):
    chain = _chain()
    gone = signature(next(iter(chain.records.values())))
    chain.missing.add(gone)
    result, _, _ = _run(chain, tmp_path)
    assert result.run.not_read == {"HistoryUnavailable": 1}
    assert result.summary.observed == len(chain.records) - 1
    assert "HistoryUnavailable 1" in result.report


def test_a_sample_reads_only_max_tx_transactions(tmp_path):
    chain = _chain()
    result, _, _ = _run(chain, tmp_path, max_tx=3)
    assert len(chain.calls("getTransaction")) == 3
    assert result.summary.observed == 3
    assert result.run.sample == 3
    assert "**This is a sample**" in result.report


def test_the_report_names_no_address_signature_token_account_or_url(tmp_path):
    chain = _chain()
    result, _, _ = _run(chain, tmp_path)
    for secret in _secrets(chain):
        assert secret not in result.report
    for path in tmp_path.rglob("*"):
        for secret in _secrets(chain):
            assert secret not in path.name


def test_the_command_prints_and_writes_no_address_or_signature(tmp_path, monkeypatch, capsys):
    chain = _chain()
    address_file = tmp_path / "address.txt"
    address_file.write_text(A + "\n")
    out_dir = tmp_path / "survey"
    monkeypatch.setattr(survey, "default_post", lambda: chain)
    environ = {"INVENTED_RPC": URL}
    argv = [
        "survey-solana",
        "--address-file",
        str(address_file),
        "--out-dir",
        str(out_dir),
        "--rpc-url-env",
        "INVENTED_RPC",
        "--rps",
        "1000",
    ]
    assert cli.main(argv, environ) == cli.EXIT_OK
    captured = capsys.readouterr()
    report = (out_dir / "report.md").read_text()
    assert "Signatures listed: " in captured.out
    assert "Signatures listed: " in report
    for secret in _secrets(chain):
        assert secret not in captured.out
        assert secret not in captured.err
        assert secret not in report
    assert len(chain.requests) > 0


def test_a_json_rpc_error_on_a_transaction_is_counted_and_the_survey_goes_on(tmp_path):
    chain = _chain()
    chain.fail_on = "getTransaction"
    result, _, _ = _run(chain, tmp_path)
    # the closed T1 is found only through a record, so its credit naming T1 alone stays unlisted
    unlisted = 1
    assert result.run.not_read == {"SolanaRpcError rpc": len(chain.records) - unlisted}
    assert result.summary.observed == 0
    assert result.listing.total == len(chain.records) - unlisted


def test_a_failing_provider_ends_the_survey_without_naming_it(tmp_path):
    chain = _chain()
    chain.status = 500
    with pytest.raises(survey.SurveyError) as caught:
        _run(chain, tmp_path)
    assert "resumes from its cache" in str(caught.value)
    for secret in _secrets(chain):
        assert secret not in str(caught.value)
    assert len(chain.requests) == survey._ATTEMPTS  # retried, then given up


def test_a_refusing_provider_ends_the_survey(tmp_path):
    chain = _chain()
    chain.status = 403
    with pytest.raises(survey.SurveyError) as caught:
        _run(chain, tmp_path)
    assert "403" in str(caught.value)
    assert len(chain.requests) == 1  # a refusal is not retried


def test_the_command_refuses_an_invalid_address(tmp_path, capsys):
    address_file = tmp_path / "address.txt"
    address_file.write_text("not-an-address\n")
    argv = ["survey-solana", "--address-file", str(address_file), "--out-dir", str(tmp_path)]
    assert cli.main(argv, {}) == cli.EXIT_ERROR
    captured = capsys.readouterr()
    assert "not-an-address" not in captured.err + captured.out


@pytest.mark.parametrize("value", ["", "ftp://x.example/"])
def test_the_command_refuses_a_bad_url_without_printing_it(tmp_path, capsys, value):
    address_file = tmp_path / "address.txt"
    address_file.write_text(A)
    argv = [
        "survey-solana",
        "--address-file",
        str(address_file),
        "--out-dir",
        str(tmp_path / "o"),
        "--rpc-url-env",
        "INVENTED_RPC",
    ]
    assert cli.main(argv, {"INVENTED_RPC": value}) == cli.EXIT_ERROR
    captured = capsys.readouterr()
    if value:
        assert value not in captured.err + captured.out
