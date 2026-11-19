"""The issuers' fund catalogues without a network (docs/phases/phase-6.md D174 amended 16 Nov
2026): an ISIN found by the issuer's own list of its funds, an ambiguous or malformed entry
left out, and a fund's name deciding which catalogue is asked. Synthetic files with invented
ISINs only (fixtures/phase6/samples/ishares_catalogue.json, spdr_fundfinder.json)."""

import json

import fixture_models_phase6 as phase6
import pytest

from hausbuch.services.prices.http import SourceError
from hausbuch.services.prices.issuer_nav import ADAPTERS, ISharesAdapter, SpdrAdapter

SAMPLES = phase6.SAMPLES


def _sample(name: str) -> bytes:
    return (SAMPLES / name).read_bytes()


def test_ishares_catalogue_maps_an_isin_to_its_product_number():
    found = ISharesAdapter.parse_catalogue(_sample("ishares_catalogue.json"))
    # IE000INVENT3 is listed under two numbers, so it is never guessed; a non-numeric id,
    # a malformed ISIN and a product without one are left out.
    assert found == {"IE000INVENT2": "1"}
    assert (
        ISharesAdapter(lambda *a: (200, b""))
        .url(found["IE000INVENT2"])
        .endswith("portfolioId=1&component=fundDownloadV2&userType=individual")
    )


def test_spdr_catalogue_maps_an_isin_to_the_ticker_slug_of_its_documents():
    found = SpdrAdapter.parse_catalogue(_sample("spdr_fundfinder.json"))
    # The two links of IE000INVENT1 agree; IE000INVENT5 names two slugs; a slug the NAV
    # file's address cannot take is left out.
    assert found == {"IE000INVENT1": "inv-gy"}
    assert SpdrAdapter(lambda *a: (200, b"")).url("inv-gy").endswith("navhist-emea-en-inv-gy.xlsx")


@pytest.mark.parametrize("adapter", [ISharesAdapter, SpdrAdapter])
def test_a_catalogue_the_reader_does_not_understand_fails_closed(adapter):
    for body in (b"<html>moved</html>", b"{}", json.dumps({"data": None}).encode()):
        with pytest.raises(SourceError) as failed:
            adapter(lambda *a, body=body: (200, body)).catalogue()
        assert failed.value.kind == "parse"


def test_a_catalogue_is_asked_with_the_apps_agent_and_no_redirect_status():
    asked = []

    def fetch(url, headers, timeout, max_bytes):
        asked.append((url, headers["User-Agent"], max_bytes))
        return 200, _sample("ishares_catalogue.json")

    assert ISharesAdapter(fetch).catalogue() == {"IE000INVENT2": "1"}
    [(url, agent, cap)] = asked
    assert url == ISharesAdapter.catalogue_url and agent.startswith("Hausbuch/")
    assert cap >= 8 * 1024 * 1024  # the real list is about 4 MB


@pytest.mark.parametrize(
    ("name", "issuer"),
    [
        ("iShares Core Invented UCITS ETF", "ishares"),
        ("iShsIII-Core Invented U.ETF", "ishares"),
        ("SPDR Invented UCITS ETF", "spdr"),
        ("SSgA SPDR ETFs Europe I-Invented", "spdr"),
        ("State Street Invented UCITS ETF", "spdr"),
        # D174 amended 19 Nov 2026 (skipped until the adapter is built); until then
        # „Xtrackers Invented“ named no adapter
        ("Xtrackers Invented", "xtrackers"),
        ("Xtr.(IE) Plc-Invented", "xtrackers"),
        ("AIS-Amundi Invented", "amundi"),
        ("MUL-Lyxor Invented", "amundi"),
        ("Invesco Markets II-Invented", "invesco"),
        ("ARK Invest UCITS ICAV-Invented", "ark"),
        ("Rize UCITS ICAV-Invented", "ark"),
        ("BNP P.E.I-Invented", "bnp"),
        ("BNPP Invented", "bnp"),
        ("L&G ETF-L&G Invented", "landg"),
        ("HANetf ICAV-Invented", "hanetf"),
        ("Fishs Invented", None),
        ("Darkside Invested", None),
    ],
)
def test_the_fund_name_decides_which_catalogue_is_asked(name, issuer):
    if issuer is not None and issuer not in ADAPTERS:
        pytest.skip(f"no {issuer} adapter yet (D174 amended 19 Nov 2026)")
    named = [key for key, cls in ADAPTERS.items() if cls.names_brand(name)]
    assert named == ([] if issuer is None else [issuer])


def test_an_ishares_file_with_a_bare_ampersand_in_a_fund_name_is_read():
    # The live file of an S&P index fund printed its name with an unescaped "&" (16 Nov 2026),
    # which a strict XML reader refuses; an escaped one and a character entity stay as they are.
    clean = _sample("ishares_fund_download.xls")
    name = b"iShares Invented EM UCITS ETF USD (Acc)"
    assert name in clean
    bare = clean.replace(name, b"iShares Invented S&P EM &amp; Co &#38; UCITS ETF", 1)
    read = ISharesAdapter.parse(bare)
    assert read == ISharesAdapter.parse(clean) and read.points
