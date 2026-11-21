"""The frontend's English error texts cover every German one (phase 10 D284).

The screens have no unit tests of their own; these read the TypeScript sources as text, the
way test_i18n_read_texts compares the category names, so a rule code added in German without
its English fails here instead of showing German on an English screen.
"""

import re
from pathlib import Path

FRONTEND = Path(__file__).parents[2] / "frontend/src"
KEY = re.compile(r'^\s+"([\w.]+)":', re.M)


def _keys(path: str, name: str) -> set[str]:
    source = (FRONTEND / path).read_text("utf-8")
    start = source.index(f"const {name}")
    return set(KEY.findall(source[start : source.index("\n};", start)]))


def test_every_rule_code_has_english():
    german = _keys("api/errors.ts", "RULE_CODE_TEXT_DE")
    english = _keys("i18n/errors.ts", "RULE_CODE_TEXT_EN")
    assert len(german) > 150
    assert german == english


def test_every_reading_error_has_english():
    german = _keys("api/errors.ts", "AI_ERROR_TEXT")
    english = _keys("i18n/errors.ts", "AI_ERROR_TEXT_EN")
    assert german
    assert german == english


def test_english_error_texts_keep_their_german_button_names():
    """A button on a screen that may still be German is quoted with its German label too."""
    source = (FRONTEND / "i18n/errors.ts").read_text("utf-8")
    assert "“Always categorise like this” („Künftig immer so zuordnen“)" in source
    assert "“Import to account” („Auf Konto importieren“)" in source
