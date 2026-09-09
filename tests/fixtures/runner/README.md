# Public synthetic runner fixtures

The deterministic fake-provider fixtures contain only authored `PUBLIC`
synthetic data. They contain no credentials, private source material, provider
sessions, remote URLs, or actual model output. They establish no provider
availability, authorization, or live acceptance result. Do not dispatch them as
a live worker assignment.

The `live/` directory is different: it retains the exact controller-accepted
`PUBLIC` synthetic file bytes produced by recorded headless provider runs. Those
files contain no credentials or private project material, but they are actual
provider output. Their run and acceptance identities are recorded under
`crates/heleos-worker-runner/evidence/`; the files alone do not authorize a new
provider run or prove current provider availability.

`fake_provider.py` uses the Python standard library, consumes at most 65,536
bytes of prompt data from stdin without echoing it, and makes fixed relative
filesystem changes. It never invokes a shell, another process, or the network.
Invoke an explicitly selected Python executable with this ordered argument
vector; use an absolute path for the script:

```text
["/absolute/path/to/fake_provider.py", "--scenario", "untracked"]
```

The executable is the selected Python interpreter, not the joined vector above.
Run it only with a freshly created disposable synthetic checkout as its working
directory. The harness creates the local Git repository, commits any seed files,
records its real 40-character base SHA and instruction hashes, and constructs a
valid task. Use `PUBLIC` with `local_only`; no external approval is needed to run
this local fixture. The fixture itself does not initialize Git or generate a
task, handoff, or acceptance evidence.

Suggested task scope is allowed path `allowed` and forbidden path `forbidden`.
For `tracked`, `delete`, and `rename`, seed and commit `allowed/tracked.txt` first.
For `ignored`, seed and commit a `.gitignore` entry for `allowed/ignored.txt`.
For `forbidden`, allow both directories and forbid `forbidden` to specifically
exercise forbidden-path precedence; with only `allowed` it also tests an
out-of-allowlist change.

| Scenario | Synthetic behavior | Expected evidence |
| --- | --- | --- |
| `noop` | Emits one fixed stdout and stderr line | Terminal exit 0 with empty inventory |
| `tracked` | Replaces `allowed/tracked.txt` | Tracked edit included |
| `untracked` | Creates `allowed/new.txt` | Untracked file included |
| `ignored` | Creates `allowed/ignored.txt` | Ignored untracked file included |
| `delete` | Deletes seeded `allowed/tracked.txt` | Deleted path included |
| `rename` | Moves seeded `allowed/tracked.txt` to `forbidden/moved.txt` | Both paths included; candidate rejected |
| `forbidden` | Creates `forbidden/new.txt` | Candidate rejected even though provider exits 0 |
| `nonzero` | Emits fixed diagnostic and exits 7 | Failed terminal outcome retained |
| `timeout` | Sleeps for five seconds | Use a shorter runner deadline; timeout retained |
| `large-log` | Emits one MiB each to stdout and stderr | Bounds and truncation recorded; no pipe deadlock |

The fake provider does not claim to pass acceptance commands. An exit-0 fixture
result proves only that the fixture process terminated successfully. Compare
inventory and source-checkout state independently, validate the runner's handoff
against the original task, and retain failure evidence according to the
[runner contract](../../../docs/operations/guarded-worker-runner.md).
