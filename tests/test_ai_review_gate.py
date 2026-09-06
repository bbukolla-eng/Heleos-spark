"""Acknowledgement matching for the AI review gate.

Codex and Copilot must acknowledge the current head. ECC Tools is detected and
printed as advisory and never fails the job. A review, issue comment, or inline
comment counts when it is bound to that SHA or created at or after the head
push. Copilot may also pass via its check-run.
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.github import ai_review_gate as gate  # noqa: E402

HEAD = "abc123def456"
OLDER = "2026-09-06T10:00:00Z"
SINCE = "2026-09-06T12:00:00Z"
NEWER = "2026-09-06T12:05:00Z"


def user(login):
    return {"login": login}


def review(login, submitted_at=NEWER, commit_id=None, state="COMMENTED"):
    item = {"user": user(login), "submitted_at": submitted_at, "state": state}
    if commit_id is not None:
        item["commit_id"] = commit_id
    return item


def comment(login, created_at=NEWER, body="looks fine", commit_id=None):
    item = {"user": user(login), "created_at": created_at, "body": body}
    if commit_id is not None:
        item["commit_id"] = commit_id
    return item


def check_run(name="copilot-pull-request-reviewer", conclusion="success", head_sha=HEAD):
    return {"name": name, "conclusion": conclusion, "head_sha": head_sha}


def evaluate(**overrides):
    payload = {
        "reviews": [],
        "issue_comments": [],
        "review_comments": [],
        "check_runs": [],
        "head_sha": HEAD,
        "head_since": SINCE,
    }
    payload.update(overrides)
    return gate.evaluate(**payload)


class LoginMatching(unittest.TestCase):
    def test_bot_suffix_is_optional(self):
        self.assertTrue(gate.login_matches("chatgpt-codex-connector", "chatgpt-codex-connector[bot]"))
        self.assertTrue(gate.login_matches("ECC-TOOLS[BOT]", "ecc-tools[bot]"))

    def test_unrelated_login_does_not_match(self):
        self.assertFalse(gate.login_matches("random-user", "ecc-tools[bot]"))


class HeadBinding(unittest.TestCase):
    def test_commit_id_on_this_head_counts(self):
        item = review("chatgpt-codex-connector[bot]", submitted_at=OLDER, commit_id=HEAD)
        self.assertTrue(gate.is_for_head(item, HEAD, SINCE))

    def test_created_at_on_or_after_head_push_counts(self):
        item = comment("ecc-tools[bot]", created_at=SINCE)
        self.assertTrue(gate.is_for_head(item, HEAD, SINCE))

    def test_older_comment_without_this_head_does_not_count(self):
        item = comment("ecc-tools[bot]", created_at=OLDER, commit_id="other")
        self.assertFalse(gate.is_for_head(item, HEAD, SINCE))

    def test_pending_review_is_ignored(self):
        items = [review("chatgpt-codex-connector[bot]", commit_id=HEAD, state="PENDING")]
        self.assertFalse(gate.bot_acknowledged(items, gate.CODEX_BOT, HEAD, SINCE))


class CodexAcknowledgement(unittest.TestCase):
    def test_review_on_this_head_is_enough(self):
        result = evaluate(reviews=[review("chatgpt-codex-connector[bot]", commit_id=HEAD)])
        self.assertTrue(result["codex"])

    def test_issue_comment_after_push_is_enough(self):
        result = evaluate(issue_comments=[comment("chatgpt-codex-connector[bot]", created_at=NEWER)])
        self.assertTrue(result["codex"])

    def test_inline_comment_on_this_head_is_enough(self):
        result = evaluate(
            review_comments=[comment("chatgpt-codex-connector[bot]", commit_id=HEAD, created_at=OLDER)]
        )
        self.assertTrue(result["codex"])

    def test_stale_comment_on_older_head_is_not_enough(self):
        result = evaluate(
            issue_comments=[comment("chatgpt-codex-connector[bot]", created_at=OLDER)],
            reviews=[review("chatgpt-codex-connector[bot]", submitted_at=OLDER, commit_id="old")],
        )
        self.assertFalse(result["codex"])


class EccAcknowledgement(unittest.TestCase):
    def test_review_or_comment_on_this_head_is_detected(self):
        result = evaluate(reviews=[review("ecc-tools[bot]", commit_id=HEAD)])
        self.assertTrue(result["ecc"])
        result = evaluate(issue_comments=[comment("ecc-tools[bot]")])
        self.assertTrue(result["ecc"])

    def test_absent_ecc_is_advisory_not_missing(self):
        result = evaluate(
            reviews=[review("chatgpt-codex-connector[bot]", commit_id=HEAD)],
            check_runs=[check_run()],
        )
        self.assertFalse(result["ecc"])
        self.assertTrue(result["all_ok"])
        self.assertEqual(result["missing"], [])
        self.assertNotIn("ecc", result["missing"])


class CopilotAcknowledgement(unittest.TestCase):
    def test_review_on_this_head_is_enough(self):
        result = evaluate(reviews=[review("copilot-pull-request-reviewer[bot]", commit_id=HEAD)])
        self.assertTrue(result["copilot"])

    def test_successful_check_run_on_this_head_is_enough(self):
        result = evaluate(check_runs=[check_run()])
        self.assertTrue(result["copilot"])

    def test_wrapped_check_runs_object_is_unwrapped(self):
        result = evaluate(check_runs={"total_count": 1, "check_runs": [check_run()]})
        self.assertTrue(result["copilot"])

    def test_failed_or_other_check_is_not_enough(self):
        result = evaluate(check_runs=[check_run(conclusion="failure")])
        self.assertFalse(result["copilot"])
        result = evaluate(check_runs=[check_run(name="checks", conclusion="success")])
        self.assertFalse(result["copilot"])


class GatePassFail(unittest.TestCase):
    def test_pass_requires_codex_and_copilot_only(self):
        result = evaluate(
            reviews=[review("chatgpt-codex-connector[bot]", commit_id=HEAD)],
            check_runs=[check_run()],
        )
        self.assertTrue(result["all_ok"])
        self.assertFalse(result["ecc"])
        self.assertEqual(result["missing"], [])

    def test_failure_lists_required_reviewers_only(self):
        result = evaluate(reviews=[review("ecc-tools[bot]", commit_id=HEAD)])
        self.assertFalse(result["all_ok"])
        self.assertTrue(result["ecc"])
        self.assertEqual(result["missing"], ["codex", "copilot"])
        self.assertNotIn("ecc", result["missing"])


class CodexPing(unittest.TestCase):
    def test_ping_when_codex_has_not_spoken_and_nobody_asked(self):
        self.assertTrue(
            gate.should_ping_codex(
                reviews=[],
                issue_comments=[],
                review_comments=[],
                head_sha=HEAD,
                head_since=SINCE,
            )
        )

    def test_no_ping_when_codex_already_acknowledged(self):
        self.assertFalse(
            gate.should_ping_codex(
                reviews=[review("chatgpt-codex-connector[bot]", commit_id=HEAD)],
                issue_comments=[],
                review_comments=[],
                head_sha=HEAD,
                head_since=SINCE,
            )
        )

    def test_no_second_ping_for_the_same_head(self):
        prior = comment("github-actions[bot]", created_at=NEWER, body="@codex review")
        self.assertFalse(
            gate.should_ping_codex(
                reviews=[],
                issue_comments=[prior],
                review_comments=[],
                head_sha=HEAD,
                head_since=SINCE,
            )
        )

    def test_new_head_may_ping_again(self):
        prior = comment("github-actions[bot]", created_at=OLDER, body="@codex review")
        self.assertTrue(
            gate.should_ping_codex(
                reviews=[],
                issue_comments=[prior],
                review_comments=[],
                head_sha=HEAD,
                head_since=SINCE,
            )
        )


class HeadSinceResolution(unittest.TestCase):
    def test_timeline_committed_event_for_this_sha_wins(self):
        timeline = [
            {"event": "committed", "sha": "other", "created_at": OLDER},
            {"event": "committed", "sha": HEAD, "created_at": SINCE},
        ]
        commit = {"commit": {"committer": {"date": OLDER}}}
        self.assertEqual(gate.resolve_head_since(timeline, commit, HEAD), SINCE)

    def test_falls_back_to_committer_date(self):
        commit = {"commit": {"committer": {"date": OLDER}}}
        self.assertEqual(gate.resolve_head_since([], commit, HEAD), OLDER)


class WorkflowContract(unittest.TestCase):
    def test_stable_job_id_and_permissions(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            ".github",
            "workflows",
            "ai-review-gate.yml",
        )
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("\n  ai-reviewers:\n", text)
        self.assertIn("contents: read", text)
        self.assertIn("pull-requests: write", text)
        self.assertIn("issues: write", text)
        self.assertIn("checks: read", text)
        self.assertIn("github.event.pull_request.draft == false", text)
        self.assertIn("@codex review", text)
        self.assertIn("pass: Codex OK, Copilot OK", text)
        self.assertNotIn("secrets.", text.replace("secrets.GITHUB_TOKEN", ""))


class CliEvaluate(unittest.TestCase):
    def test_cli_passes_without_ecc_and_prints_advisory(self):
        with tempfile.TemporaryDirectory() as tmp:
            def write(name, value):
                path = os.path.join(tmp, name)
                with open(path, "w", encoding="utf-8") as handle:
                    json.dump(value, handle)
                return path

            reviews = write(
                "reviews.json",
                [review("chatgpt-codex-connector[bot]", commit_id=HEAD)],
            )
            empty = write("empty.json", [])
            checks = write("checks.json", [check_run()])
            from io import StringIO
            from contextlib import redirect_stdout

            captured = StringIO()
            with redirect_stdout(captured):
                code = gate.main(
                    [
                        "evaluate",
                        "--head-sha",
                        HEAD,
                        "--head-since",
                        SINCE,
                        "--reviews",
                        reviews,
                        "--issue-comments",
                        empty,
                        "--review-comments",
                        empty,
                        "--check-runs",
                        checks,
                    ]
                )
            out = captured.getvalue()
            self.assertEqual(code, 0)
            self.assertIn("ECC advisory: absent", out)
            payload = json.loads(out.splitlines()[0])
            self.assertNotIn("ecc", payload["missing"])
            self.assertFalse(payload["ecc"])

            missing = write("reviews.json", [review("ecc-tools[bot]", commit_id=HEAD)])
            captured = StringIO()
            with redirect_stdout(captured):
                code = gate.main(
                    [
                        "evaluate",
                        "--head-sha",
                        HEAD,
                        "--head-since",
                        SINCE,
                        "--reviews",
                        missing,
                        "--issue-comments",
                        empty,
                        "--review-comments",
                        empty,
                        "--check-runs",
                        empty,
                    ]
                )
            out = captured.getvalue()
            self.assertEqual(code, 2)
            self.assertIn("missing: codex, copilot", out)
            self.assertIn("ECC advisory: present", out)
            self.assertNotIn("ecc", json.loads(out.splitlines()[0])["missing"])


if __name__ == "__main__":
    unittest.main()
