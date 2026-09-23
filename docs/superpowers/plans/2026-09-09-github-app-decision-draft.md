# GitHub App owner-decision draft generator plan

**Status:** Authorized local release-enablement implementation

**Base:** `1131541f2d0d3cbe5648cde91755bc306ccf111e`

**Purpose:** Remove manual HEAD/hash/name transcription from the first unfinished Foundation 0.1 owner gate while preserving the fact that only Bekim Bukolla can supply the four decisions and authentic evidence.

## Binding boundaries

- Work only in `.worktrees/github-app-decision-draft-2026-09-09` on `build/github-app-decision-draft-2026-09-09` until controller integration.
- Do not modify `governance/github-apps.toml`, `.github/workflows/**`, release evidence, the acceptance dossier, account settings, remote Git, credentials, or any Foundation product/runtime source.
- Do not select or infer a disposition, decision date, permission, egress rule, evaluation, or evidence reference. Public App metadata is not installation evidence.
- The new command prepares one deliberately incomplete local draft. It does not apply a decision, authenticate the owner, validate evidence, access the network, commit, push, or grant release authority.
- Use test-first development. The black-box test must fail because the executable is absent before implementation begins.
- The owner's no-reviewer-agent direction remains binding. Astra agents may write only their assigned paths; the controller performs deterministic inspection and verification.

## Task 1: Prepare a safe, visible, stale-bound owner draft

### Files

- Create `scripts/prepare-github-app-decisions.py`.
- Create `tests/continuity/test_prepare_github_app_decisions.py`.
- Add `/OWNER_ACTION_REQUIRED/*.json` to `.gitignore`.
- Create `OWNER_ACTION_REQUIRED/README.md`.
- Update `docs/operations/github-app-decisions.md`, `README.md`, and `SKILLS.md` only for discoverability and the exact operator flow.

### CLI and report

The exact interface is:

```text
python3 scripts/prepare-github-app-decisions.py [--repo PATH] --output PATH [--human]
```

`--repo` defaults to `.`. `--output` is required and must be a canonical direct `.json` child of the unique visible-main checkout's `OWNER_ACTION_REQUIRED` directory. That directory must already exist, be a real non-symlink directory, and the output path must be ignored and untracked. The command never creates directories and has no force, overwrite, update, refresh, apply, network, or evidence-discovery mode.

Default output is deterministic compact JSON with exact top-level keys:

```text
schema_version, status, main, registry, packet, unresolved_fields,
write_performed, account_changes_performed, owner_authenticity_verified,
release_authority, errors
```

Success is `PREPARED` with exit `0`; failure is `FAIL` with exit `1`. `unresolved_fields` is exactly `29`; the three authority booleans are always false except `write_performed`, which becomes true only after publication. Human output remains concise and cannot turn a failure into success.

### Draft bytes

The draft is indented UTF-8 JSON with one terminal newline and the existing `heleos.github-app-decisions/v1` seven-field root shape in this order:

1. `schema` = `heleos.github-app-decisions/v1`
2. `repository` = `bbukolla-eng/Heleos-spark`
3. `expected_head` = the inspected full visible-main HEAD
4. `expected_registry_sha256` = SHA-256 of the exact committed `governance/github-apps.toml` bytes
5. `decision_owner` = `Bekim Bukolla`
6. `decision_date` = JSON `null`
7. `apps` = the four canonical App records in registry order

Each App record contains fields in this order: `name`, `disposition`, `version_or_digest`, `permissions`, `egress`, `evaluation`, `decision_evidence`, `installation_evidence`. Only `name` is populated; the other seven fields are JSON `null`. The generator never copies current registry dispositions, metadata, permissions, evaluations, dates, or references. The intended owner value identifies the recipient and is not a signature.

The untouched draft must fail the existing schema/applier. A separately filled synthetic draft in tests must pass the existing applier's dry run without changing the repository.

### Source and destination safety

- Reuse the repository's canonical-path, unique-main, bounded-file, strict-registry, and Git command helpers rather than weakening them.
- Reject the nine Git routing/configuration override variables already rejected by the applier.
- Require visible main to remain on the same exact HEAD, with no tracked changes, and require the working registry, index entry, Git executable classification, identity, and bytes to match the committed regular file. Capture the full live mode only for stability rechecks; Git does not attest arbitrary owner/group permission bits.
- Capture and recheck the registry and output-parent identities immediately before publication.
- Reject an existing output of every type, including byte-identical files, symlinks, directories, FIFOs, and competing creations.
- Write and fsync a private same-directory temporary regular file, then publish atomically without replacement. Do not use `os.replace` or check-then-overwrite. Clean up only the command's own temporary inode. Fsync the containing directory where supported and verify final bytes, identity, hash, and private mode.
- A source or destination race fails closed with fixed diagnostics. Never echo packet contents, credential-shaped input, or exception text.
- Aside from the requested ignored draft, Git HEAD/index/registry/workflows and unrelated tracked or untracked files remain byte-identical.

### Test-first acceptance

Black-box tests use only disposable synthetic Git repositories and hand-derived expected bytes. They must cover:

1. Exact 29-null draft, canonical App order, stable SHA-256, private mode, and deterministic different-filename reruns.
2. Untouched-draft rejection plus a filled synthetic end-to-end dry run through the existing applier.
3. Unique-main resolution from a nested linked worktree.
4. Dirty/staged/malformed/missing/ambiguous registry state, moved HEAD, Git override variables, and wrong/noncanonical repo paths.
5. Output outside `OWNER_ACTION_REQUIRED`, traversal/alias/symlink parents, unignored or tracked targets, existing targets of every type, and a competing creator.
6. Registry or HEAD drift before publication and temporary-file interference without unsafe cleanup.
7. Fixed, secret-safe failures; unchanged repository/index/registry/workflows and unrelated files; no account, owner-authenticity, or release-authority claim.
8. `--help`, required/unknown arguments, compact JSON output, and human output under Python 3.14 and system Python 3.9.

After focused GREEN, run the entire continuity suite on both Python runtimes, all research tests, JSON/AST parsing, hash and file-mode checks, and `git diff --check`. Stage exact assigned paths, commit implementation, record the tested commit in `CURRENT_STATUS.md`, empty the active-build authority, commit the checkpoint, and fast-forward visible local `main`. Generate the real unresolved draft only after the final integration commit so its HEAD binding is current.
