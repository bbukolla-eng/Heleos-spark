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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn governance_requires_each_authority_field() {
        let record = "[[source]]\nname = \"fixture\"\n";
        assert!(verify_governance(record, "source").is_err());
        for (_, kind, bytes) in GOVERNANCE {
            assert!(verify_governance(std::str::from_utf8(bytes).unwrap(), kind).is_ok());
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
