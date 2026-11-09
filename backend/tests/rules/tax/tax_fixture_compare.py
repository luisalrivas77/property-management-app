"""Compare a phase 3 rule's result with a fixture expectation, by the rules in the docstring
of tests/fixture_models_phase3.py:

- lines: exactly the lines with a non-zero amount, a ticked box or the provisional flag, in
  any order; a line not listed is absent or 0.00. unit_rows the same, keyed by (wohnung, id).
- sources: the exact set of (kind, id); None = not asserted.
- warnings: the exact multiset of codes; listed params must match, others are free.
- lists of ids compare as sets.
"""

from collections import Counter


def refs(sources) -> set:
    return {(s.kind, s.id) for s in sources}


def _visible(line) -> bool:
    return bool(line.ticked) or line.provisional or (line.amount is not None and line.amount != 0)


def lines(actual, expected, where: str = "") -> None:
    by_key = {}
    for line in actual:
        assert line.key not in by_key, f"{where}: line {line.key} written twice"
        by_key[line.key] = line
    wanted = {e.key for e in expected}
    for e in expected:
        assert e.key in by_key, f"{where}: line {e.key} missing; have {sorted(by_key)}"
        a = by_key[e.key]
        if e.ticked is not None:
            assert a.ticked == e.ticked, f"{where}: {e.key} ticked {a.ticked}"
        else:
            assert a.amount == e.amount, f"{where}: {e.key} amount {a.amount} != {e.amount}"
        assert a.provisional == e.provisional, f"{where}: {e.key} provisional {a.provisional}"
        if e.einzelangaben is not None:
            assert a.einzelangaben == e.einzelangaben, f"{where}: {e.key} einzelangaben"
        if e.amount_rounded is not None:
            assert a.amount_rounded == e.amount_rounded, (
                f"{where}: {e.key} rounded {a.amount_rounded} != {e.amount_rounded}"
            )
        if e.sources is not None:
            got, want = refs(a.sources), refs(e.sources)
            assert got == want, (
                f"{where}: {e.key} sources: extra {got - want}, missing {want - got}"
            )
    for key, a in by_key.items():
        if key not in wanted:
            assert not _visible(a), f"{where}: unexpected line {key} = {a.amount}"


def unit_rows(actual, expected, where: str = "") -> None:
    by_key = {(r.wohnung, r.id): r for r in actual}
    assert len(by_key) == len(actual), f"{where}: a unit row written twice"
    for e in expected:
        a = by_key.get((e.wohnung, e.id))
        assert a is not None, f"{where}: unit row {e.wohnung} {e.id} missing"
        for field in ("label", "area_m2", "area_source", "amount", "pre_handover"):
            assert getattr(a, field) == getattr(e, field), (
                f"{where}: unit row {field} {getattr(a, field)} != {getattr(e, field)}"
            )
        if e.rooms is not None:
            assert [(r.unit_id, r.label, r.amount) for r in a.rooms] == [
                (r.unit_id, r.label, r.amount) for r in e.rooms
            ], f"{where}: rooms {a.rooms}"
        if e.sources is not None:
            got, want = refs(a.sources), refs(e.sources)
            assert got == want, (
                f"{where}: unit row sources: extra {got - want}, missing {want - got}"
            )
    wanted = {(e.wohnung, e.id) for e in expected}
    for key, a in by_key.items():
        if key not in wanted:
            assert a.amount == 0, f"{where}: unexpected unit row {key} = {a.amount}"


def warnings(actual, expected, where: str = "") -> None:
    codes_a = Counter(str(w.code) for w in actual)
    codes_e = Counter(str(w.code) for w in expected)
    assert codes_a == codes_e, f"{where}: warnings {dict(codes_a)} != {dict(codes_e)}"
    free = list(actual)
    for e in sorted(expected, key=lambda w: -len(w.params)):
        match = next(
            (
                a
                for a in free
                if a.code == e.code and all(a.params.get(k) == v for k, v in e.params.items())
            ),
            None,
        )
        assert match is not None, (
            f"{where}: no {e.code} with params {e.params}; have "
            f"{[a.params for a in actual if a.code == e.code]}"
        )
        free.remove(match)


def tracked(actual, expected, where: str = "") -> None:
    got = sorted((str(t.category), t.amount, t.count) for t in actual)
    want = sorted((str(t.category), t.amount, t.count) for t in expected)
    assert got == want, f"{where}: tracked {got} != {want}"


def basis_additions(actual, expected, where: str = "") -> None:
    def key(b):
        return (str(b.allocation_id), b.tax_year, b.amount, b.reason)

    got, want = sorted(map(key, actual)), sorted(map(key, expected))
    assert got == want, f"{where}: basis additions {got} != {want}"


def rule_lines(result, expect, where: str = "") -> None:
    """A line rule's RuleLines against a RuleLinesExpect."""
    assert result.tax_year == expect.tax_year
    lines(result.lines, expect.lines, where)
    unit_rows(result.unit_rows, expect.unit_rows, where)
    tracked(result.tracked, expect.tracked, where)
    basis_additions(result.basis_additions, expect.basis_additions, where)
    got = {r.id for r in result.pre_handover}
    assert got == set(expect.pre_handover), f"{where}: pre_handover {got}"
    assert result.excluded_open == expect.excluded_open, (
        f"{where}: excluded_open {result.excluded_open}"
    )
    warnings(result.warnings, expect.warnings, where)
