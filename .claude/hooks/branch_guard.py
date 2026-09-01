#!/usr/bin/env python3
"""Heleos-spark branch guard (Claude Code PreToolUse hook).

Enforces the single-lane rule: Claude Code may only edit, commit, and push on
the designated work branch named in ``.claude/work-branch``. Every other branch
in this repository, including ``main``, is read-only for Claude Code.

What it checks
--------------
* Edit / Write / MultiEdit / NotebookEdit  -> denied when the repo checkout is
  not on the lane branch.
* Bash -> git and gh invocations are parsed. On the lane, commands that would
  leave the lane (checkout/switch to another ref, new branches, worktrees,
  pushes to other branches or remotes, force pushes, remote/config rewiring)
  are denied. Off the lane, only read-only commands and ``git checkout <lane>``
  are allowed.
* GitHub MCP write tools -> denied when they target another branch of this
  repository, create branches, merge, or approve pull requests.

Exit codes follow the Claude Code hook contract: 0 allows the tool call,
2 denies it and feeds stderr back to the model.

Human kill switch: ``HELEOS_BRANCH_GUARD=off`` in the *harness* environment.
Test hook: ``HELEOS_GUARD_REPO_ROOT`` points the guard at another checkout.
"""

import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import shell_scan  # noqa: E402

TAG = "[BranchGuard]"
MAX_STDIN = 1024 * 1024

EDIT_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}

GIT_READ_ONLY = {
    "status", "log", "diff", "show", "fetch", "rev-parse", "ls-files", "ls-remote",
    "ls-tree", "cat-file", "describe", "blame", "shortlog", "reflog", "name-rev",
    "merge-base", "rev-list", "count-objects", "fsck", "check-ignore", "grep",
    "for-each-ref", "var", "version", "help", "show-ref", "diff-tree", "diff-index",
    "diff-files", "whatchanged", "bisect", "range-diff", "cherry", "check-attr",
    "show-branch", "verify-commit", "verify-tag", "annotate", "--version", "-v",
}
GIT_PLUMBING_WRITES = {
    "update-ref", "receive-pack", "send-pack", "fast-import", "filter-branch",
    "replace", "filter-repo",
}
SHELL_READ_ONLY = {
    "cd", "pushd", "popd", "pwd", "ls", "cat", "head", "tail", "less", "more", "wc",
    "grep", "rg", "egrep", "fgrep", "find", "fd", "tree", "echo", "printf", "true",
    "false", "test", "[", "[[", "which", "type", "whoami", "id", "env", "printenv",
    "date", "uname", "stat", "file", "du", "df", "diff", "sort", "uniq", "cut",
    "awk", "tr", "basename", "dirname", "realpath", "readlink", "md5sum",
    "sha256sum", "shasum", "jq", "yq", "column", "nl", "tac", "rev", "seq", "sleep",
    "export", "set", "unset", "shift", "read", "declare", "local", "readonly",
    "hostname", "history", "bat", "exa", "eza", "git", "gh", ":", "sed",
}
_INLINE_GIT_RE = re.compile(
    r"\bgit\b[\s'\",\[\]]*(checkout|switch|push|worktree|branch|symbolic-ref|update-ref|remote|config)\b",
    re.IGNORECASE,
)
_GIT_GLOBAL_VALUE_OPTS = {"-C", "-c", "--git-dir", "--work-tree", "--namespace", "--exec-path",
                          "--super-prefix", "--config-env", "--list-cmds"}
_CHECKOUT_VALUE_OPTS = {"-b", "-B", "--orphan", "--conflict", "--pathspec-from-file"}
_CHECKOUT_NEW_BRANCH_OPTS = {"-b", "-B", "--orphan", "--detach", "-t", "--track"}
_SWITCH_VALUE_OPTS = {"-c", "-C", "--create", "--force-create", "--orphan", "--conflict"}
_SWITCH_NEW_BRANCH_OPTS = {"-c", "-C", "--create", "--force-create", "--orphan", "--detach", "-d",
                           "-t", "--track"}
_PUSH_VALUE_OPTS = {"--repo", "-o", "--push-option", "--receive-pack", "--exec"}
_BLOCKED_CONFIG_PREFIXES = ("remote.", "push.", "branch.", "core.hookspath", "receive.",
                            "url.", "include.", "includeif.", "alias.")


class Decision(object):
    def __init__(self, allowed, reason=""):
        self.allowed = allowed
        self.reason = reason

    @classmethod
    def allow(cls):
        return cls(True)

    @classmethod
    def deny(cls, reason):
        return cls(False, reason)


# ---------------------------------------------------------------------------
# Repository context
# ---------------------------------------------------------------------------

class RepoContext(object):
    def __init__(self, root, lane, cwd):
        self.root = os.path.realpath(root)
        self.lane = lane
        self.cwd = cwd or self.root
        self._branch = None
        self._origin_url = None

    @property
    def branch(self):
        if self._branch is None:
            out = self._git("symbolic-ref", "--quiet", "--short", "HEAD")
            self._branch = out.strip() if out is not None and out.strip() else "(detached HEAD)"
        return self._branch

    @property
    def on_lane(self):
        return self.branch == self.lane

    @property
    def origin_url(self):
        if self._origin_url is None:
            out = self._git("remote", "get-url", "origin")
            self._origin_url = (out or "").strip()
        return self._origin_url

    def _git(self, *args):
        return run_git(self.root, *args)

    def is_ref(self, name):
        if name in ("-", "HEAD", "@") or name.startswith("@{") or "~" in name or "^" in name:
            return True
        for ref in ("refs/heads/%s" % name, "refs/remotes/%s" % name, "refs/tags/%s" % name,
                    "refs/remotes/origin/%s" % name):
            if git_succeeds(self.root, "show-ref", "--verify", "--quiet", ref):
                return True
        return git_succeeds(self.root, "rev-parse", "--verify", "--quiet", "%s^{commit}" % name)

    def inside_repo(self, path):
        try:
            real = os.path.realpath(path)
        except (OSError, ValueError):
            return True
        return real == self.root or real.startswith(self.root + os.sep)

    def inside_git_dir(self, path):
        real = os.path.realpath(path)
        git_dir = os.path.join(self.root, ".git")
        return real == git_dir or real.startswith(git_dir + os.sep)

    def owns_directory(self, directory):
        """True when ``directory`` belongs to this repository (or is unknown)."""
        if directory is None:
            return True
        if not os.path.isdir(directory):
            return True
        top = run_git(directory, "rev-parse", "--show-toplevel", ok_codes=(0, 128))
        if not top or not top.strip():
            # Not a git repository at all: nothing there can leave the lane.
            return False
        return os.path.realpath(top.strip()) == self.root

    def upstream_branch(self):
        out = run_git(self.root, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}", ok_codes=(0, 128))
        if not out or not out.strip():
            return None
        ref = out.strip()
        return ref.split("/", 1)[1] if "/" in ref else ref


def _git_proc(directory, *args):
    try:
        return subprocess.run(["git", "-C", directory] + list(args), capture_output=True, text=True, timeout=5)
    except (OSError, subprocess.SubprocessError):
        return None


def run_git(directory, *args, **kwargs):
    """Return stdout on success, "" for tolerated non-zero codes, None otherwise."""
    ok_codes = kwargs.get("ok_codes", (0,))
    proc = _git_proc(directory, *args)
    if proc is None:
        return None
    if proc.returncode == 0:
        return proc.stdout
    if proc.returncode in ok_codes:
        return ""
    return None


def git_succeeds(directory, *args):
    proc = _git_proc(directory, *args)
    return proc is not None and proc.returncode == 0


def find_repo_root():
    override = os.environ.get("HELEOS_GUARD_REPO_ROOT")
    candidates = []
    if override:
        candidates.append(override)
    here = os.path.dirname(os.path.abspath(__file__))
    candidates.append(os.path.abspath(os.path.join(here, "..", "..")))
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir:
        candidates.append(project_dir)
    for candidate in candidates:
        if os.path.isfile(os.path.join(candidate, ".claude", "work-branch")):
            return candidate
    return None


def read_lane(root):
    path = os.path.join(root, ".claude", "work-branch")
    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            text = line.strip()
            if text and not text.startswith("#"):
                return text
    return None


# ---------------------------------------------------------------------------
# Messages
# ---------------------------------------------------------------------------

def off_lane_message(ctx, what):
    return (
        "%s DENIED: %s while Heleos-spark is on branch '%s'. Claude Code may only work on the "
        "designated lane branch '%s'. Allowed right now: read-only commands and "
        "`git checkout %s` (run `git stash` first if the tree is dirty). Never edit, commit, "
        "or push on any other branch, including main."
        % (TAG, what, ctx.branch, ctx.lane, ctx.lane)
    )


def lane_message(ctx, what, hint=""):
    text = "%s DENIED: %s. Heleos-spark work stays on the designated lane branch '%s'." % (TAG, what, ctx.lane)
    if hint:
        text += " " + hint
    return text


# ---------------------------------------------------------------------------
# Git policy
# ---------------------------------------------------------------------------

def split_git_globals(argv):
    """Return ``(global_opts, subcommand, sub_args, c_dir)`` for a git argv."""
    idx = 1
    c_dir = None
    globals_ = []
    while idx < len(argv):
        tok = argv[idx]
        if not tok.startswith("-"):
            break
        globals_.append(tok)
        if tok in _GIT_GLOBAL_VALUE_OPTS:
            if idx + 1 < len(argv):
                globals_.append(argv[idx + 1])
                if tok == "-C":
                    c_dir = argv[idx + 1]
            idx += 2
            continue
        if tok.startswith("-C") and len(tok) > 2 and not tok.startswith("--"):
            c_dir = tok[2:]
        idx += 1
    if idx >= len(argv):
        return globals_, None, [], c_dir
    return globals_, argv[idx], argv[idx + 1:], c_dir


def has_hook_bypass(globals_, sub, sub_args):
    lowered = [g.lower() for g in globals_]
    for i, tok in enumerate(lowered):
        if tok == "-c" and i + 1 < len(lowered) and lowered[i + 1].startswith("core.hookspath="):
            return True
        if tok.startswith("-ccore.hookspath="):
            return True
    if sub in ("commit", "push", "merge", "rebase", "cherry-pick", "am"):
        for tok in sub_args:
            if tok == "--":
                break
            if tok == "--no-verify":
                return True
            if sub == "commit" and tok.startswith("-") and not tok.startswith("--") and "n" in tok[1:]:
                # -n clusters (e.g. -an) mean --no-verify for commit, unless the
                # cluster contains a value-taking option before the n.
                cluster = tok[1:]
                pos_n = cluster.find("n")
                if not any(c in "mFCct" for c in cluster[:pos_n]):
                    return True
    return False


def positionals(sub_args, value_opts):
    """Split args into (positionals_before_dashdash, after_dashdash, flags)."""
    before, after, flags = [], [], []
    seen_dd = False
    skip = False
    for tok in sub_args:
        if skip:
            skip = False
            continue
        if seen_dd:
            after.append(tok)
            continue
        if tok == "--":
            seen_dd = True
            continue
        if tok.startswith("-") and tok != "-":
            flags.append(tok)
            if tok in value_opts:
                skip = True
            continue
        before.append(tok)
    return before, after, flags, seen_dd


def short_cluster_hits(flags, letters):
    """True when any single-dash flag cluster contains one of ``letters``.

    ``git checkout -bfoo`` and ``git switch -cfoo`` attach the value to the
    option, so the whole token must be scanned, not just compared.
    """
    for flag in flags:
        if flag.startswith("-") and not flag.startswith("--") and len(flag) > 1:
            if any(ch in letters for ch in flag[1:]):
                return True
    return False


def check_checkout(ctx, sub_args):
    before, after, flags, seen_dd = positionals(sub_args, _CHECKOUT_VALUE_OPTS)
    flag_names = {f.split("=", 1)[0] for f in flags}
    if flag_names & _CHECKOUT_NEW_BRANCH_OPTS or short_cluster_hits(flags, "bBt"):
        return Decision.deny(lane_message(ctx, "`git checkout` would create or detach a branch",
                                          "Do not create new branches; the lane is the only branch."))
    if seen_dd and after:
        return Decision.allow()  # file restore from a tree-ish: does not switch branches
    if len(before) >= 2:
        return Decision.allow()  # `git checkout <tree-ish> <path>` restores files
    if not before:
        return Decision.allow()  # `git checkout -- .`, `git checkout -p`, etc.
    target = before[0]
    if target == ctx.lane:
        return Decision.allow()
    if not seen_dd and not ctx.is_ref(target) and os.path.exists(os.path.join(ctx.cwd, target)):
        return Decision.allow()  # plain file restore
    return Decision.deny(lane_message(
        ctx, "`git checkout %s` would leave the lane" % target,
        "If you meant a file, use `git checkout -- %s`." % target))


def check_switch(ctx, sub_args):
    before, _after, flags, _dd = positionals(sub_args, _SWITCH_VALUE_OPTS)
    flag_names = {f.split("=", 1)[0] for f in flags}
    if flag_names & _SWITCH_NEW_BRANCH_OPTS or short_cluster_hits(flags, "cCdt"):
        return Decision.deny(lane_message(ctx, "`git switch` would create or detach a branch"))
    if before and before[0] != ctx.lane:
        return Decision.deny(lane_message(ctx, "`git switch %s` would leave the lane" % before[0]))
    return Decision.allow()


def check_branch(ctx, sub_args):
    before, _after, flags, _dd = positionals(sub_args, {"--set-upstream-to", "-u", "--contains",
                                                        "--no-contains", "--merged", "--no-merged",
                                                        "--points-at", "--format", "--sort", "--color"})
    flag_names = {f.split("=", 1)[0] for f in flags}
    listing = {"-a", "-r", "-v", "-vv", "--list", "--show-current", "--all", "--remotes",
               "--verbose", "--contains", "--no-contains", "--merged", "--no-merged",
               "--points-at", "--format", "--sort", "--color", "--no-color", "--column",
               "--no-column", "-l", "--abbrev", "--no-abbrev", "-i", "--ignore-case"}
    renames_or_copies = bool(flag_names & {"-m", "-M", "--move", "-c", "-C", "--copy"}) or short_cluster_hits(flags, "mMcC")
    if renames_or_copies:
        return Decision.deny(lane_message(ctx, "`git branch` rename or copy",
                                          "Renaming or copying branches is an owner action."))
    deletes = bool(flag_names & {"-d", "-D", "--delete"}) or short_cluster_hits(flags, "dD")
    if deletes:
        protected = {ctx.lane, "main", "master"}
        plain = all(f in ("-d", "-D", "--delete", "-r", "--remotes", "-f", "--force") for f in flags)
        if not before or not plain or set(before) & protected or flag_names & {"-r", "--remotes"}:
            return Decision.deny(lane_message(ctx, "`git branch` would delete a protected or remote branch"))
        return Decision.allow()
    if flag_names & {"-u", "--set-upstream-to", "--unset-upstream"} or short_cluster_hits(flags, "u"):
        values = [f.split("=", 1)[1] for f in flags if "=" in f]
        for i, tok in enumerate(sub_args):
            if tok in ("-u", "--set-upstream-to") and i + 1 < len(sub_args):
                values.append(sub_args[i + 1])
        for value in values:
            if value.split("/", 1)[-1] != ctx.lane:
                return Decision.deny(lane_message(ctx, "`git branch` would point the lane at upstream '%s'" % value))
        return Decision.allow()
    if before and not (flag_names & listing) and not flag_names:
        return Decision.deny(lane_message(ctx, "`git branch %s` would create another branch" % before[0]))
    if before and flag_names and not (flag_names & listing):
        return Decision.deny(lane_message(ctx, "`git branch` with unrecognised options and a branch name"))
    return Decision.allow()


def check_push(ctx, sub_args):
    if not ctx.on_lane:
        return Decision.deny(off_lane_message(ctx, "`git push`"))
    remote = None
    refspecs = []
    skip = False
    for tok in sub_args:
        if skip:
            skip = False
            continue
        if tok == "--":
            continue
        if tok.startswith("--"):
            name = tok.split("=", 1)[0]
            if name in ("--force", "--force-with-lease", "--force-if-includes", "--mirror", "--all",
                        "--delete", "--prune", "--tags", "--follow-tags", "--branches"):
                return Decision.deny(lane_message(ctx, "`git push %s` is not allowed" % name,
                                                  "No force pushes, deletions, tag or bulk pushes from Claude Code."))
            if name in ("--repo", "--receive-pack", "--exec"):
                return Decision.deny(lane_message(ctx, "`git push %s` redirects the push" % name))
            if name in _PUSH_VALUE_OPTS and "=" not in tok:
                skip = True
            continue
        if tok.startswith("-") and tok != "-":
            cluster = tok[1:]
            if "f" in cluster or "d" in cluster:
                return Decision.deny(lane_message(ctx, "`git push %s` is a force or delete push" % tok))
            if "o" in cluster and cluster.endswith("o"):
                skip = True
            continue
        if remote is None:
            remote = tok
        else:
            refspecs.append(tok)
    if remote is not None and remote != "origin" and remote != ctx.origin_url:
        return Decision.deny(lane_message(ctx, "`git push` to remote '%s'" % remote,
                                          "The lane is pushed to origin only."))
    if not refspecs:
        upstream = ctx.upstream_branch()
        if upstream and upstream != ctx.lane:
            return Decision.deny(lane_message(
                ctx, "`git push` without a refspec would use upstream '%s'" % upstream,
                "Run `git push -u origin %s`." % ctx.lane))
        return Decision.allow()
    for spec in refspecs:
        if spec.startswith("+"):
            return Decision.deny(lane_message(ctx, "refspec '%s' forces the push" % spec))
        src, _sep, dst = spec.partition(":")
        if _sep and not src:
            return Decision.deny(lane_message(ctx, "refspec '%s' deletes a remote branch" % spec))
        target = dst if _sep else src
        if target in ("HEAD", "@"):
            target = ctx.branch
        if target.startswith("refs/heads/"):
            target = target[len("refs/heads/"):]
        elif target.startswith("refs/"):
            return Decision.deny(lane_message(ctx, "refspec '%s' targets a non-branch ref" % spec))
        if target != ctx.lane:
            return Decision.deny(lane_message(ctx, "`git push` would update branch '%s'" % target,
                                              "Push only `%s`." % ctx.lane))
    return Decision.allow()


def check_remote(ctx, sub_args):
    action = sub_args[0] if sub_args and not sub_args[0].startswith("-") else None
    if action in ("add", "set-url", "rename", "remove", "rm", "set-head", "set-branches", "prune"):
        if action == "prune":
            return Decision.allow()
        return Decision.deny(lane_message(ctx, "`git remote %s` rewires where the lane is pushed" % action))
    return Decision.allow()


def check_config(ctx, sub_args):
    lowered = [t.lower() for t in sub_args]
    if any(t in ("--get", "--get-all", "--get-regexp", "--list", "-l", "--get-urlmatch", "--show-origin",
                 "--show-scope") for t in lowered):
        return Decision.allow()
    if any(t in ("--unset", "--unset-all", "--remove-section", "--rename-section", "--replace-all", "--add",
                 "--edit", "-e", "--file", "-f", "--blob") for t in lowered):
        return Decision.deny(lane_message(ctx, "`git config` structural edits are not allowed from Claude Code"))
    for tok in lowered:
        if tok.startswith("-"):
            continue
        if tok.startswith(_BLOCKED_CONFIG_PREFIXES):
            return Decision.deny(lane_message(ctx, "`git config %s` would change lane routing or hooks" % tok))
        break
    return Decision.allow()


KNOWN_SUBCOMMANDS = GIT_READ_ONLY | GIT_PLUMBING_WRITES | {
    "add", "commit", "merge", "rebase", "cherry-pick", "revert", "am", "apply", "reset", "restore",
    "rm", "mv", "clean", "stash", "tag", "pull", "checkout", "switch", "branch", "push", "worktree",
    "remote", "config", "symbolic-ref", "init", "clone", "submodule", "notes", "gc", "prune", "mergetool",
    "difftool", "archive", "bundle", "format-patch", "send-email", "request-pull", "sparse-checkout",
    "maintenance", "repack", "pack-refs", "reflog", "hash-object", "write-tree", "read-tree", "commit-tree",
    "mktree", "mktag", "unpack-objects", "index-pack", "verify-pack", "lfs", "flow", "credential",
}


def resolve_alias(ctx, sub):
    """Return the alias expansion for ``sub`` (None when it is not an alias)."""
    out = run_git(ctx.root, "config", "--get", "alias.%s" % sub, ok_codes=(0, 1))
    if out is None or not out.strip():
        return None
    return out.strip()


def check_git(ctx, argv, env_names, segment_dir, depth=0):
    globals_, sub, sub_args, c_dir = split_git_globals(argv)
    if sub is None:
        return Decision.allow()
    if "$" in sub or "`" in sub or "\\" in sub:
        return Decision.deny(lane_message(ctx, "git subcommand '%s' is opaque (unexpanded variable)" % sub,
                                          "Write the git command literally so the guard can check it."))
    target_dir = segment_dir
    if c_dir:
        target_dir = None if segment_dir is None else os.path.normpath(os.path.join(segment_dir, os.path.expanduser(c_dir)))
    redirected = bool(env_names & shell_scan.GIT_REDIRECT_ENV) or any(
        g.split("=", 1)[0] in ("--git-dir", "--work-tree") for g in globals_)
    if not redirected and not ctx.owns_directory(target_dir):
        return Decision.allow()  # another repository: out of scope
    for name in env_names:
        if name.upper().startswith(shell_scan.GIT_CONFIG_ENV_PREFIXES):
            return Decision.deny(lane_message(ctx, "git run with %s overridden in its environment" % name,
                                              "Configuration injection is not allowed."))
    for idx, tok in enumerate(globals_):
        key = None
        if tok == "-c" and idx + 1 < len(globals_):
            key = globals_[idx + 1].split("=", 1)[0].lower()
        elif tok.startswith("-c") and not tok.startswith("--") and len(tok) > 2:
            key = tok[2:].split("=", 1)[0].lower()
        elif tok.startswith("--config-env"):
            key = tok.split("=", 1)[1].split("=", 1)[0].lower() if "=" in tok else (
                globals_[idx + 1].split("=", 1)[0].lower() if idx + 1 < len(globals_) else "")
        if key is not None and key.startswith(_BLOCKED_CONFIG_PREFIXES):
            return Decision.deny(lane_message(ctx, "`git -c %s` overrides lane routing, aliases, or hooks" % key))
    if sub not in KNOWN_SUBCOMMANDS:
        alias = resolve_alias(ctx, sub)
        if alias is not None:
            if alias.startswith("!"):
                return Decision.deny(lane_message(ctx, "git alias '%s' runs a shell command" % sub))
            if depth >= 3:
                return Decision.deny(lane_message(ctx, "git alias '%s' nests too deeply" % sub))
            expanded = shell_scan.tokenize(alias)
            if not expanded:
                return Decision.deny(lane_message(ctx, "git alias '%s' could not be parsed" % sub))
            return check_git(ctx, [argv[0]] + globals_ + expanded + sub_args, env_names, segment_dir, depth + 1)
    if has_hook_bypass(globals_, sub, sub_args):
        return Decision.deny(lane_message(ctx, "git hook bypass (`--no-verify` / `core.hooksPath`)"))
    if sub in GIT_PLUMBING_WRITES:
        return Decision.deny(lane_message(ctx, "`git %s` rewrites refs or history" % sub))
    if sub == "symbolic-ref":
        if len([t for t in sub_args if not t.startswith("-")]) >= 2:
            return Decision.deny(lane_message(ctx, "`git symbolic-ref` write would move HEAD"))
        return Decision.allow()
    if sub == "worktree":
        action = sub_args[0] if sub_args else ""
        if action in ("list", "prune", ""):
            return Decision.allow()
        return Decision.deny(lane_message(ctx, "`git worktree %s` creates a second working branch context" % action))
    if sub == "remote":
        return check_remote(ctx, sub_args)
    if sub == "config":
        return check_config(ctx, sub_args)
    quiet_flags = ("-q", "--quiet", "--progress", "--no-progress")
    if sub == "checkout":
        if not ctx.on_lane:
            before, after, flags, seen_dd = positionals(sub_args, _CHECKOUT_VALUE_OPTS)
            if before == [ctx.lane] and not after and not seen_dd and all(f in quiet_flags for f in flags):
                return Decision.allow()
            return Decision.deny(off_lane_message(ctx, "`git checkout`"))
        return check_checkout(ctx, sub_args)
    if sub == "switch":
        if not ctx.on_lane:
            before, after, flags, seen_dd = positionals(sub_args, _SWITCH_VALUE_OPTS)
            if before == [ctx.lane] and not after and not seen_dd and all(f in quiet_flags for f in flags):
                return Decision.allow()
            return Decision.deny(off_lane_message(ctx, "`git switch`"))
        return check_switch(ctx, sub_args)
    if sub == "push":
        return check_push(ctx, sub_args)
    if sub == "branch":
        if not ctx.on_lane:
            before, _a, flags, _dd = positionals(sub_args, set())
            listing_only = {"-a", "-r", "-v", "-vv", "--list", "--show-current", "--all", "--remotes",
                            "--verbose", "--no-color", "--color"}
            if before or (set(f.split("=", 1)[0] for f in flags) - listing_only):
                return Decision.deny(off_lane_message(ctx, "`git branch` changes"))
            return Decision.allow()
        return check_branch(ctx, sub_args)
    if sub in ("fetch", "pull"):
        before, _a, _f, _dd = positionals(sub_args, {"--depth", "--deepen", "--shallow-since", "--shallow-exclude",
                                                     "--refmap", "--recurse-submodules", "--jobs", "-j", "-o",
                                                     "--server-option", "--upload-pack", "--negotiation-tip"})
        if before:
            remote = before[0]
            looks_like_url = "://" in remote or "@" in remote or remote.endswith(".git") or os.sep in remote
            if looks_like_url and remote != ctx.origin_url:
                return Decision.deny(lane_message(ctx, "`git %s` from '%s'" % (sub, remote),
                                                  "Fetch only from origin (clean-room boundary)."))
        if sub == "fetch" or ctx.on_lane:
            return Decision.allow()
        return Decision.deny(off_lane_message(ctx, "`git pull`"))
    if sub == "stash":
        action = sub_args[0] if sub_args and not sub_args[0].startswith("-") else "push"
        if ctx.on_lane or action in ("push", "list", "show", "save", "create"):
            return Decision.allow()
        return Decision.deny(off_lane_message(ctx, "`git stash %s`" % action))
    if sub in GIT_READ_ONLY:
        return Decision.allow()
    if not ctx.on_lane:
        return Decision.deny(off_lane_message(ctx, "`git %s`" % sub))
    return Decision.allow()


# ---------------------------------------------------------------------------
# gh CLI policy
# ---------------------------------------------------------------------------

def check_gh(ctx, argv):
    args = argv[1:]
    words = [a for a in args if not a.startswith("-")]
    if len(words) >= 2 and words[0] == "pr":
        if words[1] == "merge":
            return Decision.deny(lane_message(ctx, "`gh pr merge`", "Merging is an owner decision."))
        if words[1] == "review" and ("--approve" in args or "-a" in args):
            return Decision.deny(lane_message(ctx, "`gh pr review --approve`", "Claude Code does not approve pull requests."))
        if words[1] == "create":
            for i, tok in enumerate(args):
                head = None
                if tok in ("--head", "-H") and i + 1 < len(args):
                    head = args[i + 1]
                elif tok.startswith("--head="):
                    head = tok.split("=", 1)[1]
                if head is not None and head.split(":", 1)[-1] != ctx.lane:
                    return Decision.deny(lane_message(ctx, "`gh pr create --head %s`" % head))
            if not ctx.on_lane:
                return Decision.deny(off_lane_message(ctx, "`gh pr create`"))
    if words and words[0] == "api":
        method = None
        for i, tok in enumerate(args):
            if tok in ("-X", "--method") and i + 1 < len(args):
                method = args[i + 1].upper()
            elif tok.startswith("--method="):
                method = tok.split("=", 1)[1].upper()
            elif tok in ("-f", "-F", "--field", "--raw-field", "--input"):
                method = method or "POST"
        if method in ("POST", "PUT", "PATCH", "DELETE"):
            joined = " ".join(args)
            if re.search(r"/(contents|merges|git/refs|pulls/\d+/merge|branches/[^/\s]+/protection|pulls/\d+/reviews)", joined):
                return Decision.deny(lane_message(ctx, "`gh api %s` writing to repository refs or contents" % method))
    if words[:2] == ["repo", "delete"]:
        return Decision.deny(lane_message(ctx, "`gh repo delete`"))
    return Decision.allow()


# ---------------------------------------------------------------------------
# Bash command policy
# ---------------------------------------------------------------------------

def resolve_cd(current, argv):
    name = shell_scan.command_name(argv[0]) if argv else ""
    if name not in ("cd", "pushd", "popd"):
        return current
    if name == "popd":
        return None
    targets = [t for t in argv[1:] if t == "-" or not t.startswith("-")]
    if not targets:
        return os.path.expanduser("~")
    target = targets[0]
    if "$" in target or target == "-" or target.startswith("~") and target != "~" or current is None:
        return None
    return os.path.normpath(os.path.join(current, os.path.expanduser(target)))


def check_bash(ctx, command, depth=0):
    if depth > 3:
        return Decision.deny(lane_message(ctx, "nested shell payload too deep to analyse"))
    segments, flags = shell_scan.split_segments(command)
    if flags.substitution:
        visible = shell_scan.strip_substitutions(command)
        if _INLINE_GIT_RE.search(visible):
            return Decision.deny(lane_message(
                ctx, "command substitution next to a git branch operation",
                "Write the branch name literally instead of computing it."))
    current = None if flags.cwd_unreliable else ctx.cwd
    for segment in segments:
        tokens = shell_scan.tokenize(segment)
        if tokens is None:
            if _INLINE_GIT_RE.search(segment) or not ctx.on_lane:
                return Decision.deny(lane_message(ctx, "command with unbalanced quoting references git branch operations"))
            continue
        argv, writes, _reads = shell_scan.extract_redirects(tokens)
        env_names, argv = shell_scan.strip_env_prefix(argv)
        more_env, argv = shell_scan.unwrap_wrappers(argv)
        env_names |= more_env
        segment_dir = current
        in_scope = ctx.owns_directory(segment_dir) if segment_dir is not None else True

        for target in writes:
            if target in ("/dev/null", "/dev/stdout", "/dev/stderr") or target.isdigit():
                continue
            base = segment_dir if segment_dir is not None else ctx.cwd
            path = os.path.normpath(os.path.join(base, os.path.expanduser(target)))
            if ctx.inside_git_dir(path):
                return Decision.deny(lane_message(ctx, "writing into .git/ directly"))
            if in_scope and not ctx.on_lane and ctx.inside_repo(path):
                return Decision.deny(off_lane_message(ctx, "writing to '%s'" % target))

        if not argv:
            current = resolve_cd(current, argv)
            continue

        payload = shell_scan.shell_payload(argv)
        if payload is not None:
            nested = check_bash(ctx, payload, depth + 1)
            if not nested.allowed:
                return nested
            current = resolve_cd(current, argv)
            continue

        name = shell_scan.command_name(argv[0])
        if in_scope:
            for code in shell_scan.interpreter_payloads(argv):
                if _INLINE_GIT_RE.search(code):
                    return Decision.deny(lane_message(ctx, "inline %s code runs git branch operations" % name,
                                                      "Run git directly so the guard can check it."))
        if name == "git":
            decision = check_git(ctx, argv, env_names, segment_dir)
            if not decision.allowed:
                return decision
        elif name == "gh" and in_scope:
            decision = check_gh(ctx, argv)
            if not decision.allowed:
                return decision
        elif in_scope and not ctx.on_lane:
            if name not in SHELL_READ_ONLY:
                return Decision.deny(off_lane_message(ctx, "running `%s`" % name))
            if name == "sed" and any(t == "-i" or t.startswith("-i") or t.startswith("--in-place") for t in argv[1:]):
                return Decision.deny(off_lane_message(ctx, "`sed -i`"))
            if name == "find" and any(t in ("-delete", "-exec", "-execdir", "-ok", "-okdir") for t in argv[1:]):
                return Decision.deny(off_lane_message(ctx, "`find` with side effects"))
        for tok in argv[1:]:
            if tok.startswith("-"):
                continue
            base = segment_dir if segment_dir is not None else ctx.cwd
            path = os.path.normpath(os.path.join(base, os.path.expanduser(tok)))
            if name in ("rm", "mv", "cp", "tee", "truncate", "ln", "install", "rsync", "dd") and ctx.inside_git_dir(path):
                return Decision.deny(lane_message(ctx, "`%s` touching .git/ directly" % name))
        current = resolve_cd(current, argv)
    return Decision.allow()


# ---------------------------------------------------------------------------
# Tool dispatch
# ---------------------------------------------------------------------------

GITHUB_TOOL_PREFIX = "mcp__github__"


def check_github_mcp(ctx, tool_name, tool_input):
    tool = tool_name[len(GITHUB_TOOL_PREFIX):]
    owner = str(tool_input.get("owner", "")).lower()
    repo = str(tool_input.get("repo", "")).lower()
    origin = ctx.origin_url.lower()
    if owner and repo and origin and ("%s/%s" % (owner, repo)) not in origin.replace(".git", ""):
        return Decision.allow()  # another repository
    if tool in ("push_files", "create_or_update_file", "delete_file"):
        branch = tool_input.get("branch")
        if branch != ctx.lane:
            return Decision.deny(lane_message(ctx, "GitHub API write to branch '%s'" % branch))
        return Decision.allow()
    if tool in ("create_branch", "merge_pull_request", "enable_pr_auto_merge", "update_pull_request_branch"):
        return Decision.deny(lane_message(ctx, "`%s` via the GitHub API" % tool,
                                          "Branch creation and merging are owner decisions."))
    if tool == "create_pull_request":
        head = str(tool_input.get("head", ""))
        if head.split(":", 1)[-1] != ctx.lane:
            return Decision.deny(lane_message(ctx, "pull request from head '%s'" % head))
        if tool_input.get("base") == ctx.lane:
            return Decision.deny(lane_message(ctx, "pull request targeting the lane itself"))
        return Decision.allow()
    if tool == "pull_request_review_write" and str(tool_input.get("event", "")).upper() == "APPROVE":
        return Decision.deny(lane_message(ctx, "approving a pull request", "Claude Code does not approve pull requests."))
    return Decision.allow()


def evaluate(ctx, payload):
    tool_name = str(payload.get("tool_name", ""))
    tool_input = payload.get("tool_input") or {}
    if not isinstance(tool_input, dict):
        tool_input = {}
    if tool_name in EDIT_TOOLS:
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or tool_input.get("path") or ""
        if not path:
            return Decision.allow()
        full = path if os.path.isabs(path) else os.path.join(ctx.cwd, path)
        if not ctx.inside_repo(full):
            return Decision.allow()
        if ctx.inside_git_dir(full):
            return Decision.deny(lane_message(ctx, "editing files inside .git/"))
        if not ctx.on_lane:
            return Decision.deny(off_lane_message(ctx, "editing '%s'" % path))
        return Decision.allow()
    if tool_name == "Bash":
        command = tool_input.get("command")
        if not isinstance(command, str) or not command.strip():
            return Decision.allow()
        return check_bash(ctx, command)
    if tool_name.startswith(GITHUB_TOOL_PREFIX):
        return check_github_mcp(ctx, tool_name, tool_input)
    return Decision.allow()


def main():
    if os.environ.get("HELEOS_BRANCH_GUARD", "").lower() in ("off", "0", "false"):
        return 0
    raw = sys.stdin.read(MAX_STDIN)
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except ValueError:
        sys.stderr.write("%s could not parse hook input; allowing.\n" % TAG)
        return 0
    if not isinstance(payload, dict):
        return 0
    root = find_repo_root()
    if root is None:
        sys.stderr.write("%s .claude/work-branch not found; allowing.\n" % TAG)
        return 0
    lane = read_lane(root)
    if not lane:
        sys.stderr.write("%s .claude/work-branch is empty; allowing.\n" % TAG)
        return 0
    ctx = RepoContext(root, lane, payload.get("cwd") or None)
    try:
        decision = evaluate(ctx, payload)
    except Exception as exc:  # noqa: BLE001 - fail closed only for git-shaped input
        text = json.dumps(payload.get("tool_input") or {})
        if payload.get("tool_name") in EDIT_TOOLS or "git" in text or "gh " in text:
            sys.stderr.write("%s internal error (%s); denying to stay in lane. Retry with a simpler command.\n"
                             % (TAG, exc.__class__.__name__))
            return 2
        return 0
    if decision.allowed:
        return 0
    sys.stderr.write(decision.reason + "\n")
    return 2


if __name__ == "__main__":
    sys.exit(main())
