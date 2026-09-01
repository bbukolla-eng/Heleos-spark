"""Small, dependency-free shell command scanner used by branch_guard.py.

The goal is not a full shell parser. It is a conservative approximation that
splits a command line into simple-command segments, tracks the constructs that
make static analysis unreliable (subshells, command substitution, braces), and
normalises each segment into an argv list with redirections separated out.

Whenever the scanner is unsure, it reports the ambiguity so the guard can fail
closed instead of guessing.
"""

import re
import shlex

# Environment variables that change which repository or configuration git
# operates on. Their presence disables the "another repo" exemption.
GIT_REDIRECT_ENV = {
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_COMMON_DIR",
    "GIT_NAMESPACE",
    "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY",
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CEILING_DIRECTORIES",
}
# Environment variables that can inject configuration (aliases, remotes, hooks).
GIT_CONFIG_ENV_PREFIXES = ("GIT_CONFIG", "HOME", "XDG_CONFIG_HOME", "GIT_EXEC_PATH", "PATH")

# Wrapper commands whose real command follows their own options.
_SIMPLE_WRAPPERS = {"command", "builtin", "exec", "nohup", "time", "unbuffer"}
_OPTION_VALUE_WRAPPERS = {
    # wrapper -> options that consume the next token
    "sudo": {"-u", "-g", "-C", "-D", "-h", "-p", "-r", "-t", "-T", "-U"},
    "doas": {"-u", "-C"},
    "env": {"-u", "-C", "-S", "--unset", "--chdir", "--split-string"},
    "timeout": {"-s", "-k", "--signal", "--kill-after"},
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "-n", "-p"},
    "stdbuf": {"-i", "-o", "-e"},
    "caffeinate": {"-t", "-w"},
    "xargs": {"-n", "-P", "-I", "-L", "-s", "-d", "-E", "-a", "--max-args", "--max-procs",
              "--replace", "--max-lines", "--max-chars", "--delimiter", "--eof", "--arg-file"},
}

_ENV_ASSIGN_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")
_REDIRECT_RE = re.compile(r"^(\d*)(&>>|&>|>>|>\||>&|<&|<<<|<<-|<<|<>|>|<)(.*)$")
_SUBSTITUTION_RE = re.compile(r"\$\([^)]*\)|`[^`]*`", re.DOTALL)


class ScanFlags(object):
    """What the scanner saw that weakens static analysis."""

    def __init__(self):
        self.substitution = False  # $( ... ) or backticks
        self.grouping = False      # ( ... ), { ... }, process substitution
        self.heredoc = False       # << bodies (skipped as opaque text)
        self.unbalanced = False    # unterminated quote or trailing backslash

    @property
    def cwd_unreliable(self):
        return self.substitution or self.grouping or self.unbalanced


def strip_substitutions(command):
    """Remove $( ... ) and backtick spans so adjacency tricks become visible."""
    return _SUBSTITUTION_RE.sub("", command)


def _read_heredoc_delim(command, i):
    """Parse the delimiter word after ``<<``; returns (delimiter, new_index)."""
    n = len(command)
    while i < n and command[i] in " \t":
        i += 1
    if i < n and command[i] == "-":
        i += 1
    quote = None
    word = []
    while i < n:
        ch = command[i]
        if quote:
            if ch == quote:
                quote = None
            else:
                word.append(ch)
            i += 1
            continue
        if ch in "'\"":
            quote = ch
            i += 1
            continue
        if ch == "\\" and i + 1 < n:
            word.append(command[i + 1])
            i += 2
            continue
        if ch.isspace() or ch in ";|&<>()":
            break
        word.append(ch)
        i += 1
    return "".join(word), i


def _skip_heredoc_bodies(command, i, delims):
    """Skip heredoc body lines that follow the current line. Returns new index."""
    n = len(command)
    for delim in delims:
        while i < n:
            end = command.find("\n", i)
            line = command[i:end if end != -1 else n]
            i = n if end == -1 else end + 1
            if line.strip("\t") == delim or line.strip() == delim:
                break
    return i


def split_segments(command):
    """Split a shell command line into simple-command segments.

    Returns ``(segments, flags)``. Parentheses and braces are treated as
    separators so commands inside them are still scanned; ``flags`` records
    the constructs that make cwd tracking or adjacency unreliable. Heredoc
    bodies are skipped as opaque text and never become segments.
    """
    segments = []
    buf = []
    flags = ScanFlags()
    quote = None
    escaped = False
    pending_heredocs = []
    i = 0
    n = len(command)

    def flush():
        text = "".join(buf).strip()
        if text:
            segments.append(text)
        del buf[:]

    def at_word_start():
        return not buf or buf[-1].endswith((" ", "\t"))

    while i < n:
        ch = command[i]
        nxt = command[i + 1] if i + 1 < n else ""

        if escaped:
            buf.append(ch)
            escaped = False
            i += 1
            continue

        if quote:
            if ch == quote:
                quote = None
            elif quote == '"' and ch == "\\":
                escaped = True
            elif quote == '"' and ch == "$" and nxt in "(`":
                flags.substitution = True
            elif quote == '"' and ch == "`":
                flags.substitution = True
            buf.append(ch)
            i += 1
            continue

        if ch == "\\":
            escaped = True
            buf.append(ch)
            i += 1
            continue

        if ch in "'\"":
            quote = ch
            buf.append(ch)
            i += 1
            continue

        if ch == "#" and at_word_start():
            while i < n and command[i] != "\n":
                i += 1
            continue

        if ch == "`":
            flags.substitution = True
            flush()
            i += 1
            continue

        if ch == "$" and nxt == "(":
            flags.substitution = True
            flush()
            i += 2
            continue

        if ch in "<>" and nxt == "(":
            flags.grouping = True
            flush()
            i += 2
            continue

        if ch in "(){}":
            flags.grouping = True
            flush()
            i += 1
            continue

        if ch == "\n":
            flush()
            i += 1
            if pending_heredocs:
                i = _skip_heredoc_bodies(command, i, pending_heredocs)
                pending_heredocs = []
            continue

        if ch == ";":
            flush()
            i += 1
            continue

        if ch == "|":
            flush()
            i += 2 if nxt in "|&" else 1
            continue

        if ch == "&":
            if nxt == "&":
                flush()
                i += 2
                continue
            if nxt == ">":
                buf.append(" &>")
                i += 2
                if i < n and command[i] == ">":
                    buf.append(">")
                    i += 1
                buf.append(" ")
                continue
            flush()
            i += 1
            continue

        if ch in "<>":
            # Detach a standalone fd number (the 2 in 2>&1) and keep it glued to
            # the operator so ``cat x>file`` and ``cmd 2>/dev/null`` both
            # tokenise cleanly.
            digits = []
            while buf and buf[-1].isdigit():
                digits.append(buf.pop())
            if digits and buf and not buf[-1].endswith((" ", "\t")):
                buf.extend(reversed(digits))
                digits = []
            buf.append(" ")
            buf.extend(reversed(digits))
            op_start = i
            while i < n and command[i] in "<>|":
                buf.append(command[i])
                i += 1
            op = command[op_start:i]
            if op == "<<":
                flags.heredoc = True
                delim, i = _read_heredoc_delim(command, i)
                buf.append(" " + shlex.quote(delim) + " ")
                pending_heredocs.append(delim)
                continue
            if i < n and command[i] == "&":
                buf.append("&")
                i += 1
                if i < n and (command[i].isdigit() or command[i] == "-"):
                    while i < n and command[i].isdigit():
                        buf.append(command[i])
                        i += 1
                    if i < n and command[i] == "-":
                        buf.append("-")
                        i += 1
            buf.append(" ")
            continue

        buf.append(ch)
        i += 1

    if quote or escaped:
        flags.unbalanced = True
    flush()
    return segments, flags


def tokenize(segment):
    """shlex-split a segment. Returns None when quoting is unbalanced."""
    try:
        return shlex.split(segment, posix=True)
    except ValueError:
        return None


def extract_redirects(tokens):
    """Separate redirection operators from a token list.

    Returns ``(argv, write_targets, read_targets)``. ``write_targets`` holds
    the raw target of every output redirection (``>``, ``>>``, ``&>``, ``>|``,
    ``<>``). fd duplications such as ``2>&1`` yield no target.
    """
    argv = []
    writes = []
    reads = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        m = _REDIRECT_RE.match(tok)
        if not m:
            argv.append(tok)
            i += 1
            continue
        op = m.group(2)
        rest = m.group(3)
        if op in (">&", "<&"):
            if rest == "" and i + 1 < len(tokens) and not tokens[i + 1].isdigit() and tokens[i + 1] != "-":
                writes.append(tokens[i + 1])
                i += 2
                continue
            i += 1
            continue
        if op in ("<<", "<<-", "<<<"):
            i += 1 if rest else 2
            continue
        target = rest
        if target == "":
            if i + 1 < len(tokens):
                target = tokens[i + 1]
                i += 1
            else:
                i += 1
                continue
        if op == "<":
            reads.append(target)
        else:
            writes.append(target)
        i += 1
    return argv, writes, reads


def strip_env_prefix(argv):
    """Remove leading VAR=value assignments. Returns ``(env_names, argv)``."""
    names = set()
    idx = 0
    while idx < len(argv) and _ENV_ASSIGN_RE.match(argv[idx]):
        names.add(argv[idx].split("=", 1)[0])
        idx += 1
    return names, argv[idx:]


def command_name(token):
    """Normalise a command token to a bare lowercase program name."""
    name = token.replace("\\", "/").rsplit("/", 1)[-1]
    if name.lower().endswith(".exe"):
        name = name[:-4]
    return name.lower()


def unwrap_wrappers(argv):
    """Peel off wrapper programs (env, sudo, xargs, ...) to reach the real command.

    Also returns the environment names assigned through ``env`` so the caller
    can detect git redirection variables.
    """
    env_names = set()
    changed = True
    while changed and argv:
        changed = False
        name = command_name(argv[0])
        if name in _SIMPLE_WRAPPERS:
            argv = argv[1:]
            while argv and argv[0].startswith("-") and argv[0] != "--":
                argv = argv[1:]
            if argv and argv[0] == "--":
                argv = argv[1:]
            changed = True
            continue
        if name in _OPTION_VALUE_WRAPPERS:
            value_opts = _OPTION_VALUE_WRAPPERS[name]
            rest = argv[1:]
            while rest:
                tok = rest[0]
                if tok == "--":
                    rest = rest[1:]
                    break
                if tok in value_opts:
                    rest = rest[2:]
                    continue
                if tok.startswith("-") and not tok.startswith("--") and len(tok) > 2 and tok[:2] in value_opts:
                    rest = rest[1:]
                    continue
                if tok.startswith("-"):
                    rest = rest[1:]
                    continue
                if name == "env" and _ENV_ASSIGN_RE.match(tok):
                    env_names.add(tok.split("=", 1)[0])
                    rest = rest[1:]
                    continue
                if name == "timeout" and re.match(r"^\d+(\.\d+)?[smhd]?$", tok):
                    rest = rest[1:]
                    continue
                break
            argv = rest
            changed = True
            continue
    return env_names, argv


def shell_payload(argv):
    """If argv runs an inline shell script (bash -c '...', eval ...), return it."""
    if not argv:
        return None
    name = command_name(argv[0])
    if name == "eval":
        return " ".join(argv[1:])
    if name in {"bash", "sh", "zsh", "dash", "ksh", "fish"}:
        for idx, tok in enumerate(argv[1:], start=1):
            if tok == "-c" or (tok.startswith("-") and not tok.startswith("--") and "c" in tok[1:]):
                if idx + 1 < len(argv):
                    return argv[idx + 1]
                return None
    return None


INTERPRETERS = {"python", "python3", "python2", "py", "perl", "ruby", "node", "nodejs", "php",
                "osascript", "deno", "bun", "lua", "tclsh", "pwsh", "powershell"}
_INTERPRETER_INLINE_FLAGS = {"-c", "-e", "-E", "-r", "-l", "--eval", "-p", "--print", "-Command"}


def interpreter_payloads(argv):
    """Return inline code strings passed to interpreters (python -c, node -e...)."""
    if not argv or command_name(argv[0]) not in INTERPRETERS:
        return []
    payloads = []
    for idx, tok in enumerate(argv[1:], start=1):
        if tok in _INTERPRETER_INLINE_FLAGS and idx + 1 < len(argv):
            payloads.append(argv[idx + 1])
        elif tok.startswith("-") and not tok.startswith("--") and len(tok) > 2 and tok[1] in "ce":
            payloads.append(tok[2:])
    return payloads
