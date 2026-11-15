"""Phase 7 step 3: the block window a chain sync reads and the overlap's deduplication
(docs/phases/phase-7.md D182 (1), (4)). Every case of sync_window_chain.yaml is replayed: the
function is one for EVM and Bitcoin, so the Bitcoin case (still marked for step 4, which uses
it) runs here too."""

import pytest
from fixture_models_phase7 import load

from hausbuch.rules.chain.sync_window import new_hashes, window
from hausbuch.schemas.chain import Chain

CASES = load("sync_window_chain").cases


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_window(case):
    got = window(case.chain, case.cursor, case.tip)
    if case.expect is None:
        assert got is None
    else:
        assert got == (case.expect.from_block, case.expect.to_block)
    assert new_hashes(case.read_hashes, case.seen_hashes) == case.expect_new


def test_solana_has_no_window():
    with pytest.raises(ValueError):
        window(Chain.SOLANA, None, 100)


def test_a_cursor_inside_the_overlap_starts_at_genesis():
    assert window(Chain.ETHEREUM, 5, 100) == (0, 88)


def test_new_hashes_once_each():
    assert new_hashes(["a", "b", "a", "c"], {"b"}) == ["a", "c"]
