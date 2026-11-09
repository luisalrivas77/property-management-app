"""§5.11 the assembled draft against the hand-calculated Anlage V 2026 (anlage_v_2026.yaml,
docs/phases/phase-3.md §14). Fields a case leaves out (None) are not asserted."""

import pytest
import tax_fixture_compare as cmp
from fixture_models_phase3 import DraftFixture, load

from hausbuch.rules.tax.draft import draft
from hausbuch.rules.tax.field_sheet import field_sheet
from hausbuch.rules.tax.registry import rule_set
from hausbuch.schemas.checklist import RequirementCode
from hausbuch.schemas.tax import AnlageVDraft

DRAFT = load("anlage_v_2026")
assert isinstance(DRAFT, DraftFixture)
# The checklist entries phase 3 step 2 adds to rules/document_checklist.py (§8).
PHASE3_CODES = {RequirementCode.COST_RECEIPT, RequirementCode.MARKET_RENT}


def _run(case) -> AnlageVDraft:
    return draft(case.facts, case.tax_year, rule_set(case.rule_set_year))


@pytest.mark.rule
@pytest.mark.parametrize("case", DRAFT.cases, ids=lambda c: c.id)
def test_draft(case):
    got = _run(case)
    e = case.expect
    where = case.id
    assert got.tax_year == case.tax_year and got.property_id == case.facts.property.id
    if e.rule_set is not None:
        assert got.rule_set == e.rule_set
    if e.header is not None:
        assert got.header == e.header
    cmp.lines(got.lines, e.lines, where)
    if e.unit_rows is not None:
        cmp.unit_rows(got.unit_rows, e.unit_rows, where)
    assert got.totals == e.totals
    assert [(o.owner_id, o.amount, o.amount_rounded) for o in got.owners] == [
        (o.owner_id, o.amount, o.amount_rounded) for o in e.owners
    ]
    if e.afa is not None:
        a = got.afa
        assert (a.amount, a.basis, a.months, a.method_code) == (
            e.afa.amount,
            e.afa.basis,
            e.afa.months,
            e.afa.method_code,
        )
    if e.tracked_not_deductible is not None:
        cmp.tracked(got.tracked_not_deductible, e.tracked_not_deductible, where)
    if e.moved_to_other_year is not None:
        key = lambda m: str(m.allocation_id)  # noqa: E731
        assert sorted(got.moved_to_other_year, key=key) == sorted(e.moved_to_other_year, key=key)
    if e.pre_handover is not None:
        assert {r.id for r in got.pre_handover} == set(e.pre_handover)
    if e.excluded_open is not None:
        assert got.excluded_open == e.excluded_open
    if e.warnings is not None:
        cmp.warnings(got.warnings, e.warnings, where)
    if e.limit_15 is not None:
        lim, want = got.limit_15, e.limit_15
        for field in (
            "basis_building_cost",
            "limit",
            "window_start",
            "window_end",
            "used_net",
            "ratio_percent",
            "exceeded",
        ):
            assert getattr(lim, field) == getattr(want, field), f"limit_15.{field}"
        assert {c.allocation_id for c in lim.counted} == set(want.counted)
    if e.reduced_rent is not None:
        assert [(c.id, c.agreed_warm, c.area_m2, c.outcome) for c in got.reduced_rent.checks] == [
            (r.id, r.agreed_warm, r.area_m2, r.outcome) for r in e.reduced_rent
        ]
    if e.coverage is not None:
        assert got.coverage == e.coverage
    if e.tax_free_from is not None:
        assert got.tax_free_from == e.tax_free_from
    if e.roundtrip:
        assert AnlageVDraft.model_validate_json(got.model_dump_json()) == got
    if e.field_sheet is not None:
        sheet = field_sheet(got, rule_set(case.rule_set_year))
        assert [r.model_dump() for r in sheet] == [r.model_dump() for r in e.field_sheet]


@pytest.mark.rule
@pytest.mark.parametrize(
    "case", [c for c in DRAFT.cases if c.expect.missing_documents is not None], ids=lambda c: c.id
)
def test_draft_missing_documents(case):
    got = _run(case).missing_documents
    want = case.expect.missing_documents
    if any(item.code in PHASE3_CODES for item in got):
        assert got == want
        return
    # The phase 3 checklist entries are another step's work: the phase 1 items must already
    # match; the rest is checked once build_checklist has them.
    assert got == [item for item in want if item.code not in PHASE3_CODES]
    pytest.skip("document_checklist has no cost_receipt/market_rent entries yet (§8, step 2)")
