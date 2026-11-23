"""Phase 10 D291 (§7.7): the reader of a coin's current price (services/prices/spot.py) with
mocked HTTP. Kraken's ticker first, CoinGecko's simple price as the fallback with its key in
the header only, a pair not in euros converted at the newest ECB rate, every failure counted
and none raised. Invented coins, pairs and amounts only."""

import json
from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import uuid4

import procrastinate
import pytest
from procrastinate import testing
from pydantic import SecretStr

from hausbuch.jobs import refresh_crypto, tasks
from hausbuch.schemas.prices import FxRate
from hausbuch.services.prices import spot
from hausbuch.services.prices.coingecko import KEY_HEADER
from hausbuch.services.prices.http import SourceError

KEY = "CG-invented-demo-key"
READ_AT = datetime(2026, 11, 23, 13, 32, tzinfo=UTC)


def _ticker(name: str, last: str) -> bytes:
    return json.dumps(
        {"error": [], "result": {name: {"a": ["1", "1", "1"], "c": [last, "0.5"]}}}
    ).encode()


class Fake:
    """A Fetch answering by URL part; records every call with its headers."""

    def __init__(self, answers: dict[str, tuple[int, bytes]]) -> None:
        self.answers = answers
        self.calls: list[tuple[str, dict[str, str]]] = []

    def __call__(self, url, headers, timeout, max_bytes):
        self.calls.append((url, dict(headers)))
        for part, answer in self.answers.items():
            if part in url:
                return answer
        return 404, b""


def _coin(pair=None, gecko=None):
    return spot.Coin(uuid4(), pair, gecko)


def _read(coins, fake, key=None, rates=None):
    result = spot.SpotResult()
    rows = spot.read_prices(
        coins,
        rates or {},
        fetch=fake,
        clock=lambda: READ_AT,
        coingecko_key=SecretStr(key) if key else None,
        result=result,
    )
    return rows, result


def test_the_ticker_answer_gives_the_last_trade():
    assert spot.parse_ticker(_ticker("INVTEUR", "123.456"), "INVTEUR") == Decimal("123.45600000")
    # Kraken may answer under its own name for the pair (XXBTZEUR for XBTEUR).
    assert spot.parse_ticker(_ticker("XINVZEUR", "7"), "INVTEUR") == Decimal("7.00000000")


@pytest.mark.parametrize(
    "body",
    [
        b"not json",
        json.dumps({"error": ["EQuery:Unknown asset pair"], "result": {}}).encode(),
        json.dumps({"error": [], "result": {}}).encode(),
        json.dumps({"error": [], "result": {"A": {"c": ["1"]}, "B": {"c": ["2"]}}}).encode(),
        json.dumps({"error": [], "result": {"INVTEUR": {"c": []}}}).encode(),
        json.dumps({"error": [], "result": {"INVTEUR": {"c": ["0", "1"]}}}).encode(),
        json.dumps({"error": [], "result": {"INVTEUR": {"c": ["abc", "1"]}}}).encode(),
        # `c` a string, not a list: its first character must never become the price.
        json.dumps({"error": [], "result": {"INVTEUR": {"c": "95000.1"}}}).encode(),
        json.dumps({"error": [], "result": {"INVTEUR": "95000.1"}}).encode(),
        json.dumps({"error": [], "result": {"INVTEUR": ["95000.1"]}}).encode(),
        # Above the column's limit (numeric(20, 8)) or 0 once quantized to 8 places.
        json.dumps({"error": [], "result": {"INVTEUR": {"c": ["1000000000000", "1"]}}}).encode(),
        json.dumps({"error": [], "result": {"INVTEUR": {"c": ["0.000000001", "1"]}}}).encode(),
    ],
)
def test_a_ticker_answer_not_in_krakens_shape_is_a_parse_error(body):
    with pytest.raises(SourceError) as raised:
        spot.parse_ticker(body, "INVTEUR")
    assert raised.value.kind == "parse"


def test_the_quote_currency_of_a_pair():
    assert spot.quote_currency("XXBTZEUR") == "EUR"
    assert spot.quote_currency("INVTUSD") == "USD"
    assert spot.quote_currency("EUR") is None
    assert spot.quote_currency("INVTXYZ") is None


def test_kraken_prices_each_pair_once_and_coingecko_is_not_asked():
    a, b = _coin("INVTEUR", "invented-coin"), _coin("INVTEUR")  # a chain token and its twin
    fake = Fake({"Ticker?pair=INVTEUR": (200, _ticker("INVTEUR", "120.5"))})
    rows, result = _read([a, b], fake, key=KEY)
    assert [(r.asset_id, r.price_eur, r.pair, r.read_at) for r in rows] == [
        (a.asset_id, Decimal("120.50000000"), "INVTEUR", READ_AT),
        (b.asset_id, Decimal("120.50000000"), "INVTEUR", READ_AT),
    ]
    assert (result.kraken, result.coingecko, result.unpriced) == (2, 0, 0)
    assert [url for url, _ in fake.calls] == ["https://api.kraken.com/0/public/Ticker?pair=INVTEUR"]


def test_a_failed_ticker_falls_back_to_coingecko_with_the_key_in_the_header_only():
    a = _coin("INVTEUR", "invented-coin")
    b = _coin(None, "other-invented")
    c = _coin("NOPEEUR")  # no CoinGecko id: keeps its close
    fake = Fake(
        {
            "Ticker?pair=INVTEUR": (503, b""),
            "Ticker?pair=NOPEEUR": (200, b'{"error": ["EQuery:Unknown asset pair"]}'),
            "simple/price": (
                200,
                b'{"invented-coin": {"eur": 99.5}, "other-invented": {"eur": 0.0123}}',
            ),
        }
    )
    rows, result = _read([a, b, c], fake, key=KEY)
    prices = {r.asset_id: (r.price_eur, r.pair) for r in rows}
    assert prices == {
        a.asset_id: (Decimal("99.50000000"), "invented-coin"),
        b.asset_id: (Decimal("0.01230000"), "other-invented"),
    }
    assert (result.kraken, result.coingecko, result.unpriced) == (0, 2, 1)
    assert result.errors == {
        ("kraken_ticker", "HTTPError", 503): 1,
        ("kraken_ticker", "parse", None): 1,
    }
    [(url, headers)] = [(u, h) for u, h in fake.calls if "simple/price" in u]
    assert url.endswith("simple/price?ids=invented-coin,other-invented&vs_currencies=eur")
    assert headers[KEY_HEADER] == KEY
    assert all(KEY not in u for u, _ in fake.calls)


def test_without_a_key_coingecko_is_never_asked_and_the_coin_keeps_its_close():
    a = _coin("INVTEUR", "invented-coin")
    fake = Fake({"Ticker?pair=INVTEUR": (503, b"")})
    rows, result = _read([a], fake)
    assert rows == [] and result.unpriced == 1
    assert not any("coingecko" in url for url, _ in fake.calls)


def test_a_failed_coingecko_answer_leaves_its_coins_unpriced():
    a = _coin(None, "invented-coin")
    fake = Fake({"simple/price": (429, b"")})
    rows, result = _read([a], fake, key=KEY)
    assert rows == [] and result.unpriced == 1
    assert result.errors == {("coingecko_simple", "HTTPError", 429): 1}


def test_a_pair_in_dollars_is_converted_at_the_newest_ecb_rate():
    a = _coin("INVTUSD")
    rates = {
        "USD": [
            FxRate(on=date(2026, 11, 20), per_eur=Decimal("1.0000")),
            FxRate(on=date(2026, 11, 21), per_eur=Decimal("1.2500")),
        ]
    }
    fake = Fake({"Ticker?pair=INVTUSD": (200, _ticker("INVTUSD", "100"))})
    rows, result = _read([a], fake, rates=rates)
    [row] = rows
    assert row.price_eur == Decimal("80.00000000") and row.last == Decimal("100.00000000")
    assert row.pair == "INVTUSD" and result.kraken == 1
    # Without a stored rate of its currency: no price, the coin keeps its close.
    rows, result = _read([a], fake)
    assert rows == [] and result.unpriced == 1


def test_a_price_that_cannot_be_stored_leaves_that_coin_unpriced_while_the_others_return():
    big = _coin("BIGEUR", "big-coin")  # 10^12 from Kraken, falls to CoinGecko
    tiny = _coin("TINYUSD")  # 0E-8 in euros after the conversion, no CoinGecko id
    fine = _coin("INVTEUR")
    gecko_big = _coin(None, "huge-coin")  # 10^12 from CoinGecko too: unpriced
    rates = {"USD": [FxRate(on=date(2026, 11, 21), per_eur=Decimal("3"))]}
    fake = Fake(
        {
            "Ticker?pair=BIGEUR": (200, _ticker("BIGEUR", "1000000000000")),
            "Ticker?pair=TINYUSD": (200, _ticker("TINYUSD", "0.00000001")),
            "Ticker?pair=INVTEUR": (200, _ticker("INVTEUR", "5")),
            "simple/price": (
                200,
                b'{"big-coin": {"eur": 42}, "huge-coin": {"eur": 1000000000000}}',
            ),
        }
    )
    rows, result = _read([big, tiny, fine, gecko_big], fake, key=KEY, rates=rates)
    assert {r.asset_id: (r.price_eur, r.pair) for r in rows} == {
        big.asset_id: (Decimal("42.00000000"), "big-coin"),
        fine.asset_id: (Decimal("5.00000000"), "INVTEUR"),
    }
    assert (result.kraken, result.coingecko, result.unpriced) == (1, 1, 2)
    assert result.errors == {("kraken_ticker", "parse", None): 1}  # the 10^12 ticker


def test_coingecko_prices_that_cannot_be_stored_are_left_out():
    body = (
        b'{"a": {"eur": 1000000000000}, "b": {"eur": 0.000000001},'
        b' "c": {"eur": 999999999999.5}, "d": {"eur": "7"}}'
    )
    assert spot.parse_simple(body) == {"c": Decimal("999999999999.50000000")}


def test_the_refresh_task_is_registered_under_the_wallets_lock_with_no_schedule_or_retry():
    app = tasks.register_tasks(procrastinate.App(connector=testing.InMemoryConnector()))
    task = app.tasks[refresh_crypto.REFRESH_CRYPTO_TASK]
    assert task.lock == refresh_crypto.REFRESH_CRYPTO_LOCK == "wallets:sync"
    assert task.retry_strategy is None
    assert not [p for p in app.periodic_registry.periodic_tasks.values() if p.task is task]
