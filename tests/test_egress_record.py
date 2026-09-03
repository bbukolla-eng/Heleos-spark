import json
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.egress import record  # noqa: E402

SEVEN = ("provider", "purpose", "data_class", "source_hashes", "policy_decision", "time", "result_ref")


class EgressRecord(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "docs", "policies"))
        os.makedirs(os.path.join(self.root, "docs", "decisions"))
        self.write("docs/policies/egress.md", "# Egress policy\n")
        self.write("docs/decisions/2026-09-03-egress-policy.md", "# Decision 12\n")
        self.event = os.path.join(self.root, "event.json")
        self.write("event.json", '{"pull_request": {"number": 7}}')
        for command in (
            ["git", "init", "-q", "-b", "main"],
            ["git", "-c", "user.email=t@example.org", "-c", "user.name=t", "add", "-A"],
            ["git", "-c", "user.email=t@example.org", "-c", "user.name=t", "commit", "-qm", "base"],
        ):
            subprocess.run(command, cwd=self.root, check=True, capture_output=True)
        self.env = {
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "owner/repo",
            "GITHUB_RUN_ID": "42",
            "GITHUB_RUN_ATTEMPT": "1",
            "GITHUB_JOB": "review",
            "GITHUB_SHA": "a" * 40,
            "GITHUB_EVENT_PATH": self.event,
        }
        self.argv = [
            "write", "--provider", "anthropic", "--purpose", "pull request review",
            "--data-class", "INTERNAL", "--decision", "docs/decisions/2026-09-03-egress-policy.md",
            "--rule", "claude-review-anthropic",
        ]

    def write(self, relative, text):
        with open(os.path.join(self.root, relative), "w", encoding="utf-8") as handle:
            handle.write(text)

    def build(self, **overrides):
        arguments = dict(
            provider="anthropic", purpose="pull request review", data_class="INTERNAL",
            policy="docs/policies/egress.md", decision="docs/decisions/2026-09-03-egress-policy.md",
            rule="claude-review-anthropic",
        )
        arguments.update(overrides)
        return record.build_record(self.root, record.read_env(self.env), **arguments)

    def test_record_carries_exactly_the_seven_spec_fields(self):
        self.assertEqual(tuple(self.build()), SEVEN)

    def test_source_hashes_name_the_tree_the_commit_and_the_event_payload(self):
        labels = [entry.split(":")[0] for entry in self.build()["source_hashes"]]
        self.assertEqual(labels, ["checkout-tree", "checkout-commit", "event-payload"])

    def test_no_secret_in_the_environment_can_reach_the_record(self):
        polluted = dict(self.env)
        polluted["CLAUDE_CODE_OAUTH_TOKEN"] = "sk-ant-oat01-" + "z" * 40
        polluted["ANTHROPIC_API_KEY"] = "sk-ant-api03-" + "y" * 40
        polluted["GITHUB_TOKEN"] = "ghp_" + "x" * 36
        line = record.serialise(record.build_record(
            self.root, record.read_env(polluted), provider="anthropic", purpose="review",
            data_class="INTERNAL", policy="docs/policies/egress.md",
            decision="docs/decisions/2026-09-03-egress-policy.md", rule="r",
        ))
        for secret in ("sk-ant-oat01", "sk-ant-api03", "ghp_"):
            self.assertNotIn(secret, line)

    def test_a_secret_shaped_value_is_refused_and_nothing_is_written(self):
        out = os.path.join(self.root, "egress.jsonl")
        argv = list(self.argv) + ["--purpose", "leak ghp_" + "x" * 36, "--out", out]
        self.assertEqual(record.main(argv, env=self.env, root=self.root), 1)
        self.assertFalse(os.path.exists(out))

    def test_check_fails_when_the_decision_record_is_not_in_the_checkout(self):
        failures = record.check_preconditions(
            self.root, "docs/policies/egress.md", "docs/decisions/absent.md", "INTERNAL", ("INTERNAL",)
        )
        self.assertEqual(failures, ["check: decision file missing from the checkout: docs/decisions/absent.md"])

    def test_check_refuses_a_class_this_caller_may_not_send(self):
        failures = record.check_preconditions(
            self.root, "docs/policies/egress.md", "docs/decisions/2026-09-03-egress-policy.md",
            "PROJECT_CONFIDENTIAL", ("PUBLIC", "INTERNAL"),
        )
        self.assertEqual(len(failures), 1)
        self.assertIn("may send PUBLIC, INTERNAL", failures[0])

    def test_secret_class_is_refused_even_when_the_caller_allows_it(self):
        failures = record.check_preconditions(
            self.root, "docs/policies/egress.md", "docs/decisions/2026-09-03-egress-policy.md",
            "SECRET", ("SECRET",),
        )
        self.assertEqual(failures, ["check: SECRET may never be submitted to any provider"])

    def test_write_appends_one_json_line_and_a_summary_table(self):
        out = os.path.join(self.root, "egress.jsonl")
        summary = os.path.join(self.root, "summary.md")
        argv = list(self.argv) + ["--out", out, "--summary", summary, "--session-id", "s-1",
                                  "--conclusion", "success"]
        self.assertEqual(record.main(argv, env=self.env, root=self.root), 0)
        with open(out, encoding="utf-8") as handle:
            lines = handle.read().splitlines()
        self.assertEqual(len(lines), 1)
        written = json.loads(lines[0])
        self.assertEqual(written["result_ref"]["run_url"],
                         "https://github.com/owner/repo/actions/runs/42/attempts/1")
        self.assertEqual(written["result_ref"]["provider_session_id"], "s-1")
        self.assertEqual(written["policy_decision"]["outcome"], "allow")
        with open(summary, encoding="utf-8") as handle:
            self.assertIn("| `data_class` | INTERNAL |", handle.read())

    def test_write_records_a_refused_decision_rather_than_no_line_at_all(self):
        out = os.path.join(self.root, "egress.jsonl")
        argv = ["write", "--provider", "anthropic", "--purpose", "pull request review",
                "--data-class", "INTERNAL", "--decision", "docs/decisions/absent.md",
                "--rule", "claude-review-anthropic", "--out", out]
        self.assertEqual(record.main(argv, env=self.env, root=self.root), 1)
        with open(out, encoding="utf-8") as handle:
            written = json.loads(handle.read().strip())
        self.assertEqual(written["policy_decision"]["outcome"], "refused")
        self.assertEqual(written["policy_decision"]["decision_sha256"], "absent")


if __name__ == "__main__":
    unittest.main()
