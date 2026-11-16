"""`--shard i/n`: run one of n disjoint parts of the suite (CI runs the parts as parallel jobs).

Whole test files are assigned to parts, so a file's module fixtures run once; the files are
spread by their test count, largest first, each to the part with the fewest tests so far.
The assignment depends only on the collected items, so every xdist worker (and the
controller) deselects the same tests, and the n parts together are exactly the full suite.
"""

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--shard", default=None, help="run part i of n, written i/n (1-based)")


def _parse(value: str) -> tuple[int, int]:
    index, _, total = value.partition("/")
    i, n = int(index), int(total)
    if not 1 <= i <= n:
        raise pytest.UsageError(f"--shard {value}: expected i/n with 1 <= i <= n")
    return i, n


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    value = config.getoption("--shard")
    if not value:
        return
    index, total = _parse(value)
    per_file: dict[str, int] = {}
    for item in items:
        path = item.nodeid.split("::", 1)[0]
        per_file[path] = per_file.get(path, 0) + 1
    load = [0] * total
    part: dict[str, int] = {}
    for path, count in sorted(per_file.items(), key=lambda kv: (-kv[1], kv[0])):
        target = min(range(total), key=lambda k: (load[k], k))
        part[path] = target + 1
        load[target] += count
    keep, drop = [], []
    for item in items:
        (keep if part[item.nodeid.split("::", 1)[0]] == index else drop).append(item)
    if drop:
        config.hook.pytest_deselected(items=drop)
        items[:] = keep
