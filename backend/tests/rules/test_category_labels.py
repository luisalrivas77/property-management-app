"""D49: the question a rule's near miss asks names the category by its German label, which
rules.categories.CATEGORY_LABEL copies from frontend labels.ts (ALLOCATION_CATEGORY). This
keeps the two copies equal, so the inbox and the question never name a category differently."""

import re
from pathlib import Path

from hausbuch.rules.categories import CATEGORY_LABEL

LABELS_TS = Path(__file__).resolve().parents[3] / "frontend" / "src" / "components" / "labels.ts"
_BLOCK = re.compile(r"export const ALLOCATION_CATEGORY[^=]*=\s*\{(.*?)\n\};", re.S)
_ENTRY = re.compile(r'^\s*(\w+):\s*"((?:[^"\\]|\\.)*)",?\s*$', re.M)


def test_category_labels_equal_the_frontends():
    block = _BLOCK.search(LABELS_TS.read_text(encoding="utf-8"))
    assert block is not None, "ALLOCATION_CATEGORY not found in labels.ts"
    frontend = dict(_ENTRY.findall(block.group(1)))
    assert {c.value: label for c, label in CATEGORY_LABEL.items()} == frontend
