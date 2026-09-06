# Task 7 SQLite/Store snapshot-authority review

## Status and scope

- Review base: `70484577209ea563d43a4538c35ede8940e9ec35` in `/Users/bekim/Heleos-spark/.worktrees/foundation-0.1`.
- Reviewed the Task 7 brief/preflight, the Task 6 Store/repository/Vault interfaces and inventory tests, the Foundation schema/lock threat boundary, and the locally cached `rusqlite 0.40.2` backup implementation plus its bundled SQLite C API documentation/source.
- No tracked source, index, dependency, or repository state was changed. No network/fetch or broad test was run. This ignored review report is the only filesystem write.

## Exact ruling

**Reject the unqualified claim that `&mut Store` + the application writer lock + an immediately adjacent autocommit `vault_inventory_rows()` and online backup are provably snapshot-equivalent.** They are equivalent only under the explicit Foundation 0.1 premise that every database mutation is made by a cooperating Heleos process that honors the retained application lock. That is a valid scoped product assumption, but it is not a SQLite snapshot guarantee.

**Amend Task 7 to require one explicit `DEFERRED` read transaction on the live writer connection spanning the complete `vault_inventory_rows()` call and the complete same-source-connection SQLite online backup.** The inventory's first `SELECT` establishes the source snapshot. The backup handle must be destroyed and the read transaction explicitly ended before any Vault open/hash, age operation, or other service call. The application writer lock remains held for the entire `BackupService::create` call.

With that amendment, the owned inventory and staged SQLite database are one SQLite snapshot even if a separate, noncooperative SQLite connection commits WAL changes after the first inventory read. A hostile same-principal process that edits/replaces database files outside SQLite remains outside Foundation 0.1's declared security boundary and is not solved by this amendment.

This is a **blocking Task 7 wording/invariant correction, not a path or dependency blocker**. The existing fifteen Task 7 paths suffice: top-level `Store::connection` is crate-visible, `Store::require_writer_capability` and the identity rechecks are crate-visible, and `Store::vault_inventory_rows` is crate-private. The transaction and online-backup orchestration can live in `crates/heleos-core/src/backup/mod.rs`. Do not add duplicate inventory SQL.

## Why adjacency alone is not a proof

1. `&mut Store` excludes safe-Rust reentry through that Store value, and the retained application lock excludes another cooperating `Store::open_writer`. It does not exclude a separate raw `rusqlite`/SQLite connection or another process that ignores `.writer.lock`; the lock is advisory at the application layer.
2. The plan expressly places direct out-of-band database writes and a hostile same-principal process outside Foundation 0.1's security boundary. Therefore the preflight's equivalence statement is conditional on the threat model, not unconditional.
3. `vault_inventory_rows()` is a multi-statement autocommit query: it reads the live-job count, the union count, content rows, nonterminal job rows, and per-job latest audit anchors. Its count/fetch agreement detects many drifts but does not make those statements one snapshot. Same-count replacement, job-state transition, or an update between component queries can produce a mixed view or an integrity error.
4. `rusqlite::backup::Backup::new` only calls `sqlite3_backup_init`; it does not establish the source snapshot. SQLite reads on `sqlite3_backup_step`.
5. Without an already-open source read transaction, each `sqlite3_backup_step` obtains a source read lock only for that step. SQLite documents that a different connection/process may modify the source between steps and that the next step restarts the backup. Thus an inventory captured before backup can describe state S0 while a restarted backup completes at S1.
6. WAL makes that race practical: a raw SQLite writer can commit while a reader/backup exists. The Heleos writer Store is configured for WAL. The application lock prevents this only for cooperative Heleos writers.

## Approach comparison

| Approach | Snapshot result | Ruling |
|---|---|---|
| Inventory before backup, no SQLite transaction | Exact only under the cooperative-writer premise. An external commit after inventory can be included by the backup, especially after a multi-step restart. | Insufficient as an unqualified proof. |
| Backup before inventory, no SQLite transaction | Symmetric race. A commit after backup completion but before inventory can make the inventory newer than the staged DB. | Insufficient. |
| Inventory before and after, compare results | Detects some membership drift, but not writes outside the inventory, same-count/ABA changes, or a backup endpoint between the two observations. | Diagnostic only; not authority. |
| Explicit source `DEFERRED` read transaction; inventory first; backup second | The first inventory read pins one SQLite snapshot and the backup reads through the same source connection while that transaction remains active. WAL commits by other connections are not visible to that read transaction. | **Required minimal amendment.** |
| `IMMEDIATE`/Store write transaction around inventory and backup | Wrong primitive. It makes the source connection a writer; SQLite backup stepping reports busy/locked for a source write transaction. It also confuses the Task 6 audited transaction boundary. | Forbidden. |
| Query exact inventory from the completed staged DB | Strongest by-construction alternative and independent of source ordering. | Acceptable only with a reviewed reusable inventory-on-connection/staged-Store hook. Do not duplicate SQL. It expands beyond the current fifteen paths and is unnecessary if the deferred transaction is adopted. |

The transaction-bound approach also stabilizes the component statements inside `vault_inventory_rows()`, not merely the gap between that method and the first backup step.

## Required ordering and invariants

1. `BackupService::create` takes `&mut Store`; call `require_writer_capability()` and recheck writer-lock/database identity before staging. Reject read-only and in-memory Stores.
2. Create a fresh, owner-only, capped destination database connection distinct from the source connection. No API may touch the destination while the backup handle exists.
3. Start an explicit `rusqlite::Transaction::new_unchecked(&store.connection, TransactionBehavior::Deferred)`. Use explicit `Deferred`; do not call `Store::with_immediate_transaction` and do not depend on a mutable connection default.
4. Call the exact existing `store.vault_inventory_rows()` as the first database read. This pins the transaction snapshot and returns the sole inventory authority. No callback or service call may execute between inventory and backup initialization.
5. Construct `rusqlite::backup::Backup` from that same transaction/source connection and the distinct staging destination. Step to `Done` while the read transaction remains alive. Incremental stepping is snapshot-safe only because of the outer read transaction. Bound `Busy`/`Locked` retry/time behavior; `run_to_completion` otherwise retries indefinitely. A progress/fault callback must not call Store services or write through the source connection.
6. Destroy/finish the backup handle first, then explicitly end the read transaction. On every error path, both must unwind before later work; a partial destination is non-authoritative and removed by staging RAII.
7. Recheck the writer lock and database identity. The live database may now be newer only because of an out-of-bound noncooperative writer; the owned inventory and staged DB nevertheless remain the pinned snapshot pair.
8. Only after the SQLite read transaction is closed, iterate the owned inventory in digest order. For each exact entry, call `Vault::open_verified` once, compare digest, byte length, and canonical key, and stream/hash the retained handle into the container. No SQLite transaction spans Vault, age, filesystem publication, or an external process.
9. Retain the application writer lock through database staging, all referenced Vault reads, verified plaintext construction, encryption finalization, ciphertext sync, no-clobber publication, and the final identity/durability checks. Never acquire Store state while holding a Vault lock.
10. The descriptor set is exactly one staged-database record plus the sorted unique owned inventory. A database/inventory/object mismatch fails closed; no later live inventory refresh may silently replace the pinned inventory.

Residual risk: a long pinned WAL reader can delay checkpoint reuse, and a noncooperative writer could grow the live WAL or consume disk while backup runs. Cooperative Heleos writers cannot do so because of the application lock. The step loop still needs a bounded failure policy for `BUSY`, `LOCKED`, I/O, capacity, and cancellation rather than an unbounded wait.

## Required regression tests

Add these to the already-declared `crates/heleos-core/tests/backup_restore.rs`; they do not require a new production path.

1. **External WAL commit after inventory, before first step.** Use a deterministic backup barrier after `vault_inventory_rows()` has returned. Through a separate raw `rusqlite` connection that deliberately ignores the application lock, commit a valid additional content row while the source read transaction is open, then release backup. Assert the staged/verified backup DB and descriptor table both contain the pre-commit inventory, while the live DB contains the later row. This test must fail if the outer deferred transaction is removed.
2. **External WAL commit between backup steps.** Force a multi-page database and a one/few-page step size; pause after a `More` result, commit through a separate raw WAL connection, and finish. Assert no backup restart advances the archive to the later inventory and that DB/table remain the pinned pre-commit pair.
3. **Whole-create cooperative lock retention.** At barriers after inventory, after SQLite `Done`, during Vault streaming, and before publication, a second `Store::open_writer` must return exact `WriterBusy`. This proves that closing the SQLite read transaction before Vault does not release the application writer authority.
4. **Read-transaction cleanup on every backup outcome.** Inject destination/step failure and cancellation. After return, perform a normal audited repository write through the same Store and require success; this detects a leaked read transaction or backup handle. Require staging cleanup and no published backup.
5. **Inventory completeness.** Back up committed content plus queued/running/interrupted checkpoint originals and an accepted checkpoint manifest; require exact digest-sorted descriptors and restored retrieval. Terminal failed/cancelled checkpoint-only objects remain excluded, matching the existing Task 6 inventory authority.
6. **WAL source proof.** Create committed WAL activity before backup, require the live source connection remains WAL, and verify the standalone staged DB passes integrity/foreign-key/audit/lineage checks without copying the live `-wal`/`-shm` files as archive entries.
7. **Retry bound.** Deterministically produce `Busy`/`Locked` or inject the equivalent step outcome and prove the exact retry/deadline cap terminates with cleanup. Do not rely on `run_to_completion`'s unbounded loop.

The first two tests are the essential falsification boundary: they distinguish true SQLite snapshot equivalence from equivalence that holds only because tests used cooperative writers.

## Exact Task 7 plan amendment

Replace the preflight inventory/snapshot ruling and the brief's creation ordering with:

> `BackupService::create` receives `&mut Store` and retains its exclusive application writer lock for the complete operation. After writer/database identity checks, it starts one explicit DEFERRED read transaction on the live writer connection. The exact existing `vault_inventory_rows()` is the first read and pins the source snapshot. Without any intervening callback or service call, the service runs the SQLite online backup from that same transaction/source connection to a distinct capped staging connection and reaches `SQLITE_DONE`. It destroys the backup handle and explicitly ends the read transaction before any Vault, age, or publication operation. It never uses an IMMEDIATE/write transaction for backup and never refreshes inventory from the later live database. Busy/locked stepping is bounded. It then rechecks Store identity and opens/re-hashes exactly the owned inventory while the application writer lock remains held. No SQLite transaction spans Vault hashing or age I/O.

If that amendment is not accepted, Task 7 remains blocked on authorizing at least one additional Store/repository path for a reusable exact staged-database inventory query. Inventory-before/after adjacency or duplicated inventory SQL is not an acceptable fallback.
