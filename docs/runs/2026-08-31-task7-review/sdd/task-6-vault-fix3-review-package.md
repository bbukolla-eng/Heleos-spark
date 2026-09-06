# Review package: 3fbbd0d1626979b8b374d3be29fd1cfacd253cb8..e1b4f87c8ecca5eb6e09a0738b29783dc92b632c

## Commits
e1b4f87 review snapshot: task6 vault lock fix round 3

## Files changed
 crates/heleos-core/src/vault/mod.rs | 51 ++++++++++++++++++++++++++++++++++---
 1 file changed, 47 insertions(+), 4 deletions(-)

## Diff
diff --git a/crates/heleos-core/src/vault/mod.rs b/crates/heleos-core/src/vault/mod.rs
index ffce6c9..4920444 100644
--- a/crates/heleos-core/src/vault/mod.rs
+++ b/crates/heleos-core/src/vault/mod.rs
@@ -2279,20 +2279,47 @@ mod tests {
 
     impl FaultInjector for AccountedAfterScanPauseFault<'_> {
         fn observe(self, event: OperationEvent) {
             if event == OperationEvent::AccountedScanComplete {
                 self.reached.wait();
                 self.release.wait();
             }
         }
     }
 
+    struct BarrierReleaseGuard<'a> {
+        release: Option<&'a std::sync::Barrier>,
+    }
+
+    impl<'a> BarrierReleaseGuard<'a> {
+        fn new(release: &'a std::sync::Barrier) -> Self {
+            Self {
+                release: Some(release),
+            }
+        }
+
+        fn release(mut self) {
+            self.release
+                .take()
+                .expect("barrier release guard is armed")
+                .wait();
+        }
+    }
+
+    impl Drop for BarrierReleaseGuard<'_> {
+        fn drop(&mut self) {
+            if let Some(release) = self.release.take() {
+                release.wait();
+            }
+        }
+    }
+
     #[derive(Clone, Copy)]
     struct AccountedProcessAfterScanFault<'a> {
         ready: &'a Path,
         release: &'a Path,
     }
 
     impl FaultInjector for AccountedProcessAfterScanFault<'_> {
         fn observe(self, event: OperationEvent) {
             if event == OperationEvent::AccountedScanComplete {
                 std::fs::write(self.ready, b"ready")
@@ -2957,68 +2984,84 @@ mod tests {
                 left.as_slice()
             } else {
                 b"accounted-lock-B".as_slice()
             };
             let empty = VaultInventory::try_from_entries([]).expect("empty inventory");
             let orphan_budget =
                 VaultOrphanBudget::new(left.len() as u64).expect("exact orphan headroom");
             let reached = std::sync::Barrier::new(2);
             let release = std::sync::Barrier::new(2);
             let (events_tx, events_rx) = std::sync::mpsc::channel();
+            let (right_entered_tx, right_entered_rx) = std::sync::mpsc::channel();
             let (right_results_tx, right_results_rx) = std::sync::mpsc::channel();
 
             let (left_outcome, right_outcome) = std::thread::scope(|scope| {
                 let left_thread = scope.spawn(|| {
                     first.put_reader_accounted_with_test_fault(
                         Cursor::new(left),
                         budget(),
                         &empty,
                         orphan_budget,
                         AccountedAfterScanPauseFault {
                             reached: &reached,
                             release: &release,
                         },
                     )
                 });
                 reached.wait();
+                let release_guard = BarrierReleaseGuard::new(&release);
+                assert!(
+                    matches!(
+                        second.process_lock.try_lock(),
+                        Err(std::sync::TryLockError::WouldBlock)
+                    ),
+                    "first writer must retain the process mutex after its accounted scan"
+                );
                 let right_thread = scope.spawn(|| {
+                    right_entered_tx
+                        .send(())
+                        .expect("publish second writer function entry");
                     let outcome = second.put_reader_accounted_with_test_fault(
                         Cursor::new(right),
                         budget(),
                         &empty,
                         orphan_budget,
                         EventChannelFault { sender: &events_tx },
                     );
                     right_results_tx
                         .send(outcome)
                         .expect("publish second writer result");
                 });
+                right_entered_rx
+                    .recv_timeout(std::time::Duration::from_secs(5))
+                    .expect("second writer enters its accounted function");
                 assert_eq!(
                     events_rx
                         .recv_timeout(std::time::Duration::from_secs(5))
                         .expect("second writer reaches its lock attempt"),
                     OperationEvent::LockAttempt { exclusive: true }
                 );
                 assert!(
-                    events_rx
-                        .recv_timeout(std::time::Duration::from_millis(200))
-                        .is_err(),
+                    matches!(
+                        events_rx.try_recv(),
+                        Err(std::sync::mpsc::TryRecvError::Empty)
+                    ),
                     "second writer must not acquire or enter while the first is paused after scan"
                 );
                 assert!(
                     matches!(
                         right_results_rx.try_recv(),
                         Err(std::sync::mpsc::TryRecvError::Empty)
                     ),
                     "second writer must not finish while the first is paused after scan"
                 );
-                release.wait();
+                release_guard.release();
                 let left_outcome = left_thread.join().expect("join first writer");
                 right_thread.join().expect("join second writer");
                 let right_outcome = right_results_rx
                     .recv_timeout(std::time::Duration::from_secs(5))
                     .expect("receive second writer result after release");
                 (left_outcome, right_outcome)
             });
 
             let mut labels = [left_outcome, right_outcome]
                 .into_iter()
