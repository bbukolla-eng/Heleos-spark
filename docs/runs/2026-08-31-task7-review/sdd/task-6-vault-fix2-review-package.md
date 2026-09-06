# Review package: 33a9555ef9a6e5be7cc866d934eef9b1cbece5b3..3fbbd0d1626979b8b374d3be29fd1cfacd253cb8

## Commits
3fbbd0d review snapshot: task6 vault lock fix round 2

## Files changed
 crates/heleos-core/src/vault/mod.rs | 393 ++++++++++++++++++++++++++++++++++++
 1 file changed, 393 insertions(+)

## Diff
diff --git a/crates/heleos-core/src/vault/mod.rs b/crates/heleos-core/src/vault/mod.rs
index c05d2ea..ffce6c9 100644
--- a/crates/heleos-core/src/vault/mod.rs
+++ b/crates/heleos-core/src/vault/mod.rs
@@ -586,20 +586,22 @@ impl Vault {
     ) -> Result<PutOutcome> {
         self.with_lock_internal(true, faults, || {
             self.recheck_fixed_layout()?;
             let (logical_bytes, orphan_remaining_bytes, final_object_count) = match accounting {
                 VaultPublicationAccounting::Legacy => (self.scan_logical_store()?, u64::MAX, None),
                 VaultPublicationAccounting::Accounted {
                     inventory,
                     orphan_budget,
                 } => {
                     let totals = self.scan_accounted_store(inventory, orphan_budget)?;
+                    #[cfg(test)]
+                    faults.observe(OperationEvent::AccountedScanComplete);
                     (
                         totals.physical_bytes,
                         totals.orphan_remaining_bytes,
                         Some(totals.final_object_count),
                     )
                 }
             };
             let remaining_store = FOUNDATION_MAX_STORE_BYTES
                 .checked_sub(logical_bytes)
                 .ok_or(HeleosError::Quota)?;
@@ -1891,20 +1893,21 @@ impl FaultInjector for TestFault {
         }
     }
 }
 
 #[cfg(test)]
 #[derive(Clone, Debug, Eq, PartialEq)]
 enum OperationEvent {
     LockAttempt { exclusive: bool },
     LockAcquired { exclusive: bool },
     OperationEntered { exclusive: bool },
+    AccountedScanComplete,
     StagingVerified,
     BeforeFinalLink,
     FinalLinkCreated,
     FinalDirectorySynced,
     StagingHandleClosed,
     StagingNameRemoved,
     StagingDirectorySynced,
     FinalReopened,
     FinalVerified,
     CapacityPathQuery(CapacityQuery, PathBuf),
@@ -2118,20 +2121,39 @@ impl Vault {
 
     fn put_reader_with_test_fault<R: Read>(
         &self,
         reader: R,
         budget: VaultWriteBudget,
         fault: TestFault,
     ) -> Result<PutOutcome> {
         self.put_reader_internal(reader, budget, fault)
     }
 
+    fn put_reader_accounted_with_test_fault<R: Read, F: FaultInjector>(
+        &self,
+        reader: R,
+        budget: VaultWriteBudget,
+        inventory: &VaultInventory,
+        orphan_budget: VaultOrphanBudget,
+        fault: F,
+    ) -> Result<PutOutcome> {
+        self.put_reader_internal_with_accounting(
+            reader,
+            budget,
+            VaultPublicationAccounting::Accounted {
+                inventory,
+                orphan_budget,
+            },
+            fault,
+        )
+    }
+
     fn verify_with_test_fault<F: FaultInjector>(
         &self,
         digest: Sha256Digest,
         fault: F,
     ) -> Result<VaultVerification> {
         self.verify_internal(digest, fault)
     }
 
     fn put_reader_fault_events_for_test<R: Read>(
         &self,
@@ -2242,20 +2264,92 @@ fn encode_relative_os_path_for_test(path: &std::path::Path) -> EncodedVaultPath
 }
 
 #[cfg(test)]
 mod tests {
     use std::io::Cursor;
     use std::path::{Path, PathBuf};
 
     use super::*;
     use crate::canonical_json;
 
+    #[derive(Clone, Copy)]
+    struct AccountedAfterScanPauseFault<'a> {
+        reached: &'a std::sync::Barrier,
+        release: &'a std::sync::Barrier,
+    }
+
+    impl FaultInjector for AccountedAfterScanPauseFault<'_> {
+        fn observe(self, event: OperationEvent) {
+            if event == OperationEvent::AccountedScanComplete {
+                self.reached.wait();
+                self.release.wait();
+            }
+        }
+    }
+
+    #[derive(Clone, Copy)]
+    struct AccountedProcessAfterScanFault<'a> {
+        ready: &'a Path,
+        release: &'a Path,
+    }
+
+    impl FaultInjector for AccountedProcessAfterScanFault<'_> {
+        fn observe(self, event: OperationEvent) {
+            if event == OperationEvent::AccountedScanComplete {
+                std::fs::write(self.ready, b"ready")
+                    .expect("publish accounted after-scan readiness");
+                let deadline = std::time::Instant::now() + std::time::Duration::from_secs(30);
+                while !self.release.exists() {
+                    assert!(
+                        std::time::Instant::now() < deadline,
+                        "timed out waiting for accounted after-scan release"
+                    );
+                    std::thread::sleep(std::time::Duration::from_millis(1));
+                }
+            }
+        }
+    }
+
+    #[derive(Clone, Copy)]
+    struct AccountedProcessLockSentinelFault<'a> {
+        attempt: &'a Path,
+        attempt_release: &'a Path,
+        proceeding: &'a Path,
+        acquired: &'a Path,
+    }
+
+    impl FaultInjector for AccountedProcessLockSentinelFault<'_> {
+        fn observe(self, event: OperationEvent) {
+            match event {
+                OperationEvent::LockAttempt { exclusive: true } => {
+                    std::fs::write(self.attempt, b"attempt")
+                        .expect("publish second-writer lock attempt");
+                    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(30);
+                    while !self.attempt_release.exists() {
+                        assert!(
+                            std::time::Instant::now() < deadline,
+                            "timed out waiting to release second-writer lock attempt"
+                        );
+                        std::thread::sleep(std::time::Duration::from_millis(1));
+                    }
+                    std::fs::write(self.proceeding, b"proceeding")
+                        .expect("publish second-writer lock progress");
+                }
+                OperationEvent::LockAcquired { exclusive: true } => {
+                    std::fs::write(self.acquired, b"acquired")
+                        .expect("publish second-writer lock acquisition");
+                }
+                _ => {}
+            }
+        }
+    }
+
     fn private_test_vault(label: &str) -> (tempfile::TempDir, PathBuf, Vault) {
         let parent = tempfile::Builder::new()
             .prefix(label)
             .tempdir()
             .expect("create private unit-test parent");
         crate::apply_private_permissions(parent.path()).expect("harden private unit-test parent");
         let parent_path = std::fs::canonicalize(parent.path()).expect("canonicalize parent");
         let root = parent_path.join("vault");
         let vault = Vault::open(VaultConfig {
             root: root.clone(),
@@ -2841,20 +2935,125 @@ mod tests {
                 .count();
             assert_eq!(newly_published, 1);
             assert_eq!(duplicates, usize::from(same_digest));
             assert_eq!(rejected, usize::from(!same_digest));
         }
 
         run(false);
         run(true);
     }
 
+    #[test]
+    fn accounted_writer_holds_one_lock_from_scan_through_thread_publication() {
+        fn run(same_digest: bool) {
+            let (_parent, root, first) =
+                private_test_vault("heleos-vault-accounted-after-scan-thread-");
+            let second = Vault::open(VaultConfig {
+                root,
+                open_mode: VaultOpenMode::ExistingOnly,
+            })
+            .expect("open second same-root instance");
+            let left = b"accounted-lock-A";
+            let right = if same_digest {
+                left.as_slice()
+            } else {
+                b"accounted-lock-B".as_slice()
+            };
+            let empty = VaultInventory::try_from_entries([]).expect("empty inventory");
+            let orphan_budget =
+                VaultOrphanBudget::new(left.len() as u64).expect("exact orphan headroom");
+            let reached = std::sync::Barrier::new(2);
+            let release = std::sync::Barrier::new(2);
+            let (events_tx, events_rx) = std::sync::mpsc::channel();
+            let (right_results_tx, right_results_rx) = std::sync::mpsc::channel();
+
+            let (left_outcome, right_outcome) = std::thread::scope(|scope| {
+                let left_thread = scope.spawn(|| {
+                    first.put_reader_accounted_with_test_fault(
+                        Cursor::new(left),
+                        budget(),
+                        &empty,
+                        orphan_budget,
+                        AccountedAfterScanPauseFault {
+                            reached: &reached,
+                            release: &release,
+                        },
+                    )
+                });
+                reached.wait();
+                let right_thread = scope.spawn(|| {
+                    let outcome = second.put_reader_accounted_with_test_fault(
+                        Cursor::new(right),
+                        budget(),
+                        &empty,
+                        orphan_budget,
+                        EventChannelFault { sender: &events_tx },
+                    );
+                    right_results_tx
+                        .send(outcome)
+                        .expect("publish second writer result");
+                });
+                assert_eq!(
+                    events_rx
+                        .recv_timeout(std::time::Duration::from_secs(5))
+                        .expect("second writer reaches its lock attempt"),
+                    OperationEvent::LockAttempt { exclusive: true }
+                );
+                assert!(
+                    events_rx
+                        .recv_timeout(std::time::Duration::from_millis(200))
+                        .is_err(),
+                    "second writer must not acquire or enter while the first is paused after scan"
+                );
+                assert!(
+                    matches!(
+                        right_results_rx.try_recv(),
+                        Err(std::sync::mpsc::TryRecvError::Empty)
+                    ),
+                    "second writer must not finish while the first is paused after scan"
+                );
+                release.wait();
+                let left_outcome = left_thread.join().expect("join first writer");
+                right_thread.join().expect("join second writer");
+                let right_outcome = right_results_rx
+                    .recv_timeout(std::time::Duration::from_secs(5))
+                    .expect("receive second writer result after release");
+                (left_outcome, right_outcome)
+            });
+
+            let mut labels = [left_outcome, right_outcome]
+                .into_iter()
+                .map(|outcome| match outcome.expect("accounted writer result") {
+                    PutOutcome::Stored(StoredObject {
+                        newly_published: true,
+                        ..
+                    }) => "stored-new",
+                    PutOutcome::Stored(StoredObject {
+                        newly_published: false,
+                        ..
+                    }) => "stored-duplicate",
+                    PutOutcome::QuotaRejected { .. } => "quota-rejected",
+                })
+                .collect::<Vec<_>>();
+            labels.sort_unstable();
+            let expected = if same_digest {
+                vec!["stored-duplicate", "stored-new"]
+            } else {
+                vec!["quota-rejected", "stored-new"]
+            };
+            assert_eq!(labels, expected);
+        }
+
+        run(false);
+        run(true);
+    }
+
     #[test]
     fn accounted_process_race_helper() {
         let Some(root) = std::env::var_os("HELEOS_PRIVATE_ACCOUNTED_RACE_ROOT") else {
             return;
         };
         let payload = match std::env::var("HELEOS_PRIVATE_ACCOUNTED_RACE_PAYLOAD").as_deref() {
             Ok("a") => b"accounted-race-A".as_slice(),
             Ok("b") => b"accounted-race-B".as_slice(),
             _ => panic!("invalid accounted-race payload"),
         };
@@ -2900,20 +3099,214 @@ mod tests {
             }) => "stored-new",
             PutOutcome::Stored(StoredObject {
                 newly_published: false,
                 ..
             }) => "stored-duplicate",
             PutOutcome::QuotaRejected { .. } => "quota-rejected",
         };
         std::fs::write(result, label).expect("publish accounted-race result");
     }
 
+    #[test]
+    fn accounted_after_scan_process_helper() {
+        let Some(root) = std::env::var_os("HELEOS_PRIVATE_ACCOUNTED_LOCK_ROOT") else {
+            return;
+        };
+        let role = std::env::var("HELEOS_PRIVATE_ACCOUNTED_LOCK_ROLE")
+            .expect("accounted-lock process role");
+        let payload = match std::env::var("HELEOS_PRIVATE_ACCOUNTED_LOCK_PAYLOAD").as_deref() {
+            Ok("a") => b"accounted-lock-A".as_slice(),
+            Ok("b") => b"accounted-lock-B".as_slice(),
+            _ => panic!("invalid accounted-lock payload"),
+        };
+        let result = PathBuf::from(
+            std::env::var_os("HELEOS_PRIVATE_ACCOUNTED_LOCK_RESULT")
+                .expect("accounted-lock result path"),
+        );
+        let vault = Vault::open(VaultConfig {
+            root: PathBuf::from(root),
+            open_mode: VaultOpenMode::ExistingOnly,
+        })
+        .expect("open accounted-lock vault");
+        let empty = VaultInventory::try_from_entries([]).expect("empty inventory");
+        let orphan_budget =
+            VaultOrphanBudget::new(payload.len() as u64).expect("exact orphan headroom");
+        let outcome = if role == "a" {
+            let ready = PathBuf::from(
+                std::env::var_os("HELEOS_PRIVATE_ACCOUNTED_LOCK_READY")
+                    .expect("accounted-lock ready path"),
+            );
+            let release = PathBuf::from(
+                std::env::var_os("HELEOS_PRIVATE_ACCOUNTED_LOCK_RELEASE")
+                    .expect("accounted-lock release path"),
+            );
+            vault.put_reader_accounted_with_test_fault(
+                Cursor::new(payload),
+                budget(),
+                &empty,
+                orphan_budget,
+                AccountedProcessAfterScanFault {
+                    ready: &ready,
+                    release: &release,
+                },
+            )
+        } else if role == "b" {
+            let attempt = PathBuf::from(
+                std::env::var_os("HELEOS_PRIVATE_ACCOUNTED_LOCK_ATTEMPT")
+                    .expect("accounted-lock attempt path"),
+            );
+            let attempt_release = PathBuf::from(
+                std::env::var_os("HELEOS_PRIVATE_ACCOUNTED_LOCK_ATTEMPT_RELEASE")
+                    .expect("accounted-lock attempt-release path"),
+            );
+            let proceeding = PathBuf::from(
+                std::env::var_os("HELEOS_PRIVATE_ACCOUNTED_LOCK_PROCEEDING")
+                    .expect("accounted-lock proceeding path"),
+            );
+            let acquired = PathBuf::from(
+                std::env::var_os("HELEOS_PRIVATE_ACCOUNTED_LOCK_ACQUIRED")
+                    .expect("accounted-lock acquired path"),
+            );
+            vault.put_reader_accounted_with_test_fault(
+                Cursor::new(payload),
+                budget(),
+                &empty,
+                orphan_budget,
+                AccountedProcessLockSentinelFault {
+                    attempt: &attempt,
+                    attempt_release: &attempt_release,
+                    proceeding: &proceeding,
+                    acquired: &acquired,
+                },
+            )
+        } else {
+            panic!("invalid accounted-lock process role");
+        }
+        .expect("complete accounted-lock process publication");
+        let label = match outcome {
+            PutOutcome::Stored(StoredObject {
+                newly_published: true,
+                ..
+            }) => "stored-new",
+            PutOutcome::Stored(StoredObject {
+                newly_published: false,
+                ..
+            }) => "stored-duplicate",
+            PutOutcome::QuotaRejected { .. } => "quota-rejected",
+        };
+        std::fs::write(result, label).expect("publish accounted-lock result");
+    }
+
+    #[test]
+    fn accounted_writer_holds_one_lock_from_scan_through_process_publication() {
+        fn wait_for_marker(path: &Path, description: &str) {
+            let deadline = std::time::Instant::now() + std::time::Duration::from_secs(30);
+            while !path.exists() {
+                assert!(
+                    std::time::Instant::now() < deadline,
+                    "timed out waiting for {description}"
+                );
+                std::thread::sleep(std::time::Duration::from_millis(1));
+            }
+        }
+
+        fn assert_exclusive_lock_is_held(vault: &Vault) {
+            match FileExt::try_lock_exclusive(&vault.lock_file) {
+                Err(error) if error.kind() == io::ErrorKind::WouldBlock => {}
+                Ok(()) => {
+                    FileExt::unlock(&vault.lock_file)
+                        .expect("release unexpectedly available test lock");
+                    panic!("accounted writer released its exclusive lock after scan");
+                }
+                Err(error) => panic!("unexpected nonblocking lock error: {error}"),
+            }
+        }
+
+        fn run(second_payload: &str, expected: [&str; 2]) {
+            let (_parent, root, retained) =
+                private_test_vault("heleos-vault-accounted-after-scan-process-");
+            let control = root.parent().expect("vault parent");
+            let ready = control.join("accounted-lock-ready");
+            let release = control.join("accounted-lock-release");
+            let attempt = control.join("accounted-lock-attempt");
+            let attempt_release = control.join("accounted-lock-attempt-release");
+            let proceeding = control.join("accounted-lock-proceeding");
+            let acquired = control.join("accounted-lock-acquired");
+            let first_result = control.join("accounted-lock-result-a");
+            let second_result = control.join("accounted-lock-result-b");
+            let executable = std::env::current_exe().expect("locate library test executable");
+            let mut first = TestChildGuard(Some(
+                std::process::Command::new(&executable)
+                    .arg("--exact")
+                    .arg("vault::tests::accounted_after_scan_process_helper")
+                    .arg("--nocapture")
+                    .env("HELEOS_PRIVATE_ACCOUNTED_LOCK_ROOT", &root)
+                    .env("HELEOS_PRIVATE_ACCOUNTED_LOCK_ROLE", "a")
+                    .env("HELEOS_PRIVATE_ACCOUNTED_LOCK_PAYLOAD", "a")
+                    .env("HELEOS_PRIVATE_ACCOUNTED_LOCK_READY", &ready)
+                    .env("HELEOS_PRIVATE_ACCOUNTED_LOCK_RELEASE", &release)
+                    .env("HELEOS_PRIVATE_ACCOUNTED_LOCK_RESULT", &first_result)
+                    .spawn()
+                    .expect("spawn after-scan lock holder"),
+            ));
+            wait_for_marker(&ready, "first writer after-scan readiness");
+            assert_exclusive_lock_is_held(&retained);
+
+            let mut second = TestChildGuard(Some(
+                std::process::Command::new(&executable)
+                    .arg("--exact")
+                    .arg("vault::tests::accounted_after_scan_process_helper")
+                    .arg("--nocapture")
+                    .env("HELEOS_PRIVATE_ACCOUNTED_LOCK_ROOT", &root)
+                    .env("HELEOS_PRIVATE_ACCOUNTED_LOCK_ROLE", "b")
+                    .env("HELEOS_PRIVATE_ACCOUNTED_LOCK_PAYLOAD", second_payload)
+                    .env("HELEOS_PRIVATE_ACCOUNTED_LOCK_ATTEMPT", &attempt)
+                    .env(
+                        "HELEOS_PRIVATE_ACCOUNTED_LOCK_ATTEMPT_RELEASE",
+                        &attempt_release,
+                    )
+                    .env("HELEOS_PRIVATE_ACCOUNTED_LOCK_PROCEEDING", &proceeding)
+                    .env("HELEOS_PRIVATE_ACCOUNTED_LOCK_ACQUIRED", &acquired)
+                    .env("HELEOS_PRIVATE_ACCOUNTED_LOCK_RESULT", &second_result)
+                    .spawn()
+                    .expect("spawn blocked accounted writer"),
+            ));
+            wait_for_marker(&attempt, "second writer lock attempt");
+            assert!(!acquired.exists());
+            assert!(!second_result.exists());
+            assert_exclusive_lock_is_held(&retained);
+
+            std::fs::write(&attempt_release, b"continue")
+                .expect("release second writer lock attempt");
+            wait_for_marker(&proceeding, "second writer lock progress");
+            assert!(!acquired.exists());
+            assert!(!second_result.exists());
+            assert_exclusive_lock_is_held(&retained);
+
+            std::fs::write(&release, b"release").expect("release first accounted writer");
+            assert!(first.wait().expect("wait for first writer").success());
+            assert!(second.wait().expect("wait for second writer").success());
+            wait_for_marker(&acquired, "second writer lock acquisition");
+            let mut observed = [
+                std::fs::read_to_string(&first_result).expect("read first writer result"),
+                std::fs::read_to_string(&second_result).expect("read second writer result"),
+            ];
+            observed.sort();
+            let mut expected = expected.map(str::to_owned);
+            expected.sort();
+            assert_eq!(observed, expected);
+        }
+
+        run("b", ["stored-new", "quota-rejected"]);
+        run("a", ["stored-new", "stored-duplicate"]);
+    }
+
     #[test]
     fn independent_processes_serialize_accounted_scan_and_publication() {
         fn run(payloads: [&str; 2], expected: [&str; 2]) {
             let (_parent, root, _vault) = private_test_vault("heleos-vault-accounted-process-");
             let control = root.parent().expect("vault parent");
             let start = control.join("accounted-start");
             let ready = [
                 control.join("accounted-ready-a"),
                 control.join("accounted-ready-b"),
             ];
