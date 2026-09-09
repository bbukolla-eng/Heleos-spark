#![forbid(unsafe_code)]

//! Offline Foundation admission checks and the reproducible PDF guest launcher.
//! Normal verification deliberately never invokes Cargo: acceptance calls it while
//! Cargo owns the workspace build lock. Build orchestration is an explicit mode.

use std::collections::{BTreeMap, BTreeSet};
use std::fs::{self, File, OpenOptions};
use std::io::{Cursor, Read, Write};
use std::path::{Component, Path, PathBuf};
use std::process::{Command, ExitStatus, Stdio};
use std::sync::mpsc;
use std::thread;
use std::time::{Duration, Instant};

use heleos_core::{ApprovedPdfGuest, Sha256Digest};
use heleos_pdf_protocol::{
    ArtifactBuildInputsV1, ArtifactBuildPolicyV1, GuestBuildEvidenceV1, GuestResolutionCachePlanV1,
    GuestResolutionWorkspaceSpecV1, MAX_GUEST_WASM_BYTES, SourceFileV1, artifact_build_policy_v1,
    dependency_graph_sha256, exports_sha256, guest_resolution_cache_plan_sha256,
    guest_resolution_cache_plan_v1, guest_resolution_lock_sha256,
    guest_resolution_workspace_spec_v1, imports_sha256, normalize_dependency_graph_v1,
    source_tree_sha256, validate_guest_build_evidence_v1,
    validate_guest_resolution_lock_projection_v1, validate_guest_source_closure_v1,
    validate_pdf_guest_module_policy_v1, verify_no_physical_prefixes_v1,
};

type Result<T> = std::result::Result<T, Box<dyn std::error::Error>>;
const FILE_CAP: usize = 32 * 1024 * 1024;
const COMMAND_CAP: usize = 32 * 1024 * 1024;
const GUEST_PATH: &str = "target/wasm32-wasip1/release/heleos_pdf_guest.wasm";
const CACHE_PLAN_DIGEST: &str = "809419491262af6647fc246b0e9f556ae9deeba8570a80b87cac1029bc52580c";
const BUILD_EVIDENCE_DIGEST: &str =
    "f57df7089b3c6d1234f31d436fb514bc746b6aaa201c3b290a0811c3a6f62d83";
const LOCK: &[u8] = include_bytes!("../../../../Cargo.lock");
const ROOT_MANIFEST: &[u8] = include_bytes!("../../../../Cargo.toml");
const GUEST_MANIFEST: &[u8] = include_bytes!("../../../../artifacts/pdf-guest/manifest.toml");
const GOVERNANCE: &[(&str, &str, &[u8])] = &[
    (
        "governance/tools.toml",
        "tool",
        include_bytes!("../../../../governance/tools.toml"),
    ),
    (
        "governance/fixtures.toml",
        "fixture",
        include_bytes!("../../../../governance/fixtures.toml"),
    ),
    (
        "governance/sources.toml",
        "source",
        include_bytes!("../../../../governance/sources.toml"),
    ),
    (
        "governance/github-apps.toml",
        "github_app",
        include_bytes!("../../../../governance/github-apps.toml"),
    ),
];

fn main() {
    let result = run();
    match result {
        Ok(report) => println!("{report}"),
        Err(error) => {
            // A path or a check name is useful; rejected file contents and command
            // output may contain sensitive data and must never be printed.
            let mut report = BTreeMap::new();
            report.insert("schema", "heleos.provenance-report/v1");
            report.insert("status", "fail");
            let reason = error.to_string();
            report.insert("reason", reason.as_str());
            println!("{}", serde_jcs::to_string(&report).unwrap_or_default());
            std::process::exit(1);
        }
    }
}

fn run() -> Result<String> {
    let args = std::env::args().skip(1).collect::<Vec<_>>();
    if args.first().map(String::as_str) == Some("verify-native-suite") {
        return native_suite::run(&args);
    }
    if args.first().map(String::as_str) == Some("verify-secret-scan") {
        return secret_scan::run(&args);
    }
    if matches!(
        args.first().map(String::as_str),
        Some("build-sbom" | "verify-sbom")
    ) {
        return sbom::run(&args);
    }
    ensure(
        args.is_empty() || args == ["build-pdf-guest"],
        "unexpected verifier argument",
    )?;
    let root = std::env::current_dir()?.canonicalize()?;
    verify_frozen_file(&root, "Cargo.lock", LOCK)?;
    verify_frozen_file(&root, "Cargo.toml", ROOT_MANIFEST)?;
    verify_frozen_file(&root, "artifacts/pdf-guest/manifest.toml", GUEST_MANIFEST)?;
    let source = Source::read(&root)?;
    let mut report = verify_repository(&root, &source)?;
    if !args.is_empty() {
        build_pdf_guest(&root, &source)?;
        report.insert("builds_compared".to_owned(), "2".to_owned());
    }
    let wasm = read_regular(&root.join(GUEST_PATH), MAX_GUEST_WASM_BYTES)?;
    verify_guest(&wasm, &source)?;
    report.insert(
        "schema".to_owned(),
        "heleos.provenance-report/v1".to_owned(),
    );
    report.insert("status".to_owned(), "pass".to_owned());
    report.insert("workspace_lock_sha256".to_owned(), hash(LOCK)?);
    report.insert("guest_wasm_sha256".to_owned(), hash(&wasm)?);
    report.insert("guest_source_tree_sha256".to_owned(), source.digest()?);
    report.insert("guest_manifest_sha256".to_owned(), hash(GUEST_MANIFEST)?);
    Ok(serde_jcs::to_string(&report)?)
}

fn ensure(condition: bool, message: impl Into<String>) -> Result<()> {
    if condition {
        Ok(())
    } else {
        Err(message.into().into())
    }
}

fn hash(bytes: &[u8]) -> Result<String> {
    Ok(Sha256Digest::hash_reader(Cursor::new(bytes))?.to_string())
}

fn direct_path(path: &Path) -> Result<()> {
    ensure(path.is_absolute(), "an absolute path is required")?;
    let mut current = PathBuf::new();
    for component in path.components() {
        ensure(
            !matches!(component, Component::ParentDir | Component::CurDir),
            "indirect path component",
        )?;
        current.push(component);
        let metadata = fs::symlink_metadata(&current)?;
        ensure(
            !metadata.file_type().is_symlink(),
            "symlink in governed path",
        )?;
        #[cfg(windows)]
        {
            use std::os::windows::fs::MetadataExt;
            ensure(
                metadata.file_attributes() & 0x400 == 0,
                "reparse point in governed path",
            )?;
        }
    }
    Ok(())
}

fn read_regular(path: &Path, cap: usize) -> Result<Vec<u8>> {
    direct_path(path)?;
    let before = fs::symlink_metadata(path)?;
    ensure(
        before.is_file() && before.len() <= cap as u64,
        "governed input is not a bounded regular file",
    )?;
    let mut file = File::open(path)?;
    ensure(
        file.metadata()?.len() == before.len(),
        "governed input changed before read",
    )?;
    let mut bytes = Vec::new();
    (&mut file).take(cap as u64 + 1).read_to_end(&mut bytes)?;
    ensure(
        bytes.len() <= cap && bytes.len() as u64 == before.len(),
        "governed input changed during read",
    )?;
    ensure(
        file.metadata()?.modified()? == before.modified()?,
        "governed input changed during read",
    )?;
    direct_path(path)?;
    Ok(bytes)
}

fn verify_frozen_file(root: &Path, relative: &str, expected: &[u8]) -> Result<()> {
    ensure(
        read_regular(&root.join(relative), FILE_CAP)? == expected,
        format!("build snapshot drift: {relative}"),
    )
}

fn write_new(path: &Path, bytes: &[u8]) -> Result<()> {
    let parent = path.parent().ok_or("generated output has no parent")?;
    fs::create_dir_all(parent)?;
    direct_path(parent)?;
    let mut file = OpenOptions::new().write(true).create_new(true).open(path)?;
    file.write_all(bytes)?;
    file.sync_all()?;
    Ok(())
}

fn new_directory(path: &Path) -> Result<PathBuf> {
    fs::create_dir_all(path)?;
    let path = path.canonicalize()?;
    direct_path(&path)?;
    Ok(path)
}

struct Source {
    spec: GuestResolutionWorkspaceSpecV1,
    files: Vec<SourceFileV1>,
}

impl Source {
    fn read(root: &Path) -> Result<Self> {
        let spec = guest_resolution_workspace_spec_v1(
            ROOT_MANIFEST,
            &read_regular(&root.join("crates/heleos-pdf-guest/Cargo.toml"), FILE_CAP)?,
            &read_regular(
                &root.join("crates/heleos-pdf-protocol/Cargo.toml"),
                FILE_CAP,
            )?,
            &read_regular(
                &root.join("crates/heleos-test-fixtures/Cargo.toml"),
                FILE_CAP,
            )?,
        )?;
        let files = spec
            .files
            .iter()
            .map(|entry| {
                Ok(SourceFileV1 {
                    path: entry.relative_path.to_owned(),
                    content: read_regular(&root.join(entry.relative_path), FILE_CAP)?,
                })
            })
            .collect::<Result<Vec<_>>>()?;
        validate_guest_source_closure_v1(&files)?;
        let expected = spec
            .files
            .iter()
            .map(|entry| entry.relative_path.to_owned())
            .collect::<BTreeSet<_>>();
        let mut actual = BTreeSet::new();
        for name in [
            "heleos-pdf-guest",
            "heleos-pdf-protocol",
            "heleos-test-fixtures",
        ] {
            inventory(root, &root.join("crates").join(name), &mut actual)?;
        }
        ensure(
            actual == expected,
            "guest source inventory differs from the seven-file closure",
        )?;
        let source = Self { spec, files };
        ensure(
            source.digest()? == manifest_scalar("source_tree_sha256")?,
            "guest source tree differs from admitted manifest",
        )?;
        Ok(source)
    }

    fn digest(&self) -> Result<String> {
        let files = self
            .files
            .iter()
            .filter(|file| {
                self.spec
                    .files
                    .iter()
                    .any(|entry| entry.relative_path == file.path && entry.source_tree_member)
            })
            .cloned()
            .collect::<Vec<_>>();
        Ok(source_tree_sha256(&files)?)
    }

    fn materialize(&self, root: &Path) -> Result<()> {
        write_new(
            &root.join("Cargo.toml"),
            self.spec.root_manifest_utf8_lf.as_bytes(),
        )?;
        write_new(&root.join("Cargo.lock"), LOCK)?;
        for file in &self.files {
            write_new(&root.join(&file.path), &file.content)?;
        }
        Ok(())
    }

    fn recheck(&self, root: &Path, lock: &[u8]) -> Result<()> {
        ensure(
            read_regular(&root.join("Cargo.toml"), FILE_CAP)?
                == self.spec.root_manifest_utf8_lf.as_bytes(),
            "isolated manifest drift",
        )?;
        ensure(
            read_regular(&root.join("Cargo.lock"), FILE_CAP)? == lock,
            "isolated lock drift",
        )?;
        let mut expected = BTreeSet::from(["Cargo.toml".to_owned(), "Cargo.lock".to_owned()]);
        for file in &self.files {
            ensure(
                read_regular(&root.join(&file.path), FILE_CAP)? == file.content,
                "isolated guest source drift",
            )?;
            expected.insert(file.path.clone());
        }
        let mut actual = BTreeSet::new();
        inventory(root, root, &mut actual)?;
        ensure(actual == expected, "unexpected isolated guest file")
    }
}

fn inventory(root: &Path, directory: &Path, files: &mut BTreeSet<String>) -> Result<()> {
    direct_path(directory)?;
    for entry in fs::read_dir(directory)? {
        let entry = entry?;
        let path = entry.path();
        let kind = entry.file_type()?;
        ensure(!kind.is_symlink(), "indirect inventory entry")?;
        if kind.is_dir() {
            inventory(root, &path, files)?;
        } else {
            ensure(kind.is_file(), "special inventory entry")?;
            ensure(files.len() < 131_072, "inventory entry cap exceeded")?;
            files.insert(
                path.strip_prefix(root)?
                    .to_str()
                    .ok_or("non-UTF-8 inventory path")?
                    .replace('\\', "/"),
            );
        }
    }
    Ok(())
}

fn manifest_scalar(key: &str) -> Result<String> {
    let text = std::str::from_utf8(GUEST_MANIFEST)?;
    let prefix = format!("{key} = ");
    let values = text
        .lines()
        .filter_map(|line| line.strip_prefix(&prefix))
        .collect::<Vec<_>>();
    ensure(values.len() == 1, "ambiguous guest manifest scalar")?;
    let value = values[0];
    if let Some(value) = value.strip_prefix('"').and_then(|s| s.strip_suffix('"')) {
        ensure(
            !value.contains(['"', '\\']),
            "unsupported manifest scalar encoding",
        )?;
        Ok(value.to_owned())
    } else {
        ensure(
            value.bytes().all(|b| b.is_ascii_digit()),
            "unsupported manifest scalar",
        )?;
        Ok(value.to_owned())
    }
}

fn verify_guest(wasm: &[u8], source: &Source) -> Result<()> {
    ApprovedPdfGuest::load_tracked(wasm)?;
    let (imports, exports) = validate_pdf_guest_module_policy_v1(wasm)?;
    ensure(
        hash(wasm)? == manifest_scalar("wasm_sha256")?,
        "guest bytes differ from admission",
    )?;
    ensure(
        wasm.len().to_string() == manifest_scalar("wasm_byte_length")?,
        "guest size differs from admission",
    )?;
    ensure(
        source.digest()? == manifest_scalar("source_tree_sha256")?,
        "guest source differs from admission",
    )?;
    ensure(
        imports_sha256(&imports)? == manifest_scalar("imports_sha256")?,
        "guest imports differ from admission",
    )?;
    ensure(
        exports_sha256(&exports)? == manifest_scalar("exports_sha256")?,
        "guest exports differ from admission",
    )
}

fn verify_repository(root: &Path, source: &Source) -> Result<BTreeMap<String, String>> {
    let mut report = BTreeMap::new();
    let mut records = 0;
    for (path, kind, bytes) in GOVERNANCE {
        verify_frozen_file(root, path, bytes)?;
        records += verify_governance(std::str::from_utf8(bytes)?, kind)?;
        report.insert(format!("{kind}_registry_sha256"), hash(bytes)?);
    }
    verify_lock_and_manifests(root)?;
    verify_remotes(root)?;
    reject_configs(root)?;
    let output = bounded(
        Command::new("git").current_dir(root).args([
            "ls-files",
            "--cached",
            "--others",
            "--exclude-standard",
            "-z",
        ]),
        Duration::from_secs(30),
    )?;
    output.require_success("Git source inventory")?;
    let paths = std::str::from_utf8(&output.stdout)?
        .split('\0')
        .filter(|s| !s.is_empty())
        .collect::<BTreeSet<_>>();
    ensure(
        !paths.is_empty() && paths.len() <= 16_384,
        "invalid repository inventory size",
    )?;
    for relative in &paths {
        ensure(
            !relative.starts_with('/')
                && !Path::new(relative)
                    .components()
                    .any(|part| matches!(part, Component::ParentDir)),
            "invalid repository inventory path",
        )?;
        ensure(
            !relative.starts_with(".github/workflows/"),
            "workflow has no admitted Foundation authority",
        )?;
        let bytes = read_regular(&root.join(relative), FILE_CAP)?;
        ensure(
            !contains_secret(&bytes),
            format!("secret pattern in {relative}"),
        )?;
        if relative.ends_with(".rs") {
            let text = std::str::from_utf8(&bytes)?;
            let code = rust_code(text)?;
            if relative.starts_with("tests/verification/") {
                ensure(
                    !code.contains("#[ignore")
                        && !code.contains(",ignore")
                        && !code.contains("(ignore"),
                    format!("ignored acceptance test in {relative}"),
                )?;
            }
            if relative.starts_with("crates/") && relative.contains("/src/") {
                reject_network_client(&code, relative)?;
            }
        }
    }
    for path in [
        "tests/verification/tests/foundation_acceptance.rs",
        "tests/verification/tests/hostile_intake.rs",
    ] {
        ensure(
            paths.contains(path),
            "required acceptance target is missing",
        )?;
    }
    report.insert("files_scanned".to_owned(), paths.len().to_string());
    report.insert("governance_records".to_owned(), records.to_string());
    report.insert(
        "guest_source_files".to_owned(),
        source.files.len().to_string(),
    );
    report.insert("network_authority".to_owned(), "none".to_owned());
    report.insert(
        "transitive_socket_code".to_owned(),
        "Wasmtime/WASI contains upstream socket code; guest socket imports and host capability constructors are prohibited".to_owned(),
    );
    report.insert("workflow_authority".to_owned(), "none".to_owned());
    Ok(report)
}

fn verify_governance(text: &str, kind: &str) -> Result<usize> {
    // This parser accepts the registry's deliberately constrained top-level
    // string record format, not arbitrary TOML. Full file bytes are also bound
    // to the build snapshot; arrays of supplemental metadata are never authority.
    const REQUIRED: &[&str] = &[
        "name",
        "origin",
        "version_or_digest",
        "license_or_rights",
        "data_class",
        "owner",
        "permissions",
        "egress",
        "evaluation",
        "rollback",
    ];
    let marker = format!("[[{kind}]]");
    let mut records = 0;
    let mut fields = BTreeMap::<String, String>::new();
    let mut active = false;
    let mut array_depth = 0_i32;
    let mut multiline_string = false;
    for line in text.lines().chain(std::iter::once(marker.as_str())) {
        let line = line.trim();
        if line.is_empty() || line.starts_with('#') {
            continue;
        }
        if multiline_string {
            if line == "\"\"\"" {
                multiline_string = false;
            }
            continue;
        }
        if array_depth > 0 {
            array_depth += structural_brackets(line)?;
            ensure(array_depth >= 0, "invalid governance array")?;
            continue;
        }
        if line == marker {
            if active {
                for key in REQUIRED {
                    let value = fields.get(*key).ok_or("incomplete governance record")?;
                    ensure(!value.trim().is_empty(), "empty governance field")?;
                }
                let class = fields.get("data_class").ok_or("missing governance class")?;
                ensure(
                    ["PUBLIC", "INTERNAL", "PROJECT_CONFIDENTIAL", "SECRET"]
                        .contains(&class.as_str()),
                    "unknown governance data class",
                )?;
                if kind == "github_app" {
                    ensure(
                        fields.get("disposition").map(String::as_str)
                            == Some("owner_decision_required"),
                        "unrecognized GitHub App authority",
                    )?;
                    ensure(
                        fields
                            .get("egress")
                            .is_some_and(|s| s.starts_with("prohibited")),
                        "GitHub App egress is not disabled",
                    )?;
                } else {
                    let version = fields
                        .get("version_or_digest")
                        .ok_or("missing governed version")?
                        .to_ascii_lowercase();
                    ensure(
                        !["latest", "unversioned", "not inventoried", "tbd", "pending"]
                            .contains(&version.as_str()),
                        "unversioned admitted asset",
                    )?;
                }
                records += 1;
            }
            active = true;
            fields.clear();
            continue;
        }
        ensure(
            active && !line.starts_with('['),
            "unexpected governance record type",
        )?;
        let (key, value) = line
            .split_once('=')
            .ok_or("invalid governance assignment")?;
        let key = key.trim();
        let value = value.trim();
        ensure(
            key.bytes().all(|b| b.is_ascii_alphanumeric() || b == b'_'),
            "invalid governance key",
        )?;
        if value == "\"\"\"" {
            // Supplemental package inventories are frozen with the containing
            // registry bytes but are not scalar authority fields.
            multiline_string = true;
        } else if value.starts_with('[') {
            array_depth = structural_brackets(value)?;
            ensure(array_depth >= 0, "invalid governance array")?;
        } else {
            let value = value
                .strip_prefix('"')
                .and_then(|s| s.strip_suffix('"'))
                .ok_or("governance scalar must be a quoted string")?;
            ensure(
                fields.insert(key.to_owned(), value.to_owned()).is_none(),
                "duplicate governance field",
            )?;
        }
    }
    ensure(array_depth == 0, "unterminated governance array")?;
    ensure(
        !multiline_string,
        "unterminated governance multiline string",
    )?;
    ensure(
        records > 0 || kind == "source",
        "empty required governance registry",
    )?;
    Ok(records)
}

fn structural_brackets(line: &str) -> Result<i32> {
    let mut quoted = false;
    let mut escape = false;
    let mut depth = 0;
    for ch in line.chars() {
        if escape {
            escape = false;
            continue;
        }
        if quoted && ch == '\\' {
            escape = true;
            continue;
        }
        if ch == '"' {
            quoted = !quoted;
            continue;
        }
        if !quoted {
            if ch == '#' {
                break;
            }
            if ch == '[' {
                depth += 1;
            }
            if ch == ']' {
                depth -= 1;
            }
        }
    }
    ensure(!quoted && !escape, "unterminated governance string")?;
    Ok(depth)
}

fn verify_lock_and_manifests(root: &Path) -> Result<()> {
    let lock = std::str::from_utf8(LOCK)?;
    for record in lock.split("[[package]]").skip(1) {
        let lines = record.lines().map(str::trim).collect::<Vec<_>>();
        let source = lines.iter().find_map(|line| line.strip_prefix("source = "));
        if let Some(source) = source {
            ensure(
                source == "\"registry+https://github.com/rust-lang/crates.io-index\"",
                "mutable or unexpected dependency source",
            )?;
            let checksum = lines
                .iter()
                .find_map(|line| line.strip_prefix("checksum = \""))
                .and_then(|s| s.strip_suffix('"'))
                .ok_or("registry checksum missing")?;
            ensure(
                checksum.len() == 64
                    && checksum
                        .bytes()
                        .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
                "registry checksum malformed",
            )?;
        } else {
            ensure(
                lines
                    .iter()
                    .any(|line| line.starts_with("name = \"heleos-")),
                "unrecognized source-less dependency",
            )?;
        }
    }
    let mut manifests = BTreeSet::new();
    inventory(root, &root.join("crates"), &mut manifests)?;
    inventory(root, &root.join("tests/verification"), &mut manifests)?;
    manifests.insert("Cargo.toml".to_owned());
    for path in manifests.iter().filter(|path| path.ends_with("Cargo.toml")) {
        let bytes = read_regular(&root.join(path), FILE_CAP)?;
        let mut dependencies = false;
        for line in std::str::from_utf8(&bytes)?.lines() {
            let line = line.trim();
            if line.starts_with('#') {
                continue;
            }
            if line.starts_with('[') {
                dependencies = line.ends_with("dependencies]");
                let compact = line
                    .chars()
                    .filter(|ch| !ch.is_whitespace())
                    .collect::<String>();
                ensure(
                    !compact.contains("[patch.") && !compact.starts_with("[replace]"),
                    format!("unadmitted dependency override in {path}"),
                )?;
                continue;
            }
            if dependencies && let Some((name, value)) = line.split_once('=') {
                let raw_name = name.trim();
                let name = if let Some(name) = raw_name.strip_suffix(".workspace") {
                    ensure(
                        value.trim() == "true",
                        format!("invalid workspace dependency inheritance in {path}"),
                    )?;
                    name
                } else {
                    ensure(
                        !raw_name.contains('.'),
                        format!("unsupported dotted dependency key in {path}"),
                    )?;
                    raw_name
                };
                ensure(
                    ![
                        "reqwest", "ureq", "hyper", "curl", "socket2", "surf", "isahc",
                    ]
                    .contains(&name),
                    format!("direct network client dependency in {path}"),
                )?;
                if !value.contains("path =") {
                    let tools = std::str::from_utf8(GOVERNANCE[0].2)?;
                    ensure(
                        tools
                            .lines()
                            .filter_map(|entry| entry.strip_prefix("name = \""))
                            .any(|entry| {
                                entry.strip_suffix('"').is_some_and(|entry| {
                                    entry == name || entry.starts_with(&format!("{name} "))
                                })
                            }),
                        format!("direct dependency lacks a governed record: {name}"),
                    )?;
                }
            }
            let compact = line
                .chars()
                .filter(|ch| !ch.is_whitespace())
                .collect::<String>();
            ensure(
                !compact.contains("git=")
                    && !compact.contains("branch=")
                    && !compact.contains("tag=")
                    && !compact.contains("registry=")
                    && !compact.contains("[patch.")
                    && !compact.starts_with("[replace]"),
                format!("unadmitted dependency override in {path}"),
            )?;
        }
    }
    Ok(())
}

fn verify_remotes(root: &Path) -> Result<()> {
    let output = bounded(
        Command::new("git").current_dir(root).args(["remote", "-v"]),
        Duration::from_secs(30),
    )?;
    output.require_success("Git remote inventory")?;
    for line in std::str::from_utf8(&output.stdout)?.lines() {
        let fields = line.split_whitespace().collect::<Vec<_>>();
        ensure(
            fields.len() == 3
                && fields[0] == "origin"
                && [
                    "https://github.com/bbukolla-eng/Heleos-spark.git",
                    "git@github.com:bbukolla-eng/Heleos-spark.git",
                    "ssh://git@github.com/bbukolla-eng/Heleos-spark.git",
                ]
                .contains(&fields[1])
                && ["(fetch)", "(push)"].contains(&fields[2]),
            "unexpected Git remote",
        )?;
    }
    Ok(())
}

fn reject_configs(root: &Path) -> Result<()> {
    for parent in root.ancestors() {
        for name in [".cargo/config", ".cargo/config.toml"] {
            ensure(
                !parent.join(name).try_exists()?,
                "ambient Cargo configuration is not admitted",
            )?;
        }
    }
    Ok(())
}

fn contains_secret(bytes: &[u8]) -> bool {
    // Search actual credential shapes, not words such as "token" in source.
    let text = String::from_utf8_lossy(bytes);
    for header in [
        "-----BEGIN PRIVATE KEY-----",
        "-----BEGIN RSA PRIVATE KEY-----",
        "-----BEGIN OPENSSH PRIVATE KEY-----",
        "-----BEGIN EC PRIVATE KEY-----",
    ] {
        for (offset, _) in text.match_indices(header) {
            let tail = &text[offset + header.len()..];
            // Header-only rejection fixtures have no actual PEM payload.
            if let Some(payload) = tail.strip_prefix('\n') {
                let length = payload
                    .bytes()
                    .take_while(|b| b.is_ascii_alphanumeric() || *b == b'+' || *b == b'/')
                    .count();
                if length >= 32 {
                    return true;
                }
            }
        }
    }
    for (prefix, minimum) in [
        ("ghp_", 36),
        ("github_pat_", 60),
        ("sk-proj-", 40),
        ("xai-", 40),
        ("AKIA", 16),
        ("AGE-SECRET-KEY-", 59),
    ] {
        for (_, tail) in text
            .match_indices(prefix)
            .map(|(index, _)| (index, &text[index + prefix.len()..]))
        {
            let count = tail
                .bytes()
                .take_while(|b| b.is_ascii_alphanumeric() || *b == b'_' || *b == b'-')
                .count();
            if count >= minimum {
                return true;
            }
        }
    }
    false
}

fn reject_network_client(code: &str, path: &str) -> Result<()> {
    for needle in [
        "std::net",
        "tokio::net",
        "cap_std::net",
        "reqwest::",
        "ureq::",
        "hyper::",
        "curl::",
        "socket2::",
        "TcpStream",
        "TcpListener",
        "UdpSocket",
        "ToSocketAddrs",
        ".inherit_network(",
        ".socket_addr_check(",
        ".allow_tcp(",
        ".allow_udp(",
        ".allow_ip_name_lookup(",
    ] {
        ensure(
            !code.contains(needle),
            format!("network capability in {path}"),
        )?;
    }
    Ok(())
}

fn rust_code(text: &str) -> Result<String> {
    // Retain code tokens, removing comments and string literals so hostile PDF
    // fixtures, protocol names, and documentation cannot impersonate Rust calls.
    let bytes = text.as_bytes();
    let mut result = String::new();
    let mut i = 0;
    while i < bytes.len() {
        if bytes[i..].starts_with(b"//") {
            i += 2;
            while i < bytes.len() && bytes[i] != b'\n' {
                i += 1;
            }
        } else if bytes[i..].starts_with(b"/*") {
            let mut depth = 1;
            i += 2;
            while i < bytes.len() && depth > 0 {
                if bytes[i..].starts_with(b"/*") {
                    depth += 1;
                    i += 2;
                } else if bytes[i..].starts_with(b"*/") {
                    depth -= 1;
                    i += 2;
                } else {
                    i += 1;
                }
            }
            ensure(depth == 0, "unterminated Rust comment")?;
        } else if bytes[i] == b'r' && bytes.get(i + 1).is_some_and(|b| *b == b'#' || *b == b'"') {
            let mut end = i + 1;
            while bytes.get(end) == Some(&b'#') {
                end += 1;
            }
            if bytes.get(end) == Some(&b'"') {
                let hashes = end - i - 1;
                let closing = format!("\"{}", "#".repeat(hashes));
                let rest = &text[end + 1..];
                let offset = rest.find(&closing).ok_or("unterminated Rust raw string")?;
                i = end + 1 + offset + closing.len();
            } else {
                result.push('r');
                i += 1;
            }
        } else if bytes[i] == b'"' {
            i += 1;
            let mut closed = false;
            while i < bytes.len() {
                if bytes[i] == b'\\' {
                    i += 2;
                } else if bytes[i] == b'"' {
                    i += 1;
                    closed = true;
                    break;
                } else {
                    i += 1;
                }
            }
            ensure(closed, "unterminated Rust string")?;
        } else if bytes[i] == b'\'' && bytes.get(i + 2) == Some(&b'\'') {
            i += 3;
        } else if bytes[i..].starts_with(b"'\\") {
            i += 2;
            while i < bytes.len() && bytes[i] != b'\'' {
                i += 1;
            }
            ensure(i < bytes.len(), "unterminated Rust character")?;
            i += 1;
        } else {
            if !bytes[i].is_ascii_whitespace() {
                result.push(char::from(bytes[i]));
            }
            i += 1;
        }
    }
    Ok(result)
}

struct Output {
    status: ExitStatus,
    stdout: Vec<u8>,
    stderr: Vec<u8>,
}

impl Output {
    fn require_success(&self, context: &str) -> Result<()> {
        ensure(
            self.status.success(),
            format!(
                "{context} failed (code {:?}; stderr sha256 {})",
                self.status.code(),
                hash(&self.stderr)?
            ),
        )
    }
}

fn bounded(command: &mut Command, timeout: Duration) -> Result<Output> {
    command
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    let mut child = command.spawn()?;
    let stdout = child.stdout.take().ok_or("missing command stdout")?;
    let stderr = child.stderr.take().ok_or("missing command stderr")?;
    let (sender, receiver) = mpsc::channel();
    for (index, mut stream) in [
        (0, Box::new(stdout) as Box<dyn Read + Send>),
        (1, Box::new(stderr) as Box<dyn Read + Send>),
    ] {
        let sender = sender.clone();
        thread::spawn(move || {
            let mut bytes = Vec::new();
            let result = (&mut stream)
                .take(COMMAND_CAP as u64 + 1)
                .read_to_end(&mut bytes);
            let _ = sender.send((index, result.map(|_| bytes)));
        });
    }
    drop(sender);
    let deadline = Instant::now() + timeout;
    let mut streams: [Option<Vec<u8>>; 2] = [None, None];
    let mut status = None;
    loop {
        while let Ok((index, bytes)) = receiver.try_recv() {
            let bytes = bytes?;
            if bytes.len() > COMMAND_CAP {
                let _ = child.kill();
                let _ = child.wait();
                return Err("command output cap exceeded".into());
            }
            streams[index] = Some(bytes);
        }
        if status.is_none() {
            status = child.try_wait()?;
        }
        if let Some(status) = status
            && streams.iter().all(Option::is_some)
        {
            return Ok(Output {
                status,
                stdout: streams[0].take().unwrap_or_default(),
                stderr: streams[1].take().unwrap_or_default(),
            });
        }
        if Instant::now() >= deadline {
            let _ = child.kill();
            let _ = child.wait();
            return Err("command deadline exceeded".into());
        }
        thread::sleep(Duration::from_millis(10));
    }
}

struct Toolchain {
    cargo: PathBuf,
    cargo_identity: PathBuf,
    rustup: PathBuf,
    sysroot: PathBuf,
    seed_cache: PathBuf,
    system_root: Option<PathBuf>,
}

impl Toolchain {
    fn discover() -> Result<Self> {
        for (name, _) in std::env::vars_os() {
            let name = name.to_string_lossy();
            ensure(
                ![
                    "RUSTFLAGS",
                    "CARGO_ENCODED_RUSTFLAGS",
                    "RUSTC",
                    "RUSTC_WRAPPER",
                    "RUSTC_WORKSPACE_WRAPPER",
                    "RUSTDOC",
                    "CARGO_BUILD_RUSTC",
                    "CARGO_BUILD_RUSTFLAGS",
                ]
                .contains(&name.as_ref())
                    && !name.starts_with("CARGO_TARGET_")
                    && !name.starts_with("CARGO_PROFILE_"),
                "inherited build override is not admitted",
            )?;
        }
        let user = std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" })
            .ok_or("user directory unavailable")?;
        let seed_cache = PathBuf::from(
            std::env::var_os("CARGO_HOME")
                .unwrap_or_else(|| PathBuf::from(&user).join(".cargo").into_os_string()),
        )
        .canonicalize()?;
        let rustup = PathBuf::from(
            std::env::var_os("RUSTUP_HOME")
                .unwrap_or_else(|| PathBuf::from(&user).join(".rustup").into_os_string()),
        )
        .canonicalize()?;
        let suffix = if cfg!(windows) { ".exe" } else { "" };
        let cargo = seed_cache.join("bin").join(format!("cargo{suffix}"));
        let rustc = seed_cache.join("bin").join(format!("rustc{suffix}"));
        let cargo_identity = cargo.canonicalize()?;
        let output = bounded(
            Command::new(&rustc)
                .args(["+1.96.1", "--print", "sysroot"])
                .env("RUSTUP_HOME", &rustup)
                .env("CARGO_NET_OFFLINE", "true"),
            Duration::from_secs(30),
        )?;
        output.require_success("pinned Rust sysroot discovery")?;
        let sysroot = PathBuf::from(std::str::from_utf8(&output.stdout)?.trim()).canonicalize()?;
        let system_root = if cfg!(windows) {
            Some(
                PathBuf::from(
                    std::env::var_os("SystemRoot").ok_or("Windows SystemRoot unavailable")?,
                )
                .canonicalize()?,
            )
        } else {
            None
        };
        for name in ["config", "config.toml"] {
            ensure(
                !seed_cache.join(name).try_exists()?,
                "seed Cargo configuration is not admitted",
            )?;
        }
        direct_path(&seed_cache)?;
        direct_path(&rustup)?;
        direct_path(&sysroot)?;
        Ok(Self {
            cargo,
            cargo_identity,
            rustup,
            sysroot,
            seed_cache,
            system_root,
        })
    }

    fn metadata(&self, root: &Path, cache: &Path, target: &Path, locked: bool) -> Result<Vec<u8>> {
        let temp = new_directory(&target.join(".heleos-tmp"))?;
        let mut command = Command::new(&self.cargo);
        command.current_dir(root).args([
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
        let mut paths = vec![self.sysroot.join("bin")];
        if let Some(system) = &self.system_root {
            paths.extend([system.join("System32"), system.clone()]);
        } else {
            paths.extend([PathBuf::from("/usr/bin"), PathBuf::from("/bin")]);
        }
        command
            .env("PATH", std::env::join_paths(paths)?)
            .env("CARGO_HOME", cache)
            .env("CARGO_TARGET_DIR", target)
            .env("CARGO_NET_OFFLINE", "true")
            .env("CARGO_INCREMENTAL", "0")
            .env("CARGO_TERM_COLOR", "never")
            .env("RUSTUP_HOME", &self.rustup)
            .env("RUSTUP_TOOLCHAIN", "1.96.1")
            .env("TMP", &temp)
            .env("TEMP", &temp)
            .env("TMPDIR", &temp)
            .env("LANG", "C")
            .env("LC_ALL", "C")
            .env("TZ", "UTC")
            .env("SOURCE_DATE_EPOCH", "0");
        if let Some(system) = &self.system_root {
            command.env("SystemRoot", system).env("WINDIR", system);
        }
        let output = bounded(&mut command, Duration::from_secs(120))?;
        output.require_success("offline metadata")?;
        Ok(output.stdout)
    }
}

struct BuiltGuest {
    wasm: Vec<u8>,
    lock: Vec<u8>,
    evidence: GuestBuildEvidenceV1,
    policy: ArtifactBuildPolicyV1,
}

fn build_pdf_guest(root: &Path, source: &Source) -> Result<()> {
    let tools = Toolchain::discover()?;
    let scratch = tempfile::Builder::new()
        .prefix("heleos-provenance-")
        .tempdir()?;
    let scratch_root = scratch.path().canonicalize()?;
    ensure(
        !scratch_root.starts_with(root),
        "guest scratch must be outside the repository",
    )?;
    let seed = new_directory(&scratch_root.join("seed-resolution"))?;
    source.materialize(&seed)?;
    let seed_target = new_directory(&scratch_root.join("seed-target"))?;
    // Only this disposable resolution copy is allowed an unlocked offline
    // projection. The workspace Cargo.lock remains byte-identical throughout.
    tools.metadata(&seed, &tools.seed_cache, &seed_target, false)?;
    let pruned_lock = read_regular(&seed.join("Cargo.lock"), FILE_CAP)?;
    validate_guest_resolution_lock_projection_v1(LOCK, &pruned_lock)?;
    source.recheck(&seed, &pruned_lock)?;
    ensure(
        guest_resolution_lock_sha256(&pruned_lock)?
            == manifest_scalar("guest_resolution_lock_sha256")?,
        "pruned guest lock differs from admission",
    )?;
    let plan = guest_resolution_cache_plan_v1(LOCK, &pruned_lock)?;
    ensure(
        plan.resolution_archives.len() == 67
            && guest_resolution_cache_plan_sha256(&plan)? == CACHE_PLAN_DIGEST,
        "guest cache plan differs from admitted golden",
    )?;
    let first = build_one(
        &scratch_root.join("first/short"),
        source,
        &tools,
        &plan,
        &pruned_lock,
        root,
    )?;
    let second = build_one(
        &scratch_root.join("second/distinct/longer/path"),
        source,
        &tools,
        &plan,
        &pruned_lock,
        root,
    )?;
    ensure(
        first.wasm == second.wasm && first.lock == second.lock && first.evidence == second.evidence,
        "independent guest builds differ",
    )?;
    verify_guest(&first.wasm, source)?;
    verify_no_physical_prefixes_v1(&first.wasm, &first.policy)?;
    verify_no_physical_prefixes_v1(&first.wasm, &second.policy)?;
    verify_frozen_file(root, "Cargo.lock", LOCK)?;
    let destination = root.join(GUEST_PATH);
    fs::create_dir_all(destination.parent().ok_or("missing guest output parent")?)?;
    direct_path(destination.parent().ok_or("missing guest output parent")?)?;
    if destination.try_exists()? {
        ensure(
            read_regular(&destination, MAX_GUEST_WASM_BYTES)? == first.wasm,
            "refusing to overwrite a different guest output",
        )?;
    } else {
        let mut staged = tempfile::NamedTempFile::new_in(
            destination.parent().ok_or("missing guest output parent")?,
        )?;
        staged.write_all(&first.wasm)?;
        staged.as_file().sync_all()?;
        staged
            .persist_noclobber(&destination)
            .map_err(|error| error.error)?;
    }
    ensure(
        read_regular(&destination, MAX_GUEST_WASM_BYTES)? == first.wasm,
        "published guest verification failed",
    )?;
    scratch.close()?;
    Ok(())
}

fn copy_cache(seed: &Path, destination: &Path, plan: &GuestResolutionCachePlanV1) -> Result<()> {
    let config = &plan.sparse_config_cargo_home_relative_path;
    write_new(
        &destination.join(config),
        &read_regular(&seed.join(config), FILE_CAP)?,
    )?;
    for entry in &plan.sparse_index_entries {
        let path = &entry.cargo_home_relative_path;
        write_new(
            &destination.join(path),
            &read_regular(&seed.join(path), FILE_CAP)?,
        )?;
    }
    for archive in &plan.resolution_archives {
        let bytes = read_regular(&seed.join(&archive.cargo_home_relative_path), FILE_CAP)?;
        ensure(
            hash(&bytes)? == archive.sha256,
            "registry archive checksum mismatch",
        )?;
        write_new(&destination.join(&archive.cargo_home_relative_path), &bytes)?;
    }
    Ok(())
}

fn cache_snapshot(root: &Path) -> Result<BTreeMap<String, String>> {
    let mut files = BTreeSet::new();
    inventory(root, &root.join("registry"), &mut files)?;
    let mut result = BTreeMap::new();
    let mut total = 0_usize;
    for relative in files {
        let bytes = read_regular(&root.join(&relative), FILE_CAP)?;
        total = total
            .checked_add(bytes.len())
            .ok_or("cache size overflow")?;
        ensure(total <= 1024 * 1024 * 1024, "guest cache exceeds one GiB")?;
        result.insert(relative, hash(&bytes)?);
    }
    Ok(result)
}

fn build_one(
    base: &Path,
    source: &Source,
    tools: &Toolchain,
    plan: &GuestResolutionCachePlanV1,
    expected_lock: &[u8],
    production_root: &Path,
) -> Result<BuiltGuest> {
    let base = new_directory(base)?;
    let root = new_directory(&base.join("workspace"))?;
    let cache = new_directory(&base.join("cargo-cache"))?;
    let metadata_target = new_directory(&base.join("metadata-target"))?;
    let full_root = new_directory(&base.join("full-workspace"))?;
    let full_target = new_directory(&base.join("full-metadata-target"))?;
    copy_full_workspace(production_root, &full_root)?;
    let seed_before = planned_seed_snapshot(&tools.seed_cache, plan)?;
    let full_metadata = tools.metadata(&full_root, &tools.seed_cache, &full_target, true)?;
    ensure(
        planned_seed_snapshot(&tools.seed_cache, plan)? == seed_before,
        "supplemental metadata mutated governed seed cache inputs",
    )?;
    verify_frozen_file(&full_root, "Cargo.lock", LOCK)?;
    let target = new_directory(&base.join("build-target"))?;
    new_directory(&target.join(".heleos-tmp"))?;
    source.materialize(&root)?;
    copy_cache(&tools.seed_cache, &cache, plan)?;
    // Each independent root proves the same deletion-only lock projection with
    // its own exact cache plan; no registry source tree is copied from the host.
    tools.metadata(&root, &cache, &metadata_target, false)?;
    let lock = read_regular(&root.join("Cargo.lock"), FILE_CAP)?;
    validate_guest_resolution_lock_projection_v1(LOCK, &lock)?;
    ensure(
        lock == expected_lock,
        "isolated guest lock projection differs",
    )?;
    let metadata = tools.metadata(&root, &cache, &metadata_target, true)?;
    source.recheck(&root, &lock)?;
    let graph =
        normalize_dependency_graph_v1(&metadata, &full_metadata, LOCK, &lock, "heleos-pdf-guest")?;
    ensure(
        dependency_graph_sha256(&graph.records)? == manifest_scalar("dependency_graph_sha256")?,
        "guest dependency graph differs from admission",
    )?;
    ensure(
        graph.records.len() == 58 && graph.registry_roots.len() == 56,
        "guest active graph count differs from admitted golden",
    )?;
    for registry in &graph.registry_roots {
        let archive = plan
            .resolution_archives
            .iter()
            .find(|entry| entry.normalized_dependency_id == registry.normalized_dependency_id)
            .ok_or("active registry package absent from cache plan")?;
        let expected = cache.join(&archive.unpacked_source_cargo_home_relative_path);
        direct_path(&expected)?;
        ensure(
            registry.physical_root == expected && expected.canonicalize()? == expected,
            "registry source escaped isolated cache",
        )?;
    }
    let snapshot = cache_snapshot(&cache)?;
    let policy = artifact_build_policy_v1(&ArtifactBuildInputsV1 {
        cargo_proxy_invocation: tools.cargo.clone(),
        cargo_resolved_identity: tools.cargo_identity.clone(),
        workspace_root: root.clone(),
        cargo_home: cache.clone(),
        target_root: target.clone(),
        rustc_sysroot: tools.sysroot.clone(),
        rustup_home: tools.rustup.clone(),
        registry_roots: graph.registry_roots,
        system_root: tools.system_root.clone(),
    })?;
    ensure(
        policy.env_clear(),
        "artifact policy must clear inherited environment",
    )?;
    let mut command = Command::new(policy.cargo_proxy_invocation());
    command
        .current_dir(policy.working_directory())
        .args(policy.argv())
        .env_clear();
    for entry in policy.environment() {
        command.env(entry.name(), entry.value());
    }
    let output = bounded(&mut command, Duration::from_secs(600))?;
    output.require_success("isolated PDF guest build")?;
    ensure(
        output.stderr.is_empty(),
        "isolated PDF guest build emitted diagnostics",
    )?;
    let evidence = validate_guest_build_evidence_v1(
        &output.stdout,
        &metadata,
        &full_metadata,
        LOCK,
        &lock,
        "heleos-pdf-guest",
        &policy,
    )?;
    ensure(
        evidence.guest_resolution_lock_sha256 == guest_resolution_lock_sha256(&lock)?
            && evidence.guest_resolution_cache_plan_sha256
                == guest_resolution_cache_plan_sha256(plan)?,
        "guest build resolution evidence differs",
    )?;
    ensure(
        evidence.compiler_units.len() == 68 && evidence.build_scripts.len() == 9,
        "guest compiler/build-script evidence count differs from admitted golden",
    )?;
    let mut framed = b"heleos-pdf-guest-build-evidence-v1\0".to_vec();
    framed.extend_from_slice(&serde_jcs::to_vec(&evidence)?);
    ensure(
        hash(&framed)? == BUILD_EVIDENCE_DIGEST,
        "guest build evidence differs from admitted golden",
    )?;
    ensure(
        cache_snapshot(&cache)? == snapshot,
        "guest build mutated its governed registry cache",
    )?;
    source.recheck(&root, &lock)?;
    let wasm = read_regular(
        &target.join("wasm32-wasip1/release/heleos_pdf_guest.wasm"),
        MAX_GUEST_WASM_BYTES,
    )?;
    verify_guest(&wasm, source)?;
    verify_no_physical_prefixes_v1(&wasm, &policy)?;
    Ok(BuiltGuest {
        wasm,
        lock,
        evidence,
        policy,
    })
}

fn copy_full_workspace(source: &Path, destination: &Path) -> Result<()> {
    let mut files = BTreeSet::from(["Cargo.toml".to_owned(), "Cargo.lock".to_owned()]);
    inventory(source, &source.join("crates"), &mut files)?;
    inventory(source, &source.join("tests/verification"), &mut files)?;
    for path in files {
        write_new(
            &destination.join(&path),
            &read_regular(&source.join(&path), FILE_CAP)?,
        )?;
    }
    Ok(())
}

fn planned_seed_snapshot(
    root: &Path,
    plan: &GuestResolutionCachePlanV1,
) -> Result<BTreeMap<String, String>> {
    let mut paths = BTreeSet::from([plan.sparse_config_cargo_home_relative_path.clone()]);
    paths.extend(
        plan.sparse_index_entries
            .iter()
            .map(|entry| entry.cargo_home_relative_path.clone()),
    );
    for archive in &plan.resolution_archives {
        paths.insert(archive.cargo_home_relative_path.clone());
        let source = root.join(&archive.unpacked_source_cargo_home_relative_path);
        if source.try_exists()? {
            inventory(root, &source, &mut paths)?;
        }
    }
    paths
        .into_iter()
        .map(|path| {
            let digest = hash(&read_regular(&root.join(&path), FILE_CAP)?)?;
            Ok((path, digest))
        })
        .collect()
}

mod native_suite {
    use super::*;
    use serde_json::{Value, json};

    const WINDOWS_STORE_SENTINEL: &str =
        "store::tests::windows_checked_close_releases_handles_in_three_real_rename_phases";

    struct SuiteSpec {
        id: &'static str,
        selectors: &'static [&'static str],
    }

    const SUITES: [SuiteSpec; 7] = [
        SuiteSpec {
            id: "core-backup-restore",
            selectors: &["-p", "heleos-core", "--test", "backup_restore"],
        },
        SuiteSpec {
            id: "core-store",
            selectors: &["-p", "heleos-core", "--lib", "store::tests"],
        },
        SuiteSpec {
            id: "core-backup",
            selectors: &["-p", "heleos-core", "--lib", "backup::tests"],
        },
        SuiteSpec {
            id: "platform-fs",
            selectors: &["-p", "heleos-platform-fs", "--lib"],
        },
        SuiteSpec {
            id: "cli-unit",
            selectors: &["-p", "heleos-cli", "--bin", "heleos"],
        },
        SuiteSpec {
            id: "cli-integration",
            selectors: &["-p", "heleos-cli", "--test", "cli"],
        },
        SuiteSpec {
            id: "workspace-all",
            selectors: &["--workspace", "--all-targets", "--all-features"],
        },
    ];

    impl SuiteSpec {
        fn validate(&self, suite: &Value) -> Result<()> {
            ensure(suite.is_object(), "native-suite suite must be an object")?;
            ensure(
                suite["id"].as_str() == Some(self.id),
                "native-suite ordered suite identity mismatch",
            )?;
            let mut run_argv = vec!["cargo", "+1.96.1", "test", "--frozen"];
            run_argv.extend_from_slice(self.selectors);
            let mut list_argv = run_argv.clone();
            list_argv.extend_from_slice(&["--", "--list"]);
            ensure(
                suite["run_argv"] == json!(run_argv) && suite["list_argv"] == json!(list_argv),
                "native-suite exact command mismatch",
            )?;
            ensure(
                suite["list_path"] == format!("{}.list.txt", self.id)
                    && suite["run_path"] == format!("{}.run.txt", self.id),
                "native-suite exact transcript basename mismatch",
            )
        }
    }

    fn string(value: &Value) -> Result<&str> {
        value
            .as_str()
            .ok_or_else(|| "native-suite required string missing".into())
    }

    fn transcript(directory: &Path, value: &Value) -> Result<Vec<u8>> {
        let relative = string(value)?;
        ensure(
            !relative.is_empty()
                && !relative.contains(['\\', ':'])
                && relative
                    .split('/')
                    .all(|part| !part.is_empty() && part != "." && part != ".."),
            "native-suite noncanonical transcript path",
        )?;
        read_regular(&directory.join(relative), FILE_CAP)
    }

    fn count(field: &str, suffixes: &[&str]) -> Result<usize> {
        let digits = suffixes
            .iter()
            .find_map(|suffix| field.strip_suffix(suffix))
            .ok_or("native-suite malformed transcript count")?;
        ensure(
            !digits.is_empty() && digits.bytes().all(|byte| byte.is_ascii_digit()),
            "native-suite malformed transcript count",
        )?;
        digits
            .parse()
            .map_err(|_| "native-suite transcript count overflow".into())
    }

    fn add_name<'a>(names: &mut BTreeMap<&'a str, usize>, name: &'a str) -> Result<()> {
        ensure(
            !name.is_empty()
                && !name
                    .chars()
                    .any(|character| character.is_whitespace() || character.is_control()),
            "native-suite malformed test name",
        )?;
        *names.entry(name).or_default() += 1;
        Ok(())
    }

    fn listed_tests(text: &str) -> Result<BTreeMap<&str, usize>> {
        let mut names = BTreeMap::new();
        let mut pending = 0;
        let mut summaries = 0;
        for line in text.lines() {
            if let Some(name) = line.strip_suffix(": test") {
                add_name(&mut names, name)?;
                pending += 1;
            } else if line.contains(": benchmark") || line.contains(": test") {
                return Err("native-suite unsupported listing record".into());
            } else if line.as_bytes().first().is_some_and(u8::is_ascii_digit) {
                let (tests, benchmarks) = line
                    .split_once(", ")
                    .ok_or("native-suite malformed list summary")?;
                ensure(
                    count(tests, &[" test", " tests"])? == pending
                        && count(benchmarks, &[" benchmark", " benchmarks"])? == 0,
                    "native-suite list summary does not match test records",
                )?;
                pending = 0;
                summaries += 1;
            }
        }
        ensure(
            summaries > 0 && pending == 0,
            "native-suite missing list summary",
        )?;
        Ok(names)
    }

    fn passed_tests(text: &str) -> Result<BTreeMap<&str, usize>> {
        let mut names = BTreeMap::new();
        let mut running = None;
        let mut passed = 0;
        let mut summaries = 0;
        for line in text.lines() {
            if let Some(total) = line.strip_prefix("running ") {
                ensure(running.is_none(), "native-suite missing run summary")?;
                running = Some(count(total, &[" test", " tests"])?);
                passed = 0;
            } else if let Some(result) = line.strip_prefix("test result:") {
                let result = result
                    .strip_prefix(" ok. ")
                    .ok_or("native-suite run result is not successful")?;
                let fields = result.split("; ").collect::<Vec<_>>();
                ensure(fields.len() == 6, "native-suite malformed run summary")?;
                ensure(
                    running == Some(passed)
                        && count(fields[0], &[" passed"])? == passed
                        && count(fields[1], &[" failed"])? == 0
                        && count(fields[2], &[" ignored"])? == 0
                        && count(fields[3], &[" measured"])? == 0,
                    "native-suite run summary does not match successful test records",
                )?;
                // Exact test filters legitimately exclude other library tests.
                count(fields[4], &[" filtered out"])?;
                let elapsed = fields[5]
                    .strip_prefix("finished in ")
                    .and_then(|value| value.strip_suffix('s'))
                    .and_then(|value| value.parse::<f64>().ok())
                    .ok_or("native-suite malformed run duration")?;
                ensure(
                    elapsed.is_finite() && elapsed >= 0.0,
                    "native-suite malformed run duration",
                )?;
                running = None;
                summaries += 1;
            } else if let Some(record) = line.strip_prefix("test ") {
                ensure(
                    running.is_some(),
                    "native-suite test record outside a running harness",
                )?;
                // Every status record must end in exact `ok`; failures, ignored
                // reasons, and unknown statuses must not disappear from totals.
                let name = record
                    .strip_suffix(" ... ok")
                    .ok_or("native-suite test status is not successful")?;
                add_name(&mut names, name)?;
                passed += 1;
            }
        }
        ensure(
            summaries > 0 && running.is_none(),
            "native-suite missing run summary",
        )?;
        Ok(names)
    }

    pub(super) fn run(args: &[String]) -> Result<String> {
        ensure(args.len() == 2, "native-suite requires one manifest path")?;
        let manifest_path = Path::new(&args[1]);
        let manifest_bytes = read_regular(manifest_path, FILE_CAP)?;
        let manifest: Value = serde_json::from_slice(&manifest_bytes)
            .map_err(|_| "native-suite malformed manifest JSON")?;
        ensure(
            manifest["schema"] == "heleos.native-suite-transcripts/v1"
                && manifest["platform"] == "windows-x86_64"
                && manifest["filesystem"] == "NTFS",
            "native-suite manifest identity mismatch",
        )?;
        let candidate = string(&manifest["candidate_sha"])?;
        ensure(
            candidate.len() == 40
                && candidate
                    .bytes()
                    .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte)),
            "native-suite candidate must be a lowercase Git SHA",
        )?;
        let root = std::env::current_dir()?.canonicalize()?;
        let head = bounded(
            Command::new("git")
                .args(["rev-parse", "HEAD"])
                .current_dir(&root),
            Duration::from_secs(30),
        )?;
        head.require_success("native-suite candidate lookup")?;
        ensure(
            std::str::from_utf8(&head.stdout)?.trim() == candidate,
            "native-suite candidate differs from current HEAD",
        )?;
        let suites = manifest["suites"]
            .as_array()
            .ok_or("native-suite suites must be an array")?;
        ensure(
            suites.len() == SUITES.len(),
            "native-suite requires seven suites",
        )?;
        let directory = manifest_path.parent().ok_or("manifest has no parent")?;
        let mut total_listed = 0;
        let mut total_passed = 0;
        let mut receipts = Vec::with_capacity(SUITES.len());
        for (suite, spec) in suites.iter().zip(&SUITES) {
            spec.validate(suite)?;
            ensure(
                suite["list_exit_code"].as_i64() == Some(0)
                    && suite["run_exit_code"].as_i64() == Some(0),
                "native-suite command exit must be zero",
            )?;
            let list = transcript(directory, &suite["list_path"])?;
            let run = transcript(directory, &suite["run_path"])?;
            let listed = listed_tests(
                std::str::from_utf8(&list).map_err(|_| "native-suite list is not UTF-8")?,
            )?;
            let passed = passed_tests(
                std::str::from_utf8(&run).map_err(|_| "native-suite run is not UTF-8")?,
            )?;
            if spec.id == "core-store" {
                ensure(
                    listed.get(WINDOWS_STORE_SENTINEL) == Some(&1)
                        && passed.get(WINDOWS_STORE_SENTINEL) == Some(&1),
                    "native-suite core-store must list and pass the Windows Store sentinel exactly once",
                )?;
            }
            ensure(
                !listed.is_empty() && listed == passed,
                "native-suite listed and passed name multisets must be equal and nonempty",
            )?;
            let listed_count = listed.values().sum::<usize>();
            let passed_count = passed.values().sum::<usize>();
            total_listed += listed_count;
            total_passed += passed_count;
            receipts.push(json!({
                "id": spec.id,
                "list_sha256": hash(&list)?,
                "run_sha256": hash(&run)?,
                "listed": listed_count,
                "passed": passed_count,
            }));
        }
        Ok(serde_jcs::to_string(&json!({
            "schema": "heleos.native-suite-receipt/v1",
            "manifest_sha256": hash(&manifest_bytes)?,
            "suites": receipts,
            "status": "pass",
            "candidate_sha": candidate,
            "platform": "windows-x86_64",
            "filesystem": "NTFS",
            "suite_count": suites.len(),
            "total_listed": total_listed,
            "total_passed": total_passed,
        }))?)
    }
}

mod secret_scan {
    use super::*;
    use serde_json::{Value, json};

    const BASELINE_PATH: &str = "governance/secret-scan-baseline.toml";
    const BASELINE: &[u8] = include_bytes!("../../../../governance/secret-scan-baseline.toml");
    const BASELINE_HASH: &str = "9824d4222256872805395cd2d898a308486b513146c626594a8b3a91d8d05d9b";
    const SOURCE: &str = "tests/verification/src/bin/verify-provenance.rs";
    const FIXTURE_CONTEXT_HASH: &str =
        "0b246dbefc1fa3faf4dab79a2ffccbec5f23f4e59a33fba924dee43bf83e6e87";

    fn string(v: &Value) -> Result<&str> {
        v.as_str()
            .ok_or_else(|| "secret-scan required string missing".into())
    }

    fn array(v: &Value) -> Result<&Vec<Value>> {
        v.as_array()
            .ok_or_else(|| "secret-scan required array missing".into())
    }

    fn number(v: &Value) -> Result<usize> {
        let n = usize::try_from(v.as_u64().ok_or("secret-scan invalid integer")?)?;
        ensure(n > 0 && n <= FILE_CAP, "secret-scan integer outside bounds")?;
        Ok(n)
    }

    fn lower_hex(text: &str, length: usize) -> bool {
        text.len() == length
            && text
                .bytes()
                .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
    }

    fn relative(path: &str) -> Result<()> {
        ensure(
            !path.is_empty()
                && path
                    .split('/')
                    .all(|part| !part.is_empty() && part != "." && part != "..")
                && path
                    .bytes()
                    .all(|b| b.is_ascii_alphanumeric() || b"_./-".contains(&b)),
            "secret-scan noncanonical repository path",
        )
    }

    #[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
    struct Finding {
        fingerprint: String,
        commit: String,
        path: String,
        rule: String,
        start: usize,
        end: usize,
    }

    impl Finding {
        fn read(value: &Value, history: bool) -> Result<Self> {
            let mut keys = BTreeSet::from([
                "Author",
                "Commit",
                "Date",
                "Description",
                "Email",
                "EndColumn",
                "EndLine",
                "Entropy",
                "File",
                "Fingerprint",
                "Match",
                "Message",
                "RuleID",
                "Secret",
                "StartColumn",
                "StartLine",
                "SymlinkFile",
                "Tags",
            ]);
            if history {
                keys.insert("Link");
            }
            let object = value
                .as_object()
                .ok_or("secret-scan finding must be an object")?;
            ensure(
                object.keys().map(String::as_str).collect::<BTreeSet<_>>() == keys,
                "secret-scan unknown or missing Gitleaks field",
            )?;
            for key in [
                "Author",
                "Commit",
                "Date",
                "Description",
                "Email",
                "File",
                "Fingerprint",
                "Message",
                "RuleID",
                "SymlinkFile",
            ] {
                string(&value[key])?;
            }
            if history {
                string(&value["Link"])?;
            }
            ensure(
                string(&value["SymlinkFile"])?.is_empty() && array(&value["Tags"])?.is_empty(),
                "secret-scan symlink or unsupported tag",
            )?;
            ensure(
                value["Entropy"]
                    .as_f64()
                    .is_some_and(|n| n.is_finite() && (0.0..=8.0).contains(&n)),
                "secret-scan invalid entropy",
            )?;
            number(&value["StartColumn"])?;
            number(&value["EndColumn"])?;
            // Compare only the exact redacted spellings emitted by 8.30.1 for
            // the adjudicated classes. Rejected Match/Secret values never enter
            // diagnostics, hashes, owned findings, or persisted evidence.
            ensure(
                value["Secret"] == "REDACTED",
                "secret-scan unredacted finding",
            )?;
            let allowed_match = match string(&value["RuleID"])? {
                "private-key" => "REDACTED".to_owned(),
                "generic-api-key" if string(&value["File"])?.starts_with("build_control/") => {
                    "cwd_key\":\"REDACTED\"".to_owned()
                }
                "generic-api-key" if value["File"] == "tests/test_api.py" => {
                    ["Idempotency", "-Key\": \"REDACTED\""].concat()
                }
                _ => return Err("secret-scan unadjudicated rule or path".into()),
            };
            ensure(
                value["Match"] == allowed_match,
                "secret-scan unredacted or unexpected match shape",
            )?;
            let finding = Self {
                fingerprint: string(&value["Fingerprint"])?.to_owned(),
                commit: string(&value["Commit"])?.to_owned(),
                path: string(&value["File"])?.to_owned(),
                rule: string(&value["RuleID"])?.to_owned(),
                start: number(&value["StartLine"])?,
                end: number(&value["EndLine"])?,
            };
            ensure(
                finding.end >= finding.start,
                "secret-scan inverted line range",
            )?;
            ensure(
                if history {
                    lower_hex(&finding.commit, 40)
                } else {
                    finding.commit.is_empty()
                },
                "secret-scan invalid commit identity",
            )?;
            let prefix = if history {
                format!("{}:", finding.commit)
            } else {
                String::new()
            };
            ensure(
                finding.fingerprint
                    == format!(
                        "{prefix}{}:{}:{}",
                        finding.path, finding.rule, finding.start
                    ),
                "secret-scan fingerprint differs from complete metadata",
            )?;
            if history {
                relative(&finding.path)?;
            }
            Ok(finding)
        }

        fn baseline(value: &Value) -> Result<Self> {
            let finding = Self {
                fingerprint: string(&value["fingerprint"])?.to_owned(),
                commit: string(&value["commit"])?.to_owned(),
                path: string(&value["path"])?.to_owned(),
                rule: string(&value["rule"])?.to_owned(),
                start: number(&value["start_line"])?,
                end: number(&value["end_line"])?,
            };
            relative(&finding.path)?;
            ensure(
                lower_hex(&finding.commit, 40)
                    && finding.end >= finding.start
                    && finding.fingerprint
                        == format!(
                            "{}:{}:{}:{}",
                            finding.commit, finding.path, finding.rule, finding.start
                        ),
                "secret-scan malformed baseline tuple",
            )?;
            Ok(finding)
        }
    }

    fn baseline(bytes: &[u8]) -> Result<Value> {
        ensure(
            hash(bytes)? == BASELINE_HASH,
            "secret-scan baseline bytes changed; owner adjudication required",
        )?;
        let parsed: toml::Value = toml::from_str(std::str::from_utf8(bytes)?)
            .map_err(|_| "secret-scan malformed baseline")?;
        let baseline = serde_json::to_value(parsed)?;
        ensure(
            baseline["schema_version"] == 1
                && baseline["scanner"] == "gitleaks"
                && baseline["scanner_version"] == "8.30.1"
                && baseline["foundation_scope"] == "Foundation 0.1"
                && baseline["expires_before"] == "Foundation 0.2"
                && env!("CARGO_PKG_VERSION") == "0.1.0"
                && baseline["expected_history_findings"] == 22
                && baseline["expected_current_tree_allowances"] == 1,
            "secret-scan baseline schema, scanner, or release scope drift",
        )?;
        let lock: toml::Value = toml::from_str(std::str::from_utf8(LOCK)?)
            .map_err(|_| "secret-scan frozen release lock malformed")?;
        let packages = lock
            .get("package")
            .and_then(toml::Value::as_array)
            .ok_or("secret-scan frozen release package scope absent")?;
        let local = packages
            .iter()
            .filter(|p| p.get("source").is_none())
            .collect::<Vec<_>>();
        ensure(
            local.len() == 7
                && local.iter().all(|p| {
                    p.get("version").and_then(toml::Value::as_str) == Some("0.1.0")
                        && p.get("name")
                            .and_then(toml::Value::as_str)
                            .is_some_and(|name| name.starts_with("heleos-"))
                }),
            "secret-scan baseline expired or release scope absent",
        )?;
        Ok(baseline)
    }

    fn history_set(baseline: &Value, report: &Value) -> Result<Vec<(Finding, String)>> {
        let mut expected = BTreeSet::new();
        let mut entries = Vec::new();
        let mut previous = String::new();
        let mut counts = BTreeMap::<String, usize>::new();
        for value in array(&baseline["history"])? {
            let finding = Finding::baseline(value)?;
            ensure(
                finding.fingerprint > previous,
                "secret-scan unordered or duplicated baseline fingerprint",
            )?;
            previous = finding.fingerprint.clone();
            ensure(
                expected.insert(finding.clone()),
                "secret-scan duplicate baseline tuple",
            )?;
            let class = string(&value["adjudication"])?;
            ensure(
                matches!(
                    class,
                    "cwd_key_receipt" | "invalid_idempotency_fixture" | "verifier_marker_literals"
                ),
                "secret-scan unknown adjudication",
            )?;
            *counts.entry(class.to_owned()).or_default() += 1;
            entries.push((finding, class.to_owned()));
        }
        ensure(
            expected.len() == 22
                && counts
                    == BTreeMap::from([
                        ("cwd_key_receipt".to_owned(), 20),
                        ("invalid_idempotency_fixture".to_owned(), 1),
                        ("verifier_marker_literals".to_owned(), 1),
                    ]),
            "secret-scan baseline class/count drift",
        )?;
        let values = array(report)?;
        ensure(
            values.len() == 22,
            "secret-scan history count differs from complete adjudication",
        )?;
        let mut actual = BTreeSet::new();
        let mut fingerprints = BTreeSet::new();
        for value in values {
            let finding = Finding::read(value, true)?;
            ensure(
                fingerprints.insert(finding.fingerprint.clone()) && actual.insert(finding),
                "secret-scan duplicate history finding",
            )?;
        }
        ensure(
            actual == expected,
            "secret-scan history differs from all 22 exact adjudicated tuples",
        )?;
        Ok(entries)
    }

    fn marker_context(baseline: &Value) -> Result<Vec<String>> {
        let allowance = array(&baseline["current_tree"])?;
        ensure(
            allowance.len() == 1,
            "secret-scan current allowance count drift",
        )?;
        let allowance = &allowance[0];
        ensure(
            allowance["path"] == SOURCE
                && allowance["rule"] == "private-key"
                && allowance["maximum_findings"] == 1
                && allowance["context_line_count"] == 10,
            "secret-scan marker allowance drift",
        )?;
        let mut context = [
            "function_line",
            "function_comment_line",
            "text_binding_line",
            "array_open_line",
        ]
        .iter()
        .map(|key| Ok(string(&allowance[*key])?.to_owned()))
        .collect::<Result<Vec<_>>>()?;
        let labels = array(&allowance["marker_labels"])?;
        let hashes = array(&allowance["marker_line_sha256"])?;
        ensure(
            labels.len() == 4 && hashes.len() == 4,
            "secret-scan marker count drift",
        )?;
        for (label, digest) in labels.iter().zip(hashes) {
            let line = ["\"", "-----", "BEGIN ", string(label)?, "-----", "\","].concat();
            ensure(
                hash(line.as_bytes())? == string(digest)?,
                "secret-scan marker hash drift",
            )?;
            context.push(line);
        }
        context.push(string(&allowance["array_close_line"])?.to_owned());
        context.push(string(&allowance["consumer_line"])?.to_owned());
        ensure(
            hash(format!("{}\n", context.join("\n")).as_bytes())?
                == string(&allowance["context_sha256"])?,
            "secret-scan context hash drift",
        )?;
        Ok(context)
    }

    fn marker_range(source: &[u8], context: &[String], finding: Option<&Finding>) -> Result<()> {
        let text = std::str::from_utf8(source).map_err(|_| "secret-scan source is not UTF-8")?;
        let lines = text
            .split('\n')
            .map(|line| line.trim_matches([' ', '\t']))
            .collect::<Vec<_>>();
        ensure(
            context.len() == 10 && lines.iter().filter(|line| **line == context[0]).count() == 1,
            "secret-scan marker function missing or ambiguous",
        )?;
        let starts = lines
            .windows(context.len())
            .enumerate()
            .filter_map(|(i, window)| {
                window
                    .iter()
                    .zip(context)
                    .all(|(a, b)| *a == b)
                    .then_some(i)
            })
            .collect::<Vec<_>>();
        ensure(
            starts.len() == 1,
            "secret-scan exact marker context missing or ambiguous",
        )?;
        if let Some(finding) = finding {
            let start = starts
                .first()
                .copied()
                .ok_or("secret-scan context start absent")?;
            ensure(
                finding.rule == "private-key"
                    && finding.start >= start + 5
                    && finding.end <= start + 8,
                "secret-scan finding includes non-marker source lines",
            )?;
        }
        Ok(())
    }

    fn historical_context(
        class: &str,
        finding: &Finding,
        bytes: &[u8],
        context: &[String],
    ) -> Result<()> {
        match class {
            "cwd_key_receipt" => {
                ensure(
                    finding.rule == "generic-api-key"
                        && finding.start == 1
                        && finding.end == 1
                        && finding
                            .path
                            .starts_with("build_control/graph/receipts/sha256/"),
                    "secret-scan receipt tuple/context mismatch",
                )?;
                let receipt: Value = serde_json::from_slice(bytes)
                    .map_err(|_| "secret-scan malformed historical receipt")?;
                ensure(
                    lower_hex(string(&receipt["cwd_key"])?, 64),
                    "secret-scan historical cwd_key is not a SHA-256 locator",
                )?;
                ensure(
                    std::str::from_utf8(bytes)?.split_terminator('\n').count() == 1,
                    "secret-scan historical receipt line mismatch",
                )?;
            }
            "invalid_idempotency_fixture" => {
                ensure(
                    finding.path == "tests/test_api.py"
                        && finding.rule == "generic-api-key"
                        && finding.start == 143
                        && finding.end == 143,
                    "secret-scan invalid-input fixture tuple mismatch",
                )?;
                let lines = std::str::from_utf8(bytes)?.split('\n').collect::<Vec<_>>();
                let block = lines
                    .get(129..146)
                    .ok_or("secret-scan fixture context truncated")?;
                // This exact immutable block invokes an invalid evidence ID/run
                // and mistyped fields, then asserts 400 and VALIDATION_ERROR.
                ensure(
                    hash(format!("{}\n", block.join("\n")).as_bytes())? == FIXTURE_CONTEXT_HASH,
                    "secret-scan historical invalid-input fixture context changed",
                )?;
            }
            "verifier_marker_literals" => {
                ensure(
                    finding.path == SOURCE,
                    "secret-scan historical marker path mismatch",
                )?;
                marker_range(bytes, context, Some(finding))?;
            }
            _ => return Err("secret-scan unknown historical classification".into()),
        }
        Ok(())
    }

    fn normalize_current(path: &str, prefixes: &[String]) -> Result<String> {
        if path == SOURCE {
            return Ok(path.to_owned());
        }
        let normalized = prefixes
            .iter()
            .find_map(|prefix| path.strip_prefix(prefix))
            .ok_or("secret-scan current path is outside the known source snapshot")?;
        relative(normalized)?;
        ensure(
            normalized == SOURCE,
            "secret-scan current path has no allowance",
        )?;
        Ok(normalized.to_owned())
    }

    fn snapshot_prefix(path: &Path) -> Result<String> {
        // mktemp may preserve the trailing slash in TMPDIR as a doubled
        // separator; Gitleaks cleans it. Keep the same lexical OS-temp root,
        // while making that known prefix match the scanner's spelling.
        let clean = path.components().collect::<PathBuf>();
        Ok(format!(
            "{}/",
            clean
                .to_str()
                .ok_or("secret-scan snapshot encoding")?
                .replace('\\', "/")
        ))
    }

    fn current_set(
        baseline: &Value,
        report: &Value,
        prefixes: &[String],
        source: &[u8],
    ) -> Result<usize> {
        let context = marker_context(baseline)?;
        marker_range(source, &context, None)?;
        let findings = array(report)?;
        ensure(
            findings.len() <= 1,
            "secret-scan current findings exceed the single marker allowance",
        )?;
        for value in findings {
            let mut finding = Finding::read(value, false)?;
            finding.path = normalize_current(&finding.path, prefixes)?;
            marker_range(source, &context, Some(&finding))?;
        }
        Ok(findings.len())
    }

    fn report_path(input: &str, filename: &str) -> Result<(PathBuf, PathBuf)> {
        let path = PathBuf::from(input);
        ensure(
            path.is_absolute() && path.file_name().is_some_and(|n| n == filename),
            "secret-scan report must have its exact absolute sibling path",
        )?;
        ensure(
            path.components()
                .all(|c| !matches!(c, Component::ParentDir | Component::CurDir)),
            "secret-scan indirect report path",
        )?;
        let parent = path
            .parent()
            .ok_or("secret-scan report parent absent")?
            .canonicalize()?;
        direct_path(&parent)?;
        let resolved = parent.join(filename);
        direct_path(&resolved)?;
        Ok((resolved, parent))
    }

    pub(super) fn run(args: &[String]) -> Result<String> {
        ensure(
            args.len() == 3,
            "verify-secret-scan expects history and current JSON reports",
        )?;
        let root = std::env::current_dir()?.canonicalize()?;
        verify_frozen_file(&root, "Cargo.lock", LOCK)?;
        verify_frozen_file(&root, BASELINE_PATH, BASELINE)?;
        let baseline = baseline(BASELINE)?;
        let (history_path, history_parent) = report_path(&args[1], "history.json")?;
        let (current_path, parent) = report_path(&args[2], "current.json")?;
        ensure(
            history_parent == parent
                && parent.parent() == Some(std::env::temp_dir().canonicalize()?.as_path()),
            "secret-scan reports must share one OS-temp evidence directory",
        )?;
        let name = parent
            .file_name()
            .and_then(|n| n.to_str())
            .ok_or("secret-scan evidence directory encoding")?;
        let suffix = name
            .strip_prefix("heleos-supply-chain.")
            .ok_or("secret-scan unknown evidence directory")?;
        ensure(
            !suffix.is_empty() && suffix.bytes().all(|b| b.is_ascii_alphanumeric()),
            "secret-scan evidence directory suffix",
        )?;
        let snapshot_root = parent.join("source");
        direct_path(&snapshot_root)?;
        let source_path = snapshot_root.join(SOURCE);
        let source = read_regular(&source_path, FILE_CAP)?;
        ensure(
            source == read_regular(&root.join(SOURCE), FILE_CAP)?,
            "secret-scan scanned verifier differs from current source",
        )?;
        let current_lexical = Path::new(&args[2])
            .parent()
            .ok_or("secret-scan report parent absent")?
            .join("source");
        let prefixes = vec![
            snapshot_prefix(&snapshot_root)?,
            snapshot_prefix(&current_lexical)?,
        ];
        let version = bounded(
            Command::new("gitleaks").arg("version"),
            Duration::from_secs(30),
        )?;
        version.require_success("secret-scan Gitleaks version")?;
        ensure(
            std::str::from_utf8(&version.stdout)?.trim() == "8.30.1",
            "secret-scan Gitleaks version drift",
        )?;
        let history_bytes = read_regular(&history_path, FILE_CAP)?;
        let current_bytes = read_regular(&current_path, FILE_CAP)?;
        let history: Value = serde_json::from_slice(&history_bytes)
            .map_err(|_| "secret-scan malformed history JSON")?;
        let current: Value = serde_json::from_slice(&current_bytes)
            .map_err(|_| "secret-scan malformed current JSON")?;
        let entries = history_set(&baseline, &history)?;
        let context = marker_context(&baseline)?;
        for (finding, class) in &entries {
            let object = bounded(
                Command::new("git").current_dir(&root).args([
                    "cat-file",
                    "blob",
                    &format!("{}:{}", finding.commit, finding.path),
                ]),
                Duration::from_secs(30),
            )?;
            object.require_success("secret-scan immutable historical source")?;
            historical_context(class, finding, &object.stdout, &context)?;
        }
        let current_count = current_set(&baseline, &current, &prefixes, &source)?;
        ensure(
            read_regular(&source_path, FILE_CAP)? == source
                && read_regular(&root.join(SOURCE), FILE_CAP)? == source
                && read_regular(&history_path, FILE_CAP)? == history_bytes
                && read_regular(&current_path, FILE_CAP)? == current_bytes,
            "secret-scan evidence changed during adjudication",
        )?;
        Ok(serde_jcs::to_string(
            &json!({"schema":"heleos.secret-scan-report/v1","status":"pass","scanner":"gitleaks","scanner_version":"8.30.1",
            "baseline_sha256":BASELINE_HASH,"history_findings":entries.len(),"current_findings":current_count,
            "history_report_sha256":hash(&history_bytes)?,"current_report_sha256":hash(&current_bytes)?}),
        )?)
    }
    #[cfg(test)]
    mod tests {
        use super::*;
        use serde_json::{Value, json};

        fn report(finding: &Finding, history: bool) -> Value {
            let mut value = json!({"Author":"","Commit":finding.commit,"Date":"","Description":"synthetic redacted fixture","Email":"",
            "EndColumn":1,"EndLine":finding.end,"Entropy":1.0,"File":finding.path,"Fingerprint":finding.fingerprint,"Match":"REDACTED",
            "Message":"","RuleID":finding.rule,"Secret":"REDACTED","StartColumn":1,"StartLine":finding.start,"SymlinkFile":"","Tags":[]});
            if history {
                value["Link"] = json!("");
                if finding.path.starts_with("build_control/") {
                    value["Match"] = json!("cwd_key\":\"REDACTED\"");
                }
                if finding.path == "tests/test_api.py" {
                    value["Match"] = json!(["Idempotency", "-Key\": \"REDACTED\""].concat());
                }
            }
            value
        }

        fn history(baseline: &Value) -> Value {
            json!(
                array(&baseline["history"])
                    .unwrap()
                    .iter()
                    .map(|entry| report(&Finding::baseline(entry).unwrap(), true))
                    .collect::<Vec<_>>()
            )
        }

        fn current(path: &str, start: usize, end: usize) -> Value {
            report(
                &Finding {
                    fingerprint: format!("{path}:private-key:{start}"),
                    commit: String::new(),
                    path: path.to_owned(),
                    rule: "private-key".to_owned(),
                    start,
                    end,
                },
                false,
            )
        }

        fn source(baseline: &Value) -> Vec<u8> {
            format!(
                "preamble\n{}\n",
                marker_context(baseline).unwrap().join("\n")
            )
            .into_bytes()
        }

        #[test]
        fn exact_history_is_one_to_one_and_report_order_is_irrelevant() {
            let baseline = baseline(BASELINE).unwrap();
            let mut report = history(&baseline);
            assert_eq!(history_set(&baseline, &report).unwrap().len(), 22);
            report.as_array_mut().unwrap().reverse();
            assert_eq!(history_set(&baseline, &report).unwrap().len(), 22);
            for case in 0..5 {
                let mut candidate = report.clone();
                match case {
                    0 => {
                        candidate.as_array_mut().unwrap().pop();
                    }
                    1 => {
                        candidate[0] = candidate[1].clone();
                    }
                    2 => candidate[0]["EndLine"] = json!(2),
                    3 => candidate[0]["Commit"] = json!("a".repeat(40)),
                    _ => {
                        let extra = candidate[0].clone();
                        candidate.as_array_mut().unwrap().push(extra);
                    }
                }
                assert!(history_set(&baseline, &candidate).is_err(), "case {case}");
            }
        }

        #[test]
        fn unredacted_unknown_missing_or_wrong_type_report_fields_fail() {
            let valid = current(SOURCE, 6, 8);
            assert!(Finding::read(&valid, false).is_ok());
            for case in 0..8 {
                let mut candidate = valid.clone();
                match case {
                    0 => candidate["Secret"] = json!("not-redacted"),
                    1 => candidate["Match"] = json!("unexpected REDACTED context"),
                    2 => candidate["Unknown"] = json!("field"),
                    3 => {
                        candidate.as_object_mut().unwrap().remove("Tags");
                    }
                    4 => candidate["StartLine"] = json!("6"),
                    5 => candidate["StartColumn"] = json!(0),
                    6 => candidate["SymlinkFile"] = json!("source-link"),
                    _ => candidate["Tags"] = json!(["unexpected"]),
                }
                assert!(Finding::read(&candidate, false).is_err(), "case {case}");
            }
            assert!(Finding::read(&valid, true).is_err());
        }

        #[test]
        fn marker_allowance_checks_every_line_and_the_unique_hashed_context() {
            let baseline = baseline(BASELINE).unwrap();
            let bytes = source(&baseline);
            assert_eq!(
                current_set(&baseline, &json!([current(SOURCE, 6, 9)]), &[], &bytes).unwrap(),
                1
            );
            assert_eq!(current_set(&baseline, &json!([]), &[], &bytes).unwrap(), 0);
            for (start, end) in [(5, 9), (6, 10), (9, 8), (1, 1), (100, 101)] {
                assert!(
                    current_set(
                        &baseline,
                        &json!([current(SOURCE, start, end)]),
                        &[],
                        &bytes
                    )
                    .is_err()
                );
            }
            assert!(
                current_set(
                    &baseline,
                    &json!([current(SOURCE, 6, 8), current(SOURCE, 6, 8)]),
                    &[],
                    &bytes
                )
                .is_err()
            );
            assert!(current_set(&baseline, &json!({}), &[], &bytes).is_err());
            let mut duplicate = bytes.clone();
            duplicate.extend_from_slice(&bytes);
            assert!(current_set(&baseline, &json!([]), &[], &duplicate).is_err());
            let crlf = String::from_utf8(bytes.clone())
                .unwrap()
                .replace('\n', "\r\n");
            assert!(current_set(&baseline, &json!([]), &[], crlf.as_bytes()).is_err());
            let changed = String::from_utf8(bytes)
                .unwrap()
                .replace("text.match_indices(header)", "text.match_indices(other)");
            assert!(current_set(&baseline, &json!([]), &[], changed.as_bytes()).is_err());
        }

        #[test]
        fn current_path_normalization_has_one_exact_snapshot_prefix() {
            let prefix = "/os-temp/heleos-supply-chain.fixture/source/".to_owned();
            assert_eq!(
                snapshot_prefix(Path::new("/os-temp//heleos-supply-chain.fixture/source")).unwrap(),
                prefix
            );
            assert_eq!(
                normalize_current(&format!("{prefix}{SOURCE}"), std::slice::from_ref(&prefix))
                    .unwrap(),
                SOURCE
            );
            for path in [
                format!("{prefix}../{SOURCE}"),
                format!("{prefix}./{SOURCE}"),
                format!("{prefix}/{SOURCE}"),
                format!("/os-temp/heleos-supply-chain.fixture/source-decoy/{SOURCE}"),
                format!("{prefix}{}", SOURCE.replace('/', "\\")),
                format!("target/duplicate/{SOURCE}"),
                format!("{prefix}{SOURCE}:stream"),
            ] {
                assert!(normalize_current(&path, std::slice::from_ref(&prefix)).is_err());
            }
        }

        #[test]
        fn historical_receipts_require_the_exact_locator_shape_and_line() {
            let baseline = baseline(BASELINE).unwrap();
            let finding = Finding::baseline(&baseline["history"][0]).unwrap();
            let context = marker_context(&baseline).unwrap();
            let valid = serde_jcs::to_vec(&json!({"cwd_key":"a".repeat(64)})).unwrap();
            assert!(historical_context("cwd_key_receipt", &finding, &valid, &context).is_ok());
            for value in [
                json!({}),
                json!({"cwd_key":"A".repeat(64)}),
                json!({"cwd_key":"a".repeat(63)}),
                json!({"cwd_key":64}),
            ] {
                assert!(
                    historical_context(
                        "cwd_key_receipt",
                        &finding,
                        &serde_jcs::to_vec(&value).unwrap(),
                        &context
                    )
                    .is_err()
                );
            }
            assert!(
                historical_context("invalid_idempotency_fixture", &finding, &valid, &context)
                    .is_err()
            );
        }

        #[test]
        fn baseline_byte_drift_and_marker_hash_drift_are_not_new_allowances() {
            let mut bytes = BASELINE.to_vec();
            bytes.push(b'\n');
            assert!(baseline(&bytes).is_err());
            let mut policy = baseline(BASELINE).unwrap();
            policy["current_tree"][0]["marker_line_sha256"][0] = json!("a".repeat(64));
            assert!(marker_context(&policy).is_err());
            let mut policy = baseline(BASELINE).unwrap();
            policy["current_tree"][0]["context_sha256"] = json!("a".repeat(64));
            assert!(marker_context(&policy).is_err());
        }
    }
}

// Supply-chain orchestration is deliberately separate from normal admission:
// normal admission may run as a child of Cargo while its workspace lock is held.
mod sbom {
    use super::*;
    use serde_json::{Value, json};

    const DESTINATION: &str = "artifacts/sbom/heleos-foundation-0.1.cdx.json";
    const REGISTRY: &str = "registry+https://github.com/rust-lang/crates.io-index";
    const REGISTRY_DIRECTORY: &str = "index.crates.io-1949cf8c6b5b557f";
    // The epoch belongs to the integrated Task 9 baseline. The source digest,
    // not this ancestor alone, identifies the exact copied candidate bytes.
    const BASE: &str = "c1596c4cc536155ad3a052cfec2c9b8951814aba";
    const EPOCH: &str = "1788897049";
    const TIMESTAMP: &str = "2026-09-08T19:50:49.000000000Z";
    const WORKSPACE: &str = "heleos-foundation-0.1-workspace";
    const TARGETS: [&str; 3] = [
        "aarch64-apple-darwin",
        "x86_64-pc-windows-msvc",
        "wasm32-wasip1",
    ];
    type Records = BTreeMap<String, Value>;
    type Edges = BTreeSet<(String, String)>;

    fn string(value: &Value) -> Result<&str> {
        let text = value.as_str().ok_or("SBOM required string missing")?;
        ensure(
            !text.is_empty() && !text.chars().any(char::is_control),
            "SBOM invalid string",
        )?;
        Ok(text)
    }

    fn array(value: &Value) -> Result<&Vec<Value>> {
        value
            .as_array()
            .ok_or_else(|| "SBOM required array missing".into())
    }

    fn strings(value: &Value) -> Result<BTreeSet<String>> {
        let values = array(value)?;
        let set = values
            .iter()
            .map(|v| Ok(string(v)?.to_owned()))
            .collect::<Result<BTreeSet<_>>>()?;
        ensure(set.len() == values.len(), "SBOM duplicate string record")?;
        Ok(set)
    }

    fn record<'a>(values: &'a Records, key: &str) -> Result<&'a Value> {
        values
            .get(key)
            .ok_or_else(|| "SBOM unknown package or node".into())
    }

    fn records(value: &Value, key: &str) -> Result<Records> {
        let mut result = Records::new();
        for value in array(value)? {
            ensure(
                result
                    .insert(string(&value[key])?.to_owned(), value.clone())
                    .is_none(),
                "SBOM duplicate package or node",
            )?;
        }
        Ok(result)
    }

    fn source(value: &Value) -> Result<&str> {
        if value.is_null() {
            Ok("workspace")
        } else {
            string(value)
        }
    }

    fn identity(p: &Value) -> Result<String> {
        Ok(format!(
            "{}\n{}\n{}",
            string(&p["name"])?,
            string(&p["version"])?,
            source(&p["source"])?
        ))
    }

    fn encoded(text: &str) -> String {
        text.bytes()
            .map(|b| {
                if b.is_ascii_alphanumeric() || b"+-._~".contains(&b) {
                    (b as char).to_string()
                } else {
                    format!("%{b:02X}")
                }
            })
            .collect()
    }

    fn purl(p: &Value) -> Result<String> {
        Ok(format!(
            "pkg:cargo/{}@{}",
            encoded(string(&p["name"])?),
            encoded(string(&p["version"])?)
        ))
    }

    fn property(name: &str, value: impl Into<String>) -> Value {
        json!({"name":name,"value":value.into()})
    }

    fn sorted(values: Vec<Value>) -> Result<Vec<Value>> {
        let mut values = values
            .into_iter()
            .map(|v| Ok((serde_jcs::to_string(&v)?, v)))
            .collect::<Result<Vec<_>>>()?;
        values.sort_by(|a, b| a.0.cmp(&b.0));
        Ok(values.into_iter().map(|(_, v)| v).collect())
    }

    fn parse_toml(bytes: &[u8]) -> Result<Value> {
        let value: toml::Value =
            toml::from_str(std::str::from_utf8(bytes)?).map_err(|_| "SBOM malformed TOML input")?;
        Ok(serde_json::to_value(value)?)
    }

    fn parse_json(bytes: &[u8]) -> Result<Value> {
        serde_json::from_slice(bytes).map_err(|_| "SBOM malformed JSON input".into())
    }

    fn lock_records(bytes: &[u8]) -> Result<Records> {
        let lock = parse_toml(bytes)?;
        ensure(lock["version"] == 4, "SBOM requires Cargo lock version 4")?;
        let mut result = Records::new();
        for package in array(&lock["package"])? {
            // Reject path syntax before deriving archive and sparse-index paths.
            for key in ["name", "version"] {
                ensure(
                    string(&package[key])?
                        .bytes()
                        .all(|b| b.is_ascii_alphanumeric() || b"-._+".contains(&b)),
                    "SBOM invalid locked package spelling",
                )?;
            }
            if package["source"].is_null() {
                ensure(
                    package["checksum"].is_null(),
                    "SBOM workspace checksum is not admitted",
                )?;
            } else {
                ensure(
                    string(&package["source"])? == REGISTRY,
                    "SBOM unadmitted external source",
                )?;
                let digest = string(&package["checksum"])?;
                ensure(
                    digest.len() == 64
                        && digest
                            .bytes()
                            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b)),
                    "SBOM malformed registry checksum",
                )?;
            }
            ensure(
                result.insert(identity(package)?, package.clone()).is_none(),
                "SBOM duplicate lock identity",
            )?;
        }
        ensure(
            !result.is_empty() && result.len() <= 4096,
            "SBOM package count outside bounds",
        )?;
        Ok(result)
    }

    fn relative_manifest(root: &Path, package: &Value) -> Result<String> {
        let path = Path::new(string(&package["manifest_path"])?);
        let relative = path
            .strip_prefix(root)
            .map_err(|_| "SBOM workspace package escaped copy")?;
        ensure(
            relative
                .components()
                .all(|c| matches!(c, Component::Normal(_)))
                && relative.file_name().is_some_and(|n| n == "Cargo.toml"),
            "SBOM indirect workspace manifest",
        )?;
        Ok(relative
            .to_str()
            .ok_or("SBOM non-UTF-8 manifest path")?
            .replace('\\', "/"))
    }

    struct Graph {
        packages: Records,
        nodes: Records,
        locked: Records,
        urls: BTreeMap<String, String>,
        members: BTreeSet<String>,
        edges: Edges,
    }

    impl Graph {
        fn parse(metadata: &Value, lock: &[u8], root_manifest: &[u8], root: &Path) -> Result<Self> {
            ensure(
                metadata["version"] == 1 && Path::new(string(&metadata["workspace_root"])?) == root,
                "SBOM metadata format or workspace-root drift",
            )?;
            let locked = lock_records(lock)?;
            let packages = records(&metadata["packages"], "id")?;
            let nodes = records(&metadata["resolve"]["nodes"], "id")?;
            let members = strings(&metadata["workspace_members"])?;
            ensure(
                packages.len() == locked.len() && packages.keys().eq(nodes.keys()),
                "SBOM incomplete metadata package/node coverage",
            )?;
            let identities = packages
                .values()
                .map(identity)
                .collect::<Result<BTreeSet<_>>>()?;
            ensure(
                identities.len() == packages.len()
                    && identities == locked.keys().cloned().collect(),
                "SBOM lock/metadata identities differ",
            )?;
            let manifest = parse_toml(root_manifest)?;
            let declared = strings(&manifest["workspace"]["members"])?;
            let mut actual_members = BTreeSet::new();
            let mut urls = BTreeMap::new();
            let mut edges = Edges::new();
            for (id, p) in &packages {
                let lp = record(&locked, &identity(p)?)?;
                let node = record(&nodes, id)?;
                string(&p["license"])?;
                let dependencies = strings(&node["dependencies"])?;
                let mut dep_packages = BTreeSet::new();
                let mut aliases = BTreeSet::new();
                strings(&node["features"])?;
                for dep in array(&node["deps"])? {
                    let to = string(&dep["pkg"])?;
                    record(&packages, to)?;
                    ensure(
                        aliases.insert((string(&dep["name"])?.to_owned(), to.to_owned())),
                        "SBOM duplicate dependency alias",
                    )?;
                    let kinds = array(&dep["dep_kinds"])?;
                    ensure(!kinds.is_empty(), "SBOM missing dependency-kind record")?;
                    let mut seen = BTreeSet::new();
                    for kind in kinds {
                        ensure(
                            kind["kind"].is_null()
                                || matches!(kind["kind"].as_str(), Some("build" | "dev")),
                            "SBOM unknown dependency kind",
                        )?;
                        if !kind["target"].is_null() {
                            string(&kind["target"])?;
                        }
                        ensure(
                            seen.insert(serde_jcs::to_string(kind)?),
                            "SBOM duplicate dependency-kind record",
                        )?;
                    }
                    dep_packages.insert(to.to_owned());
                    edges.insert((id.clone(), to.to_owned()));
                }
                ensure(
                    dep_packages == dependencies,
                    "SBOM redundant metadata dependency sets disagree",
                )?;
                let mut expected = BTreeSet::new();
                if !lp["dependencies"].is_null() {
                    for dependency in array(&lp["dependencies"])? {
                        let spec = string(dependency)?.split_whitespace().collect::<Vec<_>>();
                        ensure(
                            (1..=3).contains(&spec.len()),
                            "SBOM malformed lock dependency",
                        )?;
                        if spec.len() == 3 {
                            ensure(
                                spec[2].strip_prefix('(').and_then(|s| s.strip_suffix(')'))
                                    == Some(REGISTRY),
                                "SBOM malformed lock dependency source",
                            )?;
                        }
                        let mut matches = Vec::new();
                        for (candidate_id, candidate) in &packages {
                            if string(&candidate["name"])? == spec[0]
                                && (spec.len() < 2 || string(&candidate["version"])? == spec[1])
                                && (spec.len() < 3
                                    || spec[2].strip_prefix('(').and_then(|s| s.strip_suffix(')'))
                                        == candidate["source"].as_str())
                            {
                                matches.push(candidate_id.clone());
                            }
                        }
                        ensure(
                            matches.len() == 1,
                            "SBOM unknown or ambiguous lock dependency",
                        )?;
                        let matched = matches.pop().ok_or("SBOM missing lock match")?;
                        ensure(expected.insert(matched), "SBOM duplicate lock dependency")?;
                    }
                }
                ensure(
                    expected == dependencies,
                    "SBOM lock and metadata edges disagree",
                )?;
                if p["source"].is_null() {
                    ensure(members.contains(id), "SBOM unadmitted local package")?;
                    let relative = relative_manifest(root, p)?;
                    let directory = relative
                        .strip_suffix("/Cargo.toml")
                        .ok_or("SBOM root package not admitted")?;
                    ensure(
                        actual_members.insert(directory.to_owned()),
                        "SBOM duplicate workspace manifest",
                    )?;
                } else {
                    ensure(
                        !members.contains(id) && string(&p["source"])? == REGISTRY,
                        "SBOM unexpected member or source",
                    )?;
                }
                urls.insert(id.clone(), purl(p)?);
            }
            ensure(
                members.len() == actual_members.len() && actual_members == declared,
                "SBOM declared workspace membership differs",
            )?;
            ensure(
                urls.values().collect::<BTreeSet<_>>().len() == packages.len(),
                "SBOM canonical purl collision",
            )?;
            Ok(Self {
                packages,
                nodes,
                locked,
                urls,
                members,
                edges,
            })
        }

        fn url(&self, id: &str) -> Result<&str> {
            self.urls
                .get(id)
                .map(String::as_str)
                .ok_or_else(|| "SBOM unknown purl reference".into())
        }

        fn checksum(&self, package: &Value) -> Result<&Value> {
            Ok(&record(&self.locked, &identity(package)?)?["checksum"])
        }

        fn closure(&self, member: &str) -> Result<(BTreeSet<String>, Edges)> {
            let mut ids = BTreeSet::new();
            let mut edges = Edges::new();
            let mut pending = vec![member.to_owned()];
            while let Some(from) = pending.pop() {
                if !ids.insert(from.clone()) {
                    continue;
                }
                for dep in array(&record(&self.nodes, &from)?["deps"])? {
                    // cargo-cyclonedx 0.5.9 drops an edge only if every kind is dev.
                    if array(&dep["dep_kinds"])?
                        .iter()
                        .any(|kind| kind["kind"] != "dev")
                    {
                        let to = string(&dep["pkg"])?;
                        edges.insert((from.clone(), to.to_owned()));
                        pending.push(to.to_owned());
                    }
                }
            }
            Ok((ids, edges))
        }

        fn validate_target(&self, metadata: &Value, root: &Path) -> Result<Value> {
            ensure(
                metadata["version"] == 1
                    && Path::new(string(&metadata["workspace_root"])?) == root
                    && strings(&metadata["workspace_members"])? == self.members,
                "SBOM target workspace drift",
            )?;
            let packages = records(&metadata["packages"], "id")?;
            let nodes = records(&metadata["resolve"]["nodes"], "id")?;
            ensure(
                packages.keys().eq(nodes.keys()),
                "SBOM target package/node coverage differs",
            )?;
            let mut edges = Edges::new();
            for (id, package) in &packages {
                ensure(
                    package == record(&self.packages, id)?,
                    "SBOM target package identity drift",
                )?;
                let node = record(&nodes, id)?;
                let original = record(&self.nodes, id)?;
                ensure(
                    strings(&node["features"])?.is_subset(&strings(&original["features"])?),
                    "SBOM target feature drift",
                )?;
                let mut dependencies = BTreeSet::new();
                let mut aliases = BTreeSet::new();
                for dep in array(&node["deps"])? {
                    let to = string(&dep["pkg"])?;
                    record(&packages, to)?;
                    ensure(
                        aliases.insert((string(&dep["name"])?.to_owned(), to.to_owned())),
                        "SBOM duplicate target alias",
                    )?;
                    let original = array(&original["deps"])?
                        .iter()
                        .find(|d| d["pkg"] == dep["pkg"] && d["name"] == dep["name"])
                        .ok_or("SBOM target alias drift")?;
                    let kinds = array(&dep["dep_kinds"])?;
                    let all_kinds = array(&original["dep_kinds"])?;
                    ensure(
                        !kinds.is_empty() && kinds.iter().all(|kind| all_kinds.contains(kind)),
                        "SBOM target dependency-kind drift",
                    )?;
                    let unique = kinds
                        .iter()
                        .map(serde_jcs::to_string)
                        .collect::<std::result::Result<BTreeSet<_>, _>>()?;
                    ensure(
                        unique.len() == kinds.len(),
                        "SBOM duplicate target dependency-kind record",
                    )?;
                    dependencies.insert(to.to_owned());
                    edges.insert((id.clone(), to.to_owned()));
                }
                ensure(
                    dependencies == strings(&node["dependencies"])?,
                    "SBOM target dependency sets disagree",
                )?;
            }
            ensure(
                edges.is_subset(&self.edges),
                "SBOM target edge outside complete graph",
            )?;
            Ok(json!({"packages":packages.len(),"package_edges":edges.len()}))
        }
    }

    fn license(package: &Value, choice: &Value) -> Result<Value> {
        let text = string(&package["license"])?;
        ensure(
            choice == &json!([{"expression":text}])
                || choice == &json!([{"license":{"name":text}}]),
            "SBOM license choice differs from exact manifest declaration",
        )?;
        Ok(choice.clone())
    }

    fn generator_tools() -> Value {
        json!([{"vendor":"CycloneDX","name":"cargo-cyclonedx","version":"0.5.9"}])
    }

    fn local_purl(graph: &Graph, member: &str, package: &Value) -> Result<String> {
        let root = Path::new(string(&record(&graph.packages, member)?["manifest_path"])?)
            .parent()
            .ok_or("SBOM member manifest parent missing")?;
        let path = Path::new(string(&package["manifest_path"])?)
            .parent()
            .ok_or("SBOM package manifest parent missing")?;
        let from = root.components().collect::<Vec<_>>();
        let to = path.components().collect::<Vec<_>>();
        let common = from.iter().zip(&to).take_while(|(a, b)| a == b).count();
        ensure(common > 0, "SBOM workspace manifests have different roots")?;
        let mut relative = vec!["..".to_owned(); from.len() - common];
        for part in &to[common..] {
            ensure(
                matches!(part, Component::Normal(_)),
                "SBOM indirect local purl",
            )?;
            relative.push(
                part.as_os_str()
                    .to_str()
                    .ok_or("SBOM purl encoding")?
                    .to_owned(),
            );
        }
        if relative.is_empty() {
            relative.push(".".to_owned());
        }
        // The pinned generator leaves slash/colon/dot in this qualifier. Workspace
        // member names are ASCII; a new spelling needs a deliberate admission.
        let relative = relative.join(if cfg!(windows) { "\\" } else { "/" });
        ensure(
            relative
                .bytes()
                .all(|b| b.is_ascii_alphanumeric() || b"._-/\\".contains(&b)),
            "SBOM unsupported local purl spelling",
        )?;
        let relative = relative.replace('\\', "%5C");
        Ok(format!("{}?download_url=file://{relative}", purl(package)?))
    }

    fn accept_raw(
        graph: &Graph,
        member: &str,
        bom: &Value,
        raw: &mut Records,
        licenses: &mut Records,
    ) -> Result<()> {
        ensure(
            bom["bomFormat"] == "CycloneDX"
                && bom["specVersion"] == "1.5"
                && bom["version"] == 1
                && bom.get("serialNumber").is_none()
                && bom["metadata"]["timestamp"] == TIMESTAMP
                && bom["metadata"]["tools"] == generator_tools()
                && bom["metadata"]["component"]["bom-ref"] == member,
            "SBOM generator identity, epoch, or format drift",
        )?;
        let (ids, expected_edges) = graph.closure(member)?;
        let mut components = array(&bom["components"])?.clone();
        components.push(bom["metadata"]["component"].clone());
        let components = records(&json!(components), "bom-ref")?;
        ensure(
            components.keys().cloned().collect::<BTreeSet<_>>() == ids,
            "SBOM incomplete raw generator components",
        )?;
        for (id, component) in components {
            let package = record(&graph.packages, &id)?;
            ensure(
                component["name"] == package["name"] && component["version"] == package["version"],
                "SBOM raw name/version drift",
            )?;
            let choice = license(package, &component["licenses"])?;
            if let Some(prior) =
                licenses.insert(string(&package["license"])?.to_owned(), choice.clone())
            {
                ensure(prior == choice, "SBOM conflicting license interpretations")?;
            }
            if package["source"].is_null() {
                ensure(
                    string(&component["purl"])? == local_purl(graph, member, package)?
                        && component["hashes"].is_null(),
                    "SBOM workspace purl/checksum drift",
                )?;
            } else {
                ensure(
                    string(&component["purl"])? == graph.url(&id)?
                        && component["hashes"]
                            == json!([{"alg":"SHA-256","content":graph.checksum(package)?}]),
                    "SBOM raw purl/checksum drift",
                )?;
            }
            // Target subcomponents and descriptions carry host paths. They are
            // not package identities and never enter the aggregate inventory.
            let mut minimal = json!({"type":"library","bom-ref":graph.url(&id)?,"name":package["name"],
                "version":package["version"],"purl":graph.url(&id)?,"licenses":choice});
            if !package["source"].is_null() {
                minimal["hashes"] = component["hashes"].clone();
            }
            if let Some(prior) = raw.insert(id, minimal.clone()) {
                ensure(prior == minimal, "SBOM conflicting duplicate component")?;
            }
        }
        let nodes = records(&bom["dependencies"], "ref")?;
        ensure(
            nodes.keys().cloned().collect::<BTreeSet<_>>() == ids,
            "SBOM raw dependency-node coverage differs",
        )?;
        let mut edges = Edges::new();
        for (from, node) in nodes {
            if !node["dependsOn"].is_null() {
                for to in strings(&node["dependsOn"])? {
                    edges.insert((from.clone(), to));
                }
            }
        }
        ensure(
            edges == expected_edges,
            "SBOM raw generator edge coverage differs",
        )
    }

    fn assemble(
        graph: &Graph,
        raw: &Records,
        licenses: &Records,
        root: &Path,
        source_digest: &str,
        lock_digest: &str,
    ) -> Result<Vec<u8>> {
        let mut components = BTreeMap::new();
        let mut dependencies = BTreeMap::new();
        for (id, package) in &graph.packages {
            let url = graph.url(id)?;
            let mut component = if let Some(component) = raw.get(id) {
                component.clone()
            } else {
                ensure(
                    !package["source"].is_null(),
                    "SBOM workspace generator component missing",
                )?;
                let choice = record(licenses, string(&package["license"])?)?;
                license(package, choice)?;
                json!({"type":"library","bom-ref":url,"name":package["name"],"version":package["version"],"purl":url,
                    "licenses":choice,"hashes":[{"alg":"SHA-256","content":graph.checksum(package)?}]})
            };
            let node = record(&graph.nodes, id)?;
            let details = array(&node["deps"])?
                .iter()
                .map(|dep| {
                    Ok(json!({"purl":graph.url(string(&dep["pkg"])?)?,
                "name":dep["name"],"dep_kinds":sorted(array(&dep["dep_kinds"])?.clone())?}))
                })
                .collect::<Result<Vec<_>>>()?;
            let mut props = vec![
                property(
                    "heleos:cargo:resolved-features",
                    serde_jcs::to_string(&strings(&node["features"])?)?,
                ),
                property(
                    "heleos:cargo:dependency-edges",
                    serde_jcs::to_string(&sorted(details)?)?,
                ),
                property(
                    "heleos:sbom:component-origin",
                    if raw.contains_key(id) {
                        "cargo-cyclonedx-0.5.9"
                    } else {
                        "cargo-metadata-and-lock"
                    },
                ),
            ];
            if package["source"].is_null() {
                props.push(property(
                    "heleos:cargo:workspace-manifest",
                    relative_manifest(root, package)?,
                ));
                props.push(property("heleos:source:base-commit", BASE));
                props.push(property(
                    "heleos:source:workspace-inputs-sha256",
                    source_digest,
                ));
            } else {
                props.push(property("heleos:cargo:source", REGISTRY));
            }
            component["properties"] = json!(sorted(props)?);
            components.insert(url.to_owned(), component);
            let depends_on = strings(&node["dependencies"])?
                .iter()
                .map(|to| Ok(graph.url(to)?.to_owned()))
                .collect::<Result<BTreeSet<_>>>()?;
            dependencies.insert(url.to_owned(), json!({"ref":url,"dependsOn":depends_on}));
        }
        let members = graph
            .members
            .iter()
            .map(|id| Ok(graph.url(id)?.to_owned()))
            .collect::<Result<BTreeSet<_>>>()?;
        dependencies.insert(
            WORKSPACE.to_owned(),
            json!({"ref":WORKSPACE,"dependsOn":members}),
        );
        let bom = json!({"bomFormat":"CycloneDX","specVersion":"1.5","version":1,
            "metadata":{"timestamp":TIMESTAMP,"component":{"type":"application","bom-ref":WORKSPACE,"name":"heleos-foundation-workspace","version":"0.1.0"},
                "tools":[{"vendor":"CycloneDX","name":"cargo-cyclonedx","version":"0.5.9"},{"name":"heleos-verify-provenance-sbom","version":"0.1.0"}],
                "properties":sorted(vec![property("heleos:source:base-commit",BASE),property("heleos:source:epoch",EPOCH),
                    property("heleos:source:workspace-inputs-sha256",source_digest),property("heleos:source:cargo-lock-sha256",lock_digest),
                    property("heleos:sbom:coverage","all Cargo packages and resolved normal/build/dev dependency edges; all features; unfiltered target graph")])?},
            "components":components.into_values().collect::<Vec<_>>(),"dependencies":dependencies.into_values().collect::<Vec<_>>()});
        validate_output(graph, &bom)?;
        let mut bytes = serde_jcs::to_vec(&bom)?;
        bytes.push(b'\n');
        ensure(bytes.len() <= FILE_CAP, "SBOM output exceeds size bound")?;
        reject_paths(&bom)?;
        Ok(bytes)
    }

    fn validate_output(graph: &Graph, bom: &Value) -> Result<()> {
        let components = records(&bom["components"], "bom-ref")?;
        let refs = components.keys().cloned().collect::<BTreeSet<_>>();
        ensure(
            refs == graph.urls.values().cloned().collect(),
            "SBOM aggregate package coverage differs",
        )?;
        let dependencies = records(&bom["dependencies"], "ref")?;
        let mut expected_refs = refs.clone();
        expected_refs.insert(WORKSPACE.to_owned());
        ensure(
            dependencies.keys().cloned().collect::<BTreeSet<_>>() == expected_refs,
            "SBOM aggregate dependency-node coverage differs",
        )?;
        let mut edges = Edges::new();
        for (from, node) in &dependencies {
            let to = strings(&node["dependsOn"])?;
            ensure(to.is_subset(&refs), "SBOM aggregate dangling reference")?;
            if from == WORKSPACE {
                let members = graph
                    .members
                    .iter()
                    .map(|id| Ok(graph.url(id)?.to_owned()))
                    .collect::<Result<BTreeSet<_>>>()?;
                ensure(to == members, "SBOM aggregate membership edges differ")?;
            } else {
                for to in to {
                    edges.insert((from.clone(), to));
                }
            }
        }
        let expected = graph
            .edges
            .iter()
            .map(|(from, to)| Ok((graph.url(from)?.to_owned(), graph.url(to)?.to_owned())))
            .collect::<Result<Edges>>()?;
        ensure(edges == expected, "SBOM aggregate package edges differ")
    }

    fn reject_paths(value: &Value) -> Result<()> {
        match value {
            Value::String(text) => {
                if text.starts_with('[') || text.starts_with('{') {
                    reject_paths(&parse_json(text.as_bytes())?)?;
                } else {
                    ensure(
                        text == REGISTRY
                            || (!text.contains("file:")
                                && !text.contains('\\')
                                && !text.starts_with('/')
                                && !text.contains(":/")
                                && !text.contains("/Users/")
                                && !text.contains("/home/")
                                && !text.contains("/private/")),
                        "SBOM physical path leaked",
                    )?;
                }
            }
            Value::Array(values) => {
                for value in values {
                    reject_paths(value)?;
                }
            }
            Value::Object(values) => {
                for value in values.values() {
                    reject_paths(value)?;
                }
            }
            _ => {}
        }
        Ok(())
    }

    // Snapshot only source inputs consumed by Cargo and the verifier; artifacts,
    // Git metadata and governance reports are excluded to avoid self-reference.
    struct Snapshot {
        files: BTreeMap<String, Vec<u8>>,
    }

    impl Snapshot {
        fn read(root: &Path) -> Result<Self> {
            let mut names = BTreeSet::from(["Cargo.toml".to_owned(), "Cargo.lock".to_owned()]);
            inventory(root, &root.join("crates"), &mut names)?;
            inventory(root, &root.join("tests/verification"), &mut names)?;
            let mut total = 0usize;
            let mut files = BTreeMap::new();
            for name in names {
                ensure(
                    !name.contains("/.cargo/") && !name.ends_with(".cdx.json"),
                    "SBOM unexpected Cargo configuration or raw output in source",
                )?;
                let bytes = read_regular(&root.join(&name), FILE_CAP)?;
                total = total
                    .checked_add(bytes.len())
                    .ok_or("SBOM source size overflow")?;
                ensure(
                    total <= 256 * 1024 * 1024,
                    "SBOM source snapshot exceeds bound",
                )?;
                files.insert(name, bytes);
            }
            Ok(Self { files })
        }

        fn bytes(&self, name: &str) -> Result<&[u8]> {
            self.files
                .get(name)
                .map(Vec::as_slice)
                .ok_or_else(|| "SBOM source input missing".into())
        }

        fn digest(&self) -> Result<String> {
            let entries = self
                .files
                .iter()
                .map(|(path, bytes)| {
                    Ok(json!({"path":path,"length":bytes.len(),"sha256":hash(bytes)?}))
                })
                .collect::<Result<Vec<_>>>()?;
            let mut framed = b"heleos-sbom-workspace-inputs-v1\0".to_vec();
            framed.extend_from_slice(&serde_jcs::to_vec(&entries)?);
            hash(&framed)
        }

        fn copy_to(&self, destination: &Path) -> Result<()> {
            for (path, bytes) in &self.files {
                write_new(&destination.join(path), bytes)?;
            }
            Ok(())
        }

        fn recheck(&self, root: &Path, extra: &BTreeSet<String>) -> Result<()> {
            for (path, bytes) in &self.files {
                ensure(
                    read_regular(&root.join(path), FILE_CAP)? == *bytes,
                    "SBOM source input changed",
                )?;
            }
            let mut expected = self.files.keys().cloned().collect::<BTreeSet<_>>();
            expected.extend(extra.clone());
            let mut actual = BTreeSet::new();
            inventory(root, root, &mut actual)?;
            ensure(
                actual == expected,
                "SBOM copied workspace inventory changed",
            )
        }
    }

    struct Tools {
        rust: Toolchain,
        cyclonedx: PathBuf,
        tar: PathBuf,
        identities: BTreeMap<PathBuf, String>,
    }

    impl Tools {
        fn discover() -> Result<Self> {
            let rust = Toolchain::discover()?;
            let suffix = if cfg!(windows) { ".exe" } else { "" };
            let cyclonedx = rust
                .seed_cache
                .join("bin")
                .join(format!("cargo-cyclonedx{suffix}"));
            let tar = if let Some(system) = &rust.system_root {
                system.join("System32/tar.exe")
            } else {
                PathBuf::from("/usr/bin/tar")
            }
            .canonicalize()?;
            let mut identities = BTreeMap::new();
            for path in [
                &cyclonedx,
                &tar,
                &rust.sysroot.join("bin").join(format!("cargo{suffix}")),
                &rust.sysroot.join("bin").join(format!("rustc{suffix}")),
            ] {
                identities.insert(path.clone(), hash(&read_regular(path, 256 * 1024 * 1024)?)?);
            }
            let tools = Self {
                rust,
                cyclonedx,
                tar,
                identities,
            };
            let output = bounded(
                Command::new(&tools.cyclonedx).args(["cyclonedx", "--version"]),
                Duration::from_secs(30),
            )?;
            output.require_success("pinned cargo-cyclonedx version")?;
            ensure(
                std::str::from_utf8(&output.stdout)?.trim() == "cargo-cyclonedx-cyclonedx 0.5.9",
                "SBOM cargo-cyclonedx version mismatch",
            )?;
            Ok(tools)
        }

        fn recheck(&self) -> Result<()> {
            for (path, digest) in &self.identities {
                ensure(
                    hash(&read_regular(path, 256 * 1024 * 1024)?)? == *digest,
                    "SBOM tool executable changed",
                )?;
            }
            Ok(())
        }

        fn command(
            &self,
            executable: &Path,
            root: &Path,
            cache: &Path,
            target: &Path,
        ) -> Result<Command> {
            let temp = new_directory(&target.join(".heleos-tmp"))?;
            let mut paths = vec![self.rust.sysroot.join("bin")];
            if let Some(system) = &self.rust.system_root {
                paths.extend([system.join("System32"), system.clone()]);
            } else {
                paths.extend([PathBuf::from("/usr/bin"), PathBuf::from("/bin")]);
            }
            let mut command = Command::new(executable);
            command
                .current_dir(root)
                .env_clear()
                .env("PATH", std::env::join_paths(paths)?)
                .env("CARGO_HOME", cache)
                .env("CARGO_TARGET_DIR", target)
                .env("CARGO_NET_OFFLINE", "true")
                .env("CARGO_INCREMENTAL", "0")
                .env("CARGO_TERM_COLOR", "never")
                .env("RUSTUP_HOME", &self.rust.rustup)
                .env("RUSTUP_TOOLCHAIN", "1.96.1")
                .env("RUSTUP_AUTO_INSTALL", "0")
                .env("TMP", &temp)
                .env("TEMP", &temp)
                .env("TMPDIR", &temp)
                .env("LANG", "C")
                .env("LC_ALL", "C")
                .env("TZ", "UTC")
                .env("SOURCE_DATE_EPOCH", EPOCH);
            if let Some(system) = &self.rust.system_root {
                command.env("SystemRoot", system).env("WINDIR", system);
            }
            Ok(command)
        }

        fn metadata(
            &self,
            root: &Path,
            cache: &Path,
            target: &Path,
            platform: Option<&str>,
        ) -> Result<Value> {
            let mut command = self.command(&self.rust.cargo, root, cache, target)?;
            command.args([
                "+1.96.1",
                "metadata",
                "--frozen",
                "--offline",
                "--all-features",
                "--format-version",
                "1",
            ]);
            if let Some(platform) = platform {
                command.args(["--filter-platform", platform]);
            }
            let output = bounded(&mut command, Duration::from_secs(120))?;
            output.require_success("SBOM frozen offline metadata")?;
            parse_json(&output.stdout)
        }
    }

    fn sparse_path(name: &str) -> String {
        let name = name.to_ascii_lowercase();
        match name.len() {
            1 => format!("1/{name}"),
            2 => format!("2/{name}"),
            3 => format!("3/{}/{name}", &name[..1]),
            _ => format!("{}/{}/{name}", &name[..2], &name[2..4]),
        }
    }

    fn archive_path(p: &Value) -> Result<String> {
        Ok(format!(
            "registry/cache/{REGISTRY_DIRECTORY}/{}-{}.crate",
            string(&p["name"])?,
            string(&p["version"])?
        ))
    }

    fn copy_registry(
        tools: &Tools,
        locked: &Records,
        cache: &Path,
    ) -> Result<BTreeMap<String, String>> {
        let mut inputs = BTreeMap::<String, Option<String>>::new();
        inputs.insert(
            format!("registry/index/{REGISTRY_DIRECTORY}/config.json"),
            None,
        );
        for p in locked.values().filter(|p| !p["source"].is_null()) {
            inputs.insert(archive_path(p)?, Some(string(&p["checksum"])?.to_owned()));
            inputs.insert(
                format!(
                    "registry/index/{REGISTRY_DIRECTORY}/.cache/{}",
                    sparse_path(string(&p["name"])?)
                ),
                None,
            );
        }
        let mut snapshot = BTreeMap::new();
        for (path, checksum) in inputs {
            let bytes = read_regular(&tools.rust.seed_cache.join(&path), FILE_CAP)?;
            let digest = hash(&bytes)?;
            if let Some(checksum) = checksum {
                ensure(digest == checksum, "SBOM registry archive checksum drift")?;
            }
            if path.ends_with("/config.json") {
                let config = parse_json(&bytes)?;
                ensure(
                    config["dl"] == "https://static.crates.io/crates"
                        && config["api"] == "https://crates.io",
                    "SBOM registry download configuration drift",
                )?;
            }
            write_new(&cache.join(&path), &bytes)?;
            snapshot.insert(path, digest);
        }
        recheck_inputs(&tools.rust.seed_cache, &snapshot)?;
        Ok(snapshot)
    }

    fn recheck_inputs(root: &Path, snapshot: &BTreeMap<String, String>) -> Result<()> {
        for (path, digest) in snapshot {
            ensure(
                hash(&read_regular(&root.join(path), FILE_CAP)?)? == *digest,
                "SBOM registry input changed",
            )?;
        }
        Ok(())
    }

    fn validate_manifests(
        graph: &Graph,
        snapshot: &Snapshot,
        tools: &Tools,
        root: &Path,
        cache: &Path,
        target: &Path,
    ) -> Result<()> {
        let root_manifest = parse_toml(snapshot.bytes("Cargo.toml")?)?;
        for package in graph.packages.values() {
            let manifest_path = Path::new(string(&package["manifest_path"])?);
            let manifest_bytes = read_regular(manifest_path, FILE_CAP)?;
            let manifest = parse_toml(&manifest_bytes)?;
            let declared = &manifest["package"];
            ensure(
                declared["name"] == package["name"] && declared["version"] == package["version"],
                "SBOM source manifest package identity drift",
            )?;
            if package["source"].is_null() {
                ensure(
                    manifest_bytes == snapshot.bytes(&relative_manifest(root, package)?)?,
                    "SBOM local manifest differs from frozen source",
                )?;
                let declared_license = if declared["license"]["workspace"] == true {
                    &root_manifest["workspace"]["package"]["license"]
                } else {
                    &declared["license"]
                };
                ensure(
                    declared_license == &package["license"],
                    "SBOM workspace license drift",
                )?;
            } else {
                let basename = format!(
                    "{}-{}",
                    string(&package["name"])?,
                    string(&package["version"])?
                );
                ensure(
                    manifest_path
                        == cache.join(format!(
                            "registry/src/{REGISTRY_DIRECTORY}/{basename}/Cargo.toml"
                        )),
                    "SBOM metadata registry source escaped isolated cache",
                )?;
                let archive = cache.join(archive_path(package)?);
                ensure(
                    hash(&read_regular(&archive, FILE_CAP)?)? == string(graph.checksum(package)?)?,
                    "SBOM registry archive checksum drift",
                )?;
                let mut command = tools.command(&tools.tar, root, cache, target)?;
                command
                    .arg("-xOf")
                    .arg(&archive)
                    .arg(format!("{basename}/Cargo.toml"));
                let output = bounded(&mut command, Duration::from_secs(30))?;
                output.require_success("SBOM verified archive manifest read")?;
                ensure(
                    output.stdout == manifest_bytes && output.stderr.is_empty(),
                    "SBOM extracted manifest differs from locked archive",
                )?;
                ensure(
                    declared["license"] == package["license"],
                    "SBOM registry license drift",
                )?;
            }
        }
        Ok(())
    }

    fn build_one(snapshot: &Snapshot, tools: &Tools, base: &Path) -> Result<(Vec<u8>, Value)> {
        let root = new_directory(&base.join("workspace"))?;
        let cache = new_directory(&base.join("cargo-cache"))?;
        let target = new_directory(&base.join("target"))?;
        reject_configs(&root)?;
        snapshot.copy_to(&root)?;
        let locked = lock_records(snapshot.bytes("Cargo.lock")?)?;
        let seed = copy_registry(tools, &locked, &cache)?;
        let metadata = tools.metadata(&root, &cache, &target, None)?;
        let graph = Graph::parse(
            &metadata,
            snapshot.bytes("Cargo.lock")?,
            snapshot.bytes("Cargo.toml")?,
            &root,
        )?;
        ensure(
            graph.packages.len() == 405 && graph.members.len() == 7,
            "SBOM Foundation package/member count drift",
        )?;
        let mut targets = Vec::new();
        for platform in TARGETS {
            let metadata = tools.metadata(&root, &cache, &target, Some(platform))?;
            let mut counts = graph.validate_target(&metadata, &root)?;
            counts["target"] = json!(platform);
            targets.push(counts);
        }
        snapshot.recheck(&root, &BTreeSet::new())?;
        recheck_inputs(&cache, &seed)?;
        validate_manifests(&graph, snapshot, tools, &root, &cache, &target)?;
        // Only verified archives are copied from the seed. Cargo unpacks its
        // own sources here; the seed's extracted manifests are never trusted.
        let before = cache_snapshot(&cache)?;
        let mut command = tools.command(&tools.cyclonedx, &root, &cache, &target)?;
        command
            .args(["cyclonedx", "--manifest-path"])
            .arg(root.join("Cargo.toml"))
            .args([
                "--format",
                "json",
                "--spec-version",
                "1.5",
                "--all",
                "--all-features",
                "--target",
                "all",
                "--license-strict",
            ]);
        let output = bounded(&mut command, Duration::from_secs(180))?;
        output.require_success("SBOM pinned offline cargo-cyclonedx")?;
        // 0.5.9 emits warnings for legacy slash license declarations under
        // --license-strict. Exact named declarations are verified below; the
        // warning is neither license-policy approval nor grounds to drop them.
        ensure(
            cache_snapshot(&cache)? == before,
            "SBOM generator mutated registry inputs or sources",
        )?;
        let mut raw = Records::new();
        let mut licenses = Records::new();
        let mut generated = BTreeSet::new();
        for member in &graph.members {
            let package = record(&graph.packages, member)?;
            let relative = relative_manifest(&root, package)?;
            let directory = Path::new(&relative)
                .parent()
                .ok_or("SBOM member parent missing")?;
            let path = directory.join(format!("{}.cdx.json", string(&package["name"])?));
            let bom = parse_json(&read_regular(&root.join(&path), FILE_CAP)?)?;
            accept_raw(&graph, member, &bom, &mut raw, &mut licenses)?;
            generated.insert(
                path.to_str()
                    .ok_or("SBOM generated path encoding")?
                    .replace('\\', "/"),
            );
        }
        snapshot.recheck(&root, &generated)?;
        recheck_inputs(&tools.rust.seed_cache, &seed)?;
        tools.recheck()?;
        let bytes = assemble(
            &graph,
            &raw,
            &licenses,
            &root,
            &snapshot.digest()?,
            &hash(snapshot.bytes("Cargo.lock")?)?,
        )?;
        let stats = json!({"packages":graph.packages.len(),"registry_packages":graph.packages.len()-graph.members.len(),
            "package_edges":graph.edges.len(),"workspace_membership_edges":graph.members.len(),"dependency_nodes":graph.packages.len()+1,
            "generator_union_packages":raw.len(),"targets":targets});
        Ok((bytes, stats))
    }

    fn publish(destination: &Path, bytes: &[u8]) -> Result<()> {
        let parent = destination.parent().ok_or("SBOM output parent missing")?;
        direct_path(parent)?;
        ensure(
            !destination.try_exists()?,
            "SBOM output exists; publication never overwrites",
        )?;
        let mut staged = tempfile::NamedTempFile::new_in(parent)?;
        staged.write_all(bytes)?;
        staged.as_file().sync_all()?;
        staged
            .persist_noclobber(destination)
            .map_err(|error| error.error)?;
        ensure(
            read_regular(destination, FILE_CAP)? == bytes,
            "SBOM published output differs",
        )?;
        #[cfg(unix)]
        File::open(parent)?.sync_all()?;
        Ok(())
    }

    pub(super) fn run(args: &[String]) -> Result<String> {
        ensure(
            (1..=2).contains(&args.len()),
            "SBOM mode expects at most one artifact path",
        )?;
        let root = std::env::current_dir()?.canonicalize()?;
        verify_frozen_file(&root, "Cargo.lock", LOCK)?;
        verify_frozen_file(&root, "Cargo.toml", ROOT_MANIFEST)?;
        reject_configs(&root)?;
        let git = bounded(
            Command::new("git").current_dir(&root).args([
                "merge-base",
                "--is-ancestor",
                BASE,
                "HEAD",
            ]),
            Duration::from_secs(30),
        )?;
        git.require_success("SBOM source baseline ancestry")?;
        let destination = args
            .get(1)
            .map(PathBuf::from)
            .unwrap_or_else(|| root.join(DESTINATION));
        let destination = if destination.is_absolute() {
            destination
        } else {
            root.join(destination)
        };
        let parent = destination.parent().ok_or("SBOM output parent missing")?;
        // Resolve the existing parent before any generation; never create an
        // artifact directory or write into source inputs as a side effect.
        direct_path(parent)?;
        ensure(
            destination
                .file_name()
                .is_some_and(|n| n != "Cargo.toml" && n != "Cargo.lock")
                && !destination.starts_with(root.join("crates"))
                && !destination.starts_with(root.join("tests")),
            "SBOM destination overlaps source inputs",
        )?;
        if args[0] == "build-sbom" {
            ensure(
                !destination.try_exists()?,
                "SBOM output exists; publication never overwrites",
            )?;
        }
        let expected = if args[0] == "verify-sbom" {
            Some(read_regular(&destination, FILE_CAP)?)
        } else {
            None
        };
        let snapshot = Snapshot::read(&root)?;
        let tools = Tools::discover()?;
        let scratch = tempfile::Builder::new().prefix("heleos-sbom-").tempdir()?;
        let scratch_root = scratch.path().canonicalize()?;
        ensure(
            !scratch_root.starts_with(&root),
            "SBOM scratch must be outside the repository",
        )?;
        let first = build_one(&snapshot, &tools, &scratch_root.join("first/short"))?;
        let second = build_one(
            &snapshot,
            &tools,
            &scratch_root.join("second/distinct/longer/path"),
        )?;
        ensure(
            first == second,
            "SBOM independent source/cache roots produced different output",
        )?;
        ensure(
            Snapshot::read(&root)?.files == snapshot.files,
            "SBOM production source changed during generation",
        )?;
        if let Some(expected) = &expected {
            ensure(
                *expected == first.0,
                "SBOM artifact differs from complete deterministic candidate",
            )?;
        }
        // Cleanup failure is a failed build, and occurs before publication.
        scratch.close()?;
        tools.recheck()?;
        if expected.is_none() {
            publish(&destination, &first.0)?;
        } else {
            ensure(
                read_regular(&destination, FILE_CAP)? == first.0,
                "SBOM artifact changed during verification",
            )?;
        }
        let report = json!({"schema":"heleos.sbom-report/v1","status":"pass","mode":args[0],"builds_compared":2,
            "source_base_commit":BASE,"source_date_epoch":EPOCH,"workspace_inputs_sha256":snapshot.digest()?,
            "workspace_lock_sha256":hash(LOCK)?,"sbom_sha256":hash(&first.0)?,"graph":first.1});
        Ok(serde_jcs::to_string(&report)?)
    }

    #[cfg(test)]
    mod tests {
        use super::*;

        struct Fixture {
            root: PathBuf,
            manifest: Vec<u8>,
            lock: Vec<u8>,
            metadata: Value,
            raw: Value,
        }

        impl Fixture {
            fn new() -> Self {
                let root = if cfg!(windows) {
                    PathBuf::from(r"C:\sbom-fixture")
                } else {
                    PathBuf::from("/sbom-fixture")
                };
                let manifest = b"[workspace]\nmembers = [\"crates/local\"]\n".to_vec();
                let lock = format!("version = 4\n[[package]]\nname = \"local\"\nversion = \"0.1.0\"\ndependencies = [\"normal\", \"dev\"]\n[[package]]\nname = \"normal\"\nversion = \"1.0.0\"\nsource = \"{REGISTRY}\"\nchecksum = \"{}\"\n[[package]]\nname = \"dev\"\nversion = \"1.0.0\"\nsource = \"{REGISTRY}\"\nchecksum = \"{}\"\n", "a".repeat(64), "b".repeat(64)).into_bytes();
                let metadata = json!({"version":1,"workspace_root":root,"workspace_members":["local"],
                    "packages":[{"id":"local","name":"local","version":"0.1.0","source":null,"license":"MIT","manifest_path":root.join("crates/local/Cargo.toml")},
                        {"id":"normal","name":"normal","version":"1.0.0","source":REGISTRY,"license":"MIT","manifest_path":"unused"},
                        {"id":"dev","name":"dev","version":"1.0.0","source":REGISTRY,"license":"MIT","manifest_path":"unused"}],
                    "resolve":{"nodes":[{"id":"local","dependencies":["normal","dev"],"features":[],
                        "deps":[{"name":"normal","pkg":"normal","dep_kinds":[{"kind":null,"target":null}]},{"name":"dev","pkg":"dev","dep_kinds":[{"kind":"dev","target":null}]}]},
                        {"id":"normal","dependencies":[],"features":["std"],"deps":[]},{"id":"dev","dependencies":[],"features":[],"deps":[]}]}});
                let raw = json!({"bomFormat":"CycloneDX","specVersion":"1.5","version":1,
                    "metadata":{"timestamp":TIMESTAMP,"tools":generator_tools(),"component":{"bom-ref":"local","name":"local","version":"0.1.0","purl":"pkg:cargo/local@0.1.0?download_url=file://.","licenses":[{"expression":"MIT"}]}},
                    "components":[{"bom-ref":"normal","name":"normal","version":"1.0.0","purl":"pkg:cargo/normal@1.0.0","licenses":[{"expression":"MIT"}],"hashes":[{"alg":"SHA-256","content":"a".repeat(64)}]}],
                    "dependencies":[{"ref":"local","dependsOn":["normal"]},{"ref":"normal","dependsOn":[]}]});
                Self {
                    root,
                    manifest,
                    lock,
                    metadata,
                    raw,
                }
            }

            fn graph(&self) -> Result<Graph> {
                Graph::parse(&self.metadata, &self.lock, &self.manifest, &self.root)
            }

            fn assembled(&self) -> Result<Vec<u8>> {
                let graph = self.graph()?;
                let mut raw = Records::new();
                let mut licenses = Records::new();
                accept_raw(&graph, "local", &self.raw, &mut raw, &mut licenses)?;
                assemble(
                    &graph,
                    &raw,
                    &licenses,
                    &self.root,
                    &"c".repeat(64),
                    &hash(&self.lock)?,
                )
            }
        }

        #[test]
        fn complete_graph_restores_generator_omitted_dev_package_and_edge() {
            let fixture = Fixture::new();
            let bytes = fixture.assembled().unwrap();
            let bom = parse_json(&bytes).unwrap();
            assert_eq!(array(&bom["components"]).unwrap().len(), 3);
            let deps = records(&bom["dependencies"], "ref").unwrap();
            assert_eq!(
                strings(&deps["pkg:cargo/local@0.1.0"]["dependsOn"]).unwrap(),
                BTreeSet::from([
                    "pkg:cargo/normal@1.0.0".to_owned(),
                    "pkg:cargo/dev@1.0.0".to_owned()
                ])
            );
            assert_eq!(bytes.last(), Some(&b'\n'));
            let components = records(&bom["components"], "bom-ref").unwrap();
            assert_eq!(
                components["pkg:cargo/dev@1.0.0"]["hashes"][0]["content"],
                "b".repeat(64)
            );
        }

        #[test]
        fn missing_metadata_package_node_or_dev_edge_fails() {
            for case in 0..4 {
                let mut fixture = Fixture::new();
                match case {
                    0 => {
                        fixture.metadata["packages"].as_array_mut().unwrap().pop();
                    }
                    1 => {
                        fixture.metadata["resolve"]["nodes"]
                            .as_array_mut()
                            .unwrap()
                            .pop();
                    }
                    2 => {
                        fixture.metadata["resolve"]["nodes"][0]["dependencies"] = json!(["normal"]);
                        fixture.metadata["resolve"]["nodes"][0]["deps"]
                            .as_array_mut()
                            .unwrap()
                            .pop();
                    }
                    _ => {
                        fixture.metadata["workspace_members"] = json!(["local", "dev"]);
                    }
                }
                assert!(fixture.graph().is_err(), "case {case}");
            }
        }

        #[test]
        fn lock_source_checksum_and_duplicate_identity_fail_closed() {
            for case in 0..4 {
                let mut fixture = Fixture::new();
                let text = String::from_utf8(fixture.lock.clone()).unwrap();
                fixture.lock = match case {
                    0 => text
                        .replace(REGISTRY, "git+https://example.invalid/repository#deadbeef")
                        .into_bytes(),
                    1 => text.replace(&"a".repeat(64), &"A".repeat(64)).into_bytes(),
                    2 => format!("{text}\n[[package]]\nname = \"local\"\nversion = \"0.1.0\"\n")
                        .into_bytes(),
                    _ => text.replace(&"a".repeat(64), &"c".repeat(64)).into_bytes(),
                };
                assert!(fixture.assembled().is_err(), "case {case}");
            }
        }

        #[test]
        fn malformed_lock_source_qualifier_is_not_a_local_identity() {
            let mut fixture = Fixture::new();
            fixture.lock = String::from_utf8(fixture.lock)
                .unwrap()
                .replace(
                    "\"normal\", \"dev\"",
                    "\"normal 1.0.0 unbracketed\", \"dev\"",
                )
                .into_bytes();
            assert!(fixture.graph().is_err());
        }

        #[test]
        fn omitted_package_needs_an_identical_generator_license_choice() {
            let mut fixture = Fixture::new();
            fixture.metadata["packages"][2]["license"] = json!("GPL-3.0-only");
            assert!(fixture.assembled().is_err());
        }

        #[test]
        fn raw_component_edge_purl_license_epoch_and_duplicates_fail() {
            for case in 0..11 {
                let mut fixture = Fixture::new();
                match case {
                    0 => fixture.raw["components"] = json!([]),
                    1 => fixture.raw["dependencies"][0]["dependsOn"] = json!([]),
                    2 => fixture.raw["components"][0]["purl"] = json!("pkg:cargo/wrong@1.0.0"),
                    3 => {
                        fixture.raw["components"][0]["licenses"] =
                            json!([{"expression":"Apache-2.0"}])
                    }
                    4 => fixture.raw["metadata"]["timestamp"] = json!("2026-09-09T00:00:00Z"),
                    5 => fixture.raw["serialNumber"] = Value::Null,
                    6 => fixture.raw["dependencies"][0]["dependsOn"] = json!(["normal", "normal"]),
                    7 => {
                        let n = fixture.raw["dependencies"][0].clone();
                        fixture.raw["dependencies"].as_array_mut().unwrap().push(n);
                    }
                    8 => {
                        let c = fixture.raw["components"][0].clone();
                        fixture.raw["components"].as_array_mut().unwrap().push(c);
                    }
                    9 => {
                        fixture.raw["metadata"]["component"]["licenses"] =
                            json!([{"license":{"name":"MIT"}}])
                    }
                    _ => {
                        fixture.raw["metadata"]["component"]["purl"] =
                            json!("pkg:cargo/local@0.1.0?download_url=file://../wrong")
                    }
                }
                assert!(fixture.assembled().is_err(), "case {case}");
            }
        }

        #[test]
        fn metadata_alias_kind_and_feature_contract_is_checked() {
            for case in 0..4 {
                let mut fixture = Fixture::new();
                match case {
                    0 => {
                        fixture.metadata["resolve"]["nodes"][0]["deps"][0]["dep_kinds"] = json!([])
                    }
                    1 => {
                        fixture.metadata["resolve"]["nodes"][0]["deps"][0]["dep_kinds"][0]["kind"] =
                            json!("future")
                    }
                    2 => {
                        fixture.metadata["resolve"]["nodes"][1]["features"] = json!(["std", "std"])
                    }
                    _ => {
                        let dep = fixture.metadata["resolve"]["nodes"][0]["deps"][0].clone();
                        fixture.metadata["resolve"]["nodes"][0]["deps"]
                            .as_array_mut()
                            .unwrap()
                            .push(dep);
                    }
                }
                assert!(fixture.graph().is_err(), "case {case}");
            }
        }

        #[test]
        fn target_graph_is_only_admitted_as_an_exact_identity_subset() {
            let fixture = Fixture::new();
            let graph = fixture.graph().unwrap();
            assert!(
                graph
                    .validate_target(&fixture.metadata, &fixture.root)
                    .is_ok()
            );
            for case in 0..6 {
                let mut target = fixture.metadata.clone();
                match case {
                    0 => target["resolve"]["nodes"][0]["deps"][0]["pkg"] = json!("unknown"),
                    1 => target["resolve"]["nodes"][0]["deps"][0]["name"] = json!("renamed"),
                    2 => {
                        target["resolve"]["nodes"][0]["deps"][0]["dep_kinds"][0]["kind"] =
                            json!("build")
                    }
                    3 => target["resolve"]["nodes"][1]["features"] = json!(["unknown"]),
                    4 => {
                        target["packages"].as_array_mut().unwrap().pop();
                    }
                    _ => {
                        let kind = target["resolve"]["nodes"][0]["deps"][0]["dep_kinds"][0].clone();
                        target["resolve"]["nodes"][0]["deps"][0]["dep_kinds"]
                            .as_array_mut()
                            .unwrap()
                            .push(kind);
                    }
                }
                assert!(
                    graph.validate_target(&target, &fixture.root).is_err(),
                    "case {case}"
                );
            }
        }

        #[test]
        fn aggregate_rejects_missing_membership_and_dangling_edges() {
            let fixture = Fixture::new();
            let graph = fixture.graph().unwrap();
            let bom = parse_json(&fixture.assembled().unwrap()).unwrap();
            for case in 0..3 {
                let mut candidate = bom.clone();
                match case {
                    0 => {
                        candidate["components"].as_array_mut().unwrap().pop();
                    }
                    1 => candidate["dependencies"][0]["dependsOn"] = json!([]),
                    _ => candidate["dependencies"][1]["dependsOn"] = json!(["pkg:cargo/unknown@1"]),
                }
                assert!(validate_output(&graph, &candidate).is_err(), "case {case}");
            }
        }

        #[test]
        fn physical_roots_and_input_order_do_not_change_output() {
            let first = Fixture::new();
            let mut second = Fixture::new();
            second.root = second.root.join("different/longer/root");
            second.metadata["workspace_root"] = json!(second.root);
            second.metadata["packages"][0]["manifest_path"] =
                json!(second.root.join("crates/local/Cargo.toml"));
            second.metadata["packages"]
                .as_array_mut()
                .unwrap()
                .reverse();
            second.metadata["resolve"]["nodes"]
                .as_array_mut()
                .unwrap()
                .reverse();
            second.raw["dependencies"].as_array_mut().unwrap().reverse();
            assert_eq!(first.assembled().unwrap(), second.assembled().unwrap());
            for path in [
                "file:///tmp/source",
                r"C:\\source\\crate",
                "/Users/owner/source",
                "prefix /home/owner/source",
            ] {
                assert!(reject_paths(&json!({"property":path})).is_err());
            }
            assert!(reject_paths(&json!({"source":REGISTRY})).is_ok());
        }

        #[test]
        fn publication_is_atomic_no_clobber_and_has_no_partial_on_failure() {
            let scratch = tempfile::tempdir().unwrap();
            let root = scratch.path().canonicalize().unwrap();
            let path = root.join("bom.json");
            publish(&path, b"candidate\n").unwrap();
            assert!(publish(&path, b"different\n").is_err());
            assert_eq!(fs::read(&path).unwrap(), b"candidate\n");
            assert_eq!(fs::read_dir(&root).unwrap().count(), 1);
            assert!(publish(&root.join("missing/bom.json"), b"partial").is_err());
            assert!(!root.join("missing").exists());
            scratch.close().unwrap();
        }

        #[test]
        fn snapshot_digest_binds_paths_and_exact_bytes() {
            let mut first = Snapshot {
                files: BTreeMap::from([("Cargo.lock".to_owned(), b"abc".to_vec())]),
            };
            let original = first.digest().unwrap();
            first
                .files
                .insert("Cargo.lock".to_owned(), b"abcd".to_vec());
            assert_ne!(first.digest().unwrap(), original);
            first.files = BTreeMap::from([("Cargo.toml".to_owned(), b"abc".to_vec())]);
            assert_ne!(first.digest().unwrap(), original);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn governance_requires_each_authority_field() {
        let record = "[[source]]\nname = \"fixture\"\n";
        assert!(verify_governance(record, "source").is_err());
        for (_, kind, bytes) in GOVERNANCE {
            let result = verify_governance(std::str::from_utf8(bytes).unwrap(), kind);
            assert!(result.is_ok(), "{kind}: {result:?}");
        }
    }

    #[test]
    fn network_calls_are_checked_as_code_not_comments_or_strings() {
        let harmless =
            rust_code("// std::net::TcpStream\nlet s = r#\"tokio::net\"#; let c = '\"';").unwrap();
        assert!(reject_network_client(&harmless, "synthetic").is_ok());
        let client = rust_code("use std /* gap */ :: net :: { TcpStream };").unwrap();
        assert!(reject_network_client(&client, "synthetic").is_err());
        let capability = rust_code("builder . inherit_network ();").unwrap();
        assert!(reject_network_client(&capability, "synthetic").is_err());
    }

    #[test]
    fn secret_shapes_are_detected_without_matching_documentation_names() {
        assert!(!contains_secret(
            b"read environment variable API_TOKEN; ghp_ placeholder"
        ));
        assert!(contains_secret(
            format!("ghp_{}", "x".repeat(36)).as_bytes()
        ));
    }

    #[test]
    fn ignored_acceptance_attributes_survive_comment_and_literal_removal() {
        let code = rust_code("#[ignore = \"reason\"]\nfn acceptance() {} // #[test]").unwrap();
        assert!(code.contains("#[ignore"));
        assert!(
            rust_code(&["/* #[", "ignore", "] */ fn live() {}"].concat())
                .unwrap()
                .find("#[ignore")
                .is_none()
        );
    }
}
