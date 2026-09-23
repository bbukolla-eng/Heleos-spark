# Migration Recovery

Foundation 0.1 uses forward-only, transactional SQLite migrations. It does not support down-migrations, automatic rollback to an older schema, or repair by editing migration records. A failed migration leaves the last committed schema version authoritative.

## Recovery procedure

1. Stop every Heleos writer and confirm that no process holds the database writer lock. Keep readers stopped during evidence preservation and cutover.
2. Preserve the failed database and its matching `-wal` and `-shm` files as one incident artifact. Do not checkpoint, rename over, delete, or modify any member of that set. Record hashes and filesystem metadata before investigation.
3. Locate the most recent encrypted backup and verify its ciphertext integrity, trusted backup signer, manifest, and object hashes with separately controlled recovery identities. Do not use an unverified backup as a recovery source.
4. Restore the verified backup into a newly created, owner-private staging path. The destination must not already exist. Recovery files are never overwritten in place.
5. Open the staged database in defensive read-only and query-only mode. Run SQLite `integrity_check`, `quick_check`, and `foreign_key_check`; retain every reported violation. Stop if any result is not clean.
6. Acquire the staging database writer lock and run the supported forward migrations. Each version is applied in its own immediate transaction and its embedded SQL SHA-256 must match the recorded migration hash. An unknown newer schema or hash mismatch fails closed.
7. Reopen staging in defensive read-only mode, repeat integrity and foreign-key checks, and compare the restored database and vault inventory with the signed backup manifest hashes and counts.
8. Prepare a cutover record containing the failed-store hashes, verified-backup identity, staged-store hashes, migration report, integrity results, and rollback destination. Cutover requires explicit owner approval.
9. After approval, stop all processes again and switch configuration to the staged store without replacing the failed files. Preserve the failed store and the prior active store until the owner closes the incident and retention policy permits disposal.

If any step fails, leave the active and failed paths untouched, preserve the new evidence, and start again with another newly created staging path. Never copy a partially migrated database over a live database. Restore-and-forward is the only supported schema recovery path.
