# Local guarded worker runner

This crate executes one explicitly configured Codex, Claude Code, Kimi, or Grok
command in an independent local Git clone detached at a validated task's exact
base. It does not create a source worktree registration, share object
hardlinks, fetch, push, merge, create a candidate commit, or run acceptance
commands.

The library exposes `run_json(input, config)` for preserving original JSON bytes
and `run(validated_task, config)` for already-validated callers. Both return a
typed `RunResult` or `RunError`. Provider input is a bounded generated prompt on
stdin; the executable and ordered arguments are passed directly to `Command`.
Only `codex`, `claude_code`, `kimi`, and `grok` are admitted in this slice;
`cursor` remains unsupported and research providers fail before launch. Codex
admission and its stdin adapter have local executable-fixture coverage plus one
authenticated PUBLIC-only Codex/Astra write through the runner's explicit
`none` containment mode. That run does not claim outer host containment; its
failed Seatbelt precursor, version gate, hashes, and limits are in
`evidence/live-codex-run.md`. Grok has both local-fixture coverage and one
PUBLIC-only live write with controller hash acceptance under macOS Seatbelt;
its evidence is in `evidence/live-grok-run.md`.

## CLI

Supply existing absolute source, workspace-root, and executable paths. The
workspace root must be disjoint from the source checkout. The provider must
already be installed and authorized; this program does not authenticate it.

```text
cargo run -p heleos-worker-runner --locked --offline -- \
  --task /absolute/task.json \
  --source /absolute/source-checkout \
  --workspace-root /absolute/worker-runs \
  --provider claude_code \
  --command /absolute/provider-executable \
  --git /usr/bin/git \
  -- literal-provider-argument
```

Choose the provider's arguments that consume prompt text from stdin. The runner
does not infer a provider's CLI syntax. Environment inheritance is disabled:
the provider receives a fixed system `PATH` and fresh local `HOME`/`TMPDIR`.
Repeat `--inherit-env NAME` only for explicitly authorized environment names;
values are passed directly to the child and are not placed in runner prompts
or reports. The library also accepts explicit environment entries.

For Codex CLI 0.153.4, use `--provider codex --command /absolute/python3` and
the literal arguments after `--`:

```text
-B /absolute/repository/scripts/provider-adapters/codex-stdin.py --codex-executable /absolute/codex
```

The Python 3.9-compatible adapter requires exactly one absolute regular
executable and fixes the provider arguments to:

```text
exec --ephemeral --ignore-user-config --ignore-rules --strict-config --model gpt-6-astra --sandbox workspace-write --json --color never -
```

The adapter validates up to 65,536 UTF-8 prompt bytes, rejects NUL and invalid
UTF-8, and sends the original bytes through the provider's stdin pipe. The
prompt never enters argv or a temporary file. Empty input is preserved. No
shell, user-supplied provider flags, configuration overrides, MCP/plugin
controls, output paths, or permission-bypass flags are added. The caller must
verify the installed binary/version separately; the adapter does not probe it.
Its 65,536-byte limit still applies if the outer runner permits a larger prompt.

Ordinary provider exits and raw stdout/stderr pass through. Fixed adapter
diagnostics use exit 64 for invalid arguments/executable, 65 for invalid prompt,
70 for launch failure, 71 for provider or catchable adapter signals, and 74 for
stdin read failure. Catchable SIGINT/SIGTERM/SIGHUP during provider execution
terminate and reap the direct child; descendant cleanup and timeouts belong to
the outer runner. Codex's `workspace-write` option is additional provider
configuration, not evidence that the runner's optional host containment was
enabled or that arbitrary inherited authority was restricted.

Containment is explicit: use `--containment macos_seatbelt` on macOS or
`--containment windows_restricted_token_job` on Windows (or select the matching
`RunnerConfig.containment` variant). The default is `none` on Unix. Windows
rejects `none` and every non-Windows mode before launch, so it has no
uncontained fallback. There is no custom profile, sandbox executable, token, or
Job Object option.

The macOS backend validates the installed `/usr/bin/sandbox-exec`: its canonical
path must be exactly that path, and it must be a regular root-owned executable
without group/other write permissions. It applies a static Seatbelt profile to
the provider and descendants, allowing path-based filesystem writes only within
the canonical checkout, ephemeral home, and ephemeral temp roots. Literal `-D`
parameters preserve spaces and metacharacters without a shell. Canonicalization
handles macOS `/var` versus `/private/var` paths. Paths outside those roots,
including run evidence and writable host devices such as `/dev/null`, receive
no write exception. Runner Git setup, evidence persistence, and inventory run
outside that policy.

The Windows backend creates the checkout, ephemeral home, and ephemeral temp as
empty local NTFS directories, adds and verifies inheritable write permission for
the Write Restricted Code SID, and retains no-delete handles before Git
materializes the checkout. It launches the provider suspended under a restricted
token with exactly the three standard pipe handles inherited, assigns it to a
fresh kill-on-close Job Object, verifies membership, and only then resumes it.
Timeout and normal provider exit terminate and reap the complete Job tree before
inventory. Run evidence is outside the three writable roots. Reparse points,
hard links, identity changes, unsupported filesystems, unsafe environment or
command forms, and containment setup failures stop the run before an uncontained
provider can execute.

Successful JSON evidence records `containment.mode`, the restricted write scope,
whether the process tree is contained, and explicit false values for read,
network, and inherited external authority restrictions. Unsupported platforms
or failed pre-launch backend validation return `containment_unavailable`; no
fallback launches an uncontained provider.
Failure evidence retains the typed terminal reason. Once sandbox-exec starts,
policy initialization failures and provider failures share its exit status and
are reported as `provider_exit`; stderr is retained without heuristic parsing.

Exit zero means a proposal was generated and inventoried. Standard output is a
`heleos.worker-run/v1` JSON envelope containing the retained checkout location,
actual changed-file SHA-256 identities, bounded-output metadata, and a nested
validated `heleos.worker-handoff/v1`. Its terminal state is `blocked`, with no
check records, because controller acceptance is still required. Exit two emits
a fixed-diagnostic `heleos.worker-run-failure/v1` JSON envelope. Untrusted task
content and raw provider output are not echoed in fixed diagnostics.

## Bounds and evidence

The default prompt limit and each retained output-stream limit are 65,536 bytes;
`--max-prompt-bytes` and `--max-output-bytes` accept positive values up to
1,048,576. Excess stdout/stderr is drained and marked truncated. An oversized
required prompt fails before provider launch instead of dropping instructions.
Every instruction file must be a regular file at the exact base and its bytes
must match the packet's SHA-256 value. Verified instruction content is included
in the prompt.

The elapsed-time budget includes preparation, provider execution, and inventory.
One provider invocation is the one action this adapter observes. It does not
attest internal provider tool calls, action limits, or cost accounting.

Git inventory uses NUL-delimited diff and untracked-file output, including
ignored files and both sides of renames. An independent filesystem snapshot
catches hidden index flags. Each snapshot is bounded to 256 MiB of read file
content and 100,000 files/directories. Symlinks, hardlinks, special files,
unrepresentable changed paths, missing inventory, and unsafe Git metadata fail
closed. Ordinary staging and commits can be observed, but the runner itself
does neither and does not claim a candidate commit.

Each fresh owned run directory retains `ownership.json`, the original and
canonical task, canonical task digest, generated prompt, bounded `stdout.bin`
and `stderr.bin`, and either `run.json`/`handoff.json` or `failure.json` where
writing evidence succeeds. Ownership records contain the actual directory path
and device/inode. Task, prompt, and ownership bytes are checked before handoff.
Evidence creation uses an owned directory handle and exclusive no-follow opens.
Existing evidence names are never overwritten by the runner.

Success and failure retain their directories by default. The optional
`--cleanup-on-failure` deletes only the identity-checked directory allocated by
that invocation after process termination. Failed ownership checks retain it
and report `cleanup_failed`; evidence failures report `evidence_write_failed`
without erasing the original terminal reason. No success-cleanup API is exposed.

## Explicit operational limits

With default `none` on Unix, no OS filesystem containment is applied. The
opt-in macOS backend restricts new path-based filesystem writes for the provider
process tree on the tested host. The required Windows mode restricts provider
tree writes and owns provider lifecycle through a restricted token and Job
Object. None of these modes restricts reads, network, credentials, inherited
external authority, provider internal actions, or cost. The controller must
arrange any further restrictions and egress authorization independently.
Post-run inventory describes final observable project files; it cannot detect
a write that was reverted; without containment it cannot detect arbitrary host
writes by a hostile executable. Inventory remains mandatory in both modes.

The Unix process backend uses process groups and nonblocking pipes. Timeouts and
normal exits terminate same-group background descendants before inventory. A
descendant that deliberately escapes its process group requires separate
process-lifecycle containment, although Seatbelt write restrictions remain
inherited. The Windows backend instead terminates the complete Job tree.
External termination of the runner is not a guaranteed cancellation mechanism
in this slice.

Live PUBLIC-only Claude Code and Grok tasks have each completed one exact-scope
write under macOS Seatbelt and passed controller hash acceptance. Codex/Astra
completed an exact-scope write under explicit runner containment mode `none`
and its own `workspace-write` setting after the current Seatbelt profile denied
Codex runtime-state initialization. Kimi remains blocked by its combined
credential/runtime data-root design; real authentication state is not copied
into the ephemeral home. Windows code has passed host tests and MSVC
cross-compilation, but the required native Windows/NTFS gate has not run. This
slice does not claim App Sandbox, regulatory containment, production authority,
Foundation acceptance, or native Windows acceptance.

## Local verification

```text
cargo +1.96.1 test -p heleos-worker-runner --locked --offline
cargo +1.96.1 clippy -p heleos-worker-runner --all-targets --locked --offline -- -D warnings
cargo +1.96.1 fmt -p heleos-worker-runner -- --check
python3 -B tests/provider-adapters/test_grok_stdin.py
python3 -B tests/provider-adapters/test_codex_stdin.py
/usr/bin/python3 -B tests/provider-adapters/test_codex_stdin.py
# Run only from a clean native Windows/NTFS checkout:
pwsh -File scripts/verify-windows-worker-containment.ps1
```

Tests use temporary local Git repositories and fake local provider executables.
No authenticated provider, external service, or network call is involved.
The macOS integration test uses the real kernel backend and proves allowed-root
writes, host/sibling and descendant denial, symlink escape denial, literal paths
and arguments, accurate run evidence, and continued inventory enforcement.
The Windows-only suites compile on the MSVC target and are named explicitly by
the PowerShell gate, but cross-compilation is not native execution evidence. The
`evidence/` directory retains causal RED results, GREEN commands, and the exact
remaining native gate.
