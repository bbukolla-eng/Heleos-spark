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
        self.write("docs/decisions/2026-09-03-egress-policy.md", "# Decision 12\n\n**Status:** APPROVED\n\n**Decided by:** Bekim Bukolla **Date:** 2026-09-03\n")  # signed fixture
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



class GitFailureDegradesRatherThanCrashes(unittest.TestCase):
    """Write mode runs after the submission, so it must never lose a record to a git failure."""

    def setUp(self):
        self.root = tempfile.mkdtemp()  # deliberately not a git repository
        os.makedirs(os.path.join(self.root, "docs", "policies"))
        os.makedirs(os.path.join(self.root, "docs", "decisions"))
        with open(os.path.join(self.root, "docs/policies/egress.md"), "w", encoding="utf-8") as handle:
            handle.write("x\n")
        with open(os.path.join(self.root, "docs/decisions/d.md"), "w", encoding="utf-8") as handle:
            handle.write("**Status:** APPROVED\n\n**Decided by:** Bekim Bukolla **Date:** 2026-09-03\n")

    def test_git_tree_outside_a_repository_returns_unavailable(self):
        self.assertEqual(record.git_tree(self.root), "unavailable")

    def test_write_still_emits_a_record_when_git_fails(self):
        out = os.path.join(self.root, "egress.jsonl")
        code = record.main(
            ["write", "--provider", "anthropic", "--purpose", "p", "--data-class", "INTERNAL",
             "--decision", "docs/decisions/d.md", "--rule", "r", "--out", out],
            env={}, root=self.root,
        )
        self.assertEqual(code, 0)
        with open(out, encoding="utf-8") as handle:
            written = json.loads(handle.read().strip())
        self.assertEqual(set(written), set(record.FIELDS))  # written sorted, so compare the set
        self.assertIn("checkout-tree:sha1:unavailable", written["source_hashes"])


class ExistenceIsNotApproval(unittest.TestCase):
    """The gate must refuse a decision record that is present but unsigned."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "docs", "policies"))
        os.makedirs(os.path.join(self.root, "docs", "decisions"))
        self.write("docs/policies/egress.md", "policy\n")

    def write(self, rel, text):
        with open(os.path.join(self.root, rel), "w", encoding="utf-8") as handle:
            handle.write(text)

    def gate(self):
        return record.main(
            ["check", "--provider", "anthropic", "--purpose", "p", "--data-class", "INTERNAL",
             "--decision", "docs/decisions/d.md", "--rule", "r"],
            env={}, root=self.root,
        )

    def test_prepared_record_with_blank_determinations_does_not_open_the_gate(self):
        self.write("docs/decisions/d.md",
                   "# Decision 12\n\n**Status:** Awaiting the owner's determination.\n\n"
                   "Decision: ____\n\n**Decided by:** ____ **Date:** ____\n")
        self.assertEqual(self.gate(), 1)

    def test_approved_status_without_a_signer_does_not_open_the_gate(self):
        self.write("docs/decisions/d.md", "**Status:** APPROVED\n\n**Decided by:** ____ **Date:** ____\n")
        self.assertEqual(self.gate(), 1)

    def test_signer_without_an_approved_status_does_not_open_the_gate(self):
        self.write("docs/decisions/d.md", "**Decided by:** Bekim Bukolla **Date:** 2026-09-03\n")
        self.assertEqual(self.gate(), 1)

    def test_a_signed_record_opens_the_gate(self):
        self.write("docs/decisions/d.md",
                   "**Status:** APPROVED\n\n**Decided by:** Bekim Bukolla **Date:** 2026-09-03\n")
        self.assertEqual(self.gate(), 0)


class PreCallHashesSurviveACommitDuringTheRun(unittest.TestCase):
    """claude.yml commits during the action, so hashes taken afterwards would describe its output."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "docs", "policies"))
        os.makedirs(os.path.join(self.root, "docs", "decisions"))
        with open(os.path.join(self.root, "docs/policies/egress.md"), "w", encoding="utf-8") as handle:
            handle.write("policy\n")
        with open(os.path.join(self.root, "docs/decisions/d.md"), "w", encoding="utf-8") as handle:
            handle.write("**Status:** APPROVED\n\n**Decided by:** Bekim Bukolla **Date:** 2026-09-03\n")

    def test_write_uses_the_hashes_captured_before_the_call(self):
        precall = os.path.join(self.root, "precall.json")
        with open(precall, "w", encoding="utf-8") as handle:
            json.dump({"source_hashes": ["checkout-tree:sha1:beforethecall"]}, handle)
        out = os.path.join(self.root, "egress.jsonl")
        code = record.main(
            ["write", "--provider", "anthropic", "--purpose", "p", "--data-class", "INTERNAL",
             "--decision", "docs/decisions/d.md", "--rule", "r", "--precall", precall, "--out", out],
            env={}, root=self.root,
        )
        self.assertEqual(code, 0)
        with open(out, encoding="utf-8") as handle:
            written = json.loads(handle.read().strip())
        self.assertEqual(written["source_hashes"], ["checkout-tree:sha1:beforethecall"])

    def test_a_missing_snapshot_is_marked_rather_than_silently_recomputed(self):
        out = os.path.join(self.root, "egress.jsonl")
        record.main(
            ["write", "--provider", "anthropic", "--purpose", "p", "--data-class", "INTERNAL",
             "--decision", "docs/decisions/d.md", "--rule", "r",
             "--precall", os.path.join(self.root, "gone.json"), "--out", out],
            env={}, root=self.root,
        )
        with open(out, encoding="utf-8") as handle:
            written = json.loads(handle.read().strip())
        self.assertIn("precall-snapshot:absent", written["source_hashes"])

if __name__ == "__main__":
    unittest.main()


class RunningFromOutsideTheTreeItChecks(unittest.TestCase):
    """Both workflows copy the recorder to $RUNNER_TEMP, so its own location is not the checkout.

    The module derives ROOT from __file__. A copy therefore resolves every relative path against
    the temporary directory it was copied into and reports present files as missing, which turns
    a correctly signed decision record into a red job. The checkout must be named explicitly.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.elsewhere = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "docs", "policies"))
        os.makedirs(os.path.join(self.root, "docs", "decisions"))
        with open(os.path.join(self.root, "docs/policies/egress.md"), "w", encoding="utf-8") as handle:
            handle.write("# Egress policy\n")
        with open(os.path.join(self.root, "docs/decisions/d.md"), "w", encoding="utf-8") as handle:
            handle.write("# Decision\n\n**Status:** APPROVED\n\n**Decided by:** Bekim Bukolla **Date:** 2026-09-03\n")
        self.copy = os.path.join(self.elsewhere, "record.py")
        with open(record.__file__, encoding="utf-8") as source, open(self.copy, "w", encoding="utf-8") as target:
            target.write(source.read())

    def run_copy(self, *extra):
        argv = [sys.executable, self.copy, "check", "--provider", "anthropic", "--purpose", "p",
                "--data-class", "INTERNAL", "--decision", "docs/decisions/d.md", "--rule", "r"]
        return subprocess.run(argv + list(extra), capture_output=True, text=True, cwd=self.elsewhere)

    def test_without_root_the_copy_cannot_see_the_checkout(self):
        result = self.run_copy()
        self.assertEqual(result.returncode, 1)
        self.assertIn("policy file missing", result.stdout)

    def test_with_root_the_copy_checks_the_real_checkout(self):
        result = self.run_copy("--root", self.root)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("may receive INTERNAL", result.stdout)


class AnExampleSignatureIsNotASignature(unittest.TestCase):
    """The owner is told to sign by adding a line reading exactly '**Status:** APPROVED'.

    That instruction invites a paste-me example into the draft, and a bare regex cannot tell an
    example from a signature. A status line inside a fenced block or an HTML comment is
    illustration, not authorization.
    """

    def setUp(self):
        self.root = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.root, "docs"))

    def failures(self, body):
        with open(os.path.join(self.root, "docs/d.md"), "w", encoding="utf-8") as handle:
            handle.write(body)
        return record.approval_failures(self.root, "docs/d.md")

    def test_a_fenced_example_above_the_determinations_does_not_sign_the_record(self):
        """The shape the draft actually has: the how-to-sign section sits above section 6."""
        body = (
            "# Draft of Decision 12\n\nTo sign, replace the banner with:\n\n"
            "```markdown\n**Status:** APPROVED\n\n**Decided by:** Your Name **Date:** 2026-09-03\n```\n\n"
            "## 6. Decision\n\n**Status:** Draft\n\n**Decided by:** ____ **Date:** ____\n"
        )
        self.assertTrue(self.failures(body), "a fenced example above the determinations opened the gate")

    def test_a_tilde_fenced_example_above_the_determinations_does_not_sign_the_record(self):
        body = (
            "# Draft\n\n~~~\n**Status:** APPROVED\n\n**Decided by:** Someone **Date:** x\n~~~\n\n"
            "## 6. Decision\n\n**Status:** Draft\n\n**Decided by:** ____ **Date:** ____\n"
        )
        self.assertTrue(self.failures(body), "a tilde-fenced example opened the gate")

    def test_a_commented_example_above_the_determinations_does_not_sign_the_record(self):
        body = (
            "# Draft\n\n<!--\n**Status:** APPROVED\n\n**Decided by:** Someone **Date:** x\n-->\n\n"
            "## 6. Decision\n\n**Status:** Draft\n\n**Decided by:** ____ **Date:** ____\n"
        )
        self.assertTrue(self.failures(body), "a commented example opened the gate")

    def test_an_indented_code_block_example_does_not_sign_the_record(self):
        body = (
            "# Draft\n\nExample:\n\n    **Status:** APPROVED\n    **Decided by:** Someone **Date:** x\n\n"
            "## 6. Decision\n\n**Status:** Draft\n\n**Decided by:** ____ **Date:** ____\n"
        )
        self.assertTrue(self.failures(body), "an indented code block opened the gate")

    def test_a_shorter_nested_fence_does_not_end_the_block(self):
        """CommonMark: a closing fence must be at least as long as the one that opened it.

        A four-backtick block is exactly how a document shows a fenced example that itself
        contains a fence, which is what a signing instruction looks like. Collapsing every
        opener to three markers lets the inner fence close the outer block and spills the
        example into the prose the gate reads.
        """
        body = (
            "# Draft\n\n````markdown\n```\n**Status:** APPROVED\n\n"
            "**Decided by:** Someone **Date:** x\n````\n\n"
            "## 6. Decision\n\n**Status:** Draft\n\n**Decided by:** ____ **Date:** ____\n"
        )
        self.assertTrue(self.failures(body), "a shorter nested fence opened the gate")

    def test_a_fence_line_carrying_an_info_string_does_not_close_a_block(self):
        """A closing fence carries nothing after the markers, so this line is content."""
        body = (
            "# Draft\n\n```\n```python\n**Status:** APPROVED\n\n"
            "**Decided by:** Someone **Date:** x\n```\n\n"
            "## 6. Decision\n\n**Status:** Draft\n\n**Decided by:** ____ **Date:** ____\n"
        )
        self.assertTrue(self.failures(body), "an info-string line closed the block")

    def test_a_tilde_fence_is_not_closed_by_backticks(self):
        body = (
            "# Draft\n\n~~~\n```\n**Status:** APPROVED\n\n"
            "**Decided by:** Someone **Date:** x\n~~~\n\n"
            "## 6. Decision\n\n**Status:** Draft\n\n**Decided by:** ____ **Date:** ____\n"
        )
        self.assertTrue(self.failures(body), "backticks closed a tilde fence")

    def test_a_longer_closing_fence_still_closes_the_block(self):
        """Closing with more markers than the opener is legal, so prose after it is prose."""
        body = (
            "# Decision 12\n\n```\n**Status:** Draft\n`````\n\n"
            "**Status:** APPROVED\n\n**Decided by:** Bekim Bukolla **Date:** 2026-09-03\n"
        )
        self.assertEqual(self.failures(body), [])

    def test_a_real_signature_outside_any_fence_still_opens_the_gate(self):
        body = (
            "# Decision 12\n\n**Status:** APPROVED\n\n**Decided by:** Bekim Bukolla **Date:** 2026-09-03\n\n"
            "An illustration of the draft banner it replaced:\n\n```\n**Status:** Draft\n```\n"
        )
        self.assertEqual(self.failures(body), [])

    def test_todays_real_draft_is_still_refused(self):
        """The file the owner is actually told to copy must not open the gate as it stands."""
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        draft = os.path.join(here, "docs", "roadmap", "decision-12-egress-draft.md")
        if not os.path.exists(draft):
            self.skipTest("draft not present in this checkout")
        with open(draft, encoding="utf-8") as handle:
            self.assertTrue(self.failures(handle.read()), "the live draft opened the gate")
