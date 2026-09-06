# Review package: e032b14195b9b382be49c2d19f5bada4f7ef072e..589e03a45b8838a77629f0b5700274d37e93517c

## Commits
589e03a review snapshot: task6 inventory fix round 1

## Files changed
 crates/heleos-core/src/store/ingest_repository.rs | 158 +++++++++++++++++++++-
 1 file changed, 154 insertions(+), 4 deletions(-)

## Diff
diff --git a/crates/heleos-core/src/store/ingest_repository.rs b/crates/heleos-core/src/store/ingest_repository.rs
index 95c5a60..8cac4d9 100644
--- a/crates/heleos-core/src/store/ingest_repository.rs
+++ b/crates/heleos-core/src/store/ingest_repository.rs
@@ -45,33 +45,36 @@ const VAULT_INVENTORY_COUNT_SQL: &str = "WITH inventory_candidates(digest) AS (
                     WHEN json_valid(checkpoint_json)
                     THEN json_extract(checkpoint_json, '$.detail.original.digest')
                     ELSE 'invalid-checkpoint:' || id
                 END
          FROM job_runs
          WHERE state IN ('queued', 'running', 'interrupted')
            AND (
                NOT json_valid(checkpoint_json)
                OR CASE
                       WHEN json_valid(checkpoint_json)
-                      THEN json_extract(checkpoint_json, '$.phase') IN (
-                          'vault_published', 'processing_complete'
-                      )
+                      THEN json_extract(checkpoint_json, '$.phase') = 'vault_published'
+                           OR (
+                               state IN ('running', 'interrupted')
+                               AND json_extract(checkpoint_json, '$.phase') =
+                                   'processing_complete'
+                           )
                       ELSE 0
                   END
            )
          UNION ALL
          SELECT json_extract(
                     checkpoint_json,
                     '$.detail.candidate.detail.manifest_object.digest'
                 )
          FROM job_runs
-         WHERE state IN ('queued', 'running', 'interrupted')
+         WHERE state IN ('running', 'interrupted')
            AND json_valid(checkpoint_json)
            AND json_extract(checkpoint_json, '$.phase') = 'processing_complete'
            AND json_extract(checkpoint_json, '$.detail.candidate.kind') = 'accepted'
      )
      SELECT COUNT(*)
      FROM (SELECT digest FROM inventory_candidates GROUP BY digest)";
 
 pub(crate) enum IdempotencyLookup {
     MissingProject,
     Vacant,
@@ -953,20 +956,28 @@ impl Store {
                 created_at_ms,
                 deadline_at_ms,
                 input,
                 budget,
                 checkpoint,
             };
             record.validate_for_scope(row_project_id, &idempotency_key)?;
             if (record.state == JobState::Running) != lease_owner.is_some() {
                 return Err(HeleosError::Integrity);
             }
+            if record.state == JobState::Queued
+                && matches!(
+                    &record.checkpoint.phase,
+                    IngestCheckpointPhaseV1::ProcessingComplete { .. }
+                )
+            {
+                return Err(HeleosError::Integrity);
+            }
             let expected_audit_after = json!({
                 "attempt": record.attempt,
                 "budget_sha256": sha256_text(&budget_json),
                 "checkpoint_sha256": sha256_text(&checkpoint_json),
                 "deadline_at_ms": record.deadline_at_ms,
                 "input_sha256": sha256_text(&input_json),
                 "lease_expires_at_ms": record.lease_expires_at_ms,
                 "lease_owner": lease_owner,
                 "state": state_text,
                 "terminal_result_ids": JsonValue::Null,
@@ -4518,20 +4529,58 @@ mod tests {
                 project_id,
                 actor: ActorId::from_str("fixture-actor").expect("actor"),
                 lease_owner,
                 now_ms,
                 lease_expires_at_ms: now_ms + LEASE_DURATION_MS,
                 audit_event_id,
             })
             .expect("start inventory fixture job");
     }
 
+    fn replace_queued_checkpoint_and_audit(
+        store: &mut Store,
+        job_id: JobId,
+        checkpoint: &IngestCheckpointV1,
+    ) {
+        store
+            .connection
+            .execute_batch(
+                "DROP TRIGGER job_runs_legal_transition;
+                 DROP TRIGGER audit_events_no_update;",
+            )
+            .expect("open hostile queued-checkpoint fixture");
+        let checkpoint_json = json_text(checkpoint, EIGHT_MIB).expect("checkpoint JSON");
+        store
+            .with_immediate_transaction(|transaction| {
+                transaction
+                    .execute(
+                        "UPDATE job_runs SET checkpoint_json = ?1 WHERE id = ?2",
+                        params![checkpoint_json, job_id.as_uuid().to_string()],
+                    )
+                    .map_err(|_| HeleosError::Database)?;
+                let audit_after = json_text(&job_audit_snapshot(transaction, job_id)?, ONE_MIB)?;
+                let changed = transaction
+                    .execute(
+                        "UPDATE audit_events SET after_json = ?1
+                         WHERE subject_type = 'job' AND subject_id = ?2
+                           AND action = 'job_created'",
+                        params![audit_after, job_id.as_uuid().to_string()],
+                    )
+                    .map_err(|_| HeleosError::Database)?;
+                if changed != 1 {
+                    return Err(HeleosError::Integrity);
+                }
+                Ok(())
+            })
+            .expect("forge matching queued checkpoint and audit");
+    }
+
     fn accepted_commit_fixture() -> (Store, AcceptedIntakeCommand) {
         let mut store = Store::open_in_memory().expect("open store");
         store.migrate().expect("migrate store");
         let project_id = ProjectId::from_uuid(uuid(100));
         let job_id = JobId::from_uuid(uuid(101));
         insert_project(&store, project_id);
         let input = valid_input(project_id, "accepted-command");
         let original = StoredObjectV1 {
             digest: input.content_sha256.expect("content digest"),
             byte_length: input.byte_length,
@@ -6051,20 +6100,121 @@ mod tests {
                 [cancelled_job_id.as_uuid().to_string()],
             )
             .expect("cancel inventory fixture job");
 
         assert_eq!(
             store.vault_inventory_rows().expect("read empty inventory"),
             VaultInventory::try_from_entries([]).expect("empty inventory")
         );
     }
 
+    #[test]
+    fn vault_inventory_rejects_queued_processing_accepted_with_a_matching_audit() {
+        // Break caught: a forged queued accepted checkpoint binding a manifest before job start.
+        let mut store = Store::open_in_memory().expect("open store");
+        store.migrate().expect("migrate store");
+        let project_id = ProjectId::from_uuid(uuid(100));
+        insert_project(&store, project_id);
+        let original = stored_object(100, 7);
+        let job_id = JobId::from_uuid(uuid(101));
+        queue_inventory_job(
+            &mut store,
+            project_id,
+            job_id,
+            input_for_object(project_id, "queued-accepted", &original),
+            IngestCheckpointV1 {
+                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+                phase: IngestCheckpointPhaseV1::VaultPublished {
+                    original: original.clone(),
+                },
+            },
+            10,
+            AuditEventId::from_uuid(uuid(102)),
+        );
+        let manifest = accepted_manifest(original.digest);
+        let manifest_bytes = manifest.canonical_bytes().expect("manifest bytes");
+        let manifest_digest =
+            Sha256Digest::hash_reader(manifest_bytes.as_slice()).expect("manifest digest");
+        replace_queued_checkpoint_and_audit(
+            &mut store,
+            job_id,
+            &IngestCheckpointV1 {
+                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+                phase: IngestCheckpointPhaseV1::ProcessingComplete {
+                    original,
+                    candidate: crate::ingest::job::ProcessingCandidateV1::Accepted {
+                        manifest,
+                        manifest_object: StoredObjectV1 {
+                            digest: manifest_digest,
+                            byte_length: u64::try_from(manifest_bytes.len())
+                                .expect("manifest length"),
+                            vault_key: crate::Vault::object_key(manifest_digest),
+                        },
+                    },
+                },
+            },
+        );
+
+        assert!(matches!(
+            store.vault_inventory_rows(),
+            Err(HeleosError::Integrity)
+        ));
+    }
+
+    #[test]
+    fn vault_inventory_rejects_queued_processing_quarantine_with_a_matching_audit() {
+        // Break caught: a forged queued quarantine checkpoint becoming resumable authority.
+        let mut store = Store::open_in_memory().expect("open store");
+        store.migrate().expect("migrate store");
+        let project_id = ProjectId::from_uuid(uuid(103));
+        insert_project(&store, project_id);
+        let original = stored_object(103, 7);
+        let job_id = JobId::from_uuid(uuid(104));
+        queue_inventory_job(
+            &mut store,
+            project_id,
+            job_id,
+            input_for_object(project_id, "queued-quarantine", &original),
+            IngestCheckpointV1 {
+                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+                phase: IngestCheckpointPhaseV1::VaultPublished {
+                    original: original.clone(),
+                },
+            },
+            10,
+            AuditEventId::from_uuid(uuid(105)),
+        );
+        replace_queued_checkpoint_and_audit(
+            &mut store,
+            job_id,
+            &IngestCheckpointV1 {
+                schema: INGEST_CHECKPOINT_SCHEMA_V1.to_owned(),
+                phase: IngestCheckpointPhaseV1::ProcessingComplete {
+                    original,
+                    candidate: crate::ingest::job::ProcessingCandidateV1::Quarantined {
+                        outcome: IngestOutcome::QuarantinedCorrupt,
+                        quarantine: IntakeQuarantineV1 {
+                            schema: INTAKE_QUARANTINE_SCHEMA_V1.to_owned(),
+                            reason: IntakeQuarantineReasonV1::Pdf(PdfQuarantineReason::Corrupt),
+                            probe_provenance: Some(provenance()),
+                        },
+                    },
+                },
+            },
+        );
+
+        assert!(matches!(
+            store.vault_inventory_rows(),
+            Err(HeleosError::Integrity)
+        ));
+    }
+
     #[test]
     fn vault_inventory_rejects_noncanonical_job_json_stale_audit_and_object_conflicts() {
         // Break caught: trusting mutable checkpoint bytes or silently choosing one object length.
         fn queued_store() -> (Store, ProjectId, StoredObjectV1) {
             let mut store = Store::open_in_memory().expect("open store");
             store.migrate().expect("migrate store");
             let project_id = ProjectId::from_uuid(uuid(110));
             insert_project(&store, project_id);
             let object = stored_object(110, 7);
             queue_inventory_job(
