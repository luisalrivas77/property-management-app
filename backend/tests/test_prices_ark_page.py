"""The ARK fund page as the live pages print it (seen 19 Nov 2026), built in memory from the
synthetic sample (the fixture file itself is unchanged): every page also names its intraday
value's ISIN („iNAV ISIN“), and a page may print the NAV per share without its currency."""

from decimal import Decimal

import pytest
from fixture_models_phase6 import SAMPLES

from hausbuch.services.prices.http import SourceError
from hausbuch.services.prices.issuer_nav.ark import ArkAdapter

SAMPLE = (SAMPLES / "ark_fund_page.html").read_bytes()
INAV_ROW = b"<tr> <td>iNAV ISIN</td> <td>IE000INVENT9</td> </tr></table>"


def _page(*, inav: bool = True, nav: bytes = b"USD 15.4417") -> bytes:
    body = SAMPLE.replace(b"<td>USD 15.4417</td>", b"<td>" + nav + b"</td>")
    if inav:
        body = body.replace(
            b"<td>" + nav + b"</td> </tr></table>", b"<td>" + nav + b"</td> </tr>\n" + INAV_ROW
        )
    return body


def test_the_sample_page_carries_the_rows_these_variants_replace():
    assert SAMPLE.count(b"<td>USD 15.4417</td> </tr></table>") == 1
    assert _page() != SAMPLE and INAV_ROW in _page()


def test_the_intraday_value_s_isin_is_never_the_fund_s():
    page = ArkAdapter.parse(_page())
    assert page.isin == "IE000INVENT7" and page.currency == "USD"
    assert page == ArkAdapter.parse(SAMPLE)


def test_a_nav_per_share_without_its_currency_is_in_the_base_currency():
    page = ArkAdapter.parse(_page(nav=b"15.4417"))
    assert page == ArkAdapter.parse(SAMPLE)
    assert max(page.points, key=lambda p: p.on).nav == Decimal("15.4417")


def test_a_nav_per_share_in_another_currency_is_still_refused():
    with pytest.raises(SourceError) as raised:
        ArkAdapter.parse(_page(nav=b"EUR 15.4417"))
    assert raised.value.kind == "parse"
