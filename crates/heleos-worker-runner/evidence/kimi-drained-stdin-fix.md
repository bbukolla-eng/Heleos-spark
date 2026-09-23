# Kimi adapter drained-stdin regression

Worker: Codex bounded implementation subagent `kimi_containment_fix`.
Checkout: `/Users/bekim/Heleos-spark/.worktrees/agent-control-foundation`.
Branch: `build/agent-control-foundation`.
Base: `28daf4c0f213540a05f1928f4936ee641250686e`.

The controller supplied the independently reproduced cause: Python 3.9's
`subprocess.DEVNULL` opens `/dev/null` with `os.O_RDWR`, which the fixed Seatbelt
policy denies with EPERM. The adapter consequently returns its fixed launch
failure before the provider starts.

The added causal test runs the adapter in a harness that raises EPERM on any
`os.open('/dev/null', ...)` call. It launches a real fixture child through the
unchanged subprocess boundary, using a prompt pipe closed by the test driver.
The child reports exact argv and two consecutive one-byte reads from fd 0;
both reads must return EOF. The fixture uses public synthetic input only.

The implementation changes only the stdin selection from `subprocess.DEVNULL`
to `None`, inheriting the stdin already drained to EOF for an accepted prompt.
Shell avoidance, bounds, diagnostics, and exit handling remain in place.

## Commands and terminal results

Commands ran from the checkout above on macOS. All processes are terminal.

1. Before implementation:
   `python3 -B tests/provider-adapters/test_kimi_stdin.py KimiStdinTests.test_denied_devnull_still_launches_child_with_drained_stdin -v`
   exited 1: 1 test, 1 failure. The assertion observed `70 != 0` with
   `b'kimi-stdin: provider launch failed\n'`.
2. After implementation:
   `python3 -B tests/provider-adapters/test_kimi_stdin.py -v`
   exited 0: all 20 tests passed on Python 3.14.6.
3. `/usr/bin/python3 -B tests/provider-adapters/test_kimi_stdin.py -v`
   exited 0: all 20 tests passed on Python 3.9.6.
4. `python3 -B -c 'import ast, pathlib; paths = [pathlib.Path("scripts/provider-adapters/kimi-stdin.py"), pathlib.Path("tests/provider-adapters/test_kimi_stdin.py")]; [ast.parse(path.read_bytes(), filename=str(path)) for path in paths]; print("Syntax OK: 2 files")'`
   exited 0 and printed `Syntax OK: 2 files`.
5. `git diff --check` exited 0.

Changed source SHA-256 identities:

- `scripts/provider-adapters/kimi-stdin.py`:
  `feb3149f49f386153dc282eab8953b80cb84a8b5c4e1138db42779d276656315`
- `tests/provider-adapters/test_kimi_stdin.py`:
  `77b5545ff95a49ddbc27c632a62c211a9205f3292eb3de17be664a489013c8e6`

This worker changed only those two files and this evidence file. The existing
`CURRENT_STATUS.md` modification belongs to the controller and was preserved.
The adapter evidence directory did not exist, so this report uses the existing
runner evidence directory as assigned.

Implementation finished and focused local checks passed. No real Kimi,
network, Seatbelt-policy edit, commit, push, or independent review was performed.
The regression simulates the causal denied open; it does not establish live
authenticated provider compatibility or Windows behavior. Next action: the
controller verifies these exact candidate bytes and continues its authorized
contained provider smoke task.
