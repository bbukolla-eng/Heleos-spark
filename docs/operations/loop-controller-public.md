# Local admitted-task controller

This branch contains a runnable Mac controller for already admitted takeoff-engine work. It advances a pinned queue through a guarded Claude worker, ordered checks, an independently supplied review, and at most one linked repair. SQLite retains intents and outcomes across a controller restart. A foreground `run` has explicit action and time budgets; it never selects the next product priority, starts a daemon, loads a model, merges, or publishes work.

## Build and checks

From a clone with the pinned Rust toolchain and locked dependencies available:

```sh
cargo build -p heleos-worker-runner --locked
cargo test --workspace --locked
PYTHONDONTWRITEBYTECODE=1 python3 -B -m unittest discover -s tests -p 'test_*.py' -v
python3 tools/ci/checks.py
```

The macOS runner uses `/usr/bin/sandbox-exec` to restrict host writes for the provider process. Host reads and network are not restricted. The runner clones the whole source repository, including its Git history, beside the selected provider view; the provider could read that clone by relative path. The exact CLI profile also inherits the user's `HOME` for existing Claude account authentication, so that directory remains readable. A PUBLIC admission therefore requires a public-only source checkout and public Git history, plus authorization for this host read boundary. Selected-source hashes pin intended inputs, not exclusive read or egress access. The local integrated test uses a fake provider and synthetic source; it does not make a model or product-quality claim. On Linux, CI runs the Python fault tests and all Rust workspace tests, while the macOS Seatbelt integration test is skipped.

## Portable v7 admission

The public-base route uses `heleos.loop-controller/v7` in a queue manifest. The queue pins the exact checkout HEAD, worktree, result paths, dependency IDs, worker, check and review guard specs, candidate paths, a separate independent review-source path, and the named native Workflow child. The child contract includes `model: claude-opus-5-5`, `effort: xhigh`, a reviewed script hash, and `report_binding: controller_receipt`. The controller rejects changed manifests, specs, source bytes, or candidate bytes.

Before dispatch, the controller requires a separately reviewed `heleos.loop-admission/v1` JSON packet. It binds the queue SHA-256, approved scope, canonical runner workspace outside the checkout, exact runner and provider executable SHA-256, each worker-task packet path/SHA-256, each worker guard command, and an exact egress record. The `heleos.loop-egress/v1` record names `decision: approved`, `provider: claude_code`, every task ID, and the selected source path/SHA-256 map for each task. These files and the packet hashes are supplied by the controller's human coordinator; writing `approved` into a file does not grant provider or data authorization.

The worker task uses `heleos.worker-task/v1`, `provider: claude_code`, `mode: implementation`, the queue's base commit and exact candidate paths, and `input_data_class: PUBLIC` or separately authorized `INTERNAL`. Its guard spec pins the packet and every selected source input. The runner command uses the built `heleos-worker-runner`, the same packet, source checkout and workspace, `macos_seatbelt`, `--capture-claude-stream-trace`, and one `--selected-source-file` argument per pinned source. INTERNAL packets require the exact `--approved-internal-task-sha256`; PUBLIC packets must omit that flag. The admitted command requires the exact Claude CLI profile, including the selected model, effort, restricted tools, $5 budget cap, disabled MCP sources, and a fixed six-name environment inheritance list. Extra runner or provider arguments are rejected.

Run a reviewed queue with the four absolute paths and hashes filled in:

```sh
python3 -B scripts/loop_controller.py \
  --manifest /absolute/checkout/queue.json \
  --admitted-manifest-sha256 MANIFEST_SHA256 \
  --admission /absolute/checkout/admission.json \
  --admission-sha256 ADMISSION_SHA256 \
  run --max-actions 1000 --max-seconds 3600 --poll-seconds 0.25
```

After checks, the controller waits for a different reviewer to write a source JSON object binding `decision`, `reviewer`, `candidate_sha256`, `checks`, and `runner_evidence` to the durable ledger. The pinned review guard may use `scripts/loop_review_gate.py` to copy that accepted source into the review output. A missing or mismatched source stops advancement. Cancellation, uncertain ownership, failed checks, exhausted repair, and a changed approval boundary stop the queue for human reconciliation.

Legacy local P1 v2-v6 admission profiles remain fail-closed without their exact, separately retained contracts and inputs. The portable v7 route is the supported public-base entry point. It prepares infrastructure for the Division 23 takeoff engine; complete section behavior, model inference, pricing, and product acceptance remain separate admitted work.
