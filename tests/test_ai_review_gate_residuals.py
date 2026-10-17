"""Residual pins for #15 AI review-gate paths the landing suite left open.

#15 already covers login suffix matching, SHA-or-time head binding, Codex/Copilot
acknowledgement, ECC-advisory-not-missing, ping-once-per-head, committed-event
head-since, evaluate CLI, and the stable job-id/permissions contract.

These cases are the remaining production branches with meaningful blast radius:
a Copilot success on an older SHA must not open the new head; GitHub's other
SHA fields (original_commit_id, timeline commit_id, nested commit.sha) must still bind;
force-push timeline events must move head-since; the workflow's CLI exit codes
for ping and head-since must stay the contract the bash job depends on.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.github import ai_review_gate as gate  # noqa: E402

HEAD = "abc123def456"
OLDER = "2026-09-06T10:00:00Z"
SINCE = "2026-09-06T12:00:00Z"
NEWER = "2026-09-06T12:05:00Z"
LATER = "2026-09-06T12:10:00Z"


def user(login):
    return {"login": login}


def comment(login, created_at=NEWER, body="looks fine", **extra):
    item = {"user": user(login), "created_at": created_at, "body": body}
    item.update(extra)
    return item


def force_push_event(created_at, commit_id=HEAD):
    """REST issue-timeline head_ref_force_pushed event.

    Keys match the event captured from pull request 8 (id 30490119913,
    created 2026-09-03). That payload set commit_id to a SHA and had no
    push-webhook after field.
    """
    return {
        "id": 30490119913,
        "node_id": "HRFPE_lADOUFcztc8AAAABPgQpFM8AAAAHGVpO6Q",
        "url": (
            "https://api.github.com/repos/bbukolla-eng/Heleos-spark/"
            "issues/events/30490119913"
        ),
        "actor": {"login": "dependabot[bot]"},
        "event": "head_ref_force_pushed",
        "commit_id": commit_id,
        "commit_url": (
            "https://api.github.com/repos/bbukolla-eng/Heleos-spark/commits/"
            + commit_id
        ),
        "created_at": created_at,
        "performed_via_github_app": None,
    }


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


class CopilotHeadBinding(unittest.TestCase):
    def test_copilot_success_on_another_sha_does_not_pass(self):
        result = evaluate(
            check_runs=[
                {
                    "name": "copilot-pull-request-reviewer",
                    "conclusion": "success",
                    "head_sha": "oldhead000",
                }
            ]
        )
        self.assertFalse(result["copilot"])
        self.assertIn("copilot", result["missing"])


class AlternateShaFields(unittest.TestCase):
    def test_original_commit_id_binds_an_older_inline_comment(self):
        item = comment(
            "chatgpt-codex-connector[bot]",
            created_at=OLDER,
            original_commit_id=HEAD,
        )
        self.assertTrue(gate.is_for_head(item, HEAD, SINCE))
        result = evaluate(review_comments=[item])
        self.assertTrue(result["codex"])

    def test_commit_id_binds_a_force_push_event(self):
        event = force_push_event(SINCE)
        self.assertNotIn("after", event)
        self.assertTrue(gate.is_for_head(event, HEAD, NEWER))

    def test_nested_commit_sha_binds(self):
        item = {
            "user": user("chatgpt-codex-connector[bot]"),
            "created_at": OLDER,
            "commit": {"sha": HEAD},
        }
        self.assertTrue(gate.is_for_head(item, HEAD, SINCE))


class ForcePushHeadSince(unittest.TestCase):
    def test_force_push_timeline_event_wins_over_committer_date(self):
        timeline = [force_push_event(SINCE)]
        commit = {"commit": {"committer": {"date": OLDER}}}
        self.assertEqual(gate.resolve_head_since(timeline, commit, HEAD), SINCE)

    def test_later_matching_timeline_event_wins(self):
        timeline = [
            {"event": "committed", "sha": HEAD, "created_at": SINCE},
            force_push_event(LATER),
        ]
        commit = {"commit": {"committer": {"date": OLDER}}}
        self.assertEqual(gate.resolve_head_since(timeline, commit, HEAD), LATER)


class LoginAndPayloadShape(unittest.TestCase):
    def test_empty_login_does_not_match_a_bot(self):
        self.assertFalse(gate.login_matches("", gate.CODEX_BOT))
        self.assertFalse(gate.login_matches(None, gate.CODEX_BOT))
        self.assertFalse(gate.login_matches(gate.CODEX_BOT, ""))

    def test_actor_login_is_accepted(self):
        item = {
            "actor": {"login": "chatgpt-codex-connector[bot]"},
            "created_at": NEWER,
            "body": "reviewed",
        }
        self.assertTrue(gate.bot_acknowledged([item], gate.CODEX_BOT, HEAD, SINCE))

    def test_unbound_item_without_head_since_does_not_count(self):
        item = comment("chatgpt-codex-connector[bot]", created_at=NEWER)
        self.assertFalse(gate.is_for_head(item, HEAD, ""))

    def test_wrapped_reviews_object_is_unwrapped(self):
        result = evaluate(
            reviews={
                "reviews": [
                    {
                        "user": user("chatgpt-codex-connector[bot]"),
                        "submitted_at": NEWER,
                        "state": "COMMENTED",
                        "commit_id": HEAD,
                    }
                ]
            }
        )
        self.assertTrue(result["codex"])


class WorkflowCliContract(unittest.TestCase):
    def _write(self, tmp, name, value):
        path = os.path.join(tmp, name)
        with open(path, "w", encoding="utf-8") as handle:
            if value is None:
                handle.write("")
            else:
                json.dump(value, handle)
        return path

    def _run_script(self, args):
        script = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "tools",
            "github",
            "ai_review_gate.py",
        )
        return subprocess.run(
            [sys.executable, script, *args],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_cli_should_ping_codex_exit_codes(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = self._write(tmp, "empty.json", [])
            # The workflow's if sees the process status, not main()'s return value.
            ping = self._run_script(
                [
                    "should-ping-codex",
                    "--head-sha",
                    HEAD,
                    "--head-since",
                    SINCE,
                    "--reviews",
                    empty,
                    "--issue-comments",
                    empty,
                    "--review-comments",
                    empty,
                ]
            )
            self.assertEqual(ping.returncode, 0, ping.stderr)
            self.assertEqual(ping.stdout.strip(), "ping")

            prior = self._write(
                tmp,
                "comments.json",
                [comment("github-actions[bot]", body="@codex review")],
            )
            skip = self._run_script(
                [
                    "should-ping-codex",
                    "--head-sha",
                    HEAD,
                    "--head-since",
                    SINCE,
                    "--reviews",
                    empty,
                    "--issue-comments",
                    prior,
                    "--review-comments",
                    empty,
                ]
            )
            self.assertEqual(skip.returncode, 2, skip.stderr)
            self.assertEqual(skip.stdout.strip(), "skip")

    def test_cli_head_since_prints_the_resolved_timestamp(self):
        with tempfile.TemporaryDirectory() as tmp:
            timeline = self._write(
                tmp,
                "timeline.json",
                [force_push_event(SINCE)],
            )
            commit = self._write(
                tmp,
                "commit.json",
                {"commit": {"committer": {"date": OLDER}}},
            )
            captured = StringIO()
            with redirect_stdout(captured):
                code = gate.main(
                    [
                        "head-since",
                        "--head-sha",
                        HEAD,
                        "--timeline",
                        timeline,
                        "--commit",
                        commit,
                    ]
                )
            self.assertEqual(code, 0)
            self.assertEqual(captured.getvalue().strip(), SINCE)

    def test_cli_head_since_exits_zero_with_empty_stdout_when_unresolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            timeline = self._write(tmp, "timeline.json", [])
            commit = self._write(
                tmp,
                "commit.json",
                {"commit": {"committer": {}}},
            )
            captured = StringIO()
            with redirect_stdout(captured):
                code = gate.main(
                    [
                        "head-since",
                        "--head-sha",
                        HEAD,
                        "--timeline",
                        timeline,
                        "--commit",
                        commit,
                    ]
                )
            # Command substitution plus the workflow's -z guard sees this as empty.
            self.assertEqual(code, 0)
            self.assertEqual(captured.getvalue().strip(), "")

    def test_empty_json_file_is_treated_as_no_items(self):
        with tempfile.TemporaryDirectory() as tmp:
            blank = self._write(tmp, "blank.json", None)
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
                        blank,
                        "--issue-comments",
                        blank,
                        "--review-comments",
                        blank,
                        "--check-runs",
                        blank,
                    ]
                )
            self.assertEqual(code, 2)
            self.assertIn("missing: codex, copilot", captured.getvalue())


class WorkflowLoopContract(unittest.TestCase):
    def test_timeout_and_cli_commands_stay_in_the_job(self):
        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            ".github",
            "workflows",
            "ai-review-gate.yml",
        )
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        self.assertIn("DEADLINE=$((SECONDS + 720))", text)
        self.assertIn('if [ "${status}" -ne 2 ]; then', text)
        self.assertIn("should-ping-codex", text)
        self.assertIn("head-since", text)
        self.assertIn("fail: timed out waiting for required reviewers (Codex, Copilot)", text)


if __name__ == "__main__":
    unittest.main()
