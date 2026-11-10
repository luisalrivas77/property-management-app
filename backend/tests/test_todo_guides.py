"""D136 (phase-3.md): every gap the app knows is a plain to-do with an action. The frontend's
components/todos.tsx holds one entry per code, saying what to do and which button or link does
it, or marked `todo: false` for a note that needs nothing. This keeps the three maps equal to
the codes the backend can send, so a new warning code, checklist requirement or reminder kind
fails here until it has its entry (WARNING_GUIDE is a Partial record, so tsc alone would not)."""

import re
from pathlib import Path

from hausbuch.schemas.checklist import RequirementCode
from hausbuch.schemas.reminders import ReminderKind
from hausbuch.schemas.tax import WarningCode

TODOS_TSX = Path(__file__).resolve().parents[2] / "frontend" / "src" / "components" / "todos.tsx"
# A map's top-level keys sit at two spaces: `  code: {` or `  "dotted.code": {`.
_ENTRY = re.compile(r'^  "?([a-z0-9_.]+)"?: \{', re.M)


def _keys(name: str) -> list[str]:
    source = TODOS_TSX.read_text(encoding="utf-8")
    block = re.search(rf"export const {name}\b[^=]*=\s*\{{(.*?)\n\}};", source, re.S)
    assert block is not None, f"{name} not found in todos.tsx"
    return _ENTRY.findall(block.group(1))


def _equal(name: str, codes: set[str]) -> None:
    keys = _keys(name)
    assert len(keys) == len(set(keys)), f"{name} has a duplicate entry"
    assert set(keys) - codes == set(), f"{name} has entries for codes the backend does not know"
    assert codes - set(keys) == set(), f"{name} lacks an entry (an action, or todo: false)"


def test_every_warning_code_has_a_guide():
    _equal("WARNING_GUIDE", {c.value for c in WarningCode})


def test_every_checklist_requirement_has_a_guide():
    _equal("REQUIREMENT_GUIDE", {c.value for c in RequirementCode})


def test_every_reminder_kind_has_a_guide():
    _equal("REMINDER_GUIDE", {k.value for k in ReminderKind})
