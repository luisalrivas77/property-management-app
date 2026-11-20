"""The server's own card texts in the request's language (phase 10 D284, step 4c): German by
default, English when the request asks for it, from the same catalogue. Pure, no database;
the database tests of each card read it once in English too."""

import pytest

from hausbuch import i18n
from hausbuch.rules.errors import WALLET_SYNC_ERROR
from hausbuch.schemas.chain import Chain
from hausbuch.schemas.wallet_cards import WalletAddressState
from hausbuch.services import wallet_cards

_GERMAN_ONLY = ("ä", "ö", "ü", "ß", "„", " der ", " die ", " und ")


@pytest.mark.parametrize("state", list(WalletAddressState))
@pytest.mark.parametrize("error_code", [None, WALLET_SYNC_ERROR])
def test_every_wallet_address_state_speaks_both_languages(state, error_code) -> None:
    for chain in Chain:
        german = wallet_cards._texts(state, chain, error_code)
        with i18n.use("en"):
            english = wallet_cards._texts(state, chain, error_code)
        assert german == wallet_cards._texts(state, chain, error_code)  # the default
        assert german[2] == english[2]  # the setting it names is no text
        for de, en in zip(german[:2], english[:2], strict=True):
            assert (de is None) is (en is None)
            if de is not None:
                assert de != en
                assert not any(word in en for word in _GERMAN_ONLY), en


def test_a_wallet_key_is_named_where_it_can_be_typed() -> None:
    with i18n.use("en"):
        assert wallet_cards._where("HAUSBUCH_ETHERSCAN_KEY", "enter") == (
            "In Hausbuch under Settings › Access keys, enter the entry"
            " (or the variable HAUSBUCH_ETHERSCAN_KEY in the Scaleway console)"
        )
        assert wallet_cards._where("HAUSBUCH_ESPLORA_URL", "check").startswith(
            "In the Scaleway console"
        )
    assert wallet_cards._where("HAUSBUCH_ESPLORA_URL", "check").startswith(
        "In der Scaleway-Konsole"
    )
