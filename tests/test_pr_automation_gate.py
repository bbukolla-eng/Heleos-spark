import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.github import pr_automation_gate as gate  # noqa: E402


def run(name, conclusion):
    return {"name": name, "conclusion": conclusion}


class Evaluate(unittest.TestCase):
    def test_passes_when_all_default_signals_are_green(self):
        result = gate.evaluate(
            [
                run("checks", "success"),
                run("ai-reviewers", "success"),
                run("copilot-pull-request-reviewer", "success"),
                run("ECC Tools Review", "success"),
                run("Amazon Q Developer", "success"),
            ],
            gate.DEFAULT_REQUIRED,
        )
        self.assertTrue(result["all_ok"])
        self.assertEqual(result["missing"], [])

    def test_fails_when_required_signal_is_missing(self):
        result = gate.evaluate(
            [
                run("checks", "success"),
                run("ai-reviewers", "success"),
                run("copilot-pull-request-reviewer", "success"),
            ],
            gate.DEFAULT_REQUIRED,
        )
        self.assertFalse(result["all_ok"])
        self.assertEqual(result["missing"], ["ecc", "amazon-q"])

    def test_non_success_check_does_not_count(self):
        result = gate.evaluate(
            [run("Amazon Q Developer", "failure")],
            ("amazon-q",),
        )
        self.assertFalse(result["all_ok"])
        self.assertEqual(result["missing"], ["amazon-q"])

    def test_explicit_unknown_required_value_uses_literal_match(self):
        result = gate.evaluate([run("my custom gate", "success")], ("my custom gate",))
        self.assertTrue(result["all_ok"])


class Cli(unittest.TestCase):
    def test_cli_returns_zero_when_green(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "runs.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump([run("checks", "success"), run("ai-reviewers", "success")], handle)
            code = gate.main(["evaluate", "--check-runs", path, "--required", "checks", "--required", "codex"])
            self.assertEqual(code, 0)

    def test_cli_returns_two_when_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "runs.json")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump([run("checks", "success")], handle)
            code = gate.main(["evaluate", "--check-runs", path, "--required", "checks", "--required", "ecc"])
            self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
