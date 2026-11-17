"""Phase 9: the household plan's explanation (docs/phases/phase-9.md §8, §9).

- `advice_rules.yaml`, `advice_lexicon.yaml`, `advice_vocabulary.txt`: the validator's word
  lists and tables, the names it never lets through, and the words it allows (D255, §9.3);
  each versioned, the versions stored with every explanation. Loaded here.
- `facts.build`: the request's facts under opaque keys, the labels apart (D252–D254), step 4.
- `validator.validate`: the thirteen deterministic checks, failing closed (D255, D256), step 4.
- `render.render`: slots to German text after validation (§8.4), step 4.
- `template.render`: the app's own text from the same facts (D257), step 4.

Step 0 writes the data and the loaders; every rule is a stub until step 4.
"""

from functools import cache
from importlib.resources import files

import yaml


def _text(name: str) -> str:
    return (files("hausbuch.rules.advice") / name).read_text(encoding="utf-8")


@cache
def rules() -> dict:
    """advice_rules.yaml as read; `version` names it."""
    data = yaml.safe_load(_text("advice_rules.yaml"))
    if not str(data.get("version", "")).startswith("advice-rules-"):
        raise ValueError("advice_rules.yaml: no version")
    return data


@cache
def lexicon() -> tuple[str, frozenset[str]]:
    """advice_lexicon.yaml: its version and every name of every list, lower-case."""
    data = yaml.safe_load(_text("advice_lexicon.yaml"))
    version = data.pop("version")
    names: set[str] = set()
    for group, entries in data.items():
        for entry in entries:
            if entry != entry.lower():
                raise ValueError(f"advice_lexicon.yaml: {group}: {entry!r} is not lower-case")
            names.add(entry)
    return version, frozenset(names)


@cache
def vocabulary() -> tuple[str, frozenset[str]]:
    """advice_vocabulary.txt: its version and its words (one lower-case form per line)."""
    version = None
    words: set[str] = set()
    for raw in _text("advice_vocabulary.txt").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("version:"):
            version = line.split(":", 1)[1].strip()
            continue
        if line != line.lower() or " " in line:
            raise ValueError(f"advice_vocabulary.txt: {line!r} is not one lower-case word")
        words.add(line)
    if version is None:
        raise ValueError("advice_vocabulary.txt: no version")
    return version, frozenset(words)
