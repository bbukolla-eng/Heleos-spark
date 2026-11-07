"""Run every tests/ root from the repository discover command.

`python -m unittest discover -s tests` skips a directory with no
`__init__.py`. `drawing-workspace` and `provider-adapters` also cannot be
packages, because a hyphen is not a Python identifier. The checks job uses
that discover command, so `load_tests` is what executes the other roots.

A new directory under `tests/` fails this module until it is added to
`WIRED_ROOTS` or `NOT_TEST_ROOTS`.
"""

import os
import sys
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent

WIRED_ROOTS = (
    "continuity",
    "drawing-workspace",
    "packaging",
    "provider-adapters",
    "research",
)

NOT_TEST_ROOTS = (
    "fixtures",
    "verification",
)

BROAD_PATTERNS = ("test*.py", "test_*.py")


def _child_directories():
    names = []
    for entry in TESTS.iterdir():
        if not entry.is_dir() or entry.name.startswith(".") or entry.name == "__pycache__":
            continue
        names.append(entry.name)
    return sorted(names)


def _unclassified():
    known = set(WIRED_ROOTS) | set(NOT_TEST_ROOTS)
    return [name for name in _child_directories() if name not in known]


def _missing_wired():
    return [name for name in WIRED_ROOTS if not (TESTS / name).is_dir()]


def _count(suite):
    total = 0
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            total += _count(item)
        else:
            total += 1
    return total


def _failed(method_name, message):
    def test(self):
        self.fail(message)

    case = type("DiscoveryContract", (unittest.TestCase,), {method_name: test})
    return unittest.defaultTestLoader.loadTestsFromTestCase(case)


def _discover(root, pattern):
    before = list(sys.path)
    nested = unittest.TestLoader()
    try:
        return nested.discover(root, pattern=pattern, top_level_dir=root)
    finally:
        added = os.path.abspath(root)
        if added not in before:
            while added in sys.path:
                sys.path.remove(added)


def load_tests(loader, standard_tests, pattern):
    suite = unittest.TestSuite()
    suite.addTests(standard_tests)
    missing = _missing_wired()
    unclassified = _unclassified()
    if missing or unclassified:
        detail = []
        if missing:
            detail.append("missing wired roots: " + ", ".join(missing))
        if unclassified:
            detail.append(
                "unclassified tests/ directories: "
                + ", ".join(unclassified)
                + "; add each to WIRED_ROOTS or NOT_TEST_ROOTS"
            )
        suite.addTests(_failed("test_tests_directories_are_classified", "; ".join(detail)))
        return suite

    pattern = pattern or "test*.py"
    for name in WIRED_ROOTS:
        root = str(TESTS / name)
        found = _discover(root, pattern)
        suite.addTests(found)
        if _count(found) == 0 and pattern in BROAD_PATTERNS:
            suite.addTests(
                _failed(
                    "test_" + name.replace("-", "_") + "_has_tests",
                    name + " matched no tests for pattern " + pattern,
                )
            )
    return suite


class RepositoryDiscovery(unittest.TestCase):
    def test_every_tests_subdirectory_is_classified(self):
        self.assertEqual(_missing_wired(), [])
        self.assertEqual(
            _unclassified(),
            [],
            "add the new directory to WIRED_ROOTS or NOT_TEST_ROOTS",
        )
