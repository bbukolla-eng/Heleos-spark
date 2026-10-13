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

    def test_containing_name_does_not_satisfy_signal(self):
        result = gate.evaluate(
            [
                run("prechecks", "success"),
                run("codex-lint", "success"),
            ],
            ("checks", "codex"),
        )
        self.assertFalse(result["all_ok"])
        self.assertEqual(result["missing"], ["checks", "codex"])

    def test_ai_reviewers_satisfies_codex_but_not_copilot(self):
        result = gate.evaluate(
            [run("ai-reviewers", "success")],
            ("codex", "copilot"),
        )
        self.assertFalse(result["all_ok"])
        self.assertEqual(result["missing"], ["copilot"])

    def test_stale_success_does_not_satisfy_signal(self):
        result = gate.evaluate(
            [
                {"name": "checks", "conclusion": "failure", "id": 9},
                {"name": "checks", "conclusion": "success", "id": 3},
                {
                    "name": "codex",
                    "conclusion": None,
                    "status": "in_progress",
                    "id": 8,
                    "started_at": "2026-09-29T13:00:00Z",
                },
                {
                    "name": "codex",
                    "conclusion": "success",
                    "id": 4,
                    "started_at": "2026-09-29T12:00:00Z",
                },
            ],
            ("checks", "codex"),
        )
        self.assertFalse(result["all_ok"])
        self.assertEqual(result["missing"], ["checks", "codex"])
        self.assertEqual(result["successful_checks"], [])

    def test_later_started_at_outranks_an_older_success(self):
        result = gate.evaluate(
            [
                {"name": "checks", "conclusion": "success", "started_at": "2026-09-29T12:00:00Z"},
                {
                    "name": "checks",
                    "conclusion": None,
                    "status": "in_progress",
                    "started_at": "2026-09-29T13:00:00Z",
                },
            ],
            ("checks",),
        )
        self.assertFalse(result["all_ok"])
        self.assertEqual(result["missing"], ["checks"])

    def test_latest_success_replaces_earlier_failure(self):
        result = gate.evaluate(
            [
                {"name": "checks", "conclusion": "failure", "id": 2},
                {"name": "checks", "conclusion": "success", "id": 5},
            ],
            ("checks",),
        )
        self.assertTrue(result["all_ok"])
        self.assertEqual(result["missing"], [])
        self.assertEqual(result["successful_checks"], ["checks"])


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
