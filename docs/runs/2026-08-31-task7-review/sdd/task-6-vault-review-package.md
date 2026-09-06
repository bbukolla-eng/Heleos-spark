# Review package: 589e03a45b8838a77629f0b5700274d37e93517c..33a9555ef9a6e5be7cc866d934eef9b1cbece5b3

## Commits
33a9555 review snapshot: task6 atomic vault

## Files changed
 crates/heleos-core/src/vault/mod.rs | 939 +++++++++++++++++++++++++++++++++++-
 1 file changed, 932 insertions(+), 7 deletions(-)

## Diff
diff --git a/crates/heleos-core/src/vault/mod.rs b/crates/heleos-core/src/vault/mod.rs
index b6f5511..c05d2ea 100644
--- a/crates/heleos-core/src/vault/mod.rs
+++ b/crates/heleos-core/src/vault/mod.rs
@@ -1,14 +1,14 @@
 mod path;
 mod reconcile;
 
-use std::collections::BTreeMap;
+use std::collections::{BTreeMap, BTreeSet};
 use std::ffi::{OsStr, OsString};
 use std::fs::File;
 use std::io::{self, Read, Seek, SeekFrom, Write};
 use std::path::PathBuf;
 use std::sync::{Arc, Mutex, OnceLock, Weak};
 
 use cap_fs_ext::MetadataExt;
 use cap_std::fs::{Dir, File as CapFile};
 use fs2::FileExt;
 use serde::Serialize;
@@ -21,27 +21,29 @@ use path::{
     ClassifiedEntry, FileMarker, HandleKind, StagingCreateError, TrustAnchor,
     classify_and_open_entry, create_staging_file, open_directory, open_final_file,
     open_or_create_directory, open_or_create_file, open_trust_anchor, recheck_directory_name,
     recheck_file_name, recheck_trust_anchor,
 };
 
 const MIBIBYTE: u64 = 1024 * 1024;
 const GIBIBYTE: u64 = 1024 * MIBIBYTE;
 const FOUNDATION_MAX_INPUT_BYTES: u64 = 256 * MIBIBYTE;
 const FOUNDATION_MAX_STORE_BYTES: u64 = 500 * GIBIBYTE;
+const FOUNDATION_MAX_ORPHAN_BYTES: u64 = 8 * GIBIBYTE;
 const MINIMUM_FREE_RESERVE_BYTES: u64 = 20 * GIBIBYTE;
 const STREAM_BUFFER_BYTES: usize = 64 * 1024;
 const LOCK_NAME: &str = ".vault.lock";
 const OBJECTS_NAME: &str = "objects";
 const SHA256_NAME: &str = "sha256";
 const STAGING_NAME: &str = ".staging";
 const MAX_STAGING_NAME_ATTEMPTS: usize = 128;
+const MAX_FINAL_OBJECTS: usize = 100_000;
 #[cfg(test)]
 const TEST_COLLIDING_STAGING_NAME: &str = "00000000-0000-4000-8000-000000000000.partial";
 
 #[derive(Clone, Debug, Eq, PartialEq)]
 pub struct VaultConfig {
     pub root: PathBuf,
     pub open_mode: VaultOpenMode,
 }
 
 #[derive(Clone, Copy, Debug, Eq, PartialEq)]
@@ -73,20 +75,34 @@ impl VaultWriteBudget {
 
     pub const fn max_input_bytes(&self) -> u64 {
         self.max_input_bytes
     }
 
     pub const fn max_retain_bytes(&self) -> u64 {
         self.max_retain_bytes
     }
 }
 
+#[derive(Clone, Copy, Debug, Eq, PartialEq)]
+pub(crate) struct VaultOrphanBudget {
+    remaining_bytes: u64,
+}
+
+impl VaultOrphanBudget {
+    pub(crate) fn new(remaining_bytes: u64) -> Result<Self> {
+        if remaining_bytes > FOUNDATION_MAX_ORPHAN_BYTES {
+            return Err(HeleosError::Integrity);
+        }
+        Ok(Self { remaining_bytes })
+    }
+}
+
 #[derive(Clone, Debug, Eq, PartialEq)]
 pub enum PutOutcome {
     Stored(StoredObject),
     QuotaRejected {
         digest: Sha256Digest,
         byte_length: u64,
     },
 }
 
 #[derive(Clone, Debug, Eq, PartialEq, Serialize)]
@@ -189,20 +205,123 @@ impl VaultInventory {
                 Some(_) => return Err(HeleosError::Integrity),
                 None => {
                     indexed.insert(entry.digest, entry);
                 }
             }
         }
         Ok(Self { entries: indexed })
     }
 }
 
+#[derive(Clone, Copy, Debug, Eq, PartialEq)]
+struct VaultMetadataTotals {
+    final_object_count: usize,
+    physical_bytes: u64,
+    orphan_remaining_bytes: u64,
+}
+
+struct VaultMetadataAccounting<'a> {
+    inventory: &'a VaultInventory,
+    seen_inventory: BTreeSet<Sha256Digest>,
+    orphan_allowance: u64,
+    final_object_count: usize,
+    physical_bytes: u64,
+    orphan_bytes: u64,
+}
+
+#[derive(Clone, Copy)]
+enum VaultPublicationAccounting<'a> {
+    Legacy,
+    Accounted {
+        inventory: &'a VaultInventory,
+        orphan_budget: VaultOrphanBudget,
+    },
+}
+
+impl<'a> VaultMetadataAccounting<'a> {
+    fn new(inventory: &'a VaultInventory, budget: VaultOrphanBudget) -> Result<Self> {
+        if inventory.entries.len() > MAX_FINAL_OBJECTS {
+            return Err(HeleosError::ResourceLimit);
+        }
+        for entry in inventory.entries.values() {
+            if entry.vault_key != Vault::object_key(entry.digest)
+                || entry.expected_byte_length > FOUNDATION_MAX_INPUT_BYTES
+            {
+                return Err(HeleosError::Integrity);
+            }
+        }
+        Ok(Self {
+            inventory,
+            seen_inventory: BTreeSet::new(),
+            orphan_allowance: budget.remaining_bytes,
+            final_object_count: 0,
+            physical_bytes: 0,
+            orphan_bytes: 0,
+        })
+    }
+
+    fn observe(&mut self, digest: Sha256Digest, byte_length: u64, vault_key: &str) -> Result<()> {
+        self.final_object_count = self
+            .final_object_count
+            .checked_add(1)
+            .ok_or(HeleosError::ResourceLimit)?;
+        if self.final_object_count > MAX_FINAL_OBJECTS {
+            return Err(HeleosError::ResourceLimit);
+        }
+        if byte_length > FOUNDATION_MAX_INPUT_BYTES {
+            return Err(HeleosError::ResourceLimit);
+        }
+        if vault_key != Vault::object_key(digest) {
+            return Err(HeleosError::PolicyDenied);
+        }
+        self.physical_bytes = self
+            .physical_bytes
+            .checked_add(byte_length)
+            .ok_or(HeleosError::Integrity)?;
+        if self.physical_bytes > FOUNDATION_MAX_STORE_BYTES {
+            return Err(HeleosError::Integrity);
+        }
+        if let Some(entry) = self.inventory.entries.get(&digest) {
+            if entry.vault_key != vault_key || entry.expected_byte_length != byte_length {
+                return Err(HeleosError::Integrity);
+            }
+            if !self.seen_inventory.insert(digest) {
+                return Err(HeleosError::Integrity);
+            }
+        } else {
+            self.orphan_bytes = self
+                .orphan_bytes
+                .checked_add(byte_length)
+                .ok_or(HeleosError::Integrity)?;
+            if self.orphan_bytes > self.orphan_allowance {
+                return Err(HeleosError::Integrity);
+            }
+        }
+        Ok(())
+    }
+
+    fn finish(self) -> Result<VaultMetadataTotals> {
+        if self.seen_inventory.len() != self.inventory.entries.len() {
+            return Err(HeleosError::Integrity);
+        }
+        let orphan_remaining_bytes = self
+            .orphan_allowance
+            .checked_sub(self.orphan_bytes)
+            .ok_or(HeleosError::Integrity)?;
+        Ok(VaultMetadataTotals {
+            final_object_count: self.final_object_count,
+            physical_bytes: self.physical_bytes,
+            orphan_remaining_bytes,
+        })
+    }
+}
+
 #[derive(Clone, Debug, Eq, PartialEq, Serialize)]
 pub struct EncodedVaultPath {
     pub encoding: String,
     pub value: String,
 }
 
 #[derive(Clone, Debug, Eq, PartialEq, Serialize)]
 #[serde(tag = "kind", rename_all = "snake_case")]
 pub enum ReconciliationFinding {
     StagingPartial {
@@ -371,20 +490,38 @@ impl Vault {
             lock_file,
             lock_marker,
             process_lock,
         })
     }
 
     pub fn put_reader<R: Read>(&self, reader: R, budget: VaultWriteBudget) -> Result<PutOutcome> {
         self.put_reader_internal(reader, budget, NoFault)
     }
 
+    pub(crate) fn put_reader_accounted<R: Read>(
+        &self,
+        reader: R,
+        budget: VaultWriteBudget,
+        inventory: &VaultInventory,
+        orphan_budget: VaultOrphanBudget,
+    ) -> Result<PutOutcome> {
+        self.put_reader_internal_with_accounting(
+            reader,
+            budget,
+            VaultPublicationAccounting::Accounted {
+                inventory,
+                orphan_budget,
+            },
+            NoFault,
+        )
+    }
+
     pub fn open_verified(&self, digest: Sha256Digest) -> Result<VerifiedObject> {
         self.with_lock(false, || {
             let key = Self::object_key(digest);
             let mut file = match self.open_digest_file(digest) {
                 Ok(file) => file,
                 Err(HeleosError::NotFound) => return Err(HeleosError::NotFound),
                 Err(error) => return Err(error),
             };
             let link_count = link_count(&file)?;
             if link_count != 1 {
@@ -420,41 +557,72 @@ impl Vault {
         let digest = digest.to_string();
         format!(
             "objects/sha256/{}/{}/{}",
             &digest[..2],
             &digest[2..4],
             digest
         )
     }
 
     fn put_reader_internal<R: Read, F: FaultInjector>(
+        &self,
+        reader: R,
+        budget: VaultWriteBudget,
+        faults: F,
+    ) -> Result<PutOutcome> {
+        self.put_reader_internal_with_accounting(
+            reader,
+            budget,
+            VaultPublicationAccounting::Legacy,
+            faults,
+        )
+    }
+
+    fn put_reader_internal_with_accounting<R: Read, F: FaultInjector>(
         &self,
         mut reader: R,
         budget: VaultWriteBudget,
+        accounting: VaultPublicationAccounting<'_>,
         faults: F,
     ) -> Result<PutOutcome> {
         self.with_lock_internal(true, faults, || {
             self.recheck_fixed_layout()?;
-            let logical_bytes = self.scan_logical_store()?;
+            let (logical_bytes, orphan_remaining_bytes, final_object_count) = match accounting {
+                VaultPublicationAccounting::Legacy => (self.scan_logical_store()?, u64::MAX, None),
+                VaultPublicationAccounting::Accounted {
+                    inventory,
+                    orphan_budget,
+                } => {
+                    let totals = self.scan_accounted_store(inventory, orphan_budget)?;
+                    (
+                        totals.physical_bytes,
+                        totals.orphan_remaining_bytes,
+                        Some(totals.final_object_count),
+                    )
+                }
+            };
             let remaining_store = FOUNDATION_MAX_STORE_BYTES
                 .checked_sub(logical_bytes)
                 .ok_or(HeleosError::Quota)?;
-            let retain_limit = budget.max_retain_bytes.min(remaining_store);
+            let retain_limit = budget
+                .max_retain_bytes
+                .min(remaining_store)
+                .min(orphan_remaining_bytes);
             let declared_staging = budget.max_input_bytes.min(retain_limit);
             let capacity = if faults.is_capacity_shortage() {
                 false
             } else {
                 self.has_staging_capacity(declared_staging, faults)?
             };
             if !capacity {
                 let (digest, length) = hash_bounded_reader(&mut reader, budget.max_input_bytes)?;
-                return self.duplicate_or_quota(digest, length);
+                return self.duplicate_or_quota(digest, length, final_object_count);
             }
 
             let mut staging_created = None;
             for attempt in 0..MAX_STAGING_NAME_ATTEMPTS {
                 let staging_name = faults.staging_name(attempt);
                 match create_staging_file(&self.staging_dir, OsStr::new(&staging_name)) {
                     Ok(opened) => {
                         staging_created = Some((staging_name, opened.file));
                         break;
                     }
@@ -483,40 +651,40 @@ impl Vault {
             if let Err(error) = sync_directory(&self.staging_dir) {
                 return cleanup_with_error(&mut staging, error);
             }
             let allocation = match faults.allocation_error_kind() {
                 Some(kind) => Err(io::Error::new(kind, "injected staging allocation failure")),
                 None => allocate_cap_file(staging.file()?, declared_staging),
             };
             if allocation.is_err() {
                 staging.cleanup()?;
                 let (digest, length) = hash_bounded_reader(&mut reader, budget.max_input_bytes)?;
-                return self.duplicate_or_quota(digest, length);
+                return self.duplicate_or_quota(digest, length, final_object_count);
             }
 
             let stream_result = stream_to_staging(
                 &mut reader,
                 staging.file_mut()?,
                 budget.max_input_bytes,
                 retain_limit,
                 &faults,
             );
             let (digest, length, retained_all) = match stream_result {
                 Ok(result) => result,
                 Err(error) => {
                     staging.cleanup()?;
                     return Err(error);
                 }
             };
             if !retained_all {
                 staging.cleanup()?;
-                return self.duplicate_or_quota(digest, length);
+                return self.duplicate_or_quota(digest, length, final_object_count);
             }
             if let Err(error) = staging.file()?.set_len(length) {
                 return cleanup_with_error(&mut staging, HeleosError::Io(error));
             }
             if faults.fail_at(FaultPoint::Flush) {
                 return cleanup_with_error(
                     &mut staging,
                     HeleosError::Io(io::Error::other("injected staging flush failure")),
                 );
             }
@@ -550,20 +718,24 @@ impl Vault {
             faults.observe(OperationEvent::StagingVerified);
             match self.open_digest_file(digest) {
                 Ok(existing) => {
                     let outcome = self.verify_existing_winner(digest, length, existing);
                     staging.cleanup()?;
                     return outcome;
                 }
                 Err(HeleosError::NotFound) => {}
                 Err(error) => return cleanup_with_error(&mut staging, error),
             }
+            if final_object_count == Some(MAX_FINAL_OBJECTS) {
+                staging.cleanup()?;
+                return Err(HeleosError::ResourceLimit);
+            }
             let has_reserve = match self
                 .has_publication_reserve(faults, PublicationCapacityPhase::BeforeShards)
             {
                 Ok(has_reserve) => has_reserve,
                 Err(error) => return cleanup_with_error(&mut staging, error),
             };
             if !has_reserve {
                 staging.cleanup()?;
                 return Ok(PutOutcome::QuotaRejected {
                     digest,
@@ -741,23 +913,31 @@ impl Vault {
             return Err(HeleosError::Integrity);
         }
         Ok(PutOutcome::Stored(StoredObject {
             digest,
             byte_length: expected_length,
             vault_key: Self::object_key(digest),
             newly_published: false,
         }))
     }
 
-    fn duplicate_or_quota(&self, digest: Sha256Digest, length: u64) -> Result<PutOutcome> {
+    fn duplicate_or_quota(
+        &self,
+        digest: Sha256Digest,
+        length: u64,
+        final_object_count: Option<usize>,
+    ) -> Result<PutOutcome> {
         match self.open_digest_file(digest) {
             Ok(file) => self.verify_existing_winner(digest, length, file),
+            Err(HeleosError::NotFound) if final_object_count == Some(MAX_FINAL_OBJECTS) => {
+                Err(HeleosError::ResourceLimit)
+            }
             Err(HeleosError::NotFound) => Ok(PutOutcome::QuotaRejected {
                 digest,
                 byte_length: length,
             }),
             Err(error) => Err(error),
         }
     }
 
     fn verify_locked(&self, digest: Sha256Digest) -> Result<VaultVerification> {
         let vault_key = Self::object_key(digest);
@@ -1039,20 +1219,72 @@ impl Vault {
                     total = total.checked_add(length).ok_or(HeleosError::Quota)?;
                     if total > FOUNDATION_MAX_STORE_BYTES {
                         return Err(HeleosError::Quota);
                     }
                 }
             }
         }
         Ok(total)
     }
 
+    fn scan_accounted_store(
+        &self,
+        inventory: &VaultInventory,
+        orphan_budget: VaultOrphanBudget,
+    ) -> Result<VaultMetadataTotals> {
+        reconcile::validate_fixed_layout(self)?;
+        let mut accounting = VaultMetadataAccounting::new(inventory, orphan_budget)?;
+        for first in self.sha256_dir.entries().map_err(HeleosError::Io)? {
+            let first = first.map_err(HeleosError::Io)?;
+            let first_name = first.file_name();
+            if !is_lower_hex_component(&first_name, 2) {
+                return Err(HeleosError::PolicyDenied);
+            }
+            let first_dir = open_directory(&self.sha256_dir, &first_name, false)?;
+            for second in first_dir.dir.entries().map_err(HeleosError::Io)? {
+                let second = second.map_err(HeleosError::Io)?;
+                let second_name = second.file_name();
+                if !is_lower_hex_component(&second_name, 2) {
+                    return Err(HeleosError::PolicyDenied);
+                }
+                let second_dir = open_directory(&first_dir.dir, &second_name, false)?;
+                for object in second_dir.dir.entries().map_err(HeleosError::Io)? {
+                    let object = object.map_err(HeleosError::Io)?;
+                    let name = object.file_name();
+                    let Some(text) = name.to_str() else {
+                        return Err(HeleosError::PolicyDenied);
+                    };
+                    let digest: Sha256Digest =
+                        text.parse().map_err(|_| HeleosError::PolicyDenied)?;
+                    let (expected_first, expected_second, expected_name) =
+                        digest_components(digest);
+                    if first_name != OsStr::new(&expected_first)
+                        || second_name != OsStr::new(&expected_second)
+                        || name != OsStr::new(&expected_name)
+                    {
+                        return Err(HeleosError::PolicyDenied);
+                    }
+                    let file = open_final_file(&second_dir.dir, &name)?.file;
+                    let metadata = file.metadata().map_err(HeleosError::Io)?;
+                    if !cap_metadata_is_regular(&metadata) {
+                        return Err(HeleosError::PolicyDenied);
+                    }
+                    if metadata.nlink() != 1 {
+                        return Err(HeleosError::PolicyDenied);
+                    }
+                    accounting.observe(digest, metadata.len(), &Self::object_key(digest))?;
+                }
+            }
+        }
+        accounting.finish()
+    }
+
     fn has_staging_capacity<F: FaultInjector>(
         &self,
         declared_staging: u64,
         faults: F,
     ) -> Result<bool> {
         let total = self.capacity_query(CapacityQuery::Total, faults)?;
         let granularity = self.capacity_query(CapacityQuery::AllocationGranularity, faults)?;
         let available = self.capacity_query(CapacityQuery::Available, faults)?;
         staging_capacity_is_sufficient(declared_staging, total, available, granularity)
     }
@@ -2039,20 +2271,713 @@ mod tests {
             .map(|entry| entry.expect("read staging entry").path())
             .collect();
         paths.sort();
         paths
     }
 
     fn budget() -> VaultWriteBudget {
         VaultWriteBudget::new(4096, 4096).expect("construct unit-test budget")
     }
 
+    fn store_bytes(vault: &Vault, bytes: &[u8]) -> StoredObject {
+        match vault
+            .put_reader(Cursor::new(bytes), budget())
+            .expect("publish test object")
+        {
+            PutOutcome::Stored(stored) => stored,
+            PutOutcome::QuotaRejected { .. } => panic!("test publication unexpectedly rejected"),
+        }
+    }
+
+    fn inventory_for(objects: &[StoredObject]) -> VaultInventory {
+        VaultInventory::try_from_entries(objects.iter().map(|object| VaultInventoryEntry {
+            digest: object.digest,
+            expected_byte_length: object.byte_length,
+            vault_key: object.vault_key.clone(),
+        }))
+        .expect("construct test inventory")
+    }
+
+    fn indexed_digest(index: u64) -> Sha256Digest {
+        let mut bytes = [0_u8; 32];
+        bytes[..8].copy_from_slice(&index.to_be_bytes());
+        Sha256Digest::from_bytes(bytes)
+    }
+
+    #[test]
+    fn accounted_metadata_accepts_exact_object_cap_and_rejects_cap_plus_one() {
+        let inventory = VaultInventory::try_from_entries([]).expect("empty inventory");
+        let allowance = VaultOrphanBudget::new(0).expect("zero orphan allowance");
+        let mut accounting = VaultMetadataAccounting::new(&inventory, allowance)
+            .expect("create metadata accounting");
+        for index in 0..MAX_FINAL_OBJECTS {
+            let digest = indexed_digest(index as u64);
+            accounting
+                .observe(digest, 0, &Vault::object_key(digest))
+                .expect("accept object through exact cap");
+        }
+        let result = accounting.finish().expect("finish exact-cap accounting");
+        assert_eq!(result.final_object_count, MAX_FINAL_OBJECTS);
+        assert_eq!(result.physical_bytes, 0);
+        assert_eq!(result.orphan_remaining_bytes, 0);
+
+        let mut overflow = VaultMetadataAccounting::new(&inventory, allowance)
+            .expect("create overflow accounting");
+        for index in 0..=MAX_FINAL_OBJECTS {
+            let digest = indexed_digest(index as u64);
+            let observed = overflow.observe(digest, 0, &Vault::object_key(digest));
+            if index == MAX_FINAL_OBJECTS {
+                assert!(matches!(observed, Err(HeleosError::ResourceLimit)));
+            } else {
+                observed.expect("accept object below cap");
+            }
+        }
+    }
+
+    #[test]
+    fn accounted_metadata_binds_inventory_key_length_presence_and_orphan_bytes() {
+        let bound = indexed_digest(1);
+        let orphan = indexed_digest(2);
+        let inventory = VaultInventory::try_from_entries([VaultInventoryEntry {
+            digest: bound,
+            expected_byte_length: 11,
+            vault_key: Vault::object_key(bound),
+        }])
+        .expect("construct inventory");
+        let allowance = VaultOrphanBudget::new(13).expect("construct orphan allowance");
+        let mut accounting = VaultMetadataAccounting::new(&inventory, allowance)
+            .expect("create metadata accounting");
+        accounting
+            .observe(bound, 11, &Vault::object_key(bound))
+            .expect("observe bound object");
+        accounting
+            .observe(orphan, 13, &Vault::object_key(orphan))
+            .expect("observe orphan object");
+        let result = accounting.finish().expect("finish exact accounting");
+        assert_eq!(result.physical_bytes, 24);
+        assert_eq!(result.orphan_remaining_bytes, 0);
+
+        let missing = VaultMetadataAccounting::new(&inventory, allowance)
+            .expect("create missing accounting")
+            .finish();
+        assert!(matches!(missing, Err(HeleosError::Integrity)));
+
+        let mut wrong_length = VaultMetadataAccounting::new(&inventory, allowance)
+            .expect("create wrong-length accounting");
+        assert!(matches!(
+            wrong_length.observe(bound, 12, &Vault::object_key(bound)),
+            Err(HeleosError::Integrity)
+        ));
+
+        let wrong_key = VaultInventory::try_from_entries([VaultInventoryEntry {
+            digest: bound,
+            expected_byte_length: 11,
+            vault_key: Vault::object_key(orphan),
+        }])
+        .expect("construct wrong-key inventory");
+        assert!(matches!(
+            VaultMetadataAccounting::new(&wrong_key, allowance),
+            Err(HeleosError::Integrity)
+        ));
+    }
+
+    #[test]
+    fn accounted_scan_binds_inventory_and_does_not_hash_unrelated_orphan_content() {
+        let (_parent, root, vault) = private_test_vault("heleos-vault-accounted-scan-");
+        let bound = store_bytes(&vault, b"bound authority");
+        let orphan = store_bytes(&vault, b"unrelated orphan");
+        let orphan_path = root.join(&orphan.vault_key);
+        std::fs::write(&orphan_path, b"corrupted-orphan").expect("corrupt orphan at equal length");
+        assert_eq!(
+            std::fs::metadata(&orphan_path)
+                .expect("read corrupt orphan metadata")
+                .len(),
+            orphan.byte_length
+        );
+        let request = b"accounted request";
+        let outcome = vault
+            .put_reader_accounted(
+                Cursor::new(request),
+                budget(),
+                &inventory_for(std::slice::from_ref(&bound)),
+                VaultOrphanBudget::new(orphan.byte_length + request.len() as u64)
+                    .expect("construct exact orphan budget"),
+            )
+            .expect("publish without hashing unrelated orphan");
+        assert!(matches!(
+            outcome,
+            PutOutcome::Stored(StoredObject {
+                byte_length,
+                newly_published: true,
+                ..
+            }) if byte_length == request.len() as u64
+        ));
+    }
+
+    #[test]
+    fn accounted_scan_rejects_missing_or_wrong_inventory_before_reading_input() {
+        struct PanicReader;
+
+        impl Read for PanicReader {
+            fn read(&mut self, _: &mut [u8]) -> io::Result<usize> {
+                panic!("accounted scan must reject before reading the input")
+            }
+        }
+
+        let (_parent, _root, vault) = private_test_vault("heleos-vault-accounted-authority-");
+        let stored = store_bytes(&vault, b"inventory authority");
+        let missing_digest = indexed_digest(91);
+        let missing = VaultInventory::try_from_entries([VaultInventoryEntry {
+            digest: missing_digest,
+            expected_byte_length: 1,
+            vault_key: Vault::object_key(missing_digest),
+        }])
+        .expect("construct missing inventory");
+        assert!(matches!(
+            vault.put_reader_accounted(
+                PanicReader,
+                budget(),
+                &missing,
+                VaultOrphanBudget::new(stored.byte_length).expect("construct orphan budget"),
+            ),
+            Err(HeleosError::Integrity)
+        ));
+
+        let wrong_key = VaultInventory::try_from_entries([VaultInventoryEntry {
+            digest: stored.digest,
+            expected_byte_length: stored.byte_length,
+            vault_key: Vault::object_key(missing_digest),
+        }])
+        .expect("construct wrong-key inventory");
+        assert!(matches!(
+            vault.put_reader_accounted(
+                PanicReader,
+                budget(),
+                &wrong_key,
+                VaultOrphanBudget::new(0).expect("construct zero orphan budget"),
+            ),
+            Err(HeleosError::Integrity)
+        ));
+
+        let wrong_length = VaultInventory::try_from_entries([VaultInventoryEntry {
+            digest: stored.digest,
+            expected_byte_length: stored.byte_length + 1,
+            vault_key: stored.vault_key.clone(),
+        }])
+        .expect("construct wrong-length inventory");
+        assert!(matches!(
+            vault.put_reader_accounted(
+                PanicReader,
+                budget(),
+                &wrong_length,
+                VaultOrphanBudget::new(0).expect("construct zero orphan budget"),
+            ),
+            Err(HeleosError::Integrity)
+        ));
+    }
+
+    #[test]
+    fn accounted_orphan_boundary_reuses_current_digest_and_rejects_novel_without_residue() {
+        let (_parent, root, vault) = private_test_vault("heleos-vault-accounted-orphan-");
+        let bytes = b"current orphan";
+        let orphan = store_bytes(&vault, bytes);
+        let empty = VaultInventory::try_from_entries([]).expect("empty inventory");
+        let exact = VaultOrphanBudget::new(orphan.byte_length).expect("exact orphan budget");
+
+        let duplicate = vault
+            .put_reader_accounted(Cursor::new(bytes), budget(), &empty, exact)
+            .expect("reuse current orphan at zero remaining headroom");
+        assert!(matches!(
+            duplicate,
+            PutOutcome::Stored(StoredObject {
+                digest,
+                byte_length,
+                newly_published: false,
+                ..
+            }) if digest == orphan.digest && byte_length == orphan.byte_length
+        ));
+
+        let novel = b"different data";
+        assert_eq!(novel.len(), bytes.len());
+        let rejected = vault
+            .put_reader_accounted(Cursor::new(novel), budget(), &empty, exact)
+            .expect("return bounded quota evidence");
+        let novel_digest = Sha256Digest::hash_reader(Cursor::new(novel)).expect("hash novel bytes");
+        assert!(matches!(
+            rejected,
+            PutOutcome::QuotaRejected {
+                digest,
+                byte_length
+            } if digest == novel_digest && byte_length == novel.len() as u64
+        ));
+        assert!(!root.join(Vault::object_key(novel_digest)).exists());
+        assert!(partial_names(&root).is_empty());
+
+        assert!(matches!(
+            vault.put_reader_accounted(
+                Cursor::new(bytes),
+                budget(),
+                &empty,
+                VaultOrphanBudget::new(orphan.byte_length - 1).expect("smaller orphan budget"),
+            ),
+            Err(HeleosError::Integrity)
+        ));
+    }
+
+    #[test]
+    fn accounted_current_orphan_winner_content_or_length_corruption_is_integrity() {
+        for length_mismatch in [false, true] {
+            let (_parent, root, vault) =
+                private_test_vault("heleos-vault-accounted-corrupt-winner-");
+            let bytes = b"current winner";
+            let orphan = store_bytes(&vault, bytes);
+            let path = root.join(&orphan.vault_key);
+            if length_mismatch {
+                let file = std::fs::OpenOptions::new()
+                    .write(true)
+                    .open(&path)
+                    .expect("open winner to change length");
+                file.set_len(orphan.byte_length + 1)
+                    .expect("change winner length");
+            } else {
+                std::fs::write(&path, b"corrupt winner").expect("corrupt winner at equal length");
+            }
+            let allowance = std::fs::metadata(&path)
+                .expect("read hostile winner metadata")
+                .len();
+            assert!(matches!(
+                vault.put_reader_accounted(
+                    Cursor::new(bytes),
+                    budget(),
+                    &VaultInventory::try_from_entries([]).expect("empty inventory"),
+                    VaultOrphanBudget::new(allowance).expect("cover hostile orphan length"),
+                ),
+                Err(HeleosError::Integrity)
+            ));
+            assert!(partial_names(&root).is_empty());
+        }
+    }
+
+    #[test]
+    fn accounted_metadata_checks_object_physical_and_orphan_caps() {
+        assert!(matches!(
+            VaultOrphanBudget::new(FOUNDATION_MAX_ORPHAN_BYTES + 1),
+            Err(HeleosError::Integrity)
+        ));
+
+        let entries: Vec<_> = (0_u64..=2_000)
+            .map(|index| {
+                let digest = indexed_digest(index);
+                VaultInventoryEntry {
+                    digest,
+                    expected_byte_length: FOUNDATION_MAX_INPUT_BYTES,
+                    vault_key: Vault::object_key(digest),
+                }
+            })
+            .collect();
+        let exact_inventory = VaultInventory::try_from_entries(entries.iter().take(2_000).cloned())
+            .expect("construct exact-cap inventory");
+        let mut exact = VaultMetadataAccounting::new(
+            &exact_inventory,
+            VaultOrphanBudget::new(0).expect("zero orphan budget"),
+        )
+        .expect("create physical accounting");
+        for index in 0_u64..2_000 {
+            let digest = indexed_digest(index);
+            exact
+                .observe(
+                    digest,
+                    FOUNDATION_MAX_INPUT_BYTES,
+                    &Vault::object_key(digest),
+                )
+                .expect("accept through exact physical cap");
+        }
+        let exact_totals = exact.finish().expect("finish exact physical accounting");
+        assert_eq!(exact_totals.physical_bytes, FOUNDATION_MAX_STORE_BYTES);
+
+        let overflow_inventory =
+            VaultInventory::try_from_entries(entries).expect("construct overflow inventory");
+        let mut overflow = VaultMetadataAccounting::new(
+            &overflow_inventory,
+            VaultOrphanBudget::new(0).expect("zero orphan budget"),
+        )
+        .expect("create overflow accounting");
+        for index in 0_u64..2_000 {
+            let digest = indexed_digest(index);
+            overflow
+                .observe(
+                    digest,
+                    FOUNDATION_MAX_INPUT_BYTES,
+                    &Vault::object_key(digest),
+                )
+                .expect("accept through exact physical cap");
+        }
+        let overflow_digest = indexed_digest(2_000);
+        assert!(matches!(
+            overflow.observe(
+                overflow_digest,
+                FOUNDATION_MAX_INPUT_BYTES,
+                &Vault::object_key(overflow_digest),
+            ),
+            Err(HeleosError::Integrity)
+        ));
+
+        let oversize_digest = indexed_digest(9_000);
+        let oversize_inventory = VaultInventory::try_from_entries([VaultInventoryEntry {
+            digest: oversize_digest,
+            expected_byte_length: FOUNDATION_MAX_INPUT_BYTES + 1,
+            vault_key: Vault::object_key(oversize_digest),
+        }])
+        .expect("construct oversize inventory");
+        assert!(matches!(
+            VaultMetadataAccounting::new(
+                &oversize_inventory,
+                VaultOrphanBudget::new(0).expect("zero orphan budget"),
+            ),
+            Err(HeleosError::Integrity)
+        ));
+    }
+
+    #[test]
+    fn accounted_scan_rejects_oversize_and_invalid_layout_before_reading_input() {
+        struct PanicReader;
+
+        impl Read for PanicReader {
+            fn read(&mut self, _: &mut [u8]) -> io::Result<usize> {
+                panic!("invalid accounted layout must reject before input")
+            }
+        }
+
+        let (_parent, _root, vault) = private_test_vault("heleos-vault-accounted-oversize-");
+        let digest = indexed_digest(77);
+        let (first, second, name) = digest_components(digest);
+        let first = open_or_create_directory(&vault.sha256_dir, OsStr::new(&first))
+            .expect("create first shard");
+        let second =
+            open_or_create_directory(&first.dir, OsStr::new(&second)).expect("create second shard");
+        let oversized = create_staging_file(&second.dir, OsStr::new(&name))
+            .map_err(StagingCreateError::into_error)
+            .expect("create oversized final")
+            .file;
+        oversized
+            .set_len(FOUNDATION_MAX_INPUT_BYTES + 1)
+            .expect("make sparse oversized final");
+        drop(oversized);
+        assert!(matches!(
+            vault.put_reader_accounted(
+                PanicReader,
+                budget(),
+                &VaultInventory::try_from_entries([]).expect("empty inventory"),
+                VaultOrphanBudget::new(0).expect("zero orphan budget"),
+            ),
+            Err(HeleosError::ResourceLimit)
+        ));
+
+        let (_parent, root, vault) = private_test_vault("heleos-vault-accounted-layout-");
+        std::fs::create_dir(root.join("objects/sha256/zz")).expect("create invalid shard");
+        assert!(matches!(
+            vault.put_reader_accounted(
+                PanicReader,
+                budget(),
+                &VaultInventory::try_from_entries([]).expect("empty inventory"),
+                VaultOrphanBudget::new(0).expect("zero orphan budget"),
+            ),
+            Err(HeleosError::PolicyDenied)
+        ));
+
+        let (_parent, root, vault) = private_test_vault("heleos-vault-accounted-type-");
+        let object = store_bytes(&vault, b"type substitution");
+        let path = root.join(&object.vault_key);
+        std::fs::remove_file(&path).expect("remove final before type substitution");
+        std::fs::create_dir(&path).expect("substitute final directory");
+        crate::apply_private_permissions(&path).expect("harden substituted directory");
+        assert!(matches!(
+            vault.put_reader_accounted(
+                PanicReader,
+                budget(),
+                &inventory_for(std::slice::from_ref(&object)),
+                VaultOrphanBudget::new(0).expect("zero orphan budget"),
+            ),
+            Err(HeleosError::PolicyDenied)
+        ));
+    }
+
+    #[cfg(unix)]
+    #[test]
+    fn accounted_scan_rejects_permission_symlink_and_link_count_substitution() {
+        use std::os::unix::fs::{PermissionsExt, symlink};
+
+        let (_parent, root, vault) = private_test_vault("heleos-vault-accounted-permission-");
+        let object = store_bytes(&vault, b"permission object");
+        let path = root.join(&object.vault_key);
+        std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o644))
+            .expect("make object permissive");
+        assert!(matches!(
+            vault.put_reader_accounted(
+                Cursor::new(b"request"),
+                budget(),
+                &inventory_for(std::slice::from_ref(&object)),
+                VaultOrphanBudget::new(0).expect("zero orphan budget"),
+            ),
+            Err(HeleosError::PolicyDenied)
+        ));
+
+        let (_parent, root, vault) = private_test_vault("heleos-vault-accounted-symlink-");
+        let object = store_bytes(&vault, b"symlink object");
+        let path = root.join(&object.vault_key);
+        std::fs::remove_file(&path).expect("remove final before symlink");
+        symlink(root.join(".vault.lock"), &path).expect("substitute final symlink");
+        assert!(matches!(
+            vault.put_reader_accounted(
+                Cursor::new(b"request"),
+                budget(),
+                &inventory_for(std::slice::from_ref(&object)),
+                VaultOrphanBudget::new(0).expect("zero orphan budget"),
+            ),
+            Err(HeleosError::PolicyDenied)
+        ));
+
+        let (parent, root, vault) = private_test_vault("heleos-vault-accounted-nlink-");
+        let object = store_bytes(&vault, b"linked object");
+        let path = root.join(&object.vault_key);
+        std::fs::hard_link(&path, parent.path().join("extra-object-link"))
+            .expect("add hostile hard link");
+        assert!(matches!(
+            vault.put_reader_accounted(
+                Cursor::new(b"request"),
+                budget(),
+                &inventory_for(std::slice::from_ref(&object)),
+                VaultOrphanBudget::new(0).expect("zero orphan budget"),
+            ),
+            Err(HeleosError::PolicyDenied)
+        ));
+    }
+
+    #[test]
+    fn accounted_object_cap_allows_exact_duplicate_but_rejects_novel() {
+        let (_parent, _root, vault) = private_test_vault("heleos-vault-accounted-count-");
+        let bytes = b"existing at object cap";
+        let existing = store_bytes(&vault, bytes);
+        assert!(matches!(
+            vault
+                .duplicate_or_quota(
+                    existing.digest,
+                    existing.byte_length,
+                    Some(MAX_FINAL_OBJECTS)
+                )
+                .expect("duplicate remains allowed at object cap"),
+            PutOutcome::Stored(StoredObject {
+                newly_published: false,
+                ..
+            })
+        ));
+        assert!(matches!(
+            vault.duplicate_or_quota(indexed_digest(99), 1, Some(MAX_FINAL_OBJECTS)),
+            Err(HeleosError::ResourceLimit)
+        ));
+    }
+
+    #[test]
+    fn accounted_same_root_instances_serialize_exact_orphan_headroom() {
+        fn run(same_digest: bool) {
+            let (_parent, root, first) = private_test_vault("heleos-vault-accounted-race-");
+            let second = Vault::open(VaultConfig {
+                root,
+                open_mode: VaultOpenMode::ExistingOnly,
+            })
+            .expect("open second same-root instance");
+            let left = b"accounted-race-A";
+            let right = if same_digest {
+                left.as_slice()
+            } else {
+                b"accounted-race-B".as_slice()
+            };
+            let start = std::sync::Barrier::new(3);
+            let empty = VaultInventory::try_from_entries([]).expect("empty inventory");
+            let orphan_budget =
+                VaultOrphanBudget::new(left.len() as u64).expect("exact race budget");
+            let (left_outcome, right_outcome) = std::thread::scope(|scope| {
+                let left_thread = scope.spawn(|| {
+                    start.wait();
+                    first.put_reader_accounted(Cursor::new(left), budget(), &empty, orphan_budget)
+                });
+                let right_thread = scope.spawn(|| {
+                    start.wait();
+                    second.put_reader_accounted(Cursor::new(right), budget(), &empty, orphan_budget)
+                });
+                start.wait();
+                (
+                    left_thread.join().expect("join first writer"),
+                    right_thread.join().expect("join second writer"),
+                )
+            });
+            let outcomes = [left_outcome, right_outcome];
+            let newly_published = outcomes
+                .iter()
+                .filter(|outcome| {
+                    matches!(
+                        outcome,
+                        Ok(PutOutcome::Stored(StoredObject {
+                            newly_published: true,
+                            ..
+                        }))
+                    )
+                })
+                .count();
+            let duplicates = outcomes
+                .iter()
+                .filter(|outcome| {
+                    matches!(
+                        outcome,
+                        Ok(PutOutcome::Stored(StoredObject {
+                            newly_published: false,
+                            ..
+                        }))
+                    )
+                })
+                .count();
+            let rejected = outcomes
+                .iter()
+                .filter(|outcome| matches!(outcome, Ok(PutOutcome::QuotaRejected { .. })))
+                .count();
+            assert_eq!(newly_published, 1);
+            assert_eq!(duplicates, usize::from(same_digest));
+            assert_eq!(rejected, usize::from(!same_digest));
+        }
+
+        run(false);
+        run(true);
+    }
+
+    #[test]
+    fn accounted_process_race_helper() {
+        let Some(root) = std::env::var_os("HELEOS_PRIVATE_ACCOUNTED_RACE_ROOT") else {
+            return;
+        };
+        let payload = match std::env::var("HELEOS_PRIVATE_ACCOUNTED_RACE_PAYLOAD").as_deref() {
+            Ok("a") => b"accounted-race-A".as_slice(),
+            Ok("b") => b"accounted-race-B".as_slice(),
+            _ => panic!("invalid accounted-race payload"),
+        };
+        let ready = PathBuf::from(
+            std::env::var_os("HELEOS_PRIVATE_ACCOUNTED_RACE_READY")
+                .expect("accounted-race ready path"),
+        );
+        let start = PathBuf::from(
+            std::env::var_os("HELEOS_PRIVATE_ACCOUNTED_RACE_START")
+                .expect("accounted-race start path"),
+        );
+        let result = PathBuf::from(
+            std::env::var_os("HELEOS_PRIVATE_ACCOUNTED_RACE_RESULT")
+                .expect("accounted-race result path"),
+        );
+        let vault = Vault::open(VaultConfig {
+            root: PathBuf::from(root),
+            open_mode: VaultOpenMode::ExistingOnly,
+        })
+        .expect("open accounted-race vault");
+        std::fs::write(&ready, b"ready").expect("publish accounted-race readiness");
+        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(30);
+        while !start.exists() {
+            assert!(
+                std::time::Instant::now() < deadline,
+                "timed out waiting for accounted-race start"
+            );
+            std::thread::sleep(std::time::Duration::from_millis(1));
+        }
+        let empty = VaultInventory::try_from_entries([]).expect("empty inventory");
+        let outcome = vault
+            .put_reader_accounted(
+                Cursor::new(payload),
+                budget(),
+                &empty,
+                VaultOrphanBudget::new(payload.len() as u64).expect("exact process race budget"),
+            )
+            .expect("complete accounted process race");
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
+        std::fs::write(result, label).expect("publish accounted-race result");
+    }
+
+    #[test]
+    fn independent_processes_serialize_accounted_scan_and_publication() {
+        fn run(payloads: [&str; 2], expected: [&str; 2]) {
+            let (_parent, root, _vault) = private_test_vault("heleos-vault-accounted-process-");
+            let control = root.parent().expect("vault parent");
+            let start = control.join("accounted-start");
+            let ready = [
+                control.join("accounted-ready-a"),
+                control.join("accounted-ready-b"),
+            ];
+            let result = [
+                control.join("accounted-result-a"),
+                control.join("accounted-result-b"),
+            ];
+            let mut children: Vec<_> = (0..2)
+                .map(|index| {
+                    TestChildGuard(Some(
+                        std::process::Command::new(
+                            std::env::current_exe().expect("locate library test executable"),
+                        )
+                        .arg("--exact")
+                        .arg("vault::tests::accounted_process_race_helper")
+                        .arg("--nocapture")
+                        .env("HELEOS_PRIVATE_ACCOUNTED_RACE_ROOT", &root)
+                        .env("HELEOS_PRIVATE_ACCOUNTED_RACE_PAYLOAD", payloads[index])
+                        .env("HELEOS_PRIVATE_ACCOUNTED_RACE_READY", &ready[index])
+                        .env("HELEOS_PRIVATE_ACCOUNTED_RACE_START", &start)
+                        .env("HELEOS_PRIVATE_ACCOUNTED_RACE_RESULT", &result[index])
+                        .spawn()
+                        .expect("spawn accounted-race child"),
+                    ))
+                })
+                .collect();
+            let deadline = std::time::Instant::now() + std::time::Duration::from_secs(30);
+            while ready.iter().any(|path| !path.exists()) {
+                assert!(
+                    std::time::Instant::now() < deadline,
+                    "accounted-race children did not become ready"
+                );
+                std::thread::sleep(std::time::Duration::from_millis(1));
+            }
+            std::fs::write(&start, b"start").expect("release accounted-race children");
+            for child in &mut children {
+                assert!(
+                    child
+                        .wait()
+                        .expect("wait for accounted-race child")
+                        .success()
+                );
+            }
+            let mut observed: Vec<_> = result
+                .iter()
+                .map(|path| std::fs::read_to_string(path).expect("read accounted-race result"))
+                .collect();
+            observed.sort();
+            let mut expected = expected.map(str::to_owned).to_vec();
+            expected.sort();
+            assert_eq!(observed, expected);
+        }
+
+        run(["a", "b"], ["stored-new", "quota-rejected"]);
+        run(["a", "a"], ["stored-new", "stored-duplicate"]);
+    }
+
     #[test]
     fn same_root_instance_cannot_enter_verification_while_writer_holds_exclusive_lock() {
         let (_parent, root, writer) = private_test_vault("heleos-vault-thread-lock-boundary-");
         let reader = Vault::open(VaultConfig {
             root: root.clone(),
             open_mode: VaultOpenMode::ExistingOnly,
         })
         .expect("open second same-root vault instance");
         let bytes = b"thread lock boundary";
         let expected = Sha256Digest::hash_reader(Cursor::new(bytes)).expect("hash bytes");
