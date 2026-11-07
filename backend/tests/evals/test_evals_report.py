"""`hausbuch evals report` without a database: the counting, the money/date classification
from the record models' types, the table, and that a connection error never prints the URL.
The database path is tests/db/test_cli_evals_report.py."""

import uuid
from decimal import Decimal

import pytest

from hausbuch import cli
from hausbuch.evals.report import (
    UNKNOWN_KIND,
    Tally,
    aggregate,
    format_report,
    value_class,
)
from hausbuch.schemas.enums import ExtractionKind
from hausbuch.schemas.events import AppliedRecord, ProposalApplied
from hausbuch.schemas.proposal import FieldDecision

A, E, C, S = (
    FieldDecision.ACCEPTED,
    FieldDecision.EDITED,
    FieldDecision.CLEARED,
    FieldDecision.SKIPPED,
)


def _applied(kind, *records: tuple[str, str, dict]) -> ProposalApplied:
    return ProposalApplied(
        document_id=uuid.uuid4(),
        kind=kind,
        records=[
            AppliedRecord(
                key=key,
                mode="create",
                skipped=all(d == S for d in decisions.values()),
                entity_type=entity,
                entity_id=None if all(d == S for d in decisions.values()) else uuid.uuid4(),
                decisions=decisions,
            )
            for key, entity, decisions in records
        ],
    )


@pytest.mark.parametrize(
    ("entity", "name", "expected"),
    [
        ("property", "purchase_price", "money"),  # PositiveMoney
        ("property", "notarized_on", "date"),
        ("property", "address.city", None),  # nested model, text
        ("property", "build_year", None),  # a year is an int, not a date
        ("loan", "principal", "money"),
        ("loan", "nominal_rate", None),  # a Rate is a Decimal but not money
        ("loan", "fixed_until", "date"),  # date | None
        ("acquisition_cost", "amount", "money"),
        ("rent_schedule", "cold_rent", "money"),
        ("tenancy", "deposit_amount", "money"),
        ("unit", "floor_area_m2", None),  # Area: two decimals like money, not money
        ("price_split", "building_share", None),
        ("typing", "document_date", "date"),
        ("typing", "tax_year", None),
        ("link", "anything", None),
        ("property", "no_such_field", None),
    ],
)
def test_money_and_dates_come_from_the_record_models_types(entity, name, expected):
    assert value_class(entity, name) == expected


def test_aggregate_counts_per_kind_record_and_field():
    report = aggregate(
        [
            _applied(
                ExtractionKind.PURCHASE_CONTRACT,
                ("property", "property", {"purchase_price": A, "notarized_on": E, "kind": A}),
                ("unit", "unit", {"label": S, "mea": S}),
            ),
            _applied(
                ExtractionKind.PURCHASE_CONTRACT,
                ("property", "property", {"purchase_price": A, "notarized_on": A, "kind": C}),
            ),
            _applied(None, ("typing", "typing", {"document_date": S})),
        ]
    )
    assert report.proposals == 3
    price = report.rows[("purchase_contract", "property", "purchase_price")]
    assert (price.proposed, price.accepted, price.share()) == (2, 2, Decimal(1))
    notarized = report.rows[("purchase_contract", "property", "notarized_on")]
    assert (notarized.accepted, notarized.edited, notarized.share()) == (1, 1, Decimal("0.5"))
    assert report.rows[("purchase_contract", "unit", "label")].skipped == 1
    assert (UNKNOWN_KIND, "typing", "document_date") in report.rows
    # money + dates: purchase_price x2 (A, A), notarized_on x2 (E, A), document_date (S)
    md = report.money_and_dates
    assert (md.accepted, md.edited, md.skipped, md.proposed) == (3, 1, 1, 5)
    assert md.share() == Decimal("0.6")
    assert md.share(without_skipped=True) == Decimal("0.75")


def test_tally_without_decisions_has_no_share():
    assert Tally().share() is None


def test_the_table_and_the_target_line():
    report = aggregate(
        [
            _applied(
                ExtractionKind.INVOICE,
                ("cost", "acquisition_cost", {"amount": A}),
                ("typing", "typing", {"document_date": A, "title": E}),
            )
        ]
        * 9
        + [_applied(ExtractionKind.INVOICE, ("cost", "acquisition_cost", {"amount": E}))]
    )
    out = format_report(report, workspaces=1, since=None)
    lines = out.splitlines()
    assert lines[0] == "Pre-fill accuracy: 10 applied proposals (1 workspace)"
    header = lines[2].split()
    assert header[:8] == [
        "kind",
        "record",
        "field",
        "proposed",
        "accepted",
        "edited",
        "cleared",
    ] + ["skipped"]
    amount = next(line for line in lines if " amount " in line).split()
    assert amount == [
        "invoice",
        "acquisition_cost",
        "amount",
        "10",
        "9",
        "1",
        "0",
        "0",
        "90.0",
        "%",
    ]
    title = next(line for line in lines if " title " in line).split()
    assert title[-2:] == ["0.0", "%"]
    # money + dates: amount 9 of 10, document_date 9 of 9 -> 18 of 19
    assert lines[-1].startswith(
        "Money and date fields: 18 of 19 accepted = 94.7 % (target 90 %: met)"
    )


def test_an_empty_report_says_so():
    assert "No applied proposals yet." in format_report(aggregate([]), workspaces=0, since=None)


# --- the CLI never prints the URL --------------------------------------------------------

SECRET = "pw-Very-Secret-1234"


@pytest.mark.parametrize(
    "url",
    [
        # Nothing listens on port 1: the connect fails (OperationalError).
        f"postgresql+psycopg://hausbuch_owner:{SECRET}@127.0.0.1:1/hausbuch",
        # Not a URL at all: SQLAlchemy's own error would quote it.
        f"not a url {SECRET}",
    ],
)
def test_a_connection_error_never_prints_the_url(url, capsys):
    code = cli.main(["evals", "report"], environ={"HAUSBUCH_ADMIN_DATABASE_URL": url})
    out, err = capsys.readouterr()
    assert code == cli.EXIT_ERROR
    assert SECRET not in out + err
    assert "127.0.0.1" not in out + err
    assert err.startswith("hausbuch evals report: ")


def test_the_report_needs_the_owner_url(capsys):
    assert cli.main(["evals", "report"], environ={}) == cli.EXIT_ERROR
    assert "HAUSBUCH_ADMIN_DATABASE_URL" in capsys.readouterr().err
