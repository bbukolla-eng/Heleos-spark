//! Encrypted backup and staged restore contracts.

use std::fs::{self, OpenOptions};
use std::path::{Path, PathBuf};
use std::thread;
use std::time::{Duration, Instant};

use rusqlite::backup::{Backup, StepResult};
use rusqlite::{Connection, OpenFlags, Transaction, TransactionBehavior};

use crate::{
    HeleosError, Result, Store, VaultInventory, apply_private_permissions,
    verify_private_permissions,
};

#[cfg_attr(
    not(test),
    expect(
        dead_code,
        reason = "private container primitive is wired into BackupService::create in the next reviewed slice"
    )
)]
mod archive;
mod manifest;

const DATABASE_SNAPSHOT_BYTES_MAX: u64 = 16 * 1024 * 1024 * 1024;
const DATABASE_SNAPSHOT_PAGES_PER_STEP: i32 = 16;
const DATABASE_SNAPSHOT_BUSY_RETRIES_MAX: u32 = 128;
const DATABASE_SNAPSHOT_BUSY_RETRY_PAUSE: Duration = Duration::from_millis(5);
const DATABASE_SNAPSHOT_BUSY_DEADLINE: Duration = Duration::from_secs(5);

#[cfg_attr(
    not(test),
    expect(
        dead_code,
        reason = "private capacity arithmetic is wired into verification and restore in the next reviewed slice"
    )
)]
mod capacity {
    use heleos_platform_fs::{RetainedVolumeRelation, RetainedVolumeRelationError};

    use crate::{HeleosError, Result, Sha256Digest};

    const MEBIBYTE: u64 = 1024 * 1024;
    const GIBIBYTE: u64 = 1024 * MEBIBYTE;
    const MINIMUM_FREE_RESERVE_BYTES: u64 = 20 * GIBIBYTE;
    const STORE_RECOVERY_ALLOWANCE_BYTES: u64 = GIBIBYTE;
    const MAXIMUM_CIPHERTEXT_BYTES: u64 = 522 * GIBIBYTE;
    const MAXIMUM_DECODED_BYTES: u64 = 520 * GIBIBYTE;
    const MAXIMUM_DATABASE_BYTES: u64 = 16 * GIBIBYTE;
    const MAXIMUM_BLOB_BYTES: u64 = 256 * MEBIBYTE;
    const MAXIMUM_BLOB_COUNT: u64 = 2_000_000;
    const MAXIMUM_DECODED_PAYLOAD_FILE_COUNT: u64 = MAXIMUM_BLOB_COUNT + 1;
    const FIXED_RESTORE_DIRECTORY_COUNT: u64 = 5;
    const MAXIMUM_FIRST_LEVEL_SHARD_COUNT: u64 = 256;
    const MAXIMUM_SECOND_LEVEL_SHARD_COUNT: u64 = 65_536;
    const MAXIMUM_RESTORE_DIRECTORY_COUNT: u64 = FIXED_RESTORE_DIRECTORY_COUNT
        + MAXIMUM_FIRST_LEVEL_SHARD_COUNT
        + MAXIMUM_SECOND_LEVEL_SHARD_COUNT;

    #[derive(Clone, Copy, Debug, Eq, PartialEq)]
    pub(super) struct AllocationGranularity(u64);

    impl AllocationGranularity {
        pub(super) fn new(bytes: u64) -> Result<Self> {
            if bytes == 0 {
                return Err(HeleosError::PolicyDenied);
            }
            Ok(Self(bytes))
        }

        pub(super) fn round_up(self, byte_length: u64) -> Result<u64> {
            let remainder = byte_length % self.0;
            if remainder == 0 {
                return Ok(byte_length);
            }
            byte_length
                .checked_add(self.0 - remainder)
                .ok_or(HeleosError::ResourceLimit)
        }

        pub(super) fn file(self, byte_length: u64) -> Result<u64> {
            self.0
                .max(self.round_up(byte_length)?)
                .checked_add(self.0)
                .ok_or(HeleosError::ResourceLimit)
        }

        pub(super) fn directory(self) -> Result<u64> {
            self.0.checked_add(self.0).ok_or(HeleosError::ResourceLimit)
        }
    }

    pub(super) fn normal_capacity_reserve(total_space: u64) -> Result<u64> {
        if total_space == 0 {
            return Err(HeleosError::PolicyDenied);
        }
        let ten_percent_ceiling = total_space
            .checked_div(10)
            .and_then(|quotient| quotient.checked_add(u64::from(!total_space.is_multiple_of(10))))
            .ok_or(HeleosError::ResourceLimit)?;
        Ok(MINIMUM_FREE_RESERVE_BYTES.max(ten_percent_ceiling))
    }

    pub(super) fn predecrypt_capacity_required(
        granularity: AllocationGranularity,
        ciphertext_byte_length: u64,
        total_space: u64,
    ) -> Result<u64> {
        if ciphertext_byte_length > MAXIMUM_CIPHERTEXT_BYTES {
            return Err(HeleosError::ResourceLimit);
        }
        let reserve = normal_capacity_reserve(total_space)?;
        let plaintext_file = capacity_stat_math(granularity.file(ciphertext_byte_length))?;
        let possible_decoded_bodies =
            capacity_stat_math(granularity.round_up(ciphertext_byte_length))?;
        let possible_decoded_minimum_bodies_and_names = MAXIMUM_DECODED_PAYLOAD_FILE_COUNT
            .checked_mul(2)
            .and_then(|count| count.checked_mul(granularity.0))
            .ok_or(HeleosError::PolicyDenied)?;
        let generated_locks = capacity_stat_math(granularity.file(0))?
            .checked_mul(2)
            .ok_or(HeleosError::PolicyDenied)?;
        let maximum_directories = capacity_stat_math(granularity.directory())?
            .checked_mul(MAXIMUM_RESTORE_DIRECTORY_COUNT)
            .ok_or(HeleosError::PolicyDenied)?;

        plaintext_file
            .checked_add(possible_decoded_bodies)
            .and_then(|total| total.checked_add(possible_decoded_minimum_bodies_and_names))
            .and_then(|total| total.checked_add(generated_locks))
            .and_then(|total| total.checked_add(maximum_directories))
            .and_then(|total| total.checked_add(reserve))
            .ok_or(HeleosError::PolicyDenied)
    }

    #[derive(Debug, Eq, PartialEq)]
    pub(super) struct ExactRestoreWork {
        staging_granularity: AllocationGranularity,
        decoded_tree: u64,
        database_byte_length: u64,
        blob_count: u64,
        first_level_shard_count: u64,
        second_level_shard_count: u64,
    }

    impl ExactRestoreWork {
        pub(super) const fn decoded_tree(&self) -> u64 {
            self.decoded_tree
        }

        pub(super) const fn database_byte_length(&self) -> u64 {
            self.database_byte_length
        }

        pub(super) const fn blob_count(&self) -> u64 {
            self.blob_count
        }

        pub(super) const fn first_level_shard_count(&self) -> u64 {
            self.first_level_shard_count
        }

        pub(super) const fn second_level_shard_count(&self) -> u64 {
            self.second_level_shard_count
        }
    }

    pub(super) fn exact_restore_work<I>(
        granularity: AllocationGranularity,
        database_byte_length: u64,
        mut authenticated_blob_records: I,
    ) -> Result<ExactRestoreWork>
    where
        I: ExactSizeIterator<Item = (Sha256Digest, u64)>,
    {
        if database_byte_length > MAXIMUM_DATABASE_BYTES {
            return Err(HeleosError::ResourceLimit);
        }
        let expected_blob_count = u64::try_from(authenticated_blob_records.len())
            .map_err(|_| HeleosError::ResourceLimit)?;
        if expected_blob_count > MAXIMUM_BLOB_COUNT {
            return Err(HeleosError::ResourceLimit);
        }

        let mut decoded_payload_byte_length = database_byte_length;
        let mut files = capacity_stat_math(granularity.file(database_byte_length))?;
        files = files
            .checked_add(
                capacity_stat_math(granularity.file(0))?
                    .checked_mul(2)
                    .ok_or(HeleosError::PolicyDenied)?,
            )
            .ok_or(HeleosError::PolicyDenied)?;
        let mut blob_count = 0_u64;
        let mut first_level_shard_count = 0_u64;
        let mut second_level_shard_count = 0_u64;
        let mut previous_digest = None;

        for (digest, byte_length) in &mut authenticated_blob_records {
            blob_count = blob_count
                .checked_add(1)
                .ok_or(HeleosError::ResourceLimit)?;
            if blob_count > MAXIMUM_BLOB_COUNT {
                return Err(HeleosError::ResourceLimit);
            }
            if blob_count > expected_blob_count {
                return Err(HeleosError::InvalidBackupContainer);
            }
            if byte_length > MAXIMUM_BLOB_BYTES {
                return Err(HeleosError::ResourceLimit);
            }
            decoded_payload_byte_length = decoded_payload_byte_length
                .checked_add(byte_length)
                .ok_or(HeleosError::ResourceLimit)?;
            if decoded_payload_byte_length > MAXIMUM_DECODED_BYTES {
                return Err(HeleosError::ResourceLimit);
            }

            if let Some(previous) = previous_digest {
                if digest <= previous {
                    return Err(HeleosError::InvalidBackupContainer);
                }
                if digest.as_bytes()[0] != previous.as_bytes()[0] {
                    first_level_shard_count = first_level_shard_count
                        .checked_add(1)
                        .ok_or(HeleosError::ResourceLimit)?;
                }
                if digest.as_bytes()[..2] != previous.as_bytes()[..2] {
                    second_level_shard_count = second_level_shard_count
                        .checked_add(1)
                        .ok_or(HeleosError::ResourceLimit)?;
                }
            } else {
                first_level_shard_count = 1;
                second_level_shard_count = 1;
            }
            previous_digest = Some(digest);

            files = files
                .checked_add(capacity_stat_math(granularity.file(byte_length))?)
                .ok_or(HeleosError::PolicyDenied)?;
        }
        if blob_count != expected_blob_count || authenticated_blob_records.len() != 0 {
            return Err(HeleosError::InvalidBackupContainer);
        }

        let directory_count = FIXED_RESTORE_DIRECTORY_COUNT
            .checked_add(first_level_shard_count)
            .and_then(|count| count.checked_add(second_level_shard_count))
            .ok_or(HeleosError::PolicyDenied)?;
        let directories = capacity_stat_math(granularity.directory())?
            .checked_mul(directory_count)
            .ok_or(HeleosError::PolicyDenied)?;
        let decoded_tree = files
            .checked_add(directories)
            .ok_or(HeleosError::PolicyDenied)?;
        Ok(ExactRestoreWork {
            staging_granularity: granularity,
            decoded_tree,
            database_byte_length,
            blob_count,
            first_level_shard_count,
            second_level_shard_count,
        })
    }

    pub(super) fn store_scratch_capacity(
        granularity: AllocationGranularity,
        database_byte_length: u64,
    ) -> Result<u64> {
        if database_byte_length > MAXIMUM_DATABASE_BYTES {
            return Err(HeleosError::ResourceLimit);
        }
        capacity_stat_math(granularity.file(database_byte_length))?
            .checked_add(capacity_stat_math(granularity.directory())?)
            .and_then(|total| total.checked_add(STORE_RECOVERY_ALLOWANCE_BYTES))
            .ok_or(HeleosError::PolicyDenied)
    }

    #[derive(Clone, Copy, Debug, Eq, PartialEq)]
    pub(super) struct VolumeLayout {
        relation: RetainedVolumeRelation,
        staging_total_space: u64,
        temporary_granularity: AllocationGranularity,
        temporary_total_space: u64,
    }

    impl VolumeLayout {
        pub(super) const fn new(
            relation: RetainedVolumeRelation,
            staging_total_space: u64,
            temporary_granularity: AllocationGranularity,
            temporary_total_space: u64,
        ) -> Self {
            Self {
                relation,
                staging_total_space,
                temporary_granularity,
                temporary_total_space,
            }
        }
    }

    #[derive(Clone, Copy, Debug, Eq, PartialEq)]
    pub(super) struct PostAuthenticationCapacity {
        staging_required: u64,
        temporary_required: Option<u64>,
    }

    impl PostAuthenticationCapacity {
        pub(super) const fn staging_required(self) -> u64 {
            self.staging_required
        }

        pub(super) const fn temporary_required(self) -> Option<u64> {
            self.temporary_required
        }

        pub(super) fn require_available(
            self,
            staging_available: u64,
            temporary_available: Option<u64>,
        ) -> Result<()> {
            if staging_available < self.staging_required {
                return Err(HeleosError::PolicyDenied);
            }
            if let Some(required) = self.temporary_required
                && temporary_available.is_none_or(|available| available < required)
            {
                return Err(HeleosError::PolicyDenied);
            }
            Ok(())
        }
    }

    pub(super) fn post_authentication_capacity(
        work: ExactRestoreWork,
        layout: VolumeLayout,
    ) -> Result<PostAuthenticationCapacity> {
        let ExactRestoreWork {
            staging_granularity,
            decoded_tree,
            database_byte_length,
            ..
        } = work;
        let staging_reserve = normal_capacity_reserve(layout.staging_total_space)?;
        match layout.relation {
            RetainedVolumeRelation::Same => {
                let store_scratch =
                    store_scratch_capacity(staging_granularity, database_byte_length)?;
                remaining_work_capacity_requirements(
                    layout.relation,
                    decoded_tree,
                    store_scratch,
                    staging_reserve,
                    0,
                    0,
                )
            }
            RetainedVolumeRelation::Distinct => {
                let temporary_reserve = normal_capacity_reserve(layout.temporary_total_space)?;
                let store_scratch =
                    store_scratch_capacity(layout.temporary_granularity, database_byte_length)?;
                remaining_work_capacity_requirements(
                    layout.relation,
                    decoded_tree,
                    0,
                    staging_reserve,
                    store_scratch,
                    temporary_reserve,
                )
            }
            RetainedVolumeRelation::Unknown => {
                // Unknown must be safe for both possible underlying layouts:
                // the staging anchor witnesses Same, while the temporary
                // anchor independently witnesses the scratch needed if Distinct.
                let staging_scratch =
                    store_scratch_capacity(staging_granularity, database_byte_length)?;
                let temporary_scratch =
                    store_scratch_capacity(layout.temporary_granularity, database_byte_length)?;
                let temporary_reserve = normal_capacity_reserve(layout.temporary_total_space)?;
                remaining_work_capacity_requirements(
                    layout.relation,
                    decoded_tree,
                    staging_scratch,
                    staging_reserve,
                    temporary_scratch,
                    temporary_reserve,
                )
            }
        }
    }

    pub(super) fn remaining_work_capacity_requirements(
        relation: RetainedVolumeRelation,
        decoded_tree: u64,
        staging_scratch: u64,
        staging_reserve: u64,
        temporary_scratch: u64,
        temporary_reserve: u64,
    ) -> Result<PostAuthenticationCapacity> {
        let staging_required = match relation {
            RetainedVolumeRelation::Same | RetainedVolumeRelation::Unknown => decoded_tree
                .checked_add(staging_scratch)
                .and_then(|total| total.checked_add(staging_reserve)),
            RetainedVolumeRelation::Distinct => decoded_tree.checked_add(staging_reserve),
        }
        .ok_or(HeleosError::PolicyDenied)?;
        let temporary_required = match relation {
            RetainedVolumeRelation::Same => None,
            RetainedVolumeRelation::Distinct | RetainedVolumeRelation::Unknown => Some(
                temporary_scratch
                    .checked_add(temporary_reserve)
                    .ok_or(HeleosError::PolicyDenied)?,
            ),
        };
        Ok(PostAuthenticationCapacity {
            staging_required,
            temporary_required,
        })
    }

    pub(super) fn require_reserve_only_capacity(
        relation: RetainedVolumeRelation,
        staging_available: u64,
        staging_reserve: u64,
        temporary_available: u64,
        temporary_reserve: u64,
    ) -> Result<()> {
        if staging_available < staging_reserve {
            return Err(HeleosError::PolicyDenied);
        }
        if !matches!(relation, RetainedVolumeRelation::Same)
            && temporary_available < temporary_reserve
        {
            return Err(HeleosError::PolicyDenied);
        }
        Ok(())
    }

    pub(super) fn require_stable_retained_volume_relation(
        before: std::result::Result<RetainedVolumeRelation, RetainedVolumeRelationError>,
        after: std::result::Result<RetainedVolumeRelation, RetainedVolumeRelationError>,
    ) -> Result<RetainedVolumeRelation> {
        // Relation is helper-owned opaque evidence. Paths, names, and capacity
        // statistics never participate in this equality gate.
        let before = before.map_err(map_retained_volume_relation_error)?;
        let after = after.map_err(map_retained_volume_relation_error)?;
        if before != after {
            return Err(HeleosError::PolicyDenied);
        }
        Ok(before)
    }

    fn map_retained_volume_relation_error(error: RetainedVolumeRelationError) -> HeleosError {
        // Comparator failures are admission failures, not staging mutations.
        match error {
            RetainedVolumeRelationError::Unsupported | RetainedVolumeRelationError::Io(_) => {
                HeleosError::PolicyDenied
            }
        }
    }

    fn capacity_stat_math<T>(result: Result<T>) -> Result<T> {
        match result {
            Err(HeleosError::ResourceLimit) => Err(HeleosError::PolicyDenied),
            result => result,
        }
    }
}

#[cfg_attr(
    not(test),
    expect(
        dead_code,
        reason = "private staging provenance is wired into exact Store/Vault orchestration sites in the next reviewed slice"
    )
)]
mod staging_error {
    use std::fs::File;
    use std::io::{self, Write};

    use crate::{HeleosError, Result};

    pub(super) struct StagingResourceLimit(());

    impl StagingResourceLimit {
        pub(super) const fn from_staging_helper() -> Self {
            Self(())
        }
    }

    pub(super) enum StagingOrchestrationError {
        StoreOrVault(HeleosError),
        StagingResourceLimit(StagingResourceLimit),
    }

    pub(super) fn classify_direct_staging_io(error: std::io::Error) -> HeleosError {
        if matches!(
            error.kind(),
            std::io::ErrorKind::StorageFull | std::io::ErrorKind::QuotaExceeded
        ) {
            HeleosError::PolicyDenied
        } else {
            HeleosError::Io(error)
        }
    }

    fn classify_windows_directory_sync_result(result: io::Result<()>) -> Result<()> {
        match result {
            Ok(()) => Ok(()),
            Err(error) if matches!(error.raw_os_error(), Some(1 | 5 | 6)) => Ok(()),
            Err(error) => Err(classify_direct_staging_io(error)),
        }
    }

    #[cfg(windows)]
    pub(super) fn sync_staging_directory(directory: &File) -> Result<()> {
        classify_windows_directory_sync_result(directory.sync_all())
    }

    #[cfg(not(windows))]
    pub(super) fn sync_staging_directory(directory: &File) -> Result<()> {
        directory.sync_all().map_err(classify_direct_staging_io)
    }

    #[cfg(test)]
    pub(super) fn classify_windows_directory_sync_result_for_test(
        result: io::Result<()>,
    ) -> Result<()> {
        classify_windows_directory_sync_result(result)
    }

    pub(super) struct StagingMutationWriter<W> {
        inner: W,
        storage_exhausted: bool,
    }

    impl<W> StagingMutationWriter<W> {
        pub(super) const fn new(inner: W) -> Self {
            Self {
                inner,
                storage_exhausted: false,
            }
        }

        pub(super) fn classify_result<T>(&self, result: Result<T>) -> Result<T> {
            if self.storage_exhausted && result.is_err() {
                Err(HeleosError::PolicyDenied)
            } else {
                result
            }
        }
    }

    impl<W: Write> Write for StagingMutationWriter<W> {
        fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
            match self.inner.write(bytes) {
                Ok(written) => Ok(written),
                Err(error) => {
                    self.storage_exhausted |= matches!(
                        error.kind(),
                        io::ErrorKind::StorageFull | io::ErrorKind::QuotaExceeded
                    );
                    Err(error)
                }
            }
        }

        fn flush(&mut self) -> io::Result<()> {
            match self.inner.flush() {
                Ok(()) => Ok(()),
                Err(error) => {
                    self.storage_exhausted |= matches!(
                        error.kind(),
                        io::ErrorKind::StorageFull | io::ErrorKind::QuotaExceeded
                    );
                    Err(error)
                }
            }
        }
    }

    pub(super) fn classify_staging_orchestration_error(
        error: StagingOrchestrationError,
    ) -> HeleosError {
        match error {
            StagingOrchestrationError::StoreOrVault(HeleosError::Io(error)) => {
                classify_direct_staging_io(error)
            }
            StagingOrchestrationError::StoreOrVault(error) => error,
            StagingOrchestrationError::StagingResourceLimit(_) => HeleosError::PolicyDenied,
        }
    }
}

#[cfg_attr(
    not(test),
    expect(
        dead_code,
        reason = "private retained-sibling guards are composed by BackupService in the next reviewed slice"
    )
)]
mod staging_owner {
    use std::ffi::{OsStr, OsString};
    use std::fs::{self, File};
    use std::sync::Arc;

    use cap_fs_ext::{FollowSymlinks, OpenOptionsFollowExt, OpenOptionsMaybeDirExt};
    use cap_std::fs::{Dir as CapDir, OpenOptions as CapOpenOptions};
    use heleos_platform_fs::OneComponentName;

    use super::staging_error::{classify_direct_staging_io, sync_staging_directory};
    use crate::{HeleosError, Result};

    const STAGING_NAME_ATTEMPTS_MAX: usize = 128;

    #[derive(Clone, Copy, Debug, Eq, PartialEq)]
    pub(super) enum StagingOwnershipState {
        Owned,
        Building,
        FullySynced,
        Published,
        Cleaned,
    }

    #[derive(Clone, Copy, Debug, Eq, PartialEq)]
    pub(super) enum OwnedSiblingFilePurpose {
        Plaintext,
        Ciphertext,
    }

    impl OwnedSiblingFilePurpose {
        const fn label(self) -> &'static str {
            match self {
                Self::Plaintext => "plaintext",
                Self::Ciphertext => "ciphertext",
            }
        }
    }

    struct OwnedSiblingName {
        raw: OsString,
        component: OneComponentName,
    }

    impl OwnedSiblingName {
        fn new(raw: OsString) -> Result<Self> {
            let component =
                OneComponentName::try_from(raw.clone()).map_err(|_| HeleosError::PolicyDenied)?;
            Ok(Self { raw, component })
        }

        fn raw(&self) -> &OsStr {
            &self.raw
        }
    }

    struct RetainedSiblingParent {
        retained: File,
        directory: CapDir,
    }

    impl RetainedSiblingParent {
        fn from_borrowed(parent: &File) -> Result<Self> {
            crate::store::verify_private_permissions_on_handle(parent)?;
            if !parent.metadata().map_err(HeleosError::Io)?.is_dir() {
                return Err(HeleosError::PolicyDenied);
            }
            let retained = parent.try_clone().map_err(HeleosError::Io)?;
            let directory = CapDir::from_std_file(parent.try_clone().map_err(HeleosError::Io)?);
            Ok(Self {
                retained,
                directory,
            })
        }

        fn sync(&self) -> Result<()> {
            sync_staging_directory(&self.retained)
        }
    }

    #[cfg(unix)]
    #[derive(Clone, Copy, Debug, Eq, PartialEq)]
    struct StagingIdentity {
        device: u64,
        inode: u64,
    }

    #[cfg(windows)]
    #[derive(Clone, Copy, Debug, Eq, PartialEq)]
    struct StagingIdentity;

    #[cfg(not(any(unix, windows)))]
    #[derive(Clone, Copy, Debug, Eq, PartialEq)]
    struct StagingIdentity;

    #[cfg(unix)]
    fn staging_identity(file: &File) -> Result<StagingIdentity> {
        use std::os::unix::fs::MetadataExt;

        let metadata = file.metadata().map_err(HeleosError::Io)?;
        Ok(StagingIdentity {
            device: metadata.dev(),
            inode: metadata.ino(),
        })
    }

    #[cfg(windows)]
    fn staging_identity(_: &File) -> Result<StagingIdentity> {
        // The retained staged handle is opened without FILE_SHARE_DELETE, so
        // Windows itself prevents removal or rebinding while the guard lives.
        Ok(StagingIdentity)
    }

    #[cfg(not(any(unix, windows)))]
    fn staging_identity(_: &File) -> Result<StagingIdentity> {
        Err(HeleosError::PolicyDenied)
    }

    fn staging_type_identity(file: &File, expect_directory: bool) -> Result<StagingIdentity> {
        let metadata = file.metadata().map_err(HeleosError::Io)?;
        if metadata.file_type().is_symlink()
            || metadata.is_dir() != expect_directory
            || metadata.is_file() == expect_directory
        {
            return Err(HeleosError::PolicyDenied);
        }
        reject_windows_reparse(&metadata)?;
        staging_identity(file)
    }

    fn validate_staging_handle(file: &File, expect_directory: bool) -> Result<StagingIdentity> {
        let identity = staging_type_identity(file, expect_directory)?;
        crate::store::verify_private_permissions_on_handle(file)?;
        Ok(identity)
    }

    #[cfg(windows)]
    fn reject_windows_reparse(metadata: &fs::Metadata) -> Result<()> {
        use std::os::windows::fs::MetadataExt;

        const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x0000_0400;
        if metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
            return Err(HeleosError::PolicyDenied);
        }
        Ok(())
    }

    #[cfg(not(windows))]
    const fn reject_windows_reparse(_: &fs::Metadata) -> Result<()> {
        Ok(())
    }

    fn map_staging_mutation_error(error: HeleosError) -> HeleosError {
        match error {
            HeleosError::Io(error) => classify_direct_staging_io(error),
            error => error,
        }
    }

    fn file_create_options(purpose: OwnedSiblingFilePurpose) -> CapOpenOptions {
        let mut options = CapOpenOptions::new();
        options
            .read(true)
            .write(true)
            .create_new(true)
            .follow(FollowSymlinks::No)
            .maybe_dir(false);
        configure_file_create_options(&mut options, purpose);
        options
    }

    fn binding_file_options() -> CapOpenOptions {
        let mut options = CapOpenOptions::new();
        options
            .read(true)
            .follow(FollowSymlinks::No)
            .maybe_dir(false);
        configure_binding_options(&mut options, false);
        options
    }

    fn binding_directory_options() -> CapOpenOptions {
        let mut options = CapOpenOptions::new();
        options
            .read(true)
            .follow(FollowSymlinks::No)
            .maybe_dir(false);
        configure_binding_options(&mut options, true);
        options
    }

    #[cfg(test)]
    pub(super) fn binding_directory_options_for_test() -> CapOpenOptions {
        binding_directory_options()
    }

    fn directory_apply_options() -> CapOpenOptions {
        let mut options = CapOpenOptions::new();
        options
            .read(true)
            .follow(FollowSymlinks::No)
            .maybe_dir(true);
        configure_directory_apply_options(&mut options);
        options
    }

    fn directory_subject_options() -> CapOpenOptions {
        let mut options = CapOpenOptions::new();
        options
            .read(true)
            .follow(FollowSymlinks::No)
            .maybe_dir(true);
        configure_directory_subject_options(&mut options);
        options
    }

    #[cfg(unix)]
    fn configure_file_create_options(options: &mut CapOpenOptions, _: OwnedSiblingFilePurpose) {
        use cap_std::fs::OpenOptionsExt;

        options
            .mode(0o600)
            .custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK);
    }

    #[cfg(windows)]
    fn configure_file_create_options(
        options: &mut CapOpenOptions,
        purpose: OwnedSiblingFilePurpose,
    ) {
        use cap_std::fs::OpenOptionsExt;

        const GENERIC_READ: u32 = 0x8000_0000;
        const GENERIC_WRITE: u32 = 0x4000_0000;
        const DELETE: u32 = 0x0001_0000;
        const READ_CONTROL: u32 = 0x0002_0000;
        const WRITE_DAC: u32 = 0x0004_0000;
        const FILE_READ_ATTRIBUTES: u32 = 0x0000_0080;
        const FILE_SHARE_READ: u32 = 0x0000_0001;
        const FILE_SHARE_WRITE: u32 = 0x0000_0002;
        const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;

        let delete = if matches!(purpose, OwnedSiblingFilePurpose::Ciphertext) {
            DELETE
        } else {
            0
        };
        options
            .access_mode(
                GENERIC_READ
                    | GENERIC_WRITE
                    | delete
                    | READ_CONTROL
                    | WRITE_DAC
                    | FILE_READ_ATTRIBUTES,
            )
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
            .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT);
    }

    #[cfg(not(any(unix, windows)))]
    fn configure_file_create_options(_: &mut CapOpenOptions, _: OwnedSiblingFilePurpose) {}

    #[cfg(unix)]
    fn configure_binding_options(options: &mut CapOpenOptions, expect_directory: bool) {
        use cap_std::fs::OpenOptionsExt;

        let flags = libc::O_NOFOLLOW
            | libc::O_NONBLOCK
            | if expect_directory {
                libc::O_DIRECTORY
            } else {
                0
            };
        options.custom_flags(flags);
    }

    #[cfg(windows)]
    fn configure_binding_options(options: &mut CapOpenOptions, expect_directory: bool) {
        use cap_std::fs::OpenOptionsExt;

        const GENERIC_READ: u32 = 0x8000_0000;
        const READ_CONTROL: u32 = 0x0002_0000;
        const FILE_READ_ATTRIBUTES: u32 = 0x0000_0080;
        const FILE_SHARE_READ: u32 = 0x0000_0001;
        const FILE_SHARE_WRITE: u32 = 0x0000_0002;
        const FILE_SHARE_DELETE: u32 = 0x0000_0004;
        const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
        const FILE_FLAG_BACKUP_SEMANTICS: u32 = 0x0200_0000;

        options
            .access_mode(GENERIC_READ | READ_CONTROL | FILE_READ_ATTRIBUTES)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE)
            .custom_flags(
                FILE_FLAG_OPEN_REPARSE_POINT
                    | if expect_directory {
                        FILE_FLAG_BACKUP_SEMANTICS
                    } else {
                        0
                    },
            );
    }

    #[cfg(not(any(unix, windows)))]
    fn configure_binding_options(_: &mut CapOpenOptions, _: bool) {}

    #[cfg(unix)]
    fn configure_directory_apply_options(options: &mut CapOpenOptions) {
        use cap_std::fs::OpenOptionsExt;

        options.custom_flags(libc::O_NOFOLLOW | libc::O_NONBLOCK | libc::O_DIRECTORY);
    }

    #[cfg(windows)]
    fn configure_directory_apply_options(options: &mut CapOpenOptions) {
        use cap_std::fs::OpenOptionsExt;

        const GENERIC_READ: u32 = 0x8000_0000;
        const READ_CONTROL: u32 = 0x0002_0000;
        const WRITE_DAC: u32 = 0x0004_0000;
        const FILE_READ_ATTRIBUTES: u32 = 0x0000_0080;
        const FILE_SHARE_READ: u32 = 0x0000_0001;
        const FILE_SHARE_WRITE: u32 = 0x0000_0002;
        const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
        const FILE_FLAG_BACKUP_SEMANTICS: u32 = 0x0200_0000;

        options
            .access_mode(GENERIC_READ | READ_CONTROL | WRITE_DAC | FILE_READ_ATTRIBUTES)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
            .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS);
    }

    #[cfg(not(any(unix, windows)))]
    fn configure_directory_apply_options(_: &mut CapOpenOptions) {}

    #[cfg(unix)]
    fn configure_directory_subject_options(options: &mut CapOpenOptions) {
        configure_directory_apply_options(options);
    }

    #[cfg(windows)]
    fn configure_directory_subject_options(options: &mut CapOpenOptions) {
        use cap_std::fs::OpenOptionsExt;

        const DELETE: u32 = 0x0001_0000;
        const READ_CONTROL: u32 = 0x0002_0000;
        const FILE_READ_ATTRIBUTES: u32 = 0x0000_0080;
        const FILE_SHARE_READ: u32 = 0x0000_0001;
        const FILE_SHARE_WRITE: u32 = 0x0000_0002;
        const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
        const FILE_FLAG_BACKUP_SEMANTICS: u32 = 0x0200_0000;

        options
            .access_mode(DELETE | READ_CONTROL | FILE_READ_ATTRIBUTES)
            .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
            .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS);
    }

    #[cfg(not(any(unix, windows)))]
    fn configure_directory_subject_options(_: &mut CapOpenOptions) {}

    #[derive(Clone, Copy, Debug, Eq, PartialEq)]
    pub(super) enum StagingCleanupEvent {
        HandleClosed,
        EntryRemoved,
        ParentSynced,
    }

    #[cfg(test)]
    type CleanupEvents = std::rc::Rc<std::cell::RefCell<Vec<StagingCleanupEvent>>>;

    pub(super) struct OwnedSiblingFile {
        parent: Arc<RetainedSiblingParent>,
        name: OwnedSiblingName,
        purpose: OwnedSiblingFilePurpose,
        file: Option<File>,
        identity: StagingIdentity,
        state: StagingOwnershipState,
        #[cfg(test)]
        cleanup_events: Option<CleanupEvents>,
    }

    impl OwnedSiblingFile {
        pub(super) fn handle(&self) -> Result<&File> {
            self.file.as_ref().ok_or(HeleosError::Integrity)
        }

        pub(super) fn handle_mut(&mut self) -> Result<&mut File> {
            if self.state != StagingOwnershipState::Owned {
                return Err(HeleosError::Integrity);
            }
            self.file.as_mut().ok_or(HeleosError::Integrity)
        }

        pub(super) const fn purpose(&self) -> OwnedSiblingFilePurpose {
            self.purpose
        }

        pub(super) fn parent_handle(&self) -> &File {
            &self.parent.retained
        }

        pub(super) fn component_name(&self) -> &OneComponentName {
            &self.name.component
        }

        pub(super) fn has_same_parent(&self, other: &Self) -> bool {
            Arc::ptr_eq(&self.parent, &other.parent)
        }

        pub(super) fn mark_published(&mut self) -> Result<()> {
            if self.state != StagingOwnershipState::Owned {
                return Err(HeleosError::Integrity);
            }
            self.state = StagingOwnershipState::Published;
            Ok(())
        }

        pub(super) fn cleanup(&mut self) -> Result<()> {
            if matches!(
                self.state,
                StagingOwnershipState::Published | StagingOwnershipState::Cleaned
            ) {
                return Ok(());
            }
            if self.state != StagingOwnershipState::Owned {
                return Err(HeleosError::Integrity);
            }

            let binding = self.verify_name_binding();
            self.file.take();
            self.record_cleanup_event(StagingCleanupEvent::HandleClosed);
            if let Err(error) = binding {
                self.state = StagingOwnershipState::Cleaned;
                return match self.parent.sync() {
                    Ok(()) => Err(error),
                    Err(cleanup) => Err(cleanup),
                };
            }

            let removal = self
                .parent
                .directory
                .remove_file(self.name.raw())
                .map_err(classify_direct_staging_io);
            if removal.is_ok() {
                self.record_cleanup_event(StagingCleanupEvent::EntryRemoved);
            }
            let parent_sync = self.parent.sync();
            if parent_sync.is_ok() {
                self.record_cleanup_event(StagingCleanupEvent::ParentSynced);
            }
            self.state = StagingOwnershipState::Cleaned;
            removal.and(parent_sync)
        }

        fn verify_name_binding(&self) -> Result<()> {
            let retained = self.file.as_ref().ok_or(HeleosError::Integrity)?;
            if staging_type_identity(retained, false)? != self.identity {
                return Err(HeleosError::PolicyDenied);
            }
            let reopened = self
                .parent
                .directory
                .open_with(self.name.raw(), &binding_file_options())
                .map_err(classify_direct_staging_io)?
                .into_std();
            if staging_type_identity(&reopened, false)? != self.identity {
                return Err(HeleosError::PolicyDenied);
            }
            Ok(())
        }

        #[cfg(test)]
        pub(super) fn name_for_test(&self) -> &OsStr {
            self.name.raw()
        }

        #[cfg(test)]
        fn record_cleanup_event(&self, event: StagingCleanupEvent) {
            if let Some(events) = &self.cleanup_events {
                events.borrow_mut().push(event);
            }
        }

        #[cfg(not(test))]
        fn record_cleanup_event(&self, _: StagingCleanupEvent) {}
    }

    impl Drop for OwnedSiblingFile {
        fn drop(&mut self) {
            let _ = self.cleanup();
        }
    }

    pub(super) struct OwnedSiblingTree {
        parent: Arc<RetainedSiblingParent>,
        name: OwnedSiblingName,
        directory: Option<File>,
        identity: StagingIdentity,
        state: StagingOwnershipState,
        #[cfg(test)]
        cleanup_events: Option<CleanupEvents>,
    }

    impl OwnedSiblingTree {
        pub(super) fn mark_fully_synced(&mut self) -> Result<()> {
            if self.state != StagingOwnershipState::Building {
                return Err(HeleosError::Integrity);
            }
            sync_staging_directory(self.directory.as_ref().ok_or(HeleosError::Integrity)?)?;
            self.parent.sync()?;
            self.state = StagingOwnershipState::FullySynced;
            Ok(())
        }

        pub(super) fn mark_published(&mut self) -> Result<()> {
            if self.state != StagingOwnershipState::FullySynced {
                return Err(HeleosError::Integrity);
            }
            self.state = StagingOwnershipState::Published;
            Ok(())
        }

        pub(super) fn cleanup(&mut self) -> Result<()> {
            if matches!(
                self.state,
                StagingOwnershipState::Published | StagingOwnershipState::Cleaned
            ) {
                return Ok(());
            }
            if !matches!(
                self.state,
                StagingOwnershipState::Building | StagingOwnershipState::FullySynced
            ) {
                return Err(HeleosError::Integrity);
            }

            let binding = self.verify_name_binding();
            self.directory.take();
            self.record_cleanup_event(StagingCleanupEvent::HandleClosed);
            if let Err(error) = binding {
                self.state = StagingOwnershipState::Cleaned;
                return match self.parent.sync() {
                    Ok(()) => Err(error),
                    Err(cleanup) => Err(cleanup),
                };
            }

            let removal = self
                .parent
                .directory
                .remove_dir_all(self.name.raw())
                .map_err(classify_direct_staging_io);
            if removal.is_ok() {
                self.record_cleanup_event(StagingCleanupEvent::EntryRemoved);
            }
            let parent_sync = self.parent.sync();
            if parent_sync.is_ok() {
                self.record_cleanup_event(StagingCleanupEvent::ParentSynced);
            }
            self.state = StagingOwnershipState::Cleaned;
            removal.and(parent_sync)
        }

        fn verify_name_binding(&self) -> Result<()> {
            let retained = self.directory.as_ref().ok_or(HeleosError::Integrity)?;
            if staging_type_identity(retained, true)? != self.identity {
                return Err(HeleosError::PolicyDenied);
            }
            let reopened = self
                .parent
                .directory
                .open_with(self.name.raw(), &binding_directory_options())
                .map_err(classify_direct_staging_io)?
                .into_std();
            if staging_type_identity(&reopened, true)? != self.identity {
                return Err(HeleosError::PolicyDenied);
            }
            Ok(())
        }

        #[cfg(test)]
        pub(super) const fn state_for_test(&self) -> StagingOwnershipState {
            self.state
        }

        #[cfg(test)]
        pub(super) fn name_for_test(&self) -> &OsStr {
            self.name.raw()
        }

        #[cfg(test)]
        pub(super) fn mark_published_after_test_success(&mut self) -> Result<()> {
            self.mark_published()
        }

        #[cfg(test)]
        fn record_cleanup_event(&self, event: StagingCleanupEvent) {
            if let Some(events) = &self.cleanup_events {
                events.borrow_mut().push(event);
            }
        }

        #[cfg(not(test))]
        fn record_cleanup_event(&self, _: StagingCleanupEvent) {}
    }

    impl Drop for OwnedSiblingTree {
        fn drop(&mut self) {
            let _ = self.cleanup();
        }
    }

    fn create_owned_sibling_file_inner<F>(
        parent: Arc<RetainedSiblingParent>,
        purpose: OwnedSiblingFilePurpose,
        mut next_name: F,
        #[cfg(test)] cleanup_events: Option<CleanupEvents>,
    ) -> Result<OwnedSiblingFile>
    where
        F: FnMut() -> Option<OsString>,
    {
        for _ in 0..STAGING_NAME_ATTEMPTS_MAX {
            let name = OwnedSiblingName::new(next_name().ok_or(HeleosError::PolicyDenied)?)?;
            let opened = match parent
                .directory
                .open_with(name.raw(), &file_create_options(purpose))
            {
                Ok(file) => file.into_std(),
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(classify_direct_staging_io(error)),
            };
            let identity = match staging_type_identity(&opened, false) {
                Ok(identity) => identity,
                Err(error) => {
                    drop(opened);
                    let cleanup = parent
                        .directory
                        .remove_file(name.raw())
                        .map_err(classify_direct_staging_io)
                        .and_then(|()| parent.sync());
                    return match cleanup {
                        Ok(()) => Err(error),
                        Err(cleanup) => Err(cleanup),
                    };
                }
            };
            let mut guard = OwnedSiblingFile {
                parent,
                name,
                purpose,
                file: Some(opened),
                identity,
                state: StagingOwnershipState::Owned,
                #[cfg(test)]
                cleanup_events,
            };
            let prepared = guard
                .file
                .as_mut()
                .ok_or(HeleosError::Integrity)
                .and_then(crate::store::apply_private_permissions_to_handle)
                .map_err(map_staging_mutation_error)
                .and_then(|()| {
                    let file = guard.file.as_ref().ok_or(HeleosError::Integrity)?;
                    validate_staging_handle(file, false)
                });
            match prepared {
                Ok(identity) => {
                    guard.identity = identity;
                    return Ok(guard);
                }
                Err(error) => {
                    return match guard.cleanup() {
                        Ok(()) => Err(error),
                        Err(cleanup) => Err(cleanup),
                    };
                }
            }
        }
        Err(HeleosError::PolicyDenied)
    }

    fn create_owned_sibling_tree_inner<F>(
        parent: &File,
        mut next_name: F,
        #[cfg(test)] cleanup_events: Option<CleanupEvents>,
    ) -> Result<OwnedSiblingTree>
    where
        F: FnMut() -> Option<OsString>,
    {
        let parent = Arc::new(RetainedSiblingParent::from_borrowed(parent)?);
        for _ in 0..STAGING_NAME_ATTEMPTS_MAX {
            let name = OwnedSiblingName::new(next_name().ok_or(HeleosError::PolicyDenied)?)?;
            match parent.directory.create_dir(name.raw()) {
                Ok(()) => {}
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(classify_direct_staging_io(error)),
            }

            let opened = (|| {
                let mut permission_handle = parent
                    .directory
                    .open_with(name.raw(), &directory_apply_options())
                    .map_err(classify_direct_staging_io)?
                    .into_std();
                crate::store::apply_private_permissions_to_handle(&mut permission_handle)
                    .map_err(map_staging_mutation_error)?;
                drop(permission_handle);
                parent
                    .directory
                    .open_with(name.raw(), &directory_subject_options())
                    .map_err(classify_direct_staging_io)
                    .map(cap_std::fs::File::into_std)
            })();
            let opened = match opened {
                Ok(opened) => opened,
                Err(error) => {
                    let cleanup = parent
                        .directory
                        .remove_dir(name.raw())
                        .map_err(classify_direct_staging_io)
                        .and_then(|()| parent.sync());
                    return match cleanup {
                        Ok(()) => Err(error),
                        Err(cleanup) => Err(cleanup),
                    };
                }
            };
            let identity = match validate_staging_handle(&opened, true) {
                Ok(identity) => identity,
                Err(error) => {
                    drop(opened);
                    let cleanup = parent
                        .directory
                        .remove_dir(name.raw())
                        .map_err(classify_direct_staging_io)
                        .and_then(|()| parent.sync());
                    return match cleanup {
                        Ok(()) => Err(error),
                        Err(cleanup) => Err(cleanup),
                    };
                }
            };
            return Ok(OwnedSiblingTree {
                parent,
                name,
                directory: Some(opened),
                identity,
                state: StagingOwnershipState::Building,
                #[cfg(test)]
                cleanup_events,
            });
        }
        Err(HeleosError::PolicyDenied)
    }

    pub(super) fn create_owned_sibling_file(
        parent: &File,
        purpose: OwnedSiblingFilePurpose,
    ) -> Result<OwnedSiblingFile> {
        let parent = Arc::new(RetainedSiblingParent::from_borrowed(parent)?);
        create_owned_sibling_file_inner(
            parent,
            purpose,
            || {
                Some(OsString::from(format!(
                    ".heleos-backup-{}-{}.partial",
                    purpose.label(),
                    uuid::Uuid::new_v4()
                )))
            },
            #[cfg(test)]
            None,
        )
    }

    pub(super) fn create_owned_backup_file_pair(
        parent: &File,
    ) -> Result<(OwnedSiblingFile, OwnedSiblingFile)> {
        let parent = Arc::new(RetainedSiblingParent::from_borrowed(parent)?);
        let mut plaintext = create_owned_sibling_file_inner(
            Arc::clone(&parent),
            OwnedSiblingFilePurpose::Plaintext,
            || {
                Some(OsString::from(format!(
                    ".heleos-backup-plaintext-{}.partial",
                    uuid::Uuid::new_v4()
                )))
            },
            #[cfg(test)]
            None,
        )?;
        let ciphertext = match create_owned_sibling_file_inner(
            parent,
            OwnedSiblingFilePurpose::Ciphertext,
            || {
                Some(OsString::from(format!(
                    ".heleos-backup-ciphertext-{}.partial",
                    uuid::Uuid::new_v4()
                )))
            },
            #[cfg(test)]
            None,
        ) {
            Ok(ciphertext) => ciphertext,
            Err(error) => {
                return match plaintext.cleanup() {
                    Ok(()) => Err(error),
                    Err(cleanup) => Err(cleanup),
                };
            }
        };
        Ok((plaintext, ciphertext))
    }

    pub(super) fn create_owned_sibling_tree(parent: &File) -> Result<OwnedSiblingTree> {
        create_owned_sibling_tree_inner(
            parent,
            || {
                Some(OsString::from(format!(
                    ".heleos-restore-tree-{}.partial",
                    uuid::Uuid::new_v4()
                )))
            },
            #[cfg(test)]
            None,
        )
    }

    #[cfg(test)]
    pub(super) fn create_owned_sibling_file_with_test_names(
        parent: &File,
        purpose: OwnedSiblingFilePurpose,
        names: impl IntoIterator<Item = OsString>,
        cleanup_events: Option<CleanupEvents>,
    ) -> Result<OwnedSiblingFile> {
        let mut names = names.into_iter();
        let parent = Arc::new(RetainedSiblingParent::from_borrowed(parent)?);
        create_owned_sibling_file_inner(parent, purpose, || names.next(), cleanup_events)
    }

    #[cfg(test)]
    pub(super) fn create_owned_backup_file_pair_with_test_names(
        parent: &File,
        plaintext_names: impl IntoIterator<Item = OsString>,
        ciphertext_names: impl IntoIterator<Item = OsString>,
    ) -> Result<(OwnedSiblingFile, OwnedSiblingFile)> {
        let parent = Arc::new(RetainedSiblingParent::from_borrowed(parent)?);
        let mut plaintext_names = plaintext_names.into_iter();
        let mut plaintext = create_owned_sibling_file_inner(
            Arc::clone(&parent),
            OwnedSiblingFilePurpose::Plaintext,
            || plaintext_names.next(),
            None,
        )?;
        let mut ciphertext_names = ciphertext_names.into_iter();
        let ciphertext = match create_owned_sibling_file_inner(
            parent,
            OwnedSiblingFilePurpose::Ciphertext,
            || ciphertext_names.next(),
            None,
        ) {
            Ok(ciphertext) => ciphertext,
            Err(error) => {
                return match plaintext.cleanup() {
                    Ok(()) => Err(error),
                    Err(cleanup) => Err(cleanup),
                };
            }
        };
        Ok((plaintext, ciphertext))
    }

    #[cfg(test)]
    pub(super) fn create_owned_sibling_tree_with_test_names(
        parent: &File,
        names: impl IntoIterator<Item = OsString>,
        cleanup_events: Option<CleanupEvents>,
    ) -> Result<OwnedSiblingTree> {
        let mut names = names.into_iter();
        create_owned_sibling_tree_inner(parent, || names.next(), cleanup_events)
    }
}

use staging_owner::{OwnedSiblingFile, OwnedSiblingFilePurpose};
#[cfg(test)]
use staging_owner::{
    StagingCleanupEvent, StagingOwnershipState, create_owned_sibling_file_with_test_names,
    create_owned_sibling_tree_with_test_names,
};

#[cfg_attr(
    not(test),
    expect(
        dead_code,
        reason = "private hostile-parser boundary is wired into verification and restore in the next reviewed slice"
    )
)]
fn map_hostile_parser_error(error: HeleosError) -> HeleosError {
    if matches!(error, HeleosError::ResourceLimit) {
        HeleosError::InvalidBackupContainer
    } else {
        error
    }
}

#[derive(Clone, Copy, Debug, Default)]
pub struct BackupService;

#[cfg_attr(
    not(test),
    expect(
        dead_code,
        reason = "owned snapshot is consumed by BackupService::create in the next reviewed slice"
    )
)]
struct DatabaseSnapshot {
    directory: tempfile::TempDir,
    database_path: PathBuf,
    inventory: VaultInventory,
}

#[derive(Clone, Copy)]
struct SnapshotDatabaseBounds {
    page_size: u64,
    maximum_page_count: u64,
}

impl DatabaseSnapshot {
    #[cfg(test)]
    fn root_path(&self) -> &Path {
        self.directory.path()
    }
}

#[cfg(test)]
enum SnapshotStepTestOutcome {
    Failure,
}

#[cfg(test)]
struct SnapshotTestControl<'a> {
    before_inventory: Option<Box<dyn FnMut() + 'a>>,
    after_inventory: Option<Box<dyn FnMut() + 'a>>,
    after_more: Option<Box<dyn FnMut() + 'a>>,
    before_step: Option<Box<dyn FnMut() + 'a>>,
    database_bytes_max: Option<u64>,
    step_outcomes: std::collections::VecDeque<SnapshotStepTestOutcome>,
}

#[cfg(test)]
impl<'a> SnapshotTestControl<'a> {
    fn new(before_inventory: impl FnMut() + 'a, after_inventory: impl FnMut() + 'a) -> Self {
        Self {
            before_inventory: Some(Box::new(before_inventory)),
            after_inventory: Some(Box::new(after_inventory)),
            after_more: None,
            before_step: None,
            database_bytes_max: None,
            step_outcomes: std::collections::VecDeque::new(),
        }
    }

    fn after_more(mut self, callback: impl FnMut() + 'a) -> Self {
        self.after_more = Some(Box::new(callback));
        self
    }

    fn before_step(mut self, callback: impl FnMut() + 'a) -> Self {
        self.before_step = Some(Box::new(callback));
        self
    }

    const fn with_database_bytes_max(mut self, maximum: u64) -> Self {
        self.database_bytes_max = Some(maximum);
        self
    }

    fn step_outcomes(
        mut self,
        outcomes: impl IntoIterator<Item = SnapshotStepTestOutcome>,
    ) -> Self {
        self.step_outcomes.extend(outcomes);
        self
    }

    fn run_before_inventory(&mut self) {
        if let Some(mut callback) = self.before_inventory.take() {
            callback();
        }
    }

    fn run_after_inventory(&mut self) {
        if let Some(mut callback) = self.after_inventory.take() {
            callback();
        }
    }

    fn run_after_more(&mut self) {
        if let Some(mut callback) = self.after_more.take() {
            callback();
        }
    }
}

#[cfg_attr(
    not(test),
    expect(
        dead_code,
        reason = "private primitive is wired into BackupService::create in the next reviewed slice"
    )
)]
fn create_database_snapshot(store: &mut Store, staging_parent: &Path) -> Result<DatabaseSnapshot> {
    create_database_snapshot_inner(
        store,
        staging_parent,
        #[cfg(test)]
        None,
    )
}

#[cfg(test)]
fn create_database_snapshot_with_test_control(
    store: &mut Store,
    staging_parent: &Path,
    control: &mut SnapshotTestControl<'_>,
) -> Result<DatabaseSnapshot> {
    create_database_snapshot_inner(store, staging_parent, Some(control))
}

fn create_database_snapshot_inner(
    store: &mut Store,
    staging_parent: &Path,
    #[cfg(test)] mut control: Option<&mut SnapshotTestControl<'_>>,
) -> Result<DatabaseSnapshot> {
    require_database_snapshot_source(store)?;

    #[cfg(not(test))]
    let database_bytes_max = DATABASE_SNAPSHOT_BYTES_MAX;
    #[cfg(test)]
    let database_bytes_max = control
        .as_deref()
        .and_then(|control| control.database_bytes_max)
        .unwrap_or(DATABASE_SNAPSHOT_BYTES_MAX);

    let canonical_staging_parent = fs::canonicalize(staging_parent).map_err(HeleosError::Io)?;
    if canonical_staging_parent != staging_parent {
        return Err(HeleosError::PolicyDenied);
    }
    verify_private_permissions(&canonical_staging_parent)?;
    if !fs::symlink_metadata(&canonical_staging_parent)
        .map_err(HeleosError::Io)?
        .is_dir()
    {
        return Err(HeleosError::PolicyDenied);
    }

    let directory = tempfile::Builder::new()
        .prefix(".heleos-backup-database-")
        .tempdir_in(&canonical_staging_parent)
        .map_err(HeleosError::Io)?;
    apply_private_permissions(directory.path())?;
    verify_private_permissions(directory.path())?;

    let database_path = directory.path().join("snapshot.sqlite3");
    create_private_staging_file(&database_path)?;
    let mut destination = open_staging_database(&database_path)?;

    let transaction = Transaction::new_unchecked(&store.connection, TransactionBehavior::Deferred)
        .map_err(|_| HeleosError::Database)?;
    #[cfg(test)]
    if let Some(control) = control.as_deref_mut() {
        control.run_before_inventory();
    }

    let snapshot_result = match store.vault_inventory_rows() {
        Ok(inventory) => {
            #[cfg(test)]
            if let Some(control) = control.as_deref_mut() {
                control.run_after_inventory();
            }

            match snapshot_database_bounds(&transaction, database_bytes_max)
                .and_then(|bounds| configure_staging_database(&destination, bounds))
            {
                Ok(()) => match Backup::new(&transaction, &mut destination) {
                    Ok(backup) => {
                        let result = step_database_snapshot(
                            &backup,
                            #[cfg(test)]
                            control,
                        );
                        drop(backup);
                        result.map(|()| inventory)
                    }
                    Err(error) => Err(map_backup_sqlite_error(error)),
                },
                Err(error) => Err(error),
            }
        }
        Err(error) => Err(error),
    };

    let transaction_result = transaction.finish().map_err(|_| HeleosError::Database);
    let destination_result = destination.close().map_err(|_| HeleosError::Database);
    let inventory =
        combine_snapshot_teardown_results(snapshot_result, transaction_result, destination_result)?;

    require_database_snapshot_source(store)?;
    validate_staged_database(directory.path(), &database_path, database_bytes_max)?;

    Ok(DatabaseSnapshot {
        directory,
        database_path,
        inventory,
    })
}

fn require_database_snapshot_source(store: &Store) -> Result<()> {
    if store.writer_lock().is_none() {
        return Err(HeleosError::PolicyDenied);
    }
    store.require_writer_capability()
}

fn create_private_staging_file(path: &Path) -> Result<()> {
    let mut options = OpenOptions::new();
    options.read(true).write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;

        options.mode(0o600);
    }
    let mut file = options.open(path).map_err(HeleosError::Io)?;
    crate::store::apply_private_permissions_to_handle(&mut file)?;
    file.sync_all().map_err(HeleosError::Io)
}

fn open_staging_database(path: &Path) -> Result<Connection> {
    let flags = OpenFlags::SQLITE_OPEN_READ_WRITE
        | OpenFlags::SQLITE_OPEN_NO_MUTEX
        | OpenFlags::SQLITE_OPEN_NOFOLLOW
        | OpenFlags::SQLITE_OPEN_PRIVATE_CACHE;
    let connection = Connection::open_with_flags(path, flags).map_err(|_| HeleosError::Database)?;
    connection
        .busy_timeout(DATABASE_SNAPSHOT_BUSY_DEADLINE)
        .map_err(|_| HeleosError::Database)?;
    connection
        .pragma_update(None, "synchronous", "FULL")
        .map_err(|_| HeleosError::Database)?;
    Ok(connection)
}

fn snapshot_database_bounds(
    transaction: &Transaction<'_>,
    database_bytes_max: u64,
) -> Result<SnapshotDatabaseBounds> {
    let page_size = transaction
        .pragma_query_value(Some("main"), "page_size", |row| row.get::<_, i64>(0))
        .map_err(|_| HeleosError::Database)?;
    let page_size = validate_sqlite_page_size(page_size)?;
    let page_count = transaction
        .pragma_query_value(Some("main"), "page_count", |row| row.get::<_, i64>(0))
        .map_err(|_| HeleosError::Database)?;
    let page_count = u64::try_from(page_count).map_err(|_| HeleosError::Database)?;
    let logical_bytes = page_size
        .checked_mul(page_count)
        .ok_or(HeleosError::ResourceLimit)?;
    if logical_bytes > database_bytes_max {
        return Err(HeleosError::ResourceLimit);
    }
    let maximum_page_count = database_bytes_max
        .checked_div(page_size)
        .filter(|page_count| *page_count > 0)
        .ok_or(HeleosError::ResourceLimit)?;

    Ok(SnapshotDatabaseBounds {
        page_size,
        maximum_page_count,
    })
}

fn validate_sqlite_page_size(page_size: i64) -> Result<u64> {
    let page_size = u64::try_from(page_size).map_err(|_| HeleosError::Database)?;
    if !(512..=65_536).contains(&page_size) || !page_size.is_power_of_two() {
        return Err(HeleosError::Database);
    }
    Ok(page_size)
}

fn configure_staging_database(
    connection: &Connection,
    bounds: SnapshotDatabaseBounds,
) -> Result<()> {
    let page_size = i64::try_from(bounds.page_size).map_err(|_| HeleosError::ResourceLimit)?;
    connection
        .pragma_update(Some("main"), "page_size", page_size)
        .map_err(|_| HeleosError::Database)?;
    let configured_page_size = connection
        .pragma_query_value(Some("main"), "page_size", |row| row.get::<_, i64>(0))
        .map_err(|_| HeleosError::Database)?;
    if validate_sqlite_page_size(configured_page_size)? != bounds.page_size {
        return Err(HeleosError::Database);
    }

    let maximum_page_count =
        i64::try_from(bounds.maximum_page_count).map_err(|_| HeleosError::ResourceLimit)?;
    connection
        .pragma_update(Some("main"), "max_page_count", maximum_page_count)
        .map_err(|_| HeleosError::Database)?;
    let configured_maximum = connection
        .pragma_query_value(Some("main"), "max_page_count", |row| row.get::<_, i64>(0))
        .map_err(|_| HeleosError::Database)?;
    let configured_maximum =
        u64::try_from(configured_maximum).map_err(|_| HeleosError::Database)?;
    if configured_maximum == 0 || configured_maximum != bounds.maximum_page_count {
        return Err(HeleosError::ResourceLimit);
    }
    Ok(())
}

fn step_database_snapshot(
    backup: &Backup<'_, '_>,
    #[cfg(test)] mut control: Option<&mut SnapshotTestControl<'_>>,
) -> Result<()> {
    let mut transient_retries = 0_u32;
    let mut transient_deadline = None;

    loop {
        let outcome = next_database_snapshot_step(
            backup,
            #[cfg(test)]
            control.as_deref_mut(),
        )?;
        match outcome {
            StepResult::Done => return Ok(()),
            StepResult::More =>
            {
                #[cfg(test)]
                if let Some(control) = control.as_deref_mut() {
                    control.run_after_more();
                }
            }
            StepResult::Busy | StepResult::Locked => {
                transient_retries = transient_retries
                    .checked_add(1)
                    .ok_or(HeleosError::ResourceLimit)?;
                if transient_retries > DATABASE_SNAPSHOT_BUSY_RETRIES_MAX {
                    return Err(HeleosError::Timeout);
                }
                let deadline = match transient_deadline {
                    Some(deadline) => deadline,
                    None => {
                        let deadline = Instant::now()
                            .checked_add(DATABASE_SNAPSHOT_BUSY_DEADLINE)
                            .ok_or(HeleosError::ResourceLimit)?;
                        transient_deadline = Some(deadline);
                        deadline
                    }
                };
                if Instant::now() >= deadline {
                    return Err(HeleosError::Timeout);
                }
                thread::sleep(DATABASE_SNAPSHOT_BUSY_RETRY_PAUSE);
            }
            _ => return Err(HeleosError::Database),
        }
    }
}

#[cfg(not(test))]
fn next_database_snapshot_step(backup: &Backup<'_, '_>) -> Result<StepResult> {
    backup
        .step(DATABASE_SNAPSHOT_PAGES_PER_STEP)
        .map_err(map_backup_sqlite_error)
}

#[cfg(test)]
fn next_database_snapshot_step(
    backup: &Backup<'_, '_>,
    control: Option<&mut SnapshotTestControl<'_>>,
) -> Result<StepResult> {
    let injected = control.and_then(|control| {
        if let Some(callback) = control.before_step.as_mut() {
            callback();
        }
        control.step_outcomes.pop_front()
    });
    match injected {
        Some(SnapshotStepTestOutcome::Failure) => Err(HeleosError::Database),
        None => backup
            .step(DATABASE_SNAPSHOT_PAGES_PER_STEP)
            .map_err(map_backup_sqlite_error),
    }
}

fn map_backup_sqlite_error(error: rusqlite::Error) -> HeleosError {
    match error {
        rusqlite::Error::SqliteFailure(sqlite, _)
            if sqlite.code == rusqlite::ffi::ErrorCode::DiskFull =>
        {
            HeleosError::ResourceLimit
        }
        _ => HeleosError::Database,
    }
}

fn combine_snapshot_teardown_results<T>(
    operation: Result<T>,
    transaction: Result<()>,
    destination: Result<()>,
) -> Result<T> {
    transaction?;
    destination?;
    operation
}

fn validate_staged_database(
    root: &Path,
    database_path: &Path,
    database_bytes_max: u64,
) -> Result<()> {
    let mut entries = fs::read_dir(root).map_err(HeleosError::Io)?;
    let entry = entries
        .next()
        .transpose()
        .map_err(HeleosError::Io)?
        .ok_or(HeleosError::Integrity)?;
    if entry.path() != database_path
        || entries
            .next()
            .transpose()
            .map_err(HeleosError::Io)?
            .is_some()
    {
        return Err(HeleosError::Integrity);
    }
    verify_private_permissions(database_path)?;
    let file = OpenOptions::new()
        .read(true)
        .write(true)
        .open(database_path)
        .map_err(HeleosError::Io)?;
    let length = file.metadata().map_err(HeleosError::Io)?.len();
    if length > database_bytes_max {
        return Err(HeleosError::ResourceLimit);
    }
    file.sync_all().map_err(HeleosError::Io)
}

#[cfg(test)]
mod tests {
    use std::cell::{Cell, RefCell};
    use std::ffi::OsString;
    use std::fs;
    use std::io::{Cursor, Read, Seek, SeekFrom, Write};
    use std::path::{Path, PathBuf};
    use std::rc::Rc;
    use std::str::FromStr;

    use ed25519_dalek::{Signature, SigningKey};
    use heleos_platform_fs::{
        OneComponentName, RetainedVolumeRelation, RetainedVolumeRelationError,
        publish_file_noreplace,
    };
    use rusqlite::functions::FunctionFlags;
    use rusqlite::{Connection, params};

    use super::archive::{
        BackupEntryKind, BackupEntryRecord, BackupRecordPlan, BackupStagingTestFault,
        copy_authenticated_extent, encode_entry_table, encrypt_age_stream, parse_entry_table,
        parse_plaintext_container_header, stage_encrypted_backup,
        stage_encrypted_backup_with_test_fault, validate_entry_table_claim,
        validate_plaintext_container_length, validate_staged_plaintext_file_length,
        verify_plaintext_container, write_plaintext_container,
    };
    use super::capacity::{
        AllocationGranularity, VolumeLayout, exact_restore_work, normal_capacity_reserve,
        post_authentication_capacity, predecrypt_capacity_required,
        remaining_work_capacity_requirements, require_reserve_only_capacity,
        require_stable_retained_volume_relation, store_scratch_capacity,
    };
    use super::manifest::{
        BackupDatabaseDescriptorV1, BackupManifestV1, sign_manifest, verify_manifest_signature,
    };
    use super::staging_error::{
        StagingMutationWriter, StagingOrchestrationError, StagingResourceLimit,
        classify_direct_staging_io, classify_staging_orchestration_error,
    };
    use super::staging_owner::{
        create_owned_backup_file_pair, create_owned_backup_file_pair_with_test_names,
        create_owned_sibling_file, create_owned_sibling_tree,
    };
    use super::{
        OwnedSiblingFilePurpose, SnapshotStepTestOutcome, SnapshotTestControl, StagingCleanupEvent,
        StagingOwnershipState, create_database_snapshot,
        create_database_snapshot_with_test_control, create_owned_sibling_file_with_test_names,
        create_owned_sibling_tree_with_test_names, map_backup_sqlite_error,
        map_hostile_parser_error,
    };
    use crate::{
        ActorId, Clock, DataClass, HeleosError, IdGenerator, ProjectCreateRequest, ProjectId,
        ProjectService, Sha256Digest, Store, Vault, apply_private_permissions,
    };

    struct TestClock;

    impl Clock for TestClock {
        fn now_unix_ms(&self) -> i64 {
            1_700_000_000_000
        }
    }

    struct TestIds(Cell<u128>);

    impl TestIds {
        const fn new(first: u128) -> Self {
            Self(Cell::new(first))
        }
    }

    impl IdGenerator for TestIds {
        fn next_uuid(&self) -> uuid::Uuid {
            let value = self.0.get();
            self.0.set(value.checked_add(1).expect("bounded test IDs"));
            uuid::Uuid::from_u128(value)
        }
    }

    struct TestDatabase {
        _root: tempfile::TempDir,
        root: PathBuf,
        database: PathBuf,
        staging_parent: PathBuf,
    }

    impl TestDatabase {
        fn new() -> Self {
            let root = tempfile::tempdir().expect("create database test root");
            apply_private_permissions(root.path()).expect("harden database test root");
            let canonical_root = fs::canonicalize(root.path()).expect("canonicalize test root");
            let staging_parent = canonical_root.join("staging");
            fs::create_dir(&staging_parent).expect("create staging parent");
            apply_private_permissions(&staging_parent).expect("harden staging parent");
            Self {
                _root: root,
                database: canonical_root.join("foundation.sqlite3"),
                root: canonical_root,
                staging_parent,
            }
        }

        fn migrated_store(&self) -> Store {
            let mut store = Store::open_writer(&self.database).expect("open database writer");
            store.migrate().expect("migrate database");
            store
        }

        fn migrated_store_with_page_size(&self, page_size: i64) -> Store {
            let connection = Connection::open(&self.database).expect("create page-size fixture");
            connection
                .pragma_update(None, "page_size", page_size)
                .expect("set fixture page size");
            connection
                .execute_batch("VACUUM")
                .expect("persist fixture page size");
            let observed = connection
                .pragma_query_value(None, "page_size", |row| row.get::<_, i64>(0))
                .expect("read fixture page size");
            assert_eq!(observed, page_size);
            drop(connection);
            self.migrated_store()
        }

        fn raw_wal_connection(&self) -> Connection {
            let connection = Connection::open(&self.database).expect("open raw WAL connection");
            let flags = FunctionFlags::SQLITE_UTF8
                | FunctionFlags::SQLITE_DETERMINISTIC
                | FunctionFlags::SQLITE_INNOCUOUS;
            connection
                .create_scalar_function("heleos_is_jcs", 1, flags, |context| {
                    let text = context.get::<String>(0)?;
                    let Ok(value) = serde_json::from_str::<serde_json::Value>(&text) else {
                        return Ok(false);
                    };
                    let Ok(canonical) = serde_jcs::to_string(&value) else {
                        return Ok(false);
                    };
                    Ok(canonical == text)
                })
                .expect("register raw JCS validator");
            connection
                .create_scalar_function("heleos_is_uuid", 1, flags, |context| {
                    let text = context.get::<String>(0)?;
                    Ok(uuid::Uuid::parse_str(&text)
                        .is_ok_and(|value| value.hyphenated().to_string() == text))
                })
                .expect("register raw UUID validator");
            connection
                .create_scalar_function("heleos_valid_text", 3, flags, |context| {
                    let text = context.get::<String>(0)?;
                    let max_bytes = context.get::<i64>(1)?;
                    let allow_ordinary_whitespace = context.get::<i64>(2)? != 0;
                    let valid_control = |character: char| {
                        allow_ordinary_whitespace && matches!(character, '\n' | '\r' | '\t')
                    };
                    Ok(max_bytes >= 0
                        && !text.is_empty()
                        && u64::try_from(text.len()).is_ok_and(|length| length <= max_bytes as u64)
                        && !text
                            .chars()
                            .any(|character| character.is_control() && !valid_control(character)))
                })
                .expect("register raw text validator");
            let journal_mode = connection
                .pragma_query_value(Some("main"), "journal_mode", |row| row.get::<_, String>(0))
                .expect("read raw journal mode");
            assert_eq!(journal_mode.to_ascii_lowercase(), "wal");
            connection
        }
    }

    fn insert_content(connection: &Connection, digest: Sha256Digest) {
        connection
            .execute(
                "INSERT INTO content_objects (
                     sha256, byte_length, media_type, admission_state, vault_key,
                     created_at_ms, created_by, quarantine_reason
                 ) VALUES (?1, 1, 'application/pdf', 'accepted', ?2, 1, 'backup-test', NULL)",
                params![digest.to_string(), Vault::object_key(digest)],
            )
            .expect("commit valid raw content row");
    }

    fn content_digests(database: &Path) -> Vec<String> {
        let connection = Connection::open(database).expect("open snapshot verifier");
        let mut statement = connection
            .prepare("SELECT sha256 FROM content_objects ORDER BY sha256")
            .expect("prepare content query");
        statement
            .query_map([], |row| row.get::<_, String>(0))
            .expect("query content rows")
            .collect::<rusqlite::Result<Vec<_>>>()
            .expect("collect content rows")
    }

    fn indexed_digest(index: u32) -> Sha256Digest {
        let mut bytes = [0_u8; 32];
        bytes[..4].copy_from_slice(&index.to_be_bytes());
        Sha256Digest::from_bytes(bytes)
    }

    fn sharded_digest(first: u8, second: u8, index: u64) -> Sha256Digest {
        let mut bytes = [0_u8; 32];
        bytes[0] = first;
        bytes[1] = second;
        bytes[24..].copy_from_slice(&index.to_be_bytes());
        Sha256Digest::from_bytes(bytes)
    }

    fn create_audited_project(store: &mut Store, ids: &TestIds, name: &str) {
        ProjectService::new(store, &TestClock, ids)
            .expect("reuse store for project service")
            .create(ProjectCreateRequest {
                project_id: Some(ProjectId::from_uuid(ids.next_uuid())),
                name: name.to_owned(),
                actor: ActorId::from_str("backup-test").expect("test actor"),
                data_class: DataClass::Internal,
            })
            .expect("commit audited project after snapshot teardown");
    }

    struct PlaintextContainerFixture {
        bytes: Vec<u8>,
        signer: [u8; 32],
        signature_offset: usize,
        table_length_offset: usize,
        payload_offset: usize,
    }

    fn database_only_plaintext_container(payload: &[u8]) -> PlaintextContainerFixture {
        let signing_key = SigningKey::from_bytes(&[0x42; 32]);
        let signer = signing_key.verifying_key().to_bytes();
        let database_digest = Sha256Digest::hash_reader(payload).expect("hash fixture database");
        let records = [BackupEntryRecord::database(
            database_digest,
            u64::try_from(payload.len()).expect("bounded fixture database"),
        )
        .expect("construct fixture database record")];
        let table = encode_entry_table(&records).expect("encode fixture table");
        let manifest = BackupManifestV1::new(
            Sha256Digest::hash_reader(signer.as_slice()).expect("hash fixture signer"),
            Sha256Digest::hash_reader(table.as_slice()).expect("hash fixture table"),
            u64::try_from(table.len()).expect("bounded fixture table"),
            1,
            u64::try_from(payload.len()).expect("bounded fixture payload"),
            BackupDatabaseDescriptorV1::new(
                database_digest,
                u64::try_from(payload.len()).expect("bounded fixture database"),
            )
            .expect("construct fixture database descriptor"),
        )
        .expect("construct fixture manifest");
        let manifest_bytes = manifest.canonical_bytes().expect("encode fixture manifest");
        let signature =
            sign_manifest(&signing_key, &manifest_bytes).expect("sign fixture manifest");

        let mut bytes = Vec::new();
        bytes.extend_from_slice(b"HELEOSB1");
        bytes.extend_from_slice(
            &u64::try_from(manifest_bytes.len())
                .expect("bounded fixture manifest")
                .to_be_bytes(),
        );
        bytes.extend_from_slice(&manifest_bytes);
        bytes.extend_from_slice(&signer);
        let signature_offset = bytes.len();
        bytes.extend_from_slice(&signature);
        let table_length_offset = bytes.len();
        bytes.extend_from_slice(
            &u64::try_from(table.len())
                .expect("bounded fixture table")
                .to_be_bytes(),
        );
        bytes.extend_from_slice(&table);
        let payload_offset = bytes.len();
        bytes.extend_from_slice(payload);

        PlaintextContainerFixture {
            bytes,
            signer,
            signature_offset,
            table_length_offset,
            payload_offset,
        }
    }

    #[test]
    fn backup_manifest_has_one_exact_canonical_jcs_encoding() {
        let manifest = BackupManifestV1::new(
            Sha256Digest::from_bytes([0x11; 32]),
            Sha256Digest::from_bytes([0x22; 32]),
            41,
            1,
            3,
            BackupDatabaseDescriptorV1::new(Sha256Digest::from_bytes([0x33; 32]), 3)
                .expect("construct database descriptor"),
        )
        .expect("construct backup manifest");
        let bytes = manifest.canonical_bytes().expect("encode backup manifest");

        assert_eq!(
            bytes,
            br#"{"database":{"byte_length":3,"sha256":"3333333333333333333333333333333333333333333333333333333333333333"},"decoded_payload_byte_length":3,"entry_count":1,"entry_table_byte_length":41,"entry_table_sha256":"2222222222222222222222222222222222222222222222222222222222222222","schema":"heleos.backup-manifest/v1","signer_key_sha256":"1111111111111111111111111111111111111111111111111111111111111111"}"#
        );
        assert_eq!(
            BackupManifestV1::parse_canonical(&bytes).expect("parse canonical backup manifest"),
            manifest
        );
    }

    #[test]
    fn backup_manifest_parser_rejects_noncanonical_and_unknown_fields() {
        let noncanonical = br#" {"database":{"byte_length":3,"sha256":"3333333333333333333333333333333333333333333333333333333333333333"},"decoded_payload_byte_length":3,"entry_count":1,"entry_table_byte_length":41,"entry_table_sha256":"2222222222222222222222222222222222222222222222222222222222222222","schema":"heleos.backup-manifest/v1","signer_key_sha256":"1111111111111111111111111111111111111111111111111111111111111111"}"#;
        let unknown = br#"{"database":{"byte_length":3,"sha256":"3333333333333333333333333333333333333333333333333333333333333333"},"decoded_payload_byte_length":3,"entry_count":1,"entry_table_byte_length":41,"entry_table_sha256":"2222222222222222222222222222222222222222222222222222222222222222","schema":"heleos.backup-manifest/v1","signer_key_sha256":"1111111111111111111111111111111111111111111111111111111111111111","unknown":0}"#;

        for bytes in [noncanonical.as_slice(), unknown.as_slice()] {
            assert!(matches!(
                BackupManifestV1::parse_canonical(bytes),
                Err(HeleosError::InvalidBackupContainer)
            ));
        }
    }

    #[test]
    fn database_only_manifest_rejects_unaccounted_payload_bytes() {
        let bytes = br#"{"database":{"byte_length":3,"sha256":"3333333333333333333333333333333333333333333333333333333333333333"},"decoded_payload_byte_length":4,"entry_count":1,"entry_table_byte_length":41,"entry_table_sha256":"2222222222222222222222222222222222222222222222222222222222222222","schema":"heleos.backup-manifest/v1","signer_key_sha256":"1111111111111111111111111111111111111111111111111111111111111111"}"#;

        assert!(matches!(
            BackupManifestV1::parse_canonical(bytes),
            Err(HeleosError::InvalidBackupContainer)
        ));
    }

    #[test]
    fn manifest_signature_is_strictly_bound_to_the_exact_backup_domain() {
        let signing_key = SigningKey::from_bytes(&[0x42; 32]);
        let manifest = BackupManifestV1::new(
            Sha256Digest::hash_reader(signing_key.verifying_key().as_bytes().as_slice())
                .expect("hash signer key"),
            Sha256Digest::from_bytes([0x22; 32]),
            41,
            1,
            3,
            BackupDatabaseDescriptorV1::new(Sha256Digest::from_bytes([0x33; 32]), 3)
                .expect("construct database descriptor"),
        )
        .expect("construct signed manifest");
        let manifest_bytes = manifest.canonical_bytes().expect("encode signed manifest");
        let signature = sign_manifest(&signing_key, &manifest_bytes).expect("sign manifest");

        verify_manifest_signature(
            signing_key.verifying_key().as_bytes(),
            &manifest_bytes,
            &signature,
        )
        .expect("strictly verify manifest");
        let signature_value = Signature::from_bytes(&signature);
        let mut exact_domain_message = b"heleos-backup-v1\0".to_vec();
        exact_domain_message.extend_from_slice(&manifest_bytes);
        assert!(
            signing_key
                .verifying_key()
                .verify_strict(&exact_domain_message, &signature_value)
                .is_ok()
        );
        assert!(
            signing_key
                .verifying_key()
                .verify_strict(&manifest_bytes, &signature_value)
                .is_err()
        );

        let mut corrupted = signature;
        corrupted[0] ^= 1;
        assert!(matches!(
            verify_manifest_signature(
                signing_key.verifying_key().as_bytes(),
                &manifest_bytes,
                &corrupted,
            ),
            Err(HeleosError::BackupIntegrity)
        ));
    }

    #[test]
    fn entry_table_has_exact_41_byte_big_endian_golden_records() {
        let records = vec![
            BackupEntryRecord::database(Sha256Digest::from_bytes([0x10; 32]), 3)
                .expect("construct database record"),
            BackupEntryRecord::blob(Sha256Digest::from_bytes([0x20; 32]), 1)
                .expect("construct first blob record"),
            BackupEntryRecord::blob(Sha256Digest::from_bytes([0x30; 32]), 2)
                .expect("construct second blob record"),
        ];
        let encoded = encode_entry_table(&records).expect("encode entry table");
        let mut expected = Vec::new();
        expected.push(0);
        expected.extend_from_slice(&[0x10; 32]);
        expected.extend_from_slice(&3_u64.to_be_bytes());
        expected.push(1);
        expected.extend_from_slice(&[0x20; 32]);
        expected.extend_from_slice(&1_u64.to_be_bytes());
        expected.push(1);
        expected.extend_from_slice(&[0x30; 32]);
        expected.extend_from_slice(&2_u64.to_be_bytes());

        assert_eq!(encoded, expected);
        let parsed = parse_entry_table(&encoded).expect("parse entry table");
        assert_eq!(parsed.records(), records.as_slice());
        assert_eq!(parsed.decoded_payload_byte_length(), 6);
    }

    #[test]
    fn entry_table_claims_reject_caps_overflow_and_length_mismatch_without_allocation() {
        assert!(matches!(
            validate_entry_table_claim(2_000_002, 82_000_082),
            Err(HeleosError::ResourceLimit)
        ));
        assert!(matches!(
            validate_entry_table_claim(1, 80 * 1024 * 1024 + 1),
            Err(HeleosError::ResourceLimit)
        ));
        assert!(matches!(
            validate_entry_table_claim(u64::MAX, u64::MAX),
            Err(HeleosError::ResourceLimit)
        ));
        assert!(matches!(
            validate_entry_table_claim(1, 40),
            Err(HeleosError::InvalidBackupContainer)
        ));
        assert_eq!(
            validate_entry_table_claim(2_000_001, 82_000_041)
                .expect("accept exact maximum record count"),
            82_000_041
        );

        let plaintext_max = 520_u64 * 1024 * 1024 * 1024;
        let manifest_max = 16_u64 * 1024 * 1024;
        let table_max = 80_u64 * 1024 * 1024;
        let fixed_framing = 120_u64;
        let payload_at_limit = plaintext_max
            .checked_sub(manifest_max + table_max + fixed_framing)
            .expect("bounded exact payload maximum");
        assert_eq!(
            validate_plaintext_container_length(manifest_max, table_max, payload_at_limit)
                .expect("accept exact plaintext-container cap"),
            plaintext_max
        );
        assert!(matches!(
            validate_plaintext_container_length(manifest_max, table_max, payload_at_limit + 1),
            Err(HeleosError::ResourceLimit)
        ));
        assert!(matches!(
            validate_plaintext_container_length(u64::MAX, 1, 1),
            Err(HeleosError::ResourceLimit)
        ));
    }

    #[test]
    fn entry_table_parser_rejects_unknown_kinds_duplicate_database_and_unsorted_blobs() {
        let record = |kind: u8, digest: u8, byte_length: u64| {
            let mut bytes = Vec::with_capacity(41);
            bytes.push(kind);
            bytes.extend_from_slice(&[digest; 32]);
            bytes.extend_from_slice(&byte_length.to_be_bytes());
            bytes
        };
        let database = record(0, 0x10, 3);
        let first_blob = record(1, 0x20, 1);
        let earlier_blob = record(1, 0x19, 1);

        let mut unknown_kind = database.clone();
        unknown_kind[0] = 2;
        let mut duplicate_database = database.clone();
        duplicate_database.extend_from_slice(&database);
        let mut descending = database.clone();
        descending.extend_from_slice(&first_blob);
        descending.extend_from_slice(&earlier_blob);
        let mut duplicate_blob = database.clone();
        duplicate_blob.extend_from_slice(&first_blob);
        duplicate_blob.extend_from_slice(&first_blob);

        for bytes in [
            unknown_kind,
            first_blob,
            duplicate_database,
            descending,
            duplicate_blob,
            vec![0; 40],
        ] {
            assert!(matches!(
                parse_entry_table(&bytes),
                Err(HeleosError::InvalidBackupContainer)
            ));
        }
        assert!(matches!(
            BackupEntryRecord::database(
                Sha256Digest::from_bytes([0x10; 32]),
                16 * 1024 * 1024 * 1024 + 1,
            ),
            Err(HeleosError::ResourceLimit)
        ));
        assert!(matches!(
            BackupEntryRecord::blob(Sha256Digest::from_bytes([0x20; 32]), 256 * 1024 * 1024 + 1,),
            Err(HeleosError::ResourceLimit)
        ));
    }

    #[test]
    fn plaintext_header_checks_trusted_signer_and_signature_before_table_claims() {
        let fixture = database_only_plaintext_container(b"db!");
        let parsed = parse_plaintext_container_header(
            Cursor::new(fixture.bytes.as_slice()),
            &fixture.signer,
        )
        .expect("parse valid plaintext header");
        assert_eq!(parsed.entry_table().records().len(), 1);

        let mut oversized_table = fixture.bytes.clone();
        oversized_table[fixture.table_length_offset..fixture.table_length_offset + 8]
            .copy_from_slice(&u64::MAX.to_be_bytes());
        let wrong_trusted_signer = [0x99; 32];
        assert!(matches!(
            parse_plaintext_container_header(
                Cursor::new(oversized_table.as_slice()),
                &wrong_trusted_signer,
            ),
            Err(HeleosError::BackupIntegrity)
        ));
        assert!(matches!(
            parse_plaintext_container_header(
                Cursor::new(oversized_table.as_slice()),
                &fixture.signer,
            ),
            Err(HeleosError::ResourceLimit)
        ));

        let mut bad_signature_and_table = oversized_table;
        bad_signature_and_table[fixture.signature_offset] ^= 1;
        assert!(matches!(
            parse_plaintext_container_header(
                Cursor::new(bad_signature_and_table.as_slice()),
                &fixture.signer,
            ),
            Err(HeleosError::BackupIntegrity)
        ));

        let mut altered_table = fixture.bytes.clone();
        altered_table[fixture.table_length_offset + 8 + 1] ^= 1;
        assert!(matches!(
            parse_plaintext_container_header(
                Cursor::new(altered_table.as_slice()),
                &fixture.signer,
            ),
            Err(HeleosError::BackupIntegrity)
        ));
    }

    #[test]
    fn plaintext_total_cap_is_checked_from_the_signed_manifest_before_table_io() {
        let signing_key = SigningKey::from_bytes(&[0x42; 32]);
        let signer = signing_key.verifying_key().to_bytes();
        let manifest = BackupManifestV1::new(
            Sha256Digest::hash_reader(signer.as_slice()).expect("hash signer"),
            Sha256Digest::from_bytes([0x22; 32]),
            82,
            2,
            520_u64 * 1024 * 1024 * 1024,
            BackupDatabaseDescriptorV1::new(Sha256Digest::from_bytes([0x33; 32]), 0)
                .expect("construct bounded database descriptor"),
        )
        .expect("construct individually bounded hostile manifest");
        let manifest_bytes = manifest.canonical_bytes().expect("encode hostile manifest");
        let signature =
            sign_manifest(&signing_key, &manifest_bytes).expect("sign hostile manifest");
        let mut bytes = Vec::new();
        bytes.extend_from_slice(b"HELEOSB1");
        bytes.extend_from_slice(
            &u64::try_from(manifest_bytes.len())
                .expect("bounded manifest")
                .to_be_bytes(),
        );
        bytes.extend_from_slice(&manifest_bytes);
        bytes.extend_from_slice(&signer);
        bytes.extend_from_slice(&signature);

        assert!(matches!(
            parse_plaintext_container_header(Cursor::new(bytes), &signer),
            Err(HeleosError::ResourceLimit)
        ));
    }

    #[test]
    fn plaintext_payload_verifier_rejects_short_long_hash_mismatch_and_trailing_bytes() {
        let fixture = database_only_plaintext_container(b"database payload");
        verify_plaintext_container(Cursor::new(fixture.bytes.as_slice()), &fixture.signer)
            .expect("verify exact plaintext container");

        let mut short = fixture.bytes.clone();
        short.pop();
        assert!(matches!(
            verify_plaintext_container(Cursor::new(short.as_slice()), &fixture.signer),
            Err(HeleosError::BackupIntegrity)
        ));

        let mut hash_mismatch = fixture.bytes.clone();
        hash_mismatch[fixture.payload_offset] ^= 1;
        assert!(matches!(
            verify_plaintext_container(Cursor::new(hash_mismatch.as_slice()), &fixture.signer),
            Err(HeleosError::BackupIntegrity)
        ));

        let mut trailing = fixture.bytes.clone();
        trailing.push(0);
        assert!(matches!(
            verify_plaintext_container(Cursor::new(trailing.as_slice()), &fixture.signer),
            Err(HeleosError::InvalidBackupContainer)
        ));
    }

    #[test]
    fn authenticated_manifest_record_and_extent_metadata_exist_only_after_payload_eof() {
        let fixture = database_only_plaintext_container(b"database payload");
        let mut plaintext = Cursor::new(fixture.bytes.as_slice());
        let authenticated = verify_plaintext_container(&mut plaintext, &fixture.signer)
            .expect("authenticate complete container before returning metadata");
        assert_eq!(authenticated.manifest_schema(), "heleos.backup-manifest/v1");
        assert_eq!(
            authenticated.signer_key_sha256(),
            Sha256Digest::hash_reader(fixture.signer.as_slice()).expect("hash signer")
        );
        assert_eq!(authenticated.entry_table_byte_length(), 41);
        assert_eq!(authenticated.entry_count(), 1);
        assert_eq!(authenticated.decoded_payload_byte_length(), 16);
        assert_eq!(authenticated.database_byte_length(), 16);
        assert_eq!(
            authenticated.database_sha256(),
            Sha256Digest::hash_reader(b"database payload".as_slice()).expect("hash database")
        );
        assert_eq!(
            authenticated.entry_table_sha256(),
            Sha256Digest::hash_reader(
                &fixture.bytes[fixture.table_length_offset + 8..fixture.payload_offset]
            )
            .expect("hash entry table")
        );
        assert_eq!(
            authenticated.plaintext_byte_length(),
            u64::try_from(fixture.bytes.len()).expect("bounded fixture")
        );
        let extents = authenticated.extents().collect::<Vec<_>>();
        assert_eq!(extents.len(), 1);
        assert_eq!(extents[0].byte_offset(), fixture.payload_offset as u64);
        assert_eq!(extents[0].record().kind(), BackupEntryKind::Database);
        assert_eq!(extents[0].record().byte_length, 16);

        let mut extracted = Vec::new();
        copy_authenticated_extent(&mut plaintext, extents[0], &mut extracted)
            .expect("copy exact authenticated database extent");
        assert_eq!(extracted, b"database payload");

        let mut hostile = fixture.bytes.clone();
        hostile.push(0);
        let mut hostile = Cursor::new(hostile);
        let mut must_remain_empty = Vec::new();
        let result = (|| {
            let authenticated = verify_plaintext_container(&mut hostile, &fixture.signer)?;
            let extent = authenticated
                .extents()
                .next()
                .ok_or(HeleosError::InvalidBackupContainer)?;
            copy_authenticated_extent(&mut hostile, extent, &mut must_remain_empty)?;
            Ok::<(), HeleosError>(())
        })();
        assert!(matches!(result, Err(HeleosError::InvalidBackupContainer)));
        assert!(
            must_remain_empty.is_empty(),
            "no extraction may begin before complete authentication and exact EOF"
        );
    }

    #[test]
    fn staged_plaintext_file_length_has_exact_limit_and_integrity_precedence() {
        const MAXIMUM: u64 = 520 * 1024 * 1024 * 1024;

        validate_staged_plaintext_file_length(MAXIMUM, MAXIMUM)
            .expect("accept exact authenticated maximum");
        assert!(matches!(
            validate_staged_plaintext_file_length(MAXIMUM + 1, MAXIMUM),
            Err(HeleosError::ResourceLimit)
        ));
        assert!(matches!(
            validate_staged_plaintext_file_length(MAXIMUM + 1, MAXIMUM + 1),
            Err(HeleosError::ResourceLimit)
        ));
        assert!(matches!(
            validate_staged_plaintext_file_length(41, 40),
            Err(HeleosError::BackupIntegrity)
        ));
        assert!(matches!(
            validate_staged_plaintext_file_length(39, 40),
            Err(HeleosError::BackupIntegrity)
        ));
    }

    #[test]
    fn allocation_granularity_rounding_and_node_costs_are_exact_and_checked() {
        assert!(matches!(
            AllocationGranularity::new(0),
            Err(HeleosError::PolicyDenied)
        ));
        let granularity = AllocationGranularity::new(4).expect("accept nonzero granularity");
        for (length, rounded, file_cost) in [
            (0, 0, 8),
            (1, 4, 8),
            (3, 4, 8),
            (4, 4, 8),
            (5, 8, 12),
            (8, 8, 12),
            (9, 12, 16),
        ] {
            assert_eq!(granularity.round_up(length).expect("round length"), rounded);
            assert_eq!(granularity.file(length).expect("cost file"), file_cost);
        }
        assert_eq!(granularity.directory().expect("cost directory"), 8);

        let two = AllocationGranularity::new(2).expect("accept granularity two");
        assert!(matches!(
            two.round_up(u64::MAX),
            Err(HeleosError::ResourceLimit)
        ));
        assert!(matches!(
            two.file(u64::MAX),
            Err(HeleosError::ResourceLimit)
        ));
        let too_large = AllocationGranularity::new(u64::MAX / 2 + 1)
            .expect("nonzero granularity is structurally valid");
        assert!(matches!(
            too_large.directory(),
            Err(HeleosError::ResourceLimit)
        ));
    }

    #[test]
    fn normal_capacity_reserve_is_max_twenty_gib_or_ceil_ten_percent() {
        const GIB: u64 = 1024 * 1024 * 1024;
        assert!(matches!(
            normal_capacity_reserve(0),
            Err(HeleosError::PolicyDenied)
        ));
        assert_eq!(
            normal_capacity_reserve(1).expect("reserve tiny volume"),
            20 * GIB
        );
        assert_eq!(
            normal_capacity_reserve(200 * GIB).expect("reserve exact boundary"),
            20 * GIB
        );
        assert_eq!(
            normal_capacity_reserve(200 * GIB + 1).expect("ceil above boundary"),
            20 * GIB + 1
        );
        assert_eq!(
            normal_capacity_reserve(u64::MAX).expect("reserve maximum reported volume"),
            u64::MAX / 10 + 1
        );
    }

    #[test]
    fn predecrypt_capacity_matches_the_exact_worst_case_formula() {
        const GIB: u64 = 1024 * 1024 * 1024;
        let granularity = AllocationGranularity::new(4).expect("accept granularity");
        let required = predecrypt_capacity_required(granularity, 9, 1)
            .expect("compute exact predecrypt requirement");
        assert_eq!(required, 16_526_428 + 20 * GIB);

        assert!(matches!(
            predecrypt_capacity_required(granularity, u64::MAX, 0),
            Err(HeleosError::ResourceLimit)
        ));
        assert!(matches!(
            predecrypt_capacity_required(granularity, 0, 0),
            Err(HeleosError::PolicyDenied)
        ));
        assert!(matches!(
            predecrypt_capacity_required(
                AllocationGranularity::new(u64::MAX).expect("accept nonzero granularity"),
                0,
                1,
            ),
            Err(HeleosError::PolicyDenied)
        ));
        let body_and_name_multiply_overflow = u64::MAX / (2 * 2_000_001) + 1;
        assert!(matches!(
            predecrypt_capacity_required(
                AllocationGranularity::new(body_and_name_multiply_overflow)
                    .expect("accept nonzero granularity"),
                0,
                1,
            ),
            Err(HeleosError::PolicyDenied)
        ));
    }

    #[test]
    fn exact_restore_work_derives_sorted_shards_and_node_deltas_in_one_pass() {
        let granularity = AllocationGranularity::new(1).expect("accept granularity");
        let empty = exact_restore_work(granularity, 0, std::iter::empty::<(Sha256Digest, u64)>())
            .expect("cost empty decoded tree");
        assert_eq!(empty.decoded_tree(), 16);
        assert_eq!(empty.database_byte_length(), 0);
        assert_eq!(empty.blob_count(), 0);
        assert_eq!(empty.first_level_shard_count(), 0);
        assert_eq!(empty.second_level_shard_count(), 0);

        let one_empty_blob = exact_restore_work(
            granularity,
            0,
            [(sharded_digest(0x10, 0x20, 0), 0)].into_iter(),
        )
        .expect("cost one empty blob");
        assert_eq!(one_empty_blob.decoded_tree(), 22);
        assert_eq!(one_empty_blob.blob_count(), 1);
        assert_eq!(one_empty_blob.first_level_shard_count(), 1);
        assert_eq!(one_empty_blob.second_level_shard_count(), 1);

        let same_shard = exact_restore_work(
            granularity,
            0,
            [
                (sharded_digest(0x10, 0x20, 0), 0),
                (sharded_digest(0x10, 0x20, 1), 0),
            ]
            .into_iter(),
        )
        .expect("cost same-shard blob");
        assert_eq!(same_shard.decoded_tree(), 24);
        assert_eq!(same_shard.first_level_shard_count(), 1);
        assert_eq!(same_shard.second_level_shard_count(), 1);

        let new_second_level = exact_restore_work(
            granularity,
            0,
            [
                (sharded_digest(0x10, 0x20, 0), 0),
                (sharded_digest(0x10, 0x21, 0), 0),
            ]
            .into_iter(),
        )
        .expect("cost second-level shard delta");
        assert_eq!(new_second_level.decoded_tree(), 26);
        assert_eq!(new_second_level.first_level_shard_count(), 1);
        assert_eq!(new_second_level.second_level_shard_count(), 2);

        let new_first_and_second = exact_restore_work(
            granularity,
            0,
            [
                (sharded_digest(0x10, 0xff, 0), 0),
                (sharded_digest(0x11, 0x00, 0), 0),
            ]
            .into_iter(),
        )
        .expect("cost two-level shard delta");
        assert_eq!(new_first_and_second.decoded_tree(), 28);
        assert_eq!(new_first_and_second.first_level_shard_count(), 2);
        assert_eq!(new_first_and_second.second_level_shard_count(), 2);

        let dense = exact_restore_work(
            granularity,
            0,
            (0..65_536_usize).map(|index| (sharded_digest((index >> 8) as u8, index as u8, 0), 0)),
        )
        .expect("cost dense shard boundary");
        assert_eq!(dense.blob_count(), 65_536);
        assert_eq!(dense.first_level_shard_count(), 256);
        assert_eq!(dense.second_level_shard_count(), 65_536);
        assert_eq!(
            dense.decoded_tree(),
            (65_536 + 3) * 2 + (5 + 256 + 65_536) * 2
        );

        assert!(matches!(
            exact_restore_work(
                granularity,
                0,
                [
                    (sharded_digest(0x10, 0x20, 0), 0),
                    (sharded_digest(0x10, 0x20, 0), 0),
                ]
                .into_iter(),
            ),
            Err(HeleosError::InvalidBackupContainer)
        ));
        assert!(matches!(
            exact_restore_work(
                granularity,
                0,
                [
                    (sharded_digest(0x10, 0x21, 0), 0),
                    (sharded_digest(0x10, 0x20, 0), 0),
                ]
                .into_iter(),
            ),
            Err(HeleosError::InvalidBackupContainer)
        ));
        assert!(matches!(
            exact_restore_work(
                granularity,
                0,
                [(sharded_digest(0x10, 0x20, 0), 256 * 1024 * 1024 + 1,)].into_iter(),
            ),
            Err(HeleosError::ResourceLimit)
        ));
    }

    #[test]
    fn exact_restore_work_lazily_accepts_two_million_and_rejects_the_next_count() {
        let granularity = AllocationGranularity::new(1).expect("accept granularity");
        let accepted_seen = Cell::new(0_usize);
        let accepted = exact_restore_work(
            granularity,
            0,
            (0..2_000_000_usize)
                .map(|index| (sharded_digest(0, 0, index as u64), 0))
                .inspect(|_| accepted_seen.set(accepted_seen.get() + 1)),
        )
        .expect("accept exact maximum lazy blob count");
        assert_eq!(accepted_seen.get(), 2_000_000);
        assert_eq!(accepted.blob_count(), 2_000_000);
        assert_eq!(accepted.first_level_shard_count(), 1);
        assert_eq!(accepted.second_level_shard_count(), 1);
        assert_eq!(accepted.decoded_tree(), 4_000_020);

        let rejected_seen = Cell::new(0_usize);
        assert!(matches!(
            exact_restore_work(
                granularity,
                0,
                (0..2_000_001_usize)
                    .map(|index| (sharded_digest(0, 0, index as u64), 0))
                    .inspect(|_| rejected_seen.set(rejected_seen.get() + 1)),
            ),
            Err(HeleosError::ResourceLimit)
        ));
        assert_eq!(rejected_seen.get(), 0, "reject count before iteration");
    }

    #[test]
    fn post_auth_tree_scratch_and_volume_combining_fail_closed_on_every_boundary() {
        let granularity = AllocationGranularity::new(4_096).expect("accept granularity");
        let empty_work = || {
            exact_restore_work(granularity, 0, std::iter::empty::<(Sha256Digest, u64)>())
                .expect("cost zero-length decoded tree")
        };
        let tree = empty_work().decoded_tree();
        assert_eq!(tree, 65_536);
        let scratch = store_scratch_capacity(granularity, 0).expect("cost Store scratch");
        assert_eq!(scratch, 1_073_758_208);

        let shared = post_authentication_capacity(
            empty_work(),
            VolumeLayout::new(
                RetainedVolumeRelation::Same,
                1,
                AllocationGranularity::new(8_192)
                    .expect("accept deliberately different temporary granularity"),
                200 * 1024 * 1024 * 1024 + 1,
            ),
        )
        .expect("combine shared-volume requirement");
        assert_eq!(
            shared.staging_required(),
            tree + scratch + 20 * 1024 * 1024 * 1024,
            "one shared volume has one reserve"
        );
        assert_eq!(shared.temporary_required(), None);
        shared
            .require_available(shared.staging_required(), None)
            .expect("exact shared equality admits");
        assert!(matches!(
            shared.require_available(shared.staging_required() - 1, None),
            Err(HeleosError::PolicyDenied)
        ));

        let distinct = post_authentication_capacity(
            empty_work(),
            VolumeLayout::new(
                RetainedVolumeRelation::Distinct,
                1,
                AllocationGranularity::new(8_192).expect("accept distinct temporary granularity"),
                200 * 1024 * 1024 * 1024 + 1,
            ),
        )
        .expect("combine distinct-volume requirements");
        assert_eq!(distinct.staging_required(), tree + 20 * 1024 * 1024 * 1024);
        let distinct_scratch = store_scratch_capacity(
            AllocationGranularity::new(8_192).expect("accept temporary granularity"),
            0,
        )
        .expect("cost distinct Store scratch");
        assert_eq!(
            distinct.temporary_required(),
            Some(distinct_scratch + 20 * 1024 * 1024 * 1024 + 1)
        );
        distinct
            .require_available(distinct.staging_required(), distinct.temporary_required())
            .expect("exact distinct equality admits");
        assert!(matches!(
            distinct.require_available(distinct.staging_required() - 1, Some(u64::MAX)),
            Err(HeleosError::PolicyDenied)
        ));
        assert!(matches!(
            distinct.require_available(
                distinct.staging_required(),
                Some(
                    distinct
                        .temporary_required()
                        .expect("temporary requirement")
                        - 1
                ),
            ),
            Err(HeleosError::PolicyDenied)
        ));
        assert!(matches!(
            distinct.require_available(distinct.staging_required(), None),
            Err(HeleosError::PolicyDenied)
        ));

        assert!(matches!(
            exact_restore_work(
                AllocationGranularity::new(2).expect("accept granularity"),
                u64::MAX,
                std::iter::empty::<(Sha256Digest, u64)>(),
            ),
            Err(HeleosError::ResourceLimit)
        ));
        assert!(matches!(
            exact_restore_work(
                AllocationGranularity::new(2).expect("accept granularity"),
                0,
                [(sharded_digest(0, 0, 0), u64::MAX)].into_iter(),
            ),
            Err(HeleosError::ResourceLimit)
        ));
        assert!(matches!(
            post_authentication_capacity(
                empty_work(),
                VolumeLayout::new(RetainedVolumeRelation::Same, 0, granularity, 1),
            ),
            Err(HeleosError::PolicyDenied)
        ));
        assert!(matches!(
            exact_restore_work(
                AllocationGranularity::new(u64::MAX / 9)
                    .expect("accept nonzero hostile capacity stat"),
                0,
                std::iter::empty::<(Sha256Digest, u64)>(),
            ),
            Err(HeleosError::PolicyDenied)
        ));
        assert!(matches!(
            store_scratch_capacity(
                AllocationGranularity::new(u64::MAX / 4)
                    .expect("accept overflowing scratch granularity"),
                0,
            ),
            Err(HeleosError::PolicyDenied)
        ));
        assert!(matches!(
            post_authentication_capacity(
                exact_restore_work(
                    AllocationGranularity::new(u64::MAX / 20)
                        .expect("accept overflowing shared capacity granularity"),
                    0,
                    std::iter::empty::<(Sha256Digest, u64)>(),
                )
                .expect("individual restore-tree terms fit"),
                VolumeLayout::new(RetainedVolumeRelation::Same, 1, granularity, 1),
            ),
            Err(HeleosError::PolicyDenied)
        ));
        assert!(matches!(
            exact_restore_work(
                granularity,
                0,
                (0..2_081_usize)
                    .map(|index| (sharded_digest(0, 0, index as u64), 256 * 1024 * 1024)),
            ),
            Err(HeleosError::ResourceLimit)
        ));
    }

    #[test]
    fn unknown_relation_uses_the_safe_union_with_no_pooling_and_different_granularities() {
        const GIB: u64 = 1024 * 1024 * 1024;

        let staging_granularity =
            AllocationGranularity::new(4_096).expect("accept staging granularity");
        let temporary_granularity =
            AllocationGranularity::new(8_192).expect("accept temporary granularity");
        let work = exact_restore_work(
            staging_granularity,
            0,
            std::iter::empty::<(Sha256Digest, u64)>(),
        )
        .expect("derive exact empty restore work");
        let decoded_tree = work.decoded_tree();
        let staging_scratch =
            store_scratch_capacity(staging_granularity, 0).expect("cost staging scratch");
        let temporary_scratch =
            store_scratch_capacity(temporary_granularity, 0).expect("cost temporary scratch");
        assert_ne!(staging_scratch, temporary_scratch);

        let unknown = post_authentication_capacity(
            work,
            VolumeLayout::new(
                RetainedVolumeRelation::Unknown,
                1,
                temporary_granularity,
                200 * GIB + 1,
            ),
        )
        .expect("derive Unknown union witness");
        assert_eq!(
            unknown.staging_required(),
            decoded_tree + staging_scratch + 20 * GIB
        );
        assert_eq!(
            unknown.temporary_required(),
            Some(temporary_scratch + 20 * GIB + 1)
        );
        unknown
            .require_available(unknown.staging_required(), unknown.temporary_required())
            .expect("exact Unknown equality admits");
        assert!(matches!(
            unknown.require_available(unknown.staging_required() - 1, Some(u64::MAX)),
            Err(HeleosError::PolicyDenied)
        ));
        assert!(matches!(
            unknown.require_available(
                u64::MAX,
                Some(unknown.temporary_required().expect("temporary requirement") - 1),
            ),
            Err(HeleosError::PolicyDenied)
        ));

        let unsafe_distinct = remaining_work_capacity_requirements(
            RetainedVolumeRelation::Distinct,
            70,
            70,
            20,
            70,
            20,
        )
        .expect("derive unsafe Distinct counterexample");
        unsafe_distinct
            .require_available(100, Some(100))
            .expect("the unsafe Distinct substitution would admit both 90-byte checks");

        let safe_unknown = remaining_work_capacity_requirements(
            RetainedVolumeRelation::Unknown,
            70,
            70,
            20,
            70,
            20,
        )
        .expect("derive safe Unknown counterexample");
        assert_eq!(safe_unknown.staging_required(), 160);
        assert_eq!(safe_unknown.temporary_required(), Some(90));
        assert!(matches!(
            safe_unknown.require_available(100, Some(100)),
            Err(HeleosError::PolicyDenied)
        ));
        safe_unknown
            .require_available(160, Some(90))
            .expect("exact counterexample equality admits");
        assert!(matches!(
            remaining_work_capacity_requirements(
                RetainedVolumeRelation::Unknown,
                u64::MAX,
                1,
                0,
                0,
                0,
            ),
            Err(HeleosError::PolicyDenied)
        ));
        assert!(matches!(
            remaining_work_capacity_requirements(
                RetainedVolumeRelation::Unknown,
                u64::MAX,
                0,
                1,
                0,
                0,
            ),
            Err(HeleosError::PolicyDenied)
        ));
        assert!(matches!(
            remaining_work_capacity_requirements(
                RetainedVolumeRelation::Unknown,
                0,
                0,
                0,
                u64::MAX,
                1,
            ),
            Err(HeleosError::PolicyDenied)
        ));
    }

    #[test]
    fn retained_volume_relation_gate_accepts_only_exact_stability_and_never_infers() {
        for relation in [
            RetainedVolumeRelation::Same,
            RetainedVolumeRelation::Distinct,
            RetainedVolumeRelation::Unknown,
        ] {
            assert_eq!(
                require_stable_retained_volume_relation(Ok(relation), Ok(relation))
                    .expect("accept stable opaque relation"),
                relation
            );
        }

        for (before, after) in [
            (
                RetainedVolumeRelation::Same,
                RetainedVolumeRelation::Distinct,
            ),
            (
                RetainedVolumeRelation::Distinct,
                RetainedVolumeRelation::Same,
            ),
            (
                RetainedVolumeRelation::Same,
                RetainedVolumeRelation::Unknown,
            ),
            (
                RetainedVolumeRelation::Unknown,
                RetainedVolumeRelation::Same,
            ),
            (
                RetainedVolumeRelation::Distinct,
                RetainedVolumeRelation::Unknown,
            ),
            (
                RetainedVolumeRelation::Unknown,
                RetainedVolumeRelation::Distinct,
            ),
        ] {
            assert!(matches!(
                require_stable_retained_volume_relation(Ok(before), Ok(after)),
                Err(HeleosError::PolicyDenied)
            ));
        }

        for (before, after) in [
            (
                Err(RetainedVolumeRelationError::Unsupported),
                Ok(RetainedVolumeRelation::Same),
            ),
            (
                Ok(RetainedVolumeRelation::Same),
                Err(RetainedVolumeRelationError::Unsupported),
            ),
            (
                Err(RetainedVolumeRelationError::Io(std::io::Error::other(
                    "before comparator fixture",
                ))),
                Ok(RetainedVolumeRelation::Unknown),
            ),
            (
                Ok(RetainedVolumeRelation::Unknown),
                Err(RetainedVolumeRelationError::Io(std::io::Error::new(
                    std::io::ErrorKind::PermissionDenied,
                    "after comparator fixture",
                ))),
            ),
        ] {
            assert!(matches!(
                require_stable_retained_volume_relation(before, after),
                Err(HeleosError::PolicyDenied)
            ));
        }

        let equal_path_data = (PathBuf::from("same-anchor"), PathBuf::from("same-anchor"));
        let equal_statistics = ((100_u64, 100_u64, 4_096_u64), (100, 100, 4_096));
        assert_eq!(equal_path_data.0, equal_path_data.1);
        assert_eq!(equal_statistics.0, equal_statistics.1);
        assert_eq!(
            require_stable_retained_volume_relation(
                Ok(RetainedVolumeRelation::Unknown),
                Ok(RetainedVolumeRelation::Unknown),
            )
            .expect("equal path/stat fixtures cannot upgrade opaque Unknown"),
            RetainedVolumeRelation::Unknown
        );

        let equal_granularity =
            AllocationGranularity::new(4_096).expect("accept equal statistics granularity");
        let equal_work = exact_restore_work(
            equal_granularity,
            0,
            std::iter::empty::<(Sha256Digest, u64)>(),
        )
        .expect("derive equal-statistics restore work");
        let equal_tree = equal_work.decoded_tree();
        let equal_scratch =
            store_scratch_capacity(equal_granularity, 0).expect("cost equal scratch");
        let equal_unknown = post_authentication_capacity(
            equal_work,
            VolumeLayout::new(RetainedVolumeRelation::Unknown, 1, equal_granularity, 1),
        )
        .expect("equal statistics must retain Unknown arithmetic");
        assert_eq!(
            equal_unknown.staging_required(),
            equal_tree + equal_scratch + 20 * 1024 * 1024 * 1024
        );
        assert_eq!(
            equal_unknown.temporary_required(),
            Some(equal_scratch + 20 * 1024 * 1024 * 1024)
        );

        let granularity = AllocationGranularity::new(4).expect("accept granularity");
        assert_eq!(
            predecrypt_capacity_required(granularity, 9, 1)
                .expect("stable Unknown does not alter initial staging-only admission"),
            16_526_428 + 20 * 1024 * 1024 * 1024
        );
    }

    #[test]
    fn reserve_only_unknown_and_distinct_require_both_anchors_without_pooling() {
        require_reserve_only_capacity(RetainedVolumeRelation::Same, 20, 20, 0, u64::MAX)
            .expect("Same uses one staging reserve");

        for relation in [
            RetainedVolumeRelation::Distinct,
            RetainedVolumeRelation::Unknown,
        ] {
            require_reserve_only_capacity(relation, 20, 20, 30, 30)
                .expect("exact independent reserve equality admits");
            assert!(matches!(
                require_reserve_only_capacity(relation, 19, 20, u64::MAX, 30),
                Err(HeleosError::PolicyDenied)
            ));
            assert!(matches!(
                require_reserve_only_capacity(relation, u64::MAX, 20, 29, 30),
                Err(HeleosError::PolicyDenied)
            ));
        }
    }

    #[test]
    fn staging_error_classifiers_preserve_exact_provenance_and_boundaries() {
        for kind in [
            std::io::ErrorKind::StorageFull,
            std::io::ErrorKind::QuotaExceeded,
        ] {
            assert!(matches!(
                classify_direct_staging_io(std::io::Error::new(kind, "storage fixture")),
                HeleosError::PolicyDenied
            ));
            assert!(matches!(
                classify_staging_orchestration_error(StagingOrchestrationError::StoreOrVault(
                    HeleosError::Io(std::io::Error::new(kind, "orchestration fixture",)),
                )),
                HeleosError::PolicyDenied
            ));
        }

        for kind in [
            std::io::ErrorKind::Other,
            std::io::ErrorKind::PermissionDenied,
            std::io::ErrorKind::InvalidData,
            std::io::ErrorKind::InvalidInput,
        ] {
            match classify_direct_staging_io(std::io::Error::new(kind, "ordinary fixture")) {
                HeleosError::Io(error) => assert_eq!(error.kind(), kind),
                error => panic!("ordinary direct I/O was reclassified: {error:?}"),
            }
            match classify_staging_orchestration_error(StagingOrchestrationError::StoreOrVault(
                HeleosError::Io(std::io::Error::new(kind, "ordinary orchestration fixture")),
            )) {
                HeleosError::Io(error) => assert_eq!(error.kind(), kind),
                error => panic!("ordinary orchestration I/O was reclassified: {error:?}"),
            }
        }

        #[cfg(unix)]
        {
            for raw in [libc::ENOSPC, libc::EDQUOT] {
                assert!(matches!(
                    classify_direct_staging_io(std::io::Error::from_raw_os_error(raw)),
                    HeleosError::PolicyDenied
                ));
            }
            match classify_direct_staging_io(std::io::Error::from_raw_os_error(libc::EIO)) {
                HeleosError::Io(error) => assert_eq!(error.raw_os_error(), Some(libc::EIO)),
                error => panic!("EIO was reclassified: {error:?}"),
            }
        }

        assert!(matches!(
            classify_staging_orchestration_error(StagingOrchestrationError::StoreOrVault(
                HeleosError::ResourceLimit,
            )),
            HeleosError::ResourceLimit
        ));
        assert!(matches!(
            classify_staging_orchestration_error(StagingOrchestrationError::StagingResourceLimit(
                StagingResourceLimit::from_staging_helper(),
            )),
            HeleosError::PolicyDenied
        ));
        assert!(matches!(
            classify_staging_orchestration_error(StagingOrchestrationError::StoreOrVault(
                HeleosError::Database,
            )),
            HeleosError::Database
        ));
        assert!(matches!(
            map_hostile_parser_error(HeleosError::ResourceLimit),
            HeleosError::InvalidBackupContainer
        ));
        assert!(matches!(
            map_hostile_parser_error(HeleosError::BackupIntegrity),
            HeleosError::BackupIntegrity
        ));
        match map_hostile_parser_error(HeleosError::Io(std::io::Error::new(
            std::io::ErrorKind::Other,
            "hostile parser I/O",
        ))) {
            HeleosError::Io(error) => assert_eq!(error.kind(), std::io::ErrorKind::Other),
            error => panic!("hostile boundary reclassified ordinary I/O: {error:?}"),
        }

        let disk_full = rusqlite::Error::SqliteFailure(
            rusqlite::ffi::Error::new(rusqlite::ffi::SQLITE_FULL),
            Some("direct online backup full".to_owned()),
        );
        assert!(matches!(
            map_backup_sqlite_error(disk_full),
            HeleosError::ResourceLimit
        ));
    }

    #[test]
    fn windows_directory_sync_and_binding_policy_are_exact() {
        for raw in [1, 5, 6] {
            super::staging_error::classify_windows_directory_sync_result_for_test(Err(
                std::io::Error::from_raw_os_error(raw),
            ))
            .expect("governed unsupported Windows directory sync is best effort");
        }
        match super::staging_error::classify_windows_directory_sync_result_for_test(Err(
            std::io::Error::from_raw_os_error(50),
        )) {
            Err(HeleosError::Io(error)) => assert_eq!(error.raw_os_error(), Some(50)),
            result => panic!("unapproved Windows directory-sync result changed: {result:?}"),
        }
        assert!(matches!(
            super::staging_error::classify_windows_directory_sync_result_for_test(Err(
                std::io::Error::new(std::io::ErrorKind::StorageFull, "directory full"),
            )),
            Err(HeleosError::PolicyDenied)
        ));

        let options = super::staging_owner::binding_directory_options_for_test();
        let configured = format!("{options:?}");
        assert!(configured.contains("follow: No"));
        assert!(configured.contains("maybe_dir: false"));
    }

    #[test]
    fn staging_writer_classifies_only_its_own_storage_exhaustion() {
        struct StorageFullWriter;

        impl Write for StorageFullWriter {
            fn write(&mut self, _: &[u8]) -> std::io::Result<usize> {
                Err(std::io::Error::new(
                    std::io::ErrorKind::StorageFull,
                    "staging writer full",
                ))
            }

            fn flush(&mut self) -> std::io::Result<()> {
                Ok(())
            }
        }

        let mut staging = StagingMutationWriter::new(StorageFullWriter);
        let write_result = staging.write_all(b"x").map_err(HeleosError::Io);
        assert!(matches!(
            staging.classify_result(write_result),
            Err(HeleosError::PolicyDenied)
        ));

        let idle = StagingMutationWriter::new(Vec::<u8>::new());
        match idle.classify_result::<()>(Err(HeleosError::Io(std::io::Error::new(
            std::io::ErrorKind::StorageFull,
            "non-staging reader full",
        )))) {
            Err(HeleosError::Io(error)) => {
                assert_eq!(error.kind(), std::io::ErrorKind::StorageFull)
            }
            result => panic!("non-staging error was reclassified: {result:?}"),
        }
    }

    #[test]
    fn staging_writer_classifier_has_one_private_control_location() {
        let archive_source = include_str!("archive.rs");
        let backup_module_source = include_str!("mod.rs");
        assert!(!archive_source.contains("struct StagingMutationWriter"));
        assert!(backup_module_source.contains("struct StagingMutationWriter"));
    }

    #[test]
    fn plaintext_writer_matches_the_exact_database_only_golden_container() {
        let expected = database_only_plaintext_container(b"database payload");
        let signing_key = SigningKey::from_bytes(&[0x42; 32]);
        let database_digest =
            Sha256Digest::hash_reader(b"database payload".as_slice()).expect("hash database");
        let record_plan = BackupRecordPlan::new(vec![
            BackupEntryRecord::database(database_digest, 16)
                .expect("construct database payload record"),
        ])
        .expect("construct database-only record plan");
        let mut actual = Vec::new();

        write_plaintext_container(&mut actual, &signing_key, &record_plan, |_, _| {
            Ok(Cursor::new(b"database payload".to_vec()))
        })
        .expect("write plaintext container");

        assert_eq!(actual, expected.bytes);
    }

    #[test]
    fn record_opener_copy_independently_rejects_short_long_and_hash_mismatch() {
        let signing_key = SigningKey::from_bytes(&[0x42; 32]);
        let expected_digest = Sha256Digest::hash_reader(b"abc".as_slice()).expect("hash payload");

        for payload in [b"ab".to_vec(), b"abcd".to_vec(), b"abd".to_vec()] {
            let record_plan = BackupRecordPlan::new(vec![
                BackupEntryRecord::database(expected_digest, 3)
                    .expect("construct database payload record"),
            ])
            .expect("construct database-only record plan");
            let mut output = Vec::new();
            assert!(matches!(
                write_plaintext_container(&mut output, &signing_key, &record_plan, |_, _| {
                    Ok(Cursor::new(payload.clone()))
                }),
                Err(HeleosError::Integrity)
            ));
        }
    }

    #[test]
    fn authenticated_extent_cursor_copies_database_and_sorted_blobs_once_in_forward_order() {
        let signing_key = SigningKey::from_bytes(&[0x42; 32]);
        let signer = signing_key.verifying_key().to_bytes();
        let database = b"database".to_vec();
        let mut blobs = [b"second blob".to_vec(), b"first blob".to_vec()]
            .into_iter()
            .map(|bytes| {
                let digest = Sha256Digest::hash_reader(bytes.as_slice()).expect("hash blob");
                (digest, bytes)
            })
            .collect::<Vec<_>>();
        blobs.sort_unstable_by_key(|(digest, _)| *digest);

        let mut payloads = vec![database.clone()];
        payloads.extend(blobs.iter().map(|(_, bytes)| bytes.clone()));
        let records = std::iter::once(
            BackupEntryRecord::database(
                Sha256Digest::hash_reader(database.as_slice()).expect("hash database"),
                u64::try_from(database.len()).expect("bounded database"),
            )
            .expect("construct database record"),
        )
        .chain(blobs.iter().map(|(digest, bytes)| {
            BackupEntryRecord::blob(*digest, u64::try_from(bytes.len()).expect("bounded blob"))
                .expect("construct blob record")
        }))
        .collect::<Vec<_>>();
        let record_plan = BackupRecordPlan::new(records.clone()).expect("construct record plan");
        let mut container = Vec::new();
        write_plaintext_container(&mut container, &signing_key, &record_plan, |index, _| {
            Ok(Cursor::new(payloads[index].clone()))
        })
        .expect("write three-extent container");

        let mut plaintext = Cursor::new(container.clone());
        let authenticated = verify_plaintext_container(&mut plaintext, &signer)
            .expect("authenticate all three extents");
        let mut copied = Vec::new();
        let mut expected_offset = authenticated
            .plaintext_byte_length()
            .checked_sub(authenticated.decoded_payload_byte_length())
            .expect("authenticated framing precedes payload");
        for (expected_record, extent) in records.iter().zip(authenticated.extents()) {
            assert_eq!(extent.record(), *expected_record);
            assert_eq!(extent.byte_offset(), expected_offset);
            let mut bytes = Vec::new();
            copy_authenticated_extent(&mut plaintext, extent, &mut bytes)
                .expect("copy one already-yielded extent");
            expected_offset = expected_offset
                .checked_add(extent.record().byte_length)
                .expect("bounded extent offset");
            copied.push(bytes);
        }
        assert_eq!(copied, payloads);

        let mut corrupt_last_extent = container;
        *corrupt_last_extent.last_mut().expect("last payload byte") ^= 1;
        let mut corrupt_last_extent = Cursor::new(corrupt_last_extent);
        let mut extraction_attempts = 0_u32;
        let result = (|| {
            let authenticated = verify_plaintext_container(&mut corrupt_last_extent, &signer)?;
            for extent in authenticated.extents() {
                extraction_attempts = extraction_attempts
                    .checked_add(1)
                    .ok_or(HeleosError::ResourceLimit)?;
                copy_authenticated_extent(&mut corrupt_last_extent, extent, &mut std::io::sink())?;
            }
            Ok::<(), HeleosError>(())
        })();
        assert!(matches!(result, Err(HeleosError::BackupIntegrity)));
        assert_eq!(
            extraction_attempts, 0,
            "last-extent corruption must prevent authenticated metadata and all extraction"
        );
    }

    #[test]
    fn one_hundred_thousand_record_plan_opens_at_most_one_reader_at_a_time() {
        struct TrackedReader {
            reader: Cursor<Vec<u8>>,
            live: Rc<Cell<usize>>,
        }

        impl TrackedReader {
            fn new(bytes: Vec<u8>, live: &Rc<Cell<usize>>, maximum_live: &Rc<Cell<usize>>) -> Self {
                let next = live.get().checked_add(1).expect("bounded live readers");
                live.set(next);
                maximum_live.set(maximum_live.get().max(next));
                Self {
                    reader: Cursor::new(bytes),
                    live: Rc::clone(live),
                }
            }
        }

        impl Read for TrackedReader {
            fn read(&mut self, buffer: &mut [u8]) -> std::io::Result<usize> {
                self.reader.read(buffer)
            }
        }

        impl Drop for TrackedReader {
            fn drop(&mut self) {
                self.live.set(
                    self.live
                        .get()
                        .checked_sub(1)
                        .expect("tracked reader must be live"),
                );
            }
        }

        const BLOB_COUNT: usize = 100_000;
        const RECORD_COUNT: usize = BLOB_COUNT + 1;
        let database = b"db".to_vec();
        let mut blob_payloads = (0_u32..u32::try_from(BLOB_COUNT).expect("bounded count"))
            .map(|index| {
                let bytes = index.to_be_bytes().to_vec();
                let digest = Sha256Digest::hash_reader(bytes.as_slice()).expect("hash blob");
                (digest, bytes)
            })
            .collect::<Vec<_>>();
        blob_payloads.sort_unstable_by_key(|(digest, _)| *digest);
        assert!(blob_payloads.windows(2).all(|pair| pair[0].0 < pair[1].0));

        let mut payloads = Vec::with_capacity(RECORD_COUNT);
        payloads.push(database.clone());
        payloads.extend(blob_payloads.iter().map(|(_, bytes)| bytes.clone()));
        let mut records = Vec::with_capacity(RECORD_COUNT);
        records.push(
            BackupEntryRecord::database(
                Sha256Digest::hash_reader(database.as_slice()).expect("hash database"),
                u64::try_from(database.len()).expect("bounded database"),
            )
            .expect("construct database record"),
        );
        records.extend(blob_payloads.iter().map(|(digest, bytes)| {
            BackupEntryRecord::blob(*digest, u64::try_from(bytes.len()).expect("bounded blob"))
                .expect("construct blob record")
        }));
        let record_plan = BackupRecordPlan::new(records).expect("construct large record plan");
        let live = Rc::new(Cell::new(0_usize));
        let maximum_live = Rc::new(Cell::new(0_usize));
        let opened = Rc::new(Cell::new(0_usize));
        let mut plaintext = Vec::new();

        write_plaintext_container(
            &mut plaintext,
            &SigningKey::from_bytes(&[0x42; 32]),
            &record_plan,
            {
                let live = Rc::clone(&live);
                let maximum_live = Rc::clone(&maximum_live);
                let opened = Rc::clone(&opened);
                move |index, record| {
                    assert_eq!(
                        Sha256Digest::hash_reader(payloads[index].as_slice())
                            .expect("hash opened payload"),
                        record.sha256
                    );
                    opened.set(opened.get().checked_add(1).expect("bounded open count"));
                    Ok(TrackedReader::new(
                        payloads[index].clone(),
                        &live,
                        &maximum_live,
                    ))
                }
            },
        )
        .expect("emit large record plan sequentially");

        assert_eq!(opened.get(), RECORD_COUNT);
        assert_eq!(maximum_live.get(), 1);
        assert_eq!(live.get(), 0);
    }

    #[test]
    fn age_stream_finishes_empty_and_multichunk_ciphertexts() {
        let identity = age::x25519::Identity::generate();
        let recipient = identity.to_public();
        for plaintext in [Vec::new(), vec![0x5a; 192 * 1024 + 17]] {
            let ciphertext = encrypt_age_stream(
                Cursor::new(plaintext.as_slice()),
                Vec::new(),
                &recipient,
                u64::try_from(plaintext.len()).expect("bounded plaintext"),
            )
            .expect("encrypt and finish age stream");
            let decryptor = age::Decryptor::new(ciphertext.as_slice()).expect("parse ciphertext");
            let mut decrypted = decryptor
                .decrypt(std::iter::once(&identity as &dyn age::Identity))
                .expect("decrypt ciphertext");
            let mut actual = Vec::new();
            decrypted
                .read_to_end(&mut actual)
                .expect("reach ciphertext integrity EOF");
            assert_eq!(actual, plaintext);
        }
    }

    #[test]
    fn verified_plaintext_is_encrypted_and_owned_staging_cleans_on_drop() {
        let root = tempfile::tempdir().expect("create encryption staging parent");
        apply_private_permissions(root.path()).expect("harden encryption staging parent");
        let staging_parent = fs::canonicalize(root.path()).expect("canonicalize staging parent");
        let parent = fs::File::open(&staging_parent).expect("retain encryption staging parent");
        let (plaintext, ciphertext) = create_owned_backup_file_pair(&parent)
            .expect("create direct-sibling backup staging pair");
        let plaintext_name = plaintext.name_for_test().to_os_string();
        let ciphertext_name = ciphertext.name_for_test().to_os_string();
        let identity = age::x25519::Identity::generate();
        let recipient = identity.to_public();
        let signing_key = SigningKey::from_bytes(&[0x42; 32]);
        let expected = database_only_plaintext_container(b"database payload");
        let record_plan = BackupRecordPlan::new(vec![
            BackupEntryRecord::database(
                Sha256Digest::hash_reader(b"database payload".as_slice()).expect("hash database"),
                16,
            )
            .expect("construct database payload record"),
        ])
        .expect("construct database-only record plan");

        let staging = stage_encrypted_backup(
            plaintext,
            ciphertext,
            &signing_key,
            &recipient,
            record_plan,
            |_, _| Ok(Cursor::new(b"database payload".to_vec())),
        )
        .expect("self-verify and encrypt plaintext staging");
        let entries = fs::read_dir(&staging_parent)
            .expect("read direct-sibling staging parent")
            .collect::<std::io::Result<Vec<_>>>()
            .expect("collect direct-sibling staging");
        assert_eq!(entries.len(), 1);
        assert_eq!(entries[0].file_name(), ciphertext_name);
        assert!(!staging_parent.join(plaintext_name).exists());
        assert!(
            entries[0]
                .file_type()
                .expect("classify ciphertext")
                .is_file()
        );

        let mut ciphertext = staging
            .ciphertext_handle()
            .expect("borrow retained ciphertext")
            .try_clone()
            .expect("clone retained ciphertext");
        ciphertext
            .seek(SeekFrom::Start(0))
            .expect("rewind retained ciphertext");
        let decryptor = age::Decryptor::new(ciphertext).expect("parse staged ciphertext");
        let mut decrypted = decryptor
            .decrypt(std::iter::once(&identity as &dyn age::Identity))
            .expect("decrypt staged ciphertext");
        let mut plaintext = Vec::new();
        decrypted
            .read_to_end(&mut plaintext)
            .expect("reach staged ciphertext EOF");
        assert_eq!(plaintext, expected.bytes);
        assert_eq!(
            staging.authenticated().manifest_schema(),
            "heleos.backup-manifest/v1"
        );
        assert_eq!(staging.authenticated().extents().len(), 1);
        assert_eq!(
            staging.ciphertext_byte_length(),
            staging
                .ciphertext_handle()
                .expect("borrow retained ciphertext")
                .metadata()
                .expect("read ciphertext metadata")
                .len()
        );

        drop(staging);
        assert!(
            fs::read_dir(&staging_parent)
                .expect("read emptied staging parent")
                .next()
                .is_none()
        );
    }

    #[test]
    fn encrypted_staging_rejects_guards_from_different_retained_parents() {
        let plaintext_root = tempfile::tempdir().expect("create plaintext staging parent");
        apply_private_permissions(plaintext_root.path()).expect("harden plaintext staging parent");
        let plaintext_parent =
            fs::File::open(plaintext_root.path()).expect("retain plaintext staging parent");
        let ciphertext_root = tempfile::tempdir().expect("create ciphertext staging parent");
        apply_private_permissions(ciphertext_root.path())
            .expect("harden ciphertext staging parent");
        let ciphertext_parent =
            fs::File::open(ciphertext_root.path()).expect("retain ciphertext staging parent");
        let plaintext = create_owned_sibling_file_with_test_names(
            &plaintext_parent,
            OwnedSiblingFilePurpose::Plaintext,
            [OsString::from("plaintext.partial")],
            None,
        )
        .expect("create plaintext sibling");
        let ciphertext = create_owned_sibling_file_with_test_names(
            &ciphertext_parent,
            OwnedSiblingFilePurpose::Ciphertext,
            [OsString::from("ciphertext.partial")],
            None,
        )
        .expect("create ciphertext sibling");
        let signing_key = SigningKey::from_bytes(&[0x42; 32]);
        let recipient = age::x25519::Identity::generate().to_public();
        let record_plan = BackupRecordPlan::new(vec![
            BackupEntryRecord::database(
                Sha256Digest::hash_reader(b"database payload".as_slice()).expect("hash database"),
                16,
            )
            .expect("construct database payload record"),
        ])
        .expect("construct database-only record plan");

        let result = stage_encrypted_backup(
            plaintext,
            ciphertext,
            &signing_key,
            &recipient,
            record_plan,
            |_, _| Ok(Cursor::new(b"database payload".to_vec())),
        );
        assert!(matches!(result, Err(HeleosError::Integrity)));
        assert!(
            fs::read_dir(plaintext_root.path())
                .expect("read plaintext parent")
                .next()
                .is_none()
        );
        assert!(
            fs::read_dir(ciphertext_root.path())
                .expect("read ciphertext parent")
                .next()
                .is_none()
        );
    }

    #[test]
    fn encrypted_staging_returns_synchronized_ciphertext_ownership_for_publication() {
        let root = tempfile::tempdir().expect("create publishable staging parent");
        apply_private_permissions(root.path()).expect("harden publishable staging parent");
        let staging_parent = fs::canonicalize(root.path()).expect("canonicalize staging parent");
        let parent = fs::File::open(&staging_parent).expect("retain publishable staging parent");
        let (plaintext, ciphertext) = create_owned_backup_file_pair_with_test_names(
            &parent,
            [OsString::from("publishable-plaintext.partial")],
            [OsString::from("publishable-ciphertext.partial")],
        )
        .expect("create publishable backup staging pair");
        let signing_key = SigningKey::from_bytes(&[0x42; 32]);
        let recipient = age::x25519::Identity::generate().to_public();
        let record_plan = BackupRecordPlan::new(vec![
            BackupEntryRecord::database(
                Sha256Digest::hash_reader(b"database payload".as_slice()).expect("hash database"),
                16,
            )
            .expect("construct database payload record"),
        ])
        .expect("construct database-only record plan");
        let staging = stage_encrypted_backup(
            plaintext,
            ciphertext,
            &signing_key,
            &recipient,
            record_plan,
            |_, _| Ok(Cursor::new(b"database payload".to_vec())),
        )
        .expect("build synchronized publishable ciphertext");
        let expected_length = staging.ciphertext_byte_length();
        let (mut ciphertext, authenticated, ciphertext_byte_length) = staging.into_parts();
        assert_eq!(ciphertext_byte_length, expected_length);
        assert_eq!(authenticated.extents().len(), 1);
        assert_eq!(
            ciphertext
                .handle()
                .expect("borrow ciphertext")
                .metadata()
                .expect("read ciphertext metadata")
                .len(),
            ciphertext_byte_length
        );

        #[cfg(target_os = "macos")]
        let preserved_path = {
            let final_name = OneComponentName::try_from(OsString::from("publishable.age"))
                .expect("validate publishable final name");
            publish_file_noreplace(
                ciphertext.parent_handle(),
                ciphertext.handle().expect("borrow ciphertext subject"),
                ciphertext.component_name(),
                &final_name,
            )
            .expect("publish retained ciphertext");
            ciphertext
                .mark_published()
                .expect("immediately flip ciphertext ownership");
            staging_parent.join("publishable.age")
        };
        #[cfg(not(target_os = "macos"))]
        let preserved_path = {
            ciphertext
                .mark_published()
                .expect("record simulated ciphertext publication");
            staging_parent.join("publishable-ciphertext.partial")
        };

        drop(ciphertext);
        assert!(preserved_path.is_file());
        fs::remove_file(preserved_path).expect("remove preserved ciphertext final");
    }

    #[test]
    fn direct_sibling_collision_retry_preserves_every_preexisting_entry() {
        let root = tempfile::tempdir().expect("create collision staging parent");
        apply_private_permissions(root.path()).expect("harden collision staging parent");
        let staging_parent = fs::canonicalize(root.path()).expect("canonicalize staging parent");
        let parent = fs::File::open(&staging_parent).expect("retain collision staging parent");
        let file_collision = staging_parent.join("preexisting-file");
        fs::write(&file_collision, b"owner bytes").expect("create colliding file");
        apply_private_permissions(&file_collision).expect("harden colliding file");
        let tree_collision = staging_parent.join("preexisting-tree");
        fs::create_dir(&tree_collision).expect("create colliding tree");
        apply_private_permissions(&tree_collision).expect("harden colliding tree");
        fs::write(tree_collision.join("owner-marker"), b"owner tree bytes")
            .expect("populate colliding tree");

        let events = Rc::new(RefCell::new(Vec::new()));
        let mut file = create_owned_sibling_file_with_test_names(
            &parent,
            OwnedSiblingFilePurpose::Plaintext,
            [
                OsString::from("preexisting-file"),
                OsString::from("owned-file"),
            ],
            Some(Rc::clone(&events)),
        )
        .expect("retry file collision");
        assert_eq!(
            fs::read(&file_collision).expect("reread collision"),
            b"owner bytes"
        );
        assert!(staging_parent.join("owned-file").is_file());
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;

            fs::set_permissions(
                staging_parent.join("owned-file"),
                fs::Permissions::from_mode(0o644),
            )
            .expect("drift owned-file permissions after identity retention");
        }
        file.cleanup().expect("explicitly clean owned file");
        assert_eq!(
            events.borrow().as_slice(),
            [
                StagingCleanupEvent::HandleClosed,
                StagingCleanupEvent::EntryRemoved,
                StagingCleanupEvent::ParentSynced,
            ]
        );
        assert_eq!(
            fs::read(&file_collision).expect("reread collision"),
            b"owner bytes"
        );
        assert!(!staging_parent.join("owned-file").exists());

        #[cfg(unix)]
        {
            let mut rebound = create_owned_sibling_file_with_test_names(
                &parent,
                OwnedSiblingFilePurpose::Plaintext,
                [OsString::from("rebound-file")],
                None,
            )
            .expect("create rebound fixture owner");
            let rebound_path = staging_parent.join("rebound-file");
            fs::remove_file(&rebound_path).expect("unlink retained owned name");
            fs::write(&rebound_path, b"replacement owner bytes")
                .expect("create replacement binding");
            apply_private_permissions(&rebound_path).expect("harden replacement binding");
            assert!(matches!(rebound.cleanup(), Err(HeleosError::PolicyDenied)));
            assert_eq!(
                fs::read(&rebound_path).expect("read preserved replacement"),
                b"replacement owner bytes"
            );
            fs::remove_file(rebound_path).expect("remove preserved replacement fixture");
        }

        let pair_result = create_owned_backup_file_pair_with_test_names(
            &parent,
            [OsString::from("pair-owned-plaintext")],
            [OsString::from("preexisting-file")],
        );
        assert!(matches!(pair_result, Err(HeleosError::PolicyDenied)));
        assert!(!staging_parent.join("pair-owned-plaintext").exists());
        assert_eq!(
            fs::read(&file_collision).expect("reread pair collision"),
            b"owner bytes"
        );

        let tree_events = Rc::new(RefCell::new(Vec::new()));
        let mut tree = create_owned_sibling_tree_with_test_names(
            &parent,
            [
                OsString::from("preexisting-tree"),
                OsString::from("owned-tree"),
            ],
            Some(Rc::clone(&tree_events)),
        )
        .expect("retry tree collision");
        assert_eq!(tree.state_for_test(), StagingOwnershipState::Building);
        tree.mark_fully_synced().expect("sync bare owned tree");
        tree.cleanup().expect("explicitly clean owned tree");
        assert_eq!(
            tree_events.borrow().as_slice(),
            [
                StagingCleanupEvent::HandleClosed,
                StagingCleanupEvent::EntryRemoved,
                StagingCleanupEvent::ParentSynced,
            ]
        );
        assert_eq!(
            fs::read(tree_collision.join("owner-marker")).expect("reread tree collision"),
            b"owner tree bytes"
        );
        assert!(!staging_parent.join("owned-tree").exists());
    }

    #[test]
    fn fully_synced_tree_becomes_irrevocably_published_before_later_failure() {
        let root = tempfile::tempdir().expect("create tree staging parent");
        apply_private_permissions(root.path()).expect("harden tree staging parent");
        let staging_parent = fs::canonicalize(root.path()).expect("canonicalize staging parent");
        let parent = fs::File::open(&staging_parent).expect("retain tree staging parent");
        let mut tree = create_owned_sibling_tree_with_test_names(
            &parent,
            [OsString::from(".heleos-restore-tree-test.partial")],
            None,
        )
        .expect("create direct-sibling tree");
        let final_path = staging_parent.join(tree.name_for_test());

        assert_eq!(tree.state_for_test(), StagingOwnershipState::Building);
        tree.mark_fully_synced().expect("fully sync bare tree");
        assert_eq!(tree.state_for_test(), StagingOwnershipState::FullySynced);
        tree.mark_published_after_test_success()
            .expect("record simulated helper success");
        assert_eq!(tree.state_for_test(), StagingOwnershipState::Published);
        drop(tree);

        assert!(
            final_path.is_dir(),
            "published state must disable Drop cleanup"
        );
        fs::remove_dir(&final_path).expect("remove preserved test final");
    }

    #[test]
    fn file_publication_success_irrevocably_disables_owned_name_cleanup() {
        let root = tempfile::tempdir().expect("create file publication parent");
        apply_private_permissions(root.path()).expect("harden file publication parent");
        let staging_parent = fs::canonicalize(root.path()).expect("canonicalize staging parent");
        let parent = fs::File::open(&staging_parent).expect("retain file publication parent");

        let mut random_plaintext =
            create_owned_sibling_file(&parent, OwnedSiblingFilePurpose::Plaintext)
                .expect("create unpredictable plaintext sibling");
        random_plaintext
            .cleanup()
            .expect("clean unpredictable plaintext sibling");
        let mut random_tree =
            create_owned_sibling_tree(&parent).expect("create unpredictable tree sibling");
        random_tree
            .mark_fully_synced()
            .expect("sync unpredictable tree sibling");
        random_tree
            .cleanup()
            .expect("clean unpredictable tree sibling");

        let mut ciphertext = create_owned_sibling_file_with_test_names(
            &parent,
            OwnedSiblingFilePurpose::Ciphertext,
            [OsString::from("file-publication-source.partial")],
            None,
        )
        .expect("create deterministic ciphertext sibling");
        let staged_path = staging_parent.join(ciphertext.name_for_test());

        #[cfg(target_os = "macos")]
        let preserved_path = {
            let final_name = OneComponentName::try_from(OsString::from("published-backup.age"))
                .expect("validate final component");
            publish_file_noreplace(
                ciphertext.parent_handle(),
                ciphertext.handle().expect("borrow ciphertext subject"),
                ciphertext.component_name(),
                &final_name,
            )
            .expect("publish ciphertext with retained helper");
            ciphertext
                .mark_published()
                .expect("immediately flip cleanup ownership");
            staging_parent.join("published-backup.age")
        };

        #[cfg(not(target_os = "macos"))]
        let preserved_path = {
            let _ = publish_file_noreplace;
            let _ = OneComponentName::try_from(OsString::from("published-backup.age"))
                .expect("validate final component");
            ciphertext
                .mark_published()
                .expect("record simulated helper success");
            staged_path.clone()
        };

        drop(ciphertext);
        assert!(preserved_path.is_file());
        #[cfg(target_os = "macos")]
        assert!(!staged_path.exists());
        fs::remove_file(preserved_path).expect("remove preserved test final");
    }

    #[test]
    fn staging_cleans_plaintext_and_ciphertext_on_self_verify_or_age_failure() {
        let root = tempfile::tempdir().expect("create failure staging parent");
        apply_private_permissions(root.path()).expect("harden failure staging parent");
        let staging_parent = fs::canonicalize(root.path()).expect("canonicalize staging parent");
        let parent = fs::File::open(&staging_parent).expect("retain failure staging parent");
        let collision = staging_parent.join("unrelated-owner-file");
        fs::write(&collision, b"unrelated").expect("create unrelated owner file");
        apply_private_permissions(&collision).expect("harden unrelated owner file");
        let identity = age::x25519::Identity::generate();
        let recipient = identity.to_public();
        let signing_key = SigningKey::from_bytes(&[0x42; 32]);
        let database_digest =
            Sha256Digest::hash_reader(b"database payload".as_slice()).expect("hash database");

        for (index, (fault, expected_error)) in [
            (
                BackupStagingTestFault::CorruptPlaintextBeforeVerification,
                "integrity",
            ),
            (BackupStagingTestFault::CiphertextByteLimit(512), "limit"),
            (BackupStagingTestFault::BeforePlaintextWrite, "fault"),
            (BackupStagingTestFault::AfterPlaintextWritten, "fault"),
            (BackupStagingTestFault::AfterPlaintextSynced, "fault"),
            (BackupStagingTestFault::AfterAuthenticated, "fault"),
            (BackupStagingTestFault::BeforeAgeEncryption, "fault"),
            (BackupStagingTestFault::AfterAgeFinished, "fault"),
            (BackupStagingTestFault::AfterCiphertextSynced, "fault"),
        ]
        .into_iter()
        .enumerate()
        {
            let (plaintext, ciphertext) = create_owned_backup_file_pair_with_test_names(
                &parent,
                [OsString::from(format!("plaintext-{index}.partial"))],
                [OsString::from(format!("ciphertext-{index}.partial"))],
            )
            .expect("create fault backup staging pair");
            let record_plan = BackupRecordPlan::new(vec![
                BackupEntryRecord::database(database_digest, 16)
                    .expect("construct database payload record"),
            ])
            .expect("construct database-only record plan");
            let result = stage_encrypted_backup_with_test_fault(
                plaintext,
                ciphertext,
                &signing_key,
                &recipient,
                record_plan,
                |_, _| Ok(Cursor::new(b"database payload".to_vec())),
                fault,
            );
            match expected_error {
                "integrity" => assert!(matches!(result, Err(HeleosError::BackupIntegrity))),
                "limit" => match result {
                    Err(HeleosError::ResourceLimit) => {}
                    Err(error) => panic!("unexpected age failure: {error:?}"),
                    Ok(_) => panic!("age failure unexpectedly succeeded"),
                },
                "fault" => assert!(matches!(result, Err(HeleosError::FaultInjected))),
                _ => unreachable!("bounded fault matrix"),
            }
            let remaining = fs::read_dir(&staging_parent)
                .expect("read cleaned staging parent")
                .collect::<std::io::Result<Vec<_>>>()
                .expect("collect cleaned staging parent");
            assert_eq!(remaining.len(), 1);
            assert_eq!(
                remaining[0].file_name(),
                OsString::from("unrelated-owner-file")
            );
            assert_eq!(
                fs::read(&collision).expect("reread unrelated file"),
                b"unrelated"
            );
        }
    }

    #[test]
    fn preauthentication_staging_never_creates_a_decoded_tree_or_generated_locks() {
        let root = tempfile::tempdir().expect("create preauthentication parent");
        apply_private_permissions(root.path()).expect("harden preauthentication parent");
        let staging_parent = fs::canonicalize(root.path()).expect("canonicalize staging parent");
        let parent = fs::File::open(&staging_parent).expect("retain preauthentication parent");
        let (plaintext, ciphertext) = create_owned_backup_file_pair_with_test_names(
            &parent,
            [OsString::from("preauth-plaintext.partial")],
            [OsString::from("preauth-ciphertext.partial")],
        )
        .expect("create preauthentication backup staging pair");
        let signing_key = SigningKey::from_bytes(&[0x42; 32]);
        let recipient = age::x25519::Identity::generate().to_public();
        let database_digest =
            Sha256Digest::hash_reader(b"database payload".as_slice()).expect("hash database");
        let record_plan = BackupRecordPlan::new(vec![
            BackupEntryRecord::database(database_digest, 16)
                .expect("construct database payload record"),
        ])
        .expect("construct database-only record plan");

        let result = stage_encrypted_backup(
            plaintext,
            ciphertext,
            &signing_key,
            &recipient,
            record_plan,
            |_, _| {
                let entries = fs::read_dir(&staging_parent)
                    .expect("inspect preauthentication namespace")
                    .collect::<std::io::Result<Vec<_>>>()
                    .expect("collect preauthentication namespace");
                assert_eq!(entries.len(), 2);
                assert!(entries.iter().all(|entry| {
                    entry
                        .file_type()
                        .expect("classify preauthentication entry")
                        .is_file()
                }));
                assert!(!staging_parent.join("foundation.sqlite3").exists());
                assert!(
                    !staging_parent
                        .join("foundation.sqlite3.writer.lock")
                        .exists()
                );
                assert!(!staging_parent.join("vault").exists());
                Ok(Cursor::new(b"database payload".to_vec()))
            },
        )
        .expect("stage authenticated ciphertext without decoded layout");
        drop(result);
        assert!(
            fs::read_dir(&staging_parent)
                .expect("read cleaned preauthentication parent")
                .next()
                .is_none()
        );
    }

    #[test]
    fn snapshot_rejects_in_memory_and_read_only_stores_before_staging() {
        let fixture = TestDatabase::new();
        let writer = fixture.migrated_store();
        let untouched_staging = fixture.root.join("must-not-be-created");
        let mut in_memory = Store::open_in_memory().expect("open in-memory store");

        assert!(matches!(
            create_database_snapshot(&mut in_memory, &untouched_staging),
            Err(HeleosError::PolicyDenied)
        ));
        assert!(!untouched_staging.exists());

        drop(writer);
        let mut read_only = Store::open_read_only(&fixture.database).expect("open read-only store");
        assert!(matches!(
            create_database_snapshot(&mut read_only, &untouched_staging),
            Err(HeleosError::PolicyDenied)
        ));
        assert!(!untouched_staging.exists());
    }

    #[test]
    fn inventory_read_is_the_first_database_read_and_pins_the_deferred_snapshot() {
        let fixture = TestDatabase::new();
        let mut store = fixture.migrated_store();
        let raw = fixture.raw_wal_connection();
        let before_inventory = Sha256Digest::from_bytes([0x11; 32]);
        let after_inventory = Sha256Digest::from_bytes([0x22; 32]);
        let mut control = SnapshotTestControl::new(
            || insert_content(&raw, before_inventory),
            || insert_content(&raw, after_inventory),
        );

        let snapshot = create_database_snapshot_with_test_control(
            &mut store,
            &fixture.staging_parent,
            &mut control,
        )
        .expect("create pinned database snapshot");

        assert_eq!(
            snapshot
                .inventory
                .entries
                .keys()
                .copied()
                .collect::<Vec<_>>(),
            vec![before_inventory]
        );
        assert_eq!(
            content_digests(&snapshot.database_path),
            vec![before_inventory.to_string()]
        );
        assert_eq!(
            content_digests(&fixture.database),
            vec![before_inventory.to_string(), after_inventory.to_string()]
        );

        let snapshot_root = snapshot.root_path().to_owned();
        drop(snapshot);
        assert!(!snapshot_root.exists());
        assert!(fixture.root.exists());
    }

    #[test]
    fn external_commit_after_more_does_not_restart_the_snapshot() {
        let fixture = TestDatabase::new();
        let mut store = fixture.migrated_store();
        let raw = fixture.raw_wal_connection();
        for index in 0..512 {
            insert_content(&raw, indexed_digest(index));
        }
        let late_digest = Sha256Digest::from_bytes([0xff; 32]);
        let observed_more = Rc::new(Cell::new(false));
        let observed_more_in_callback = Rc::clone(&observed_more);
        let mut control = SnapshotTestControl::new(|| {}, || {}).after_more(|| {
            observed_more_in_callback.set(true);
            insert_content(&raw, late_digest);
        });

        let snapshot = create_database_snapshot_with_test_control(
            &mut store,
            &fixture.staging_parent,
            &mut control,
        )
        .expect("create multi-page snapshot");

        assert!(
            observed_more.get(),
            "backup must expose a real More boundary"
        );
        assert!(!snapshot.inventory.entries.contains_key(&late_digest));
        assert_eq!(
            content_digests(&snapshot.database_path),
            snapshot
                .inventory
                .entries
                .keys()
                .map(ToString::to_string)
                .collect::<Vec<_>>()
        );
        assert!(content_digests(&fixture.database).contains(&late_digest.to_string()));
    }

    #[test]
    fn snapshot_success_and_step_failure_release_handles_and_leave_store_reusable() {
        let fixture = TestDatabase::new();
        let mut store = fixture.migrated_store();
        let ids = TestIds::new(100);

        let snapshot = create_database_snapshot(&mut store, &fixture.staging_parent)
            .expect("create reusable-store snapshot");
        let successful_root = snapshot.root_path().to_owned();
        drop(snapshot);
        assert!(!successful_root.exists());
        create_audited_project(&mut store, &ids, "After snapshot success");

        let mut control = SnapshotTestControl::new(|| {}, || {})
            .step_outcomes([SnapshotStepTestOutcome::Failure]);
        assert!(matches!(
            create_database_snapshot_with_test_control(
                &mut store,
                &fixture.staging_parent,
                &mut control,
            ),
            Err(HeleosError::Database)
        ));
        assert_eq!(
            fs::read_dir(&fixture.staging_parent)
                .expect("read staging parent after failed snapshot")
                .count(),
            0
        );
        create_audited_project(&mut store, &ids, "After snapshot failure");
    }

    #[test]
    fn oversized_64k_page_snapshot_is_rejected_before_backup_step() {
        const TEST_DATABASE_BYTES_MAX: u64 = 64 * 1024;

        let fixture = TestDatabase::new();
        let mut store = fixture.migrated_store_with_page_size(65_536);
        let page_size = store
            .connection
            .pragma_query_value(None, "page_size", |row| row.get::<_, i64>(0))
            .expect("read migrated page size");
        let page_count = store
            .connection
            .pragma_query_value(None, "page_count", |row| row.get::<_, i64>(0))
            .expect("read migrated page count");
        assert_eq!(page_size, 65_536);
        assert!(
            u64::try_from(page_size)
                .expect("positive fixture page size")
                .checked_mul(u64::try_from(page_count).expect("nonnegative fixture page count"))
                .expect("bounded fixture database")
                > TEST_DATABASE_BYTES_MAX
        );

        let step_attempts = Rc::new(Cell::new(0_u32));
        let observed_step_attempts = Rc::clone(&step_attempts);
        let mut control = SnapshotTestControl::new(|| {}, || {})
            .with_database_bytes_max(TEST_DATABASE_BYTES_MAX)
            .before_step(move || {
                observed_step_attempts.set(
                    observed_step_attempts
                        .get()
                        .checked_add(1)
                        .expect("bounded test step count"),
                );
            });

        assert!(matches!(
            create_database_snapshot_with_test_control(
                &mut store,
                &fixture.staging_parent,
                &mut control,
            ),
            Err(HeleosError::ResourceLimit)
        ));
        assert_eq!(step_attempts.get(), 0);
        assert_eq!(
            fs::read_dir(&fixture.staging_parent)
                .expect("read staging parent after size rejection")
                .count(),
            0
        );
        create_audited_project(&mut store, &TestIds::new(400), "After size rejection");
    }
}
