"""The request's language and the catalogues (phase 10 D284, step 4c)."""

import asyncio
import importlib
import pkgutil
from datetime import date
from decimal import Decimal

import pytest
from starlette.types import Message, Receive, Scope, Send

import hausbuch
from hausbuch import i18n
from hausbuch.api.logging import RequestMiddleware


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, "de"),
        ("", "de"),
        ("de", "de"),
        ("en", "en"),
        ("en-GB", "en"),
        ("en-GB,en;q=0.9,de;q=0.8", "en"),
        ("de-DE,de;q=0.9,en;q=0.8", "de"),
        ("de;q=0.5,en;q=0.9", "en"),
        ("en,de", "en"),
        ("de,en", "de"),
        ("fr", "de"),
        ("fr,en;q=0.5", "en"),
        ("en;q=0", "de"),
        ("*", "de"),
        ("en;q=abc,de", "de"),
        ("garbage;;,,", "de"),
    ],
)
def test_accept_language(header: str | None, expected: str) -> None:
    assert i18n.from_accept_language(header) == expected


def test_formats() -> None:
    assert i18n.money(Decimal("1400.5"), "de") == "1.400,50 €"
    assert i18n.money(Decimal("1400.5"), "en") == "€1,400.50"
    assert i18n.money(Decimal("-1234567.891"), "en") == "-€1,234,567.89"
    assert i18n.day(date(2026, 3, 5), "de") == "05.03.2026"
    assert i18n.day(date(2026, 3, 5), "en") == "5 Mar 2026"
    assert i18n.month(2026, 3, "de") == "März 2026"
    assert i18n.month(2026, 3, "en") == "March 2026"
    assert i18n.number(Decimal("1234.5"), 1, "en") == "1,234.5"


def test_texts_follow_the_language() -> None:
    texts = i18n.Texts("test.texts", {"n": ("{n} Zahlungen", "{n} payments")})
    try:
        assert texts("n", n=3) == "3 Zahlungen"
        with i18n.use("en"):
            assert texts("n", n=3) == "3 payments"
            assert texts.german("n", n=3) == "3 Zahlungen"
        assert texts("n", n=3) == "3 Zahlungen"
    finally:
        i18n.CATALOGUES.remove(texts)


def test_texts_refuse_differing_placeholders() -> None:
    with pytest.raises(ValueError, match="placeholders"):
        i18n.Texts("test.bad", {"k": ("{a} da", "{b} there")})


def test_patterns_convert_german_formats() -> None:
    patterns = i18n.Patterns(
        "test.patterns",
        [
            (
                "Für „{label}“ fehlt der Auszug bis {d:date}, Saldo {a:money}, {p:percent}.",
                "The statement for “{label}” is missing until {d}, balance {a}, {p}.",
            ),
            ("Für {m:month} nichts.", "Nothing for {m}."),
        ],
    )
    try:
        text = "Für „Giro“ fehlt der Auszug bis 15.03.2026, Saldo -1.234,56 €, 3,5 %."
        assert patterns.translate(text) == text  # German outside an English request
        with i18n.use("en"):
            assert patterns.translate(text) == (
                "The statement for “Giro” is missing until 15 Mar 2026, balance -€1,234.56, 3.5%."
            )
            assert patterns.translate("Für März 2026 nichts.") == "Nothing for March 2026."
            assert patterns.translate("Etwas anderes.") == "Etwas anderes."
    finally:
        i18n.CATALOGUES.remove(patterns)


def _every_module() -> None:
    for info in pkgutil.walk_packages(hausbuch.__path__, "hausbuch."):
        if ".evals" in info.name:
            continue
        importlib.import_module(info.name)


def test_every_catalogue_is_complete() -> None:
    """Every German text of every catalogue has its English, naming the same placeholders
    (the constructors refuse otherwise; this imports every module so each one is built)."""
    _every_module()
    # (non-empty once the first module has its catalogue)
    for catalogue in i18n.CATALOGUES:
        if isinstance(catalogue, i18n.Texts):
            assert catalogue.entries, catalogue.name
            for key, (german, english) in catalogue.entries.items():
                assert german.strip() and english.strip(), (catalogue.name, key)
                assert i18n.fields(german) == i18n.fields(english), (catalogue.name, key)
                assert german != english or not any(c in german for c in "äöüÄÖÜß„"), (
                    catalogue.name,
                    key,
                )
        else:
            assert catalogue.entries, catalogue.name


async def _run(middleware: RequestMiddleware, headers: list[tuple[bytes, bytes]]) -> None:
    scope: Scope = {"type": "http", "method": "GET", "path": "/x", "headers": headers}

    async def receive() -> Message:
        return {"type": "http.request", "body": b""}

    async def send(message: Message) -> None:
        pass

    await middleware(scope, receive, send)


def test_middleware_sets_the_language_for_the_request_only() -> None:
    seen: list[str] = []

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        seen.append(i18n.current())
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = RequestMiddleware(app)

    async def scenario() -> list[str]:
        worker_saw: list[str] = []
        started = asyncio.Event()
        release = asyncio.Event()

        async def worker() -> None:  # a task started outside any request: the job worker
            started.set()
            await release.wait()
            worker_saw.append(i18n.current())

        task = asyncio.create_task(worker())
        await started.wait()
        await _run(middleware, [(b"accept-language", b"en")])
        await _run(middleware, [])
        await _run(middleware, [(b"accept-language", b"de")])
        after = i18n.current()
        release.set()
        await task
        return [*worker_saw, after]

    worker_and_after = asyncio.run(scenario())
    assert seen == ["en", "de", "de"]
    assert worker_and_after == ["de", "de"]
