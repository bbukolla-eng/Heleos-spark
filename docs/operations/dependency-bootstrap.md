# Dependency bootstrap and offline supply-chain verification

Task 10 starts from source commit `c1596c4cc536155ad3a052cfec2c9b8951814aba`.
Its two entry points are `bash scripts/verify-supply-chain` on native Darwin
ARM64 and `pwsh -NoProfile -File scripts/verify-supply-chain.ps1` on native
Windows x64/NTFS. They install nothing, refresh nothing, and have no skip,
repair, bootstrap, or acceptance-override option. Missing prerequisites or a
failed local check cause failure. A successful local run reports
`SUPPLY_CHAIN_LOCAL_PASS` and explicitly lists deferred platform and release
gates; it does not attest Foundation 0.1 acceptance.

## Pinned verification tools

These are host verification tools with no application runtime authority.
The authoritative owner, permissions, provenance, data classification, and
rollback records are the four exact-name `[[tool]]` entries in
[the tool registry](../../governance/tools.toml). Preserve all earlier registry
bytes when adding an admission. The owner is Heleos engineering; any changed
version, source, checksum, permission, or advisory snapshot needs a new
explicit admission.

| Tool | Version | Official origin | Source archive SHA-256 | License |
| --- | --- | --- | --- | --- |
| cargo-deny | 0.20.2 | [crates.io](https://crates.io/crates/cargo-deny/0.20.2) | `e528dfcbe739af7ce37a77d3d6df1b29dd6887b1c701d888820c0f16b864f737` | MIT OR Apache-2.0 |
| cargo-audit | 0.22.2 | [crates.io](https://crates.io/crates/cargo-audit/0.22.2) | `700c2b240f7fd330c24b675fe429f73a5b676531fcc6300400b2b67f155ba12a` | Apache-2.0 OR MIT |
| cargo-cyclonedx | 0.5.9 | [crates.io](https://crates.io/crates/cargo-cyclonedx/0.5.9) | `5d162f67705f0f5038759d73bf546a083bf30e8677c2e944b416bca48d9d69a8` | Apache-2.0 |
| gitleaks | 8.30.1 | [official release](https://github.com/gitleaks/gitleaks/releases/tag/v8.30.1) | Platform archives below | MIT |

The admitted gitleaks Darwin ARM64 `.tar.gz` digest is
`b40ab0ae55c505963e365f271a8d3846efbc170aa17f2607f13df610a9aeb6a5`.
The Windows x64 `.zip` digest is
`d29144deff3a68aa93ced33dddf84b7fdc26070add4aa0f4513094c8332afc4e`.
Verify the downloaded archive before extracting or executing it. Keep its
verified archive and installation evidence. A locally compiled Cargo tool's
executable hash may be recorded for that host, but is not the cross-platform
source identity or proof of an equivalent Windows executable.

Ordinary platform prerequisites are the existing pinned Rust 1.96.1 toolchain,
Git, native Bash/macOS utilities or PowerShell 7/Windows utilities, and tar.
Windows also requires the native Storage `Get-Volume` command for the NTFS
check. No Python, jq, Node, package manager, or extra semantic-checker package
is installed or required by the entry points.

## Explicitly online bootstrap

This is a separate owner-authorized preparation operation, outside runtime
testing and outside the verification scripts. It may download public packages
and advisory/license data; it must not consume project secrets, configure a
GitHub App, create a workflow, publish a branch, or change production code.
Use a fresh native checkout with complete Git history and byte-preserving
checkout settings (`core.autocrlf=false`); Windows CRLF conversion is rejected
by the exact source-byte gate.

With the offline environment removed **only in the bootstrap process**, prepare
the existing Rust target and locked workspace cache:

```text
rustup target add wasm32-wasip1 --toolchain 1.96.1
cargo +1.96.1 fetch --locked
cargo +1.96.1 install cargo-deny --version =0.20.2 --locked
cargo +1.96.1 install cargo-audit --version =0.22.2 --locked
cargo +1.96.1 install cargo-cyclonedx --version =0.5.9 --locked
```

Before installation, match the three source archives against the admitted
checksums above. Use the exact gitleaks platform archive rather than a mutable
installer or an unpinned package-manager formula. Record the commands, resolved
origins, verified digests, exit results, UTC time, and local installation paths.
Do not overwrite the workspace lock or repeat an unlocked dependency resolution.
The one Task 10 offline normalization is already complete: it adds only
`serde_json` and `toml 1.1.4+spec-1.1.0` to the existing `heleos-verification`
lock dependency list, using the existing workspace pins. No package stanza,
registry identity, checksum, production dependency, or resolved feature was
added or changed. This bounded verifier preparation is not a bootstrap step
to repeat on each host; every subsequent workspace resolution is frozen.

Refresh advisory data with the pinned `cargo deny fetch all` during bootstrap.
Prepare cargo-audit's separate `advisory-db` checkout from the same official
RustSec repository and pin it to the identical commit. Defaults are
`$CARGO_HOME/advisory-db` and `$CARGO_HOME/advisory-dbs`; when `CARGO_HOME` is
unset, each script derives the ordinary per-user `.cargo` directory. The deny
root may contain its normal `db.lock`, plus exactly one advisory checkout.
Copy caches to Windows with their original file bytes; do not follow symlinks
or substitute a different database.

The recorded snapshot is commit
`bf25f6575a93a35f30796c65c0ed91bee7fa19fd`, tree
`ed200a89710d16363c706a5af55081dbf2f875e4`, captured
`2026-09-08T20:02:54Z`, with 1,242 advisories. Its canonical
`git ls-tree -r --full-tree HEAD` output, including LF line endings and final
newline, has SHA-256
`956fc04305345d1141596c6e92ee668ba35f78d0557a9ac4bf2d4fe694eb951f`.
This is a snapshot of knowledge at that time, not a statement about later
advisories. Both consumers must match the same clean checkout, commit, tree,
raw tracked file bytes, digest, and capture time in their governance records.
The scripts reject future snapshots and captures older than seven days;
cargo-deny also rejects a stale database. A refresh requires a new recorded
snapshot, never `--stale` or an ignored advisory.

License data consists of the exact cached package manifests/license files and
cargo-deny's pinned bundled license-identification data. Missing cached source,
unknown rights, or unrecognized licenses must fail. Do not fetch license data
in a runtime test. Rollback means stopping admission, retaining reports and
prior snapshots/artifacts, and selecting an explicitly admitted replacement;
it never means weakening the policy or deleting a finding.

## Offline verification and evidence

The scripts set `CARGO_NET_OFFLINE=true`, `RUSTUP_AUTO_INSTALL=0`, and
`RUSTUP_TOOLCHAIN=1.96.1` before invoking Cargo. They reject native-target,
target-directory, compiler-wrapper/flag, and secret-scanner overrides. They
freeze production and existing test-host bytes, guest trust data, and Task 9
scripts against the baseline above. The bounded exceptions are the verifier
manifest/source, the exact two verifier lock edges, and one `publish = false`
line in each of the `heleos-cli`, `heleos-core`, and `heleos-platform-fs`
manifests. Together with the already-unpublished `heleos-verification`, these
are the four source packages with explicit `publish = false`. The real
`heleos-pdf-guest`, `heleos-pdf-protocol`, and `heleos-test-fixtures` manifests
remain byte-identical to the baseline because Task 9 provenance freezes the
PDF source closure. No production dependency, feature, core unsafe/identity
FFI, helper authority, or Store API/test-host change is admitted. The scripts
recheck the changed verifier inputs against their starting identities, require
the admitted semantic-verifier source hash, and compare the lock and three
source packaging edits to their exact permitted baseline transformations.
Existing governance is append-only.

The source/dependency checks explicitly retain the existing two
`cfg(windows)` cap-primitives direct edges in `heleos-platform-fs` and
`heleos-cli`, their empty direct feature requests, and zero core edge/use.
They do not introduce another edge or widen the helper's five-symbol authority.
Source equality preserves the existing rustix and Store admissions; it does
not replace their semantic evidence or the native Windows acceptance matrix.

Only the verifier package is built before the shared Task 9
`verify-provenance build-pdf-guest` launcher. That launcher owns the two isolated
workspace/cache/target builds, tracked-manifest comparison, remapping policy,
guest hash, and physical-build-prefix rejection. The Windows entry point
requires its successful two-build report and retains that report. No CLI or
workspace build precedes this launcher. The same provenance verifier runs again
after the security checks.

Cargo commands use `--frozen`; cargo-deny 0.20.2 uses its global `--offline
--frozen` options. This version's global offline flag controls advisory
fetching; do not substitute the obsolete `--disable-fetch` flag. cargo-audit
uses `--no-fetch`, the explicit verified database path, and denies warnings.
[deny.toml](../../deny.toml) covers all resolved targets and features without
pruning development or unpublished packages from the graph. License checks
include development and build dependencies, and duplicate checks include
development dependencies. Yanked packages, unsound/unmaintained advisories,
unknown registries/Git sources, and external licenses outside the allowlist
fail; no advisory is ignored.

The policy records exact-version duplicate debt in `bans.skip`, with a reason
for each pinned upstream dependency family, Heleos engineering ownership, and
expiry before Foundation 0.2. One version in each family remains unskipped;
there are no crate-name wildcard or subtree skips. The source/lock freeze
rejects new versions or groups, and skipped duplicate versions remain subject
to advisory, source, and license checks. Converging or re-admitting this debt
requires an explicit policy change before the next Foundation scope.

The sole private-license treatment is `licenses.private.ignore = true`.
cargo-deny scopes that setting by publishability. To classify all seven local
members without changing the frozen PDF closure, the scripts create a bounded,
disposable audit workspace from exact copies of the governed source and
`Cargo.lock`. Only the copied manifests for `heleos-pdf-guest`,
`heleos-pdf-protocol`, and `heleos-test-fixtures` receive one `publish = false`
line immediately after `[package]`. All other copied source bytes must remain
exact, and the audit lock must remain byte-identical to the candidate lock
before and after cargo-deny. The transformation admits no dependency, feature,
source, checksum, or package-graph delta and performs no unlocked resolution.

This copy supplies only cargo-deny's private-package and local-path
classification. It grants no product authority and is not an input to PDF
guest builds, runtime tests, provenance, or the SBOM; those consume the actual
candidate source. The audit graph's only `publish = false` packages are the
seven local Heleos members, covering their `LicenseRef-Proprietary`
declarations while retaining all seven in the full dependency graph. No
external source or private registry is exempted. Explicit license exceptions
and private-registry/source exemptions remain empty.

Existing local path dependencies have no redundant version requirement;
`allow-wildcard-paths` admits only that path form. The scripts independently
freeze their exact manifest, lock, and source bytes. Registry/Git wildcard
requirements remain denied, and adding or redirecting a local path fails the
source freeze.

Gitleaks produces fully redacted JSON reports for all available Git refs/history
(`git --log-opts=--all`) and for a separate current-source snapshot (`dir`).
Both use `--redact=100` and `--ignore-gitleaks-allow`. The snapshot contains
the current bytes of `git ls-files --cached --others --exclude-standard`:
tracked and nonignored untracked source files, with ignored target/cache
copies excluded. Each input must be a direct regular file with direct parent
paths, and its copied bytes must match. Reports, redacted logs, and this
snapshot are retained in an OS-temp `heleos-supply-chain.*` directory, outside
the repository source inventory. Shallow history, replacement refs, grafts,
alternate local scanner configuration, and ignore files are rejected. A
full-history scan cannot attest unreachable commits or remote refs that were
never fetched.

`--exit-code 0` allows report collection; scanner execution failures still
stop the scripts, and `verify-provenance verify-secret-scan` must adjudicate
both reports before the gate can pass. The
[semantic baseline](../../governance/secret-scan-baseline.toml) is not passed
to Gitleaks as an ignore or suppression input. It admits exactly 22 historical
tuples of fingerprint, commit, path, rule, start line, and end line: 20
`cwd_key` receipt findings, one invalid-input Idempotency-Key fixture, and one
literal verifier marker finding. Every historical allowance must be used
exactly once; missing, duplicate, or additional findings fail.

The sole current-source allowance permits at most one `private-key` finding
within the exact four literal marker lines of the verifier's unique ten-line
`contains_secret` context. It requires exact path/rule, an empty commit,
recomputed marker/context hashes, and every reported line within that literal
array. It does not reuse a historical line number or allow a receipt or API
fixture in the current tree. The baseline rejects malformed, unredacted, or
unmatched reports and expires before Foundation 0.2; scanner, rule, scope, or
source-context drift requires owner re-adjudication. Adjudication diagnostics
contain approved metadata and counts, never raw matched or secret values.

Both platforms run the existing Task 7 Store, backup, platform helper, private
CLI binary, public CLI integration, and full workspace/all-target/all-feature
suites. macOS also executes the verifier suite inside an OS network-denial
sandbox. Windows must execute natively on NTFS; cross-compilation is never
substituted for runtime or native filesystem evidence. The remaining Windows
no-egress and detailed source/native attestation gaps below are explicit.

## Deterministic SBOM contract

The final artifact is
`artifacts/sbom/heleos-foundation-0.1.cdx.json`. Its independently validated
SHA-256 must be recorded as `generated_sbom_sha256` in the exact
`cargo-cyclonedx` tool record. The scripts reject a missing or malformed field,
missing artifact, symlink, or hash mismatch. The field is an artifact identity,
not an acceptance assertion.

The Rust authority is the existing
[verify-provenance binary](../../tests/verification/src/bin/verify-provenance.rs),
with `build-sbom [output]` and `verify-sbom [artifact]` modes. Both modes run
generation in two distinct workspace/cache/target roots beneath a new OS-temp
`heleos-sbom-*` directory outside the repository. They copy exact candidate
source inputs and only checksum-verified registry archives, reconstruct
isolated sources, check manifests against those archives, and invoke pinned
cargo-cyclonedx offline. `SOURCE_DATE_EPOCH=1788897049` is fixed to the baseline
commit above; the report also binds the exact candidate source and lock hashes.
Generation uses JSON/CycloneDX 1.5, all features, all targets, all transitive
dependencies, and strict license parsing. The source, lock, cache inputs, and
tool executables must remain unchanged, and both roots must produce identical
canonical bytes and graph statistics.

Raw cargo-cyclonedx 0.5.9 output is insufficient: it emits one BOM per member,
can omit development-only dependencies, can contain physical paths, and can
return zero despite license warnings. Neither a zero exit code nor a matching
aggregate hash establishes full-graph coverage. Rust validates the raw member
records, fills development-only coverage from the independently checked
metadata/lock graph, and assembles one canonical aggregate. Exact legacy
license declarations remain represented even when the generator warns about
slash syntax; cargo-deny's independent license policy still applies.

The current contract is 405 packages: 398 registry packages and seven local
members, with 1,035 distinct package-to-package edges plus seven workspace
membership edges. The aggregate has 406 dependency nodes including its
synthetic workspace root. It must equal the complete frozen, unfiltered
`cargo metadata --all-features` and lock graph for every package, version,
source, checksum, license, resolved feature, dependency alias/kind/target, and
edge. Separate metadata projections for `aarch64-apple-darwin`,
`x86_64-pc-windows-msvc`, and `wasm32-wasip1` must remain valid subsets. Mutable
sources, physical paths, dangling references, omissions, extras, and graph or
archive drift fail. These counts describe the required candidate graph, not
evidence that a final artifact or gate run has already passed.

`build-sbom` publishes only after both roots agree, source/tool rechecks pass,
and scratch cleanup succeeds. Its destination parent must already exist and
the output must be absent; publication never overwrites an existing artifact.
`verify-sbom` regenerates both roots and requires exact byte equality with the
supplied artifact, including a final reread. Raw member BOMs are disposable
and removed with generation scratch. The scripts retain the regenerated
aggregate under `target/supply-chain.*`; Windows also retains its guest-build
and metadata reports there. They compare the regenerated aggregate with the
admitted artifact and recheck its hash, provenance, policy, source, and advisory
identities before reporting a local pass.

## Local result and remaining release gates

The scripts can finish successfully after their local checks pass. The macOS
marker attests only the native ARM64 offline gate. The Windows marker is
conditional on native x64/NTFS verification and all listed native test commands
passing there; it does not come from cross-compilation. Each script also emits
`DEFERRED_PLATFORM_GATE` and `DEFERRED_RELEASE_GATE` to preserve these separate
requirements:

- Native Windows/NTFS test evidence, the exact cap-primitives Windows
  build-probe and safe optional API/source attestation, and Windows no-egress
  evidence must be captured on the native runner. Source freezing and the
  existing helper-library source/unsafe audit tests do not manufacture that
  platform evidence.
- The same candidate must have macOS and Windows guest hashes, native NTFS
  results, both remap-root classes, and no physical-prefix leakage evidenced.
  Cross-target checks or operator-set booleans cannot replace those results.
- GitHub App owner dispositions, workflow authority, push authorization,
  immutable CI attestation for the exact release-candidate SHA, and the later
  Foundation acceptance dossier remain separate gates. A local pass closes
  none of those requirements by itself.

No workflow is created here. Task 9 currently rejects all workflow paths; any
later owner-authorized Task 10 workflow needs that explicit verifier-policy
amendment. GitHub App dispositions, remote publication, CI attestation, and the
documentation-only acceptance dossier remain later Task 10 gates.

Upstream behavior references: [cargo-deny 0.20.2 configuration](https://github.com/EmbarkStudios/cargo-deny/blob/0.20.2/deny.template.toml),
[cargo-deny offline handling](https://github.com/EmbarkStudios/cargo-deny/blob/0.20.2/src/cargo-deny/check.rs),
[cargo-audit 0.22.2 options](https://github.com/RustSec/rustsec/blob/cargo-audit/v0.22.2/cargo-audit/src/commands/audit.rs),
[cargo-cyclonedx 0.5.9 CLI](https://docs.rs/crate/cargo-cyclonedx/0.5.9/source/README.md),
and [deterministic timestamp support](https://docs.rs/crate/cargo-cyclonedx/0.5.9/source/CHANGELOG.md).
