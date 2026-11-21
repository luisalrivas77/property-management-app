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


_GUIDE_ENTRY = re.compile(r'^  "?([a-z0-9_.]+)"?: \{', re.M)


def _guide_keys(path: str, name: str) -> set[str]:
    source = (FRONTEND / path).read_text("utf-8")
    block = re.search(rf"export const {name}\b[^=]*=\s*\{{(.*?)\n\}};", source, re.S)
    assert block is not None, f"{name} not found in {path}"
    return set(_GUIDE_ENTRY.findall(block.group(1)))


def test_every_todo_guide_has_english():
    """todos.tsx keeps its German guides; todosEn.ts has the English of every code."""
    for german, english in (
        ("WARNING_GUIDE", "WARNING_EN"),
        ("REQUIREMENT_GUIDE", "REQUIREMENT_EN"),
        ("REMINDER_GUIDE", "REMINDER_EN"),
    ):
        codes = _guide_keys("components/todos.tsx", german)
        assert codes, german
        assert codes == _guide_keys("components/todosEn.ts", english), german


def test_every_todo_link_label_has_english():
    todos = (FRONTEND / "components/todos.tsx").read_text("utf-8")
    labels = set(re.findall(r'label: "([^"]+)"', todos))
    source = (FRONTEND / "components/todosEn.ts").read_text("utf-8")
    block = source[source.index("export const LABEL_EN") :]
    english = set(re.findall(r'^\s+"([^"]+)":', block, re.M))
    assert labels
    assert labels <= english, sorted(labels - english)


_TOP_KEY = re.compile(r'^  "?(\w+)"?: ', re.M)


def _top_keys(path: str, name: str) -> set[str]:
    """The record's own keys (two spaces in), not the keys of the objects inside it."""
    source = (FRONTEND / path).read_text("utf-8")
    start = source.index(f"const {name}")
    return set(_TOP_KEY.findall(source[start : source.index("\n};", start)]))


def test_german_and_english_record_pairs_have_the_same_keys():
    """A record pair behind localized() missing a key in English shows nothing there."""
    pairs = [
        ("i18n/returnInputs.ts", "KIND_TEXT"),
        ("i18n/proposals.ts", "MOVEMENT_KIND"),
        ("i18n/proposals.ts", "TAX_INPUT_TITLE"),
        ("i18n/proposals.ts", "DERIVED_INPUT"),
    ]
    for path, name in pairs:
        german = _top_keys(path, f"{name}_DE")
        assert german, name
        assert german == _top_keys(path, f"{name}_EN"), name
