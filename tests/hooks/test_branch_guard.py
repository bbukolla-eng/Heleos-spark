"""Tests for the Heleos-spark Claude Code branch guard.

Runs with the standard library only:

    python3 -m unittest discover -s tests -p 'test_*.py' -v

Two disposable repositories are created: one checked out on the lane branch
and one checked out on ``main``. Each policy table below states the expected
decision for a tool call in that state.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
HOOKS_DIR = os.path.join(REPO_ROOT, ".claude", "hooks")
sys.path.insert(0, HOOKS_DIR)

import branch_guard  # noqa: E402
import shell_scan  # noqa: E402

LANE = "claude/heleos-spark-branch-60gd5e"
ALLOW = 0
DENY = 2


def git(directory, *args):
    subprocess.run(["git", "-C", directory] + list(args), check=True, capture_output=True, text=True)


def make_fixture(base):
    """Create origin + two clones (on lane, on main). Returns (lane_dir, main_dir)."""
    origin = os.path.join(base, "bbukolla-eng", "Heleos-spark.git")
    os.makedirs(origin)
    git(base, "init", "-q", "--bare", origin)
    git(origin, "symbolic-ref", "HEAD", "refs/heads/main")
    seed = os.path.join(base, "seed")
    git(base, "init", "-q", "-b", "main", seed)
    git(seed, "config", "user.email", "test@example.com")
    git(seed, "config", "user.name", "Test")
    with open(os.path.join(seed, "README.md"), "w", encoding="utf-8") as handle:
        handle.write("seed\n")
    os.makedirs(os.path.join(seed, ".claude"))
    with open(os.path.join(seed, ".claude", "work-branch"), "w", encoding="utf-8") as handle:
        handle.write("# lane\n%s\n" % LANE)
    git(seed, "add", ".")
    git(seed, "commit", "-q", "-m", "init")
    git(seed, "branch", "some-old-branch")
    git(seed, "branch", LANE)
    git(seed, "remote", "add", "origin", origin)
    git(seed, "push", "-q", "origin", "main", LANE, "some-old-branch")

    lane_dir = os.path.join(base, "on_lane")
    main_dir = os.path.join(base, "on_main")
    for target, branch in ((lane_dir, LANE), (main_dir, "main")):
        git(base, "clone", "-q", origin, target)
        git(target, "config", "user.email", "test@example.com")
        git(target, "config", "user.name", "Test")
        git(target, "checkout", "-q", branch)
        if branch == LANE:
            git(target, "branch", "--set-upstream-to", "origin/%s" % LANE)
    return lane_dir, main_dir


class FixtureMixin(object):
    @classmethod
    def setUpClass(cls):
        cls.base = tempfile.mkdtemp(prefix="heleos-guard-")
        cls.lane_dir, cls.main_dir = make_fixture(cls.base)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.base, ignore_errors=True)

    def decide(self, root, tool, tool_input, cwd=None):
        ctx = branch_guard.RepoContext(root, LANE, cwd or root)
        decision = branch_guard.evaluate(ctx, {"tool_name": tool, "tool_input": tool_input, "cwd": cwd or root})
        return ALLOW if decision.allowed else DENY

    def check_table(self, root, table):
        for tool, tool_input, expected in table:
            with self.subTest(tool=tool, input=tool_input):
                self.assertEqual(self.decide(root, tool, tool_input), expected)


class ScannerTests(unittest.TestCase):
    def test_splits_on_operators_and_flags_grouping(self):
        segments, flags = shell_scan.split_segments("a && b || c; d | e & f")
        self.assertEqual(segments, ["a", "b", "c", "d", "e", "f"])
        self.assertFalse(flags.cwd_unreliable)
        _segments, flags = shell_scan.split_segments("(cd /x) && git status")
        self.assertTrue(flags.grouping)
        _segments, flags = shell_scan.split_segments("git checkout $(echo main)")
        self.assertTrue(flags.substitution)

    def test_quotes_protect_operators(self):
        segments, _flags = shell_scan.split_segments("echo 'a && b' && echo \"c; d\"")
        self.assertEqual(segments, ["echo 'a && b'", "echo \"c; d\""])

    def test_comments_only_start_at_word_boundaries(self):
        segments, _flags = shell_scan.split_segments("git checkout main # comment")
        self.assertEqual(shell_scan.tokenize(segments[0]), ["git", "checkout", "main"])
        segments, _flags = shell_scan.split_segments("echo a#b")
        self.assertEqual(shell_scan.tokenize(segments[0]), ["echo", "a#b"])

    def test_redirections_are_separated(self):
        segments, _flags = shell_scan.split_segments("git checkout main 2>/dev/null >&2 1>>log.txt")
        argv, writes, _reads = shell_scan.extract_redirects(shell_scan.tokenize(segments[0]))
        self.assertEqual(argv, ["git", "checkout", "main"])
        self.assertEqual(writes, ["/dev/null", "log.txt"])
        segments, _flags = shell_scan.split_segments("cat x>file")
        argv, writes, _reads = shell_scan.extract_redirects(shell_scan.tokenize(segments[0]))
        self.assertEqual((argv, writes), (["cat", "x"], ["file"]))

    def test_heredoc_bodies_are_opaque(self):
        segments, flags = shell_scan.split_segments("cat > notes.md <<'EOF'\ngit checkout main\nEOF\necho done")
        self.assertTrue(flags.heredoc)
        self.assertEqual([shell_scan.tokenize(s)[0] for s in segments], ["cat", "echo"])

    def test_wrappers_are_unwrapped(self):
        for command in ("sudo git status", "env FOO=1 git status", "nice -n 5 git status",
                        "timeout 5 git status", "xargs -n1 git status", "command git status"):
            _segments, _flags = shell_scan.split_segments(command)
            _env, argv = shell_scan.strip_env_prefix(shell_scan.tokenize(command))
            _env, argv = shell_scan.unwrap_wrappers(argv)
            self.assertEqual(argv[:2], ["git", "status"], command)

    def test_payload_detection(self):
        self.assertEqual(shell_scan.shell_payload(["bash", "-c", "git status"]), "git status")
        self.assertEqual(shell_scan.shell_payload(["eval", "git", "status"]), "git status")
        self.assertEqual(shell_scan.interpreter_payloads(["python3", "-c", "print(1)"]), ["print(1)"])
        self.assertEqual(shell_scan.interpreter_payloads(["python3", "script.py"]), [])


class OnLaneTests(FixtureMixin, unittest.TestCase):
    def test_git_branch_operations(self):
        self.check_table(self.lane_dir, [
            ("Bash", {"command": "git status"}, ALLOW),
            ("Bash", {"command": "git log --oneline -3"}, ALLOW),
            ("Bash", {"command": "git fetch origin main && git merge origin/main"}, ALLOW),
            ("Bash", {"command": "git commit -am 'x'"}, ALLOW),
            ("Bash", {"command": "git checkout %s" % LANE}, ALLOW),
            ("Bash", {"command": "git checkout -- README.md"}, ALLOW),
            ("Bash", {"command": "git checkout README.md"}, ALLOW),
            ("Bash", {"command": "git checkout main -- README.md"}, ALLOW),
            ("Bash", {"command": "git checkout main"}, DENY),
            ("Bash", {"command": "git checkout main 2>&1"}, DENY),
            ("Bash", {"command": "git checkout main # fine"}, DENY),
            ("Bash", {"command": "git checkout -"}, DENY),
            ("Bash", {"command": "git checkout HEAD~1"}, DENY),
            ("Bash", {"command": "git checkout -b feature/x"}, DENY),
            ("Bash", {"command": "git checkout --detach"}, DENY),
            ("Bash", {"command": "git switch main"}, DENY),
            ("Bash", {"command": "git switch -c topic"}, DENY),
            ("Bash", {"command": "git switch %s" % LANE}, ALLOW),
            ("Bash", {"command": "git branch"}, ALLOW),
            ("Bash", {"command": "git branch -vv"}, ALLOW),
            ("Bash", {"command": "git branch new-branch"}, DENY),
            ("Bash", {"command": "git branch -D main"}, DENY),
            ("Bash", {"command": "git branch -d some-old-branch"}, ALLOW),
            ("Bash", {"command": "git branch --set-upstream-to=origin/main"}, DENY),
            ("Bash", {"command": "git branch -u origin/%s" % LANE}, ALLOW),
            ("Bash", {"command": "git worktree list"}, ALLOW),
            ("Bash", {"command": "git worktree add ../wt main"}, DENY),
            ("Bash", {"command": "git symbolic-ref --short HEAD"}, ALLOW),
            ("Bash", {"command": "git symbolic-ref HEAD refs/heads/main"}, DENY),
            ("Bash", {"command": "git update-ref refs/heads/main HEAD"}, DENY),
        ])

    def test_git_push_policy(self):
        self.check_table(self.lane_dir, [
            ("Bash", {"command": "git push"}, ALLOW),
            ("Bash", {"command": "git push -u origin %s" % LANE}, ALLOW),
            ("Bash", {"command": "git push origin HEAD"}, ALLOW),
            ("Bash", {"command": "git push origin %s:%s" % (LANE, LANE)}, ALLOW),
            ("Bash", {"command": "git push origin main"}, DENY),
            ("Bash", {"command": "git push origin HEAD:main"}, DENY),
            ("Bash", {"command": "git push origin %s:refs/heads/main" % LANE}, DENY),
            ("Bash", {"command": "git push --force origin %s" % LANE}, DENY),
            ("Bash", {"command": "git push -fu origin %s" % LANE}, DENY),
            ("Bash", {"command": "git push origin +%s" % LANE}, DENY),
            ("Bash", {"command": "git push origin %s --force-with-lease" % LANE}, DENY),
            ("Bash", {"command": "git push origin :some-old-branch"}, DENY),
            ("Bash", {"command": "git push --tags"}, DENY),
            ("Bash", {"command": "git push upstream %s" % LANE}, DENY),
            ("Bash", {"command": "git push --no-verify"}, DENY),
        ])

    def test_git_config_remote_and_hooks(self):
        self.check_table(self.lane_dir, [
            ("Bash", {"command": "git remote -v"}, ALLOW),
            ("Bash", {"command": "git remote prune origin"}, ALLOW),
            ("Bash", {"command": "git remote set-url origin https://example.com/x.git"}, DENY),
            ("Bash", {"command": "git remote add other https://example.com/x.git"}, DENY),
            ("Bash", {"command": "git config user.name foo"}, ALLOW),
            ("Bash", {"command": "git config --get remote.origin.url"}, ALLOW),
            ("Bash", {"command": "git config push.default upstream"}, DENY),
            ("Bash", {"command": "git config alias.co checkout"}, DENY),
            ("Bash", {"command": "git config --unset remote.origin.url"}, DENY),
            ("Bash", {"command": "git -c alias.co=checkout co main"}, DENY),
            ("Bash", {"command": "git -c user.name=x commit -m y"}, ALLOW),
            ("Bash", {"command": "git commit --no-verify -m x"}, DENY),
            ("Bash", {"command": "git commit -an -m x"}, DENY),
            ("Bash", {"command": "git -c core.hooksPath=/dev/null commit -m x"}, DENY),
            ("Bash", {"command": "HOME=/tmp git checkout main"}, DENY),
            ("Bash", {"command": "GIT_DIR=/elsewhere/.git git checkout main"}, DENY),
        ])

    def test_git_alias_is_resolved(self):
        git(self.lane_dir, "config", "alias.co", "checkout")
        git(self.lane_dir, "config", "alias.sh", "!sh")
        try:
            self.assertEqual(self.decide(self.lane_dir, "Bash", {"command": "git co main"}), DENY)
            self.assertEqual(self.decide(self.lane_dir, "Bash", {"command": "git co README.md"}), ALLOW)
            self.assertEqual(self.decide(self.lane_dir, "Bash", {"command": "git sh -c 'echo hi'"}), DENY)
        finally:
            git(self.lane_dir, "config", "--unset", "alias.co")
            git(self.lane_dir, "config", "--unset", "alias.sh")

    def test_shell_shapes(self):
        other_repo = os.path.join(self.base, "on_main")  # a different clone counts as another repository
        self.check_table(self.lane_dir, [
            ("Bash", {"command": "cd %s && git checkout main" % other_repo}, ALLOW),
            ("Bash", {"command": "git -C %s checkout main" % other_repo}, ALLOW),
            ("Bash", {"command": "(cd %s) && git checkout main" % other_repo}, DENY),
            ("Bash", {"command": "cd %s; cd -; git checkout main" % other_repo}, DENY),
            ("Bash", {"command": "git status && git checkout main"}, DENY),
            ("Bash", {"command": "git checkout main; git status"}, DENY),
            ("Bash", {"command": "bash -c 'git checkout main'"}, DENY),
            ("Bash", {"command": "eval git checkout main"}, DENY),
            ("Bash", {"command": "sudo git checkout main"}, DENY),
            ("Bash", {"command": "nice -n 5 git switch main"}, DENY),
            ("Bash", {"command": "xargs -n1 git checkout main"}, DENY),
            ("Bash", {"command": "/usr/bin/git checkout main"}, DENY),
            ("Bash", {"command": "\"git\" checkout main"}, DENY),
            ("Bash", {"command": "git checkout ma\"in\""}, DENY),
            ("Bash", {"command": "git checkout $(echo main)"}, DENY),
            ("Bash", {"command": "git `echo`checkout main"}, DENY),
            ("Bash", {"command": "B=main; git checkout $B"}, DENY),
            ("Bash", {"command": "X=check; git ${X}out main"}, DENY),
            ("Bash", {"command": "python3 -c \"import subprocess; subprocess.run(['git','checkout','main'])\""}, DENY),
            ("Bash", {"command": "node -e \"require('child_process').execSync('git checkout main')\""}, DENY),
            ("Bash", {"command": "python3 -c 'print(1)'"}, ALLOW),
            ("Bash", {"command": "grep -rn 'git checkout main' docs/"}, ALLOW),
            ("Bash", {"command": "echo 'git checkout main'"}, ALLOW),
            ("Bash", {"command": "echo \"$(git branch --show-current)\""}, ALLOW),
            ("Bash", {"command": "git push -u origin \"$(git branch --show-current)\""}, DENY),
            ("Bash", {"command": "cat > notes.md <<'EOF'\ngit checkout main\nEOF"}, ALLOW),
            ("Bash", {"command": "git commit -m \"$(cat <<'EOF'\nfeat: guard\n\nblocks git checkout main\nEOF\n)\""}, ALLOW),
        ])

    def test_git_dir_is_protected(self):
        self.check_table(self.lane_dir, [
            ("Bash", {"command": "echo 'ref: refs/heads/main' > .git/HEAD"}, DENY),
            ("Bash", {"command": "cat .git/HEAD"}, ALLOW),
            ("Bash", {"command": "rm -rf .git/refs/heads/main"}, DENY),
            ("Bash", {"command": "cp x .git/hooks/pre-commit"}, DENY),
            ("Bash", {"command": "tee .git/HEAD <<< 'x'"}, DENY),
            ("Bash", {"command": "cat > .git/x <<'EOF'\nhi\nEOF"}, DENY),
            ("Write", {"file_path": os.path.join(self.lane_dir, ".git", "HEAD"), "content": "x"}, DENY),
            ("Write", {"file_path": os.path.join(self.lane_dir, "README.md"), "content": "x"}, ALLOW),
            ("Edit", {"file_path": "docs/new.md"}, ALLOW),
            ("Edit", {"file_path": os.path.join(self.base, "on_main", "README.md")}, ALLOW),
        ])

    def test_gh_cli(self):
        self.check_table(self.lane_dir, [
            ("Bash", {"command": "gh pr view 1"}, ALLOW),
            ("Bash", {"command": "gh pr create --base main --title x"}, ALLOW),
            ("Bash", {"command": "gh pr create --head other --base main"}, DENY),
            ("Bash", {"command": "gh pr merge 1"}, DENY),
            ("Bash", {"command": "gh pr review 1 --approve"}, DENY),
            ("Bash", {"command": "gh api -X PUT repos/bbukolla-eng/Heleos-spark/contents/x"}, DENY),
            ("Bash", {"command": "gh repo delete bbukolla-eng/Heleos-spark"}, DENY),
        ])

    def test_github_mcp_tools(self):
        me = {"owner": "bbukolla-eng", "repo": "Heleos-spark"}
        self.check_table(self.lane_dir, [
            ("mcp__github__push_files", dict(me, branch="main"), DENY),
            ("mcp__github__push_files", dict(me, branch=LANE), ALLOW),
            ("mcp__github__create_or_update_file", dict(me, branch="main"), DENY),
            ("mcp__github__push_files", {"owner": "bbukolla-eng", "repo": "ECC", "branch": "main"}, ALLOW),
            ("mcp__github__merge_pull_request", dict(me, pullNumber=1), DENY),
            ("mcp__github__create_branch", dict(me, branch="x"), DENY),
            ("mcp__github__update_pull_request_branch", dict(me, pullNumber=1), DENY),
            ("mcp__github__create_pull_request", dict(me, head=LANE, base="main"), ALLOW),
            ("mcp__github__create_pull_request", dict(me, head="feat/x", base="main"), DENY),
            ("mcp__github__create_pull_request", dict(me, head=LANE, base=LANE), DENY),
            ("mcp__github__pull_request_review_write", dict(me, event="APPROVE"), DENY),
            ("mcp__github__pull_request_review_write", dict(me, event="COMMENT"), ALLOW),
            ("mcp__github__pull_request_read", dict(me, pullNumber=1), ALLOW),
            ("Read", {"file_path": os.path.join(self.lane_dir, "README.md")}, ALLOW),
        ])


class OffLaneTests(FixtureMixin, unittest.TestCase):
    def test_read_only_and_lane_reentry(self):
        self.check_table(self.main_dir, [
            ("Bash", {"command": "ls -la"}, ALLOW),
            ("Bash", {"command": "git status"}, ALLOW),
            ("Bash", {"command": "git log --oneline"}, ALLOW),
            ("Bash", {"command": "git diff"}, ALLOW),
            ("Bash", {"command": "git branch -a"}, ALLOW),
            ("Bash", {"command": "git branch --show-current"}, ALLOW),
            ("Bash", {"command": "git fetch origin"}, ALLOW),
            ("Bash", {"command": "git show HEAD:README.md"}, ALLOW),
            ("Bash", {"command": "git stash"}, ALLOW),
            ("Bash", {"command": "git checkout %s" % LANE}, ALLOW),
            ("Bash", {"command": "git checkout -q %s" % LANE}, ALLOW),
            ("Bash", {"command": "git switch %s" % LANE}, ALLOW),
            ("Bash", {"command": "git stash && git checkout %s" % LANE}, ALLOW),
            ("Bash", {"command": "cat README.md | sort | uniq -c"}, ALLOW),
            ("Bash", {"command": "grep -rn foo . | head"}, ALLOW),
            ("Bash", {"command": "echo hi > /tmp/heleos-guard-scratch.txt"}, ALLOW),
            ("Bash", {"command": "cd /tmp && python3 -c 'print(1)'"}, ALLOW),
            ("Bash", {"command": "cd %s && git status && echo ok" % self.lane_dir}, ALLOW),
        ])

    def test_writes_are_denied(self):
        self.check_table(self.main_dir, [
            ("Bash", {"command": "git checkout main"}, DENY),
            ("Bash", {"command": "git checkout %s -- README.md" % LANE}, DENY),
            ("Bash", {"command": "git checkout -b %s" % LANE}, DENY),
            ("Bash", {"command": "git commit -m x"}, DENY),
            ("Bash", {"command": "git commit --amend"}, DENY),
            ("Bash", {"command": "git add ."}, DENY),
            ("Bash", {"command": "git push"}, DENY),
            ("Bash", {"command": "git merge some-old-branch"}, DENY),
            ("Bash", {"command": "git stash pop"}, DENY),
            ("Bash", {"command": "git branch foo"}, DENY),
            ("Bash", {"command": "git tag v1"}, DENY),
            ("Bash", {"command": "echo hi > README.md"}, DENY),
            ("Bash", {"command": "echo hi >> README.md"}, DENY),
            ("Bash", {"command": "printf 'x' | tee README.md"}, DENY),
            ("Bash", {"command": "sed -i s/a/b/ README.md"}, DENY),
            ("Bash", {"command": "sed -n 1p README.md"}, ALLOW),
            ("Bash", {"command": "rm README.md"}, DENY),
            ("Bash", {"command": "touch new.txt"}, DENY),
            ("Bash", {"command": "python3 script.py"}, DENY),
            ("Bash", {"command": "bash -c 'echo hi > README.md'"}, DENY),
            ("Bash", {"command": "find . -name '*.py' -delete"}, DENY),
            ("Bash", {"command": "find . -name '*.py'"}, ALLOW),
            ("Write", {"file_path": os.path.join(self.main_dir, "README.md"), "content": "x"}, DENY),
            ("Edit", {"file_path": os.path.join(self.main_dir, "README.md")}, DENY),
            ("MultiEdit", {"file_path": os.path.join(self.main_dir, "README.md")}, DENY),
            ("NotebookEdit", {"notebook_path": os.path.join(self.main_dir, "nb.ipynb")}, DENY),
            ("Write", {"file_path": "/tmp/heleos-guard-other.txt", "content": "x"}, ALLOW),
            ("Read", {"file_path": os.path.join(self.main_dir, "README.md")}, ALLOW),
        ])

    def test_detached_head_counts_as_off_lane(self):
        detached = os.path.join(self.base, "detached")
        git(self.base, "clone", "-q", os.path.join(self.base, "bbukolla-eng", "Heleos-spark.git"), detached)
        git(detached, "checkout", "-q", "--detach", "main")
        self.assertEqual(self.decide(detached, "Bash", {"command": "git commit -m x"}), DENY)
        self.assertEqual(self.decide(detached, "Bash", {"command": "git checkout %s" % LANE}), ALLOW)


class LauncherTests(FixtureMixin, unittest.TestCase):
    def run_hook(self, payload, env_extra=None, raw=None):
        env = dict(os.environ)
        env["HELEOS_GUARD_REPO_ROOT"] = self.main_dir
        env.pop("HELEOS_BRANCH_GUARD", None)
        env.update(env_extra or {})
        data = raw if raw is not None else json.dumps(payload)
        proc = subprocess.run(["bash", os.path.join(HOOKS_DIR, "branch_guard.sh")], input=data,
                              capture_output=True, text=True, env=env)
        return proc.returncode, proc.stderr

    def test_launcher_denies_with_message(self):
        code, err = self.run_hook({"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}, "cwd": self.main_dir})
        self.assertEqual(code, DENY)
        self.assertIn("[BranchGuard]", err)
        self.assertIn(LANE, err)

    def test_launcher_allows_read_only(self):
        code, _err = self.run_hook({"tool_name": "Bash", "tool_input": {"command": "git status"}, "cwd": self.main_dir})
        self.assertEqual(code, ALLOW)

    def test_kill_switch_and_malformed_input(self):
        code, _err = self.run_hook({"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}, "cwd": self.main_dir},
                                   env_extra={"HELEOS_BRANCH_GUARD": "off"})
        self.assertEqual(code, ALLOW)
        code, _err = self.run_hook(None, raw="not json")
        self.assertEqual(code, ALLOW)
        code, _err = self.run_hook(None, raw="")
        self.assertEqual(code, ALLOW)

    def test_work_branch_file_is_read(self):
        self.assertEqual(branch_guard.read_lane(REPO_ROOT), LANE)


class LaneConfigTests(FixtureMixin, unittest.TestCase):
    def test_lane_name_validation(self):
        for name in (LANE, "main", "feat/x.y_z", "release-1.2"):
            self.assertTrue(branch_guard.valid_lane_name(self.lane_dir, name), name)
        for name in ("", "-x", "--upload-pack=touch /tmp/x", "a b", "a..b", "a/", "a.lock", "$(x)", "a@{1}", "a\\b", "-"):
            self.assertFalse(branch_guard.valid_lane_name(self.lane_dir, name), repr(name))

    def run_hook(self, root, tool, tool_input):
        env = dict(os.environ)
        env["HELEOS_GUARD_REPO_ROOT"] = root
        env.pop("HELEOS_BRANCH_GUARD", None)
        env.pop("HELEOS_GUARD_ALLOW_SELF_EDIT", None)
        proc = subprocess.run(["bash", os.path.join(HOOKS_DIR, "branch_guard.sh")],
                              input=json.dumps({"tool_name": tool, "tool_input": tool_input, "cwd": root}),
                              capture_output=True, text=True, env=env)
        return proc.returncode, proc.stderr

    def test_invalid_or_missing_lane_fails_closed(self):
        for content in ("--upload-pack=touch /tmp/pwned\n", "-x\n", "main branch\n", "", None):
            with self.subTest(content=content):
                clone = os.path.join(self.base, "badlane")
                shutil.rmtree(clone, ignore_errors=True)
                git(self.base, "clone", "-q", os.path.join(self.base, "bbukolla-eng", "Heleos-spark.git"), clone)
                git(clone, "checkout", "-q", LANE)
                lane_file = os.path.join(clone, ".claude", "work-branch")
                if content is None:
                    os.remove(lane_file)
                else:
                    with open(lane_file, "w", encoding="utf-8") as handle:
                        handle.write(content)
                code, err = self.run_hook(clone, "Write", {"file_path": os.path.join(clone, "README.md"), "content": "x"})
                self.assertEqual(code, DENY)
                self.assertIn("work-branch", err)
                code, _err = self.run_hook(clone, "Bash", {"command": "git commit -m x"})
                self.assertEqual(code, DENY)
                code, _err = self.run_hook(clone, "Bash", {"command": "git status && ls"})
                self.assertEqual(code, ALLOW)


class SelfProtectionTests(FixtureMixin, unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        super(SelfProtectionTests, cls).setUpClass()
        # The lane carries a guard file that main does not have, so restoring
        # from main would remove it.
        settings = os.path.join(cls.lane_dir, ".claude", "settings.json")
        with open(settings, "w", encoding="utf-8") as handle:
            handle.write("{}\n")
        git(cls.lane_dir, "add", ".claude/settings.json")
        git(cls.lane_dir, "commit", "-q", "-m", "add settings")

    def test_guard_files_are_owner_managed(self):
        d = self.lane_dir
        self.check_table(d, [
            ("Write", {"file_path": os.path.join(d, ".claude", "work-branch"), "content": "main"}, DENY),
            ("Write", {"file_path": os.path.join(d, ".claude", "hooks", "branch_guard.py"), "content": "x"}, DENY),
            ("Edit", {"file_path": os.path.join(d, ".githooks", "pre-push")}, DENY),
            ("Write", {"file_path": os.path.join(d, ".claude", "settings.json"), "content": "{}"}, DENY),
            ("Write", {"file_path": os.path.join(d, ".claude", "skills", "x", "SKILL.md"), "content": "x"}, ALLOW),
            ("Write", {"file_path": os.path.join(d, "README.md"), "content": "x"}, ALLOW),
            ("Bash", {"command": "echo main > .claude/work-branch"}, DENY),
            ("Bash", {"command": "rm -rf .githooks"}, DENY),
            ("Bash", {"command": "rm -rf .claude"}, DENY),
            ("Bash", {"command": "rm -rf ."}, DENY),
            ("Bash", {"command": "chmod -x .githooks/pre-push"}, DENY),
            ("Bash", {"command": "chmod +x scripts/run.sh"}, ALLOW),
            ("Bash", {"command": "sed -i s/a/b/ .claude/hooks/branch_guard.py"}, DENY),
            ("Bash", {"command": "sed -n 1p .claude/hooks/branch_guard.py"}, ALLOW),
            ("Bash", {"command": "cat .githooks/pre-push .claude/work-branch"}, ALLOW),
            ("Bash", {"command": "cp x .claude/settings.json"}, DENY),
            ("Bash", {"command": "find . -name '*.pyc' -delete"}, DENY),
            ("Bash", {"command": "find docs -name '*.tmp' -delete"}, ALLOW),
            ("Bash", {"command": "git rm .claude/settings.json"}, DENY),
            ("Bash", {"command": "git mv .githooks hooks"}, DENY),
            ("Bash", {"command": "git rm docs/a.md"}, ALLOW),
            ("Bash", {"command": "git checkout main -- .claude/work-branch"}, DENY),
            ("Bash", {"command": "git checkout main -- ."}, DENY),
            ("Bash", {"command": "git checkout main -- README.md"}, ALLOW),
            ("Bash", {"command": "git checkout -- ."}, ALLOW),
            ("Bash", {"command": "git checkout HEAD -- ."}, ALLOW),
            ("Bash", {"command": "git restore --source=main .claude/settings.json"}, DENY),
            ("Bash", {"command": "git restore --source main ."}, DENY),
            ("Bash", {"command": "git restore --staged README.md"}, ALLOW),
            ("Bash", {"command": "git restore README.md"}, ALLOW),
            ("Bash", {"command": "git reset --hard"}, ALLOW),
            ("Bash", {"command": "git reset --hard HEAD"}, ALLOW),
            ("Bash", {"command": "git reset --hard main"}, DENY),
            ("Bash", {"command": "git reset --soft main"}, ALLOW),
            ("Bash", {"command": "git reset --hard origin/%s" % LANE}, DENY),
            ("Bash", {"command": "git update-index --assume-unchanged .githooks/pre-push"}, DENY),
        ])

    def test_owner_can_lift_self_protection(self):
        d = self.lane_dir
        with unittest.mock.patch.dict(os.environ, {"HELEOS_GUARD_ALLOW_SELF_EDIT": "1"}):
            self.check_table(d, [
                ("Write", {"file_path": os.path.join(d, ".claude", "work-branch"), "content": "main"}, ALLOW),
                ("Bash", {"command": "rm -rf .githooks"}, ALLOW),
                ("Bash", {"command": "git checkout main -- .claude/work-branch"}, ALLOW),
                ("Bash", {"command": "git checkout main"}, DENY),
            ])

    def test_touches_protected(self):
        ctx = branch_guard.RepoContext(self.lane_dir, LANE, self.lane_dir)
        for rel in (".claude/work-branch", ".claude/settings.json", ".claude/hooks", ".claude/hooks/x.py",
                    ".githooks", ".githooks/pre-push", ".claude", "."):
            self.assertTrue(ctx.touches_protected(os.path.join(self.lane_dir, rel)), rel)
        for rel in ("README.md", "docs", ".claude/skills/x/SKILL.md", ".claude/identity.json", ".gitignore"):
            self.assertFalse(ctx.touches_protected(os.path.join(self.lane_dir, rel)), rel)
        self.assertFalse(ctx.touches_protected("/tmp"))


if __name__ == "__main__":
    unittest.main()
