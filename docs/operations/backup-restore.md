# Backup and Restore

Foundation 0.1 backups are single-file containers that are signed with an Ed25519 key and encrypted to an age X25519 recipient. Restore is always restore-into-new: Heleos materialises a complete Foundation tree at a destination that does not yet exist and never writes into a live store. This guide covers key custody, the four operator commands, the exact restored layout, failure response, the quarterly drill, and the staging and durability limits an operator must be able to recognise.

## Key custody

Four pieces of key material take part. Two are secrets and two are trust anchors. They are separate files with separate lifecycles, and neither backup creation nor verification ever holds both halves of a pair.

| Flag | Material | Secrecy | Format |
| --- | --- | --- | --- |
| `--signing-key` | Ed25519 signing key | **Private** | exactly 32 raw bytes, no trailing newline |
| `--recipient` | age X25519 recipient | Public | `age1…` string on the command line |
| `--identity` | age X25519 identity | **Private** | one `AGE-SECRET-KEY-1…` line |
| `--trusted-signer` | Ed25519 verifying key | Public value, protected file | exactly 32 raw bytes, no trailing newline |

Rules Heleos enforces at the command boundary:

- Every key file is opened **no-follow and nonblocking**, and the type check happens on the returned handle before a single byte is read. A symbolic link, a reparse point, a directory, a FIFO, or a device at the given path is refused. Nonblocking admission is what makes a FIFO a refusal rather than a stall: a pipe with no writer would otherwise park the command inside `open` before its type could be judged.
- Every key file must be a **private, single-linked regular file**: mode `0600` on Unix; on Windows, one link, no reparse point, an owner SID equal to this process's SID, and a protected DACL granting full control to exactly this principal and SYSTEM. This applies to `--trusted-signer` as well. Its bytes are public, but its *integrity* is the entire trust decision, so a file another principal can rewrite is refused.
- Every one of those checks is made **against the handle that is then read**, on both platforms. Heleos never reopens a key path to inspect it, so the permission evidence and the key bytes always describe the same object; a rename or a symlink swap after admission cannot separate them.
- `--signing-key` and `--trusted-signer` must be **exactly 32 bytes followed by end of file**. Thirty-one bytes, thirty-three bytes, and a trailing newline are all refused. Heleos builds exactly one `SigningKey` from the bytes, then overwrites and drops the scratch buffer immediately.
- `--identity` must hold **exactly one age X25519 identity**. An empty file, an SSH key, an SSH-agent stanza, a plugin identity, or two identities in one file are all refused.
- Secret material is never echoed. No diagnostic, log line, or exit message contains key bytes, identity text, or the path a key was read from. Heleos never invokes a shell, so no key value can reach a command line.
- `--recipient` is the only key value passed on the command line, and it is public by construction. Never pass an identity or a signing key as an argument.

Recommended custody split: the signing key lives with the backup producer; the identity lives with the recovery custodian, offline, and is only ever mounted for a verification or a drill; the trusted signer is distributed to every verifier as a read-only, integrity-checked artifact. Rotate by issuing a new pair, re-signing, and retaining the previous trusted signer for as long as containers signed with it are retained.

## Commands

### Create

```
heleos backup create <destination> \
    --database <path> --vault <path> \
    --recipient <age1...> --signing-key <path>
```

`create` takes the database writer lock, makes a consistent SQLite snapshot beside the destination, streams the snapshot and every referenced vault object into a signed manifest and entry table, encrypts the whole container to the recipient, and publishes the ciphertext to `<destination>` **without replacing** anything already there. The destination's parent directory must exist and be owner-private, because the snapshot and the plaintext and ciphertext staging siblings are created inside it.

The success envelope carries `heleos.backup-receipt/v1`: the container summary (manifest schema, signer key digest, entry-table digest and length, entry count, decoded payload length, database digest and length) plus the ciphertext length. It contains no paths.

### Offline copy

Copy the published ciphertext to offline media before doing anything else, then re-verify the copy in place:

```
cp <destination> /Volumes/<offline>/<name>.age
heleos backup verify /Volumes/<offline>/<name>.age \
    --identity <path> --trusted-signer <path>
```

Record the container summary from the offline verification, not from the create receipt. A backup that has only ever been verified on the machine that produced it is not evidence of recoverability.

### Verify

```
heleos backup verify <backup> --identity <path> --trusted-signer <path>
```

Verification opens the ciphertext once, no-follow, as one regular file and moves that handle into the verifier. It decrypts to private staging, checks the container signature against the trusted signer, re-hashes the entry table and every entry, opens the decoded database read-only, runs `integrity_check`, `quick_check`, and `foreign_key_check`, reconciles the decoded vault against the database inventory, and verifies the audit chain end to end. The report is `heleos.backup-verification-report/v1` and adds the checked audit-event count and the audit head hash. Nothing is written to the container or to the live store.

### Restore

```
heleos restore <backup> --into <nonexistent-destination> \
    --identity <path> --trusted-signer <path> --verify
```

`--verify` is the operator's acknowledgement that this command materialises a new Foundation tree. It is a command-line gate only: it is never passed into the core service, and its absence or presence cannot make Heleos skip a check. Core always performs the full verification described above **before** publishing anything, whether or not the operator typed `--verify`.

`--into` must not already exist. Restore builds the whole tree in a private `.heleos-restore-tree-<uuid>.partial` sibling of the destination, fully verifies and syncs it, and only then publishes it to the final name without replacing an existing entry. A destination that already exists is refused and the pre-existing directory is left untouched.

The receipt is `heleos.restore-receipt/v1` and embeds the same verification report.

## Exact restored layout

A successful restore produces exactly this tree and nothing else:

```
<into>/
├── foundation.sqlite3                 # the restored Foundation database
├── foundation.sqlite3.writer.lock     # the writer lock file, no writer held
└── vault/
    ├── .staging/                      # present and empty
    ├── .vault.lock                    # the vault lock file
    └── objects/
        └── sha256/
            └── <aa>/<bb>/<64-hex-digest>   # one file per content object
```

Directories are mode `0700` and files are mode `0600` on Unix; on Windows they carry an owner-and-SYSTEM-only DACL. `vault/.staging` is present and **empty**: a restored tree that contains a `.partial` entry under `.staging` did not come from this restore path and must not be adopted. There is no `-wal` or `-shm` sidecar in a freshly restored tree.

Confirm the restore before adopting it:

```
heleos verify --database <into>/foundation.sqlite3 --vault <into>/vault
heleos verify --database <into>/foundation.sqlite3 --vault <into>/vault \
    --revision <sha256>
```

`verify --revision` re-reads the evidence manifest for that revision and separately re-verifies both the original content object and the manifest object in the restored vault.

## Wrong key, wrong signer, and corruption

Every one of these failures exits **20** and prints exactly one JSON diagnostic object on stderr. The `class` field distinguishes them:

| `class` | Meaning | Response |
| --- | --- | --- |
| `backup_decryption` | The identity does not open this container, or the ciphertext is damaged | Confirm you mounted the right identity. If it is right, treat the container as damaged. |
| `backup_integrity` | The container opened but its signature or an entry hash does not verify | Do **not** restore. Treat as tampering or media damage until proven otherwise. |
| `invalid_backup_container` | The plaintext is not a well-formed Heleos container | Confirm the file is a Heleos backup and not a truncated or partially copied one. |
| `integrity` | The container verified but its decoded contents did not | Do **not** adopt. This is a defect in the source store at backup time. |

In every case:

1. **Do not delete the container and do not re-run `create` over it.** The failing container is incident evidence. Record its SHA-256, byte length, and filesystem metadata.
2. Fall back to the previous retained container and verify that one. Recover from the newest container that verifies cleanly, never from an unverified one.
3. Compare copies to *narrow* the cause, not to settle it. If a second, independently stored copy verifies, copy damage on the first is the leading explanation; if both fail identically, an upstream cause at signing, at encryption, or in the source store is the leading explanation. Neither result is proof: wrong trust material, an I/O fault on the reading host, and a change of environment between the two verifications all produce the same shapes. Record the observation and keep both copies.
4. A `backup_integrity` result on a container that previously verified is a security event until it is shown to be a storage event. Escalate before overwriting anything.
5. Re-verify the trusted signer file itself. A `backup_integrity` result whose real cause is a substituted `--trusted-signer` file looks exactly like tampering with the container.

Other exit codes an operator will see: `0` success, `2` command-line usage error, `21` quarantine, `22` denied (`policy_denied`, `writer_busy`, `idempotency_conflict`, `lease_unavailable`), `23` not found, `70` every other internal failure including `io`, `database`, `resource_limit`, and `commit_outcome_unknown`.

Usage diagnostics (exit `2`) carry a fixed sentence drawn from a closed vocabulary of parser failure kinds. They deliberately do **not** quote the operand that was rejected, because an operator who types an identity where a public recipient belongs must not have it echoed into stderr and from there into logs. A repeated option is named only when it is one of the CLI's own declared flags. Run `--help` to see which option a rejected command line was missing or duplicating.

### Quarantined intake

`ingest` and `jobs resume` exit **21** with `class: "quarantine"` when the intake was quarantined — as a corrupt, encrypted, unsupported, or suspicious PDF, or by an intake limit. This is not a lost result. Core has already committed a durable quarantine receipt and the audit events that record it, and `heleos verify` will still report `audit_chain.valid`. Exit 21 says only that the content was refused, so no document, revision, or evidence manifest was created.

The same exit applies to a **replay** of the same idempotency key and to a **`jobs resume`** of a job that resolved to quarantine: core returns its retained quarantine record, and a retained quarantine is still a quarantine for the operator. Do not read a later exit 0 for the same key as a change of outcome; there will not be one. Respond by investigating the source document, not the store.

## Quarterly restore drill

Run every quarter. A backup that has never been restored is not a backup.

1. Pick the newest retained container from offline media, not from the producing host.
2. On a host that is not the production writer, verify it: `heleos backup verify <backup> --identity <path> --trusted-signer <path>`.
3. Choose a **new, not-yet-created** destination name beneath an existing directory you have already admitted as private and own, and restore into it: `heleos restore <backup> --into <drill-path> --identity <path> --trusted-signer <path> --verify`. Do not pre-create `<drill-path>`; restore refuses a destination that already exists.
4. Confirm the restored tree matches the layout above, byte for byte in structure and permissions.
5. Run `heleos verify --database <drill-path>/foundation.sqlite3 --vault <drill-path>/vault` and confirm `result.database.clean` and `result.audit_chain.valid` are both `true`.
6. Pick at least one known revision and run `heleos verify … --revision <sha256>`. Confirm `result.revision.evidence.manifest.revision_id` is the revision you asked for and that `result.revision.original.kind` and `result.revision.manifest.kind` are both `verified`.
7. Close the drill: unmount the identity volume and end the identity's session. **Do not destroy the recovery keys**, and do not delete the drill tree as part of this step. Retiring a drill tree is a separate, separately authorised action — see the enumerated-cleanup rules under *Crash-left staging* — carried out after the drill evidence below has been recorded and stored.

**Evidence to retain for each drill**, as one immutable record. The field paths below are the actual JSON envelope fields, so the record can be assembled mechanically:

- the drill date, the operator, and the host;
- the container's SHA-256 and byte length;
- the full `result` object of the `heleos.backup-verification-report/v1` envelope, including `result.container.signer_key_sha256`, `result.container.entry_table_sha256`, `result.container.entry_count`, `result.container.database_sha256`, `result.checked_audit_event_count`, and `result.audit_head_hash`;
- the `heleos.restore-receipt/v1` `result` object, whose `result.verification` embeds the same verification report;
- the restored-tree listing and permissions;
- the `heleos.cli-verify/v1` `result` object for the whole-store check (`result.database`, `result.audit_chain`) and for each spot-checked revision (`result.revision`);
- the exit code of every command.

A drill that produced no verification report is not evidence that recovery works. A drill whose report differs from the create receipt's container summary is a finding, not a pass.

## Crash-left staging

Heleos stages every mutation in a private, unpredictably named sibling and publishes without replacing. If the process is killed, the machine loses power, or the filesystem fills mid-operation, that staging entry can survive. It is **inert**: it is never read back, never adopted, and never published by a later run.

### Names are discovery hints, never authority to delete

The patterns below tell you **where to look**. They are not proof of ownership, provenance, inactivity, or name binding, and a matching name is never on its own a reason to remove anything.

| Location | Name pattern | Kind |
| --- | --- | --- |
| the backup destination's parent | `.heleos-backup-plaintext-<uuid>.partial` | file |
| the backup destination's parent | `.heleos-backup-ciphertext-<uuid>.partial` | file |
| the restore destination's parent | `.heleos-restore-tree-<uuid>.partial` | directory |
| the shared default temporary parent | `heleos-read-snapshot-<random>` | directory |
| the shared default temporary parent | `.heleos-restore-tree-<uuid>.partial` | directory |
| the shared default temporary parent | `heleos-pdf-<uuid>` | directory |
| `<vault>/.staging/` | `<uuid>.partial` | file |

Heleos creates these owner-private (`0700` for directories, `0600` for files). The converse does **not** hold: an entry here with an unexpected owner or mode does not prove it did not originate in Heleos. Hardening runs immediately after creation, but an entry left behind by a crash *during* that window, or one whose permissions were changed afterwards by something else, will not match. Treat an unexpected owner or mode as an unresolved finding to investigate, not as a foreign artifact to sweep away.

### Enumerated, owner-authorised cleanup

There is **no Heleos cleanup tool for crash-left staging**. Nothing in this release enumerates, adjudicates, or removes leftovers for you, and the guarantees described below apply only while a *live* Heleos process still holds the retained parent and child capabilities for that entry — which, by definition, a crash-left leftover no longer has. Manual cleanup is therefore a reviewed, human-authorised operation, and it is deliberately narrow:

1. **Prove the store is idle.** Confirm no Heleos process is running against the affected database or vault, that the writer lock and the vault lock are free, and that no ingest job is in a resumable state (`heleos inspect foundation --project <uuid> --database <path> --vault <path>`).
2. **Enumerate explicitly.** Write down every candidate entry by full name, one at a time. Do not use a wildcard, a glob, a recursive delete, or a `find -delete`. This procedure never authorises removing "everything matching a pattern".
3. **Record identity and provenance for each candidate**: owner, mode or DACL, inode or file index, device or volume, size, and timestamps; and the incident, host, and time window that is supposed to explain it. An entry you cannot tie to a known interrupted run is not a candidate.
4. **Preserve anything unknown, rebound, or uncertain.** If the owner, mode, or identity is not what the record says it should be; if the name now refers to a different object than the one you recorded; or if the artifact could belong to a `commit_outcome_unknown` outcome — stop and preserve it. Escalate to a human review instead of removing it. Preservation is always the safe answer here; deletion is not reversible.
5. **Get the removal separately authorised**, by someone other than the person who enumerated it, and remove only the enumerated entries. Never remove `<vault>/.staging` itself, `.vault.lock`, or `foundation.sqlite3.writer.lock`.
6. **Re-run `heleos verify --database <path> --vault <path>`.** Crash-left staging cannot corrupt a published store, so this must pass; if it does not, the incident is larger than leftover staging.

An ordinary shell removal cannot reproduce the parent-relative cleanup Heleos performs internally, and this guide does not pretend otherwise. `cd`-ing into a directory and running `rm -r <name>` re-resolves the whole path from the root, and it validates nothing about what the name currently denotes; Heleos removes exactly one component relative to a parent handle it has held and re-checked since before the child existed. That is a stronger position, not an absolute one — see *What the final checkpoint guarantees, and what follows it* below. Until a reviewed capability-relative cleanup tool exists, the correct answer for anything beyond a small, fully enumerated, provenance-matched set is human-assisted escalation.

### What Heleos itself guarantees

Scoped separately, because these are two different mechanisms:

- **Core services** (backup creation, verification, restore) remove their own staging relative to a retained parent directory handle whose identity and policy they have re-checked, rather than by re-walking the path from the root.
- **The CLI's own PDF sandbox child** follows the same discipline. The shared temporary parent is admitted through one retained directory handle before anything is created; that handle creates the child, opens it no-follow, and hardens it through the child's own handle; and the checked cleanup at the end of the command re-verifies the retained parent's policy marker, the child's identity, owner, and privacy, and that the name still binds to that same child.
- **The parent's captured name is bound to that same handle.** The temporary selector is read and canonicalized exactly once, and the process identity is captured once; neither is read again, and no alternate fallback parent is ever opened. The captured canonical path is held together with the retained handle's strong identity and its policy marker, and at each checkpoint — immediately after admission, before the staging path is handed to the PDF sandbox, and before removal — Heleos re-reads the retained handle (directory, not a link or reparse point, same strong identity, same exact owner/mode or owner/DACL marker) and then requires the captured name to still denote that same object. This matters because the sandbox is given a *path* and opens the staging tree from it: the checkpoint is what ties that path back to the parent this run admitted. A name that no longer denotes it is refused; the replacement is never adopted.

**What the final checkpoint guarantees, and what follows it.** That last re-verification is a *checkpoint*, not an atomic validated deletion. Every mismatch the checkpoint detects — a changed parent, a rebound name, an identity that is no longer the child this run created, a child that is no longer private — **preserves** the entry and fails the command. Only after the checkpoint passes does Heleos run the removal itself, which is the pinned `cap-std` recursive removal of exactly one name component relative to the still-retained parent handle. Those are two operations. On Windows that pinned primitive internally resolves its own handle and path before deleting, and Foundation 0.1 explicitly does **not** continuously defend against a deliberate same-principal replacement in the interval between the checkpoint and the removal, and does **not** claim it can never delete a decoy substituted inside that interval. Absence is verified relative to the same retained parent afterwards, and a name that reappears is reported as a failure rather than retried. Read an unexpected ownership or mode on a staging entry as a finding to investigate, not as something Heleos would have prevented.

A refusal before the staging path is handed to the sandbox permanently preserves the child. Even if a later checkpoint would pass, command completion and the destructor do not reconsider removal or hand out the path again; command completion reports the original refusal. An uncertain commit retains its existing higher priority and its own error class. These are decisions at observed checkpoints, not continuous protection of the interval after a valid checkpoint.

A cleanup failure always reaches the operator: a command whose work succeeded but whose child could not be removed does not report success. Neither a refusal nor a deliberate preservation is ever retried by the destructor.

**Construction failures.** The window between creating the staging name and proving what it is has its own rule. The handle and the strong identity of the created child are retained as soon as each is available, and every failure in that window settles through one explicit checked decision: preserve, or remove after re-verifying the parent, the retained identity, the private permissions once hardening has run, and that the entry is still the empty directory this run created. Removal always requires an identity established from this run's own retained authority — the identity recorded during construction, or, if the failure came first, the identity read from the child handle this run still holds. If a failure arrives before even that handle exists, the created name is **preserved**: an identity read back from the name, or inferred from the directory being empty, would prove nothing about what created it, so Heleos reports cleanup uncertainty (`policy_denied`) above the original error instead of deleting. A name whose binding was *refused* is likewise never deleted by the failure handling that refused it — that is precisely the case the check exists for. That single decision is final: the destructor never re-decides it.

What is *not* claimed: that every failure removes staging. An abrupt termination, a power loss, or a construction failure that settled on preservation can leave a child behind, and an uncertain commit deliberately leaves one behind (see *Preservation under `commit_outcome_unknown`*).

## Checked close before reports and publication

**Scope: backup verification and restore only.** When `BackupService` materialises and checks a Foundation tree, it performs a **checked close** before it will report success or publish anything: the SQLite connection is closed and the close result is captured; only then is the capability-relative scratch cleanup run; and the source handles and the reader lock are held alive until that cleanup has finished. A verification report is emitted, and a restored tree is published, only after both the close and the cleanup have succeeded.

This is why `heleos backup verify` and `heleos restore` can fail *after* every content check passed: the content was correct but the store could not be released cleanly, and Heleos will not certify a store it could not close.

**`heleos verify` and `heleos inspect foundation` do not make this claim.** They open the live store through the public read-only reader and release it when the reader is dropped, whose cleanup is best-effort. Their reports certify what they read; they do not certify that the read snapshot was released cleanly. When you need the checked-close guarantee, it is the backup verification and restore reports that carry it.

## Cleanup precedence

When an operation fails *and* its cleanup also fails, the classification rules are fixed, and they are applied in this order:

1. **`commit_outcome_unknown` (exit 70) is preserved first.** If publication already happened and anything afterwards failed, that outcome outranks every other classification, keeps its own class, and its staging tree is deliberately preserved rather than cleaned. Heleos will not claim the operation succeeded and will not claim it failed.
2. **Otherwise a cleanup failure overrides the result, whether that result was a success or a primary error.** If the operation failed for one reason and the cleanup failed for another, the cleanup error is what you see and the primary cause does not reach stderr. A cleanup failure means Heleos may have left state behind, and that is the more urgent fact.
3. **The original result — success or failure — is returned only after cleanup is certain.**

The same order applies to a failure during the construction of a staging guard: its cleanup uncertainty is reported above the error that triggered it.

Read a cleanup-class error (`io`, `policy_denied`) on a command you expected to fail for a different reason as "and staging may still be present". Follow the crash-left staging procedure before retrying.

## Preservation under `commit_outcome_unknown`

`commit_outcome_unknown` (exit 70, `class: "commit_outcome_unknown"`) means Heleos published a result but could not confirm the state afterwards. **It does not mean the operation failed.**

Preserve, do not repair:

1. Do not delete, rename, truncate, move, or overwrite the destination. Do not re-run the same command over the same destination.
2. Do not remove any `.partial` sibling near the destination yet. Under this outcome the staging entries are part of the evidence.
3. Record the destination's SHA-256, byte length, and full filesystem metadata, and do the same for the source database and its `-wal` and `-shm` sidecars as one set.
4. Determine the outcome by reading, never by writing. For a backup, run `heleos backup verify` on the destination as it stands. If it verifies, the container is good and the uncertainty was in the post-publication confirmation only; record that and retain the container. If it does not verify, retain it as evidence and produce a new backup at a **different** destination path.
5. Never resolve the uncertainty by overwriting the uncertain artifact.

## Durability and ownership limits

Two claims Heleos deliberately does **not** make. Both matter when reading an incident.

**Heleos does not claim continuous same-principal protection of a staging entry from checkpoint to removal.** It verifies the retained parent's identity and policy at each step, and immediately before removal it re-validates the exact one-component child and its expected relative binding, identity, and private policy. Every mismatch detected *at that checkpoint* is preserved. The removal that follows is a separate operation against the same retained parent, and Foundation 0.1 makes no continuous-protection claim and no never-delete-a-decoy claim for a deliberate same-principal replacement performed inside that interval. No such proof is available from the filesystem with the capabilities this milestone admits. Treat an unexpected ownership or mode on a staging entry as a finding, not as something Heleos would have prevented.

**Heleos does not claim namespace durability across power loss on Windows.** Windows exposes no documented guarantee that a directory-entry creation or rename survives a power failure, and flushing a directory handle is not such a guarantee — Heleos tolerates the errors Windows returns for that operation rather than treating them as durability. File *contents* are synced before publication on every platform. What is not guaranteed on Windows is that the *name* of a published file or directory survives a power loss that occurs immediately after publication. After an unclean Windows shutdown, re-list the destination and re-verify any container or restored tree published near the failure; do not assume its presence or absence is authoritative.

On Unix, Heleos additionally **attempts** to synchronise the containing directory after publication, which is the strongest step available to it. That is a durability measure, not a universal guarantee: whether a synced directory entry actually survives depends on the filesystem, the volume's mount options, the storage stack, and whether the device honours cache-flush requests. The explicit Windows limitation above is not weakened by it, and after any unclean shutdown on any platform the correct response is the same — re-list the destination and re-verify what was published near the failure.

## The shared default temporary parent

Read-only snapshots, the decoded `.heleos-restore-tree` staging used during backup verification, and the PDF sandbox all need scratch space, and all default to the platform's shared temporary directory (`TMPDIR` on Unix, `%TEMP%` on Windows). Heleos treats that shared parent as **untrusted-but-admissible** and accepts it only under an exact policy, applied to a directory handle it opens and retains *before* it creates anything inside:

- **Unix:** the shared parent must be a directory whose owner UID, read from the retained handle, is either this process's effective UID or `0`, **and** whose mode either denies group and other writes outright (`mode & 0o022 == 0`) or carries the sticky bit (`mode & 0o1000 != 0`). This admits the two safe real-world shapes — a per-user `0700` temporary directory, and a system `1777` `/tmp` where the sticky bit prevents another principal from renaming our child away — and rejects everything else, including a group-writable directory without the sticky bit. The effective UID is read from the process itself, not inferred from the metadata of any file.
- **Windows:** the shared parent must be a directory, not a reparse point, and it must pass **both** halves of the policy — owner **and** DACL. Neither half admits a parent on its own.
  - **Owner.** The owner SID read from the retained handle must be this process's SID, `SYSTEM` (`S-1-5-18`), or the local administrators group (`S-1-5-32-544`) that owns the machine-wide temporary root. The SID is compared only as a canonical string that survives an exact round trip; an account name is never used to infer it.
  - **DACL.** The parent must have a **present, non-null** DACL with exact `ACL_REVISION` (revision 2). Its access-control entries must contain no *dangerous allow grant* to an untrusted SID, judged in two independent ways. An entry that is **effective on the parent itself** (its inherit-only flag clear) is refused if it grants any of `FILE_DELETE_CHILD`, `DELETE`, `WRITE_DAC`, `WRITE_OWNER`, or `GENERIC_ALL` — the rights that would let another principal rename our child away or re-permission the parent. An entry that is **inheritable** into the child we are about to create (its container- or object-inherit flag set, with or without inherit-only or no-propagate) is refused if it grants any of those plus `GENERIC_WRITE`, the expanded file/directory add and write rights, or the extended-attribute writes. This pre-creation check is mandatory: hardening the child *afterwards* cannot revoke a handle an inherited grant already let another principal open. The trusted set is this process, `SYSTEM`, and `BUILTIN\Administrators`; `CREATOR OWNER` (`S-1-3-0`) is trusted only as a pure inherit-only template entry, and `CREATOR GROUP` (`S-1-3-1`) is never trusted. Deny entries are inert — they grant nothing — and are recorded but never enter the dangerous-grant test.

  This is **not** an owner-only rule, and it is **not** the exact private-DACL rule Heleos applies to its own children. A shared temporary directory legitimately grants access to principals Heleos does not control, and Windows has no equivalent of the sticky bit. The narrow protection for our own data comes from the child, which is created inside the retained parent and given a protected DACL granting full control to exactly this principal and SYSTEM.

  **Compatibility boundary.** Foundation 0.1 reads the parent's descriptor once per policy snapshot and requires its DACL to stringify into a bounded, flat allow/deny grammar before it will touch the access-control list directly. A parent whose DACL is a callback, object, object-callback, audit, unknown or future, malformed, revision-DS, or otherwise unparseable form is **unsupported**: it is refused with `policy_denied` *before* child creation rather than being interpreted optimistically. Standard namespace-safe revision-2 shared defaults — including a normal `%LOCALAPPDATA%\Temp` — are valid. Every recheck repeats the whole sequence against a fresh descriptor and requires the exact same owner, DACL text, revision, and ordered entry evidence.

**Heleos never chmods, chowns, or rewrites the shared parent.** It never edits a rejected parent's owner, DACL, ACL revision, or ACEs. It has no permission to change a directory it does not own, and silently widening or narrowing a shared temporary directory would be a hazard to every other process using it. Heleos creates one unpredictably named child inside the retained parent, makes **that child only** private through the child's own handle, and removes that child under the checked cleanup described above.

If the admission policy rejects your shared parent, do not relax — or tighten — the parent itself. Remediate through normal process configuration by selecting a different, already-safe temporary parent before start.

On Unix, point `TMPDIR` at a directory you own with mode `0700` and re-run:

```
mkdir -m 700 -p /var/lib/heleos/tmp
TMPDIR=/var/lib/heleos/tmp heleos verify --database <path> --vault <path>
```

On Windows, create a directory you own, give it a protected DACL granting full control to your account and SYSTEM only, and point `%TEMP%` and `%TMP%` at it for the Heleos process before starting it. Do not attempt to repair the machine-wide temporary root: it is shared with every other process on the host, and Heleos will not modify it for you.

Use a dedicated, owner-private temporary parent on any multi-tenant host. It removes the shared-parent question entirely and it makes crash-left staging trivial to find.
