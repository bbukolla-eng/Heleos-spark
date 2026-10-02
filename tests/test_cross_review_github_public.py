"""Include the PR snapshot adapter suite in the repository's CI discovery."""

from pathlib import Path
import unittest


def load_tests(_loader, _tests, _pattern):
    directory = Path(__file__).resolve().parent / "cross-review"
    return unittest.TestLoader().discover(
        start_dir=str(directory),
        pattern="test_cross_review_github.py",
        top_level_dir=str(directory),
    )
