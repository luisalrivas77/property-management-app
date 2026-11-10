"""Compare a phase 4b rule's result with a fixture expectation, by the rules in the docstring of
tests/fixture_models_phase4b.py: amounts exactly; warnings as a multiset of codes, the params an
expectation names must match, others are free; an `error` names the RuleError code."""

from collections import Counter

import pytest

from hausbuch.rules.errors import RuleError


def warnings(actual, expected, where: str = "") -> None:
    got = Counter(str(w.code.value) for w in actual)
    want = Counter(str(w.code.value) for w in expected)
    assert got == want, f"{where}: warnings {sorted(got.elements())} != {sorted(want.elements())}"
    for w in expected:
        assert any(
            a.code == w.code and all(a.params.get(k) == v for k, v in w.params.items())
            for a in actual
        ), f"{where}: no {w.code.value} with params {w.params}"


def refused(code: str, run) -> None:
    with pytest.raises(RuleError) as info:
        run()
    assert info.value.code == code
