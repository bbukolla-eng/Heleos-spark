"""Expose controller, guard, and review-gate suites to the public CI command."""

from pathlib import Path
import unittest


def load_tests(_loader, _tests, _pattern):
    root = Path(__file__).resolve().parent
    suites = unittest.TestSuite()
    for directory, pattern in (
        ("cross-review", "test_cross_review_protocol.py"),
        ("loop-controller", "test_*.py"),
        ("worker-run-guard", "test_*.py"),
    ):
        path = root / directory
        suites.addTests(unittest.TestLoader().discover(
            start_dir=str(path), pattern=pattern, top_level_dir=str(path)))
    return suites
