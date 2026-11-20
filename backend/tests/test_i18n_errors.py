"""The error messages services write in German for the screen, in English under `en` (phase 10
D284, api/errors.py MESSAGES); German without a header, and codes and kinds unchanged."""

import pytest

from hausbuch import i18n
from hausbuch.api.errors import MESSAGES, render
from hausbuch.services.errors import ConflictError


@pytest.mark.parametrize("german", [p for p, _, _ in MESSAGES.entries], ids=str)
def test_each_message_has_english(german):
    text = german.pattern.replace("\\", "")
    german_body = render(ConflictError(text), None).body
    assert german_body.message == text
    with i18n.use(i18n.EN):
        english_body = render(ConflictError(text), None).body
    assert english_body.message != text
    assert not any(mark in english_body.message for mark in ("ä", "ö", "ü", "ß"))
    assert english_body.model_dump(exclude={"message"}) == german_body.model_dump(
        exclude={"message"}
    )


def test_an_unknown_message_stays_as_written():
    with i18n.use(i18n.EN):
        assert render(ConflictError("something else"), None).body.message == "something else"
