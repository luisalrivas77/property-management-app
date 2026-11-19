"""The ARK reader's NAV per share: printed without a currency it is read in the base currency
(the fixture case `ark_live_layout`); printed in another currency the page is still refused.
The variant is built in memory from the live-layout sample, which is unchanged."""

import pytest
from fixture_models_phase6 import SAMPLES

from hausbuch.services.prices.http import SourceError
from hausbuch.services.prices.issuer_nav.ark import ArkAdapter

SAMPLE = (SAMPLES / "ark_fund_page_live_layout.html").read_bytes()
NAV_CELL = b"<td>NAV per share</td> <td>15.4417</td>"


def test_a_nav_per_share_in_another_currency_is_still_refused():
    assert SAMPLE.count(NAV_CELL) == 1
    ArkAdapter.parse(SAMPLE)  # the sample as it is reads
    body = SAMPLE.replace(NAV_CELL, b"<td>NAV per share</td> <td>EUR 15.4417</td>")
    with pytest.raises(SourceError) as raised:
        ArkAdapter.parse(body)
    assert raised.value.kind == "parse"
