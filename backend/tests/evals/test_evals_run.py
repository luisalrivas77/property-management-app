"""`hausbuch evals run` with the model client replaced by the scripted fake (no network, no
key needed): the comparison against a golden file, the output with and without values,
--write-expected, and the refusals (a directory inside the repo, no key).

Every model answer here is synthetic (hausbuch.ai.fake), never API output."""

import json
from pathlib import Path

import pytest
import yaml
from make_docs import build_pdf

from hausbuch import cli
from hausbuch.ai import documents
from hausbuch.ai.fake import FakeModelClient, cited_response, text_response
from hausbuch.evals import run as evals_run
from hausbuch.evals.reading import classify_request_for, eval_settings, extract_requests_for
from hausbuch.rules.extraction_parse import PropertyAddress
from hausbuch.schemas.enums import ExtractionKind

KEY = "sk-ant-api03-SECRET-evals-run-test"
PDF = build_pdf([["Kostenrechnung Notar Dr. Beispiel", "Gesamtbetrag 1.309,00 EUR"]])

CLASSIFY = json.dumps(
    {
        "type": "invoice",
        "subtype": None,
        "contains_tenant_data": False,
        "language": "de",
        "confidence": "high",
    }
)
QUOTE = "Kostenrechnung Notar Dr. Beispiel"
EXTRACT = {
    "title": ("Notarrechnung Kaufvertrag", 1, QUOTE),
    "sender": ("Notar Dr. Beispiel", 1, QUOTE),
    "amount_total": ("1.309,00 EUR", 1, "Gesamtbetrag 1.309,00 EUR"),
    "document_date": ("12.02.2026", 1, "12.02.2026"),
    "due_on": "unbekannt",
}
GOLDEN = {
    "kind": "invoice",
    "fields": {
        "title": "Notarrechnung Kaufvertrag",  # match
        "sender": "Notar Dr. Max Beispiel",  # differs
        "amount_total": 1309.00,  # match: YAML loads a number, compared as an amount
        "amount_net": "1100.00",  # missing: expected, not read
        "due_on": None,  # empty in both
        # document_date not in the golden file but read: extra
    },
}


def _client() -> FakeModelClient:
    return FakeModelClient([text_response(CLASSIFY), cited_response(EXTRACT)])


@pytest.fixture
def evals_dir(tmp_path) -> Path:
    directory = tmp_path / "evals"
    directory.mkdir()
    (directory / "rechnung.pdf").write_bytes(PDF)
    (directory / "notes.txt").write_text("not a document")
    return directory


@pytest.fixture
def fake(monkeypatch):
    clients: list[FakeModelClient] = []
    keys: list[str] = []

    def factory(key, settings):
        keys.append(key)
        clients.append(_client())
        return clients[-1]

    monkeypatch.setattr(evals_run, "make_client", factory)
    return clients, keys


def _run(evals_dir, *extra, env=None) -> int:
    environ = {"HAUSBUCH_ANTHROPIC_API_KEY": KEY} if env is None else env
    return cli.main(["evals", "run", "--dir", str(evals_dir), *extra], environ=environ)


def _write_golden(evals_dir, golden=GOLDEN) -> None:
    (evals_dir / "rechnung.expected.yaml").write_text(yaml.safe_dump(golden, sort_keys=False))


def test_fields_are_compared_without_values(evals_dir, fake, capsys):
    _write_golden(evals_dir)
    assert _run(evals_dir) == 1  # not everything matches
    out, err = capsys.readouterr()
    assert err == ""
    lines = out.splitlines()
    assert "rechnung.pdf: kind invoice" in lines
    states = {line.split()[1]: line.split()[0] for line in lines if line.startswith("  ")}
    assert states["title"] == "match"
    assert states["amount_total"] == "match"
    assert states["sender"] == "differs"
    assert states["amount_net"] == "missing"
    assert states["document_date"] == "extra"
    assert "due_on" not in states  # empty in both: counted, not listed
    assert "  -> 2 match, 1 differs, 1 missing, 1 extra; 4 empty in both" in lines
    assert "  invoice: 1 document: 2 match, 1 differs, 1 missing, 1 extra (40.0 % match)" in lines
    # No value of the document or the golden file, and never the key.
    for value in ("Beispiel", "1309", "1100", "2026-02-12", "Notarrechnung", KEY):
        assert value not in out, value


def test_the_same_requests_as_the_job(evals_dir, fake):
    _write_golden(evals_dir)
    _run(evals_dir)
    (client,), (key,) = fake
    assert key == KEY
    settings = eval_settings()
    expected = [classify_request_for(settings, PDF, documents.PDF)] + [
        request
        for _, request in extract_requests_for(settings, ExtractionKind.INVOICE, PDF, documents.PDF)
    ]
    assert [r.sha256() for r in client.requests] == [r.sha256() for r in expected]


def test_show_values_prints_them(evals_dir, fake, capsys):
    _write_golden(evals_dir)
    _run(evals_dir, "--show-values")
    out = capsys.readouterr().out
    assert "expected 'Notar Dr. Max Beispiel'  read 'Notar Dr. Beispiel'" in out
    assert "expected '1100.00'  read None" in out
    assert KEY not in out


def test_everything_matching_exits_0(evals_dir, fake, capsys):
    golden = {
        "kind": "invoice",
        "fields": {
            "title": "Notarrechnung Kaufvertrag",
            "sender": "Notar Dr. Beispiel",
            "amount_total": "1309.00",
            "document_date": "2026-02-12",
        },
    }
    _write_golden(evals_dir, golden)
    assert _run(evals_dir) == 0
    assert "Total: 1 document: 4 match, 0 differs, 0 missing, 0 extra (100.0 % match)" in (
        capsys.readouterr().out
    )


def test_a_wrong_kind_is_reported_and_fields_are_not_compared(evals_dir, fake, capsys):
    _write_golden(evals_dir, {"kind": "loan_contract", "fields": {"principal": "1.00"}})
    assert _run(evals_dir) == 1
    out = capsys.readouterr().out
    assert "rechnung.pdf: kind differs (read invoice); fields not compared" in out
    assert "loan_contract" not in out


def test_write_expected_writes_and_refuses_to_overwrite(evals_dir, fake, capsys):
    golden = evals_dir / "rechnung.expected.yaml"
    assert _run(evals_dir, "--write-expected") == 0
    written = yaml.safe_load(golden.read_text())
    assert written["kind"] == "invoice"
    assert written["fields"]["amount_total"] == "1309.00"
    assert written["fields"]["document_date"] == "2026-02-12"
    assert written["fields"]["due_on"] is None
    assert set(written["fields"]) == {
        "title",
        "document_date",
        "sender",
        "subject",
        "amount_total",
        "amount_net",
        "reference",
        "iban",
        "due_on",
    }
    out = capsys.readouterr().out
    assert "wrote rechnung.expected.yaml" in out
    assert "Notar" not in out  # values only with --show-values

    golden.write_text("kind: invoice\nfields: {}\n")  # Luis's hand-checked version
    assert _run(evals_dir, "--write-expected") == 1
    assert "rechnung.expected.yaml exists (--force to overwrite)" in capsys.readouterr().out
    assert golden.read_text() == "kind: invoice\nfields: {}\n"

    assert _run(evals_dir, "--write-expected", "--force") == 0
    assert yaml.safe_load(golden.read_text())["fields"]["amount_total"] == "1309.00"
    # The golden file is the reading itself: comparing against it matches.
    assert _run(evals_dir) == 0


def test_a_document_without_golden_file(evals_dir, fake, capsys):
    assert _run(evals_dir) == 1
    out = capsys.readouterr().out
    assert "no rechnung.expected.yaml" in out
    assert "4 of 9 fields stated (--show-values to see them)" in out


def test_a_directory_inside_the_repo_is_refused(fake, capsys):
    inside = Path(__file__).resolve().parent  # backend/tests/evals
    assert _run(inside) == cli.EXIT_ERROR
    err = capsys.readouterr().err
    assert "inside the repository" in err
    clients, _ = fake
    assert clients == []  # nothing was sent, no client made


def test_the_key_is_needed_and_never_printed(evals_dir, fake, capsys):
    assert _run(evals_dir, env={}) == cli.EXIT_ERROR
    assert "HAUSBUCH_ANTHROPIC_API_KEY is not set" in capsys.readouterr().err
    assert fake[0] == []


def test_the_eval_dir_comes_from_the_environment(evals_dir, fake, capsys):
    _write_golden(evals_dir)
    env = {"HAUSBUCH_ANTHROPIC_API_KEY": KEY, "HAUSBUCH_EVAL_DIR": str(evals_dir)}
    assert cli.main(["evals", "run"], environ=env) == 1
    assert "rechnung.pdf: kind invoice" in capsys.readouterr().out
    assert cli.main(["evals", "run"], environ={"HAUSBUCH_ANTHROPIC_API_KEY": KEY}) == 1
    assert "set HAUSBUCH_EVAL_DIR or pass --dir" in capsys.readouterr().err


def test_a_failed_call_prints_its_code_only(evals_dir, monkeypatch, capsys):
    from hausbuch.ai.client import ModelCallError

    error = ModelCallError(retryable=False, status=400, request_id="req_1")
    monkeypatch.setattr(evals_run, "make_client", lambda key, s: FakeModelClient([error]))
    assert _run(evals_dir) == 1
    assert "rechnung.pdf: not read (ai.api_error (HTTP 400))" in capsys.readouterr().out


def test_the_property_addresses_come_from_properties_yaml(tmp_path):
    # The general reading keeps only the workspace's property addresses; an eval run takes
    # them from properties.yaml beside the documents (none without it).
    assert evals_run.load_properties(tmp_path) == ()
    (tmp_path / "properties.yaml").write_text(
        "properties:\n  - {street: Musterstraße, house_number: '1', postal_code: '12345'}\n",
        encoding="utf-8",
    )
    assert evals_run.load_properties(tmp_path) == (PropertyAddress("Musterstraße", "1", "12345"),)
    (tmp_path / "properties.yaml").write_text("properties: [{street: X}]\n", encoding="utf-8")
    with pytest.raises(evals_run.EvalRunError):
        evals_run.load_properties(tmp_path)
