use std::fs::{File, OpenOptions};
use std::future::Future;
use std::io::{Cursor, Read, Seek, SeekFrom, Write};
use std::path::{Component, Path, PathBuf};
use std::pin::Pin;
use std::str::FromStr;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::mpsc::{SyncSender, sync_channel};
use std::sync::{Arc, Condvar, Mutex};
use std::task::{Context, Poll};
use std::thread::JoinHandle;
use std::time::Duration;

use heleos_pdf_protocol::{
    DependencyRecordV1, ExportRecordV1, ImportRecordV1, MAX_GUEST_WASM_BYTES, PROTOCOL_VERSION,
    PageUnitV1, PdfActiveFeatureV1, PdfDocumentLimitV1, PdfDocumentLimitsV1, PdfGuestOutcomeV1,
    PdfGuestReasonV1, PdfRequestV1, PdfResponseV1, SourceFileV1, decode_response_v1,
    dependency_graph_sha256, encode_request_v1, exports_sha256, imports_sha256, source_tree_sha256,
    validate_pdf_guest_module_policy_v1,
};
use serde::Deserialize;

use crate::{
    HeleosError, PageMetadata, PageTransform, PageUnit, PdfActiveFeature, PdfInspection,
    PdfLimitKind, PdfLimits, PdfProbe, PdfProbeOutcome, PdfProbeProvenance, PdfQuarantine,
    PdfQuarantineReason, Result, RevisionId, Sha256Digest, VerifiedObject,
};

use super::PdfSandboxConfig;
use super::geometry::validate_page_metadata;

const MANIFEST_SCHEMA: &str = "heleos.pdf-guest-manifest/v1";
const MANIFEST_MAX_BYTES: usize = 1024 * 1024;
const GUEST_TARGET: &str = "wasm32-wasip1";
const GUEST_PROFILE: &str = "release";
const GUEST_RUSTC: &str = "1.96.1";
const GUEST_RUST_PATH_REMAP: &str = "heleos-rust-path-remap/v1";
const GUEST_BUILD_COMMAND: &str = concat!(
    "cargo +1.96.1 build --locked -p heleos-pdf-guest --target wasm32-wasip1 ",
    "--release --message-format=json-render-diagnostics --quiet"
);
const MINIMUM_STAGING_RESERVE_BYTES: u64 = 20 * 1024 * 1024 * 1024;
const TRACKED_MANIFEST: &str = include_str!("../../../../artifacts/pdf-guest/manifest.toml");

#[derive(Clone, Debug)]
pub struct ApprovedPdfGuest {
    wasm: Arc<[u8]>,
    manifest: Arc<GuestManifestV1>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
struct GuestManifestV1 {
    schema: String,
    protocol: String,
    wasm_sha256: String,
    wasm_byte_length: u64,
    source_tree_sha256: String,
    dependency_graph_sha256: String,
    guest_resolution_lock_sha256: String,
    dependencies: Vec<DependencyRecordV1>,
    target: String,
    profile: String,
    rustc: String,
    rust_path_remap: String,
    imports_sha256: String,
    imports: Vec<ImportRecordV1>,
    exports_sha256: String,
    exports: Vec<ExportRecordV1>,
    build_command: String,
}

impl ApprovedPdfGuest {
    pub fn load_tracked(wasm: &[u8]) -> Result<Self> {
        if wasm.is_empty() || wasm.len() > MAX_GUEST_WASM_BYTES {
            return Err(HeleosError::Integrity);
        }
        let manifest = parse_manifest(TRACKED_MANIFEST)?;
        verify_manifest(&manifest, wasm)?;
        Ok(Self {
            wasm: Arc::from(wasm),
            manifest: Arc::new(manifest),
        })
    }
}

fn parse_manifest(text: &str) -> Result<GuestManifestV1> {
    if text.is_empty() || text.len() > MANIFEST_MAX_BYTES || text.as_bytes().contains(&b'\r') {
        return Err(HeleosError::Integrity);
    }
    let manifest: GuestManifestV1 = toml::from_str(text).map_err(|_| HeleosError::Integrity)?;
    if manifest.schema != MANIFEST_SCHEMA
        || manifest.protocol != PROTOCOL_VERSION
        || manifest.wasm_byte_length == 0
        || manifest.wasm_byte_length > heleos_pdf_protocol::JCS_SAFE_INTEGER_MAX
        || manifest.target != GUEST_TARGET
        || manifest.profile != GUEST_PROFILE
        || manifest.rustc != GUEST_RUSTC
        || manifest.rust_path_remap != GUEST_RUST_PATH_REMAP
        || manifest.build_command != GUEST_BUILD_COMMAND
        || parse_digest(&manifest.wasm_sha256).is_err()
        || parse_digest(&manifest.source_tree_sha256).is_err()
        || parse_digest(&manifest.dependency_graph_sha256).is_err()
        || parse_digest(&manifest.guest_resolution_lock_sha256).is_err()
        || parse_digest(&manifest.imports_sha256).is_err()
        || parse_digest(&manifest.exports_sha256).is_err()
    {
        return Err(HeleosError::Integrity);
    }
    Ok(manifest)
}

fn verify_manifest(manifest: &GuestManifestV1, wasm: &[u8]) -> Result<()> {
    let wasm_len = u64::try_from(wasm.len()).map_err(|_| HeleosError::Integrity)?;
    let wasm_digest = Sha256Digest::hash_reader(Cursor::new(wasm))?;
    let source_digest = trusted_source_tree_sha256()?;
    let dependency_digest =
        dependency_graph_sha256(&manifest.dependencies).map_err(|_| HeleosError::Integrity)?;
    let import_digest = imports_sha256(&manifest.imports).map_err(|_| HeleosError::Integrity)?;
    let export_digest = exports_sha256(&manifest.exports).map_err(|_| HeleosError::Integrity)?;
    let (actual_imports, actual_exports) = validate_module_policy(wasm)?;

    if wasm_len != manifest.wasm_byte_length
        || wasm_digest != parse_digest(&manifest.wasm_sha256)?
        || source_digest != parse_digest(&manifest.source_tree_sha256)?
        || dependency_digest != manifest.dependency_graph_sha256
        || import_digest != manifest.imports_sha256
        || export_digest != manifest.exports_sha256
        || actual_imports != manifest.imports
        || actual_exports != manifest.exports
    {
        return Err(HeleosError::Integrity);
    }
    Ok(())
}

fn parse_digest(value: &str) -> Result<Sha256Digest> {
    Sha256Digest::from_str(value).map_err(|_| HeleosError::Integrity)
}

fn trusted_source_tree_sha256() -> Result<Sha256Digest> {
    let files = [
        SourceFileV1 {
            path: "crates/heleos-pdf-protocol/Cargo.toml".to_owned(),
            content: include_bytes!("../../../heleos-pdf-protocol/Cargo.toml").to_vec(),
        },
        SourceFileV1 {
            path: "crates/heleos-pdf-protocol/src/lib.rs".to_owned(),
            content: include_bytes!("../../../heleos-pdf-protocol/src/lib.rs").to_vec(),
        },
        SourceFileV1 {
            path: "crates/heleos-pdf-guest/Cargo.toml".to_owned(),
            content: include_bytes!("../../../heleos-pdf-guest/Cargo.toml").to_vec(),
        },
        SourceFileV1 {
            path: "crates/heleos-pdf-guest/src/lib.rs".to_owned(),
            content: include_bytes!("../../../heleos-pdf-guest/src/lib.rs").to_vec(),
        },
        SourceFileV1 {
            path: "crates/heleos-pdf-guest/src/main.rs".to_owned(),
            content: include_bytes!("../../../heleos-pdf-guest/src/main.rs").to_vec(),
        },
        SourceFileV1 {
            path: "crates/heleos-test-fixtures/Cargo.toml".to_owned(),
            content: include_bytes!("../../../heleos-test-fixtures/Cargo.toml").to_vec(),
        },
    ];
    let digest = source_tree_sha256(&files).map_err(|_| HeleosError::Integrity)?;
    parse_digest(&digest)
}

fn validate_module_policy(wasm: &[u8]) -> Result<(Vec<ImportRecordV1>, Vec<ExportRecordV1>)> {
    validate_pdf_guest_module_policy_v1(wasm).map_err(|_| HeleosError::PolicyDenied)
}

#[derive(Clone, Copy, Debug)]
struct ValidatedLimits {
    public: PdfLimits,
    output_cap: usize,
}

fn validate_limits(limits: PdfLimits) -> Result<ValidatedLimits> {
    let ceiling = PdfLimits::default();
    let valid = limits.max_input_bytes > 0
        && limits.max_input_bytes <= ceiling.max_input_bytes
        && limits.max_pages > 0
        && limits.max_pages <= ceiling.max_pages
        && limits.max_indirect_objects > 0
        && limits.max_indirect_objects <= ceiling.max_indirect_objects
        && limits.max_nested_references > 0
        && limits.max_nested_references <= ceiling.max_nested_references
        && limits.max_metadata_bytes > 0
        && limits.max_metadata_bytes <= ceiling.max_metadata_bytes
        && limits.max_page_axis_points > 0
        && limits.max_page_axis_points <= ceiling.max_page_axis_points
        && limits.max_guest_memory_bytes > 0
        && limits.max_guest_memory_bytes <= ceiling.max_guest_memory_bytes
        && limits.max_instances > 0
        && limits.max_instances <= ceiling.max_instances
        && limits.max_tables > 0
        && limits.max_tables <= ceiling.max_tables
        && limits.max_fuel > 0
        && limits.max_fuel <= ceiling.max_fuel
        && limits.timeout_seconds > 0
        && limits.timeout_seconds <= ceiling.timeout_seconds
        && limits.max_protocol_output_bytes > 0
        && limits.max_protocol_output_bytes <= ceiling.max_protocol_output_bytes;
    if !valid {
        return Err(HeleosError::PolicyDenied);
    }
    let output_cap =
        usize::try_from(limits.max_protocol_output_bytes).map_err(|_| HeleosError::PolicyDenied)?;
    Ok(ValidatedLimits {
        public: limits,
        output_cap,
    })
}

fn engine() -> Result<wasmtime::Engine> {
    let mut config = wasmtime::Config::new();
    config
        .consume_fuel(true)
        .epoch_interruption(true)
        .wasm_component_model(false)
        .wasm_multi_memory(false)
        .wasm_memory64(false);
    wasmtime::Engine::new(&config).map_err(|_| HeleosError::Integrity)
}

fn static_resource_admission(
    module: &wasmtime::Module,
    limits: &ValidatedLimits,
) -> std::result::Result<(), PdfLimitKind> {
    let resources = module.resources_required();
    if resources
        .max_initial_memory_size
        .and_then(|pages| pages.checked_mul(65_536))
        .is_some_and(|bytes| bytes > limits.public.max_guest_memory_bytes)
    {
        return Err(PdfLimitKind::GuestMemoryBytes);
    }
    if resources.num_memories > 1 {
        return Err(PdfLimitKind::Memories);
    }
    if resources.num_tables > limits.public.max_tables {
        return Err(PdfLimitKind::Tables);
    }
    if resources
        .max_initial_memory_size
        .is_some_and(|pages| pages.checked_mul(65_536).is_none())
    {
        return Err(PdfLimitKind::GuestMemoryBytes);
    }
    if resources
        .max_initial_table_size
        .is_some_and(|elements| elements > 10_000)
    {
        return Err(PdfLimitKind::TableElements);
    }
    Ok(())
}

fn resolve_guest_start<T>(
    instance: &wasmtime::Instance,
    store: &mut wasmtime::Store<T>,
    observer: &mut impl FnMut(&'static str),
) -> std::result::Result<wasmtime::TypedFunc<(), ()>, wasmtime::Error> {
    observer("_start");
    instance.get_typed_func::<(), ()>(store, "_start")
}

fn record_instance_attempt(
    attempts: &mut u32,
    maximum: u32,
    signals: &RuntimeSignals,
) -> std::result::Result<(), PdfLimitKind> {
    let next = attempts.checked_add(1).ok_or_else(|| {
        signals.record_limiter(PdfLimitKind::Instances);
        PdfLimitKind::Instances
    })?;
    *attempts = next;
    if next > maximum {
        signals.record_limiter(PdfLimitKind::Instances);
        return Err(PdfLimitKind::Instances);
    }
    Ok(())
}

#[derive(Clone, Debug)]
struct RngMaterial {
    secure: [u8; 32],
    insecure: [u8; 32],
    seed: [u8; 32],
}

fn derive_rng_material(input: Sha256Digest) -> RngMaterial {
    use sha2::{Digest, Sha256};

    fn derive(domain: &[u8], input: Sha256Digest) -> [u8; 32] {
        let mut hasher = Sha256::new();
        hasher.update(domain);
        hasher.update(input.as_bytes());
        hasher.finalize().into()
    }
    RngMaterial {
        secure: derive(b"heleos-pdf-secure-rng-v1\0", input),
        insecure: derive(b"heleos-pdf-insecure-rng-v1\0", input),
        seed: derive(b"heleos-pdf-insecure-seed-v1\0", input),
    }
}

#[derive(Clone, Debug)]
struct RecordingClosedStderr {
    attempted: Arc<AtomicBool>,
}

impl RecordingClosedStderr {
    fn new() -> Self {
        Self {
            attempted: Arc::new(AtomicBool::new(false)),
        }
    }

    fn record(&self) {
        self.attempted.store(true, Ordering::SeqCst);
    }
}

impl wasmtime_wasi::cli::IsTerminal for RecordingClosedStderr {
    fn is_terminal(&self) -> bool {
        false
    }
}

impl wasmtime_wasi::cli::StdoutStream for RecordingClosedStderr {
    fn async_stream(&self) -> Box<dyn tokio::io::AsyncWrite + Send + Sync> {
        Box::new(self.clone())
    }

    fn p2_stream(&self) -> Box<dyn wasmtime_wasi::p2::OutputStream> {
        Box::new(self.clone())
    }
}

impl tokio::io::AsyncWrite for RecordingClosedStderr {
    fn poll_write(
        self: Pin<&mut Self>,
        _: &mut Context<'_>,
        _: &[u8],
    ) -> Poll<std::io::Result<usize>> {
        self.record();
        Poll::Ready(Err(std::io::Error::new(
            std::io::ErrorKind::BrokenPipe,
            "closed",
        )))
    }

    fn poll_flush(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<std::io::Result<()>> {
        self.record();
        Poll::Ready(Err(std::io::Error::new(
            std::io::ErrorKind::BrokenPipe,
            "closed",
        )))
    }

    fn poll_shutdown(self: Pin<&mut Self>, _: &mut Context<'_>) -> Poll<std::io::Result<()>> {
        self.record();
        Poll::Ready(Err(std::io::Error::new(
            std::io::ErrorKind::BrokenPipe,
            "closed",
        )))
    }
}

#[wasmtime_wasi::async_trait]
impl wasmtime_wasi::p2::Pollable for RecordingClosedStderr {
    async fn ready(&mut self) {}
}

impl wasmtime_wasi::p2::OutputStream for RecordingClosedStderr {
    fn write(
        &mut self,
        _: bytes::Bytes,
    ) -> std::result::Result<(), wasmtime_wasi::p2::StreamError> {
        self.record();
        Err(wasmtime_wasi::p2::StreamError::Closed)
    }

    fn flush(&mut self) -> std::result::Result<(), wasmtime_wasi::p2::StreamError> {
        self.record();
        Err(wasmtime_wasi::p2::StreamError::Closed)
    }

    fn check_write(&mut self) -> std::result::Result<usize, wasmtime_wasi::p2::StreamError> {
        self.record();
        Err(wasmtime_wasi::p2::StreamError::Closed)
    }
}

#[derive(Clone, Debug)]
struct RecordingStdout {
    pipe: wasmtime_wasi::p2::pipe::MemoryOutputPipe,
    capacity: usize,
    overflowed: Arc<AtomicBool>,
    post_flush_check: Arc<AtomicBool>,
}

impl RecordingStdout {
    fn new(capacity: usize) -> Self {
        Self {
            pipe: wasmtime_wasi::p2::pipe::MemoryOutputPipe::new(capacity),
            capacity,
            overflowed: Arc::new(AtomicBool::new(false)),
            post_flush_check: Arc::new(AtomicBool::new(false)),
        }
    }

    fn contents(&self) -> bytes::Bytes {
        self.pipe.contents()
    }

    fn overflowed(&self) -> bool {
        self.overflowed.load(Ordering::SeqCst)
    }

    fn remaining(&self) -> usize {
        self.capacity.saturating_sub(self.contents().len())
    }

    fn record_overflow(&self) {
        self.overflowed.store(true, Ordering::SeqCst);
    }
}

impl wasmtime_wasi::cli::IsTerminal for RecordingStdout {
    fn is_terminal(&self) -> bool {
        false
    }
}

impl wasmtime_wasi::cli::StdoutStream for RecordingStdout {
    fn async_stream(&self) -> Box<dyn tokio::io::AsyncWrite + Send + Sync> {
        Box::new(self.clone())
    }

    fn p2_stream(&self) -> Box<dyn wasmtime_wasi::p2::OutputStream> {
        Box::new(self.clone())
    }
}

impl tokio::io::AsyncWrite for RecordingStdout {
    fn poll_write(
        mut self: Pin<&mut Self>,
        context: &mut Context<'_>,
        bytes: &[u8],
    ) -> Poll<std::io::Result<usize>> {
        self.post_flush_check.store(false, Ordering::SeqCst);
        if bytes.len() > self.remaining() {
            self.record_overflow();
            return Poll::Ready(Err(std::io::Error::new(
                std::io::ErrorKind::BrokenPipe,
                "bounded protocol output",
            )));
        }
        Pin::new(&mut self.pipe).poll_write(context, bytes)
    }

    fn poll_flush(
        mut self: Pin<&mut Self>,
        context: &mut Context<'_>,
    ) -> Poll<std::io::Result<()>> {
        let result = Pin::new(&mut self.pipe).poll_flush(context);
        if matches!(result, Poll::Ready(Ok(()))) {
            self.post_flush_check.store(true, Ordering::SeqCst);
        }
        result
    }

    fn poll_shutdown(
        mut self: Pin<&mut Self>,
        context: &mut Context<'_>,
    ) -> Poll<std::io::Result<()>> {
        Pin::new(&mut self.pipe).poll_shutdown(context)
    }
}

#[wasmtime_wasi::async_trait]
impl wasmtime_wasi::p2::Pollable for RecordingStdout {
    async fn ready(&mut self) {}
}

impl wasmtime_wasi::p2::OutputStream for RecordingStdout {
    fn write(
        &mut self,
        bytes: bytes::Bytes,
    ) -> std::result::Result<(), wasmtime_wasi::p2::StreamError> {
        self.post_flush_check.store(false, Ordering::SeqCst);
        if bytes.len() > self.remaining() {
            self.record_overflow();
            return Err(wasmtime_wasi::p2::StreamError::Closed);
        }
        wasmtime_wasi::p2::OutputStream::write(&mut self.pipe, bytes)
    }

    fn flush(&mut self) -> std::result::Result<(), wasmtime_wasi::p2::StreamError> {
        let result = wasmtime_wasi::p2::OutputStream::flush(&mut self.pipe);
        if result.is_ok() {
            self.post_flush_check.store(true, Ordering::SeqCst);
        }
        result
    }

    fn check_write(&mut self) -> std::result::Result<usize, wasmtime_wasi::p2::StreamError> {
        let completing_flush = self.post_flush_check.swap(false, Ordering::SeqCst);
        if self.remaining() == 0 {
            if !completing_flush {
                self.record_overflow();
            }
            return Err(wasmtime_wasi::p2::StreamError::Closed);
        }
        wasmtime_wasi::p2::OutputStream::check_write(&mut self.pipe)
    }
}

#[derive(Debug, Default)]
struct RuntimeSignalState {
    limiter_denials: Vec<PdfLimitKind>,
    resource_failure: bool,
    host_calls: bool,
    output: bool,
    stderr: bool,
}

#[derive(Clone, Debug, Default)]
struct RuntimeSignals(Arc<Mutex<RuntimeSignalState>>);

impl RuntimeSignals {
    fn record_limiter(&self, kind: PdfLimitKind) {
        let mut state = self
            .0
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        if !state.limiter_denials.contains(&kind) {
            state.limiter_denials.push(kind);
        }
    }

    fn record_host_calls(&self) {
        self.0
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .host_calls = true;
    }

    fn record_resource_failure(&self) {
        self.0
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .resource_failure = true;
    }

    fn record_output(&self) {
        self.0
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .output = true;
    }

    fn record_stderr(&self) {
        self.0
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner())
            .stderr = true;
    }

    fn first_limiter_denial(&self) -> Option<PdfLimitKind> {
        let state = self
            .0
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        [
            PdfLimitKind::GuestMemoryBytes,
            PdfLimitKind::Memories,
            PdfLimitKind::Instances,
            PdfLimitKind::Tables,
            PdfLimitKind::TableElements,
        ]
        .into_iter()
        .find(|kind| state.limiter_denials.contains(kind))
    }

    fn snapshot(&self) -> RuntimeSignalState {
        let state = self
            .0
            .lock()
            .unwrap_or_else(|poisoned| poisoned.into_inner());
        RuntimeSignalState {
            limiter_denials: state.limiter_denials.clone(),
            resource_failure: state.resource_failure,
            host_calls: state.host_calls,
            output: state.output,
            stderr: state.stderr,
        }
    }

    #[cfg(test)]
    fn with_limiter_denials<const N: usize>(kinds: [PdfLimitKind; N]) -> Self {
        let signals = Self::default();
        for kind in kinds {
            signals.record_limiter(kind);
        }
        signals
    }

    #[cfg(test)]
    fn with_host_calls_and_output() -> Self {
        let signals = Self::default();
        signals.record_host_calls();
        signals.record_output();
        signals
    }

    #[cfg(test)]
    fn with_output_and_stderr() -> Self {
        let signals = Self::default();
        signals.record_output();
        signals.record_stderr();
        signals
    }

    #[cfg(test)]
    fn with_stderr() -> Self {
        let signals = Self::default();
        signals.record_stderr();
        signals
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct RuntimeLimitMarker(PdfLimitKind);

impl std::fmt::Display for RuntimeLimitMarker {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("typed sandbox resource limit")
    }
}

impl std::error::Error for RuntimeLimitMarker {}

#[derive(Debug)]
struct RecordingLimiter {
    memory_bytes: usize,
    table_elements: usize,
    instances: usize,
    tables: usize,
    signals: RuntimeSignals,
}

impl RecordingLimiter {
    fn new(
        memory_bytes: usize,
        table_elements: usize,
        instances: usize,
        tables: usize,
        signals: RuntimeSignals,
    ) -> Self {
        Self {
            memory_bytes,
            table_elements,
            instances,
            tables,
            signals,
        }
    }

    fn deny(&self, kind: PdfLimitKind) -> wasmtime::Error {
        self.signals.record_limiter(kind);
        wasmtime::Error::new(RuntimeLimitMarker(kind))
    }
}

impl wasmtime::ResourceLimiter for RecordingLimiter {
    fn memory_growing(
        &mut self,
        _: usize,
        desired: usize,
        _: Option<usize>,
    ) -> wasmtime::Result<bool> {
        if desired > self.memory_bytes {
            return Err(self.deny(PdfLimitKind::GuestMemoryBytes));
        }
        Ok(true)
    }

    fn table_growing(
        &mut self,
        _: usize,
        desired: usize,
        _: Option<usize>,
    ) -> wasmtime::Result<bool> {
        if desired > self.table_elements {
            return Err(self.deny(PdfLimitKind::TableElements));
        }
        Ok(true)
    }

    fn memory_grow_failed(&mut self, _: wasmtime::Error) -> wasmtime::Result<()> {
        self.signals.record_resource_failure();
        Ok(())
    }

    fn table_grow_failed(&mut self, _: wasmtime::Error) -> wasmtime::Result<()> {
        self.signals.record_resource_failure();
        Ok(())
    }

    fn instances(&self) -> usize {
        self.instances
    }

    fn tables(&self) -> usize {
        self.tables
    }

    fn memories(&self) -> usize {
        1
    }
}

#[derive(Clone, Copy, Debug, Default)]
struct CompletionEvidence {
    out_of_fuel: bool,
    interrupted: bool,
    outer_timeout: bool,
    zero_exit: bool,
    nonzero_exit: bool,
    other_trap: bool,
    protocol_breach: bool,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum RuntimeDecision {
    Proceed,
    Limit(PdfLimitKind),
    SandboxTrap,
    ProtocolBreach,
}

fn arbitrate_runtime(signals: &RuntimeSignals, completion: CompletionEvidence) -> RuntimeDecision {
    if let Some(kind) = signals.first_limiter_denial() {
        return RuntimeDecision::Limit(kind);
    }
    if completion.out_of_fuel {
        return RuntimeDecision::Limit(PdfLimitKind::Fuel);
    }
    let signals = signals.snapshot();
    if signals.host_calls {
        return RuntimeDecision::Limit(PdfLimitKind::HostCalls);
    }
    if signals.output {
        return RuntimeDecision::Limit(PdfLimitKind::ProtocolOutputBytes);
    }
    if completion.interrupted || completion.outer_timeout {
        return RuntimeDecision::Limit(PdfLimitKind::Timeout);
    }
    if signals.resource_failure
        || signals.stderr
        || completion.nonzero_exit
        || completion.other_trap
    {
        return RuntimeDecision::SandboxTrap;
    }
    if completion.protocol_breach {
        return RuntimeDecision::ProtocolBreach;
    }
    let _ = completion.zero_exit;
    RuntimeDecision::Proceed
}

#[derive(Debug)]
struct AdmissionState {
    accepting: bool,
    active: usize,
}

#[derive(Debug)]
struct Admission {
    maximum: usize,
    state: Mutex<AdmissionState>,
    changed: Condvar,
}

impl Admission {
    fn new(maximum: usize) -> Self {
        Self {
            maximum,
            state: Mutex::new(AdmissionState {
                accepting: true,
                active: 0,
            }),
            changed: Condvar::new(),
        }
    }

    fn acquire(self: &Arc<Self>) -> Result<AdmissionPermit> {
        let mut state = self.state.lock().map_err(|_| HeleosError::Integrity)?;
        loop {
            if !state.accepting {
                return Err(HeleosError::Integrity);
            }
            if state.active < self.maximum {
                state.active = state.active.checked_add(1).ok_or(HeleosError::Integrity)?;
                return Ok(AdmissionPermit {
                    admission: Arc::clone(self),
                });
            }
            state = self
                .changed
                .wait(state)
                .map_err(|_| HeleosError::Integrity)?;
        }
    }

    fn close(&self) {
        if let Ok(mut state) = self.state.lock() {
            state.accepting = false;
            self.changed.notify_all();
        }
    }
}

#[derive(Debug)]
struct AdmissionPermit {
    admission: Arc<Admission>,
}

impl Drop for AdmissionPermit {
    fn drop(&mut self) {
        if let Ok(mut state) = self.admission.state.lock() {
            state.active = state.active.saturating_sub(1);
            self.admission.changed.notify_one();
        }
    }
}

type OwnedRuntimeJob = Pin<Box<dyn Future<Output = ()> + Send + 'static>>;

struct RuntimeOwner {
    sender: Option<SyncSender<OwnedRuntimeJob>>,
    join: Option<JoinHandle<()>>,
}

impl std::fmt::Debug for RuntimeOwner {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("RuntimeOwner")
    }
}

impl RuntimeOwner {
    fn start() -> Result<Self> {
        let (sender, receiver) = sync_channel::<OwnedRuntimeJob>(2);
        let (ready_sender, ready_receiver) = sync_channel(1);
        let join = std::thread::Builder::new()
            .name("heleos-pdf-runtime-owner".to_owned())
            .spawn(move || {
                let runtime = tokio::runtime::Builder::new_multi_thread()
                    .worker_threads(2)
                    .enable_time()
                    .build();
                let Ok(runtime) = runtime else {
                    let _ = ready_sender.send(false);
                    return;
                };
                if ready_sender.send(true).is_err() {
                    return;
                }
                let mut jobs = Vec::new();
                while let Ok(job) = receiver.recv() {
                    jobs.retain(|handle: &tokio::task::JoinHandle<()>| !handle.is_finished());
                    jobs.push(runtime.spawn(job));
                }
                runtime.block_on(async {
                    for job in jobs {
                        let _ = job.await;
                    }
                });
            })
            .map_err(HeleosError::Io)?;
        if ready_receiver.recv().map_err(|_| HeleosError::Integrity)? {
            Ok(Self {
                sender: Some(sender),
                join: Some(join),
            })
        } else {
            let _ = join.join();
            Err(HeleosError::Integrity)
        }
    }

    fn dispatch(&self, job: OwnedRuntimeJob) -> Result<()> {
        self.sender
            .as_ref()
            .ok_or(HeleosError::Integrity)?
            .send(job)
            .map_err(|_| HeleosError::Integrity)
    }
}

impl Drop for RuntimeOwner {
    fn drop(&mut self) {
        self.sender.take();
        if let Some(join) = self.join.take() {
            let _ = join.join();
        }
    }
}

struct EpochTicker {
    cancel: Option<SyncSender<()>>,
    join: Option<JoinHandle<()>>,
}

impl std::fmt::Debug for EpochTicker {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("EpochTicker")
    }
}

impl EpochTicker {
    fn start(engine: wasmtime::Engine) -> Result<Self> {
        let (cancel, receiver) = sync_channel(1);
        let join = std::thread::Builder::new()
            .name("heleos-pdf-epoch-ticker".to_owned())
            .spawn(move || {
                loop {
                    match receiver.recv_timeout(Duration::from_millis(10)) {
                        Ok(()) | Err(std::sync::mpsc::RecvTimeoutError::Disconnected) => break,
                        Err(std::sync::mpsc::RecvTimeoutError::Timeout) => engine.increment_epoch(),
                    }
                }
            })
            .map_err(HeleosError::Io)?;
        Ok(Self {
            cancel: Some(cancel),
            join: Some(join),
        })
    }
}

impl Drop for EpochTicker {
    fn drop(&mut self) {
        if let Some(cancel) = self.cancel.take() {
            let _ = cancel.send(());
        }
        if let Some(join) = self.join.take() {
            let _ = join.join();
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct StagingMarker {
    first: u64,
    second: u64,
}

#[cfg(unix)]
fn staging_marker(file: &File) -> Result<StagingMarker> {
    use std::os::unix::fs::MetadataExt;

    let metadata = file.metadata().map_err(HeleosError::Io)?;
    Ok(StagingMarker {
        first: metadata.dev(),
        second: metadata.ino(),
    })
}

#[cfg(windows)]
fn staging_marker(file: &File) -> Result<StagingMarker> {
    use std::os::windows::fs::MetadataExt;

    let metadata = file.metadata().map_err(HeleosError::Io)?;
    Ok(StagingMarker {
        first: u64::from(metadata.file_attributes()),
        second: metadata.creation_time(),
    })
}

#[cfg(not(any(unix, windows)))]
fn staging_marker(_: &File) -> Result<StagingMarker> {
    Err(HeleosError::PolicyDenied)
}

fn validate_staging_parent_path(path: &Path) -> Result<()> {
    if !path.is_absolute()
        || path.parent().is_none()
        || !matches!(path.components().next_back(), Some(Component::Normal(_)))
        || contains_raw_staging_dot_component(path)
        || path
            .components()
            .any(|component| matches!(component, Component::CurDir | Component::ParentDir))
    {
        return Err(HeleosError::PolicyDenied);
    }
    Ok(())
}

#[cfg(unix)]
fn contains_raw_staging_dot_component(path: &Path) -> bool {
    use std::os::unix::ffi::OsStrExt;

    path.as_os_str()
        .as_bytes()
        .split(|byte| *byte == b'/')
        .any(|component| component == b"." || component == b"..")
}

#[cfg(windows)]
fn contains_raw_staging_dot_component(path: &Path) -> bool {
    use std::os::windows::ffi::OsStrExt;

    let units = path.as_os_str().encode_wide().collect::<Vec<_>>();
    units
        .split(|unit| *unit == u16::from(b'/') || *unit == u16::from(b'\\'))
        .any(|component| component == [u16::from(b'.')] || component == [u16::from(b'.'); 2])
}

#[cfg(not(any(unix, windows)))]
fn contains_raw_staging_dot_component(_: &Path) -> bool {
    true
}

#[cfg(unix)]
fn open_staging_directory(path: &Path, apply: bool) -> Result<(File, StagingMarker)> {
    use std::os::unix::fs::OpenOptionsExt;

    let mut options = OpenOptions::new();
    options
        .read(true)
        .custom_flags(libc::O_NOFOLLOW | libc::O_DIRECTORY | libc::O_NONBLOCK);
    let mut file = options.open(path).map_err(map_staging_open_error)?;
    if !file.metadata().map_err(HeleosError::Io)?.is_dir() {
        return Err(HeleosError::PolicyDenied);
    }
    if apply {
        crate::store::apply_private_permissions_to_handle(&mut file)?;
    } else {
        crate::store::verify_private_permissions_on_handle(&file)?;
    }
    let marker = staging_marker(&file)?;
    Ok((file, marker))
}

#[cfg(unix)]
fn map_staging_open_error(error: std::io::Error) -> HeleosError {
    if matches!(error.raw_os_error(), Some(code) if code == libc::ELOOP || code == libc::ENOTDIR) {
        HeleosError::PolicyDenied
    } else {
        HeleosError::Io(error)
    }
}

#[cfg(windows)]
fn open_staging_directory(path: &Path, apply: bool) -> Result<(File, StagingMarker)> {
    use std::os::windows::fs::{MetadataExt, OpenOptionsExt};

    const GENERIC_READ: u32 = 0x8000_0000;
    const READ_CONTROL: u32 = 0x0002_0000;
    const WRITE_DAC: u32 = 0x0004_0000;
    const FILE_READ_ATTRIBUTES: u32 = 0x0000_0080;
    const FILE_SHARE_READ: u32 = 0x0000_0001;
    const FILE_SHARE_WRITE: u32 = 0x0000_0002;
    const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x0000_0400;
    const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
    const FILE_FLAG_BACKUP_SEMANTICS: u32 = 0x0200_0000;

    let mut file = OpenOptions::new()
        .access_mode(
            GENERIC_READ | READ_CONTROL | FILE_READ_ATTRIBUTES | if apply { WRITE_DAC } else { 0 },
        )
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_BACKUP_SEMANTICS)
        .open(path)
        .map_err(HeleosError::Io)?;
    let metadata = file.metadata().map_err(HeleosError::Io)?;
    if !metadata.is_dir() || metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
        return Err(HeleosError::PolicyDenied);
    }
    if apply {
        crate::store::apply_private_permissions_to_handle(&mut file)?;
    } else {
        crate::store::verify_private_permissions_on_handle(&file)?;
    }
    let marker = staging_marker(&file)?;
    Ok((file, marker))
}

#[cfg(not(any(unix, windows)))]
fn open_staging_directory(_: &Path, _: bool) -> Result<(File, StagingMarker)> {
    Err(HeleosError::PolicyDenied)
}

#[derive(Debug)]
struct ParentAnchor {
    path: PathBuf,
    retained: File,
    marker: StagingMarker,
}

impl ParentAnchor {
    fn open(path: &Path) -> Result<Self> {
        validate_staging_parent_path(path)?;
        let (retained, marker) = open_staging_directory(path, false)?;
        let anchor = Self {
            path: path.to_owned(),
            retained,
            marker,
        };
        anchor.recheck()?;
        Ok(anchor)
    }

    fn recheck(&self) -> Result<()> {
        crate::store::verify_private_permissions_on_handle(&self.retained)?;
        if staging_marker(&self.retained)? != self.marker {
            return Err(HeleosError::PolicyDenied);
        }
        let (reopened, marker) = open_staging_directory(&self.path, false)?;
        if marker != self.marker {
            return Err(HeleosError::PolicyDenied);
        }
        drop(reopened);
        Ok(())
    }

    fn require_capacity(&self, copy_bytes: u64) -> Result<()> {
        self.recheck()?;
        let total = fs2::total_space(&self.path).map_err(HeleosError::Io)?;
        let available = fs2::available_space(&self.path).map_err(HeleosError::Io)?;
        let allocation = fs2::allocation_granularity(&self.path).map_err(HeleosError::Io)?;
        self.recheck()?;
        if allocation == 0 {
            return Err(HeleosError::Integrity);
        }
        let ten_percent = total.checked_add(9).ok_or(HeleosError::ResourceLimit)? / 10;
        let reserve = MINIMUM_STAGING_RESERVE_BYTES.max(ten_percent);
        let required = copy_bytes
            .checked_add(allocation)
            .and_then(|value| value.checked_add(reserve))
            .ok_or(HeleosError::ResourceLimit)?;
        if available < required {
            return Err(HeleosError::ResourceLimit);
        }
        Ok(())
    }
}

#[cfg(unix)]
fn create_staging_input(path: &Path) -> Result<File> {
    use std::os::unix::fs::OpenOptionsExt;

    let mut file = OpenOptions::new()
        .read(true)
        .write(true)
        .create_new(true)
        .mode(0o600)
        .custom_flags(libc::O_NOFOLLOW)
        .open(path)
        .map_err(map_staging_open_error)?;
    crate::store::apply_private_permissions_to_handle(&mut file)?;
    Ok(file)
}

#[cfg(windows)]
fn windows_staging_input_access_mask(writable: bool) -> u32 {
    use windows_permissions::constants::AccessRights;

    let mut rights = AccessRights::GenericRead | AccessRights::ReadControl;
    if writable {
        rights |= AccessRights::GenericWrite | AccessRights::WriteDac;
    }
    rights.bits()
}

#[cfg(windows)]
fn create_staging_input(path: &Path) -> Result<File> {
    use std::os::windows::fs::OpenOptionsExt;

    const FILE_SHARE_READ: u32 = 0x0000_0001;
    const FILE_SHARE_WRITE: u32 = 0x0000_0002;
    const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
    let mut file = OpenOptions::new()
        .read(true)
        .write(true)
        .create_new(true)
        .access_mode(windows_staging_input_access_mask(true))
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
        .open(path)
        .map_err(HeleosError::Io)?;
    crate::store::apply_private_permissions_to_handle(&mut file)?;
    Ok(file)
}

#[cfg(not(any(unix, windows)))]
fn create_staging_input(_: &Path) -> Result<File> {
    Err(HeleosError::PolicyDenied)
}

#[cfg(unix)]
fn open_staging_input_read_only(path: &Path) -> Result<(File, StagingMarker)> {
    use std::os::unix::fs::OpenOptionsExt;

    let file = OpenOptions::new()
        .read(true)
        .custom_flags(libc::O_NOFOLLOW)
        .open(path)
        .map_err(map_staging_open_error)?;
    verify_read_only_input(&file)?;
    let marker = staging_marker(&file)?;
    Ok((file, marker))
}

#[cfg(windows)]
fn open_staging_input_read_only(path: &Path) -> Result<(File, StagingMarker)> {
    use std::os::windows::fs::OpenOptionsExt;

    const FILE_SHARE_READ: u32 = 0x0000_0001;
    const FILE_SHARE_WRITE: u32 = 0x0000_0002;
    const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
    let file = OpenOptions::new()
        .read(true)
        .access_mode(windows_staging_input_access_mask(false))
        .share_mode(FILE_SHARE_READ | FILE_SHARE_WRITE)
        .custom_flags(FILE_FLAG_OPEN_REPARSE_POINT)
        .open(path)
        .map_err(HeleosError::Io)?;
    verify_read_only_input(&file)?;
    let marker = staging_marker(&file)?;
    Ok((file, marker))
}

#[cfg(not(any(unix, windows)))]
fn open_staging_input_read_only(_: &Path) -> Result<(File, StagingMarker)> {
    Err(HeleosError::PolicyDenied)
}

#[cfg(unix)]
fn set_input_read_only(file: &File) -> Result<()> {
    use std::os::unix::fs::PermissionsExt;

    file.set_permissions(std::fs::Permissions::from_mode(0o400))
        .map_err(HeleosError::Io)?;
    file.sync_all().map_err(HeleosError::Io)?;
    verify_read_only_input(file)
}

#[cfg(windows)]
fn set_input_read_only(file: &File) -> Result<()> {
    let mut permissions = file.metadata().map_err(HeleosError::Io)?.permissions();
    permissions.set_readonly(true);
    file.set_permissions(permissions).map_err(HeleosError::Io)?;
    file.sync_all().map_err(HeleosError::Io)?;
    verify_read_only_input(file)
}

#[cfg(not(any(unix, windows)))]
fn set_input_read_only(_: &File) -> Result<()> {
    Err(HeleosError::PolicyDenied)
}

#[cfg(unix)]
fn verify_read_only_input(file: &File) -> Result<()> {
    use std::os::unix::fs::{MetadataExt, PermissionsExt};

    let metadata = file.metadata().map_err(HeleosError::Io)?;
    if !metadata.is_file()
        || metadata.permissions().mode() & 0o777 != 0o400
        || metadata.nlink() != 1
    {
        return Err(HeleosError::PolicyDenied);
    }
    Ok(())
}

#[cfg(windows)]
fn verify_read_only_input(file: &File) -> Result<()> {
    use std::os::windows::fs::MetadataExt;

    const FILE_ATTRIBUTE_READONLY: u32 = 0x0000_0001;
    const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x0000_0400;
    let metadata = file.metadata().map_err(HeleosError::Io)?;
    let retained = cap_std::fs::File::from_std(file.try_clone().map_err(HeleosError::Io)?);
    let retained_metadata = retained.metadata().map_err(HeleosError::Io)?;
    if !metadata.is_file()
        || metadata.file_attributes() & FILE_ATTRIBUTE_READONLY == 0
        || metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
        || cap_fs_ext::MetadataExt::nlink(&retained_metadata) != 1
    {
        return Err(HeleosError::PolicyDenied);
    }
    crate::store::verify_private_permissions_on_handle(file)
}

#[cfg(not(any(unix, windows)))]
fn verify_read_only_input(_: &File) -> Result<()> {
    Err(HeleosError::PolicyDenied)
}

fn hash_exact_file(file: &File, expected_length: u64) -> Result<Sha256Digest> {
    use sha2::{Digest, Sha256};

    let mut reader = file.try_clone().map_err(HeleosError::Io)?;
    reader.seek(SeekFrom::Start(0)).map_err(HeleosError::Io)?;
    let mut remaining = expected_length;
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];
    while remaining > 0 {
        let wanted = usize::try_from(remaining.min(buffer.len() as u64))
            .map_err(|_| HeleosError::Integrity)?;
        let read = reader
            .read(&mut buffer[..wanted])
            .map_err(HeleosError::Io)?;
        if read == 0 {
            return Err(HeleosError::Integrity);
        }
        hasher.update(&buffer[..read]);
        remaining = remaining
            .checked_sub(u64::try_from(read).map_err(|_| HeleosError::Integrity)?)
            .ok_or(HeleosError::Integrity)?;
    }
    let mut trailing = [0_u8; 1];
    if reader.read(&mut trailing).map_err(HeleosError::Io)? != 0 {
        return Err(HeleosError::Integrity);
    }
    Ok(Sha256Digest::from_bytes(hasher.finalize().into()))
}

fn copy_exact_input<R: Read + Seek>(
    source: &mut R,
    destination: &mut File,
    expected_length: u64,
    expected_digest: Sha256Digest,
) -> Result<()> {
    use sha2::{Digest, Sha256};

    source.seek(SeekFrom::Start(0)).map_err(HeleosError::Io)?;
    destination
        .seek(SeekFrom::Start(0))
        .map_err(HeleosError::Io)?;
    let mut remaining = expected_length;
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];
    while remaining > 0 {
        let wanted = usize::try_from(remaining.min(buffer.len() as u64))
            .map_err(|_| HeleosError::Integrity)?;
        let read = source
            .read(&mut buffer[..wanted])
            .map_err(HeleosError::Io)?;
        if read == 0 {
            return Err(HeleosError::Integrity);
        }
        destination
            .write_all(&buffer[..read])
            .map_err(HeleosError::Io)?;
        hasher.update(&buffer[..read]);
        remaining = remaining
            .checked_sub(u64::try_from(read).map_err(|_| HeleosError::Integrity)?)
            .ok_or(HeleosError::Integrity)?;
    }
    let mut trailing = [0_u8; 1];
    if source.read(&mut trailing).map_err(HeleosError::Io)? != 0
        || Sha256Digest::from_bytes(hasher.finalize().into()) != expected_digest
    {
        return Err(HeleosError::Integrity);
    }
    destination.flush().map_err(HeleosError::Io)?;
    destination.sync_all().map_err(HeleosError::Io)
}

struct StagedInput {
    parent: Option<ParentAnchor>,
    temporary: Option<tempfile::TempDir>,
    root: Option<File>,
    root_marker: Option<StagingMarker>,
    input: Option<File>,
    input_marker: Option<StagingMarker>,
    expected_length: u64,
    expected_digest: Sha256Digest,
    #[cfg(all(test, windows))]
    recheck_observer: Option<StagingRecheckObserver>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum StagingRecheckPoint {
    BeforePreopen,
    AfterPreopen,
    BeforeOutputAcceptance,
}

#[cfg(all(test, windows))]
type StagingRecheckObserver = Arc<dyn Fn(StagingRecheckPoint) + Send + Sync>;

impl std::fmt::Debug for StagedInput {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("StagedInput")
    }
}

impl StagedInput {
    fn create<R: Read + Seek>(
        parent_path: &Path,
        source: &mut R,
        expected_length: u64,
        expected_digest: Sha256Digest,
    ) -> Result<Self> {
        let parent = ParentAnchor::open(parent_path)?;
        parent.require_capacity(expected_length)?;
        let temporary = match tempfile::Builder::new()
            .prefix(".heleos-pdf-")
            .tempdir_in(parent_path)
        {
            Ok(temporary) => temporary,
            Err(error) => {
                parent.recheck()?;
                return Err(HeleosError::Io(error));
            }
        };
        let mut staged = Self {
            parent: Some(parent),
            temporary: Some(temporary),
            root: None,
            root_marker: None,
            input: None,
            input_marker: None,
            expected_length,
            expected_digest,
            #[cfg(all(test, windows))]
            recheck_observer: None,
        };
        let build_result = (|| {
            staged.parent()?.recheck()?;
            let (root, root_marker) = open_staging_directory(staged.root_path(), true)?;
            staged.root = Some(root);
            staged.root_marker = Some(root_marker);

            let input_path = staged.root_path().join("input.pdf");
            let mut writable = create_staging_input(&input_path)?;
            if expected_length > 0 {
                fs2::FileExt::allocate(&writable, expected_length).map_err(HeleosError::Io)?;
            }
            copy_exact_input(source, &mut writable, expected_length, expected_digest)?;
            if hash_exact_file(&writable, expected_length)? != expected_digest {
                return Err(HeleosError::Integrity);
            }
            set_input_read_only(&writable)?;
            drop(writable);
            let (input, input_marker) = open_staging_input_read_only(&input_path)?;
            staged.input = Some(input);
            staged.input_marker = Some(input_marker);
            staged.recheck()
        })();
        if let Err(error) = build_result {
            return match staged.cleanup_inner() {
                Ok(()) => Err(error),
                Err(cleanup) => Err(cleanup),
            };
        }
        Ok(staged)
    }

    fn parent(&self) -> Result<&ParentAnchor> {
        self.parent.as_ref().ok_or(HeleosError::Integrity)
    }

    fn root_path(&self) -> &Path {
        self.temporary
            .as_ref()
            .map(tempfile::TempDir::path)
            .unwrap_or_else(|| Path::new(""))
    }

    fn recheck(&self) -> Result<()> {
        self.parent()?.recheck()?;
        let root = self.root.as_ref().ok_or(HeleosError::Integrity)?;
        let root_marker = self.root_marker.ok_or(HeleosError::Integrity)?;
        crate::store::verify_private_permissions_on_handle(root)?;
        if staging_marker(root)? != root_marker {
            return Err(HeleosError::PolicyDenied);
        }
        let (reopened_root, reopened_root_marker) =
            open_staging_directory(self.root_path(), false)?;
        if reopened_root_marker != root_marker {
            return Err(HeleosError::PolicyDenied);
        }
        drop(reopened_root);

        let mut entries = std::fs::read_dir(self.root_path()).map_err(HeleosError::Io)?;
        let entry = entries
            .next()
            .ok_or(HeleosError::PolicyDenied)?
            .map_err(HeleosError::Io)?;
        if entry.file_name() != "input.pdf" || entries.next().is_some() {
            return Err(HeleosError::PolicyDenied);
        }
        let path_metadata = std::fs::symlink_metadata(entry.path()).map_err(HeleosError::Io)?;
        if path_metadata.file_type().is_symlink() || !path_metadata.is_file() {
            return Err(HeleosError::PolicyDenied);
        }

        let input = self.input.as_ref().ok_or(HeleosError::Integrity)?;
        let input_marker = self.input_marker.ok_or(HeleosError::Integrity)?;
        verify_read_only_input(input)?;
        if staging_marker(input)? != input_marker
            || input.metadata().map_err(HeleosError::Io)?.len() != self.expected_length
            || hash_exact_file(input, self.expected_length)? != self.expected_digest
        {
            return Err(HeleosError::PolicyDenied);
        }
        let (reopened_input, reopened_input_marker) = open_staging_input_read_only(&entry.path())?;
        if reopened_input_marker != input_marker {
            return Err(HeleosError::PolicyDenied);
        }
        drop(reopened_input);
        self.parent()?.recheck()
    }

    fn recheck_at(&self, point: StagingRecheckPoint) -> Result<()> {
        #[cfg(all(test, windows))]
        if let Some(observer) = &self.recheck_observer {
            observer(point);
        }
        #[cfg(not(all(test, windows)))]
        let _ = point;
        self.recheck()
    }

    #[cfg(unix)]
    fn displaced_root_name(&self) -> Result<Option<std::ffi::OsString>> {
        let Some(root_marker) = self.root_marker else {
            return Ok(None);
        };
        let temporary = self.temporary.as_ref().ok_or(HeleosError::Integrity)?;
        let expected_name = temporary.path().file_name().ok_or(HeleosError::Integrity)?;
        let parent = self.parent()?;
        parent.recheck()?;
        let directory =
            cap_std::fs::Dir::from_std_file(parent.retained.try_clone().map_err(HeleosError::Io)?);
        let mut found = None;
        for entry in directory.entries().map_err(HeleosError::Io)? {
            let entry = entry.map_err(HeleosError::Io)?;
            let name = entry.file_name();
            let metadata = directory.symlink_metadata(&name).map_err(HeleosError::Io)?;
            if !metadata.is_dir() {
                continue;
            }
            let marker = StagingMarker {
                first: cap_fs_ext::MetadataExt::dev(&metadata),
                second: cap_fs_ext::MetadataExt::ino(&metadata),
            };
            if marker == root_marker && found.replace(name).is_some() {
                return Err(HeleosError::PolicyDenied);
            }
        }
        parent.recheck()?;
        match found {
            Some(name) if name == expected_name => Ok(None),
            Some(name) => Ok(Some(name)),
            None => Err(HeleosError::PolicyDenied),
        }
    }

    #[cfg(not(unix))]
    fn displaced_root_name(&self) -> Result<Option<std::ffi::OsString>> {
        Ok(None)
    }

    fn remove_displaced_root(&self, name: &std::ffi::OsStr) -> Result<()> {
        let parent = self.parent()?;
        parent.recheck()?;
        let directory =
            cap_std::fs::Dir::from_std_file(parent.retained.try_clone().map_err(HeleosError::Io)?);
        directory.remove_dir_all(name).map_err(HeleosError::Io)?;
        parent.recheck()
    }

    #[cfg(all(test, windows))]
    fn set_recheck_observer(&mut self, observer: StagingRecheckObserver) {
        self.recheck_observer = Some(observer);
    }

    fn cleanup_inner(&mut self) -> Result<()> {
        let displaced_root = self.displaced_root_name();
        self.input.take();
        self.root.take();
        let cleanup = self
            .temporary
            .take()
            .map(tempfile::TempDir::close)
            .transpose()
            .map(|_| ())
            .map_err(HeleosError::Io);
        let displaced_cleanup = match displaced_root {
            Ok(Some(name)) => self.remove_displaced_root(&name),
            Ok(None) => Ok(()),
            Err(error) => Err(error),
        };
        let parent = self.parent.as_ref().map(ParentAnchor::recheck).transpose();
        self.parent.take();
        match (cleanup, displaced_cleanup, parent) {
            (_, _, Err(error)) => Err(error),
            (_, Err(error), Ok(_)) => Err(error),
            (Err(error), Ok(()), Ok(_)) => Err(error),
            (Ok(()), Ok(()), Ok(_)) => Ok(()),
        }
    }

    fn close(mut self) -> Result<()> {
        self.cleanup_inner()
    }
}

impl Drop for StagedInput {
    fn drop(&mut self) {
        let _ = self.cleanup_inner();
    }
}

struct StagedCleanupObserver {
    staged: Option<StagedInput>,
    cleanup_sender: Option<SyncSender<Result<()>>>,
}

impl StagedCleanupObserver {
    fn new(staged: StagedInput, cleanup_sender: SyncSender<Result<()>>) -> Self {
        Self {
            staged: Some(staged),
            cleanup_sender: Some(cleanup_sender),
        }
    }

    fn staged(&self) -> Result<&StagedInput> {
        self.staged.as_ref().ok_or(HeleosError::Integrity)
    }

    fn report_cleanup(&mut self) {
        let cleanup = self
            .staged
            .take()
            .ok_or(HeleosError::Integrity)
            .and_then(StagedInput::close);
        if let Some(sender) = self.cleanup_sender.take() {
            let _ = sender.send(cleanup);
        }
    }

    fn close(mut self) {
        self.report_cleanup();
    }
}

impl Drop for StagedCleanupObserver {
    fn drop(&mut self) {
        if self.staged.is_some() || self.cleanup_sender.is_some() {
            self.report_cleanup();
        }
    }
}

#[derive(Clone, Copy, Debug)]
struct RuntimeDeadlines {
    wall: Duration,
    epoch_ticks: u64,
}

impl RuntimeDeadlines {
    fn from_limits(limits: &ValidatedLimits) -> Result<Self> {
        let milliseconds = limits
            .public
            .timeout_seconds
            .checked_mul(1_000)
            .ok_or(HeleosError::PolicyDenied)?;
        let epoch_ticks = milliseconds
            .checked_add(9)
            .ok_or(HeleosError::PolicyDenied)?
            / 10;
        Ok(Self {
            wall: Duration::from_secs(limits.public.timeout_seconds),
            epoch_ticks,
        })
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct HostCallMarker;

impl std::fmt::Display for HostCallMarker {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("typed sandbox host-call limit")
    }
}

impl std::error::Error for HostCallMarker {}

struct StoreState {
    wasi: wasmtime_wasi::p1::WasiP1Ctx,
    limiter: RecordingLimiter,
    host_calls: u32,
    signals: RuntimeSignals,
}

struct ModuleExecution {
    stdout: bytes::Bytes,
    decision: RuntimeDecision,
}

enum ModuleExecutionError {
    Instantiate,
    Start,
    Call(wasmtime::Error),
}

fn admit_instantiation_failure(signals: &RuntimeSignals) -> Result<()> {
    if signals.first_limiter_denial().is_some() || signals.snapshot().resource_failure {
        Ok(())
    } else {
        Err(HeleosError::Integrity)
    }
}

fn map_wasi_setup_error(error: wasmtime::Error) -> HeleosError {
    match error.downcast::<std::io::Error>() {
        Ok(error) => HeleosError::Io(error),
        Err(_) => HeleosError::Integrity,
    }
}

async fn execute_module(
    module: Arc<wasmtime::Module>,
    preopen: Option<&StagedInput>,
    request: Vec<u8>,
    input_digest: Sha256Digest,
    limits: ValidatedLimits,
    deadlines: RuntimeDeadlines,
) -> Result<ModuleExecution> {
    let signals = RuntimeSignals::default();
    if let Err(kind) = static_resource_admission(&module, &limits) {
        signals.record_limiter(kind);
        return Ok(ModuleExecution {
            stdout: bytes::Bytes::new(),
            decision: arbitrate_runtime(&signals, CompletionEvidence::default()),
        });
    }
    let mut instance_attempts = 0_u32;
    if let Err(kind) = record_instance_attempt(
        &mut instance_attempts,
        limits.public.max_instances,
        &signals,
    ) {
        signals.record_limiter(kind);
        return Ok(ModuleExecution {
            stdout: bytes::Bytes::new(),
            decision: arbitrate_runtime(&signals, CompletionEvidence::default()),
        });
    }

    let stdout = RecordingStdout::new(limits.output_cap);
    let stderr = RecordingClosedStderr::new();
    let stdin = wasmtime_wasi::p2::pipe::MemoryInputPipe::new(request);
    let material = derive_rng_material(input_digest);
    let seed = u128::from_be_bytes(
        material.seed[..16]
            .try_into()
            .map_err(|_| HeleosError::Integrity)?,
    );
    let mut builder = wasmtime_wasi::WasiCtxBuilder::new();
    builder
        .stdin(stdin)
        .stdout(stdout.clone())
        .stderr(stderr.clone());
    if let Some(staged) = preopen {
        staged.recheck_at(StagingRecheckPoint::BeforePreopen)?;
        builder
            .preopened_dir(
                staged.root_path(),
                "/input",
                wasmtime_wasi::FsPerms::ReadOnly,
            )
            .map_err(map_wasi_setup_error)?;
        staged.recheck_at(StagingRecheckPoint::AfterPreopen)?;
    }
    builder
        .secure_random(wasmtime_wasi::random::Deterministic::new(
            material.secure.to_vec(),
        ))
        .insecure_random(wasmtime_wasi::random::Deterministic::new(
            material.insecure.to_vec(),
        ))
        .insecure_random_seed(seed)
        .max_random_size(32);
    let wasi = builder.build_p1();

    let memory_bytes = usize::try_from(limits.public.max_guest_memory_bytes)
        .map_err(|_| HeleosError::PolicyDenied)?;
    let instances =
        usize::try_from(limits.public.max_instances).map_err(|_| HeleosError::PolicyDenied)?;
    let tables =
        usize::try_from(limits.public.max_tables).map_err(|_| HeleosError::PolicyDenied)?;
    let limiter = RecordingLimiter::new(memory_bytes, 10_000, instances, tables, signals.clone());
    let state = StoreState {
        wasi,
        limiter,
        host_calls: 0,
        signals: signals.clone(),
    };
    let mut store = wasmtime::Store::new(module.engine(), state);
    store.limiter(|state| &mut state.limiter);
    store
        .set_fuel(limits.public.max_fuel)
        .map_err(|_| HeleosError::Integrity)?;
    store.set_epoch_deadline(deadlines.epoch_ticks);
    store.call_hook(|mut context, hook| {
        if matches!(hook, wasmtime::CallHook::CallingHost) {
            let state = context.data_mut();
            state.host_calls = state
                .host_calls
                .checked_add(1)
                .ok_or_else(|| wasmtime::Error::new(HostCallMarker))?;
            if state.host_calls > 256 {
                state.signals.record_host_calls();
                return Err(wasmtime::Error::new(HostCallMarker));
            }
        }
        Ok(())
    });

    let mut linker = wasmtime::Linker::new(module.engine());
    wasmtime_wasi::p1::add_to_linker_async(&mut linker, |state: &mut StoreState| &mut state.wasi)
        .map_err(|_| HeleosError::Integrity)?;

    let execution = tokio::time::timeout(deadlines.wall, async {
        let instance = linker
            .instantiate_async(&mut store, &module)
            .await
            .map_err(|_| ModuleExecutionError::Instantiate)?;
        let mut lookup_observer = |_: &'static str| {};
        let start = resolve_guest_start(&instance, &mut store, &mut lookup_observer)
            .map_err(|_| ModuleExecutionError::Start)?;
        start
            .call_async(&mut store, ())
            .await
            .map_err(ModuleExecutionError::Call)
    })
    .await;

    if stdout.overflowed() {
        signals.record_output();
    }
    if stderr.attempted.load(Ordering::SeqCst) {
        signals.record_stderr();
    }

    let mut completion = CompletionEvidence::default();
    match execution {
        Err(_) => completion.outer_timeout = true,
        Ok(Ok(())) => {}
        Ok(Err(ModuleExecutionError::Instantiate)) => {
            admit_instantiation_failure(&signals)?;
        }
        Ok(Err(ModuleExecutionError::Start)) => return Err(HeleosError::Integrity),
        Ok(Err(ModuleExecutionError::Call(error))) => {
            if let Some(trap) = error.downcast_ref::<wasmtime::Trap>() {
                completion.out_of_fuel = *trap == wasmtime::Trap::OutOfFuel;
                completion.interrupted = *trap == wasmtime::Trap::Interrupt;
                completion.other_trap = !completion.out_of_fuel && !completion.interrupted;
            } else if let Some(exit) = error.downcast_ref::<wasmtime_wasi::I32Exit>() {
                completion.zero_exit = exit.0 == 0;
                completion.nonzero_exit = exit.0 != 0;
            } else if error.downcast_ref::<RuntimeLimitMarker>().is_none()
                && error.downcast_ref::<HostCallMarker>().is_none()
            {
                completion.other_trap = true;
            }
        }
    }

    if let Some(staged) = preopen {
        staged.recheck_at(StagingRecheckPoint::BeforeOutputAcceptance)?;
    }

    Ok(ModuleExecution {
        stdout: stdout.contents(),
        decision: arbitrate_runtime(&signals, completion),
    })
}

fn quarantine_outcome(
    content_sha256: Sha256Digest,
    byte_length: u64,
    provenance: PdfProbeProvenance,
    reason: PdfQuarantineReason,
) -> PdfProbeOutcome {
    PdfProbeOutcome::Quarantined(PdfQuarantine {
        content_sha256,
        byte_length,
        provenance,
        reason,
    })
}

fn map_active_feature(feature: PdfActiveFeatureV1) -> PdfActiveFeature {
    match feature {
        PdfActiveFeatureV1::OpenAction => PdfActiveFeature::OpenAction,
        PdfActiveFeatureV1::AdditionalActions => PdfActiveFeature::AdditionalActions,
        PdfActiveFeatureV1::JavaScriptAbbreviation => PdfActiveFeature::JavaScriptAbbreviation,
        PdfActiveFeatureV1::JavaScript => PdfActiveFeature::JavaScript,
        PdfActiveFeatureV1::Launch => PdfActiveFeature::Launch,
        PdfActiveFeatureV1::Uri => PdfActiveFeature::Uri,
        PdfActiveFeatureV1::GoToRemote => PdfActiveFeature::GoToRemote,
        PdfActiveFeatureV1::SubmitForm => PdfActiveFeature::SubmitForm,
        PdfActiveFeatureV1::ImportData => PdfActiveFeature::ImportData,
        PdfActiveFeatureV1::RichMedia => PdfActiveFeature::RichMedia,
        PdfActiveFeatureV1::EmbeddedFiles => PdfActiveFeature::EmbeddedFiles,
        PdfActiveFeatureV1::AssociatedFiles => PdfActiveFeature::AssociatedFiles,
        PdfActiveFeatureV1::Xfa => PdfActiveFeature::Xfa,
        PdfActiveFeatureV1::AcroForm => PdfActiveFeature::AcroForm,
    }
}

fn map_document_limit(limit: PdfDocumentLimitV1) -> PdfLimitKind {
    match limit {
        PdfDocumentLimitV1::InputBytes => PdfLimitKind::InputBytes,
        PdfDocumentLimitV1::Pages => PdfLimitKind::Pages,
        PdfDocumentLimitV1::IndirectObjects => PdfLimitKind::IndirectObjects,
        PdfDocumentLimitV1::NestedReferences => PdfLimitKind::NestedReferences,
        PdfDocumentLimitV1::MetadataBytes => PdfLimitKind::MetadataBytes,
        PdfDocumentLimitV1::PageAxisPoints => PdfLimitKind::PageAxisPoints,
    }
}

fn map_guest_reason(reason: PdfGuestReasonV1) -> PdfQuarantineReason {
    match reason {
        PdfGuestReasonV1::BadMagic => PdfQuarantineReason::BadMagic,
        PdfGuestReasonV1::Corrupt => PdfQuarantineReason::Corrupt,
        PdfGuestReasonV1::Encrypted => PdfQuarantineReason::Encrypted,
        PdfGuestReasonV1::InvalidGeometry => PdfQuarantineReason::InvalidGeometry,
        PdfGuestReasonV1::UnsupportedUserUnit => PdfQuarantineReason::UnsupportedUserUnit,
        PdfGuestReasonV1::ActiveFeature(feature) => {
            PdfQuarantineReason::ActiveFeature(map_active_feature(feature))
        }
        PdfGuestReasonV1::LimitExceeded(limit) => {
            PdfQuarantineReason::LimitExceeded(map_document_limit(limit))
        }
    }
}

fn response_pages(
    pages: Vec<heleos_pdf_protocol::PdfPageV1>,
    content_sha256: Sha256Digest,
    limits: &ValidatedLimits,
) -> std::result::Result<Vec<PageMetadata>, ()> {
    if pages.len() > usize::try_from(limits.public.max_pages).map_err(|_| ())? {
        return Err(());
    }
    let mut result = Vec::with_capacity(pages.len());
    for (expected, page) in pages.into_iter().enumerate() {
        let index = u32::try_from(expected).map_err(|_| ())?;
        let page_id = crate::page_id(content_sha256, index);
        let expected_page_id = page_id.as_digest().to_string();
        if page.index != index || page.page_id != expected_page_id {
            return Err(());
        }
        let unit = match page.unit {
            PageUnitV1::Point => PageUnit::Point,
        };
        result.push(PageMetadata {
            index,
            page_id,
            width_micropoints: page.width_micropoints,
            height_micropoints: page.height_micropoints,
            unit,
            rotation_degrees: page.rotation_degrees,
            transform: PageTransform {
                m11: page.transform.m11,
                m12: page.transform.m12,
                m21: page.transform.m21,
                m22: page.transform.m22,
                tx_micropoints: page.transform.tx_micropoints,
                ty_micropoints: page.transform.ty_micropoints,
            },
        });
    }
    validate_page_metadata(&result, content_sha256, limits.public).map_err(|_| ())?;
    Ok(result)
}

fn translate_module_execution(
    execution: ModuleExecution,
    revision: RevisionId,
    content_sha256: Sha256Digest,
    byte_length: u64,
    provenance: PdfProbeProvenance,
    limits: ValidatedLimits,
) -> Result<PdfProbeOutcome> {
    let runtime_reason = match execution.decision {
        RuntimeDecision::Limit(kind) => Some(PdfQuarantineReason::LimitExceeded(kind)),
        RuntimeDecision::SandboxTrap => Some(PdfQuarantineReason::SandboxTrap),
        RuntimeDecision::ProtocolBreach => Some(PdfQuarantineReason::ProtocolBreach),
        RuntimeDecision::Proceed => None,
    };
    if let Some(reason) = runtime_reason {
        return Ok(quarantine_outcome(
            content_sha256,
            byte_length,
            provenance,
            reason,
        ));
    }

    let response: PdfResponseV1 = match decode_response_v1(&execution.stdout, limits.output_cap) {
        Ok(response) => response,
        Err(_) => {
            return Ok(quarantine_outcome(
                content_sha256,
                byte_length,
                provenance,
                PdfQuarantineReason::ProtocolBreach,
            ));
        }
    };
    if response.protocol != PROTOCOL_VERSION
        || response.input_sha256 != content_sha256.to_string()
        || response.byte_length != byte_length
    {
        return Ok(quarantine_outcome(
            content_sha256,
            byte_length,
            provenance,
            PdfQuarantineReason::ProtocolBreach,
        ));
    }
    match response.outcome {
        PdfGuestOutcomeV1::Rejected { reason } => Ok(quarantine_outcome(
            content_sha256,
            byte_length,
            provenance,
            map_guest_reason(reason),
        )),
        PdfGuestOutcomeV1::Accepted { pages } => {
            let pages = match response_pages(pages, content_sha256, &limits) {
                Ok(pages) => pages,
                Err(()) => {
                    return Ok(quarantine_outcome(
                        content_sha256,
                        byte_length,
                        provenance,
                        PdfQuarantineReason::ProtocolBreach,
                    ));
                }
            };
            Ok(PdfProbeOutcome::Accepted(PdfInspection {
                revision_id: revision,
                content_sha256,
                byte_length,
                provenance,
                pages,
            }))
        }
    }
}

fn manifest_provenance(manifest: &GuestManifestV1) -> Result<PdfProbeProvenance> {
    Ok(PdfProbeProvenance {
        parser_name: "lopdf".to_owned(),
        parser_version: "0.44.0".to_owned(),
        guest_wasm_sha256: parse_digest(&manifest.wasm_sha256)?,
        guest_source_tree_sha256: parse_digest(&manifest.source_tree_sha256)?,
        guest_dependency_graph_sha256: parse_digest(&manifest.dependency_graph_sha256)?,
        protocol_version: PROTOCOL_VERSION.to_owned(),
    })
}

fn verify_source_object(input: &mut VerifiedObject) -> Result<()> {
    use sha2::{Digest, Sha256};

    if input.byte_length() > heleos_pdf_protocol::JCS_SAFE_INTEGER_MAX {
        return Err(HeleosError::Integrity);
    }
    input.seek(SeekFrom::Start(0)).map_err(HeleosError::Io)?;
    let mut remaining = input.byte_length();
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];
    while remaining > 0 {
        let wanted = usize::try_from(remaining.min(buffer.len() as u64))
            .map_err(|_| HeleosError::Integrity)?;
        let read = input.read(&mut buffer[..wanted]).map_err(HeleosError::Io)?;
        if read == 0 {
            return Err(HeleosError::Integrity);
        }
        hasher.update(&buffer[..read]);
        remaining = remaining
            .checked_sub(u64::try_from(read).map_err(|_| HeleosError::Integrity)?)
            .ok_or(HeleosError::Integrity)?;
    }
    let mut trailing = [0_u8; 1];
    if input.read(&mut trailing).map_err(HeleosError::Io)? != 0
        || Sha256Digest::from_bytes(hasher.finalize().into()) != input.digest()
    {
        return Err(HeleosError::Integrity);
    }
    Ok(())
}

fn protocol_request(
    content_sha256: Sha256Digest,
    byte_length: u64,
    limits: &ValidatedLimits,
) -> Result<Vec<u8>> {
    let request = PdfRequestV1 {
        protocol: PROTOCOL_VERSION.to_owned(),
        input_sha256: content_sha256.to_string(),
        byte_length,
        limits: PdfDocumentLimitsV1 {
            max_input_bytes: limits.public.max_input_bytes,
            max_pages: limits.public.max_pages,
            max_indirect_objects: limits.public.max_indirect_objects,
            max_nested_references: limits.public.max_nested_references,
            max_metadata_bytes: limits.public.max_metadata_bytes,
            max_page_axis_points: limits.public.max_page_axis_points,
        },
    };
    encode_request_v1(&request).map_err(|_| HeleosError::Integrity)
}

pub struct WasiPdfProbe {
    module: Arc<wasmtime::Module>,
    manifest: Arc<GuestManifestV1>,
    config: PdfSandboxConfig,
    admission: Arc<Admission>,
    runtime: Option<RuntimeOwner>,
    ticker: Option<EpochTicker>,
}

impl std::fmt::Debug for WasiPdfProbe {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str("WasiPdfProbe")
    }
}

fn finish_staged_candidate<T>(
    candidate: Result<T>,
    final_check: Result<()>,
    cleanup: Result<()>,
) -> Result<T> {
    let candidate = match final_check {
        Ok(()) => candidate,
        Err(error) => Err(error),
    };
    match cleanup {
        Ok(()) => candidate,
        Err(error) => Err(error),
    }
}

impl WasiPdfProbe {
    pub fn new(guest: ApprovedPdfGuest, config: PdfSandboxConfig) -> Result<Self> {
        let parent = ParentAnchor::open(&config.staging_parent)?;
        parent.recheck()?;
        drop(parent);

        let engine = engine()?;
        let module = wasmtime::Module::from_binary(&engine, &guest.wasm)
            .map_err(|_| HeleosError::Integrity)?;
        let runtime = RuntimeOwner::start()?;
        let ticker = EpochTicker::start(engine)?;
        Ok(Self {
            module: Arc::new(module),
            manifest: guest.manifest,
            config,
            admission: Arc::new(Admission::new(2)),
            runtime: Some(runtime),
            ticker: Some(ticker),
        })
    }
}

impl PdfProbe for WasiPdfProbe {
    fn provenance(&self) -> Result<PdfProbeProvenance> {
        manifest_provenance(&self.manifest)
    }

    fn probe(
        &self,
        mut input: VerifiedObject,
        revision: RevisionId,
        limits: PdfLimits,
    ) -> Result<PdfProbeOutcome> {
        if revision.as_digest() != &input.digest() {
            return Err(HeleosError::Integrity);
        }
        let limits = validate_limits(limits)?;
        verify_source_object(&mut input)?;
        let content_sha256 = input.digest();
        let byte_length = input.byte_length();
        let provenance = self.provenance()?;
        if byte_length > limits.public.max_input_bytes {
            return Ok(quarantine_outcome(
                content_sha256,
                byte_length,
                provenance,
                PdfQuarantineReason::LimitExceeded(PdfLimitKind::InputBytes),
            ));
        }

        let permit = self.admission.acquire()?;
        let request = protocol_request(content_sha256, byte_length, &limits)?;
        let deadlines = RuntimeDeadlines::from_limits(&limits)?;
        let staged = StagedInput::create(
            &self.config.staging_parent,
            &mut input,
            byte_length,
            content_sha256,
        )?;
        let module = Arc::clone(&self.module);
        let (result_sender, result_receiver) = sync_channel(1);
        let (cleanup_sender, cleanup_receiver) = sync_channel(1);
        let staged = StagedCleanupObserver::new(staged, cleanup_sender);
        let job: OwnedRuntimeJob = Box::pin(async move {
            let candidate = match staged.staged() {
                Ok(staged_input) => execute_module(
                    module,
                    Some(staged_input),
                    request,
                    content_sha256,
                    limits,
                    deadlines,
                )
                .await
                .and_then(|execution| {
                    translate_module_execution(
                        execution,
                        revision,
                        content_sha256,
                        byte_length,
                        provenance,
                        limits,
                    )
                }),
                Err(error) => Err(error),
            };
            let final_check = staged.staged().and_then(StagedInput::recheck);
            staged.close();
            let _ = result_sender.send((candidate, final_check));
        });
        let dispatch = match self.runtime.as_ref() {
            Some(runtime) => runtime.dispatch(job),
            None => {
                drop(job);
                Err(HeleosError::Integrity)
            }
        };
        let candidate = match dispatch {
            Ok(()) => result_receiver.recv().map_err(|_| HeleosError::Integrity),
            Err(error) => Err(error),
        };
        let cleanup = cleanup_receiver
            .recv()
            .unwrap_or(Err(HeleosError::Integrity));
        drop(permit);
        match candidate {
            Ok((candidate, final_check)) => {
                finish_staged_candidate(candidate, final_check, cleanup)
            }
            Err(error) => finish_staged_candidate(Err(error), Ok(()), cleanup),
        }
    }
}

impl Drop for WasiPdfProbe {
    fn drop(&mut self) {
        self.admission.close();
        self.runtime.take();
        self.ticker.take();
    }
}

#[cfg(test)]
mod tests {
    use std::str::FromStr;
    use std::sync::atomic::Ordering;

    use crate::{
        PdfLimitKind, PdfLimits, PdfProbe, PdfProbeOutcome, PdfProbeProvenance,
        PdfQuarantineReason, PutOutcome, RevisionId, Sha256Digest, Vault, VaultConfig,
        VaultOpenMode, VaultWriteBudget,
    };
    use heleos_pdf_protocol::{
        CoreValueTypeV1, ExportRecordV1, ImportKindV1, ImportRecordV1, PROTOCOL_VERSION,
        PageTransformV1, PageUnitV1, PdfGuestOutcomeV1, PdfPageV1, PdfResponseV1,
        encode_response_v1, extract_module_records,
    };

    #[cfg(windows)]
    use super::StagingRecheckPoint;
    use super::{
        Admission, ApprovedPdfGuest, CompletionEvidence, EpochTicker, GuestManifestV1,
        ModuleExecution, RecordingClosedStderr, RecordingLimiter, RecordingStdout,
        RuntimeDeadlines, RuntimeDecision, RuntimeOwner, RuntimeSignals, StagedCleanupObserver,
        StagedInput, admit_instantiation_failure, arbitrate_runtime, derive_rng_material, engine,
        execute_module, finish_staged_candidate, parse_manifest, record_instance_attempt,
        resolve_guest_start, static_resource_admission, translate_module_execution,
        trusted_source_tree_sha256, validate_limits, validate_module_policy,
    };

    #[test]
    fn module_records_capture_full_function_and_resource_shapes() {
        let wasm = wat::parse_str(
            r#"(module
                (import "wasi_snapshot_preview1" "fd_read"
                    (func $read (param i32 i32 i32 i32) (result i32)))
                (func (export "_start"))
                (memory (export "memory") 2 9)
                (table (export "table") 3 7 funcref))"#,
        )
        .expect("test WAT compiles");

        let (imports, exports) = extract_module_records(&wasm).expect("module records extract");
        assert_eq!(
            imports,
            vec![ImportRecordV1 {
                module: "wasi_snapshot_preview1".to_owned(),
                name: "fd_read".to_owned(),
                kind: ImportKindV1::Func,
                params: vec![CoreValueTypeV1::I32; 4],
                results: vec![CoreValueTypeV1::I32],
            }]
        );
        assert_eq!(
            exports,
            vec![
                ExportRecordV1::Func {
                    name: "_start".to_owned(),
                    params: vec![],
                    results: vec![],
                },
                ExportRecordV1::Memory {
                    name: "memory".to_owned(),
                    minimum_pages: 2,
                    maximum_pages: Some(9),
                    memory64: false,
                    shared: false,
                    page_size_log2: None,
                },
                ExportRecordV1::Table {
                    name: "table".to_owned(),
                    element: CoreValueTypeV1::Funcref,
                    minimum_elements: 3,
                    maximum_elements: Some(7),
                    table64: false,
                },
            ]
        );
    }

    #[test]
    fn module_records_reject_start_non_function_import_and_duplicate_export() {
        for wat in [
            r#"(module (func $start) (start $start))"#,
            r#"(module (import "x" "memory" (memory 1)))"#,
            r#"(module (func (export "same")) (memory (export "same") 1))"#,
        ] {
            let wasm = wat::parse_str(wat).expect("malicious test WAT compiles");
            assert!(extract_module_records(&wasm).is_err(), "accepted {wat}");
        }
    }

    #[test]
    fn module_records_reject_gc_operators_even_with_core_reference_operands() {
        let wasm = wat::parse_str(
            r#"(module
                (func
                    ref.null extern
                    ref.null extern
                    ref.eq
                    drop))"#,
        )
        .expect("the malicious GC-operator module is syntactically valid");

        assert!(
            extract_module_records(&wasm).is_err(),
            "GC proposal operators are outside the frozen artifact shape"
        );
    }

    #[test]
    fn module_records_reject_unsupported_reference_operators_and_heap_types() {
        for wat in [
            r#"(module (func ref.null any drop))"#,
            r#"(module (func (param funcref) local.get 0 ref.as_non_null drop))"#,
        ] {
            let wasm = wat::parse_str(wat)
                .unwrap_or_else(|error| panic!("reference-shape WAT must encode: {wat}: {error}"));
            assert!(
                extract_module_records(&wasm).is_err(),
                "accepted unsupported reference shape: {wat}"
            );
        }
    }

    #[test]
    fn module_records_reject_out_of_range_active_element_table() {
        let wasm = wat::parse_str(
            r#"(module
                (table 1 funcref)
                (elem (table 1) (i32.const 0) func))"#,
        )
        .expect("the out-of-range element-table module is syntactically valid");

        assert!(
            extract_module_records(&wasm).is_err(),
            "active element table indices must resolve in the frozen shape"
        );
    }

    #[test]
    fn module_records_reject_out_of_range_element_function() {
        let wasm = wat::parse_str(
            r#"(module
                (table 1 funcref)
                (elem (i32.const 0) func 7))"#,
        )
        .expect("the out-of-range element-function module is syntactically valid");

        assert!(
            extract_module_records(&wasm).is_err(),
            "element function indices must resolve in the frozen shape"
        );
    }

    #[test]
    fn module_records_reject_out_of_range_active_data_memory() {
        let wasm = wat::parse_str(
            r#"(module
                (memory 1)
                (data (memory 1) (i32.const 0) "x"))"#,
        )
        .expect("the out-of-range data-memory module is syntactically valid");

        assert!(
            extract_module_records(&wasm).is_err(),
            "active data memory indices must resolve in the frozen shape"
        );
    }

    #[test]
    fn module_records_reject_shared_globals() {
        let wasm = wat::parse_str(r#"(module (global (shared i32) (i32.const 0)))"#)
            .expect("the shared-global module is syntactically valid");

        assert!(
            extract_module_records(&wasm).is_err(),
            "shared-everything globals are outside the frozen artifact shape"
        );
    }

    #[test]
    fn module_records_reject_inverted_resource_limits() {
        for wat in [
            r#"(module (memory 2 1))"#,
            r#"(module (table 2 1 funcref))"#,
        ] {
            let wasm = wat::parse_str(wat)
                .unwrap_or_else(|error| panic!("resource-shape WAT must encode: {wat}: {error}"));
            assert!(
                extract_module_records(&wasm).is_err(),
                "accepted inverted resource limits: {wat}"
            );
        }
    }

    #[test]
    fn module_records_reject_duplicate_and_out_of_order_sections() {
        let header = b"\0asm\x01\0\0\0";
        let duplicate_empty_type_sections = [header.as_slice(), &[1, 1, 0, 1, 1, 0]].concat();
        let memory_then_type = [header.as_slice(), &[5, 1, 0, 1, 1, 0]].concat();

        for wasm in [duplicate_empty_type_sections, memory_then_type] {
            assert!(
                extract_module_records(&wasm).is_err(),
                "noncanonical core-section shape was accepted"
            );
        }
    }

    #[test]
    fn tracked_source_allowlist_is_lf_only_and_hashes_raw_embedded_bytes() {
        let digest = trusted_source_tree_sha256().expect("trusted source tree hashes");
        assert_ne!(
            digest,
            Sha256Digest::from_str(&"0".repeat(64)).expect("zero digest parses")
        );
    }

    #[test]
    fn manifest_parser_rejects_unknown_duplicate_and_invalid_trust_fields() {
        let valid = concat!(
            "schema = \"heleos.pdf-guest-manifest/v1\"\n",
            "protocol = \"heleos.pdf-probe/v1\"\n",
            "wasm_sha256 = \"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\"\n",
            "wasm_byte_length = 8\n",
            "source_tree_sha256 = \"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb\"\n",
            "dependency_graph_sha256 = \"cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc\"\n",
            "guest_resolution_lock_sha256 = \"ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff\"\n",
            "target = \"wasm32-wasip1\"\n",
            "profile = \"release\"\n",
            "rustc = \"1.96.1\"\n",
            "rust_path_remap = \"heleos-rust-path-remap/v1\"\n",
            "imports_sha256 = \"dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd\"\n",
            "exports_sha256 = \"eeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeeee\"\n",
            "build_command = \"cargo +1.96.1 build --locked -p heleos-pdf-guest --target wasm32-wasip1 --release --message-format=json-render-diagnostics --quiet\"\n",
            "dependencies = []\nimports = []\nexports = []\n",
        );
        assert!(parse_manifest(valid).is_ok());
        assert!(parse_manifest(&format!("{valid}unknown = true\n")).is_err());
        assert!(parse_manifest(&format!("{valid}schema = \"duplicate\"\n")).is_err());
        assert!(
            parse_manifest(&valid.replace(
                "wasm_byte_length = 8",
                "wasm_byte_length = 9007199254740992"
            ))
            .is_err()
        );
        assert!(
            parse_manifest(&valid.replace("rustc = \"1.96.1\"", "rustc = \"nightly\"")).is_err()
        );
        assert!(
            parse_manifest(&valid.replace(
                "rust_path_remap = \"heleos-rust-path-remap/v1\"",
                "rust_path_remap = \"unreviewed/v2\""
            ))
            .is_err()
        );
    }

    #[test]
    fn binary_policy_accepts_only_exact_wasip1_functions_and_start_export() {
        let allowed = wat::parse_str(
            r#"(module
                (import "wasi_snapshot_preview1" "random_get"
                    (func (param i32 i32) (result i32)))
                (func (export "_start"))
                (func (export "__main_void") (result i32) i32.const 0))"#,
        )
        .expect("allowed test WAT compiles");
        assert!(validate_module_policy(&allowed).is_ok());

        for wat in [
            r#"(module (import "wasi_unstable" "random_get" (func (param i32 i32) (result i32))) (func (export "_start")))"#,
            r#"(module (import "wasi_snapshot_preview1" "clock_time_get" (func (param i32 i64 i32) (result i32))) (func (export "_start")))"#,
            r#"(module (import "wasi_snapshot_preview1" "random_get" (func (param i64 i32) (result i32))) (func (export "_start")))"#,
            r#"(module (func (export "_start") (param i32)))"#,
            r#"(module (func (export "_start")))"#,
            r#"(module (func (export "__main_void") (result i32) i32.const 0))"#,
            r#"(module (func (export "_start")) (func (export "__main_void")))"#,
            r#"(module (func (export "_start")) (func (export "__main_void") (result i32) i32.const 0) (func (export "extra")))"#,
        ] {
            let wasm = wat::parse_str(wat).expect("policy test WAT compiles");
            assert!(validate_module_policy(&wasm).is_err(), "accepted {wat}");
        }
    }

    #[test]
    fn production_entry_lookup_resolves_only_start_not_main_void() {
        let engine = engine().expect("production engine builds");
        let module = wasmtime::Module::from_binary(
            &engine,
            &wat::parse_str(
                r#"(module
                    (func (export "_start"))
                    (func (export "__main_void") (result i32) i32.const 0))"#,
            )
            .expect("entry-policy WAT compiles"),
        )
        .expect("entry-policy module compiles");
        let mut store = wasmtime::Store::new(&engine, ());
        let instance = wasmtime::Instance::new(&mut store, &module, &[])
            .expect("entry-policy module instantiates");
        let mut lookups = Vec::new();
        resolve_guest_start(&instance, &mut store, &mut |name| {
            lookups.push(name.to_owned())
        })
        .expect("required start resolves");
        assert_eq!(lookups, ["_start"]);
    }

    #[test]
    fn production_engine_disables_the_component_model() {
        let engine = engine().expect("production engine builds");
        let component = b"\0asm\x0d\0\x01\0";
        assert!(
            wasmtime::component::Component::new(&engine, component).is_err(),
            "production engine admitted a component"
        );
    }

    #[test]
    fn built_guest_module_has_a_policy_admissible_static_shape_when_supplied() {
        let Ok(path) = std::env::var("HELEOS_PDF_GUEST") else {
            return;
        };
        let path = std::path::PathBuf::from(path);
        let path = if path.is_absolute() {
            path
        } else {
            std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
                .join("../..")
                .join(path)
        };
        let wasm = std::fs::read(path).expect("read explicitly supplied built guest");
        let extracted = extract_module_records(&wasm).expect("guest records extract");
        let (imports, exports) = validate_module_policy(&wasm).unwrap_or_else(|error| {
            panic!("guest shape is admitted: {error:?}; extracted={extracted:?}")
        });
        assert!(!imports.is_empty());
        assert!(exports.iter().any(
            |record| matches!(record, ExportRecordV1::Func { name, params, results } if name == "_start" && params.is_empty() && results.is_empty())
        ));
        assert!(exports.iter().any(
            |record| matches!(record, ExportRecordV1::Func { name, params, results } if name == "__main_void" && params.is_empty() && results == &[CoreValueTypeV1::I32])
        ));
    }

    #[test]
    fn caller_limits_are_nonzero_and_tightening_only() {
        assert!(validate_limits(PdfLimits::default()).is_ok());
        let defaults = PdfLimits::default();
        let invalid = [
            PdfLimits {
                max_input_bytes: 0,
                ..defaults
            },
            PdfLimits {
                max_pages: 0,
                ..defaults
            },
            PdfLimits {
                max_indirect_objects: 0,
                ..defaults
            },
            PdfLimits {
                max_nested_references: 0,
                ..defaults
            },
            PdfLimits {
                max_metadata_bytes: 0,
                ..defaults
            },
            PdfLimits {
                max_page_axis_points: 0,
                ..defaults
            },
            PdfLimits {
                max_guest_memory_bytes: 0,
                ..defaults
            },
            PdfLimits {
                max_instances: 0,
                ..defaults
            },
            PdfLimits {
                max_tables: 0,
                ..defaults
            },
            PdfLimits {
                max_fuel: 0,
                ..defaults
            },
            PdfLimits {
                timeout_seconds: 0,
                ..defaults
            },
            PdfLimits {
                max_protocol_output_bytes: 0,
                ..defaults
            },
            PdfLimits {
                max_input_bytes: defaults.max_input_bytes + 1,
                ..defaults
            },
            PdfLimits {
                max_pages: defaults.max_pages + 1,
                ..defaults
            },
            PdfLimits {
                max_indirect_objects: defaults.max_indirect_objects + 1,
                ..defaults
            },
            PdfLimits {
                max_nested_references: defaults.max_nested_references + 1,
                ..defaults
            },
            PdfLimits {
                max_metadata_bytes: defaults.max_metadata_bytes + 1,
                ..defaults
            },
            PdfLimits {
                max_page_axis_points: defaults.max_page_axis_points + 1,
                ..defaults
            },
            PdfLimits {
                max_guest_memory_bytes: defaults.max_guest_memory_bytes + 1,
                ..defaults
            },
            PdfLimits {
                max_instances: defaults.max_instances + 1,
                ..defaults
            },
            PdfLimits {
                max_tables: defaults.max_tables + 1,
                ..defaults
            },
            PdfLimits {
                max_fuel: defaults.max_fuel + 1,
                ..defaults
            },
            PdfLimits {
                timeout_seconds: defaults.timeout_seconds + 1,
                ..defaults
            },
            PdfLimits {
                max_protocol_output_bytes: defaults.max_protocol_output_bytes + 1,
                ..defaults
            },
        ];
        for limits in invalid {
            assert!(validate_limits(limits).is_err(), "accepted {limits:?}");
        }
    }

    #[test]
    fn static_module_resources_map_to_exact_typed_limits() {
        let mut test_config = wasmtime::Config::new();
        test_config.wasm_multi_memory(true);
        let test_engine = wasmtime::Engine::new(&test_config).expect("test engine builds");
        let limits = validate_limits(PdfLimits {
            max_guest_memory_bytes: 65_536,
            max_tables: 1,
            ..PdfLimits::default()
        })
        .expect("limits validate");
        for wat in [
            "(module (memory 1))",
            "(module (table 1 funcref))",
            "(module (table 10000 funcref))",
        ] {
            let wasm = wat::parse_str(wat).expect("exact-boundary resource WAT compiles");
            let module =
                wasmtime::Module::from_binary(&test_engine, &wasm).expect("module compiles");
            assert_eq!(
                static_resource_admission(&module, &limits),
                Ok(()),
                "exact static resource boundary rejected: {wat}"
            );
        }
        for (wat, expected) in [
            (
                "(module (memory 2) (memory 2))",
                PdfLimitKind::GuestMemoryBytes,
            ),
            ("(module (memory 1) (memory 1))", PdfLimitKind::Memories),
            (
                "(module (table 1 funcref) (table 1 funcref))",
                PdfLimitKind::Tables,
            ),
            ("(module (memory 2))", PdfLimitKind::GuestMemoryBytes),
            (
                "(module (table 10001 funcref))",
                PdfLimitKind::TableElements,
            ),
        ] {
            let wasm = wat::parse_str(wat).expect("resource WAT compiles");
            let module =
                wasmtime::Module::from_binary(&test_engine, &wasm).expect("module compiles");
            assert_eq!(static_resource_admission(&module, &limits), Err(expected));
        }
    }

    #[test]
    fn closed_stderr_records_check_write_write_and_flush_independently() {
        use wasmtime_wasi::p2::OutputStream;

        for operation in 0..3 {
            let stderr = RecordingClosedStderr::new();
            let mut stream = stderr.clone();
            match operation {
                0 => assert!(OutputStream::check_write(&mut stream).is_err()),
                1 => assert!(
                    OutputStream::write(&mut stream, bytes::Bytes::from_static(b"x")).is_err()
                ),
                2 => assert!(OutputStream::flush(&mut stream).is_err()),
                _ => unreachable!(),
            }
            assert!(stderr.attempted.load(Ordering::SeqCst));
        }
    }

    #[test]
    fn bounded_stdout_records_only_actual_overflow() {
        use wasmtime_wasi::p2::OutputStream;

        let stdout = RecordingStdout::new(3);
        let mut stream = stdout.clone();
        OutputStream::write(&mut stream, bytes::Bytes::from_static(b"abc"))
            .expect("exact output cap is accepted");
        OutputStream::flush(&mut stream).expect("exact-cap output flushes");
        assert_eq!(stdout.contents().as_ref(), b"abc");
        assert!(!stdout.overflowed());
        assert!(OutputStream::check_write(&mut stream).is_err());
        assert!(!stdout.overflowed());
        assert!(OutputStream::check_write(&mut stream).is_err());
        assert!(stdout.overflowed());

        let stdout = RecordingStdout::new(3);
        let mut stream = stdout.clone();
        assert!(OutputStream::write(&mut stream, bytes::Bytes::from_static(b"abcd")).is_err());
        assert!(stdout.contents().is_empty());
        assert!(stdout.overflowed());
    }

    #[test]
    fn dynamic_limiter_denials_are_typed_and_module_maximum_failures_are_not() {
        use wasmtime::ResourceLimiter;

        let signals = RuntimeSignals::default();
        let mut limiter = RecordingLimiter::new(65_536, 3, 1, 1, signals.clone());
        let error = limiter
            .memory_growing(65_536, 131_072, None)
            .expect_err("configured memory denial traps");
        assert!(error.downcast_ref::<super::RuntimeLimitMarker>().is_some());
        assert_eq!(
            signals.first_limiter_denial(),
            Some(PdfLimitKind::GuestMemoryBytes)
        );

        let signals = RuntimeSignals::default();
        let mut limiter = RecordingLimiter::new(262_144, 3, 1, 1, signals.clone());
        assert!(
            limiter
                .memory_growing(65_536, 131_072, Some(65_536))
                .expect("configured cap allows the request")
        );
        assert_eq!(signals.first_limiter_denial(), None);

        let error = limiter
            .table_growing(3, 4, None)
            .expect_err("configured table denial traps");
        assert!(error.downcast_ref::<super::RuntimeLimitMarker>().is_some());
        assert_eq!(
            signals.first_limiter_denial(),
            Some(PdfLimitKind::TableElements)
        );

        let signals = RuntimeSignals::default();
        let mut attempts = 0;
        assert_eq!(record_instance_attempt(&mut attempts, 1, &signals), Ok(()));
        assert_eq!(
            record_instance_attempt(&mut attempts, 1, &signals),
            Err(PdfLimitKind::Instances)
        );
        assert_eq!(
            signals.first_limiter_denial(),
            Some(PdfLimitKind::Instances)
        );
    }

    #[test]
    fn compound_runtime_signals_follow_the_frozen_priority() {
        let cases = [
            (
                RuntimeSignals::with_limiter_denials([
                    PdfLimitKind::Memories,
                    PdfLimitKind::GuestMemoryBytes,
                ]),
                CompletionEvidence::default(),
                RuntimeDecision::Limit(PdfLimitKind::GuestMemoryBytes),
            ),
            (
                RuntimeSignals::with_limiter_denials([
                    PdfLimitKind::Instances,
                    PdfLimitKind::Memories,
                ]),
                CompletionEvidence::default(),
                RuntimeDecision::Limit(PdfLimitKind::Memories),
            ),
            (
                RuntimeSignals::with_limiter_denials([
                    PdfLimitKind::Tables,
                    PdfLimitKind::Instances,
                ]),
                CompletionEvidence::default(),
                RuntimeDecision::Limit(PdfLimitKind::Instances),
            ),
            (
                RuntimeSignals::with_limiter_denials([
                    PdfLimitKind::TableElements,
                    PdfLimitKind::Tables,
                ]),
                CompletionEvidence::default(),
                RuntimeDecision::Limit(PdfLimitKind::Tables),
            ),
            (
                RuntimeSignals::with_limiter_denials([
                    PdfLimitKind::TableElements,
                    PdfLimitKind::GuestMemoryBytes,
                ]),
                CompletionEvidence {
                    out_of_fuel: true,
                    protocol_breach: true,
                    ..CompletionEvidence::default()
                },
                RuntimeDecision::Limit(PdfLimitKind::GuestMemoryBytes),
            ),
            (
                RuntimeSignals::with_limiter_denials([PdfLimitKind::TableElements]),
                CompletionEvidence {
                    out_of_fuel: true,
                    ..CompletionEvidence::default()
                },
                RuntimeDecision::Limit(PdfLimitKind::TableElements),
            ),
            (
                RuntimeSignals::with_host_calls_and_output(),
                CompletionEvidence {
                    out_of_fuel: true,
                    ..CompletionEvidence::default()
                },
                RuntimeDecision::Limit(PdfLimitKind::Fuel),
            ),
            (
                RuntimeSignals::with_host_calls_and_output(),
                CompletionEvidence {
                    interrupted: true,
                    outer_timeout: true,
                    ..CompletionEvidence::default()
                },
                RuntimeDecision::Limit(PdfLimitKind::HostCalls),
            ),
            (
                RuntimeSignals::with_output_and_stderr(),
                CompletionEvidence {
                    outer_timeout: true,
                    nonzero_exit: true,
                    ..CompletionEvidence::default()
                },
                RuntimeDecision::Limit(PdfLimitKind::ProtocolOutputBytes),
            ),
            (
                RuntimeSignals::with_stderr(),
                CompletionEvidence {
                    outer_timeout: true,
                    ..CompletionEvidence::default()
                },
                RuntimeDecision::Limit(PdfLimitKind::Timeout),
            ),
            (
                RuntimeSignals::with_stderr(),
                CompletionEvidence {
                    nonzero_exit: true,
                    protocol_breach: true,
                    ..CompletionEvidence::default()
                },
                RuntimeDecision::SandboxTrap,
            ),
            (
                RuntimeSignals::default(),
                CompletionEvidence {
                    nonzero_exit: true,
                    protocol_breach: true,
                    ..CompletionEvidence::default()
                },
                RuntimeDecision::SandboxTrap,
            ),
            (
                RuntimeSignals::default(),
                CompletionEvidence {
                    zero_exit: true,
                    protocol_breach: true,
                    ..CompletionEvidence::default()
                },
                RuntimeDecision::ProtocolBreach,
            ),
        ];

        for (signals, completion, expected) in cases {
            assert_eq!(arbitrate_runtime(&signals, completion), expected);
        }
    }

    #[test]
    fn admission_blocks_the_third_call_until_a_permit_is_released() {
        let admission = std::sync::Arc::new(Admission::new(2));
        let first = admission.acquire().expect("first permit");
        let _second = admission.acquire().expect("second permit");
        let (sent, received) = std::sync::mpsc::sync_channel(1);
        let contender = std::sync::Arc::clone(&admission);
        let join = std::thread::spawn(move || {
            let permit = contender.acquire().expect("third permit after release");
            sent.send(permit).expect("report third permit");
        });
        assert!(
            received
                .recv_timeout(std::time::Duration::from_millis(20))
                .is_err(),
            "third call advanced before a permit was released"
        );
        drop(first);
        let third = received
            .recv_timeout(std::time::Duration::from_secs(1))
            .expect("third call wakes");
        drop(third);
        join.join().expect("contender joins");
    }

    #[test]
    fn runtime_owner_has_a_timer_and_is_safe_from_nested_tokio_runtimes() {
        let owner = RuntimeOwner::start().expect("runtime owner starts");
        for flavor in 0..2 {
            let (sent, received) = std::sync::mpsc::sync_channel(1);
            let invoke = || {
                owner
                    .dispatch(Box::pin(async move {
                        tokio::time::sleep(std::time::Duration::from_millis(1)).await;
                        sent.send(7_u8).expect("return timer result");
                    }))
                    .expect("job dispatches");
                received
                    .recv_timeout(std::time::Duration::from_secs(1))
                    .expect("timer job completes")
            };
            let value = if flavor == 0 {
                tokio::runtime::Builder::new_current_thread()
                    .build()
                    .expect("current-thread caller runtime")
                    .block_on(async { invoke() })
            } else {
                tokio::runtime::Builder::new_multi_thread()
                    .worker_threads(1)
                    .build()
                    .expect("multi-thread caller runtime")
                    .block_on(async { invoke() })
            };
            assert_eq!(value, 7);
        }
    }

    #[test]
    fn shared_epoch_ticker_interrupts_each_relative_store_deadline() {
        let engine = engine().expect("production engine builds");
        let module = wasmtime::Module::from_binary(
            &engine,
            &wat::parse_str("(module (func (export \"_start\") (loop br 0)))")
                .expect("loop WAT compiles"),
        )
        .expect("loop module compiles");
        let _ticker = EpochTicker::start(engine.clone()).expect("ticker starts");

        for _ in 0..2 {
            let mut store = wasmtime::Store::new(&engine, ());
            store.set_fuel(u64::MAX).expect("loop has fuel until epoch");
            store.set_epoch_deadline(1);
            let instance = wasmtime::Instance::new(&mut store, &module, &[])
                .expect("loop module instantiates");
            let start = instance
                .get_typed_func::<(), ()>(&mut store, "_start")
                .expect("start exists");
            let error = start
                .call(&mut store, ())
                .expect_err("epoch interrupts loop");
            assert_eq!(
                error.downcast_ref::<wasmtime::Trap>(),
                Some(&wasmtime::Trap::Interrupt)
            );
        }
    }

    fn execute_test_wat_execution(
        wat: &str,
        limits: PdfLimits,
        output_cap: usize,
    ) -> ModuleExecution {
        execute_test_wat_execution_with_preopen(wat, limits, output_cap, None)
    }

    fn execute_test_wat_execution_with_preopen(
        wat: &str,
        limits: PdfLimits,
        output_cap: usize,
        preopen: Option<&StagedInput>,
    ) -> ModuleExecution {
        try_execute_test_wat_execution_with_preopen(wat, limits, output_cap, preopen)
            .expect("runtime execution completes")
    }

    fn try_execute_test_wat_execution_with_preopen(
        wat: &str,
        limits: PdfLimits,
        output_cap: usize,
        preopen: Option<&StagedInput>,
    ) -> crate::Result<ModuleExecution> {
        let engine = engine().expect("production engine builds");
        let module = std::sync::Arc::new(
            wasmtime::Module::from_binary(
                &engine,
                &wat::parse_str(wat).expect("malicious unit-test WAT compiles"),
            )
            .expect("malicious unit-test module compiles"),
        );
        let _ticker = EpochTicker::start(engine.clone()).expect("ticker starts");
        tokio::runtime::Builder::new_current_thread()
            .enable_time()
            .build()
            .expect("unit-test runtime builds")
            .block_on(execute_module(
                module,
                preopen,
                Vec::new(),
                Sha256Digest::from_bytes([0x51; 32]),
                validate_limits(PdfLimits {
                    max_protocol_output_bytes: u64::try_from(output_cap)
                        .expect("test output cap fits u64"),
                    ..limits
                })
                .expect("test limits validate"),
                RuntimeDeadlines {
                    wall: std::time::Duration::from_millis(100),
                    epoch_ticks: 10,
                },
            ))
    }

    fn execute_test_wat(wat: &str, limits: PdfLimits, output_cap: usize) -> RuntimeDecision {
        execute_test_wat_execution(wat, limits, output_cap).decision
    }

    #[test]
    fn real_execution_records_fuel_dynamic_memory_and_host_call_limits() {
        assert_eq!(
            execute_test_wat(
                "(module (func (export \"_start\") (loop br 0)))",
                PdfLimits {
                    max_fuel: 1,
                    ..PdfLimits::default()
                },
                1024,
            ),
            RuntimeDecision::Limit(PdfLimitKind::Fuel)
        );
        assert_eq!(
            execute_test_wat(
                "(module (memory 1) (func (export \"_start\") i32.const 1 memory.grow drop))",
                PdfLimits {
                    max_guest_memory_bytes: 65_536,
                    ..PdfLimits::default()
                },
                1024,
            ),
            RuntimeDecision::Limit(PdfLimitKind::GuestMemoryBytes)
        );
        assert_eq!(
            execute_test_wat(
                "(module (table 1 funcref) (func (export \"_start\") ref.null func i32.const 9999 table.grow drop))",
                PdfLimits::default(),
                1024,
            ),
            RuntimeDecision::Proceed
        );
        assert_eq!(
            execute_test_wat(
                "(module (table 1 funcref) (func (export \"_start\") ref.null func i32.const 10000 table.grow drop))",
                PdfLimits::default(),
                1024,
            ),
            RuntimeDecision::Limit(PdfLimitKind::TableElements)
        );

        let calls = "i32.const 0 i32.const 0 call $random_get drop ".repeat(256);
        assert_eq!(
            execute_test_wat(
                &format!(
                    "(module (import \"wasi_snapshot_preview1\" \"random_get\" (func $random_get (param i32 i32) (result i32))) (memory (export \"memory\") 1) (func (export \"_start\") {calls}))"
                ),
                PdfLimits::default(),
                1024,
            ),
            RuntimeDecision::Proceed
        );
        let calls = "i32.const 0 i32.const 0 call $random_get drop ".repeat(257);
        assert_eq!(
            execute_test_wat(
                &format!(
                    "(module (import \"wasi_snapshot_preview1\" \"random_get\" (func $random_get (param i32 i32) (result i32))) (memory (export \"memory\") 1) (func (export \"_start\") {calls}))"
                ),
                PdfLimits::default(),
                1024,
            ),
            RuntimeDecision::Limit(PdfLimitKind::HostCalls)
        );
    }

    #[test]
    fn module_maximum_growth_failures_are_unclassified_sandbox_traps() {
        assert_eq!(
            execute_test_wat(
                "(module (memory 1 1) (func (export \"_start\") i32.const 1 memory.grow drop))",
                PdfLimits::default(),
                1024,
            ),
            RuntimeDecision::SandboxTrap
        );
        assert_eq!(
            execute_test_wat(
                "(module (table 1 1 funcref) (func (export \"_start\") ref.null func i32.const 1 table.grow drop))",
                PdfLimits::default(),
                1024,
            ),
            RuntimeDecision::SandboxTrap
        );
    }

    #[test]
    fn instantiation_resource_failure_reaches_unclassified_sandbox_arbitration() {
        let signals = RuntimeSignals::default();
        signals.record_resource_failure();
        admit_instantiation_failure(&signals)
            .expect("callback-recorded instantiation allocation failure is input-dependent");
        assert_eq!(
            arbitrate_runtime(&signals, CompletionEvidence::default()),
            RuntimeDecision::SandboxTrap
        );

        assert!(matches!(
            admit_instantiation_failure(&RuntimeSignals::default()),
            Err(crate::HeleosError::Integrity)
        ));
    }

    #[test]
    fn real_execution_types_timeout_exit_and_oversized_random_denial() {
        assert_eq!(
            execute_test_wat(
                "(module (func (export \"_start\") (loop br 0)))",
                PdfLimits::default(),
                1024,
            ),
            RuntimeDecision::Limit(PdfLimitKind::Timeout)
        );
        assert_eq!(
            execute_test_wat(
                "(module (import \"wasi_snapshot_preview1\" \"proc_exit\" (func $exit (param i32))) (func (export \"_start\") i32.const 7 call $exit))",
                PdfLimits::default(),
                1024,
            ),
            RuntimeDecision::SandboxTrap
        );
        assert_eq!(
            execute_test_wat(
                "(module (import \"wasi_snapshot_preview1\" \"random_get\" (func $random (param i32 i32) (result i32))) (import \"wasi_snapshot_preview1\" \"proc_exit\" (func $exit (param i32))) (memory (export \"memory\") 1) (func (export \"_start\") i32.const 0 i32.const 33 call $random if i32.const 7 call $exit end))",
                PdfLimits::default(),
                1024,
            ),
            RuntimeDecision::SandboxTrap
        );
    }

    #[test]
    fn real_execution_records_stdout_overflow_and_handled_stderr() {
        let writer = |fd: u32, data: &str| {
            format!(
                "(module (import \"wasi_snapshot_preview1\" \"fd_write\" (func $write (param i32 i32 i32 i32) (result i32))) (memory (export \"memory\") 1) (data (i32.const 8) \"{data}\") (func (export \"_start\") i32.const 0 i32.const 8 i32.store i32.const 4 i32.const {} i32.store i32.const {fd} i32.const 0 i32.const 1 i32.const 12 call $write drop))",
                data.len()
            )
        };
        assert_eq!(
            execute_test_wat(&writer(1, "abcd"), PdfLimits::default(), 3),
            RuntimeDecision::Limit(PdfLimitKind::ProtocolOutputBytes)
        );
        assert_eq!(
            execute_test_wat(&writer(2, "x"), PdfLimits::default(), 1024),
            RuntimeDecision::SandboxTrap
        );
    }

    #[test]
    fn real_preopen_denies_parent_escape_and_input_write_rights() {
        use std::io::Cursor;

        let parent = tempfile::tempdir().expect("private staging parent");
        crate::apply_private_permissions(parent.path()).expect("harden staging parent");
        let secret = parent.path().join("secret");
        std::fs::write(&secret, b"must remain outside the guest capability")
            .expect("write outside secret");
        crate::apply_private_permissions(&secret).expect("harden outside secret");

        let bytes = b"%PDF-1.4\nsynthetic\n".to_vec();
        let digest = Sha256Digest::hash_reader(Cursor::new(&bytes)).expect("fixture hashes");
        let mut source = Cursor::new(bytes.clone());
        let staged = StagedInput::create(
            parent.path(),
            &mut source,
            u64::try_from(bytes.len()).expect("fixture length fits"),
            digest,
        )
        .expect("input stages");

        for (path, requested_rights) in [("../secret", 2_u64), ("input.pdf", 64_u64)] {
            let wat = format!(
                r#"(module
                    (import "wasi_snapshot_preview1" "path_open"
                        (func $path_open (param i32 i32 i32 i32 i32 i64 i64 i32 i32) (result i32)))
                    (import "wasi_snapshot_preview1" "proc_exit" (func $exit (param i32)))
                    (memory (export "memory") 1)
                    (data (i32.const 0) "{path}")
                    (func (export "_start")
                        i32.const 3
                        i32.const 0
                        i32.const 0
                        i32.const {path_len}
                        i32.const 0
                        i64.const {requested_rights}
                        i64.const 0
                        i32.const 0
                        i32.const 64
                        call $path_open
                        i32.eqz
                        if
                            i32.const 7
                            call $exit
                        end))"#,
                path_len = path.len(),
            );
            let execution = execute_test_wat_execution_with_preopen(
                &wat,
                PdfLimits::default(),
                1024,
                Some(&staged),
            );
            assert_eq!(
                execution.decision,
                RuntimeDecision::Proceed,
                "the read-only preopen unexpectedly granted {path:?} with rights {requested_rights}"
            );
        }

        staged.close().expect("explicit staging cleanup");
        assert_eq!(
            std::fs::read(&secret).expect("outside secret remains readable to the host"),
            b"must remain outside the guest capability"
        );
    }

    #[test]
    fn explicit_staging_cleanup_error_overrides_every_candidate() {
        let result = finish_staged_candidate(
            Ok(7_u8),
            Err(crate::HeleosError::PolicyDenied),
            Err(crate::HeleosError::ResourceLimit),
        );
        assert!(matches!(result, Err(crate::HeleosError::ResourceLimit)));

        let result =
            finish_staged_candidate(Ok(7_u8), Err(crate::HeleosError::PolicyDenied), Ok(()));
        assert!(matches!(result, Err(crate::HeleosError::PolicyDenied)));
        assert_eq!(
            finish_staged_candidate(Ok(7_u8), Ok(()), Ok(())).expect("clean staging wins"),
            7
        );
    }

    #[test]
    fn dispatch_failure_reports_explicit_staging_cleanup() {
        use std::io::Cursor;

        let parent = tempfile::tempdir().expect("private staging parent");
        crate::apply_private_permissions(parent.path()).expect("harden staging parent");
        let bytes = b"%PDF-1.4\nclosed-dispatch\n".to_vec();
        let digest = Sha256Digest::hash_reader(Cursor::new(&bytes)).expect("fixture hashes");
        let mut source = Cursor::new(bytes.clone());
        let staged = StagedInput::create(
            parent.path(),
            &mut source,
            u64::try_from(bytes.len()).expect("fixture length fits"),
            digest,
        )
        .expect("input stages");
        let root = staged.root_path().to_owned();
        let owner = RuntimeOwner {
            sender: None,
            join: None,
        };
        let (cleanup_sender, cleanup_receiver) = std::sync::mpsc::sync_channel(1);
        let staged = StagedCleanupObserver::new(staged, cleanup_sender);

        assert!(
            owner
                .dispatch(Box::pin(async move {
                    staged.close();
                }))
                .is_err(),
            "closed dispatch must fail"
        );
        let cleanup = cleanup_receiver
            .recv_timeout(std::time::Duration::from_secs(1))
            .expect("dispatch failure reports explicit cleanup");
        cleanup.expect("dispatch-failure cleanup succeeds");
        assert!(!root.exists());
        assert_eq!(
            std::fs::read_dir(parent.path())
                .expect("read staging parent")
                .count(),
            0
        );
    }

    #[test]
    fn runtime_job_panic_reports_explicit_staging_cleanup() {
        use std::io::Cursor;

        let parent = tempfile::tempdir().expect("private staging parent");
        crate::apply_private_permissions(parent.path()).expect("harden staging parent");
        let bytes = b"%PDF-1.4\npanicking-job\n".to_vec();
        let digest = Sha256Digest::hash_reader(Cursor::new(&bytes)).expect("fixture hashes");
        let mut source = Cursor::new(bytes.clone());
        let staged = StagedInput::create(
            parent.path(),
            &mut source,
            u64::try_from(bytes.len()).expect("fixture length fits"),
            digest,
        )
        .expect("input stages");
        let root = staged.root_path().to_owned();
        let owner = RuntimeOwner::start().expect("runtime owner starts");
        let (cleanup_sender, cleanup_receiver) = std::sync::mpsc::sync_channel(1);
        let staged = StagedCleanupObserver::new(staged, cleanup_sender);

        owner
            .dispatch(Box::pin(async move {
                let _staged = staged;
                panic!("injected runtime job panic");
            }))
            .expect("panicking job dispatches");
        let cleanup = cleanup_receiver
            .recv_timeout(std::time::Duration::from_secs(1))
            .expect("panicking job reports explicit cleanup");
        cleanup.expect("panic-path cleanup succeeds");
        drop(owner);
        assert!(!root.exists());
        assert_eq!(
            std::fs::read_dir(parent.path())
                .expect("read staging parent")
                .count(),
            0
        );
    }

    #[test]
    fn private_staging_rechecks_identity_permissions_contents_and_cleans_explicitly() {
        use std::io::Cursor;

        let parent = tempfile::tempdir().expect("private staging parent");
        crate::apply_private_permissions(parent.path()).expect("harden staging parent");
        let bytes = b"%PDF-1.4\nsynthetic\n".to_vec();
        let digest = Sha256Digest::hash_reader(Cursor::new(&bytes)).expect("fixture hashes");
        let mut source = Cursor::new(bytes.clone());
        let staged = StagedInput::create(
            parent.path(),
            &mut source,
            u64::try_from(bytes.len()).expect("fixture length fits"),
            digest,
        )
        .expect("input stages");
        let root = staged.root_path().to_owned();
        assert_eq!(
            std::fs::read_dir(&root).expect("read staging root").count(),
            1
        );
        staged.recheck().expect("staging identity rechecks");
        staged.close().expect("explicit staging cleanup");
        assert!(!root.exists());
        assert_eq!(
            std::fs::read_dir(parent.path())
                .expect("read staging parent")
                .count(),
            0
        );
    }

    #[cfg(windows)]
    fn windows_staged_input(label: &str) -> (tempfile::TempDir, StagedInput) {
        use std::io::Cursor;

        let parent = tempfile::Builder::new()
            .prefix(label)
            .tempdir()
            .expect("private Windows staging parent");
        crate::apply_private_permissions(parent.path()).expect("harden Windows staging parent");
        let bytes = b"%PDF-1.4\nwindows-staging\n".to_vec();
        let digest = Sha256Digest::hash_reader(Cursor::new(&bytes)).expect("fixture hashes");
        let mut source = Cursor::new(bytes.clone());
        let staged = StagedInput::create(
            parent.path(),
            &mut source,
            u64::try_from(bytes.len()).expect("fixture length fits"),
            digest,
        )
        .expect("input stages");
        (parent, staged)
    }

    #[cfg(windows)]
    #[test]
    fn windows_staging_input_handles_request_exact_data_and_dacl_access() {
        assert_eq!(super::windows_staging_input_access_mask(false), 0x8002_0000);
        assert_eq!(super::windows_staging_input_access_mask(true), 0xc006_0000);
    }

    #[cfg(windows)]
    #[test]
    fn windows_staging_handles_enforce_private_readonly_no_delete_and_cleanup() {
        use std::os::windows::fs::MetadataExt;

        const FILE_ATTRIBUTE_READONLY: u32 = 0x0000_0001;
        const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x0000_0400;

        let (parent, staged) = windows_staged_input("heleos-pdf-windows-no-delete-");
        let parent_path = parent.path().to_owned();
        let root_path = staged.root_path().to_owned();
        let input_path = root_path.join("input.pdf");
        crate::store::verify_private_permissions_on_handle(
            &staged.parent().expect("retained parent").retained,
        )
        .expect("parent has the exact protected private DACL");
        crate::store::verify_private_permissions_on_handle(
            staged.root.as_ref().expect("retained staging root"),
        )
        .expect("root has the exact protected private DACL");
        let retained_input = staged.input.as_ref().expect("retained staged input");
        crate::store::verify_private_permissions_on_handle(retained_input)
            .expect("input has the exact protected private DACL");
        super::verify_read_only_input(retained_input)
            .expect("input has exact DACL, READONLY, regular, non-reparse, single-link policy");
        let input_metadata = retained_input.metadata().expect("read input metadata");
        assert_ne!(
            input_metadata.file_attributes() & FILE_ATTRIBUTE_READONLY,
            0
        );
        assert_eq!(
            input_metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT,
            0
        );

        let parent_moved = parent_path.with_extension("replacement-attempt");
        let root_moved = parent_path.join("root-replacement-attempt");
        let input_moved = root_path.join("input-replacement-attempt.pdf");
        assert!(std::fs::rename(&parent_path, &parent_moved).is_err());
        assert!(std::fs::remove_dir_all(&parent_path).is_err());
        assert!(std::fs::rename(&root_path, &root_moved).is_err());
        assert!(std::fs::remove_dir_all(&root_path).is_err());
        assert!(std::fs::rename(&input_path, &input_moved).is_err());
        assert!(std::fs::remove_file(&input_path).is_err());
        staged
            .recheck()
            .expect("failed replacement attempts leave every retained object intact");

        staged.close().expect("explicit Windows staging cleanup");
        assert!(!root_path.exists());
        assert_eq!(
            std::fs::read_dir(&parent_path)
                .expect("read Windows staging parent")
                .count(),
            0
        );
    }

    #[cfg(windows)]
    #[test]
    fn windows_staging_rejects_directory_symlink_junction_and_file_reparse_points() {
        use std::os::windows::fs::{MetadataExt, symlink_dir, symlink_file};

        let outer = tempfile::Builder::new()
            .prefix("heleos-pdf-windows-reparse-")
            .tempdir()
            .expect("Windows reparse fixture root");
        crate::apply_private_permissions(outer.path()).expect("harden reparse fixture root");

        let directory_target = outer.path().join("directory-target");
        std::fs::create_dir(&directory_target).expect("create directory target");
        crate::apply_private_permissions(&directory_target).expect("harden directory target");
        let directory_symlink = outer.path().join("directory-symlink");
        symlink_dir(&directory_target, &directory_symlink)
            .expect("create directory symlink reparse point");
        assert!(super::open_staging_directory(&directory_symlink, false).is_err());

        let directory_junction = outer.path().join("directory-junction");
        const JUNCTION_SCRIPT: &[u8] = b"@mklink /J directory-junction directory-target\r\n";
        let script_path = outer.path().join("junction-fixture.cmd");
        let mut script = super::create_staging_input(&script_path)
            .expect("create the private fixed junction fixture script");
        std::io::Write::write_all(&mut script, JUNCTION_SCRIPT)
            .expect("write the fixed junction fixture script");
        script.sync_all().expect("sync the junction fixture script");
        crate::store::verify_private_permissions_on_handle(&script)
            .expect("junction fixture script has the exact protected private DACL");
        let script_metadata = script.metadata().expect("inspect retained fixture script");
        assert!(
            script_metadata.is_file()
                && !script_metadata.file_type().is_symlink()
                && script_metadata.file_attributes() & 0x0000_0400 == 0,
            "fixture script must be a regular non-reparse file"
        );
        assert_eq!(
            std::fs::symlink_metadata(&script_path)
                .expect("inspect fixture script path")
                .file_attributes()
                & 0x0000_0400,
            0,
            "fixture script path must not be a reparse point"
        );

        let mut shadow_name = script_path.as_os_str().to_os_string();
        shadow_name.push(".exe");
        let shadow_path = std::path::PathBuf::from(shadow_name);
        assert!(
            !shadow_path.exists(),
            "the Windows command resolver must not find an adjacent .cmd.exe shadow"
        );
        let mut inventory = std::fs::read_dir(outer.path())
            .expect("enumerate the exact junction fixture directory")
            .map(|entry| entry.expect("read junction fixture entry").file_name())
            .collect::<Vec<_>>();
        inventory.sort();
        assert_eq!(
            inventory,
            [
                std::ffi::OsString::from("directory-symlink"),
                std::ffi::OsString::from("directory-target"),
                std::ffi::OsString::from("junction-fixture.cmd"),
            ]
        );

        let output = std::process::Command::new(&script_path)
            .current_dir(outer.path())
            .env_clear()
            .output()
            .expect("run the absolute fixed junction fixture script");
        assert!(output.status.success(), "junction fixture command failed");
        drop(script);
        std::fs::remove_file(&script_path).expect("remove fixed junction fixture script");
        let junction_metadata = std::fs::symlink_metadata(&directory_junction)
            .expect("inspect created directory junction");
        assert_ne!(
            junction_metadata.file_attributes() & 0x0000_0400,
            0,
            "fixture command must create a directory reparse point"
        );
        assert!(super::open_staging_directory(&directory_junction, false).is_err());

        let input_target = outer.path().join("input-target.pdf");
        let mut input = super::create_staging_input(&input_target)
            .expect("create and harden input reparse target");
        std::io::Write::write_all(&mut input, b"%PDF-1.4\nreparse-target\n")
            .expect("write input reparse target");
        super::set_input_read_only(&input).expect("set input reparse target read-only");
        drop(input);
        let input_symlink = outer.path().join("input-symlink.pdf");
        symlink_file(&input_target, &input_symlink).expect("create file reparse point");
        assert!(super::open_staging_input_read_only(&input_symlink).is_err());

        let mut permissions = std::fs::metadata(&input_target)
            .expect("read target permissions")
            .permissions();
        permissions.set_readonly(false);
        std::fs::set_permissions(&input_target, permissions)
            .expect("restore target cleanup permissions");
    }

    #[cfg(windows)]
    #[test]
    fn windows_staging_detects_path_handle_marker_mismatch_and_cleans_without_leaks() {
        let (parent, mut staged) = windows_staged_input("heleos-pdf-windows-marker-");
        let parent_path = parent.path().to_owned();
        let root_path = staged.root_path().to_owned();

        let alternate = tempfile::Builder::new()
            .prefix("heleos-pdf-windows-marker-alternate-")
            .tempdir()
            .expect("alternate Windows parent path");
        crate::apply_private_permissions(alternate.path()).expect("harden alternate parent");
        let expected_parent_path = staged.parent().expect("retained parent").path.clone();
        staged.parent.as_mut().expect("retained parent").path = alternate.path().to_owned();
        assert!(matches!(
            staged.recheck(),
            Err(crate::HeleosError::PolicyDenied)
        ));
        staged.parent.as_mut().expect("retained parent").path = expected_parent_path;

        let expected_root_marker = staged.root_marker.expect("root marker");
        staged.root_marker = Some(super::StagingMarker {
            first: expected_root_marker.first ^ 1,
            second: expected_root_marker.second,
        });
        assert!(matches!(
            staged.recheck(),
            Err(crate::HeleosError::PolicyDenied)
        ));
        staged.root_marker = Some(expected_root_marker);

        let expected_input_marker = staged.input_marker.expect("input marker");
        staged.input_marker = Some(super::StagingMarker {
            first: expected_input_marker.first,
            second: expected_input_marker.second ^ 1,
        });
        assert!(matches!(
            staged.recheck(),
            Err(crate::HeleosError::PolicyDenied)
        ));
        staged.input_marker = Some(expected_input_marker);
        staged.recheck().expect("restored markers recheck");

        staged.close().expect("explicit marker-negative cleanup");
        assert!(!root_path.exists());
        assert_eq!(
            std::fs::read_dir(&parent_path)
                .expect("read marker-negative staging parent")
                .count(),
            0
        );
    }

    #[cfg(windows)]
    #[test]
    fn windows_preopen_identity_brackets_prevent_every_path_replacement_attempt() {
        let (parent, mut staged) = windows_staged_input("heleos-pdf-windows-brackets-");
        let parent_path = parent.path().to_owned();
        let root_path = staged.root_path().to_owned();
        let input_path = root_path.join("input.pdf");
        let attempts = std::sync::Arc::new(std::sync::Mutex::new(Vec::new()));
        let observed = std::sync::Arc::clone(&attempts);
        let parent_source = parent_path.clone();
        let root_source = root_path.clone();
        let input_source = input_path.clone();
        staged.set_recheck_observer(std::sync::Arc::new(move |point| {
            let parent_blocked =
                std::fs::rename(&parent_source, parent_source.with_extension("moved")).is_err();
            let root_blocked =
                std::fs::rename(&root_source, parent_source.join("root-moved")).is_err();
            let input_blocked =
                std::fs::rename(&input_source, root_source.join("input-moved.pdf")).is_err();
            observed.lock().expect("replacement observer lock").push((
                point,
                parent_blocked,
                root_blocked,
                input_blocked,
            ));
        }));

        let execution = execute_test_wat_execution_with_preopen(
            "(module (func (export \"_start\")))",
            PdfLimits::default(),
            1024,
            Some(&staged),
        );
        assert_eq!(execution.decision, RuntimeDecision::Proceed);
        assert_eq!(
            *attempts.lock().expect("replacement observer lock"),
            [
                (StagingRecheckPoint::BeforePreopen, true, true, true),
                (StagingRecheckPoint::AfterPreopen, true, true, true),
                (
                    StagingRecheckPoint::BeforeOutputAcceptance,
                    true,
                    true,
                    true,
                ),
            ]
        );
        staged.close().expect("explicit bracket-test cleanup");
        assert!(!root_path.exists());
        assert_eq!(
            std::fs::read_dir(&parent_path)
                .expect("read bracket-test staging parent")
                .count(),
            0
        );
    }

    #[cfg(windows)]
    #[test]
    fn windows_staging_cleanup_covers_success_trap_timeout_limit_and_setup_error() {
        let cases = [
            (
                "success",
                "(module (func (export \"_start\")))",
                PdfLimits::default(),
                RuntimeDecision::Proceed,
            ),
            (
                "trap",
                "(module (func (export \"_start\") unreachable))",
                PdfLimits::default(),
                RuntimeDecision::SandboxTrap,
            ),
            (
                "timeout",
                "(module (func (export \"_start\") (loop br 0)))",
                PdfLimits::default(),
                RuntimeDecision::Limit(PdfLimitKind::Timeout),
            ),
            (
                "limit",
                "(module (memory 1) (func (export \"_start\") i32.const 1 memory.grow drop))",
                PdfLimits {
                    max_guest_memory_bytes: 65_536,
                    ..PdfLimits::default()
                },
                RuntimeDecision::Limit(PdfLimitKind::GuestMemoryBytes),
            ),
        ];
        for (label, wat, limits, expected) in cases {
            let (parent, staged) = windows_staged_input("heleos-pdf-windows-outcome-");
            let root_path = staged.root_path().to_owned();
            let execution =
                execute_test_wat_execution_with_preopen(wat, limits, 1024, Some(&staged));
            assert_eq!(execution.decision, expected, "unexpected {label} decision");
            staged.close().expect("explicit outcome cleanup");
            assert!(!root_path.exists());
            assert_eq!(
                std::fs::read_dir(parent.path())
                    .expect("read outcome staging parent")
                    .count(),
                0,
                "{label} leaked staging"
            );
        }

        let (parent, mut staged) = windows_staged_input("heleos-pdf-windows-setup-error-");
        let root_path = staged.root_path().to_owned();
        let expected_marker = staged.input_marker.expect("input marker");
        staged.input_marker = Some(super::StagingMarker {
            first: expected_marker.first ^ 1,
            second: expected_marker.second,
        });
        assert!(matches!(
            try_execute_test_wat_execution_with_preopen(
                "(module (func (export \"_start\")))",
                PdfLimits::default(),
                1024,
                Some(&staged),
            ),
            Err(crate::HeleosError::PolicyDenied)
        ));
        staged.input_marker = Some(expected_marker);
        staged.close().expect("explicit setup-error cleanup");
        assert!(!root_path.exists());
        assert_eq!(
            std::fs::read_dir(parent.path())
                .expect("read setup-error staging parent")
                .count(),
            0
        );
    }

    #[cfg(unix)]
    #[test]
    fn private_staging_fails_closed_after_input_permission_substitution() {
        use std::io::Cursor;
        use std::os::unix::fs::PermissionsExt;

        let parent = tempfile::tempdir().expect("private staging parent");
        crate::apply_private_permissions(parent.path()).expect("harden staging parent");
        let bytes = b"%PDF-1.4\nsynthetic\n".to_vec();
        let digest = Sha256Digest::hash_reader(Cursor::new(&bytes)).expect("fixture hashes");
        let mut source = Cursor::new(bytes.clone());
        let staged = StagedInput::create(
            parent.path(),
            &mut source,
            u64::try_from(bytes.len()).expect("fixture length fits"),
            digest,
        )
        .expect("input stages");
        let input = staged.root_path().join("input.pdf");
        std::fs::set_permissions(&input, std::fs::Permissions::from_mode(0o600))
            .expect("tamper input permissions");
        assert!(staged.recheck().is_err());
        staged.close().expect("cleanup still succeeds");
        assert!(
            std::fs::read_dir(parent.path())
                .expect("read staging parent")
                .next()
                .is_none()
        );
    }

    #[cfg(unix)]
    #[test]
    fn private_staging_rejects_parent_symlink_and_input_symlink_substitution() {
        use std::io::Cursor;

        let outer = tempfile::tempdir().expect("private staging outer directory");
        crate::apply_private_permissions(outer.path()).expect("harden staging outer directory");
        let target_parent = outer.path().join("target-parent");
        std::fs::create_dir(&target_parent).expect("create target staging parent");
        crate::apply_private_permissions(&target_parent).expect("harden target staging parent");
        let linked_parent = outer.path().join("linked-parent");
        std::os::unix::fs::symlink(&target_parent, &linked_parent)
            .expect("create staging-parent symlink");
        assert!(matches!(
            super::ParentAnchor::open(&linked_parent),
            Err(crate::HeleosError::PolicyDenied)
        ));

        let bytes = b"%PDF-1.4\ninput-substitution\n".to_vec();
        let digest = Sha256Digest::hash_reader(Cursor::new(&bytes)).expect("fixture hashes");
        let mut source = Cursor::new(bytes.clone());
        let staged = StagedInput::create(
            &target_parent,
            &mut source,
            u64::try_from(bytes.len()).expect("fixture length fits"),
            digest,
        )
        .expect("input stages");
        let root = staged.root_path().to_owned();
        let input = root.join("input.pdf");
        let displaced = root.join("displaced-input.pdf");
        std::fs::rename(&input, &displaced).expect("displace retained input pathname");
        std::os::unix::fs::symlink(&displaced, &input)
            .expect("substitute input pathname with a symlink");
        assert!(matches!(
            staged.recheck(),
            Err(crate::HeleosError::PolicyDenied)
        ));
        staged
            .close()
            .expect("input-substitution cleanup is explicit");
        assert!(
            std::fs::read_dir(&target_parent)
                .expect("read target staging parent")
                .next()
                .is_none(),
            "input substitution leaked staging"
        );
    }

    #[cfg(unix)]
    #[test]
    fn private_staging_detects_root_substitution_and_removes_the_original_entry() {
        use std::io::Cursor;

        let parent = tempfile::tempdir().expect("private staging parent");
        crate::apply_private_permissions(parent.path()).expect("harden staging parent");
        let bytes = b"%PDF-1.4\nroot-substitution\n".to_vec();
        let digest = Sha256Digest::hash_reader(Cursor::new(&bytes)).expect("fixture hashes");
        let mut source = Cursor::new(bytes.clone());
        let staged = StagedInput::create(
            parent.path(),
            &mut source,
            u64::try_from(bytes.len()).expect("fixture length fits"),
            digest,
        )
        .expect("input stages");
        let expected_root = staged.root_path().to_owned();
        let displaced_root = parent.path().join("displaced-original");
        std::fs::rename(&expected_root, &displaced_root).expect("displace retained root path");
        std::os::unix::fs::symlink(&displaced_root, &expected_root)
            .expect("substitute root pathname with a symlink");

        assert!(matches!(
            staged.recheck(),
            Err(crate::HeleosError::PolicyDenied)
        ));
        staged.close().expect("substitution cleanup is explicit");
        assert!(
            std::fs::read_dir(parent.path())
                .expect("read staging parent")
                .next()
                .is_none(),
            "the displaced retained staging root leaked"
        );
    }

    #[test]
    fn host_revalidates_response_identity_indices_page_ids_and_runtime_priority() {
        let digest = Sha256Digest::from_bytes([0x61; 32]);
        let provenance = PdfProbeProvenance {
            parser_name: "lopdf".to_owned(),
            parser_version: "0.44.0".to_owned(),
            guest_wasm_sha256: Sha256Digest::from_bytes([1; 32]),
            guest_source_tree_sha256: Sha256Digest::from_bytes([2; 32]),
            guest_dependency_graph_sha256: Sha256Digest::from_bytes([3; 32]),
            protocol_version: PROTOCOL_VERSION.to_owned(),
        };
        let response = |index: u32| PdfResponseV1 {
            protocol: PROTOCOL_VERSION.to_owned(),
            input_sha256: digest.to_string(),
            byte_length: 7,
            outcome: PdfGuestOutcomeV1::Accepted {
                pages: vec![PdfPageV1 {
                    index,
                    page_id: crate::page_id(digest, 0).as_digest().to_string(),
                    width_micropoints: 1_000_000,
                    height_micropoints: 2_000_000,
                    unit: PageUnitV1::Point,
                    rotation_degrees: 0,
                    transform: PageTransformV1 {
                        m11: 1,
                        m12: 0,
                        m21: 0,
                        m22: -1,
                        tx_micropoints: 0,
                        ty_micropoints: 2_000_000,
                    },
                }],
            },
        };
        let limits = validate_limits(PdfLimits::default()).expect("limits validate");
        let accepted = translate_module_execution(
            ModuleExecution {
                stdout: encode_response_v1(&response(0), limits.output_cap)
                    .expect("response encodes")
                    .into(),
                decision: RuntimeDecision::Proceed,
            },
            RevisionId::from(digest),
            digest,
            7,
            provenance.clone(),
            limits,
        )
        .expect("response translates");
        assert!(matches!(accepted, PdfProbeOutcome::Accepted(_)));

        let mut invalid_index =
            serde_jcs::to_vec(&response(1)).expect("invalid shape canonicalizes");
        invalid_index.push(b'\n');
        let breached = translate_module_execution(
            ModuleExecution {
                stdout: invalid_index.into(),
                decision: RuntimeDecision::Proceed,
            },
            RevisionId::from(digest),
            digest,
            7,
            provenance.clone(),
            limits,
        )
        .expect("breach quarantines");
        assert!(matches!(
            breached,
            PdfProbeOutcome::Quarantined(ref value)
                if value.reason == PdfQuarantineReason::ProtocolBreach
        ));

        let limited = translate_module_execution(
            ModuleExecution {
                stdout: encode_response_v1(&response(0), limits.output_cap)
                    .expect("response encodes")
                    .into(),
                decision: RuntimeDecision::Limit(PdfLimitKind::Fuel),
            },
            RevisionId::from(digest),
            digest,
            7,
            provenance,
            limits,
        )
        .expect("runtime limit quarantines");
        assert!(matches!(
            limited,
            PdfProbeOutcome::Quarantined(ref value)
                if value.reason == PdfQuarantineReason::LimitExceeded(PdfLimitKind::Fuel)
        ));
    }

    #[test]
    fn public_probe_rewinds_verified_input_runs_off_thread_and_removes_staging() {
        use std::io::{Cursor, Seek, SeekFrom};
        use std::sync::Arc;

        let source_parent = tempfile::tempdir().expect("private vault parent");
        crate::apply_private_permissions(source_parent.path()).expect("harden vault parent");
        let vault = Vault::open(VaultConfig {
            root: source_parent.path().join("vault"),
            open_mode: VaultOpenMode::CreateNew,
        })
        .expect("test vault opens");
        let pdf = b"%PDF-1.4\nsynthetic host boundary\n".to_vec();
        let stored = match vault
            .put_reader(
                Cursor::new(pdf.clone()),
                VaultWriteBudget::new(
                    u64::try_from(pdf.len()).expect("fixture length fits"),
                    u64::try_from(pdf.len()).expect("fixture length fits"),
                )
                .expect("budget validates"),
            )
            .expect("fixture stores")
        {
            PutOutcome::Stored(stored) => stored,
            PutOutcome::QuotaRejected { .. } => panic!("fixture unexpectedly rejected"),
        };

        let staging_parent = source_parent.path().join("sandbox");
        std::fs::create_dir(&staging_parent).expect("create sandbox parent");
        crate::apply_private_permissions(&staging_parent).expect("harden sandbox parent");
        let response = PdfResponseV1 {
            protocol: PROTOCOL_VERSION.to_owned(),
            input_sha256: stored.digest.to_string(),
            byte_length: stored.byte_length,
            outcome: PdfGuestOutcomeV1::Accepted { pages: Vec::new() },
        };
        let response = encode_response_v1(&response, 4096).expect("response encodes");
        let data = response
            .iter()
            .map(|byte| format!("\\{byte:02x}"))
            .collect::<String>();
        let wasm = wat::parse_str(format!(
            "(module (import \"wasi_snapshot_preview1\" \"fd_write\" (func $write (param i32 i32 i32 i32) (result i32))) (memory (export \"memory\") 1) (data (i32.const 32) \"{data}\") (func (export \"_start\") i32.const 0 i32.const 32 i32.store i32.const 4 i32.const {} i32.store i32.const 1 i32.const 0 i32.const 1 i32.const 16 call $write drop))",
            response.len()
        ))
        .expect("test guest WAT compiles");
        let wasm_digest = Sha256Digest::hash_reader(Cursor::new(&wasm)).expect("WASM hashes");
        let manifest = GuestManifestV1 {
            schema: super::MANIFEST_SCHEMA.to_owned(),
            protocol: PROTOCOL_VERSION.to_owned(),
            wasm_sha256: wasm_digest.to_string(),
            wasm_byte_length: u64::try_from(wasm.len()).expect("WASM length fits"),
            source_tree_sha256: "11".repeat(32),
            dependency_graph_sha256: "22".repeat(32),
            guest_resolution_lock_sha256: "55".repeat(32),
            dependencies: Vec::new(),
            target: super::GUEST_TARGET.to_owned(),
            profile: super::GUEST_PROFILE.to_owned(),
            rustc: super::GUEST_RUSTC.to_owned(),
            rust_path_remap: super::GUEST_RUST_PATH_REMAP.to_owned(),
            imports_sha256: "33".repeat(32),
            imports: Vec::new(),
            exports_sha256: "44".repeat(32),
            exports: Vec::new(),
            build_command: super::GUEST_BUILD_COMMAND.to_owned(),
        };
        let guest = ApprovedPdfGuest {
            wasm: Arc::from(wasm),
            manifest: Arc::new(manifest),
        };
        let probe = super::WasiPdfProbe::new(
            guest,
            crate::PdfSandboxConfig {
                staging_parent: staging_parent.clone(),
            },
        )
        .expect("probe constructs");

        let mut input = vault
            .open_verified(stored.digest)
            .expect("verified input opens");
        input.seek(SeekFrom::End(0)).expect("pre-seek input");
        let outcome = tokio::runtime::Builder::new_current_thread()
            .build()
            .expect("unrelated caller runtime")
            .block_on(async {
                probe.probe(input, RevisionId::from(stored.digest), PdfLimits::default())
            })
            .expect("probe succeeds");
        assert!(matches!(outcome, PdfProbeOutcome::Accepted(ref value) if value.pages.is_empty()));
        assert!(
            std::fs::read_dir(&staging_parent)
                .expect("read staging parent")
                .next()
                .is_none()
        );

        assert!(matches!(
            probe.probe(
                vault
                    .open_verified(stored.digest)
                    .expect("verified input reopens"),
                RevisionId::from(Sha256Digest::from_bytes([0x99; 32])),
                PdfLimits::default(),
            ),
            Err(crate::HeleosError::Integrity)
        ));
        let limited = probe
            .probe(
                vault
                    .open_verified(stored.digest)
                    .expect("verified input reopens"),
                RevisionId::from(stored.digest),
                PdfLimits {
                    max_input_bytes: stored.byte_length - 1,
                    ..PdfLimits::default()
                },
            )
            .expect("tight input limit quarantines");
        assert!(matches!(
            limited,
            PdfProbeOutcome::Quarantined(ref value)
                if value.reason
                    == PdfQuarantineReason::LimitExceeded(PdfLimitKind::InputBytes)
        ));
        assert!(
            std::fs::read_dir(&staging_parent)
                .expect("read staging parent")
                .next()
                .is_none(),
            "input-byte quarantine allocated staging"
        );
    }

    #[test]
    fn deterministic_rng_material_has_the_pinned_stride_four_oracle() {
        let input = Sha256Digest::from_bytes([0x42; 32]);
        let material = derive_rng_material(input);
        let expected = [
            material.secure[3],
            material.secure[7],
            material.secure[11],
            material.secure[15],
            material.secure[19],
            material.secure[23],
            material.secure[27],
            material.secure[31],
            material.secure[3],
            material.secure[7],
            material.secure[11],
            material.secure[15],
            material.secure[19],
            material.secure[23],
            material.secure[27],
            material.secure[31],
        ];
        let actual = material
            .secure
            .chunks_exact(4)
            .map(|word| word[3])
            .cycle()
            .take(16)
            .collect::<Vec<_>>();
        assert_eq!(actual, expected);
        assert_ne!(material.secure, material.insecure);
        assert_ne!(material.secure, material.seed);
    }

    #[test]
    fn fresh_stores_receive_the_exact_deterministic_random_get_byte_oracle() {
        let wat = r#"(module
            (import "wasi_snapshot_preview1" "random_get"
                (func $random (param i32 i32) (result i32)))
            (import "wasi_snapshot_preview1" "fd_write"
                (func $write (param i32 i32 i32 i32) (result i32)))
            (memory (export "memory") 1)
            (func (export "_start")
                i32.const 32 i32.const 16 call $random drop
                i32.const 0 i32.const 32 i32.store
                i32.const 4 i32.const 16 i32.store
                i32.const 1 i32.const 0 i32.const 1 i32.const 8 call $write drop))"#;
        let material = derive_rng_material(Sha256Digest::from_bytes([0x51; 32]));
        let expected = [
            material.secure[3],
            material.secure[7],
            material.secure[11],
            material.secure[15],
            material.secure[19],
            material.secure[23],
            material.secure[27],
            material.secure[31],
            material.secure[3],
            material.secure[7],
            material.secure[11],
            material.secure[15],
            material.secure[19],
            material.secure[23],
            material.secure[27],
            material.secure[31],
        ];

        for _ in 0..2 {
            let execution = execute_test_wat_execution(wat, PdfLimits::default(), expected.len());
            assert_eq!(execution.decision, RuntimeDecision::Proceed);
            assert_eq!(execution.stdout.as_ref(), expected);
        }
    }
}
