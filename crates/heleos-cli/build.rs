#![forbid(unsafe_code)]

use std::fs::{self, File, OpenOptions};
use std::io::{Cursor, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, ExitStatus, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, mpsc};
use std::thread;
use std::time::{Duration, Instant};

use heleos_pdf_protocol::{
    ArtifactBuildInputsV1, ArtifactBuildPolicyV1, ArtifactDependencyGraphV1, DependencyRecordV1,
    ExportRecordV1, GuestResolutionCachePlanV1, GuestResolutionWorkspaceSpecV1, ImportRecordV1,
    MAX_GUEST_WASM_BYTES, PROTOCOL_VERSION, SourceFileV1, artifact_build_policy_v1,
    dependency_graph_sha256, exports_sha256, guest_resolution_cache_plan_sha256,
    guest_resolution_cache_plan_v1, guest_resolution_lock_sha256,
    guest_resolution_workspace_spec_v1, imports_sha256, normalize_dependency_graph_v1,
    source_tree_sha256, validate_guest_resolution_lock_projection_v1,
    validate_guest_source_closure_v1, validate_pdf_guest_module_policy_v1,
    verify_no_physical_prefixes_v1,
};
use serde::Deserialize;
use sha2::{Digest, Sha256};

type BuildResult<T> = std::result::Result<T, String>;

const PROCESS_STREAM_CAP: usize = 32 * 1024 * 1024;
const PROCESS_POLL_INTERVAL: Duration = Duration::from_millis(10);
const TOOL_PROBE_TIMEOUT: Duration = Duration::from_secs(30);
const ROOT_MANIFEST_CAP: usize = 1024 * 1024;
const LOCK_CAP: usize = 16 * 1024 * 1024;
const SOURCE_FILE_CAP: usize = 8 * 1024 * 1024;
const MANIFEST_CAP: usize = 1024 * 1024;
const FROZEN_RESOLUTION_ARCHIVE_COUNT: usize = 67;
const FROZEN_ACTIVE_IDENTITY_COUNT: usize = 58;
const FROZEN_ACTIVE_REGISTRY_COUNT: usize = 56;
const FROZEN_RESOLUTION_LOCK_SHA256: &str =
    "355ee328894390cc65ea62ff25020a67cfd614042864f41a0cebe1eeb6f3ff52";
const FROZEN_CACHE_PLAN_SHA256: &str =
    "809419491262af6647fc246b0e9f556ae9deeba8570a80b87cac1029bc52580c";
const TRACKED_DEPENDENCY_GRAPH_SHA256: &str =
    "63c98f658963977879d12eeacbc07299a9a01c1bdae663c7e006a698e0f8cb01";
const TRACKED_WASM_SHA256: &str =
    "c4a39659129a3d7fe97b3cf01753eec85e2474f514d50cb82b379ccf0f0aedaf";
const TRACKED_WASM_BYTE_LENGTH: u64 = 1_018_666;
const TRACKED_BUILD_COMMAND: &str = concat!(
    "cargo +1.96.1 build --locked -p heleos-pdf-guest --target wasm32-wasip1 ",
    "--release --message-format=json-render-diagnostics --quiet"
);

#[derive(Clone, Debug)]
struct ToolchainInputs {
    proxies: ToolProxySet,
    seed_cargo_home: PathBuf,
    rustup_home: PathBuf,
    rustc_sysroot: PathBuf,
    system_root: Option<PathBuf>,
}

#[derive(Clone, Debug)]
struct ToolProxySet {
    cargo_proxy_invocation: PathBuf,
    rustc_proxy_invocation: PathBuf,
    rustup_proxy_invocation: PathBuf,
    cargo_resolved_identity: PathBuf,
    marker: ExecutableIdentityMarker,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct ExecutableIdentityMarker {
    device: u64,
    file_id: u64,
    byte_length: u64,
}

#[derive(Clone, Debug)]
struct ValidatedGuestSource {
    workspace: PathBuf,
    spec: GuestResolutionWorkspaceSpecV1,
    files: Vec<SourceFileV1>,
    production_lock: Vec<u8>,
}

#[derive(Debug)]
struct ResolutionProjection {
    root: PathBuf,
    pruned_lock: Vec<u8>,
}

#[derive(Debug)]
struct ResolutionEvidence {
    pruned_lock: Vec<u8>,
    locked_metadata: Vec<u8>,
}

#[derive(Debug)]
struct IndependentResolution {
    cargo_home: PathBuf,
    resolution_target: PathBuf,
    resolution_temp: PathBuf,
    resolution: ResolutionProjection,
    cache_plan: GuestResolutionCachePlanV1,
    cache_input_snapshot: Vec<SnapshotRecord>,
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
struct SnapshotRecord {
    relative_path: String,
    kind: u8,
    byte_length: u64,
    sha256: String,
    device: u64,
    file_id: u64,
    link_count: u64,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq)]
#[serde(deny_unknown_fields)]
struct TrackedGuestManifestV1 {
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

#[derive(Debug)]
struct ApprovedBuildOutputs {
    wasm: Vec<u8>,
    manifest: Vec<u8>,
}

fn main() {
    emit_rerun_inputs();
    if let Err(error) = run_build_gate() {
        panic!("approved PDF guest build gate failed: {error}");
    }
}

fn emit_rerun_inputs() {
    let manifest_dir = Path::new(env!("CARGO_MANIFEST_DIR"));
    let workspace = manifest_dir.join("../..");
    for path in [
        workspace.join("Cargo.toml"),
        workspace.join("Cargo.lock"),
        workspace.join("artifacts/pdf-guest/manifest.toml"),
        workspace.join("target/wasm32-wasip1/release/heleos_pdf_guest.wasm"),
        workspace.join("crates/heleos-pdf-guest"),
        workspace.join("crates/heleos-pdf-protocol"),
        workspace.join("crates/heleos-test-fixtures"),
    ] {
        println!("cargo:rerun-if-changed={}", path.display());
    }
}

fn run_build_gate() -> BuildResult<()> {
    let out_dir = canonical_directory(
        &PathBuf::from(
            std::env::var_os("OUT_DIR").ok_or_else(|| "OUT_DIR is unavailable".to_owned())?,
        ),
        "CLI build output",
    )?;
    let scratch = tempfile::Builder::new()
        .prefix(".heleos-cli-build-gate-")
        .tempdir_in(&out_dir)
        .map_err(|_| "build-gate scratch creation failed".to_owned())?;
    let approval = canonical_directory(scratch.path(), "build-gate scratch")
        .and_then(|scratch_path| approve_build_outputs(&scratch_path));
    let cleanup = scratch
        .close()
        .map_err(|_| "build-gate scratch cleanup failed".to_owned());
    let approved = match (approval, cleanup) {
        (_, Err(cleanup_error)) => return Err(cleanup_error),
        (Err(validation_error), Ok(())) => return Err(validation_error),
        (Ok(approved), Ok(())) => approved,
    };

    write_output_file(&out_dir, "heleos_pdf_guest.wasm", &approved.wasm)?;
    write_output_file(&out_dir, "pdf_guest_manifest.toml", &approved.manifest)?;
    Ok(())
}

fn approve_build_outputs(scratch_path: &Path) -> BuildResult<ApprovedBuildOutputs> {
    let workspace = source_workspace()?;
    let source = validate_approved_guest_source(workspace.clone())?;
    reject_cargo_configs(&workspace, None)?;

    let toolchain = discover_toolchain(scratch_path)?;
    reject_inherited_build_authority(&toolchain)?;
    reject_cargo_configs(&workspace, Some(&toolchain.seed_cargo_home))?;

    let seed_target = create_canonical_directory(&scratch_path.join("seed-target"), "seed target")?;
    let seed_temp = create_canonical_directory(&seed_target.join("tmp"), "seed temp")?;
    let seed_cache_before = snapshot_seed_cargo_inputs(&toolchain.seed_cargo_home)?;
    let full_metadata = cargo_metadata(
        &toolchain,
        &workspace,
        &toolchain.seed_cargo_home,
        &seed_target,
        &seed_temp,
    )
    .map_err(|error| format!("supplemental full-workspace {error}"))?;
    let seed_root =
        materialize_resolution_workspace(&scratch_path.join("seed-resolution"), &source)?;
    let seed = normalize_resolution_workspace(
        seed_root,
        &source,
        &toolchain,
        &toolchain.seed_cargo_home,
        &seed_target,
        &seed_temp,
    )?;
    let seed_graph = normalize_dependency_graph_v1(
        &seed.locked_metadata,
        &full_metadata,
        &source.production_lock,
        &seed.pruned_lock,
        "heleos-pdf-guest",
    )
    .map_err(|_| "seed dependency graph normalization failed".to_owned())?;
    let seed_cache_after = snapshot_seed_cargo_inputs(&toolchain.seed_cargo_home)?;
    if seed_cache_before != seed_cache_after {
        return Err("seed metadata mutated validated Cargo inputs".to_owned());
    }

    let seed_cache_plan =
        guest_resolution_cache_plan_v1(&source.production_lock, &seed.pruned_lock)
            .map_err(|_| "seed cache-plan derivation failed".to_owned())?;
    let resolution_lock_sha256 = guest_resolution_lock_sha256(&seed.pruned_lock)
        .map_err(|_| "seed resolution-lock hashing failed".to_owned())?;
    let cache_plan_sha256 = guest_resolution_cache_plan_sha256(&seed_cache_plan)
        .map_err(|_| "seed cache-plan hashing failed".to_owned())?;
    let seed_graph_sha256 = dependency_graph_sha256(&seed_graph.records)
        .map_err(|_| "seed dependency-graph hashing failed".to_owned())?;
    if seed_cache_plan.resolution_archives.len() != FROZEN_RESOLUTION_ARCHIVE_COUNT
        || seed_graph.records.len() != FROZEN_ACTIVE_IDENTITY_COUNT
        || seed_graph.registry_roots.len() != FROZEN_ACTIVE_REGISTRY_COUNT
        || resolution_lock_sha256 != FROZEN_RESOLUTION_LOCK_SHA256
        || cache_plan_sha256 != FROZEN_CACHE_PLAN_SHA256
        || seed_graph_sha256 != TRACKED_DEPENDENCY_GRAPH_SHA256
    {
        return Err("seed resolution evidence differs from the frozen authority".to_owned());
    }

    let independent = prepare_independent_resolution(
        &scratch_path.join("independent"),
        &source,
        &toolchain,
        &seed_cache_plan,
        &seed.pruned_lock,
    )?;
    validate_pruned_cache(
        &independent.cargo_home,
        &independent.cache_plan,
        &independent.cache_input_snapshot,
        true,
    )?;
    let guest_metadata = lock_resolution_workspace(
        &independent.resolution,
        &source,
        &toolchain,
        &independent.cargo_home,
        &independent.resolution_target,
        &independent.resolution_temp,
    )?;
    validate_pruned_cache(
        &independent.cargo_home,
        &independent.cache_plan,
        &independent.cache_input_snapshot,
        true,
    )?;
    let graph = normalize_dependency_graph_v1(
        &guest_metadata,
        &full_metadata,
        &source.production_lock,
        &independent.resolution.pruned_lock,
        "heleos-pdf-guest",
    )
    .map_err(|_| "independent dependency graph normalization failed".to_owned())?;
    if graph.records != seed_graph.records
        || independent.resolution.pruned_lock != seed.pruned_lock
        || independent.cache_plan != seed_cache_plan
    {
        return Err("independent resolution differs from seed evidence".to_owned());
    }
    validate_active_registry_roots_against_plan(&graph, &independent)?;

    let tracked_manifest_path = workspace.join("artifacts/pdf-guest/manifest.toml");
    let tracked_manifest_bytes =
        read_bounded_regular_nofollow(&tracked_manifest_path, MANIFEST_CAP)?;
    if tracked_manifest_bytes.contains(&b'\r') {
        return Err("tracked guest manifest contains a carriage return".to_owned());
    }
    let tracked_manifest_text = std::str::from_utf8(&tracked_manifest_bytes)
        .map_err(|_| "tracked guest manifest is not UTF-8".to_owned())?;
    let tracked: TrackedGuestManifestV1 = toml::from_str(tracked_manifest_text)
        .map_err(|_| "tracked guest manifest parsing failed".to_owned())?;
    let target_root = canonical_directory(&workspace.join("target"), "workspace target")?;
    let wasm_path = target_root.join("wasm32-wasip1/release/heleos_pdf_guest.wasm");
    let wasm = read_bounded_regular_nofollow(&wasm_path, MAX_GUEST_WASM_BYTES)?;
    let policy = artifact_build_policy_v1(&ArtifactBuildInputsV1 {
        cargo_proxy_invocation: toolchain.proxies.cargo_proxy_invocation.clone(),
        cargo_resolved_identity: toolchain.proxies.cargo_resolved_identity.clone(),
        workspace_root: workspace.clone(),
        cargo_home: independent.cargo_home.clone(),
        target_root,
        rustc_sysroot: toolchain.rustc_sysroot.clone(),
        rustup_home: toolchain.rustup_home.clone(),
        registry_roots: graph.registry_roots.clone(),
        system_root: toolchain.system_root.clone(),
    })
    .map_err(|_| "artifact scanner policy rejected validated roots".to_owned())?;
    let build_command = tracked_build_command_from_policy(&policy)?;
    let candidate = candidate_manifest(
        &source,
        &graph,
        &independent.resolution.pruned_lock,
        &wasm,
        &build_command,
    )?;
    verify_no_physical_prefixes_v1(&wasm, &policy)
        .map_err(|_| "approved guest contains a physical build prefix".to_owned())?;
    if tracked != candidate {
        return Err("tracked guest manifest differs from approved bytes and records".to_owned());
    }

    Ok(ApprovedBuildOutputs {
        wasm,
        manifest: tracked_manifest_bytes,
    })
}

fn source_workspace() -> BuildResult<PathBuf> {
    let manifest_dir = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .canonicalize()
        .map_err(|_| "CLI manifest directory identity failed".to_owned())?;
    if manifest_dir.file_name().and_then(|name| name.to_str()) != Some("heleos-cli")
        || manifest_dir
            .parent()
            .and_then(Path::file_name)
            .and_then(|name| name.to_str())
            != Some("crates")
    {
        return Err("CLI manifest directory is outside the governed layout".to_owned());
    }
    canonical_directory(
        manifest_dir
            .parent()
            .and_then(Path::parent)
            .ok_or_else(|| "workspace root derivation failed".to_owned())?,
        "source workspace",
    )
}

fn candidate_manifest(
    source: &ValidatedGuestSource,
    graph: &ArtifactDependencyGraphV1,
    pruned_lock: &[u8],
    wasm: &[u8],
    build_command: &str,
) -> BuildResult<TrackedGuestManifestV1> {
    let source_members = source
        .spec
        .files
        .iter()
        .zip(&source.files)
        .filter(|(expected, actual)| {
            expected.source_tree_member && expected.relative_path == actual.path
        })
        .map(|(_, actual)| actual.clone())
        .collect::<Vec<_>>();
    if source_members.len() != 6 {
        return Err("source-tree allowlist does not contain exactly six files".to_owned());
    }
    let source_tree_sha256 =
        source_tree_sha256(&source_members).map_err(|_| "source-tree hashing failed".to_owned())?;
    let dependency_graph_sha256 = dependency_graph_sha256(&graph.records)
        .map_err(|_| "dependency-graph hashing failed".to_owned())?;
    let guest_resolution_lock_sha256 = guest_resolution_lock_sha256(pruned_lock)
        .map_err(|_| "resolution-lock hashing failed".to_owned())?;
    let (imports, exports) = validate_pdf_guest_module_policy_v1(wasm)
        .map_err(|_| "approved guest module policy failed".to_owned())?;
    let wasm_byte_length = u64::try_from(wasm.len())
        .map_err(|_| "approved guest length does not fit u64".to_owned())?;
    let wasm_sha256 = hash_bytes(wasm);
    if wasm_sha256 != TRACKED_WASM_SHA256 || wasm_byte_length != TRACKED_WASM_BYTE_LENGTH {
        return Err("approved guest identity differs from the frozen artifact".to_owned());
    }
    Ok(TrackedGuestManifestV1 {
        schema: "heleos.pdf-guest-manifest/v1".to_owned(),
        protocol: PROTOCOL_VERSION.to_owned(),
        wasm_sha256,
        wasm_byte_length,
        source_tree_sha256,
        dependency_graph_sha256,
        guest_resolution_lock_sha256,
        dependencies: graph.records.clone(),
        target: "wasm32-wasip1".to_owned(),
        profile: "release".to_owned(),
        rustc: "1.96.1".to_owned(),
        rust_path_remap: "heleos-rust-path-remap/v1".to_owned(),
        imports_sha256: imports_sha256(&imports)
            .map_err(|_| "import-record hashing failed".to_owned())?,
        imports,
        exports_sha256: exports_sha256(&exports)
            .map_err(|_| "export-record hashing failed".to_owned())?,
        exports,
        build_command: build_command.to_owned(),
    })
}

fn tracked_build_command_from_policy(policy: &ArtifactBuildPolicyV1) -> BuildResult<String> {
    let mut words = Vec::with_capacity(
        policy
            .argv()
            .len()
            .checked_add(1)
            .ok_or_else(|| "artifact policy argv length overflow".to_owned())?,
    );
    words.push("cargo");
    words.extend(policy.argv().iter().map(String::as_str));
    let command = words.join(" ");
    if command != TRACKED_BUILD_COMMAND {
        return Err("artifact policy argv differs from the tracked build command".to_owned());
    }
    Ok(command)
}

fn validate_approved_guest_source(workspace: PathBuf) -> BuildResult<ValidatedGuestSource> {
    let production_manifest =
        read_bounded_regular_nofollow(&workspace.join("Cargo.toml"), ROOT_MANIFEST_CAP)?;
    let guest_manifest = read_bounded_regular_nofollow(
        &workspace.join("crates/heleos-pdf-guest/Cargo.toml"),
        ROOT_MANIFEST_CAP,
    )?;
    let protocol_manifest = read_bounded_regular_nofollow(
        &workspace.join("crates/heleos-pdf-protocol/Cargo.toml"),
        ROOT_MANIFEST_CAP,
    )?;
    let fixtures_manifest = read_bounded_regular_nofollow(
        &workspace.join("crates/heleos-test-fixtures/Cargo.toml"),
        ROOT_MANIFEST_CAP,
    )?;
    let spec = guest_resolution_workspace_spec_v1(
        &production_manifest,
        &guest_manifest,
        &protocol_manifest,
        &fixtures_manifest,
    )
    .map_err(|_| "workspace manifests violate the guest-resolution spec".to_owned())?;
    let files = read_and_validate_guest_inventory(&workspace, &spec, false)?;
    validate_guest_source_closure_v1(&files)
        .map_err(|_| "approved guest source closure was rejected".to_owned())?;
    let production_lock = read_bounded_regular_nofollow(&workspace.join("Cargo.lock"), LOCK_CAP)?;
    Ok(ValidatedGuestSource {
        workspace,
        spec,
        files,
        production_lock,
    })
}

fn read_and_validate_guest_inventory(
    workspace: &Path,
    spec: &GuestResolutionWorkspaceSpecV1,
    exclusive_root: bool,
) -> BuildResult<Vec<SourceFileV1>> {
    use std::collections::{BTreeMap, BTreeSet};

    use cap_fs_ext::DirExt;

    let root = cap_std::fs::Dir::open_ambient_dir(workspace, cap_std::ambient_authority())
        .map_err(|_| "guest inventory root open failed".to_owned())?;
    if exclusive_root {
        require_exact_cap_entries(&root, &["Cargo.lock", "Cargo.toml", "crates"])?;
    }
    let crates = root
        .open_dir_nofollow("crates")
        .map_err(|_| "guest inventory crates root is missing or indirect".to_owned())?;
    let mut directory_entries = BTreeMap::<String, BTreeSet<String>>::new();
    let mut crate_names = BTreeSet::new();
    for file in spec.files {
        let components = Path::new(file.relative_path)
            .components()
            .map(|component| component.as_os_str().to_str())
            .collect::<Option<Vec<_>>>()
            .ok_or_else(|| "guest inventory path is not UTF-8".to_owned())?;
        if components.len() != 3 && components.len() != 4 {
            return Err("guest inventory path has unexpected depth".to_owned());
        }
        if components[0] != "crates" || components.len() == 4 && components[2] != "src" {
            return Err("guest inventory path has unexpected shape".to_owned());
        }
        let crate_name = components[1].to_owned();
        crate_names.insert(crate_name.clone());
        let root_entries = directory_entries.entry(crate_name.clone()).or_default();
        if components.len() == 3 {
            root_entries.insert(components[2].to_owned());
        } else {
            root_entries.insert("src".to_owned());
            directory_entries
                .entry(format!("{crate_name}/src"))
                .or_default()
                .insert(components[3].to_owned());
        }
    }
    if exclusive_root {
        let expected = crate_names.iter().map(String::as_str).collect::<Vec<_>>();
        require_exact_cap_entries(&crates, &expected)?;
    }
    for crate_name in &crate_names {
        let crate_dir = crates
            .open_dir_nofollow(crate_name)
            .map_err(|_| "governed crate root is missing or indirect".to_owned())?;
        let expected = directory_entries
            .get(crate_name)
            .ok_or_else(|| "governed crate inventory is absent".to_owned())?
            .iter()
            .map(String::as_str)
            .collect::<Vec<_>>();
        require_exact_cap_entries(&crate_dir, &expected)?;
        if let Some(expected_src) = directory_entries.get(&format!("{crate_name}/src")) {
            let source_dir = crate_dir
                .open_dir_nofollow("src")
                .map_err(|_| "governed source directory is missing or indirect".to_owned())?;
            let expected = expected_src.iter().map(String::as_str).collect::<Vec<_>>();
            require_exact_cap_entries(&source_dir, &expected)?;
        }
    }

    let mut files = Vec::with_capacity(spec.files.len());
    for expected in spec.files {
        files.push(SourceFileV1 {
            path: expected.relative_path.to_owned(),
            content: read_cap_relative_file(&root, expected.relative_path, SOURCE_FILE_CAP)?,
        });
    }
    Ok(files)
}

fn require_exact_cap_entries(directory: &cap_std::fs::Dir, expected: &[&str]) -> BuildResult<()> {
    let mut actual = directory
        .entries()
        .map_err(|_| "capability directory enumeration failed".to_owned())?
        .map(|entry| {
            entry
                .map_err(|_| "capability directory entry failed".to_owned())?
                .file_name()
                .into_string()
                .map_err(|_| "capability directory entry is not UTF-8".to_owned())
        })
        .collect::<BuildResult<Vec<_>>>()?;
    actual.sort();
    let mut expected = expected
        .iter()
        .map(|value| (*value).to_owned())
        .collect::<Vec<_>>();
    expected.sort();
    if actual != expected {
        return Err("capability directory inventory differs from the frozen spec".to_owned());
    }
    Ok(())
}

fn read_cap_relative_file(
    root: &cap_std::fs::Dir,
    relative: &str,
    cap: usize,
) -> BuildResult<Vec<u8>> {
    use cap_fs_ext::{DirExt, FollowSymlinks, MetadataExt, OpenOptionsFollowExt};

    let path = Path::new(relative);
    if path.is_absolute()
        || path
            .components()
            .any(|component| !matches!(component, std::path::Component::Normal(_)))
    {
        return Err("capability file path is not canonical relative syntax".to_owned());
    }
    let parent = path
        .parent()
        .ok_or_else(|| "capability file has no parent".to_owned())?;
    let name = path
        .file_name()
        .ok_or_else(|| "capability file has no name".to_owned())?;
    let mut directory = root
        .try_clone()
        .map_err(|_| "capability root clone failed".to_owned())?;
    for component in parent.components() {
        directory = directory
            .open_dir_nofollow(component.as_os_str())
            .map_err(|_| "capability file parent is missing or indirect".to_owned())?;
    }
    let mut options = cap_std::fs::OpenOptions::new();
    options.read(true).follow(FollowSymlinks::No);
    let mut file = directory
        .open_with(name, &options)
        .map_err(|_| "capability file open failed".to_owned())?;
    let before = file
        .metadata()
        .map_err(|_| "capability file metadata failed".to_owned())?;
    let declared = usize::try_from(before.len())
        .map_err(|_| "capability file length does not fit memory".to_owned())?;
    if !before.is_file() || declared == 0 || declared > cap {
        return Err("capability file violates its regular-file bound".to_owned());
    }
    let mut content = Vec::with_capacity(declared);
    (&mut file)
        .take(
            u64::try_from(cap)
                .ok()
                .and_then(|value| value.checked_add(1))
                .ok_or_else(|| "capability file cap overflow".to_owned())?,
        )
        .read_to_end(&mut content)
        .map_err(|_| "capability file read failed".to_owned())?;
    let after = file
        .metadata()
        .map_err(|_| "capability file metadata recheck failed".to_owned())?;
    if content.len() != declared
        || after.len() != before.len()
        || MetadataExt::dev(&after) != MetadataExt::dev(&before)
        || MetadataExt::ino(&after) != MetadataExt::ino(&before)
        || MetadataExt::nlink(&after) != MetadataExt::nlink(&before)
    {
        return Err("capability file changed while reading".to_owned());
    }
    Ok(content)
}

fn materialize_resolution_workspace(
    destination: &Path,
    source: &ValidatedGuestSource,
) -> BuildResult<PathBuf> {
    match fs::symlink_metadata(destination) {
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Ok(_) => return Err("guest-resolution destination already exists".to_owned()),
        Err(_) => return Err("guest-resolution destination inspection failed".to_owned()),
    }
    fs::create_dir_all(destination.join("crates"))
        .map_err(|_| "guest-resolution root creation failed".to_owned())?;
    let destination = canonical_directory(destination, "guest-resolution workspace")?;
    write_new_synced(
        &destination.join("Cargo.toml"),
        source.spec.root_manifest_utf8_lf.as_bytes(),
    )?;
    write_new_synced(&destination.join("Cargo.lock"), &source.production_lock)?;
    for file in &source.files {
        write_new_synced(&destination.join(&file.path), &file.content)?;
    }
    validate_materialized_resolution(&destination, source, &source.production_lock)?;
    sync_directory(&destination)?;
    Ok(destination)
}

fn validate_materialized_resolution(
    workspace: &Path,
    source: &ValidatedGuestSource,
    expected_lock: &[u8],
) -> BuildResult<()> {
    let copied = read_and_validate_guest_inventory(workspace, &source.spec, true)?;
    if copied != source.files {
        return Err("materialized guest source differs from approved bytes".to_owned());
    }
    validate_guest_source_closure_v1(&copied)
        .map_err(|_| "materialized guest source closure was rejected".to_owned())?;
    if read_bounded_regular_nofollow(&workspace.join("Cargo.toml"), ROOT_MANIFEST_CAP)?
        != source.spec.root_manifest_utf8_lf.as_bytes()
        || read_bounded_regular_nofollow(&workspace.join("Cargo.lock"), LOCK_CAP)? != expected_lock
    {
        return Err("materialized root manifest or lock differs".to_owned());
    }
    reject_cargo_configs(workspace, None)
}

fn normalize_resolution_workspace(
    root: PathBuf,
    source: &ValidatedGuestSource,
    toolchain: &ToolchainInputs,
    cargo_home: &Path,
    target: &Path,
    temp: &Path,
) -> BuildResult<ResolutionEvidence> {
    let projection =
        project_resolution_workspace(root, source, toolchain, cargo_home, target, temp)?;
    let locked_metadata =
        lock_resolution_workspace(&projection, source, toolchain, cargo_home, target, temp)?;
    Ok(ResolutionEvidence {
        pruned_lock: projection.pruned_lock,
        locked_metadata,
    })
}

fn project_resolution_workspace(
    root: PathBuf,
    source: &ValidatedGuestSource,
    toolchain: &ToolchainInputs,
    cargo_home: &Path,
    target: &Path,
    temp: &Path,
) -> BuildResult<ResolutionProjection> {
    validate_materialized_resolution(&root, source, &source.production_lock)?;
    let _discarded = cargo_metadata_unlocked(toolchain, &root, cargo_home, target, temp)
        .map_err(|error| format!("guest-resolution unlocked {error}"))?;
    let pruned_lock = read_bounded_regular_nofollow(&root.join("Cargo.lock"), LOCK_CAP)?;
    validate_guest_resolution_lock_projection_v1(&source.production_lock, &pruned_lock)
        .map_err(|_| "guest-resolution lock is not a deletion-only projection".to_owned())?;
    validate_materialized_resolution(&root, source, &pruned_lock)?;
    Ok(ResolutionProjection { root, pruned_lock })
}

fn lock_resolution_workspace(
    projection: &ResolutionProjection,
    source: &ValidatedGuestSource,
    toolchain: &ToolchainInputs,
    cargo_home: &Path,
    target: &Path,
    temp: &Path,
) -> BuildResult<Vec<u8>> {
    validate_materialized_resolution(&projection.root, source, &projection.pruned_lock)?;
    let metadata = cargo_metadata(toolchain, &projection.root, cargo_home, target, temp)
        .map_err(|error| format!("guest-resolution locked {error}"))?;
    if read_bounded_regular_nofollow(&projection.root.join("Cargo.lock"), LOCK_CAP)?
        != projection.pruned_lock
    {
        return Err("locked metadata mutated the frozen resolution lock".to_owned());
    }
    Ok(metadata)
}

fn write_new_synced(path: &Path, bytes: &[u8]) -> BuildResult<()> {
    let parent = path
        .parent()
        .ok_or_else(|| "generated file has no parent".to_owned())?;
    fs::create_dir_all(parent).map_err(|_| "generated parent creation failed".to_owned())?;
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|_| "generated file create-new failed".to_owned())?;
    file.write_all(bytes)
        .and_then(|_| file.flush())
        .and_then(|_| file.sync_all())
        .map_err(|_| "generated file write/sync failed".to_owned())
}

fn reject_inherited_build_authority(toolchain: &ToolchainInputs) -> BuildResult<()> {
    const FORBIDDEN_EXACT: &[&str] = &[
        "AR",
        "CC",
        "CFLAGS",
        "CXX",
        "CXXFLAGS",
        "LD",
        "LDFLAGS",
        "RUSTC",
        "RUSTC_WRAPPER",
        "RUSTC_WORKSPACE_WRAPPER",
        "RUSTDOCFLAGS",
        "RUSTFLAGS",
        "CARGO_BUILD_RUSTC",
        "CARGO_BUILD_RUSTFLAGS",
    ];
    let expected_cargo_rustc = "rustc";
    let mut saw_cargo_rustc = false;
    let mut saw_encoded_rustflags = false;
    for (name, value) in std::env::vars_os() {
        let name = name
            .to_str()
            .ok_or_else(|| "non-UTF-8 inherited environment name".to_owned())?;
        if name == "RUSTC" {
            if value.as_os_str() != std::ffi::OsStr::new(expected_cargo_rustc) {
                return Err(
                    "Cargo's RUSTC binding differs from the pinned proxy spelling".to_owned(),
                );
            }
            saw_cargo_rustc = true;
            continue;
        }
        if name == "CARGO_ENCODED_RUSTFLAGS" {
            if !value.is_empty() {
                return Err("inherited encoded Rust flags are forbidden".to_owned());
            }
            saw_encoded_rustflags = true;
            continue;
        }
        if name == "RUSTC_WORKSPACE_WRAPPER" {
            validate_clippy_workspace_wrapper(value.as_os_str(), toolchain)?;
            continue;
        }
        let target_override = name.starts_with("CARGO_TARGET_")
            && (name.ends_with("_LINKER") || name.ends_with("_RUNNER"));
        let compiler_family = name.starts_with("CC_")
            || name.starts_with("CXX_")
            || name.starts_with("AR_")
            || name.starts_with("LD_")
            || name.ends_with("_CC")
            || name.ends_with("_CXX")
            || name.ends_with("_AR")
            || name.ends_with("_LD");
        if FORBIDDEN_EXACT.contains(&name)
            || name.starts_with("CARGO_PROFILE_RELEASE_")
            || target_override
            || compiler_family
        {
            return Err(format!("inherited build authority is forbidden: {name}"));
        }
    }
    if !saw_cargo_rustc {
        return Err("Cargo's mandatory RUSTC binding is absent".to_owned());
    }
    if !saw_encoded_rustflags {
        return Err("Cargo's mandatory encoded Rust-flags binding is absent".to_owned());
    }
    Ok(())
}

fn validate_clippy_workspace_wrapper(
    wrapper: &std::ffi::OsStr,
    toolchain: &ToolchainInputs,
) -> BuildResult<()> {
    let executable_name = if cfg!(windows) {
        "clippy-driver.exe"
    } else {
        "clippy-driver"
    };
    let expected = toolchain.rustc_sysroot.join("bin").join(executable_name);
    let supplied = PathBuf::from(wrapper);
    if !supplied.is_absolute()
        || supplied.file_name().and_then(|name| name.to_str()) != Some(executable_name)
        || supplied
            .canonicalize()
            .map_err(|_| "Clippy workspace wrapper identity failed".to_owned())?
            != expected
        || executable_identity_marker(&expected).is_err()
    {
        return Err("inherited Rust workspace wrapper is forbidden".to_owned());
    }
    Ok(())
}

fn discover_toolchain(scratch: &Path) -> BuildResult<ToolchainInputs> {
    let cargo_name = if cfg!(windows) { "cargo.exe" } else { "cargo" };
    let rustup_name = if cfg!(windows) {
        "rustup.exe"
    } else {
        "rustup"
    };
    let rustc_name = if cfg!(windows) { "rustc.exe" } else { "rustc" };
    let path = std::env::var_os("PATH").ok_or_else(|| "PATH is unavailable".to_owned())?;
    let mut selected = None;
    for directory in std::env::split_paths(&path) {
        if !directory.is_absolute() {
            continue;
        }
        let cargo = directory.join(cargo_name);
        let rustup = directory.join(rustup_name);
        if !cargo.exists() || !rustup.exists() {
            continue;
        }
        let cargo_resolved = cargo
            .canonicalize()
            .map_err(|_| "Cargo proxy resolution failed".to_owned())?;
        let rustup_resolved = rustup
            .canonicalize()
            .map_err(|_| "rustup proxy resolution failed".to_owned())?;
        let cargo_marker = executable_identity_marker(&cargo_resolved)?;
        let rustup_marker = executable_identity_marker(&rustup_resolved)?;
        if cargo_marker == rustup_marker {
            selected = Some((cargo, rustup, cargo_resolved, cargo_marker));
            break;
        }
    }
    let (cargo_proxy_invocation, rustup_proxy_invocation, cargo_resolved_identity, marker) =
        selected.ok_or_else(|| "validated rustup Cargo proxy not found".to_owned())?;
    let bin = cargo_proxy_invocation
        .parent()
        .filter(|parent| parent.file_name().and_then(|name| name.to_str()) == Some("bin"))
        .ok_or_else(|| "Cargo proxy is outside a canonical bin directory".to_owned())?;
    let seed_cargo_home = bin
        .parent()
        .ok_or_else(|| "Cargo home derivation failed".to_owned())?
        .canonicalize()
        .map_err(|_| "Cargo home identity validation failed".to_owned())?;
    let rustup_home_input = std::env::var_os("RUSTUP_HOME")
        .map(PathBuf::from)
        .or_else(|| std::env::var_os("HOME").map(|home| PathBuf::from(home).join(".rustup")))
        .ok_or_else(|| "Rustup home is unavailable".to_owned())?;
    let rustup_home = rustup_home_input
        .canonicalize()
        .map_err(|_| "Rustup home identity validation failed".to_owned())?;
    if !rustup_home.is_dir() {
        return Err("Rustup home is not a directory".to_owned());
    }

    #[cfg(windows)]
    let system_root = {
        use std::os::windows::fs::MetadataExt;

        const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;
        let root = std::env::var_os("SystemRoot")
            .ok_or_else(|| "Windows SystemRoot is absent".to_owned())?;
        let root = PathBuf::from(root)
            .canonicalize()
            .map_err(|_| "Windows SystemRoot identity failed".to_owned())?;
        let system32 = root.join("System32");
        for executable in [system32.join("cmd.exe"), system32.join("taskkill.exe")] {
            let metadata = fs::symlink_metadata(&executable)
                .map_err(|_| "Windows system executable metadata failed".to_owned())?;
            if !metadata.is_file()
                || metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
                || executable
                    .canonicalize()
                    .map_err(|_| "Windows system executable identity failed".to_owned())?
                    != executable
            {
                return Err("Windows system executable is indirect".to_owned());
            }
        }
        Some(root)
    };
    #[cfg(not(windows))]
    let system_root = None;

    let proxies = ToolProxySet {
        cargo_proxy_invocation,
        rustc_proxy_invocation: seed_cargo_home.join("bin").join(rustc_name),
        rustup_proxy_invocation,
        cargo_resolved_identity,
        marker,
    };
    validate_tool_proxy_set(&proxies)?;
    let context = CleanToolContext {
        proxies: &proxies,
        cargo_home: &seed_cargo_home,
        rustup_home: &rustup_home,
        temp: scratch,
        system_root: system_root.as_deref(),
    };
    let cargo_version = successful_utf8(
        run_clean_tool(
            &context,
            &proxies.cargo_proxy_invocation,
            &["+1.96.1", "-Vv"],
            TOOL_PROBE_TIMEOUT,
        )?,
        "Cargo version probe",
    )?;
    if !cargo_version.lines().any(|line| line == "release: 1.96.1") {
        return Err("Cargo proxy did not report pinned release 1.96.1".to_owned());
    }
    let sysroot_text = successful_utf8(
        run_clean_tool(
            &context,
            &proxies.rustc_proxy_invocation,
            &["+1.96.1", "--print", "sysroot"],
            TOOL_PROBE_TIMEOUT,
        )?,
        "rustc sysroot probe",
    )?;
    let sysroot_spelling = sysroot_text.trim_end_matches(['\r', '\n']);
    if sysroot_spelling.is_empty() || sysroot_spelling.lines().count() != 1 {
        return Err("rustc sysroot output was not one bounded path".to_owned());
    }
    let rustc_sysroot = PathBuf::from(sysroot_spelling)
        .canonicalize()
        .map_err(|_| "rustc sysroot identity validation failed".to_owned())?;
    if !rustc_sysroot.join("bin").is_dir()
        || !rustc_sysroot.join("lib/rustlib/wasm32-wasip1/lib").is_dir()
    {
        return Err("pinned sysroot or wasm32-wasip1 target is absent".to_owned());
    }
    Ok(ToolchainInputs {
        proxies,
        seed_cargo_home,
        rustup_home,
        rustc_sysroot,
        system_root,
    })
}

fn executable_identity_marker(path: &Path) -> BuildResult<ExecutableIdentityMarker> {
    use cap_fs_ext::MetadataExt;

    let file = fs::File::open(path).map_err(|_| "tool executable open failed".to_owned())?;
    let std_metadata = file
        .metadata()
        .map_err(|_| "tool executable metadata failed".to_owned())?;
    if !std_metadata.is_file() {
        return Err("tool executable is not a regular file".to_owned());
    }
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;
        if std_metadata.permissions().mode() & 0o111 == 0 {
            return Err("tool executable lacks execute permission".to_owned());
        }
    }
    let metadata = cap_std::fs::File::from_std(file)
        .metadata()
        .map_err(|_| "tool executable handle metadata failed".to_owned())?;
    Ok(ExecutableIdentityMarker {
        device: MetadataExt::dev(&metadata),
        file_id: MetadataExt::ino(&metadata),
        byte_length: metadata.len(),
    })
}

fn validate_tool_proxy_set(proxies: &ToolProxySet) -> BuildResult<()> {
    let expected_names = if cfg!(windows) {
        ["cargo.exe", "rustc.exe", "rustup.exe"]
    } else {
        ["cargo", "rustc", "rustup"]
    };
    for (proxy, expected_name) in [
        (&proxies.cargo_proxy_invocation, expected_names[0]),
        (&proxies.rustc_proxy_invocation, expected_names[1]),
        (&proxies.rustup_proxy_invocation, expected_names[2]),
    ] {
        if !proxy.is_absolute()
            || proxy.file_name().and_then(|name| name.to_str()) != Some(expected_name)
        {
            return Err("tool proxy spelling differs from frozen identity".to_owned());
        }
        let resolved = proxy
            .canonicalize()
            .map_err(|_| "tool proxy resolution failed".to_owned())?;
        if executable_identity_marker(&resolved)? != proxies.marker {
            return Err("tool proxy does not name recorded identity".to_owned());
        }
    }
    if proxies
        .cargo_resolved_identity
        .canonicalize()
        .map_err(|_| "Cargo identity resolution failed".to_owned())?
        != proxies.cargo_resolved_identity
        || executable_identity_marker(&proxies.cargo_resolved_identity)? != proxies.marker
    {
        return Err("recorded Cargo executable identity changed".to_owned());
    }
    Ok(())
}

struct CleanToolContext<'a> {
    proxies: &'a ToolProxySet,
    cargo_home: &'a Path,
    rustup_home: &'a Path,
    temp: &'a Path,
    system_root: Option<&'a Path>,
}

fn run_clean_tool(
    context: &CleanToolContext<'_>,
    executable: &Path,
    arguments: &[&str],
    timeout: Duration,
) -> BuildResult<BoundedOutput> {
    validate_tool_proxy_set(context.proxies)?;
    let mut command = Command::new(executable);
    command
        .args(arguments)
        .env_clear()
        .env("CARGO_HOME", context.cargo_home)
        .env("CARGO_NET_OFFLINE", "true")
        .env("HOME", context.cargo_home)
        .env("USERPROFILE", context.cargo_home)
        .env("RUSTUP_HOME", context.rustup_home)
        .env("RUSTUP_TOOLCHAIN", "1.96.1")
        .env("TMPDIR", context.temp)
        .env("TMP", context.temp)
        .env("TEMP", context.temp)
        .env("LANG", "C")
        .env("LC_ALL", "C")
        .env("TZ", "UTC")
        .env("SOURCE_DATE_EPOCH", "0")
        .env("CARGO_TERM_COLOR", "never");
    let output = run_bounded_with_system_root(&mut command, timeout, context.system_root);
    validate_tool_proxy_set(context.proxies)?;
    output
}

#[derive(Debug)]
struct BoundedOutput {
    status: ExitStatus,
    stdout: Vec<u8>,
    stderr: Vec<u8>,
}

#[derive(Debug)]
struct StreamCapture {
    bytes: Vec<u8>,
    overflowed: bool,
}

fn run_bounded_with_system_root(
    command: &mut Command,
    timeout: Duration,
    system_root: Option<&Path>,
) -> BuildResult<BoundedOutput> {
    run_bounded_with_options(command, timeout, PROCESS_STREAM_CAP, system_root)
}

fn run_bounded_with_options(
    command: &mut Command,
    timeout: Duration,
    stream_cap: usize,
    system_root: Option<&Path>,
) -> BuildResult<BoundedOutput> {
    if stream_cap == 0 || stream_cap > PROCESS_STREAM_CAP {
        return Err("bounded subprocess stream cap is invalid".to_owned());
    }
    configure_process_tree(command);
    let mut child = command
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .map_err(|_| "bounded subprocess spawn failed".to_owned())?;
    let stdout = child
        .stdout
        .take()
        .ok_or_else(|| "bounded stdout pipe unavailable".to_owned())?;
    let stderr = child
        .stderr
        .take()
        .ok_or_else(|| "bounded stderr pipe unavailable".to_owned())?;
    let overflow = Arc::new(AtomicBool::new(false));
    let stdout_overflow = Arc::clone(&overflow);
    let stderr_overflow = Arc::clone(&overflow);
    let (stdout_sender, stdout_receiver) = mpsc::sync_channel(1);
    let (stderr_sender, stderr_receiver) = mpsc::sync_channel(1);
    thread::spawn(move || {
        let _ = stdout_sender.send(capture_stream(stdout, stdout_overflow, stream_cap));
    });
    thread::spawn(move || {
        let _ = stderr_sender.send(capture_stream(stderr, stderr_overflow, stream_cap));
    });
    let started = Instant::now();
    let (status, stream_overflow, timed_out) = loop {
        let stream_overflow = overflow.load(Ordering::SeqCst);
        let timed_out = started.elapsed() > timeout;
        if stream_overflow || timed_out {
            terminate_process_tree(child.id(), false, system_root)?;
            let grace_started = Instant::now();
            let status = 'cancellation: loop {
                if let Some(status) = child
                    .try_wait()
                    .map_err(|_| "bounded cancellation status failed".to_owned())?
                {
                    break 'cancellation status;
                }
                if grace_started.elapsed() >= Duration::from_millis(100) {
                    terminate_process_tree(child.id(), true, system_root)?;
                    let hard_started = Instant::now();
                    loop {
                        if let Some(status) = child
                            .try_wait()
                            .map_err(|_| "bounded hard-cancellation status failed".to_owned())?
                        {
                            break 'cancellation status;
                        }
                        if hard_started.elapsed() >= Duration::from_secs(1) {
                            return Err("subprocess tree resisted forced termination".to_owned());
                        }
                        thread::sleep(PROCESS_POLL_INTERVAL);
                    }
                }
                thread::sleep(PROCESS_POLL_INTERVAL);
            };
            terminate_process_tree(child.id(), true, system_root)?;
            break (status, stream_overflow, timed_out);
        }
        if let Some(status) = child
            .try_wait()
            .map_err(|_| "bounded subprocess status failed".to_owned())?
        {
            terminate_process_tree(child.id(), true, system_root)?;
            break (status, stream_overflow, timed_out);
        }
        thread::sleep(PROCESS_POLL_INTERVAL);
    };
    let drain_deadline = Duration::from_secs(1);
    let stdout = stdout_receiver
        .recv_timeout(drain_deadline)
        .map_err(|_| "bounded stdout did not drain".to_owned())?
        .map_err(|_| "bounded stdout read failed".to_owned())?;
    let stderr = stderr_receiver
        .recv_timeout(drain_deadline)
        .map_err(|_| "bounded stderr did not drain".to_owned())?
        .map_err(|_| "bounded stderr read failed".to_owned())?;
    if stdout.overflowed || stderr.overflowed || stream_overflow {
        return Err("bounded subprocess exceeded its stream cap".to_owned());
    }
    if timed_out {
        return Err("bounded subprocess exceeded its deadline".to_owned());
    }
    Ok(BoundedOutput {
        status,
        stdout: stdout.bytes,
        stderr: stderr.bytes,
    })
}

fn configure_process_tree(command: &mut Command) {
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NEW_PROCESS_GROUP: u32 = 0x0000_0200;
        command.creation_flags(CREATE_NEW_PROCESS_GROUP);
    }
}

fn terminate_process_tree(
    process_id: u32,
    force: bool,
    system_root: Option<&Path>,
) -> BuildResult<()> {
    #[cfg(unix)]
    {
        let _ = system_root;
        let signal = if force { "-KILL" } else { "-TERM" };
        let group = format!("-{process_id}");
        let _status = Command::new("/bin/kill")
            .args([signal, &group])
            .env_clear()
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map_err(|_| "process-group terminator failed to start".to_owned())?;
    }
    #[cfg(windows)]
    {
        let system_root = system_root
            .ok_or_else(|| "Windows termination lacks validated SystemRoot".to_owned())?;
        let system32 = system_root.join("System32");
        let mut command = Command::new(system32.join("taskkill.exe"));
        command.args(["/PID", &process_id.to_string(), "/T"]);
        if force {
            command.arg("/F");
        }
        let _status = command
            .env_clear()
            .env("SystemRoot", system_root)
            .env("WINDIR", system_root)
            .env("ComSpec", system32.join("cmd.exe"))
            .env("PATHEXT", ".COM;.EXE;.BAT;.CMD")
            .current_dir(system32)
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .map_err(|_| "Windows process-tree terminator failed to start".to_owned())?;
    }
    Ok(())
}

fn capture_stream(
    mut stream: impl Read,
    overflow: Arc<AtomicBool>,
    stream_cap: usize,
) -> std::io::Result<StreamCapture> {
    let mut retained = Vec::new();
    let mut buffer = [0_u8; 16 * 1024];
    let mut overflowed = false;
    loop {
        let read = stream.read(&mut buffer)?;
        if read == 0 {
            break;
        }
        let remaining = stream_cap.saturating_sub(retained.len());
        let keep = remaining.min(read);
        retained.extend_from_slice(&buffer[..keep]);
        if keep != read {
            overflowed = true;
            overflow.store(true, Ordering::SeqCst);
        }
    }
    Ok(StreamCapture {
        bytes: retained,
        overflowed,
    })
}

fn successful_utf8(output: BoundedOutput, label: &str) -> BuildResult<String> {
    if !output.status.success() {
        return Err(format!(
            "{label} failed: stdout_bytes={} stdout_sha256={} stderr_bytes={} stderr_sha256={}",
            output.stdout.len(),
            hash_bytes(&output.stdout),
            output.stderr.len(),
            hash_bytes(&output.stderr)
        ));
    }
    if !output.stderr.is_empty() {
        return Err(format!("{label} emitted unexpected stderr"));
    }
    String::from_utf8(output.stdout).map_err(|_| format!("{label} emitted non-UTF-8 stdout"))
}

fn reject_cargo_configs(workspace: &Path, cargo_home: Option<&Path>) -> BuildResult<()> {
    for ancestor in workspace.ancestors() {
        for relative in [".cargo/config", ".cargo/config.toml"] {
            match fs::symlink_metadata(ancestor.join(relative)) {
                Ok(_) => return Err("uncontrolled Cargo config exists in ancestry".to_owned()),
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                Err(_) => return Err("Cargo config ancestry inspection failed".to_owned()),
            }
        }
    }
    if let Some(cargo_home) = cargo_home {
        for name in ["config", "config.toml"] {
            match fs::symlink_metadata(cargo_home.join(name)) {
                Ok(_) => return Err("uncontrolled Cargo-home config exists".to_owned()),
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
                Err(_) => return Err("Cargo-home config inspection failed".to_owned()),
            }
        }
    }
    Ok(())
}

fn cargo_metadata(
    toolchain: &ToolchainInputs,
    workspace: &Path,
    cargo_home: &Path,
    target: &Path,
    temp: &Path,
) -> BuildResult<Vec<u8>> {
    cargo_metadata_with_lock_mode(toolchain, workspace, cargo_home, target, temp, true)
}

fn cargo_metadata_unlocked(
    toolchain: &ToolchainInputs,
    workspace: &Path,
    cargo_home: &Path,
    target: &Path,
    temp: &Path,
) -> BuildResult<Vec<u8>> {
    cargo_metadata_with_lock_mode(toolchain, workspace, cargo_home, target, temp, false)
}

fn cargo_metadata_with_lock_mode(
    toolchain: &ToolchainInputs,
    workspace: &Path,
    cargo_home: &Path,
    target: &Path,
    temp: &Path,
    locked: bool,
) -> BuildResult<Vec<u8>> {
    validate_tool_proxy_set(&toolchain.proxies)?;
    let mut command = Command::new(&toolchain.proxies.cargo_proxy_invocation);
    command.current_dir(workspace).args([
        "+1.96.1",
        "metadata",
        "--offline",
        "--filter-platform",
        "wasm32-wasip1",
        "--format-version",
        "1",
    ]);
    if locked {
        command.arg("--locked");
    }
    command.env_clear();
    apply_clean_cargo_environment(&mut command, toolchain, cargo_home, target, temp)?;
    let output = run_bounded_with_system_root(
        &mut command,
        TOOL_PROBE_TIMEOUT,
        toolchain.system_root.as_deref(),
    );
    validate_tool_proxy_set(&toolchain.proxies)?;
    let output = output?;
    if !output.status.success() {
        return Err(format!(
            "offline Cargo metadata (locked={locked}) failed: stdout_bytes={} stdout_sha256={} stderr_bytes={} stderr_sha256={}",
            output.stdout.len(),
            hash_bytes(&output.stdout),
            output.stderr.len(),
            hash_bytes(&output.stderr)
        ));
    }
    std::str::from_utf8(&output.stdout)
        .map_err(|_| "Cargo metadata stdout was not UTF-8".to_owned())?;
    Ok(output.stdout)
}

fn apply_clean_cargo_environment(
    command: &mut Command,
    toolchain: &ToolchainInputs,
    cargo_home: &Path,
    target: &Path,
    temp: &Path,
) -> BuildResult<()> {
    let mut path_entries = vec![toolchain.rustc_sysroot.join("bin")];
    #[cfg(unix)]
    path_entries.extend([PathBuf::from("/usr/bin"), PathBuf::from("/bin")]);
    #[cfg(windows)]
    {
        let system_root = toolchain
            .system_root
            .as_ref()
            .ok_or_else(|| "Windows SystemRoot is unavailable".to_owned())?;
        path_entries.extend([system_root.join("System32"), system_root.clone()]);
    }
    let controlled_path = std::env::join_paths(path_entries)
        .map_err(|_| "controlled PATH construction failed".to_owned())?;
    command
        .env("CARGO_HOME", cargo_home)
        .env("CARGO_TARGET_DIR", target)
        .env("CARGO_NET_OFFLINE", "true")
        .env("CARGO_INCREMENTAL", "0")
        .env("CARGO_TERM_COLOR", "never")
        .env("HOME", cargo_home)
        .env("USERPROFILE", cargo_home)
        .env("RUSTUP_HOME", &toolchain.rustup_home)
        .env("RUSTUP_TOOLCHAIN", "1.96.1")
        .env("PATH", controlled_path)
        .env("TMPDIR", temp)
        .env("TMP", temp)
        .env("TEMP", temp)
        .env("LANG", "C")
        .env("LC_ALL", "C")
        .env("TZ", "UTC")
        .env("SOURCE_DATE_EPOCH", "0");
    #[cfg(windows)]
    {
        let system_root = toolchain
            .system_root
            .as_ref()
            .ok_or_else(|| "Windows SystemRoot is unavailable".to_owned())?;
        command
            .env("SystemRoot", system_root)
            .env("WINDIR", system_root)
            .env("ComSpec", system_root.join("System32/cmd.exe"))
            .env("PATHEXT", ".COM;.EXE;.BAT;.CMD");
    }
    Ok(())
}

fn prepare_independent_resolution(
    base: &Path,
    source: &ValidatedGuestSource,
    toolchain: &ToolchainInputs,
    seed_cache_plan: &GuestResolutionCachePlanV1,
    seed_pruned_lock: &[u8],
) -> BuildResult<IndependentResolution> {
    fs::create_dir_all(base).map_err(|_| "independent root creation failed".to_owned())?;
    let base = canonical_directory(base, "independent root")?;
    let cargo_home =
        create_canonical_directory(&base.join("cargo-cache"), "independent Cargo home")?;
    let resolution_target =
        create_canonical_directory(&base.join("resolution-target"), "resolution target")?;
    let resolution_temp =
        create_canonical_directory(&resolution_target.join("tmp"), "resolution temp")?;
    reject_cargo_configs(&source.workspace, Some(&cargo_home))?;
    let cache_input_snapshot =
        materialize_verified_cache(&toolchain.seed_cargo_home, &cargo_home, seed_cache_plan)?;
    let resolution_root = materialize_resolution_workspace(&base.join("guest-resolution"), source)?;
    let resolution = project_resolution_workspace(
        resolution_root,
        source,
        toolchain,
        &cargo_home,
        &resolution_target,
        &resolution_temp,
    )?;
    validate_pruned_cache(&cargo_home, seed_cache_plan, &cache_input_snapshot, true)?;
    let cache_plan =
        guest_resolution_cache_plan_v1(&source.production_lock, &resolution.pruned_lock)
            .map_err(|_| "independent cache-plan derivation failed".to_owned())?;
    if resolution.pruned_lock != seed_pruned_lock || cache_plan != *seed_cache_plan {
        return Err("independent unlocked resolution differs from seed evidence".to_owned());
    }
    Ok(IndependentResolution {
        cargo_home,
        resolution_target,
        resolution_temp,
        resolution,
        cache_plan,
        cache_input_snapshot,
    })
}

fn validate_active_registry_roots_against_plan(
    graph: &ArtifactDependencyGraphV1,
    resolution: &IndependentResolution,
) -> BuildResult<()> {
    let archives = resolution
        .cache_plan
        .resolution_archives
        .iter()
        .map(|archive| (archive.normalized_dependency_id.as_str(), archive))
        .collect::<std::collections::BTreeMap<_, _>>();
    if archives.len() != resolution.cache_plan.resolution_archives.len() {
        return Err("cache plan contains duplicate normalized identities".to_owned());
    }
    for root in &graph.registry_roots {
        let archive = archives
            .get(root.normalized_dependency_id.as_str())
            .copied()
            .ok_or_else(|| "active registry identity is absent from cache plan".to_owned())?;
        let expected = resolution
            .cargo_home
            .join(&archive.unpacked_source_cargo_home_relative_path);
        let canonical = expected
            .canonicalize()
            .map_err(|_| "active registry source is missing".to_owned())?;
        if canonical != expected || !canonical.is_dir() || root.physical_root != canonical {
            return Err("active registry root differs from cache-plan source".to_owned());
        }
    }
    Ok(())
}

fn materialize_verified_cache(
    seed_cargo_home: &Path,
    destination_cargo_home: &Path,
    plan: &GuestResolutionCachePlanV1,
) -> BuildResult<Vec<SnapshotRecord>> {
    const COPY_FILE_CAP: usize = 512 * 1024 * 1024;

    let seed = cap_std::fs::Dir::open_ambient_dir(seed_cargo_home, cap_std::ambient_authority())
        .map_err(|_| "seed Cargo home open failed".to_owned())?;
    let destination =
        cap_std::fs::Dir::open_ambient_dir(destination_cargo_home, cap_std::ambient_authority())
            .map_err(|_| "independent Cargo home open failed".to_owned())?;
    copy_cache_plan_file(
        &seed,
        &destination,
        &plan.sparse_config_cargo_home_relative_path,
        None,
        COPY_FILE_CAP,
    )?;
    for entry in &plan.sparse_index_entries {
        copy_cache_plan_file(
            &seed,
            &destination,
            &entry.cargo_home_relative_path,
            None,
            COPY_FILE_CAP,
        )?;
    }
    for archive in &plan.resolution_archives {
        copy_cache_plan_file(
            &seed,
            &destination,
            &archive.cargo_home_relative_path,
            Some(&archive.sha256),
            COPY_FILE_CAP,
        )?;
    }
    let snapshot = snapshot_pruned_cargo_home(destination_cargo_home)?;
    validate_pruned_cache_snapshot(plan, &snapshot, &snapshot, false)?;
    Ok(snapshot)
}

fn copy_cache_plan_file(
    source_root: &cap_std::fs::Dir,
    destination_root: &cap_std::fs::Dir,
    relative: &str,
    expected_sha256: Option<&str>,
    cap: usize,
) -> BuildResult<()> {
    use cap_fs_ext::{FollowSymlinks, MetadataExt, OpenOptionsFollowExt};

    let source_bytes = read_cap_relative_file(source_root, relative, cap)?;
    if expected_sha256.is_some_and(|expected| hash_bytes(&source_bytes) != expected) {
        return Err("seed cache archive checksum mismatch".to_owned());
    }
    let (parent, name) = ensure_cap_parent(destination_root, relative)?;
    let mut options = cap_std::fs::OpenOptions::new();
    options
        .write(true)
        .create_new(true)
        .follow(FollowSymlinks::No);
    let mut file = parent
        .open_with(&name, &options)
        .map_err(|_| "independent cache file create-new failed".to_owned())?;
    file.write_all(&source_bytes)
        .and_then(|_| file.flush())
        .and_then(|_| file.sync_all())
        .map_err(|_| "independent cache file write/sync failed".to_owned())?;
    let metadata = file
        .metadata()
        .map_err(|_| "independent cache file metadata failed".to_owned())?;
    if !metadata.is_file() || MetadataExt::nlink(&metadata) != 1 {
        return Err("independent cache file identity is invalid".to_owned());
    }
    drop(file);
    if read_cap_relative_file(destination_root, relative, cap)? != source_bytes {
        return Err("independent cache file bytes differ after copy".to_owned());
    }
    Ok(())
}

fn ensure_cap_parent(
    root: &cap_std::fs::Dir,
    relative: &str,
) -> BuildResult<(cap_std::fs::Dir, std::ffi::OsString)> {
    use cap_fs_ext::DirExt;

    let path = Path::new(relative);
    if path.is_absolute()
        || path
            .components()
            .any(|component| !matches!(component, std::path::Component::Normal(_)))
    {
        return Err("cache-plan path is not canonical relative syntax".to_owned());
    }
    let name = path
        .file_name()
        .ok_or_else(|| "cache-plan path has no name".to_owned())?
        .to_os_string();
    let mut directory = root
        .try_clone()
        .map_err(|_| "cache destination root clone failed".to_owned())?;
    let parent = path
        .parent()
        .ok_or_else(|| "cache-plan path has no parent".to_owned())?;
    for component in parent.components() {
        let component = component.as_os_str();
        match directory.symlink_metadata(component) {
            Ok(metadata) if metadata.is_dir() => {}
            Ok(_) => return Err("cache parent is indirect or non-directory".to_owned()),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => directory
                .create_dir(component)
                .map_err(|_| "cache parent creation failed".to_owned())?,
            Err(_) => return Err("cache parent inspection failed".to_owned()),
        }
        directory = directory
            .open_dir_nofollow(component)
            .map_err(|_| "cache parent is indirect".to_owned())?;
    }
    Ok((directory, name))
}

fn snapshot_seed_cargo_inputs(seed_cargo_home: &Path) -> BuildResult<Vec<SnapshotRecord>> {
    const ENTRY_CAP: usize = 131_072;
    const CONTENT_CAP: u64 = 16 * 1024 * 1024 * 1024;

    use cap_fs_ext::DirExt;

    let root = cap_std::fs::Dir::open_ambient_dir(seed_cargo_home, cap_std::ambient_authority())
        .map_err(|_| "seed Cargo snapshot root open failed".to_owned())?;
    for config in ["config", "config.toml"] {
        match root.symlink_metadata(config) {
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Ok(_) => return Err("seed Cargo root config appeared".to_owned()),
            Err(_) => return Err("seed Cargo root config inspection failed".to_owned()),
        }
    }
    let registry = root
        .open_dir_nofollow("registry")
        .map_err(|_| "seed Cargo registry root is missing or indirect".to_owned())?;
    let mut records = vec![
        SnapshotRecord {
            relative_path: "config".to_owned(),
            kind: 0,
            byte_length: 0,
            sha256: String::new(),
            device: 0,
            file_id: 0,
            link_count: 0,
        },
        SnapshotRecord {
            relative_path: "config.toml".to_owned(),
            kind: 0,
            byte_length: 0,
            sha256: String::new(),
            device: 0,
            file_id: 0,
            link_count: 0,
        },
    ];
    let mut total_bytes = 0_u64;
    for name in ["cache", "index", "src"] {
        let directory = registry
            .open_dir_nofollow(name)
            .map_err(|_| "seed Cargo snapshot subtree is missing or indirect".to_owned())?;
        let metadata = directory
            .dir_metadata()
            .map_err(|_| "seed Cargo snapshot subtree metadata failed".to_owned())?;
        records.push(SnapshotRecord {
            relative_path: format!("registry/{name}"),
            kind: 1,
            byte_length: 0,
            sha256: String::new(),
            device: cap_fs_ext::MetadataExt::dev(&metadata),
            file_id: cap_fs_ext::MetadataExt::ino(&metadata),
            link_count: 0,
        });
        snapshot_cap_directory(
            &directory,
            &format!("registry/{name}"),
            &mut records,
            &mut total_bytes,
            ENTRY_CAP,
            CONTENT_CAP,
        )?;
    }
    if records.len() > ENTRY_CAP {
        return Err("seed Cargo snapshot exceeded its entry cap".to_owned());
    }
    records.sort();
    Ok(records)
}

fn snapshot_pruned_cargo_home(cargo_home: &Path) -> BuildResult<Vec<SnapshotRecord>> {
    const ENTRY_CAP: usize = 131_072;
    const CONTENT_CAP: u64 = 16 * 1024 * 1024 * 1024;

    let root = cap_std::fs::Dir::open_ambient_dir(cargo_home, cap_std::ambient_authority())
        .map_err(|_| "independent Cargo snapshot root open failed".to_owned())?;
    let mut records = Vec::new();
    let mut total_bytes = 0_u64;
    snapshot_cap_directory(
        &root,
        "",
        &mut records,
        &mut total_bytes,
        ENTRY_CAP,
        CONTENT_CAP,
    )?;
    records.sort();
    Ok(records)
}

fn snapshot_cap_directory(
    directory: &cap_std::fs::Dir,
    relative: &str,
    records: &mut Vec<SnapshotRecord>,
    total_bytes: &mut u64,
    entry_cap: usize,
    content_cap: u64,
) -> BuildResult<()> {
    use cap_fs_ext::{DirExt, FollowSymlinks, MetadataExt, OpenOptionsFollowExt};

    let mut names = directory
        .entries()
        .map_err(|_| "Cargo snapshot enumeration failed".to_owned())?
        .map(|entry| {
            entry
                .map_err(|_| "Cargo snapshot entry failed".to_owned())?
                .file_name()
                .into_string()
                .map_err(|_| "Cargo snapshot name is not UTF-8".to_owned())
        })
        .collect::<BuildResult<Vec<_>>>()?;
    names.sort();
    for name in names {
        if records.len() >= entry_cap {
            return Err("Cargo snapshot exceeded its entry cap".to_owned());
        }
        let child_relative = if relative.is_empty() {
            name.clone()
        } else {
            format!("{relative}/{name}")
        };
        let metadata = directory
            .symlink_metadata(&name)
            .map_err(|_| "Cargo snapshot metadata failed".to_owned())?;
        if metadata.is_dir() {
            let child = directory
                .open_dir_nofollow(&name)
                .map_err(|_| "Cargo snapshot directory is indirect".to_owned())?;
            records.push(SnapshotRecord {
                relative_path: child_relative.clone(),
                kind: 1,
                byte_length: 0,
                sha256: String::new(),
                device: MetadataExt::dev(&metadata),
                file_id: MetadataExt::ino(&metadata),
                link_count: 0,
            });
            snapshot_cap_directory(
                &child,
                &child_relative,
                records,
                total_bytes,
                entry_cap,
                content_cap,
            )?;
        } else if metadata.is_file() {
            *total_bytes = total_bytes
                .checked_add(metadata.len())
                .ok_or_else(|| "Cargo snapshot byte count overflow".to_owned())?;
            if *total_bytes > content_cap {
                return Err("Cargo snapshot exceeded its content cap".to_owned());
            }
            let mut options = cap_std::fs::OpenOptions::new();
            options.read(true).follow(FollowSymlinks::No);
            let mut file = directory
                .open_with(&name, &options)
                .map_err(|_| "Cargo snapshot file open failed".to_owned())?;
            let before = file
                .metadata()
                .map_err(|_| "Cargo snapshot file metadata failed".to_owned())?;
            let digest = hash_reader(&mut file)?;
            let after = file
                .metadata()
                .map_err(|_| "Cargo snapshot file metadata recheck failed".to_owned())?;
            if before.len() != metadata.len()
                || after.len() != before.len()
                || MetadataExt::dev(&after) != MetadataExt::dev(&before)
                || MetadataExt::ino(&after) != MetadataExt::ino(&before)
            {
                return Err("Cargo snapshot file changed while hashing".to_owned());
            }
            records.push(SnapshotRecord {
                relative_path: child_relative,
                kind: 2,
                byte_length: before.len(),
                sha256: digest,
                device: MetadataExt::dev(&before),
                file_id: MetadataExt::ino(&before),
                link_count: MetadataExt::nlink(&before),
            });
        } else {
            return Err("Cargo snapshot contains a symlink or special entry".to_owned());
        }
    }
    Ok(())
}

fn validate_pruned_cache(
    cargo_home: &Path,
    plan: &GuestResolutionCachePlanV1,
    input_snapshot: &[SnapshotRecord],
    allow_sources: bool,
) -> BuildResult<Vec<SnapshotRecord>> {
    let current = snapshot_pruned_cargo_home(cargo_home)?;
    validate_pruned_cache_snapshot(plan, input_snapshot, &current, allow_sources)?;
    Ok(current)
}

fn validate_pruned_cache_snapshot(
    plan: &GuestResolutionCachePlanV1,
    input_snapshot: &[SnapshotRecord],
    current: &[SnapshotRecord],
    allow_sources: bool,
) -> BuildResult<()> {
    use std::collections::{BTreeMap, BTreeSet};

    const CACHEDIR_TAG_SHA256: &str =
        "6d9d1d216e0f83abc5e5662ca62c92b4f23009466b54fa27321a69acdb778bb2";
    const EMPTY_SHA256: &str = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855";
    const GLOBAL_CACHE_CAP: u64 = 16 * 1024 * 1024;

    let mut planned_files = BTreeMap::<String, Option<&str>>::new();
    planned_files.insert(plan.sparse_config_cargo_home_relative_path.clone(), None);
    for entry in &plan.sparse_index_entries {
        if planned_files
            .insert(entry.cargo_home_relative_path.clone(), None)
            .is_some()
        {
            return Err("cache plan repeats an input path".to_owned());
        }
    }
    for archive in &plan.resolution_archives {
        if planned_files
            .insert(
                archive.cargo_home_relative_path.clone(),
                Some(archive.sha256.as_str()),
            )
            .is_some()
        {
            return Err("cache plan repeats an archive path".to_owned());
        }
    }
    let mut planned_directories = BTreeSet::new();
    for path in planned_files.keys() {
        insert_relative_parents(path, &mut planned_directories)?;
    }
    let mut source_roots = BTreeSet::new();
    let mut source_parent_directories = BTreeSet::new();
    for archive in &plan.resolution_archives {
        if !source_roots.insert(archive.unpacked_source_cargo_home_relative_path.clone()) {
            return Err("cache plan repeats an unpacked source root".to_owned());
        }
        insert_relative_parents(
            &archive.unpacked_source_cargo_home_relative_path,
            &mut source_parent_directories,
        )?;
    }

    let inputs = input_snapshot
        .iter()
        .map(|record| (record.relative_path.as_str(), record))
        .collect::<BTreeMap<_, _>>();
    if inputs.len() != input_snapshot.len()
        || input_snapshot.len() != planned_files.len() + planned_directories.len()
    {
        return Err("initial cache snapshot is not the exact planned inventory".to_owned());
    }
    for directory in &planned_directories {
        let record = inputs
            .get(directory.as_str())
            .copied()
            .ok_or_else(|| "planned cache parent is missing".to_owned())?;
        if record.kind != 1 {
            return Err("planned cache parent is not a directory".to_owned());
        }
    }
    for (path, expected_hash) in &planned_files {
        let record = inputs
            .get(path.as_str())
            .copied()
            .ok_or_else(|| "planned cache input is missing".to_owned())?;
        if record.kind != 2
            || record.byte_length == 0
            || record.link_count != 1
            || expected_hash.is_some_and(|expected| record.sha256 != expected)
        {
            return Err("planned cache input violates type/hash/identity".to_owned());
        }
    }

    let current_by_path = current
        .iter()
        .map(|record| (record.relative_path.as_str(), record))
        .collect::<BTreeMap<_, _>>();
    if current_by_path.len() != current.len() {
        return Err("independent cache contains duplicate paths".to_owned());
    }
    for input in input_snapshot {
        if current_by_path.get(input.relative_path.as_str()).copied() != Some(input) {
            return Err("planned cache input or parent changed".to_owned());
        }
    }
    for record in current {
        let path = record.relative_path.as_str();
        if inputs.contains_key(path) {
            continue;
        }
        if allow_sources && source_parent_directories.contains(path) && record.kind == 1 {
            continue;
        }
        if allow_sources
            && source_roots.iter().any(|root| {
                path == root
                    || path
                        .strip_prefix(root)
                        .is_some_and(|tail| tail.starts_with('/'))
            })
        {
            if (source_roots.contains(path) && record.kind != 1)
                || !matches!(record.kind, 1 | 2)
                || record.kind == 2 && record.link_count != 1
            {
                return Err("unpacked cache source has invalid type or identity".to_owned());
            }
            continue;
        }
        let allowed_generated = match path {
            "registry/CACHEDIR.TAG" => {
                record.kind == 2
                    && record.link_count == 1
                    && record.byte_length == 177
                    && record.sha256 == CACHEDIR_TAG_SHA256
            }
            ".package-cache" | ".package-cache-mutate" => {
                record.kind == 2
                    && record.link_count == 1
                    && record.byte_length == 0
                    && record.sha256 == EMPTY_SHA256
            }
            ".global-cache" => {
                record.kind == 2 && record.link_count == 1 && record.byte_length <= GLOBAL_CACHE_CAP
            }
            _ => false,
        };
        if !allowed_generated {
            return Err("independent Cargo home contains an unplanned entry".to_owned());
        }
    }
    Ok(())
}

fn insert_relative_parents(
    relative: &str,
    parents: &mut std::collections::BTreeSet<String>,
) -> BuildResult<()> {
    let path = Path::new(relative);
    if path.is_absolute()
        || path
            .components()
            .any(|component| !matches!(component, std::path::Component::Normal(_)))
    {
        return Err("cache-plan path is not canonical relative syntax".to_owned());
    }
    let mut accumulated = PathBuf::new();
    for component in path
        .parent()
        .ok_or_else(|| "cache-plan path has no parent".to_owned())?
        .components()
    {
        accumulated.push(component.as_os_str());
        parents.insert(
            accumulated
                .to_str()
                .ok_or_else(|| "cache-plan parent is not UTF-8".to_owned())?
                .replace('\\', "/"),
        );
    }
    Ok(())
}

fn read_bounded_regular_nofollow(path: &Path, cap: usize) -> BuildResult<Vec<u8>> {
    let parent = path
        .parent()
        .ok_or_else(|| "bounded file has no parent".to_owned())?;
    let canonical_parent = parent
        .canonicalize()
        .map_err(|_| "bounded file parent identity failed".to_owned())?;
    if canonical_parent != parent {
        return Err("bounded file parent is not canonical".to_owned());
    }
    let name = path
        .file_name()
        .and_then(|name| name.to_str())
        .ok_or_else(|| "bounded file name is not UTF-8".to_owned())?;
    let root = cap_std::fs::Dir::open_ambient_dir(parent, cap_std::ambient_authority())
        .map_err(|_| "bounded file parent open failed".to_owned())?;
    let bytes = read_cap_relative_file(&root, name, cap)?;
    if path
        .canonicalize()
        .map_err(|_| "bounded file path identity failed".to_owned())?
        != path
    {
        return Err("bounded file path is not canonical".to_owned());
    }
    Ok(bytes)
}

fn write_output_file(out_dir: &Path, name: &str, bytes: &[u8]) -> BuildResult<()> {
    use cap_fs_ext::{FollowSymlinks, OpenOptionsFollowExt};

    if bytes.is_empty() {
        return Err("approved build output is empty".to_owned());
    }
    let directory = cap_std::fs::Dir::open_ambient_dir(out_dir, cap_std::ambient_authority())
        .map_err(|_| "CLI build output capability open failed".to_owned())?;
    match directory.symlink_metadata(name) {
        Ok(metadata) if metadata.is_file() => {}
        Ok(_) => return Err("CLI build output is indirect or non-regular".to_owned()),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
        Err(_) => return Err("CLI build output inspection failed".to_owned()),
    }
    let mut options = cap_std::fs::OpenOptions::new();
    options
        .write(true)
        .create(true)
        .truncate(true)
        .follow(FollowSymlinks::No);
    let mut file = directory
        .open_with(name, &options)
        .map_err(|_| "CLI build output open failed".to_owned())?;
    file.write_all(bytes)
        .and_then(|_| file.flush())
        .and_then(|_| file.sync_all())
        .map_err(|_| "CLI build output write/sync failed".to_owned())?;
    drop(file);
    if read_cap_relative_file(&directory, name, bytes.len())? != bytes {
        return Err("CLI build output differs after write".to_owned());
    }
    Ok(())
}

fn canonical_directory(path: &Path, label: &str) -> BuildResult<PathBuf> {
    let canonical = path
        .canonicalize()
        .map_err(|_| format!("{label} identity validation failed"))?;
    if canonical != path || !canonical.is_dir() {
        return Err(format!("{label} is not a canonical directory"));
    }
    Ok(canonical)
}

fn create_canonical_directory(path: &Path, label: &str) -> BuildResult<PathBuf> {
    fs::create_dir_all(path).map_err(|_| format!("{label} creation failed"))?;
    canonical_directory(path, label)
}

fn hash_reader(reader: &mut impl Read) -> BuildResult<String> {
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let read = reader
            .read(&mut buffer)
            .map_err(|_| "stream hashing failed".to_owned())?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let digest = hasher.finalize();
    let mut encoded = String::with_capacity(digest.len() * 2);
    for byte in digest {
        encoded.push(char::from(HEX[usize::from(byte >> 4)]));
        encoded.push(char::from(HEX[usize::from(byte & 0x0f)]));
    }
    Ok(encoded)
}

fn hash_bytes(bytes: &[u8]) -> String {
    hash_reader(&mut Cursor::new(bytes)).expect("in-memory SHA-256 hashing cannot fail")
}

fn sync_directory(path: &Path) -> BuildResult<()> {
    let directory = File::open(path).map_err(|_| "directory sync open failed".to_owned())?;
    match directory.sync_all() {
        Ok(()) => Ok(()),
        #[cfg(windows)]
        Err(error) if matches!(error.raw_os_error(), Some(1 | 5 | 6)) => Ok(()),
        Err(_) => Err("directory sync failed".to_owned()),
    }
}
