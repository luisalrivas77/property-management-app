"""Phase 6 step 4: the pre-fill records of a depot reading (docs/phases/phase-6.md D165 (2),
(3), D132): one `movement:<n>` per movement and one `holding:<n>` per position, each with the
depot account to book it on. All ISINs, names and figures are invented."""

from uuid import UUID

import pytest

from hausbuch.rules.prefill import build_proposal
from hausbuch.schemas.ai import ClassifyOutput, ExtractedValue, SourceRef
from hausbuch.schemas.enums import DocumentSubtype, DocumentType, ExtractionKind
from hausbuch.schemas.extraction import (
    DepotReportExtraction,
    DistributionNoteExtraction,
    SecuritiesTransferExtraction,
    TradeConfirmationExtraction,
)
from hausbuch.schemas.proposal import ExistingDepotAccount, PrefillContext, ProposalRecord

DOC = UUID("00000000-0000-0000-0000-00000000d0c1")
DEPOT_A = ExistingDepotAccount(
    id=UUID("00000000-0000-0000-0000-0000000000d1"), label="Depot Beispiel (****1234)"
)
DEPOT_B = ExistingDepotAccount(
    id=UUID("00000000-0000-0000-0000-0000000000d2"), label="Verrechnungskonto (****5678)"
)
ISIN = "IE00BEISPL01"


def _v(value, quote: str | None = None) -> ExtractedValue:
    return ExtractedValue(
        value=value,
        source=SourceRef(page=1, quote=quote or str(value)),
        checks=["cited", "value_in_quote", "validates"],
        confidence="high",
    )


def _context(subtype: DocumentSubtype, accounts=(DEPOT_A,)) -> PrefillContext:
    classification = ClassifyOutput(
        type=DocumentType.LETTER,
        subtype=subtype,
        contains_tenant_data=False,
        language="de",
        confidence="high",
    )
    return PrefillContext(
        document_id=DOC, classification=classification, depot_accounts=list(accounts)
    )


def _records(kind, extraction, subtype, **context) -> dict[str, ProposalRecord]:
    records = build_proposal(kind, extraction, _context(subtype, **context))
    return {r.key: r for r in records if r.key != "typing"}


def _trade(**overrides) -> TradeConfirmationExtraction:
    values = {
        "isin": _v(ISIN),
        "security_name": _v("Beispiel Welt ETF"),
        "trade_date": _v("2026-05-14", "14.05.2026"),
        "side": _v("Kauf"),
        "units": _v("2.5000", "2,5000"),
        "gross_amount": _v("250.00", "250,00"),
        "fees": _v("0.00", "0,00"),
        "taxes": _v("0.00", "0,00"),
        "net_amount": _v("250.00", "250,00"),
        "order_reference": _v("REF0001"),
    }
    return TradeConfirmationExtraction(**(values | overrides))


def _trade_records(**context):
    return _records(
        ExtractionKind.TRADE_CONFIRMATION,
        _trade(),
        DocumentSubtype.TRADE_CONFIRMATION,
        **context,
    )


def test_a_trade_proposes_one_movement_on_the_only_depot():
    records = _trade_records()
    assert list(records) == ["movement:1"]
    movement = records["movement:1"]
    assert movement.entity_type == "asset_movement" and movement.mode == "create"
    fields = {name: value.value for name, value in movement.fields.items()}
    assert fields == {
        "account_id": str(DEPOT_A.id),
        "isin": ISIN,
        "name": "Beispiel Welt ETF",
        "kind": "acquisition",
        "quantity": "2.5000",
        "eur_amount": "-250.00",
        "fee_eur": "0.00",
        "tax_withheld_eur": "0.00",
        "occurred_on": "2026-05-14",
        "external_ref": "REF0001",
    }
    # The units are the reading's own value with its quote; the signed amount is code's.
    assert movement.fields["quantity"].source is not None
    assert movement.fields["eur_amount"].derived_from == ["net_amount", "side"]
    assert movement.fields["account_id"].confidence == "low"
    assert [o.value for o in movement.options["account_id"]] == [str(DEPOT_A.id)]
    assert movement.notes == []


def test_with_two_depots_the_account_is_chosen_by_the_user():
    movement = _trade_records(accounts=(DEPOT_A, DEPOT_B))["movement:1"]
    assert movement.fields["account_id"].value is None
    assert [o.label for o in movement.options["account_id"]] == [DEPOT_A.label, DEPOT_B.label]


def test_without_a_depot_the_card_says_to_create_one():
    movement = _trade_records(accounts=())["movement:1"]
    assert movement.fields["account_id"].value is None
    assert movement.options == {}
    assert any("Depot" in note for note in movement.notes)


def test_a_sale_is_a_disposal_with_negative_units():
    extraction = _trade(side=_v("Verkauf"), net_amount=_v("250.00"))
    movement = _records(
        ExtractionKind.TRADE_CONFIRMATION, extraction, DocumentSubtype.TRADE_CONFIRMATION
    )["movement:1"]
    assert movement.fields["kind"].value == "disposal"
    assert movement.fields["quantity"].value == "-2.5000"
    assert movement.fields["eur_amount"].value == "250.00"


def test_a_trade_that_does_not_add_up_carries_a_note():
    extraction = _trade(net_amount=_v("251.00"))
    movement = _records(
        ExtractionKind.TRADE_CONFIRMATION, extraction, DocumentSubtype.TRADE_CONFIRMATION
    )["movement:1"]
    assert len(movement.notes) == 1 and "Endbetrag" in movement.notes[0]


def test_a_distribution_moves_no_units_and_keeps_the_fund_name():
    extraction = DistributionNoteExtraction(
        isin=_v(ISIN),
        security_name=_v("Beispiel Anleihen ETF"),
        payment_date=_v("2026-06-25", "25.06.2026"),
        units=_v("10.0000"),
        gross_amount_eur=_v("3.00", "3,00"),
        german_taxes_eur=_v("0.79", "0,79"),
        net_amount_eur=_v("2.21", "2,21"),
    )
    movement = _records(
        ExtractionKind.DISTRIBUTION_NOTE, extraction, DocumentSubtype.DISTRIBUTION_NOTE
    )["movement:1"]
    fields = {name: value.value for name, value in movement.fields.items()}
    assert fields["kind"] == "distribution"
    assert fields["quantity"] == "0"
    assert fields["name"] == "Beispiel Anleihen ETF"
    assert fields["eur_amount"] == "3.00"
    assert fields["tax_withheld_eur"] == "0.79"
    assert movement.fields["tax_withheld_eur"].derived_from == [
        "gross_amount_eur",
        "net_amount_eur",
    ]


def test_a_transfer_proposes_one_movement_per_security():
    extraction = SecuritiesTransferExtraction(
        direction=_v("Ausbuchung"),
        transfer_date=_v("2026-03-02", "02.03.2026"),
        reference=_v("UMB0001"),
        positions=_v([f"{ISIN} | Beispiel Welt ETF | 3,0000", "IE00BEISPL02 | Zweiter | 1,5000"]),
    )
    records = _records(
        ExtractionKind.SECURITIES_TRANSFER, extraction, DocumentSubtype.SECURITIES_TRANSFER
    )
    assert list(records) == ["movement:1", "movement:2"]
    first = records["movement:1"].fields
    assert first["kind"].value == "internal_transfer_out"
    assert first["quantity"].value == "-3.0000"
    assert first["external_ref"].value == "UMB0001"
    assert records["movement:2"].fields["isin"].value == "IE00BEISPL02"


def test_a_report_proposes_one_holding_per_position():
    extraction = DepotReportExtraction(
        report_date=_v("2026-06-30", "30.06.2026"),
        positions=_v(
            [
                f"{ISIN} | Beispiel Welt ETF | 2,5000 | 101,00 | 252,50 | EUR",
                "IE00BEISPL02 | Zweiter | 1,0000 | 10,00 | 10,00 | EUR",
            ]
        ),
        trades=_v([]),
    )
    records = _records(ExtractionKind.DEPOT_REPORT, extraction, DocumentSubtype.DEPOT_REPORT)
    assert list(records) == ["holding:1", "holding:2"]
    holding = records["holding:1"]
    assert holding.entity_type == "holding_statement"
    fields = {name: value.value for name, value in holding.fields.items()}
    assert fields == {
        "account_id": str(DEPOT_A.id),
        "isin": ISIN,
        "name": "Beispiel Welt ETF",
        "as_of": "2026-06-30",
        "declared_units": "2.5000",
        "declared_value_eur": "252.50",
    }
    assert holding.fields["declared_units"].derived_from == ["positions"]


@pytest.mark.parametrize(
    "kind, extraction, subtype",
    [
        (
            ExtractionKind.TRADE_CONFIRMATION,
            TradeConfirmationExtraction(),
            DocumentSubtype.TRADE_CONFIRMATION,
        ),
        (ExtractionKind.DEPOT_REPORT, DepotReportExtraction(), DocumentSubtype.DEPOT_REPORT),
    ],
)
def test_an_empty_reading_proposes_only_its_typing(kind, extraction, subtype):
    assert _records(kind, extraction, subtype) == {}
