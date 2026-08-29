#![forbid(unsafe_code)]

use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use thiserror::Error;

pub const PROTOCOL_VERSION: &str = "heleos.pdf-probe/v1";
pub const MAX_REQUEST_BYTES: usize = 16 * 1024;
pub const MAX_PROTOCOL_OUTPUT_BYTES: usize = 4 * 1024 * 1024;
pub const MAX_GUEST_WASM_BYTES: usize = 32 * 1024 * 1024;
pub const JCS_SAFE_INTEGER_MAX: u64 = 9_007_199_254_740_991;
pub const JCS_SAFE_INTEGER_MIN: i64 = -9_007_199_254_740_991;

pub type Result<T> = std::result::Result<T, ProtocolError>;

#[derive(Clone, Debug, Eq, Error, PartialEq)]
pub enum ProtocolError {
    #[error("invalid protocol document")]
    InvalidDocument,
    #[error("protocol size limit exceeded")]
    SizeLimit,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PdfDocumentLimitsV1 {
    pub max_input_bytes: u64,
    pub max_pages: u32,
    pub max_indirect_objects: u32,
    pub max_nested_references: u32,
    pub max_metadata_bytes: u64,
    pub max_page_axis_points: u32,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PdfRequestV1 {
    pub protocol: String,
    pub input_sha256: String,
    pub byte_length: u64,
    pub limits: PdfDocumentLimitsV1,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PdfResponseV1 {
    pub protocol: String,
    pub input_sha256: String,
    pub byte_length: u64,
    pub outcome: PdfGuestOutcomeV1,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(tag = "outcome", content = "value", rename_all = "snake_case")]
#[serde(deny_unknown_fields)]
pub enum PdfGuestOutcomeV1 {
    Accepted { pages: Vec<PdfPageV1> },
    Rejected { reason: PdfGuestReasonV1 },
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(tag = "kind", content = "detail", rename_all = "snake_case")]
#[serde(deny_unknown_fields)]
pub enum PdfGuestReasonV1 {
    BadMagic,
    Corrupt,
    Encrypted,
    InvalidGeometry,
    UnsupportedUserUnit,
    ActiveFeature(PdfActiveFeatureV1),
    LimitExceeded(PdfDocumentLimitV1),
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PdfActiveFeatureV1 {
    OpenAction,
    AdditionalActions,
    JavaScriptAbbreviation,
    JavaScript,
    Launch,
    Uri,
    GoToRemote,
    SubmitForm,
    ImportData,
    RichMedia,
    EmbeddedFiles,
    AssociatedFiles,
    Xfa,
    AcroForm,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum PdfDocumentLimitV1 {
    InputBytes,
    Pages,
    IndirectObjects,
    NestedReferences,
    MetadataBytes,
    PageAxisPoints,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PdfPageV1 {
    pub index: u32,
    pub page_id: String,
    pub width_micropoints: u64,
    pub height_micropoints: u64,
    pub unit: PageUnitV1,
    pub rotation_degrees: u16,
    pub transform: PageTransformV1,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
pub enum PageUnitV1 {
    #[serde(rename = "pt")]
    Point,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct PageTransformV1 {
    pub m11: i8,
    pub m12: i8,
    pub m21: i8,
    pub m22: i8,
    pub tx_micropoints: i64,
    pub ty_micropoints: i64,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct DependencyRecordV1 {
    pub id: String,
    pub features: Vec<String>,
    pub edges: Vec<DependencyEdgeV1>,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct DependencyEdgeV1 {
    pub kind: DependencyKindV1,
    pub id: String,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum DependencyKindV1 {
    Normal,
    Build,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ImportRecordV1 {
    pub module: String,
    pub name: String,
    pub kind: ImportKindV1,
    pub params: Vec<CoreValueTypeV1>,
    pub results: Vec<CoreValueTypeV1>,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ImportKindV1 {
    Func,
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CoreValueTypeV1 {
    I32,
    I64,
    F32,
    F64,
    V128,
    Funcref,
    Externref,
}

#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(tag = "kind", rename_all = "snake_case")]
#[serde(deny_unknown_fields)]
pub enum ExportRecordV1 {
    Func {
        name: String,
        params: Vec<CoreValueTypeV1>,
        results: Vec<CoreValueTypeV1>,
    },
    Memory {
        name: String,
        minimum_pages: u64,
        maximum_pages: Option<u64>,
        memory64: bool,
        shared: bool,
        page_size_log2: Option<u32>,
    },
    Table {
        name: String,
        element: CoreValueTypeV1,
        minimum_elements: u64,
        maximum_elements: Option<u64>,
        table64: bool,
    },
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub struct SourceFileV1 {
    pub path: String,
    pub content: Vec<u8>,
}

#[cfg(feature = "artifact-host")]
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ArtifactRegistryRootV1 {
    pub normalized_dependency_id: String,
    pub physical_root: std::path::PathBuf,
}

#[cfg(feature = "artifact-host")]
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct RegistryPackageIdentityV1 {
    pub name: String,
    pub version: String,
    pub checksum: String,
}

#[cfg(feature = "artifact-host")]
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ArtifactDependencyGraphV1 {
    pub records: Vec<DependencyRecordV1>,
    pub registry_roots: Vec<ArtifactRegistryRootV1>,
}

#[cfg(feature = "artifact-host")]
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct GuestResolutionSparseIndexEntryV1 {
    pub package_name: String,
    pub cargo_home_relative_path: String,
}

#[cfg(feature = "artifact-host")]
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct GuestResolutionArchiveV1 {
    pub normalized_dependency_id: String,
    pub cargo_home_relative_path: String,
    pub unpacked_source_cargo_home_relative_path: String,
    pub sha256: String,
}

#[cfg(feature = "artifact-host")]
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct GuestResolutionCachePlanV1 {
    pub sparse_config_cargo_home_relative_path: String,
    pub sparse_index_entries: Vec<GuestResolutionSparseIndexEntryV1>,
    pub resolution_archives: Vec<GuestResolutionArchiveV1>,
}

#[cfg(feature = "artifact-host")]
#[derive(Clone, Copy, Debug, Deserialize, Eq, Ord, PartialEq, PartialOrd, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum CargoTargetKindV1 {
    Lib,
    ProcMacro,
    CustomBuild,
    Bin,
}

#[cfg(feature = "artifact-host")]
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct CargoCompilerUnitV1 {
    pub normalized_dependency_id: String,
    pub target_name: String,
    pub target_kind: CargoTargetKindV1,
    pub crate_types: Vec<String>,
    pub features: Vec<String>,
    pub profile_test: bool,
}

#[cfg(feature = "artifact-host")]
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct CargoBuildScriptEvidenceV1 {
    pub normalized_dependency_id: String,
    pub cfgs: Vec<String>,
    pub env: Vec<(String, String)>,
    pub linked_libs: Vec<String>,
    pub linked_paths: Vec<String>,
}

#[cfg(feature = "artifact-host")]
#[derive(Clone, Debug, Deserialize, Eq, PartialEq, Serialize)]
#[serde(deny_unknown_fields)]
pub struct GuestBuildEvidenceV1 {
    pub guest_resolution_lock_sha256: String,
    pub guest_resolution_cache_plan_sha256: String,
    pub compiler_units: Vec<CargoCompilerUnitV1>,
    pub build_scripts: Vec<CargoBuildScriptEvidenceV1>,
}

#[cfg(feature = "artifact-host")]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct GuestResolutionFileV1 {
    pub relative_path: &'static str,
    pub source_tree_member: bool,
}

#[cfg(feature = "artifact-host")]
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub struct GuestResolutionWorkspaceSpecV1 {
    pub root_manifest_utf8_lf: &'static str,
    pub files: &'static [GuestResolutionFileV1],
}

#[cfg(feature = "artifact-host")]
const GUEST_RESOLUTION_FILES_V1: &[GuestResolutionFileV1] = &[
    GuestResolutionFileV1 {
        relative_path: "crates/heleos-pdf-guest/Cargo.toml",
        source_tree_member: true,
    },
    GuestResolutionFileV1 {
        relative_path: "crates/heleos-pdf-guest/src/lib.rs",
        source_tree_member: true,
    },
    GuestResolutionFileV1 {
        relative_path: "crates/heleos-pdf-guest/src/main.rs",
        source_tree_member: true,
    },
    GuestResolutionFileV1 {
        relative_path: "crates/heleos-pdf-protocol/Cargo.toml",
        source_tree_member: true,
    },
    GuestResolutionFileV1 {
        relative_path: "crates/heleos-pdf-protocol/src/lib.rs",
        source_tree_member: true,
    },
    GuestResolutionFileV1 {
        relative_path: "crates/heleos-test-fixtures/Cargo.toml",
        source_tree_member: true,
    },
    GuestResolutionFileV1 {
        relative_path: "crates/heleos-test-fixtures/src/lib.rs",
        source_tree_member: false,
    },
];

#[cfg(feature = "artifact-host")]
pub fn guest_resolution_workspace_spec_v1(
    production_workspace_toml: &[u8],
    guest_manifest_toml: &[u8],
    protocol_manifest_toml: &[u8],
    fixtures_manifest_toml: &[u8],
) -> Result<GuestResolutionWorkspaceSpecV1> {
    const ROOT_MANIFEST: &str = r#"[workspace]
resolver = "3"
members = [
  "crates/heleos-pdf-guest",
  "crates/heleos-pdf-protocol",
  "crates/heleos-test-fixtures",
]
default-members = ["crates/heleos-pdf-guest"]

[workspace.package]
edition = "2024"
rust-version = "1.96.1"
license = "LicenseRef-Proprietary"

[workspace.dependencies]
lopdf = { version = "=0.44.0", default-features = false }
serde = { version = "=1.0.229", features = ["derive"] }
serde_jcs = "=0.2.0"
serde_json = "=1.0.151"
sha2 = "=0.11.0"
thiserror = "=2.0.20"
toml = { version = "=1.1.4", default-features = false, features = ["parse", "serde", "std"] }
wasmparser = { version = "=0.254.0", default-features = false }
"#;
    const GUEST_MANIFEST: &str = r#"[package]
name = "heleos-pdf-guest"
version = "0.1.0"
edition.workspace = true
rust-version.workspace = true
license.workspace = true
build = false
autolib = false
autobins = false
autoexamples = false
autotests = false
autobenches = false

[lib]
path = "src/lib.rs"

[[bin]]
name = "heleos_pdf_guest"
path = "src/main.rs"

[dependencies]
heleos-pdf-protocol = { path = "../heleos-pdf-protocol" }
lopdf.workspace = true

[dev-dependencies]
heleos-test-fixtures = { path = "../heleos-test-fixtures" }
"#;
    const PROTOCOL_MANIFEST: &str = r#"[package]
name = "heleos-pdf-protocol"
version = "0.1.0"
edition.workspace = true
rust-version.workspace = true
license.workspace = true
build = false
autolib = false
autobins = false
autoexamples = false
autotests = false
autobenches = false

[lib]
path = "src/lib.rs"

[features]
default = []
artifact-host = ["dep:toml", "dep:wasmparser"]

[dependencies]
serde.workspace = true
serde_jcs.workspace = true
serde_json.workspace = true
sha2.workspace = true
thiserror.workspace = true
toml = { workspace = true, optional = true }
wasmparser = { workspace = true, optional = true }
"#;
    const FIXTURES_MANIFEST: &str = r#"[package]
name = "heleos-test-fixtures"
version = "0.1.0"
edition.workspace = true
rust-version.workspace = true
license.workspace = true
build = false
autolib = false
autobins = false
autoexamples = false
autotests = false
autobenches = false

[lib]
path = "src/lib.rs"

[dependencies]
heleos-pdf-protocol = { path = "../heleos-pdf-protocol" }
lopdf.workspace = true
"#;

    let production = parse_bounded_manifest(production_workspace_toml)?;
    validate_production_resolution_workspace(&production, ROOT_MANIFEST)?;
    for (actual, expected) in [
        (guest_manifest_toml, GUEST_MANIFEST),
        (protocol_manifest_toml, PROTOCOL_MANIFEST),
        (fixtures_manifest_toml, FIXTURES_MANIFEST),
    ] {
        if parse_bounded_manifest(actual)? != parse_bounded_manifest(expected.as_bytes())? {
            return Err(ProtocolError::InvalidDocument);
        }
    }
    Ok(GuestResolutionWorkspaceSpecV1 {
        root_manifest_utf8_lf: ROOT_MANIFEST,
        files: GUEST_RESOLUTION_FILES_V1,
    })
}

#[cfg(feature = "artifact-host")]
fn parse_bounded_manifest(bytes: &[u8]) -> Result<toml::Value> {
    const MAX_MANIFEST_BYTES: usize = 1024 * 1024;
    if bytes.is_empty() || bytes.len() > MAX_MANIFEST_BYTES || bytes.contains(&b'\r') {
        return Err(ProtocolError::SizeLimit);
    }
    let text = std::str::from_utf8(bytes).map_err(|_| ProtocolError::InvalidDocument)?;
    toml::from_str(text).map_err(|_| ProtocolError::InvalidDocument)
}

#[cfg(feature = "artifact-host")]
fn validate_production_resolution_workspace(
    production: &toml::Value,
    canonical_resolution_root: &str,
) -> Result<()> {
    let table = production
        .as_table()
        .ok_or(ProtocolError::InvalidDocument)?;
    if table.contains_key("patch") || table.contains_key("replace") {
        return Err(ProtocolError::InvalidDocument);
    }
    let workspace = table
        .get("workspace")
        .and_then(toml::Value::as_table)
        .ok_or(ProtocolError::InvalidDocument)?;
    if workspace.get("resolver").and_then(toml::Value::as_str) != Some("3") {
        return Err(ProtocolError::InvalidDocument);
    }
    let members = workspace
        .get("members")
        .and_then(toml::Value::as_array)
        .ok_or(ProtocolError::InvalidDocument)?;
    for expected in [
        "crates/heleos-pdf-guest",
        "crates/heleos-pdf-protocol",
        "crates/heleos-test-fixtures",
    ] {
        if members
            .iter()
            .filter_map(toml::Value::as_str)
            .filter(|member| *member == expected)
            .count()
            != 1
        {
            return Err(ProtocolError::InvalidDocument);
        }
    }
    let canonical = parse_bounded_manifest(canonical_resolution_root.as_bytes())?;
    let canonical_workspace = canonical
        .get("workspace")
        .and_then(toml::Value::as_table)
        .ok_or(ProtocolError::InvalidDocument)?;
    for key in ["package", "dependencies"] {
        let actual = workspace.get(key).ok_or(ProtocolError::InvalidDocument)?;
        let expected = canonical_workspace
            .get(key)
            .ok_or(ProtocolError::InvalidDocument)?;
        if key == "package" {
            if actual != expected {
                return Err(ProtocolError::InvalidDocument);
            }
        } else {
            let actual = actual.as_table().ok_or(ProtocolError::InvalidDocument)?;
            let expected = expected.as_table().ok_or(ProtocolError::InvalidDocument)?;
            for (dependency, expected_value) in expected {
                if actual.get(dependency) != Some(expected_value) {
                    return Err(ProtocolError::InvalidDocument);
                }
            }
        }
    }
    Ok(())
}

#[cfg(feature = "artifact-host")]
pub fn validate_guest_source_closure_v1(files: &[SourceFileV1]) -> Result<()> {
    use std::collections::BTreeMap;

    const MAX_SOURCE_FILE_BYTES: usize = 8 * 1024 * 1024;
    const MAX_SOURCE_TOTAL_BYTES: usize = 32 * 1024 * 1024;
    if files.len() != GUEST_RESOLUTION_FILES_V1.len() {
        return Err(ProtocolError::InvalidDocument);
    }
    let mut total = 0_usize;
    let mut by_path = BTreeMap::new();
    for file in files {
        if file.content.is_empty()
            || file.content.len() > MAX_SOURCE_FILE_BYTES
            || file.content.contains(&b'\r')
            || by_path
                .insert(file.path.as_str(), file.content.as_slice())
                .is_some()
        {
            return Err(ProtocolError::InvalidDocument);
        }
        total = total
            .checked_add(file.content.len())
            .ok_or(ProtocolError::SizeLimit)?;
        if total > MAX_SOURCE_TOTAL_BYTES {
            return Err(ProtocolError::SizeLimit);
        }
    }
    for expected in GUEST_RESOLUTION_FILES_V1 {
        by_path
            .get(expected.relative_path)
            .ok_or(ProtocolError::InvalidDocument)?;
    }
    if by_path.len() != GUEST_RESOLUTION_FILES_V1.len() {
        return Err(ProtocolError::InvalidDocument);
    }
    for path in [
        "crates/heleos-pdf-guest/src/lib.rs",
        "crates/heleos-pdf-guest/src/main.rs",
        "crates/heleos-pdf-protocol/src/lib.rs",
    ] {
        validate_bound_rust_source(
            by_path
                .get(path)
                .copied()
                .ok_or(ProtocolError::InvalidDocument)?,
        )?;
    }
    Ok(())
}

#[cfg(feature = "artifact-host")]
#[derive(Clone, Copy)]
enum RustSourceToken<'a> {
    Ident(&'a [u8]),
    Hash,
    Bang,
    Dollar,
    OpenBracket,
    CloseBracket,
    Other,
}

#[cfg(feature = "artifact-host")]
fn validate_bound_rust_source(source: &[u8]) -> Result<()> {
    let mut lexer = RustSourceLexer { source, cursor: 0 };
    let mut macro_rules_candidate = false;
    let mut macro_dollar = false;
    let mut synthesized_identifier = false;
    let mut attribute_prefix = 0_u8;
    let mut attribute_depth = 0_usize;
    while let Some(token) = lexer.next_token()? {
        if matches!(
            token,
            RustSourceToken::Ident(name)
                if matches!(name, b"include" | b"include_bytes" | b"include_str")
        ) {
            return Err(ProtocolError::InvalidDocument);
        }
        if (macro_rules_candidate || synthesized_identifier)
            && matches!(token, RustSourceToken::Bang)
        {
            return Err(ProtocolError::InvalidDocument);
        }
        synthesized_identifier = macro_dollar && matches!(token, RustSourceToken::Ident(_));
        macro_dollar = matches!(token, RustSourceToken::Dollar);
        macro_rules_candidate =
            matches!(token, RustSourceToken::Ident(name) if name == b"macro_rules");

        if attribute_depth > 0 {
            match token {
                RustSourceToken::Ident(b"path") | RustSourceToken::Dollar => {
                    return Err(ProtocolError::InvalidDocument);
                }
                RustSourceToken::OpenBracket => {
                    attribute_depth = attribute_depth
                        .checked_add(1)
                        .ok_or(ProtocolError::SizeLimit)?;
                }
                RustSourceToken::CloseBracket => attribute_depth -= 1,
                _ => {}
            }
            continue;
        }
        match (attribute_prefix, token) {
            (_, RustSourceToken::Hash) => attribute_prefix = 1,
            (1, RustSourceToken::Bang) => attribute_prefix = 2,
            (1 | 2, RustSourceToken::OpenBracket) => {
                attribute_prefix = 0;
                attribute_depth = 1;
            }
            _ => attribute_prefix = 0,
        }
    }
    if attribute_depth != 0 {
        return Err(ProtocolError::InvalidDocument);
    }
    Ok(())
}

#[cfg(feature = "artifact-host")]
struct RustSourceLexer<'a> {
    source: &'a [u8],
    cursor: usize,
}

#[cfg(feature = "artifact-host")]
impl<'a> RustSourceLexer<'a> {
    fn next_token(&mut self) -> Result<Option<RustSourceToken<'a>>> {
        loop {
            while self
                .source
                .get(self.cursor)
                .is_some_and(u8::is_ascii_whitespace)
            {
                self.cursor += 1;
            }
            if self.cursor == self.source.len() {
                return Ok(None);
            }
            if self.source.get(self.cursor..self.cursor + 2) == Some(b"//") {
                self.cursor += 2;
                while self.cursor < self.source.len() && self.source[self.cursor] != b'\n' {
                    self.cursor += 1;
                }
                continue;
            }
            if self.source.get(self.cursor..self.cursor + 2) == Some(b"/*") {
                self.skip_block_comment()?;
                continue;
            }
            break;
        }

        if let Some((quote, hashes)) = raw_string_start(self.source, self.cursor) {
            self.cursor = raw_string_end(self.source, quote, hashes)?;
            return Ok(Some(RustSourceToken::Other));
        }
        for prefix in [b"b\"".as_slice(), b"c\"".as_slice()] {
            if self.source.get(self.cursor..self.cursor + prefix.len()) == Some(prefix) {
                self.cursor = cooked_string_end(self.source, self.cursor + 1, b'\"')?;
                return Ok(Some(RustSourceToken::Other));
            }
        }
        if self.source[self.cursor] == b'\"' {
            self.cursor = cooked_string_end(self.source, self.cursor, b'\"')?;
            return Ok(Some(RustSourceToken::Other));
        }
        if self.source.get(self.cursor..self.cursor + 2) == Some(b"b'") {
            self.cursor = cooked_string_end(self.source, self.cursor + 1, b'\'')?;
            return Ok(Some(RustSourceToken::Other));
        }
        if self.source[self.cursor] == b'\''
            && let Some(end) = ordinary_char_end(self.source, self.cursor)
        {
            self.cursor = end;
            return Ok(Some(RustSourceToken::Other));
        }

        if self.source.get(self.cursor..self.cursor + 2) == Some(b"r#")
            && self
                .source
                .get(self.cursor + 2)
                .is_some_and(|byte| is_rust_ident_start(*byte))
        {
            self.cursor += 2;
            let start = self.cursor;
            self.cursor += 1;
            while self
                .source
                .get(self.cursor)
                .is_some_and(|byte| is_rust_ident_continue(*byte))
            {
                self.cursor += 1;
            }
            return Ok(Some(RustSourceToken::Ident(
                &self.source[start..self.cursor],
            )));
        }
        if is_rust_ident_start(self.source[self.cursor]) {
            let start = self.cursor;
            self.cursor += 1;
            while self
                .source
                .get(self.cursor)
                .is_some_and(|byte| is_rust_ident_continue(*byte))
            {
                self.cursor += 1;
            }
            return Ok(Some(RustSourceToken::Ident(
                &self.source[start..self.cursor],
            )));
        }

        let byte = self.source[self.cursor];
        if !byte.is_ascii() || (byte.is_ascii_control() && !byte.is_ascii_whitespace()) {
            return Err(ProtocolError::InvalidDocument);
        }
        self.cursor += 1;
        Ok(Some(match byte {
            b'#' => RustSourceToken::Hash,
            b'!' => RustSourceToken::Bang,
            b'$' => RustSourceToken::Dollar,
            b'[' => RustSourceToken::OpenBracket,
            b']' => RustSourceToken::CloseBracket,
            _ => RustSourceToken::Other,
        }))
    }

    fn skip_block_comment(&mut self) -> Result<()> {
        let mut depth = 1_usize;
        self.cursor += 2;
        while self.cursor < self.source.len() {
            match self.source.get(self.cursor..self.cursor + 2) {
                Some(b"/*") => {
                    depth = depth.checked_add(1).ok_or(ProtocolError::SizeLimit)?;
                    self.cursor += 2;
                }
                Some(b"*/") => {
                    depth -= 1;
                    self.cursor += 2;
                    if depth == 0 {
                        return Ok(());
                    }
                }
                _ => self.cursor += 1,
            }
        }
        Err(ProtocolError::InvalidDocument)
    }
}

#[cfg(feature = "artifact-host")]
const fn is_rust_ident_start(byte: u8) -> bool {
    byte.is_ascii_alphabetic() || byte == b'_'
}

#[cfg(feature = "artifact-host")]
const fn is_rust_ident_continue(byte: u8) -> bool {
    is_rust_ident_start(byte) || byte.is_ascii_digit()
}

#[cfg(feature = "artifact-host")]
fn raw_string_start(source: &[u8], cursor: usize) -> Option<(usize, usize)> {
    let mut quote = if source.get(cursor) == Some(&b'r') {
        cursor + 1
    } else if matches!(source.get(cursor), Some(b'b' | b'c'))
        && source.get(cursor + 1) == Some(&b'r')
    {
        cursor + 2
    } else {
        return None;
    };
    let hash_start = quote;
    while source.get(quote) == Some(&b'#') {
        quote += 1;
    }
    (source.get(quote) == Some(&b'\"')).then_some((quote, quote - hash_start))
}

#[cfg(feature = "artifact-host")]
fn raw_string_end(source: &[u8], quote: usize, hashes: usize) -> Result<usize> {
    let mut cursor = quote + 1;
    while cursor < source.len() {
        let end = cursor
            .checked_add(1)
            .and_then(|value| value.checked_add(hashes))
            .ok_or(ProtocolError::SizeLimit)?;
        if source[cursor] == b'\"'
            && source
                .get(cursor + 1..end)
                .is_some_and(|suffix| suffix.iter().all(|byte| *byte == b'#'))
        {
            return Ok(end);
        }
        cursor += 1;
    }
    Err(ProtocolError::InvalidDocument)
}

#[cfg(feature = "artifact-host")]
fn cooked_string_end(source: &[u8], quote: usize, delimiter: u8) -> Result<usize> {
    let mut cursor = quote + 1;
    while cursor < source.len() {
        match source[cursor] {
            b'\\' => {
                cursor = cursor.checked_add(2).ok_or(ProtocolError::SizeLimit)?;
            }
            byte if byte == delimiter => return Ok(cursor + 1),
            b'\n' | b'\r' if delimiter == b'\'' => {
                return Err(ProtocolError::InvalidDocument);
            }
            _ => cursor += 1,
        }
    }
    Err(ProtocolError::InvalidDocument)
}

#[cfg(feature = "artifact-host")]
fn ordinary_char_end(source: &[u8], quote: usize) -> Option<usize> {
    let content = quote.checked_add(1)?;
    let first = *source.get(content)?;
    let end = if first == b'\\' {
        escaped_char_end(source, content)?
    } else {
        if matches!(first, b'\'' | b'\n' | b'\r' | b'\\') {
            return None;
        }
        let width = match first {
            0x00..=0x7f => 1,
            0xc2..=0xdf => 2,
            0xe0..=0xef => 3,
            0xf0..=0xf4 => 4,
            _ => return None,
        };
        let end = content.checked_add(width)?;
        let scalar = std::str::from_utf8(source.get(content..end)?).ok()?;
        if scalar.chars().count() != 1 {
            return None;
        }
        end
    };
    (source.get(end) == Some(&b'\'')).then(|| end + 1)
}

#[cfg(feature = "artifact-host")]
fn escaped_char_end(source: &[u8], slash: usize) -> Option<usize> {
    let escape = *source.get(slash.checked_add(1)?)?;
    match escape {
        b'\\' | b'\'' | b'"' | b'n' | b'r' | b't' | b'0' => slash.checked_add(2),
        b'x' => {
            let first = *source.get(slash.checked_add(2)?)?;
            let second = *source.get(slash.checked_add(3)?)?;
            (first.is_ascii_hexdigit()
                && second.is_ascii_hexdigit()
                && first.to_ascii_lowercase() <= b'7')
                .then(|| slash + 4)
        }
        b'u' if source.get(slash.checked_add(2)?) == Some(&b'{') => {
            let mut cursor = slash.checked_add(3)?;
            let mut digits = 0_u8;
            let mut scalar = 0_u32;
            let mut previous_underscore = false;
            while let Some(byte) = source.get(cursor).copied() {
                if byte == b'}' {
                    if digits == 0 || previous_underscore || char::from_u32(scalar).is_none() {
                        return None;
                    }
                    return cursor.checked_add(1);
                }
                if byte == b'_' && digits > 0 && !previous_underscore {
                    previous_underscore = true;
                    cursor = cursor.checked_add(1)?;
                    continue;
                }
                let value = ascii_hex_value(byte)?;
                digits = digits.checked_add(1)?;
                if digits > 6 {
                    return None;
                }
                scalar = scalar.checked_mul(16)?.checked_add(value)?;
                previous_underscore = false;
                cursor = cursor.checked_add(1)?;
            }
            None
        }
        _ => None,
    }
}

#[cfg(feature = "artifact-host")]
fn ascii_hex_value(byte: u8) -> Option<u32> {
    match byte {
        b'0'..=b'9' => Some(u32::from(byte - b'0')),
        b'a'..=b'f' => Some(u32::from(byte - b'a') + 10),
        b'A'..=b'F' => Some(u32::from(byte - b'A') + 10),
        _ => None,
    }
}

#[cfg(feature = "artifact-host")]
pub fn validate_guest_resolution_lock_projection_v1(
    production_cargo_lock_v4: &[u8],
    guest_resolution_cargo_lock_v4: &[u8],
) -> Result<()> {
    let production = parse_cargo_lock_v4(production_cargo_lock_v4)?;
    let resolution = parse_cargo_lock_v4(guest_resolution_cargo_lock_v4)?;
    validate_guest_resolution_lock_projection(&production, &resolution)
}

#[cfg(feature = "artifact-host")]
fn validate_guest_resolution_lock_projection(
    production: &ParsedCargoLockV4,
    resolution: &ParsedCargoLockV4,
) -> Result<()> {
    for (identity, package) in &resolution.packages {
        let production_package = production
            .packages
            .get(identity)
            .ok_or(ProtocolError::InvalidDocument)?;
        if package.checksum != production_package.checksum {
            return Err(ProtocolError::InvalidDocument);
        }
        let resolution_targets = resolution
            .dependency_targets
            .get(identity)
            .ok_or(ProtocolError::InvalidDocument)?;
        let production_targets = production
            .dependency_targets
            .get(identity)
            .ok_or(ProtocolError::InvalidDocument)?;
        if !resolution_targets.is_subset(production_targets) {
            return Err(ProtocolError::InvalidDocument);
        }
    }
    Ok(())
}

#[cfg(feature = "artifact-host")]
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
struct CargoLockIdentity {
    name: String,
    version: String,
    source: Option<String>,
}

#[cfg(feature = "artifact-host")]
#[derive(Clone, Debug)]
struct CargoLockPackageV4 {
    checksum: Option<String>,
    dependencies: Vec<String>,
}

#[cfg(feature = "artifact-host")]
struct ParsedCargoLockV4 {
    packages: std::collections::BTreeMap<CargoLockIdentity, CargoLockPackageV4>,
    dependency_targets: std::collections::BTreeMap<
        CargoLockIdentity,
        std::collections::BTreeSet<CargoLockIdentity>,
    >,
}

#[cfg(feature = "artifact-host")]
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CargoLockDocumentV4 {
    version: u32,
    package: Vec<CargoLockPackageDocumentV4>,
}

#[cfg(feature = "artifact-host")]
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CargoLockPackageDocumentV4 {
    name: String,
    version: String,
    source: Option<String>,
    checksum: Option<String>,
    #[serde(default)]
    dependencies: Vec<String>,
    replace: Option<String>,
}

#[cfg(feature = "artifact-host")]
fn parse_cargo_lock_v4(bytes: &[u8]) -> Result<ParsedCargoLockV4> {
    use std::collections::BTreeMap;

    const MAX_LOCK_BYTES: usize = 16 * 1024 * 1024;
    const MAX_LOCK_PACKAGES: usize = 65_536;
    if bytes.is_empty() || bytes.len() > MAX_LOCK_BYTES {
        return Err(ProtocolError::SizeLimit);
    }
    let text = std::str::from_utf8(bytes).map_err(|_| ProtocolError::InvalidDocument)?;
    let document: CargoLockDocumentV4 =
        toml::from_str(text).map_err(|_| ProtocolError::InvalidDocument)?;
    if document.version != 4
        || document.package.is_empty()
        || document.package.len() > MAX_LOCK_PACKAGES
    {
        return Err(ProtocolError::InvalidDocument);
    }
    let mut packages = BTreeMap::new();
    for package in document.package {
        if package.name.is_empty()
            || package.version.is_empty()
            || package.replace.is_some()
            || package
                .name
                .bytes()
                .chain(package.version.bytes())
                .any(|byte| byte.is_ascii_control() || byte.is_ascii_whitespace())
            || package.source.as_ref().is_some_and(|source| {
                source.is_empty() || source.bytes().any(|byte| byte.is_ascii_control())
            })
            || package
                .checksum
                .as_ref()
                .is_some_and(|checksum| !is_lower_sha256(checksum))
            || (package.source.is_none() && package.checksum.is_some())
        {
            return Err(ProtocolError::InvalidDocument);
        }
        let identity = CargoLockIdentity {
            name: package.name,
            version: package.version,
            source: package.source,
        };
        let package = CargoLockPackageV4 {
            checksum: package.checksum,
            dependencies: package.dependencies,
        };
        if packages.insert(identity, package).is_some() {
            return Err(ProtocolError::InvalidDocument);
        }
    }
    let index = build_lock_dependency_index(&packages)?;
    let mut dependency_targets = BTreeMap::new();
    for (identity, package) in &packages {
        dependency_targets.insert(
            identity.clone(),
            resolve_lock_dependencies(package, &index)?,
        );
    }
    Ok(ParsedCargoLockV4 {
        packages,
        dependency_targets,
    })
}

#[cfg(feature = "artifact-host")]
type LockDependencyIndex<'a> = std::collections::BTreeMap<
    &'a str,
    std::collections::BTreeMap<
        &'a str,
        std::collections::BTreeMap<Option<&'a str>, &'a CargoLockIdentity>,
    >,
>;

#[cfg(feature = "artifact-host")]
fn build_lock_dependency_index(
    packages: &std::collections::BTreeMap<CargoLockIdentity, CargoLockPackageV4>,
) -> Result<LockDependencyIndex<'_>> {
    use std::collections::BTreeMap;

    let mut index = BTreeMap::new();
    for identity in packages.keys() {
        if index
            .entry(identity.name.as_str())
            .or_insert_with(BTreeMap::new)
            .entry(identity.version.as_str())
            .or_insert_with(BTreeMap::new)
            .insert(identity.source.as_deref(), identity)
            .is_some()
        {
            return Err(ProtocolError::InvalidDocument);
        }
    }
    Ok(index)
}

#[cfg(feature = "artifact-host")]
fn resolve_lock_dependencies(
    package: &CargoLockPackageV4,
    index: &LockDependencyIndex<'_>,
) -> Result<std::collections::BTreeSet<CargoLockIdentity>> {
    use std::collections::BTreeSet;

    let mut targets = BTreeSet::new();
    for dependency in &package.dependencies {
        let parsed = parse_lock_dependency(dependency)?;
        let versions = index
            .get(parsed.name)
            .ok_or(ProtocolError::InvalidDocument)?;
        let candidates = match parsed.version {
            Some(version) => versions
                .get(version)
                .ok_or(ProtocolError::InvalidDocument)?,
            None => {
                let mut candidates = versions.values();
                let candidates = candidates.next().ok_or(ProtocolError::InvalidDocument)?;
                if candidates.is_empty() || versions.len() != 1 {
                    return Err(ProtocolError::InvalidDocument);
                }
                candidates
            }
        };
        let selected = if let Some(source) = parsed.source {
            candidates
                .get(&Some(source))
                .copied()
                .ok_or(ProtocolError::InvalidDocument)?
        } else if let Some(source_less) = candidates.get(&None) {
            *source_less
        } else if candidates.len() == 1 {
            candidates
                .values()
                .next()
                .copied()
                .ok_or(ProtocolError::InvalidDocument)?
        } else {
            return Err(ProtocolError::InvalidDocument);
        };
        if !targets.insert(selected.clone()) {
            return Err(ProtocolError::InvalidDocument);
        }
    }
    Ok(targets)
}

#[cfg(feature = "artifact-host")]
struct ParsedLockDependency<'a> {
    name: &'a str,
    version: Option<&'a str>,
    source: Option<&'a str>,
}

#[cfg(feature = "artifact-host")]
fn parse_lock_dependency(value: &str) -> Result<ParsedLockDependency<'_>> {
    if value.is_empty()
        || value
            .chars()
            .any(|character| character.is_whitespace() && character != ' ')
        || value.starts_with(' ')
        || value.ends_with(' ')
        || value.contains("  ")
    {
        return Err(ProtocolError::InvalidDocument);
    }
    let (without_source, source) = if let Some(without_close) = value.strip_suffix(')') {
        let (prefix, source) = without_close
            .rsplit_once(" (")
            .ok_or(ProtocolError::InvalidDocument)?;
        if source.is_empty() || source.contains(['(', ')', ' ']) {
            return Err(ProtocolError::InvalidDocument);
        }
        (prefix, Some(source))
    } else {
        if value.contains(['(', ')']) {
            return Err(ProtocolError::InvalidDocument);
        }
        (value, None)
    };
    let (name, version) = match without_source.split_once(' ') {
        Some((name, version)) if !name.is_empty() && !version.is_empty() => (name, Some(version)),
        None => (without_source, None),
        Some(_) => return Err(ProtocolError::InvalidDocument),
    };
    if name.is_empty()
        || name.contains(['(', ')'])
        || version.is_some_and(|version| version.contains(['(', ')', ' ']))
        || (source.is_some() && version.is_none())
    {
        return Err(ProtocolError::InvalidDocument);
    }
    Ok(ParsedLockDependency {
        name,
        version,
        source,
    })
}

#[cfg(feature = "artifact-host")]
pub fn guest_resolution_cache_plan_v1(
    production_cargo_lock_v4: &[u8],
    guest_resolution_cargo_lock_v4: &[u8],
) -> Result<GuestResolutionCachePlanV1> {
    use std::collections::{BTreeMap, BTreeSet};

    const REGISTRY_SOURCE: &str = "registry+https://github.com/rust-lang/crates.io-index";
    const REGISTRY_NAMESPACE: &str = "index.crates.io-1949cf8c6b5b557f";

    let production = parse_cargo_lock_v4(production_cargo_lock_v4)?;
    let resolution = parse_cargo_lock_v4(guest_resolution_cargo_lock_v4)?;
    validate_guest_resolution_lock_projection(&production, &resolution)?;

    let expected_workspace = BTreeSet::from([
        ("heleos-pdf-guest", "0.1.0"),
        ("heleos-pdf-protocol", "0.1.0"),
        ("heleos-test-fixtures", "0.1.0"),
    ]);
    let actual_workspace = resolution
        .packages
        .keys()
        .filter(|identity| identity.source.is_none())
        .map(|identity| (identity.name.as_str(), identity.version.as_str()))
        .collect::<BTreeSet<_>>();
    if actual_workspace != expected_workspace {
        return Err(ProtocolError::InvalidDocument);
    }

    let mut sparse_entries_by_path = BTreeMap::new();
    let mut resolution_archives = Vec::new();
    for (identity, package) in &resolution.packages {
        let Some(source) = identity.source.as_deref() else {
            if package.checksum.is_some() {
                return Err(ProtocolError::InvalidDocument);
            }
            continue;
        };
        let checksum = package
            .checksum
            .as_deref()
            .filter(|checksum| is_lower_sha256(checksum))
            .ok_or(ProtocolError::InvalidDocument)?;
        if source != REGISTRY_SOURCE
            || !valid_registry_package_name(&identity.name)
            || !valid_registry_package_version(&identity.version)
        {
            return Err(ProtocolError::InvalidDocument);
        }
        let sparse_key = crates_io_sparse_key_v1(&identity.name)?;
        let sparse_path = format!("registry/index/{REGISTRY_NAMESPACE}/.cache/{sparse_key}");
        match sparse_entries_by_path.insert(sparse_path, identity.name.clone()) {
            None => {}
            Some(previous) if previous == identity.name => {}
            Some(_) => return Err(ProtocolError::InvalidDocument),
        }
        let stem = format!("{}-{}", identity.name, identity.version);
        resolution_archives.push(GuestResolutionArchiveV1 {
            normalized_dependency_id: format!(
                "registry:{}@{}#{checksum}",
                identity.name, identity.version
            ),
            cargo_home_relative_path: format!("registry/cache/{REGISTRY_NAMESPACE}/{stem}.crate"),
            unpacked_source_cargo_home_relative_path: format!(
                "registry/src/{REGISTRY_NAMESPACE}/{stem}"
            ),
            sha256: checksum.to_owned(),
        });
    }
    resolution_archives.sort_by(|left, right| {
        left.normalized_dependency_id
            .as_bytes()
            .cmp(right.normalized_dependency_id.as_bytes())
    });
    if resolution_archives.windows(2).any(|pair| {
        pair[0].normalized_dependency_id.as_bytes() >= pair[1].normalized_dependency_id.as_bytes()
    }) {
        return Err(ProtocolError::InvalidDocument);
    }
    let sparse_index_entries = sparse_entries_by_path
        .into_iter()
        .map(
            |(cargo_home_relative_path, package_name)| GuestResolutionSparseIndexEntryV1 {
                package_name,
                cargo_home_relative_path,
            },
        )
        .collect();
    let plan = GuestResolutionCachePlanV1 {
        sparse_config_cargo_home_relative_path: format!(
            "registry/index/{REGISTRY_NAMESPACE}/config.json"
        ),
        sparse_index_entries,
        resolution_archives,
    };
    validate_guest_resolution_cache_plan(&plan)?;
    Ok(plan)
}

#[cfg(feature = "artifact-host")]
pub fn guest_resolution_lock_sha256(guest_resolution_cargo_lock_v4: &[u8]) -> Result<String> {
    parse_cargo_lock_v4(guest_resolution_cargo_lock_v4)?;
    let mut hasher = Sha256::new();
    hasher.update(b"heleos-pdf-guest-resolution-lock-v1\0");
    hasher.update(guest_resolution_cargo_lock_v4);
    Ok(lower_hex(&hasher.finalize()))
}

#[cfg(feature = "artifact-host")]
pub fn guest_resolution_cache_plan_sha256(plan: &GuestResolutionCachePlanV1) -> Result<String> {
    validate_guest_resolution_cache_plan(plan)?;
    let encoded = serde_jcs::to_vec(plan).map_err(|_| ProtocolError::InvalidDocument)?;
    if encoded.len() > MAX_CACHE_PLAN_ENCODED_BYTES {
        return Err(ProtocolError::SizeLimit);
    }
    let mut hasher = Sha256::new();
    hasher.update(b"heleos-pdf-guest-resolution-cache-plan-v1\0");
    hasher.update(encoded);
    Ok(lower_hex(&hasher.finalize()))
}

#[cfg(feature = "artifact-host")]
fn valid_registry_package_name(value: &str) -> bool {
    !value.is_empty()
        && value.bytes().all(|byte| {
            byte.is_ascii_lowercase() || byte.is_ascii_digit() || matches!(byte, b'_' | b'-')
        })
}

#[cfg(feature = "artifact-host")]
fn valid_registry_package_version(value: &str) -> bool {
    !value.is_empty()
        && value.is_ascii()
        && !value.starts_with('.')
        && !value.ends_with('.')
        && !value.contains("..")
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'+' | b'_' | b'-'))
}

#[cfg(feature = "artifact-host")]
fn crates_io_sparse_key_v1(name: &str) -> Result<String> {
    if !valid_registry_package_name(name) {
        return Err(ProtocolError::InvalidDocument);
    }
    Ok(match name.len() {
        1 => format!("1/{name}"),
        2 => format!("2/{name}"),
        3 => format!("3/{}/{name}", &name[..1]),
        _ => format!("{}/{}/{name}", &name[..2], &name[2..4]),
    })
}

#[cfg(feature = "artifact-host")]
fn validate_guest_resolution_cache_plan(plan: &GuestResolutionCachePlanV1) -> Result<()> {
    use std::collections::BTreeSet;

    validate_guest_resolution_cache_plan_bounds(plan)?;
    const CONFIG: &str = "registry/index/index.crates.io-1949cf8c6b5b557f/config.json";
    if plan.sparse_config_cargo_home_relative_path != CONFIG {
        return Err(ProtocolError::InvalidDocument);
    }
    let mut paths = BTreeSet::new();
    insert_cache_plan_path(&mut paths, CONFIG)?;
    let mut prior_sparse: Option<(&[u8], &[u8])> = None;
    let mut names = BTreeSet::new();
    for entry in &plan.sparse_index_entries {
        if !valid_registry_package_name(&entry.package_name)
            || entry.cargo_home_relative_path
                != format!(
                    "registry/index/index.crates.io-1949cf8c6b5b557f/.cache/{}",
                    crates_io_sparse_key_v1(&entry.package_name)?
                )
        {
            return Err(ProtocolError::InvalidDocument);
        }
        let key = (
            entry.cargo_home_relative_path.as_bytes(),
            entry.package_name.as_bytes(),
        );
        if prior_sparse.is_some_and(|prior| prior >= key)
            || !names.insert(entry.package_name.clone())
        {
            return Err(ProtocolError::InvalidDocument);
        }
        prior_sparse = Some(key);
        insert_cache_plan_path(&mut paths, &entry.cargo_home_relative_path)?;
    }

    let mut prior_id: Option<&[u8]> = None;
    let mut archive_names = BTreeSet::new();
    for archive in &plan.resolution_archives {
        let identity = parse_registry_dependency_id_v1(&archive.normalized_dependency_id)?;
        if !valid_registry_package_name(&identity.name)
            || !valid_registry_package_version(&identity.version)
            || archive.sha256 != identity.checksum
        {
            return Err(ProtocolError::InvalidDocument);
        }
        let stem = format!("{}-{}", identity.name, identity.version);
        if archive.cargo_home_relative_path
            != format!("registry/cache/index.crates.io-1949cf8c6b5b557f/{stem}.crate")
            || archive.unpacked_source_cargo_home_relative_path
                != format!("registry/src/index.crates.io-1949cf8c6b5b557f/{stem}")
            || prior_id.is_some_and(|prior| prior >= archive.normalized_dependency_id.as_bytes())
        {
            return Err(ProtocolError::InvalidDocument);
        }
        archive_names.insert(identity.name);
        prior_id = Some(archive.normalized_dependency_id.as_bytes());
        insert_cache_plan_path(&mut paths, &archive.cargo_home_relative_path)?;
        insert_cache_plan_path(
            &mut paths,
            &archive.unpacked_source_cargo_home_relative_path,
        )?;
    }
    if names != archive_names {
        return Err(ProtocolError::InvalidDocument);
    }
    Ok(())
}

#[cfg(feature = "artifact-host")]
const MAX_CACHE_PLAN_ENTRIES: usize = 65_536;
#[cfg(feature = "artifact-host")]
const MAX_CACHE_PLAN_STRING_BYTES: usize = 16 * 1024 * 1024;
#[cfg(feature = "artifact-host")]
const MAX_CACHE_PLAN_TOTAL_STRING_BYTES: usize = 32 * 1024 * 1024;
#[cfg(feature = "artifact-host")]
const MAX_CACHE_PLAN_ENCODED_BYTES: usize = 64 * 1024 * 1024;

#[cfg(feature = "artifact-host")]
fn validate_guest_resolution_cache_plan_bounds(plan: &GuestResolutionCachePlanV1) -> Result<()> {
    if plan.sparse_index_entries.len() > MAX_CACHE_PLAN_ENTRIES
        || plan.resolution_archives.len() > MAX_CACHE_PLAN_ENTRIES
    {
        return Err(ProtocolError::SizeLimit);
    }

    let mut total_string_bytes = 0_usize;
    let mut encoded_bytes = 2_usize;
    add_cache_plan_object_field_len(
        &mut encoded_bytes,
        "resolution_archives",
        cache_plan_array_encoded_len(
            plan.resolution_archives
                .iter()
                .map(|archive| cache_plan_archive_encoded_len(archive, &mut total_string_bytes)),
        )?,
        false,
    )?;
    add_cache_plan_object_field_len(
        &mut encoded_bytes,
        "sparse_config_cargo_home_relative_path",
        cache_plan_string_encoded_len_and_account(
            &plan.sparse_config_cargo_home_relative_path,
            &mut total_string_bytes,
        )?,
        true,
    )?;
    let sparse_encoded = cache_plan_array_encoded_len(
        plan.sparse_index_entries
            .iter()
            .map(|entry| cache_plan_sparse_entry_encoded_len(entry, &mut total_string_bytes)),
    )?;
    add_cache_plan_object_field_len(
        &mut encoded_bytes,
        "sparse_index_entries",
        sparse_encoded,
        true,
    )?;
    if total_string_bytes > MAX_CACHE_PLAN_TOTAL_STRING_BYTES
        || encoded_bytes > MAX_CACHE_PLAN_ENCODED_BYTES
    {
        return Err(ProtocolError::SizeLimit);
    }
    Ok(())
}

#[cfg(feature = "artifact-host")]
fn cache_plan_sparse_entry_encoded_len(
    entry: &GuestResolutionSparseIndexEntryV1,
    total_string_bytes: &mut usize,
) -> Result<usize> {
    let mut encoded = 2_usize;
    add_cache_plan_object_field_len(
        &mut encoded,
        "cargo_home_relative_path",
        cache_plan_string_encoded_len_and_account(
            &entry.cargo_home_relative_path,
            total_string_bytes,
        )?,
        false,
    )?;
    add_cache_plan_object_field_len(
        &mut encoded,
        "package_name",
        cache_plan_string_encoded_len_and_account(&entry.package_name, total_string_bytes)?,
        true,
    )?;
    Ok(encoded)
}

#[cfg(feature = "artifact-host")]
fn cache_plan_archive_encoded_len(
    archive: &GuestResolutionArchiveV1,
    total_string_bytes: &mut usize,
) -> Result<usize> {
    let mut encoded = 2_usize;
    for (index, (name, value)) in [
        (
            "cargo_home_relative_path",
            archive.cargo_home_relative_path.as_str(),
        ),
        (
            "normalized_dependency_id",
            archive.normalized_dependency_id.as_str(),
        ),
        ("sha256", archive.sha256.as_str()),
        (
            "unpacked_source_cargo_home_relative_path",
            archive.unpacked_source_cargo_home_relative_path.as_str(),
        ),
    ]
    .into_iter()
    .enumerate()
    {
        add_cache_plan_object_field_len(
            &mut encoded,
            name,
            cache_plan_string_encoded_len_and_account(value, total_string_bytes)?,
            index != 0,
        )?;
    }
    Ok(encoded)
}

#[cfg(feature = "artifact-host")]
fn cache_plan_array_encoded_len(values: impl IntoIterator<Item = Result<usize>>) -> Result<usize> {
    let mut encoded = 2_usize;
    let mut first = true;
    for value in values {
        if !first {
            encoded = encoded.checked_add(1).ok_or(ProtocolError::SizeLimit)?;
        }
        first = false;
        encoded = encoded
            .checked_add(value?)
            .ok_or(ProtocolError::SizeLimit)?;
        if encoded > MAX_CACHE_PLAN_ENCODED_BYTES {
            return Err(ProtocolError::SizeLimit);
        }
    }
    Ok(encoded)
}

#[cfg(feature = "artifact-host")]
fn add_cache_plan_object_field_len(
    encoded: &mut usize,
    name: &str,
    value_len: usize,
    has_preceding_field: bool,
) -> Result<()> {
    let separator = usize::from(has_preceding_field);
    let name_len = cache_plan_string_encoded_len(name).ok_or(ProtocolError::SizeLimit)?;
    *encoded = encoded
        .checked_add(separator)
        .and_then(|value| value.checked_add(name_len))
        .and_then(|value| value.checked_add(1))
        .and_then(|value| value.checked_add(value_len))
        .ok_or(ProtocolError::SizeLimit)?;
    if *encoded > MAX_CACHE_PLAN_ENCODED_BYTES {
        return Err(ProtocolError::SizeLimit);
    }
    Ok(())
}

#[cfg(feature = "artifact-host")]
fn cache_plan_string_encoded_len_and_account(
    value: &str,
    total_string_bytes: &mut usize,
) -> Result<usize> {
    if value.len() > MAX_CACHE_PLAN_STRING_BYTES {
        return Err(ProtocolError::SizeLimit);
    }
    *total_string_bytes = total_string_bytes
        .checked_add(value.len())
        .ok_or(ProtocolError::SizeLimit)?;
    if *total_string_bytes > MAX_CACHE_PLAN_TOTAL_STRING_BYTES {
        return Err(ProtocolError::SizeLimit);
    }
    cache_plan_string_encoded_len(value).ok_or(ProtocolError::SizeLimit)
}

#[cfg(feature = "artifact-host")]
fn cache_plan_string_encoded_len(value: &str) -> Option<usize> {
    let mut encoded = 2_usize;
    for character in value.chars() {
        let width = match character {
            '"' | '\\' | '\u{0008}' | '\t' | '\n' | '\u{000c}' | '\r' => 2,
            '\u{0000}'..='\u{001f}' => 6,
            _ => character.len_utf8(),
        };
        encoded = encoded.checked_add(width)?;
    }
    Some(encoded)
}

#[cfg(feature = "artifact-host")]
fn insert_cache_plan_path(
    paths: &mut std::collections::BTreeSet<String>,
    path: &str,
) -> Result<()> {
    if path.is_empty()
        || !path.is_ascii()
        || path.starts_with('/')
        || path.contains('\\')
        || path
            .split('/')
            .any(|component| component.is_empty() || matches!(component, "." | ".."))
        || !paths.insert(path.to_ascii_lowercase())
    {
        return Err(ProtocolError::InvalidDocument);
    }
    Ok(())
}

#[cfg(feature = "artifact-host")]
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ArtifactBuildInputsV1 {
    pub cargo_proxy_invocation: std::path::PathBuf,
    pub cargo_resolved_identity: std::path::PathBuf,
    pub workspace_root: std::path::PathBuf,
    pub cargo_home: std::path::PathBuf,
    pub target_root: std::path::PathBuf,
    pub rustc_sysroot: std::path::PathBuf,
    pub rustup_home: std::path::PathBuf,
    pub registry_roots: Vec<ArtifactRegistryRootV1>,
    pub system_root: Option<std::path::PathBuf>,
}

#[cfg(feature = "artifact-host")]
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ArtifactEnvironmentV1 {
    name: String,
    value: String,
}

#[cfg(feature = "artifact-host")]
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ArtifactRemapV1 {
    physical: String,
    virtual_prefix: String,
}

#[cfg(feature = "artifact-host")]
#[derive(Clone, Debug, Eq, PartialEq)]
pub struct ArtifactBuildPolicyV1 {
    cargo_proxy_invocation: std::path::PathBuf,
    cargo_resolved_identity: std::path::PathBuf,
    working_directory: std::path::PathBuf,
    argv: Vec<String>,
    env_clear: bool,
    environment: Vec<ArtifactEnvironmentV1>,
    remap_mappings: Vec<ArtifactRemapV1>,
    scan_prefixes: Vec<Vec<u8>>,
}

#[cfg(feature = "artifact-host")]
impl ArtifactEnvironmentV1 {
    pub fn name(&self) -> &str {
        &self.name
    }

    pub fn value(&self) -> &str {
        &self.value
    }
}

#[cfg(feature = "artifact-host")]
impl ArtifactRemapV1 {
    pub fn physical(&self) -> &str {
        &self.physical
    }

    pub fn virtual_prefix(&self) -> &str {
        &self.virtual_prefix
    }
}

#[cfg(feature = "artifact-host")]
impl ArtifactBuildPolicyV1 {
    pub fn cargo_proxy_invocation(&self) -> &std::path::Path {
        &self.cargo_proxy_invocation
    }

    pub fn cargo_resolved_identity(&self) -> &std::path::Path {
        &self.cargo_resolved_identity
    }

    pub fn working_directory(&self) -> &std::path::Path {
        &self.working_directory
    }

    pub fn argv(&self) -> &[String] {
        &self.argv
    }

    pub const fn env_clear(&self) -> bool {
        self.env_clear
    }

    pub fn environment(&self) -> &[ArtifactEnvironmentV1] {
        &self.environment
    }

    pub fn remap_mappings(&self) -> &[ArtifactRemapV1] {
        &self.remap_mappings
    }
}

#[cfg(feature = "artifact-host")]
pub fn artifact_build_policy_v1(inputs: &ArtifactBuildInputsV1) -> Result<ArtifactBuildPolicyV1> {
    use std::collections::{BTreeMap, BTreeSet};
    use std::path::Path;

    #[derive(Clone)]
    struct Mapping {
        physical: String,
        virtual_prefix: String,
        component_count: usize,
        kind_ordinal: u8,
        registry_id: String,
    }

    validated_absolute_path(&inputs.cargo_proxy_invocation)?;
    let expected_proxy_name = if cfg!(windows) { "cargo.exe" } else { "cargo" };
    if inputs
        .cargo_proxy_invocation
        .file_name()
        .and_then(|name| name.to_str())
        != Some(expected_proxy_name)
    {
        return Err(ProtocolError::InvalidDocument);
    }
    validated_absolute_path(&inputs.cargo_resolved_identity)?;
    let workspace = validated_absolute_path(&inputs.workspace_root)?;
    let cargo_home = validated_absolute_path(&inputs.cargo_home)?;
    let target = validated_absolute_path(&inputs.target_root)?;
    let rustc_sysroot = validated_absolute_path(&inputs.rustc_sysroot)?;
    let rustup_home = validated_absolute_path(&inputs.rustup_home)?;

    #[cfg(unix)]
    if inputs.system_root.is_some() {
        return Err(ProtocolError::InvalidDocument);
    }
    #[cfg(windows)]
    if inputs.system_root.is_none() {
        return Err(ProtocolError::InvalidDocument);
    }

    let system_root = inputs
        .system_root
        .as_ref()
        .map(|path| validated_absolute_path(path))
        .transpose()?;

    let mut roots_by_comparison = BTreeMap::<String, String>::new();
    for (role, physical) in [
        ("workspace", workspace),
        ("cargo", cargo_home),
        ("target", target),
        ("rust", rustc_sysroot),
        ("rustup", rustup_home),
    ] {
        insert_distinct_physical_role(&mut roots_by_comparison, role, physical)?;
    }
    if let Some(root) = system_root {
        insert_distinct_physical_role(&mut roots_by_comparison, "system", root)?;
    }

    let mut registry_ids = BTreeSet::new();
    let mut mappings = vec![
        mapping(rustc_sysroot, "/heleos/rust", 0, "")?,
        mapping(cargo_home, "/heleos/cargo", 1, "")?,
        mapping(workspace, "/heleos/workspace", 2, "")?,
        mapping(target, "/heleos/target", 3, "")?,
    ];
    let mut scan_paths = vec![workspace, cargo_home, target, rustc_sysroot, rustup_home];
    if let Some(root) = system_root {
        scan_paths.push(root);
    }
    for registry in &inputs.registry_roots {
        validate_normalized_registry_id(&registry.normalized_dependency_id)?;
        if !registry_ids.insert(registry.normalized_dependency_id.as_str()) {
            return Err(ProtocolError::InvalidDocument);
        }
        let physical = validated_absolute_path(&registry.physical_root)?;
        insert_distinct_physical_role(
            &mut roots_by_comparison,
            &format!("registry:{}", registry.normalized_dependency_id),
            physical,
        )?;
        let virtual_prefix = format!(
            "/heleos/registry/{}",
            lower_hex(&Sha256::digest(
                registry.normalized_dependency_id.as_bytes()
            ))
        );
        mappings.push(mapping(
            physical,
            &virtual_prefix,
            4,
            &registry.normalized_dependency_id,
        )?);
        scan_paths.push(physical);
    }

    mappings.sort_by(|left, right| {
        left.component_count
            .cmp(&right.component_count)
            .then_with(|| left.physical.len().cmp(&right.physical.len()))
            .then_with(|| left.physical.as_bytes().cmp(right.physical.as_bytes()))
            .then_with(|| left.kind_ordinal.cmp(&right.kind_ordinal))
            .then_with(|| {
                left.registry_id
                    .as_bytes()
                    .cmp(right.registry_id.as_bytes())
            })
    });
    if mappings.windows(2).any(|pair| {
        pair[0].component_count == pair[1].component_count
            && pair[0].physical.len() == pair[1].physical.len()
            && pair[0].physical.as_bytes() == pair[1].physical.as_bytes()
    }) {
        return Err(ProtocolError::InvalidDocument);
    }

    let mut rustflags = vec!["--remap-path-scope=object".to_owned()];
    for item in &mappings {
        rustflags.push("--remap-path-prefix".to_owned());
        rustflags.push(format!("{}={}", item.physical, item.virtual_prefix));
    }
    let encoded_rustflags = rustflags.join("\u{1f}");
    let rust_bin = path_join_utf8(&inputs.rustc_sysroot, "bin")?;
    let temp_dir = path_join_utf8(&inputs.target_root, ".heleos-tmp")?;
    let path_value = if cfg!(windows) {
        let root = system_root.ok_or(ProtocolError::InvalidDocument)?;
        let system32 = path_join_utf8(Path::new(root), "System32")?;
        join_path_list(&[&rust_bin, &system32, root], ';')?
    } else {
        join_path_list(&[&rust_bin, "/usr/bin", "/bin"], ':')?
    };

    let mut environment_vars = vec![
        environment("CARGO_ENCODED_RUSTFLAGS", &encoded_rustflags)?,
        environment("CARGO_HOME", cargo_home)?,
        environment("CARGO_INCREMENTAL", "0")?,
        environment("CARGO_NET_OFFLINE", "true")?,
        environment("CARGO_TARGET_DIR", target)?,
        environment("CARGO_TERM_COLOR", "never")?,
        environment("HOME", cargo_home)?,
        environment("LANG", "C")?,
        environment("LC_ALL", "C")?,
        environment("PATH", &path_value)?,
        environment("RUSTUP_HOME", rustup_home)?,
        environment("RUSTUP_TOOLCHAIN", "1.96.1")?,
        environment("SOURCE_DATE_EPOCH", "0")?,
        environment("TEMP", &temp_dir)?,
        environment("TMP", &temp_dir)?,
        environment("TMPDIR", &temp_dir)?,
        environment("TZ", "UTC")?,
        environment("USERPROFILE", cargo_home)?,
    ];
    if let Some(root) = system_root {
        let command = path_join_utf8(Path::new(root), "System32/cmd.exe")?;
        environment_vars.extend([
            environment("ComSpec", &command)?,
            environment("PATHEXT", ".COM;.EXE;.BAT;.CMD")?,
            environment("SystemRoot", root)?,
            environment("WINDIR", root)?,
        ]);
    }
    environment_vars.sort_by(|left, right| left.name.as_bytes().cmp(right.name.as_bytes()));
    if environment_vars
        .windows(2)
        .any(|pair| pair[0].name.as_bytes() >= pair[1].name.as_bytes())
    {
        return Err(ProtocolError::InvalidDocument);
    }

    let mut scan_prefixes = BTreeSet::new();
    for path in scan_paths {
        for form in physical_scan_forms(path)? {
            if !form.is_empty() {
                scan_prefixes.insert(form);
            }
        }
    }

    return Ok(ArtifactBuildPolicyV1 {
        cargo_proxy_invocation: inputs.cargo_proxy_invocation.clone(),
        cargo_resolved_identity: inputs.cargo_resolved_identity.clone(),
        working_directory: inputs.workspace_root.clone(),
        argv: [
            "+1.96.1",
            "build",
            "--locked",
            "-p",
            "heleos-pdf-guest",
            "--target",
            "wasm32-wasip1",
            "--release",
            "--message-format=json-render-diagnostics",
            "--quiet",
        ]
        .into_iter()
        .map(str::to_owned)
        .collect(),
        env_clear: true,
        environment: environment_vars,
        remap_mappings: mappings
            .into_iter()
            .map(|item| ArtifactRemapV1 {
                physical: item.physical,
                virtual_prefix: item.virtual_prefix,
            })
            .collect(),
        scan_prefixes: scan_prefixes.into_iter().collect(),
    });

    fn mapping(
        physical: &str,
        virtual_prefix: &str,
        kind_ordinal: u8,
        registry_id: &str,
    ) -> Result<Mapping> {
        Ok(Mapping {
            physical: physical.to_owned(),
            virtual_prefix: virtual_prefix.to_owned(),
            component_count: physical_component_count(physical)?,
            kind_ordinal,
            registry_id: registry_id.to_owned(),
        })
    }
}

#[cfg(feature = "artifact-host")]
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CargoCompilerArtifactMessage {
    reason: String,
    package_id: String,
    manifest_path: String,
    target: CargoTarget,
    profile: CargoArtifactProfile,
    features: Vec<String>,
    filenames: Vec<String>,
    executable: Option<String>,
    fresh: bool,
}

#[cfg(feature = "artifact-host")]
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CargoArtifactProfile {
    opt_level: String,
    debuginfo: u64,
    debug_assertions: bool,
    overflow_checks: bool,
    test: bool,
}

#[cfg(feature = "artifact-host")]
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CargoBuildScriptExecutedMessage {
    reason: String,
    package_id: String,
    linked_libs: Vec<String>,
    linked_paths: Vec<String>,
    cfgs: Vec<String>,
    env: Vec<(String, String)>,
    out_dir: String,
}

#[cfg(feature = "artifact-host")]
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct CargoBuildFinishedMessage {
    reason: String,
    success: bool,
}

#[cfg(feature = "artifact-host")]
#[derive(Deserialize)]
struct CargoReasonMessage {
    reason: String,
}

#[cfg(feature = "artifact-host")]
#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
struct ExpectedCompilerUnit {
    normalized_dependency_id: String,
    target_name: String,
    target_kind: CargoTargetKindV1,
    crate_types: Vec<String>,
}

#[cfg(feature = "artifact-host")]
pub fn validate_guest_build_evidence_v1(
    cargo_json_stdout: &[u8],
    guest_metadata_json: &[u8],
    full_workspace_metadata_json: &[u8],
    production_cargo_lock_v4: &[u8],
    guest_resolution_cargo_lock_v4: &[u8],
    root_package_name: &str,
    policy: &ArtifactBuildPolicyV1,
) -> Result<GuestBuildEvidenceV1> {
    use std::collections::{BTreeMap, BTreeSet};

    const MAX_TRACE_BYTES: usize = 32 * 1024 * 1024;
    const MAX_TRACE_RECORDS: usize = 65_536;
    if cargo_json_stdout.is_empty()
        || cargo_json_stdout.len() > MAX_TRACE_BYTES
        || cargo_json_stdout.last() != Some(&b'\n')
        || cargo_json_stdout.contains(&b'\r')
    {
        return Err(ProtocolError::SizeLimit);
    }

    let graph = normalize_dependency_graph_v1(
        guest_metadata_json,
        full_workspace_metadata_json,
        production_cargo_lock_v4,
        guest_resolution_cargo_lock_v4,
        root_package_name,
    )?;
    let cache_plan =
        guest_resolution_cache_plan_v1(production_cargo_lock_v4, guest_resolution_cargo_lock_v4)?;
    let guest_resolution_lock_sha256 =
        guest_resolution_lock_sha256(guest_resolution_cargo_lock_v4)?;
    let guest_resolution_cache_plan_sha256 = guest_resolution_cache_plan_sha256(&cache_plan)?;
    let production_lock = parse_cargo_lock_v4(production_cargo_lock_v4)?;
    let resolution_lock = parse_cargo_lock_v4(guest_resolution_cargo_lock_v4)?;
    let metadata = parse_cargo_metadata_v1(guest_metadata_json)?;

    let packages = metadata
        .packages
        .iter()
        .map(|package| (package.id.as_str(), package))
        .collect::<BTreeMap<_, _>>();
    if packages.len() != metadata.packages.len() {
        return Err(ProtocolError::InvalidDocument);
    }
    let mut normalized_by_package_id = BTreeMap::new();
    let mut package_id_by_normalized = BTreeMap::new();
    for package in &metadata.packages {
        let normalized = normalize_package_id_from_locks(
            package,
            &metadata.workspace_root,
            &production_lock,
            Some(&resolution_lock),
        )?;
        if normalized_by_package_id
            .insert(package.id.as_str(), normalized.clone())
            .is_some()
            || package_id_by_normalized
                .insert(normalized, package.id.as_str())
                .is_some()
        {
            return Err(ProtocolError::InvalidDocument);
        }
    }
    let active_records = graph
        .records
        .iter()
        .map(|record| (record.id.as_str(), record))
        .collect::<BTreeMap<_, _>>();
    if active_records.len() != graph.records.len() {
        return Err(ProtocolError::InvalidDocument);
    }

    require_policy_build_roots(&graph, &cache_plan, &metadata.workspace_root, policy)?;
    let target_root = policy_environment_path(policy, "CARGO_TARGET_DIR")?;
    let guest_executable = target_root
        .join("wasm32-wasip1")
        .join("release")
        .join("heleos_pdf_guest.wasm");
    let guest_executable = guest_executable
        .to_str()
        .ok_or(ProtocolError::InvalidDocument)?
        .to_owned();

    let mut expected_units = BTreeMap::<ExpectedCompilerUnit, CargoTarget>::new();
    let mut expected_custom_builds = BTreeSet::new();
    let mut guest_bin_count = 0_usize;
    for record in &graph.records {
        let package_id = package_id_by_normalized
            .get(record.id.as_str())
            .copied()
            .ok_or(ProtocolError::InvalidDocument)?;
        let package = packages
            .get(package_id)
            .copied()
            .ok_or(ProtocolError::InvalidDocument)?;
        let mut primary_count = 0_usize;
        for target in &package.targets {
            let target_kind = match declared_target_kind(target)? {
                Some(CargoTargetKindV1::Bin)
                    if package.name == root_package_name && target.name == "heleos_pdf_guest" =>
                {
                    if !target.required_features.is_empty() {
                        return Err(ProtocolError::InvalidDocument);
                    }
                    guest_bin_count = guest_bin_count
                        .checked_add(1)
                        .ok_or(ProtocolError::SizeLimit)?;
                    Some(CargoTargetKindV1::Bin)
                }
                Some(CargoTargetKindV1::Bin) => None,
                Some(kind @ (CargoTargetKindV1::Lib | CargoTargetKindV1::ProcMacro)) => {
                    primary_count = primary_count
                        .checked_add(1)
                        .ok_or(ProtocolError::SizeLimit)?;
                    Some(kind)
                }
                Some(CargoTargetKindV1::CustomBuild) => {
                    if target.name != "build-script-build"
                        || !expected_custom_builds.insert(record.id.clone())
                    {
                        return Err(ProtocolError::InvalidDocument);
                    }
                    Some(CargoTargetKindV1::CustomBuild)
                }
                None => None,
            };
            if let Some(target_kind) = target_kind {
                let mut crate_types = target.crate_types.clone();
                crate_types.sort_by(|left, right| left.as_bytes().cmp(right.as_bytes()));
                if crate_types.is_empty()
                    || crate_types.iter().any(String::is_empty)
                    || crate_types
                        .windows(2)
                        .any(|pair| pair[0].as_bytes() >= pair[1].as_bytes())
                {
                    return Err(ProtocolError::InvalidDocument);
                }
                let key = ExpectedCompilerUnit {
                    normalized_dependency_id: record.id.clone(),
                    target_name: target.name.clone(),
                    target_kind,
                    crate_types,
                };
                if expected_units.insert(key, target.clone()).is_some() {
                    return Err(ProtocolError::InvalidDocument);
                }
            }
        }
        if primary_count != 1 {
            return Err(ProtocolError::InvalidDocument);
        }
    }
    if guest_bin_count != 1 {
        return Err(ProtocolError::InvalidDocument);
    }

    let trace = &cargo_json_stdout[..cargo_json_stdout.len() - 1];
    let mut record_count = 0_usize;
    for line in trace.split(|byte| *byte == b'\n') {
        record_count = record_count
            .checked_add(1)
            .ok_or(ProtocolError::SizeLimit)?;
        if record_count > MAX_TRACE_RECORDS || line.is_empty() {
            return Err(ProtocolError::SizeLimit);
        }
    }
    if record_count == 0 {
        return Err(ProtocolError::SizeLimit);
    }
    let mut compiler_units = Vec::new();
    let mut seen_units = BTreeSet::new();
    let mut feature_union = BTreeMap::<String, BTreeSet<String>>::new();
    let mut build_scripts = Vec::new();
    let mut seen_build_scripts = BTreeSet::new();
    let mut saw_finished = false;
    for (index, line) in trace.split(|byte| *byte == b'\n').enumerate() {
        let reason: CargoReasonMessage =
            serde_json::from_slice(line).map_err(|_| ProtocolError::InvalidDocument)?;
        match reason.reason.as_str() {
            "compiler-artifact" if !saw_finished => {
                let artifact: CargoCompilerArtifactMessage =
                    serde_json::from_slice(line).map_err(|_| ProtocolError::InvalidDocument)?;
                if artifact.reason != "compiler-artifact" || artifact.fresh {
                    return Err(ProtocolError::InvalidDocument);
                }
                let normalized = normalized_by_package_id
                    .get(artifact.package_id.as_str())
                    .ok_or(ProtocolError::InvalidDocument)?;
                let record = active_records
                    .get(normalized.as_str())
                    .copied()
                    .ok_or(ProtocolError::InvalidDocument)?;
                let package = packages
                    .get(artifact.package_id.as_str())
                    .copied()
                    .ok_or(ProtocolError::InvalidDocument)?;
                if artifact.manifest_path != package.manifest_path
                    || artifact.profile.test
                    || !matches!(artifact.profile.opt_level.as_str(), "0" | "3")
                    || artifact.profile.debuginfo != 0
                    || artifact.profile.debug_assertions
                    || artifact.profile.overflow_checks
                {
                    return Err(ProtocolError::InvalidDocument);
                }
                let target_kind = declared_target_kind(&artifact.target)?
                    .ok_or(ProtocolError::InvalidDocument)?;
                let mut crate_types = artifact.target.crate_types.clone();
                crate_types.sort_by(|left, right| left.as_bytes().cmp(right.as_bytes()));
                crate_types.dedup();
                let key = ExpectedCompilerUnit {
                    normalized_dependency_id: normalized.clone(),
                    target_name: artifact.target.name.clone(),
                    target_kind,
                    crate_types: crate_types.clone(),
                };
                let declared = expected_units
                    .get(&key)
                    .ok_or(ProtocolError::InvalidDocument)?;
                if &artifact.target != declared || !seen_units.insert(key) {
                    return Err(ProtocolError::InvalidDocument);
                }
                validate_artifact_filenames(
                    &artifact.filenames,
                    artifact.executable.as_deref(),
                    target_kind,
                    &guest_executable,
                    &target_root,
                )?;
                validate_artifact_profile(
                    &artifact.profile,
                    &artifact.filenames,
                    target_kind,
                    &target_root,
                )?;
                let mut features = artifact.features;
                features.sort_by(|left, right| left.as_bytes().cmp(right.as_bytes()));
                if features.iter().any(String::is_empty)
                    || features
                        .windows(2)
                        .any(|pair| pair[0].as_bytes() >= pair[1].as_bytes())
                    || features
                        .iter()
                        .any(|feature| !record.features.contains(feature))
                {
                    return Err(ProtocolError::InvalidDocument);
                }
                feature_union
                    .entry(normalized.clone())
                    .or_default()
                    .extend(features.iter().cloned());
                compiler_units.push(CargoCompilerUnitV1 {
                    normalized_dependency_id: normalized.clone(),
                    target_name: artifact.target.name,
                    target_kind,
                    crate_types,
                    features,
                    profile_test: artifact.profile.test,
                });
            }
            "build-script-executed" if !saw_finished => {
                let message: CargoBuildScriptExecutedMessage =
                    serde_json::from_slice(line).map_err(|_| ProtocolError::InvalidDocument)?;
                if message.reason != "build-script-executed" {
                    return Err(ProtocolError::InvalidDocument);
                }
                let normalized = normalized_by_package_id
                    .get(message.package_id.as_str())
                    .ok_or(ProtocolError::InvalidDocument)?;
                if !expected_custom_builds.contains(normalized)
                    || !seen_build_scripts.insert(normalized.clone())
                    || !message.linked_libs.is_empty()
                    || !message.linked_paths.is_empty()
                {
                    return Err(ProtocolError::InvalidDocument);
                }
                validate_build_script_out_dir(&message.out_dir, &target_root)?;
                let cfgs = normalize_trace_strings(message.cfgs, policy)?;
                let env = normalize_trace_env(message.env, policy)?;
                build_scripts.push(CargoBuildScriptEvidenceV1 {
                    normalized_dependency_id: normalized.clone(),
                    cfgs,
                    env,
                    linked_libs: message.linked_libs,
                    linked_paths: message.linked_paths,
                });
            }
            "build-finished" if !saw_finished && index + 1 == record_count => {
                let message: CargoBuildFinishedMessage =
                    serde_json::from_slice(line).map_err(|_| ProtocolError::InvalidDocument)?;
                if message.reason != "build-finished" || !message.success {
                    return Err(ProtocolError::InvalidDocument);
                }
                saw_finished = true;
            }
            _ => return Err(ProtocolError::InvalidDocument),
        }
    }
    if !saw_finished
        || seen_units.len() != expected_units.len()
        || seen_units.iter().ne(expected_units.keys())
        || seen_build_scripts != expected_custom_builds
    {
        return Err(ProtocolError::InvalidDocument);
    }
    for record in &graph.records {
        let union = feature_union.remove(&record.id).unwrap_or_default();
        if union.iter().ne(record.features.iter()) {
            return Err(ProtocolError::InvalidDocument);
        }
    }
    if !feature_union.is_empty() {
        return Err(ProtocolError::InvalidDocument);
    }
    compiler_units.sort_by(compiler_unit_cmp);
    build_scripts.sort_by(|left, right| {
        left.normalized_dependency_id
            .as_bytes()
            .cmp(right.normalized_dependency_id.as_bytes())
            .then_with(|| left.cfgs.cmp(&right.cfgs))
            .then_with(|| left.env.cmp(&right.env))
    });
    Ok(GuestBuildEvidenceV1 {
        guest_resolution_lock_sha256,
        guest_resolution_cache_plan_sha256,
        compiler_units,
        build_scripts,
    })
}

#[cfg(feature = "artifact-host")]
fn declared_target_kind(target: &CargoTarget) -> Result<Option<CargoTargetKindV1>> {
    if target.name.is_empty()
        || target.edition.is_empty()
        || !std::path::Path::new(&target.src_path).is_absolute()
        || target.kind.is_empty()
        || target.crate_types.is_empty()
    {
        return Err(ProtocolError::InvalidDocument);
    }
    let kind = if let Some(kind) = dependency_capable_target_kind(target) {
        Some(kind)
    } else {
        match (target.kind.as_slice(), target.crate_types.as_slice()) {
            ([kind], [crate_type]) if kind == "custom-build" && crate_type == "bin" => {
                Some(CargoTargetKindV1::CustomBuild)
            }
            ([kind], [crate_type]) if kind == "bin" && crate_type == "bin" => {
                Some(CargoTargetKindV1::Bin)
            }
            ([kind], _) if matches!(kind.as_str(), "test" | "example" | "bench") => None,
            _ => return Err(ProtocolError::InvalidDocument),
        }
    };
    if kind.is_some_and(|kind| kind != CargoTargetKindV1::Bin)
        && !target.required_features.is_empty()
    {
        return Err(ProtocolError::InvalidDocument);
    }
    Ok(kind)
}

#[cfg(feature = "artifact-host")]
fn dependency_capable_target_kind(target: &CargoTarget) -> Option<CargoTargetKindV1> {
    match (target.kind.as_slice(), target.crate_types.as_slice()) {
        ([kind], [crate_type])
            if (kind == "lib" && crate_type == "lib")
                || (kind == "rlib" && crate_type == "rlib") =>
        {
            Some(CargoTargetKindV1::Lib)
        }
        ([kind], [crate_type]) if kind == "proc-macro" && crate_type == "proc-macro" => {
            Some(CargoTargetKindV1::ProcMacro)
        }
        _ => None,
    }
}

#[cfg(feature = "artifact-host")]
fn require_policy_build_roots(
    graph: &ArtifactDependencyGraphV1,
    cache_plan: &GuestResolutionCachePlanV1,
    metadata_workspace_root: &str,
    policy: &ArtifactBuildPolicyV1,
) -> Result<()> {
    use std::collections::{BTreeMap, BTreeSet};

    let metadata_workspace = std::path::Path::new(metadata_workspace_root);
    validated_absolute_path(metadata_workspace)?;
    if policy.working_directory != metadata_workspace {
        return Err(ProtocolError::InvalidDocument);
    }
    let cargo_home = policy_environment_path(policy, "CARGO_HOME")?;
    let archives = cache_plan
        .resolution_archives
        .iter()
        .map(|archive| (archive.normalized_dependency_id.as_str(), archive))
        .collect::<BTreeMap<_, _>>();
    if archives.len() != cache_plan.resolution_archives.len() {
        return Err(ProtocolError::InvalidDocument);
    }
    for root in &graph.registry_roots {
        let archive = archives
            .get(root.normalized_dependency_id.as_str())
            .copied()
            .ok_or(ProtocolError::InvalidDocument)?;
        let expected = cargo_home.join(&archive.unpacked_source_cargo_home_relative_path);
        validated_absolute_path(&expected)?;
        if root.physical_root != expected {
            return Err(ProtocolError::InvalidDocument);
        }
    }

    let expected = graph
        .registry_roots
        .iter()
        .map(|root| {
            let physical = root
                .physical_root
                .to_str()
                .ok_or(ProtocolError::InvalidDocument)?;
            Ok((
                physical.to_owned(),
                format!(
                    "/heleos/registry/{}",
                    lower_hex(&Sha256::digest(root.normalized_dependency_id.as_bytes()))
                ),
            ))
        })
        .collect::<Result<BTreeSet<_>>>()?;
    let actual = policy
        .remap_mappings
        .iter()
        .filter(|mapping| mapping.virtual_prefix.starts_with("/heleos/registry/"))
        .map(|mapping| (mapping.physical.clone(), mapping.virtual_prefix.clone()))
        .collect::<BTreeSet<_>>();
    if expected != actual {
        return Err(ProtocolError::InvalidDocument);
    }
    Ok(())
}

#[cfg(feature = "artifact-host")]
fn policy_environment_path(
    policy: &ArtifactBuildPolicyV1,
    name: &str,
) -> Result<std::path::PathBuf> {
    let value = policy
        .environment
        .iter()
        .find(|entry| entry.name == name)
        .map(|entry| entry.value.as_str())
        .ok_or(ProtocolError::InvalidDocument)?;
    let path = std::path::Path::new(value);
    validated_absolute_path(path)?;
    Ok(path.to_path_buf())
}

#[cfg(feature = "artifact-host")]
fn validate_artifact_filenames(
    filenames: &[String],
    executable: Option<&str>,
    target_kind: CargoTargetKindV1,
    guest_executable: &str,
    target_root: &std::path::Path,
) -> Result<()> {
    use std::collections::BTreeSet;

    if target_kind == CargoTargetKindV1::Bin {
        if filenames != [guest_executable] || executable != Some(guest_executable) {
            return Err(ProtocolError::InvalidDocument);
        }
        return Ok(());
    }
    if executable.is_some() || filenames.is_empty() {
        return Err(ProtocolError::InvalidDocument);
    }
    let mut seen = BTreeSet::new();
    for filename in filenames {
        let path = std::path::Path::new(filename);
        validated_absolute_path(path)?;
        if !path.starts_with(target_root) || !seen.insert(filename) {
            return Err(ProtocolError::InvalidDocument);
        }
    }
    Ok(())
}

#[cfg(feature = "artifact-host")]
fn validate_artifact_profile(
    profile: &CargoArtifactProfile,
    filenames: &[String],
    target_kind: CargoTargetKindV1,
    target_root: &std::path::Path,
) -> Result<()> {
    let host_release = target_root.join("release");
    let guest_release = target_root.join("wasm32-wasip1").join("release");
    let all_under = |root: &std::path::Path| {
        filenames
            .iter()
            .all(|filename| std::path::Path::new(filename).starts_with(root))
    };
    let expected_opt_level = match target_kind {
        CargoTargetKindV1::ProcMacro | CargoTargetKindV1::CustomBuild => {
            if !all_under(&host_release) {
                return Err(ProtocolError::InvalidDocument);
            }
            "0"
        }
        CargoTargetKindV1::Bin => {
            if !all_under(&guest_release) {
                return Err(ProtocolError::InvalidDocument);
            }
            "3"
        }
        CargoTargetKindV1::Lib => match (all_under(&host_release), all_under(&guest_release)) {
            (true, false) => "0",
            (false, true) => "3",
            _ => return Err(ProtocolError::InvalidDocument),
        },
    };
    if profile.opt_level != expected_opt_level
        || profile.debuginfo != 0
        || profile.debug_assertions
        || profile.overflow_checks
        || profile.test
    {
        return Err(ProtocolError::InvalidDocument);
    }
    Ok(())
}

#[cfg(feature = "artifact-host")]
fn validate_build_script_out_dir(out_dir: &str, target_root: &std::path::Path) -> Result<()> {
    let path = std::path::Path::new(out_dir);
    validated_absolute_path(path)?;
    let relative = path
        .strip_prefix(target_root)
        .map_err(|_| ProtocolError::InvalidDocument)?;
    if relative.as_os_str().is_empty() {
        return Err(ProtocolError::InvalidDocument);
    }
    Ok(())
}

#[cfg(feature = "artifact-host")]
fn normalize_trace_strings(
    mut values: Vec<String>,
    policy: &ArtifactBuildPolicyV1,
) -> Result<Vec<String>> {
    for value in &values {
        if value.is_empty()
            || value.chars().any(|character| character.is_control())
            || contains_policy_prefix(value.as_bytes(), policy)
        {
            return Err(ProtocolError::InvalidDocument);
        }
    }
    values.sort_by(|left, right| left.as_bytes().cmp(right.as_bytes()));
    values.dedup();
    Ok(values)
}

#[cfg(feature = "artifact-host")]
fn normalize_trace_env(
    mut values: Vec<(String, String)>,
    policy: &ArtifactBuildPolicyV1,
) -> Result<Vec<(String, String)>> {
    for (name, value) in &values {
        if name.is_empty()
            || name.contains('=')
            || name.chars().any(|character| character.is_control())
            || value.chars().any(|character| character.is_control())
            || contains_policy_prefix(name.as_bytes(), policy)
            || contains_policy_prefix(value.as_bytes(), policy)
        {
            return Err(ProtocolError::InvalidDocument);
        }
    }
    values.sort_by(|left, right| {
        left.0
            .as_bytes()
            .cmp(right.0.as_bytes())
            .then_with(|| left.1.as_bytes().cmp(right.1.as_bytes()))
    });
    values.dedup();
    Ok(values)
}

#[cfg(feature = "artifact-host")]
fn contains_policy_prefix(bytes: &[u8], policy: &ArtifactBuildPolicyV1) -> bool {
    policy.scan_prefixes.iter().any(|prefix| {
        !prefix.is_empty()
            && bytes
                .windows(prefix.len())
                .any(|window| window == prefix.as_slice())
    })
}

#[cfg(feature = "artifact-host")]
fn compiler_unit_cmp(
    left: &CargoCompilerUnitV1,
    right: &CargoCompilerUnitV1,
) -> std::cmp::Ordering {
    left.normalized_dependency_id
        .as_bytes()
        .cmp(right.normalized_dependency_id.as_bytes())
        .then_with(|| {
            left.target_name
                .as_bytes()
                .cmp(right.target_name.as_bytes())
        })
        .then_with(|| left.target_kind.cmp(&right.target_kind))
        .then_with(|| left.crate_types.cmp(&right.crate_types))
        .then_with(|| left.features.cmp(&right.features))
        .then_with(|| left.profile_test.cmp(&right.profile_test))
}

#[cfg(feature = "artifact-host")]
pub fn verify_no_physical_prefixes_v1(wasm: &[u8], policy: &ArtifactBuildPolicyV1) -> Result<()> {
    use std::collections::BTreeSet;

    use wasmparser::{Encoding, Parser, Payload};

    let prefixes = &policy.scan_prefixes;
    if wasm.is_empty() || wasm.len() > MAX_GUEST_WASM_BYTES || prefixes.is_empty() {
        return Err(ProtocolError::InvalidDocument);
    }
    let unique = prefixes.iter().collect::<BTreeSet<_>>();
    if unique.len() != prefixes.len() || unique.iter().any(|prefix| prefix.is_empty()) {
        return Err(ProtocolError::InvalidDocument);
    }
    let leaked = |bytes: &[u8]| {
        unique.iter().any(|prefix| {
            bytes
                .windows(prefix.len())
                .any(|window| window == prefix.as_slice())
        })
    };
    let mut saw_version = false;
    let mut saw_end = false;
    for payload in Parser::new(0).parse_all(wasm) {
        match payload.map_err(|_| ProtocolError::InvalidDocument)? {
            Payload::Version { num, encoding, .. } => {
                if saw_version || num != 1 || encoding != Encoding::Module {
                    return Err(ProtocolError::InvalidDocument);
                }
                saw_version = true;
            }
            Payload::CustomSection(section) => {
                if leaked(section.name().as_bytes()) || leaked(section.data()) {
                    return Err(ProtocolError::InvalidDocument);
                }
            }
            Payload::DataSection(section) => {
                for data in section {
                    let data = data.map_err(|_| ProtocolError::InvalidDocument)?;
                    if leaked(data.data) {
                        return Err(ProtocolError::InvalidDocument);
                    }
                }
            }
            Payload::End(_) => saw_end = true,
            _ => {}
        }
    }
    if !saw_version || !saw_end {
        return Err(ProtocolError::InvalidDocument);
    }
    Ok(())
}

#[cfg(feature = "artifact-host")]
fn validated_absolute_path(path: &std::path::Path) -> Result<&str> {
    use std::path::Component;

    if !path.is_absolute()
        || path.file_name().is_none()
        || path
            .components()
            .any(|component| matches!(component, Component::CurDir | Component::ParentDir))
    {
        return Err(ProtocolError::InvalidDocument);
    }
    let value = path.to_str().ok_or(ProtocolError::InvalidDocument)?;
    validate_plain_value(value)?;
    let path_list_separator = if cfg!(windows) { ';' } else { ':' };
    if value.as_bytes().contains(&b'=')
        || value.as_bytes().contains(&0x1f)
        || value.contains(path_list_separator)
    {
        return Err(ProtocolError::InvalidDocument);
    }
    Ok(value)
}

#[cfg(feature = "artifact-host")]
fn validate_plain_value(value: &str) -> Result<()> {
    if value.is_empty()
        || value
            .chars()
            .any(|character| character.is_control() || character == '\u{7f}')
    {
        return Err(ProtocolError::InvalidDocument);
    }
    Ok(())
}

#[cfg(feature = "artifact-host")]
fn physical_component_count(value: &str) -> Result<usize> {
    let count = value
        .split(['/', '\\'])
        .filter(|component| !component.is_empty())
        .count();
    if count == 0 {
        return Err(ProtocolError::InvalidDocument);
    }
    Ok(count)
}

#[cfg(feature = "artifact-host")]
fn physical_comparison_key(value: &str) -> String {
    let normalized = value.replace('\\', "/");
    if cfg!(windows) {
        normalized.to_ascii_lowercase()
    } else {
        normalized
    }
}

#[cfg(feature = "artifact-host")]
fn insert_distinct_physical_role(
    roles: &mut std::collections::BTreeMap<String, String>,
    role: &str,
    physical: &str,
) -> Result<()> {
    let key = physical_comparison_key(physical);
    if roles.insert(key, role.to_owned()).is_some() {
        return Err(ProtocolError::InvalidDocument);
    }
    Ok(())
}

#[cfg(feature = "artifact-host")]
fn validate_normalized_registry_id(value: &str) -> Result<()> {
    parse_registry_dependency_id_v1(value).map(|_| ())
}

#[cfg(feature = "artifact-host")]
pub fn parse_registry_dependency_id_v1(value: &str) -> Result<RegistryPackageIdentityV1> {
    validate_plain_value(value)?;
    if !value.is_ascii() || value.as_bytes().contains(&b'=') {
        return Err(ProtocolError::InvalidDocument);
    }
    let (prefix, checksum) = value
        .rsplit_once('#')
        .ok_or(ProtocolError::InvalidDocument)?;
    let package = prefix
        .strip_prefix("registry:")
        .ok_or(ProtocolError::InvalidDocument)?;
    let (name, version) = package
        .rsplit_once('@')
        .ok_or(ProtocolError::InvalidDocument)?;
    if name.is_empty()
        || version.is_empty()
        || name.contains(['@', '#', '/', '\\'])
        || version.contains(['@', '#', '/', '\\'])
        || !is_lower_sha256(checksum)
    {
        return Err(ProtocolError::InvalidDocument);
    }
    Ok(RegistryPackageIdentityV1 {
        name: name.to_owned(),
        version: version.to_owned(),
        checksum: checksum.to_owned(),
    })
}

#[cfg(feature = "artifact-host")]
fn path_join_utf8(path: &std::path::Path, child: &str) -> Result<String> {
    let joined = path.join(child);
    let value = joined.to_str().ok_or(ProtocolError::InvalidDocument)?;
    validate_plain_value(value)?;
    Ok(value.to_owned())
}

#[cfg(feature = "artifact-host")]
fn join_path_list(values: &[&str], separator: char) -> Result<String> {
    if values
        .iter()
        .any(|value| value.is_empty() || value.contains(separator))
    {
        return Err(ProtocolError::InvalidDocument);
    }
    Ok(values.join(&separator.to_string()))
}

#[cfg(feature = "artifact-host")]
fn environment(name: &str, value: &str) -> Result<ArtifactEnvironmentV1> {
    if name.is_empty()
        || !name
            .bytes()
            .all(|byte| byte == b'_' || byte.is_ascii_alphanumeric())
        || name.contains('=')
    {
        return Err(ProtocolError::InvalidDocument);
    }
    if name == "CARGO_ENCODED_RUSTFLAGS" {
        if value.is_empty()
            || value.chars().any(|character| {
                character != '\u{1f}' && (character.is_control() || character == '\u{7f}')
            })
        {
            return Err(ProtocolError::InvalidDocument);
        }
    } else {
        validate_plain_value(value)?;
    }
    if value.as_bytes().contains(&0) {
        return Err(ProtocolError::InvalidDocument);
    }
    Ok(ArtifactEnvironmentV1 {
        name: name.to_owned(),
        value: value.to_owned(),
    })
}

#[cfg(feature = "artifact-host")]
fn physical_scan_forms(value: &str) -> Result<[Vec<u8>; 3]> {
    validate_plain_value(value)?;
    Ok([
        value.as_bytes().to_vec(),
        value.replace('\\', "/").into_bytes(),
        value.replace('/', "\\").into_bytes(),
    ])
}

pub fn dependency_graph_sha256(records: &[DependencyRecordV1]) -> Result<String> {
    let mut prior_id: Option<&str> = None;
    for record in records {
        if record.id.is_empty()
            || prior_id.is_some_and(|prior| prior.as_bytes() >= record.id.as_bytes())
            || !strictly_sorted_strings(&record.features)
            || !strictly_sorted_edges(&record.edges)
        {
            return Err(ProtocolError::InvalidDocument);
        }
        prior_id = Some(&record.id);
    }
    hash_jcs_records(b"heleos-pdf-guest-dependency-graph-v1\0", records)
}

pub fn imports_sha256(records: &[ImportRecordV1]) -> Result<String> {
    if records.iter().any(|record| {
        record.module.is_empty()
            || record.name.is_empty()
            || record
                .params
                .iter()
                .chain(&record.results)
                .any(|value| !core_value_type_allowed(*value))
    }) || records
        .windows(2)
        .any(|pair| import_sort_key(&pair[0]) > import_sort_key(&pair[1]))
    {
        return Err(ProtocolError::InvalidDocument);
    }
    hash_jcs_records(b"heleos-pdf-guest-imports-v1\0", records)
}

pub fn exports_sha256(records: &[ExportRecordV1]) -> Result<String> {
    if records.iter().any(|record| !valid_export(record))
        || records.windows(2).any(|pair| {
            export_name(&pair[0]).as_bytes() == export_name(&pair[1]).as_bytes()
                || export_sort_key(&pair[0]) >= export_sort_key(&pair[1])
        })
    {
        return Err(ProtocolError::InvalidDocument);
    }
    hash_jcs_records(b"heleos-pdf-guest-exports-v1\0", records)
}

fn export_name(record: &ExportRecordV1) -> &str {
    match record {
        ExportRecordV1::Func { name, .. }
        | ExportRecordV1::Memory { name, .. }
        | ExportRecordV1::Table { name, .. } => name,
    }
}

pub fn source_tree_sha256(files: &[SourceFileV1]) -> Result<String> {
    let mut files = files.iter().collect::<Vec<_>>();
    files.sort_by(|left, right| left.path.as_bytes().cmp(right.path.as_bytes()));
    if files.iter().any(|file| {
        file.path.is_empty()
            || file.path.starts_with('/')
            || file.path.contains('\\')
            || file
                .path
                .split('/')
                .any(|part| part.is_empty() || part == "." || part == "..")
            || file.content.contains(&b'\r')
    }) || files
        .windows(2)
        .any(|pair| pair[0].path.as_bytes() == pair[1].path.as_bytes())
    {
        return Err(ProtocolError::InvalidDocument);
    }
    let mut hasher = Sha256::new();
    hasher.update(b"heleos-pdf-guest-source-v1\0");
    for file in files {
        let path = file.path.as_bytes();
        let path_len = u32::try_from(path.len()).map_err(|_| ProtocolError::SizeLimit)?;
        let content_len =
            u64::try_from(file.content.len()).map_err(|_| ProtocolError::SizeLimit)?;
        hasher.update(path_len.to_be_bytes());
        hasher.update(path);
        hasher.update(content_len.to_be_bytes());
        hasher.update(&file.content);
    }
    Ok(lower_hex(&hasher.finalize()))
}

#[cfg(feature = "artifact-host")]
pub fn extract_module_records(wasm: &[u8]) -> Result<(Vec<ImportRecordV1>, Vec<ExportRecordV1>)> {
    use std::collections::BTreeSet;

    use wasmparser::{CompositeInnerType, Encoding, ExternalKind, Parser, Payload, TypeRef};

    type Signature = (Vec<CoreValueTypeV1>, Vec<CoreValueTypeV1>);

    if wasm.is_empty() || wasm.len() > MAX_GUEST_WASM_BYTES {
        return Err(ProtocolError::SizeLimit);
    }

    let mut saw_version = false;
    let mut saw_end = false;
    let mut function_types = Vec::<Signature>::new();
    let mut function_type_indices = Vec::<u32>::new();
    let mut memories = Vec::new();
    let mut tables = Vec::new();
    let mut pending_exports = Vec::new();
    let mut imports = Vec::new();
    let mut code_count = None;

    for payload in Parser::new(0).parse_all(wasm) {
        let payload = payload.map_err(|_| ProtocolError::InvalidDocument)?;
        match payload {
            Payload::Version { num, encoding, .. } => {
                if saw_version || num != 1 || encoding != Encoding::Module {
                    return Err(ProtocolError::InvalidDocument);
                }
                saw_version = true;
            }
            Payload::TypeSection(reader) => {
                if !function_types.is_empty() {
                    return Err(ProtocolError::InvalidDocument);
                }
                for group in reader {
                    let group = group.map_err(|_| ProtocolError::InvalidDocument)?;
                    if group.is_explicit_rec_group() {
                        return Err(ProtocolError::InvalidDocument);
                    }
                    let mut types = group.into_types();
                    let subtype = types.next().ok_or(ProtocolError::InvalidDocument)?;
                    if types.next().is_some()
                        || !subtype.is_final
                        || subtype.supertype_idx.is_some()
                        || subtype.composite_type.shared
                        || subtype.composite_type.descriptor_idx.is_some()
                        || subtype.composite_type.describes_idx.is_some()
                    {
                        return Err(ProtocolError::InvalidDocument);
                    }
                    let CompositeInnerType::Func(function_type) = subtype.composite_type.inner
                    else {
                        return Err(ProtocolError::InvalidDocument);
                    };
                    function_types.push((
                        convert_value_types(function_type.params())?,
                        convert_value_types(function_type.results())?,
                    ));
                }
            }
            Payload::ImportSection(reader) => {
                for import in reader.into_imports() {
                    let import = import.map_err(|_| ProtocolError::InvalidDocument)?;
                    let type_index = match import.ty {
                        TypeRef::Func(index) => index,
                        TypeRef::FuncExact(_)
                        | TypeRef::Table(_)
                        | TypeRef::Memory(_)
                        | TypeRef::Global(_)
                        | TypeRef::Tag(_) => {
                            return Err(ProtocolError::InvalidDocument);
                        }
                    };
                    let function_type = checked_index(&function_types, type_index)?
                        .ok_or(ProtocolError::InvalidDocument)?;
                    imports.push(ImportRecordV1 {
                        module: import.module.to_owned(),
                        name: import.name.to_owned(),
                        kind: ImportKindV1::Func,
                        params: function_type.0.clone(),
                        results: function_type.1.clone(),
                    });
                    function_type_indices.push(type_index);
                }
            }
            Payload::FunctionSection(reader) => {
                for index in reader {
                    let index = index.map_err(|_| ProtocolError::InvalidDocument)?;
                    if checked_index(&function_types, index)?.is_none() {
                        return Err(ProtocolError::InvalidDocument);
                    }
                    function_type_indices.push(index);
                }
            }
            Payload::TableSection(reader) => {
                for table in reader {
                    let table = table.map_err(|_| ProtocolError::InvalidDocument)?;
                    if table.ty.shared {
                        return Err(ProtocolError::InvalidDocument);
                    }
                    if table
                        .ty
                        .maximum
                        .is_some_and(|maximum| maximum < table.ty.initial)
                    {
                        return Err(ProtocolError::InvalidDocument);
                    }
                    convert_reference_type(table.ty.element_type)?;
                    tables.push(table.ty);
                }
            }
            Payload::MemorySection(reader) => {
                for memory in reader {
                    let memory = memory.map_err(|_| ProtocolError::InvalidDocument)?;
                    if memory
                        .maximum
                        .is_some_and(|maximum| maximum < memory.initial)
                        || (memory.shared && memory.maximum.is_none())
                    {
                        return Err(ProtocolError::InvalidDocument);
                    }
                    memories.push(memory);
                }
            }
            Payload::ExportSection(reader) => {
                for export in reader {
                    pending_exports.push(export.map_err(|_| ProtocolError::InvalidDocument)?);
                }
            }
            Payload::StartSection { .. } => return Err(ProtocolError::InvalidDocument),
            Payload::CodeSectionStart { count, .. } => code_count = Some(count),
            Payload::CodeSectionEntry(body) => {
                let locals = body
                    .get_locals_reader()
                    .map_err(|_| ProtocolError::InvalidDocument)?;
                for local in locals {
                    let (_, value_type) = local.map_err(|_| ProtocolError::InvalidDocument)?;
                    convert_value_type(value_type)?;
                }
                let mut operators = body
                    .get_operators_reader()
                    .map_err(|_| ProtocolError::InvalidDocument)?;
                while !operators.eof() {
                    let operator = operators
                        .read()
                        .map_err(|_| ProtocolError::InvalidDocument)?;
                    validate_operator_shape(&operator)?;
                }
            }
            Payload::GlobalSection(reader) => {
                for global in reader {
                    let global = global.map_err(|_| ProtocolError::InvalidDocument)?;
                    if global.ty.shared {
                        return Err(ProtocolError::InvalidDocument);
                    }
                    convert_value_type(global.ty.content_type)?;
                    drain_const_expr(&global.init_expr)?;
                }
            }
            Payload::ElementSection(reader) => {
                use wasmparser::{ElementItems, ElementKind};

                for element in reader {
                    let element = element.map_err(|_| ProtocolError::InvalidDocument)?;
                    if let ElementKind::Active {
                        table_index,
                        offset_expr,
                    } = &element.kind
                    {
                        if checked_index(&tables, table_index.unwrap_or(0))?.is_none() {
                            return Err(ProtocolError::InvalidDocument);
                        }
                        drain_const_expr(offset_expr)?;
                    }
                    match element.items {
                        ElementItems::Functions(functions) => {
                            for function in functions {
                                let function =
                                    function.map_err(|_| ProtocolError::InvalidDocument)?;
                                if checked_index(&function_type_indices, function)?.is_none() {
                                    return Err(ProtocolError::InvalidDocument);
                                }
                            }
                        }
                        ElementItems::Expressions(reference, expressions) => {
                            convert_reference_type(reference)?;
                            for expression in expressions {
                                let expression =
                                    expression.map_err(|_| ProtocolError::InvalidDocument)?;
                                drain_const_expr(&expression)?;
                            }
                        }
                    }
                }
            }
            Payload::DataSection(reader) => {
                use wasmparser::DataKind;

                for data in reader {
                    let data = data.map_err(|_| ProtocolError::InvalidDocument)?;
                    if let DataKind::Active {
                        memory_index,
                        offset_expr,
                    } = &data.kind
                    {
                        if checked_index(&memories, *memory_index)?.is_none() {
                            return Err(ProtocolError::InvalidDocument);
                        }
                        drain_const_expr(offset_expr)?;
                    }
                }
            }
            Payload::DataCountSection { .. } | Payload::CustomSection(_) => {}
            Payload::UnknownSection { .. } => return Err(ProtocolError::InvalidDocument),
            Payload::TagSection(_) => return Err(ProtocolError::InvalidDocument),
            Payload::End(_) => saw_end = true,
            _ => return Err(ProtocolError::InvalidDocument),
        }
    }

    if !saw_version || !saw_end {
        return Err(ProtocolError::InvalidDocument);
    }
    let imported_function_count = imports.len();
    let defined_function_count = function_type_indices
        .len()
        .checked_sub(imported_function_count)
        .ok_or(ProtocolError::InvalidDocument)?;
    let defined_function_count =
        u64::try_from(defined_function_count).map_err(|_| ProtocolError::SizeLimit)?;
    if u64::from(code_count.unwrap_or(0)) != defined_function_count {
        return Err(ProtocolError::InvalidDocument);
    }

    imports.sort_by(|left, right| import_sort_key(left).cmp(&import_sort_key(right)));
    imports_sha256(&imports)?;

    let mut export_names = BTreeSet::new();
    let mut exports = Vec::with_capacity(pending_exports.len());
    for export in pending_exports {
        if export.name.is_empty() || !export_names.insert(export.name.as_bytes().to_vec()) {
            return Err(ProtocolError::InvalidDocument);
        }
        let record = match export.kind {
            ExternalKind::Func => {
                let type_index = *function_type_indices
                    .get(checked_usize(export.index)?)
                    .ok_or(ProtocolError::InvalidDocument)?;
                let function_type = checked_index(&function_types, type_index)?
                    .ok_or(ProtocolError::InvalidDocument)?;
                ExportRecordV1::Func {
                    name: export.name.to_owned(),
                    params: function_type.0.clone(),
                    results: function_type.1.clone(),
                }
            }
            ExternalKind::Memory => {
                let memory = memories
                    .get(checked_usize(export.index)?)
                    .ok_or(ProtocolError::InvalidDocument)?;
                ExportRecordV1::Memory {
                    name: export.name.to_owned(),
                    minimum_pages: memory.initial,
                    maximum_pages: memory.maximum,
                    memory64: memory.memory64,
                    shared: memory.shared,
                    page_size_log2: memory.page_size_log2,
                }
            }
            ExternalKind::Table => {
                let table = tables
                    .get(checked_usize(export.index)?)
                    .ok_or(ProtocolError::InvalidDocument)?;
                ExportRecordV1::Table {
                    name: export.name.to_owned(),
                    element: convert_reference_type(table.element_type)?,
                    minimum_elements: table.initial,
                    maximum_elements: table.maximum,
                    table64: table.table64,
                }
            }
            ExternalKind::Global | ExternalKind::Tag | ExternalKind::FuncExact => {
                return Err(ProtocolError::InvalidDocument);
            }
        };
        exports.push(record);
    }
    exports.sort_by(|left, right| export_sort_key(left).cmp(&export_sort_key(right)));
    exports_sha256(&exports)?;
    Ok((imports, exports))
}

#[cfg(feature = "artifact-host")]
pub fn validate_pdf_guest_module_policy_v1(
    wasm: &[u8],
) -> Result<(Vec<ImportRecordV1>, Vec<ExportRecordV1>)> {
    let (imports, exports) = extract_module_records(wasm)?;
    if imports
        .iter()
        .any(|record| !allowed_pdf_guest_import(record))
    {
        return Err(ProtocolError::InvalidDocument);
    }
    let start_count = exports
        .iter()
        .filter(|record| {
            matches!(record, ExportRecordV1::Func { name, params, results }
                if name == "_start" && params.is_empty() && results.is_empty())
        })
        .count();
    let main_void_count = exports
        .iter()
        .filter(|record| {
            matches!(record, ExportRecordV1::Func { name, params, results }
                if name == "__main_void"
                    && params.is_empty()
                    && results == &[CoreValueTypeV1::I32])
        })
        .count();
    if start_count != 1
        || main_void_count != 1
        || exports.iter().any(|record| {
            matches!(record, ExportRecordV1::Func { name, .. }
                if name != "_start" && name != "__main_void")
        })
    {
        return Err(ProtocolError::InvalidDocument);
    }
    Ok((imports, exports))
}

#[cfg(feature = "artifact-host")]
fn allowed_pdf_guest_import(import: &ImportRecordV1) -> bool {
    use CoreValueTypeV1::{I32, I64};

    if import.module != "wasi_snapshot_preview1" {
        return false;
    }
    let (params, results): (&[CoreValueTypeV1], &[CoreValueTypeV1]) = match import.name.as_str() {
        "args_get" | "args_sizes_get" | "environ_get" | "environ_sizes_get" | "random_get" => {
            (&[I32, I32], &[I32])
        }
        "fd_close" => (&[I32], &[I32]),
        "fd_fdstat_get" | "fd_filestat_get" | "fd_prestat_get" => (&[I32, I32], &[I32]),
        "fd_prestat_dir_name" => (&[I32, I32, I32], &[I32]),
        "fd_read" | "fd_write" => (&[I32, I32, I32, I32], &[I32]),
        "fd_seek" => (&[I32, I64, I32, I32], &[I32]),
        "path_filestat_get" => (&[I32, I32, I32, I32, I32], &[I32]),
        "path_open" => (&[I32, I32, I32, I32, I32, I64, I64, I32, I32], &[I32]),
        "proc_exit" => (&[I32], &[]),
        _ => return false,
    };
    import.params == params && import.results == results
}

#[cfg(feature = "artifact-host")]
fn convert_value_types(values: &[wasmparser::ValType]) -> Result<Vec<CoreValueTypeV1>> {
    values.iter().copied().map(convert_value_type).collect()
}

#[cfg(feature = "artifact-host")]
fn convert_value_type(value: wasmparser::ValType) -> Result<CoreValueTypeV1> {
    use wasmparser::ValType;

    match value {
        ValType::I32 => Ok(CoreValueTypeV1::I32),
        ValType::I64 => Ok(CoreValueTypeV1::I64),
        ValType::F32 => Ok(CoreValueTypeV1::F32),
        ValType::F64 => Ok(CoreValueTypeV1::F64),
        ValType::V128 => Ok(CoreValueTypeV1::V128),
        ValType::Ref(reference) => convert_reference_type(reference),
    }
}

#[cfg(feature = "artifact-host")]
fn convert_reference_type(reference: wasmparser::RefType) -> Result<CoreValueTypeV1> {
    if reference == wasmparser::RefType::FUNCREF {
        Ok(CoreValueTypeV1::Funcref)
    } else if reference == wasmparser::RefType::EXTERNREF {
        Ok(CoreValueTypeV1::Externref)
    } else {
        Err(ProtocolError::InvalidDocument)
    }
}

#[cfg(feature = "artifact-host")]
fn checked_usize(index: u32) -> Result<usize> {
    usize::try_from(index).map_err(|_| ProtocolError::SizeLimit)
}

#[cfg(feature = "artifact-host")]
fn checked_index<T>(values: &[T], index: u32) -> Result<Option<&T>> {
    Ok(values.get(checked_usize(index)?))
}

#[cfg(feature = "artifact-host")]
fn drain_const_expr(expression: &wasmparser::ConstExpr<'_>) -> Result<()> {
    let mut operators = expression.get_operators_reader();
    while !operators.eof() {
        let operator = operators
            .read()
            .map_err(|_| ProtocolError::InvalidDocument)?;
        validate_operator_shape(&operator)?;
    }
    Ok(())
}

#[cfg(feature = "artifact-host")]
fn validate_operator_shape(operator: &wasmparser::Operator<'_>) -> Result<()> {
    use wasmparser::{Operator, RefType};

    match operator {
        Operator::TypedSelect { ty } => {
            convert_value_type(*ty)?;
        }
        Operator::TypedSelectMulti { tys } => {
            for ty in tys {
                convert_value_type(*ty)?;
            }
        }
        Operator::RefNull { hty } => {
            let reference = RefType::new(true, *hty).ok_or(ProtocolError::InvalidDocument)?;
            convert_reference_type(reference)?;
        }
        _ => {}
    }

    if matches!(
        operator,
        Operator::RefEq
            | Operator::StructNew { .. }
            | Operator::StructNewDefault { .. }
            | Operator::StructGet { .. }
            | Operator::StructGetS { .. }
            | Operator::StructGetU { .. }
            | Operator::StructSet { .. }
            | Operator::ArrayNew { .. }
            | Operator::ArrayNewDefault { .. }
            | Operator::ArrayNewFixed { .. }
            | Operator::ArrayNewData { .. }
            | Operator::ArrayNewElem { .. }
            | Operator::ArrayGet { .. }
            | Operator::ArrayGetS { .. }
            | Operator::ArrayGetU { .. }
            | Operator::ArraySet { .. }
            | Operator::ArrayLen
            | Operator::ArrayFill { .. }
            | Operator::ArrayCopy { .. }
            | Operator::ArrayInitData { .. }
            | Operator::ArrayInitElem { .. }
            | Operator::RefTestNonNull { .. }
            | Operator::RefTestNullable { .. }
            | Operator::RefCastNonNull { .. }
            | Operator::RefCastNullable { .. }
            | Operator::BrOnCast { .. }
            | Operator::BrOnCastFail { .. }
            | Operator::AnyConvertExtern
            | Operator::ExternConvertAny
            | Operator::RefI31
            | Operator::I31GetS
            | Operator::I31GetU
            | Operator::StructNewDesc { .. }
            | Operator::StructNewDefaultDesc { .. }
            | Operator::RefGetDesc { .. }
            | Operator::RefCastDescEqNonNull { .. }
            | Operator::RefCastDescEqNullable { .. }
            | Operator::BrOnCastDescEq { .. }
            | Operator::BrOnCastDescEqFail { .. }
            | Operator::GlobalAtomicGet { .. }
            | Operator::GlobalAtomicSet { .. }
            | Operator::GlobalAtomicRmwAdd { .. }
            | Operator::GlobalAtomicRmwSub { .. }
            | Operator::GlobalAtomicRmwAnd { .. }
            | Operator::GlobalAtomicRmwOr { .. }
            | Operator::GlobalAtomicRmwXor { .. }
            | Operator::GlobalAtomicRmwXchg { .. }
            | Operator::GlobalAtomicRmwCmpxchg { .. }
            | Operator::TableAtomicGet { .. }
            | Operator::TableAtomicSet { .. }
            | Operator::TableAtomicRmwXchg { .. }
            | Operator::TableAtomicRmwCmpxchg { .. }
            | Operator::StructAtomicGet { .. }
            | Operator::StructAtomicGetS { .. }
            | Operator::StructAtomicGetU { .. }
            | Operator::StructAtomicSet { .. }
            | Operator::StructAtomicRmwAdd { .. }
            | Operator::StructAtomicRmwSub { .. }
            | Operator::StructAtomicRmwAnd { .. }
            | Operator::StructAtomicRmwOr { .. }
            | Operator::StructAtomicRmwXor { .. }
            | Operator::StructAtomicRmwXchg { .. }
            | Operator::StructAtomicRmwCmpxchg { .. }
            | Operator::ArrayAtomicGet { .. }
            | Operator::ArrayAtomicGetS { .. }
            | Operator::ArrayAtomicGetU { .. }
            | Operator::ArrayAtomicSet { .. }
            | Operator::ArrayAtomicRmwAdd { .. }
            | Operator::ArrayAtomicRmwSub { .. }
            | Operator::ArrayAtomicRmwAnd { .. }
            | Operator::ArrayAtomicRmwOr { .. }
            | Operator::ArrayAtomicRmwXor { .. }
            | Operator::ArrayAtomicRmwXchg { .. }
            | Operator::ArrayAtomicRmwCmpxchg { .. }
            | Operator::RefI31Shared
            | Operator::CallRef { .. }
            | Operator::ReturnCallRef { .. }
            | Operator::RefAsNonNull
            | Operator::BrOnNull { .. }
            | Operator::BrOnNonNull { .. }
            | Operator::ContNew { .. }
            | Operator::ContBind { .. }
            | Operator::Suspend { .. }
            | Operator::Resume { .. }
            | Operator::ResumeThrow { .. }
            | Operator::ResumeThrowRef { .. }
            | Operator::Switch { .. }
    ) {
        Err(ProtocolError::InvalidDocument)
    } else {
        Ok(())
    }
}

#[cfg(feature = "artifact-host")]
#[derive(Clone, Copy, Debug, Eq, Ord, PartialEq, PartialOrd)]
enum ClassifiedCargoDependencyKind {
    Normal,
    Build,
    Dev,
}

#[cfg(feature = "artifact-host")]
#[derive(Clone, Copy)]
struct ClassifiedCargoDependency<'a> {
    child_id: &'a str,
    kind: ClassifiedCargoDependencyKind,
    active: bool,
}

#[cfg(feature = "artifact-host")]
struct ClassifiedCargoMetadata<'a> {
    package_by_id: std::collections::BTreeMap<&'a str, &'a CargoPackage>,
    node_by_id: std::collections::BTreeMap<&'a str, &'a CargoNode>,
    dependencies_by_parent: std::collections::BTreeMap<&'a str, Vec<ClassifiedCargoDependency<'a>>>,
}

#[cfg(feature = "artifact-host")]
#[derive(Clone, Copy)]
enum CargoFeatureSpec<'a> {
    Local(&'a str),
    StrongDependency(&'a str),
    StrongForward { key: &'a str, child: &'a str },
    WeakForward { key: &'a str, child: &'a str },
}

#[cfg(feature = "artifact-host")]
#[derive(Default)]
struct CargoDependencyKeySummary {
    has_optional: bool,
}

#[cfg(feature = "artifact-host")]
fn classify_cargo_metadata_v1(metadata: &CargoMetadata) -> Result<ClassifiedCargoMetadata<'_>> {
    use std::collections::{BTreeMap, BTreeSet, VecDeque};

    let package_by_id = metadata
        .packages
        .iter()
        .map(|package| (package.id.as_str(), package))
        .collect::<BTreeMap<_, _>>();
    let node_by_id = metadata
        .resolve
        .nodes
        .iter()
        .map(|node| (node.id.as_str(), node))
        .collect::<BTreeMap<_, _>>();
    if package_by_id.len() != metadata.packages.len()
        || node_by_id.len() != metadata.resolve.nodes.len()
        || package_by_id.len() != node_by_id.len()
        || package_by_id.keys().ne(node_by_id.keys())
    {
        return Err(ProtocolError::InvalidDocument);
    }

    let mut dependency_target_by_id = BTreeMap::new();
    for package in &metadata.packages {
        let dependency_targets = package
            .targets
            .iter()
            .filter(|target| dependency_capable_target_kind(target).is_some())
            .collect::<Vec<_>>();
        let [target] = dependency_targets.as_slice() else {
            return Err(ProtocolError::InvalidDocument);
        };
        if target.name.is_empty()
            || normalize_cargo_external_name(&target.name).as_deref() != Some(target.name.as_str())
        {
            return Err(ProtocolError::InvalidDocument);
        }
        dependency_target_by_id.insert(package.id.as_str(), target.name.as_str());
    }

    let mut strong_dependency_keys_by_parent = BTreeMap::new();
    for package in &metadata.packages {
        let node = node_by_id
            .get(package.id.as_str())
            .copied()
            .ok_or(ProtocolError::InvalidDocument)?;
        if !node.features.is_empty() && !strictly_sorted_strings(&node.features) {
            return Err(ProtocolError::InvalidDocument);
        }

        let mut declarations_by_local_key = BTreeMap::<&str, CargoDependencyKeySummary>::new();
        for dependency in &package.dependencies {
            validate_cargo_package_dependency(dependency)?;
            let summary = declarations_by_local_key
                .entry(dependency_local_key(dependency))
                .or_default();
            if dependency.optional {
                summary.has_optional = true;
            }
        }

        let enabled = node
            .features
            .iter()
            .map(String::as_str)
            .collect::<BTreeSet<_>>();
        if enabled.len() != node.features.len()
            || enabled
                .iter()
                .any(|feature| !package.features.0.contains_key(*feature))
        {
            return Err(ProtocolError::InvalidDocument);
        }
        for (feature, specs) in &package.features.0 {
            if !valid_cargo_feature_atom(feature) {
                return Err(ProtocolError::InvalidDocument);
            }
            let mut unique_specs = BTreeSet::new();
            for spec in specs {
                if !unique_specs.insert(spec.as_str()) {
                    return Err(ProtocolError::InvalidDocument);
                }
                validate_cargo_feature_spec(
                    parse_cargo_feature_spec(spec)?,
                    &package.features.0,
                    &declarations_by_local_key,
                )?;
            }
        }
        let implicit_optional_features = package
            .features
            .0
            .iter()
            .filter(|(feature, specs)| {
                declarations_by_local_key
                    .get(feature.as_str())
                    .is_some_and(|dependency| dependency.has_optional)
                    && specs
                        .iter()
                        .any(|spec| spec.strip_prefix("dep:") == Some(feature.as_str()))
            })
            .map(|(feature, _)| feature.as_str())
            .collect::<BTreeSet<_>>();

        let mut pending = VecDeque::from_iter(enabled.iter().copied());
        let mut visited = BTreeSet::new();
        let mut strong_dependency_keys = BTreeSet::new();
        while let Some(feature) = pending.pop_front() {
            if !visited.insert(feature) {
                continue;
            }
            let specs = package
                .features
                .0
                .get(feature)
                .ok_or(ProtocolError::InvalidDocument)?;
            for spec in specs {
                match parse_cargo_feature_spec(spec)? {
                    CargoFeatureSpec::Local(local) => {
                        if !enabled.contains(local) {
                            return Err(ProtocolError::InvalidDocument);
                        }
                        pending.push_back(local);
                    }
                    CargoFeatureSpec::StrongDependency(key) => {
                        strong_dependency_keys.insert(key);
                    }
                    CargoFeatureSpec::StrongForward { key, .. } => {
                        strong_dependency_keys.insert(key);
                        if implicit_optional_features.contains(key) {
                            if !enabled.contains(key) {
                                return Err(ProtocolError::InvalidDocument);
                            }
                            pending.push_back(key);
                        }
                    }
                    CargoFeatureSpec::WeakForward { .. } => {}
                }
            }
        }
        if visited != enabled {
            return Err(ProtocolError::InvalidDocument);
        }
        strong_dependency_keys_by_parent.insert(package.id.as_str(), strong_dependency_keys);
    }

    let mut dependencies_by_parent = BTreeMap::new();
    for package in &metadata.packages {
        let node = node_by_id
            .get(package.id.as_str())
            .copied()
            .ok_or(ProtocolError::InvalidDocument)?;
        let strong_keys = strong_dependency_keys_by_parent
            .get(package.id.as_str())
            .ok_or(ProtocolError::InvalidDocument)?;
        let mut declaration_join_index = BTreeMap::<
            (
                String,
                Option<String>,
                Option<String>,
                ClassifiedCargoDependencyKind,
                Option<String>,
            ),
            Vec<&CargoPackageDependency>,
        >::new();
        for declaration in &package.dependencies {
            let external_alias = declaration
                .rename
                .as_deref()
                .map(normalize_cargo_external_name)
                .map(|name| name.ok_or(ProtocolError::InvalidDocument))
                .transpose()?;
            declaration_join_index
                .entry((
                    declaration.name.clone(),
                    declaration.source.clone(),
                    external_alias,
                    classify_cargo_dependency_kind(declaration.kind.as_deref())?,
                    declaration.target.clone(),
                ))
                .or_default()
                .push(declaration);
        }
        let mut classified = Vec::new();
        let mut seen_join_keys = BTreeSet::new();
        for dependency in &node.deps {
            if dependency.dep_kinds.is_empty()
                || normalize_cargo_external_name(&dependency.name).as_deref()
                    != Some(dependency.name.as_str())
            {
                return Err(ProtocolError::InvalidDocument);
            }
            let child = package_by_id
                .get(dependency.pkg.as_str())
                .copied()
                .ok_or(ProtocolError::InvalidDocument)?;
            let child_target_name = dependency_target_by_id
                .get(dependency.pkg.as_str())
                .copied()
                .ok_or(ProtocolError::InvalidDocument)?;
            for dep_kind in &dependency.dep_kinds {
                let kind = classify_cargo_dependency_kind(dep_kind.kind.as_deref())?;
                let join_key = (
                    dependency.pkg.as_str(),
                    dependency.name.as_str(),
                    kind,
                    dep_kind.target.as_deref(),
                );
                if !seen_join_keys.insert(join_key) {
                    return Err(ProtocolError::InvalidDocument);
                }
                let mut declaration = None;
                if dependency.name == child_target_name
                    && let Some(candidates) = declaration_join_index.get(&(
                        child.name.clone(),
                        child.source.clone(),
                        None,
                        kind,
                        dep_kind.target.clone(),
                    ))
                {
                    for &candidate in candidates {
                        if declaration.replace(candidate).is_some() {
                            return Err(ProtocolError::InvalidDocument);
                        }
                    }
                }
                if let Some(candidates) = declaration_join_index.get(&(
                    child.name.clone(),
                    child.source.clone(),
                    Some(dependency.name.clone()),
                    kind,
                    dep_kind.target.clone(),
                )) {
                    for &candidate in candidates {
                        if declaration.replace(candidate).is_some() {
                            return Err(ProtocolError::InvalidDocument);
                        }
                    }
                }
                let declaration = declaration.ok_or(ProtocolError::InvalidDocument)?;
                let active = !declaration.optional
                    || strong_keys.contains(dependency_local_key(declaration));
                classified.push(ClassifiedCargoDependency {
                    child_id: dependency.pkg.as_str(),
                    kind,
                    active,
                });
            }
        }
        dependencies_by_parent.insert(package.id.as_str(), classified);
    }

    Ok(ClassifiedCargoMetadata {
        package_by_id,
        node_by_id,
        dependencies_by_parent,
    })
}

#[cfg(feature = "artifact-host")]
fn validate_cargo_package_dependency(dependency: &CargoPackageDependency) -> Result<()> {
    if dependency.name.is_empty()
        || dependency.req.is_empty()
        || dependency
            .name
            .bytes()
            .chain(dependency.req.bytes())
            .any(|byte| byte.is_ascii_control())
        || dependency.registry.is_some()
        || dependency
            .rename
            .as_deref()
            .is_some_and(|rename| normalize_cargo_external_name(rename).is_none())
        || dependency.target.as_deref().is_some_and(|target| {
            target.is_empty() || target.bytes().any(|byte| byte.is_ascii_control())
        })
        || dependency
            .path
            .as_deref()
            .is_some_and(|path| path.is_empty() || !std::path::Path::new(path).is_absolute())
        || dependency
            .features
            .iter()
            .any(|feature| !valid_cargo_feature_atom(feature))
    {
        return Err(ProtocolError::InvalidDocument);
    }
    classify_cargo_dependency_kind(dependency.kind.as_deref())?;
    match dependency.source.as_deref() {
        None if dependency.path.is_some() => {}
        Some("registry+https://github.com/rust-lang/crates.io-index")
            if dependency.path.is_none() => {}
        _ => return Err(ProtocolError::InvalidDocument),
    }
    let _ = dependency.uses_default_features;
    Ok(())
}

#[cfg(feature = "artifact-host")]
fn validate_cargo_feature_spec(
    spec: CargoFeatureSpec<'_>,
    features: &std::collections::BTreeMap<String, Vec<String>>,
    dependencies: &std::collections::BTreeMap<&str, CargoDependencyKeySummary>,
) -> Result<()> {
    let summary = |key: &str| dependencies.get(key).ok_or(ProtocolError::InvalidDocument);
    match spec {
        CargoFeatureSpec::Local(local) => {
            if !features.contains_key(local) {
                return Err(ProtocolError::InvalidDocument);
            }
        }
        CargoFeatureSpec::StrongDependency(key) => {
            let dependency = summary(key)?;
            if !dependency.has_optional {
                return Err(ProtocolError::InvalidDocument);
            }
        }
        CargoFeatureSpec::StrongForward { key, child } => {
            let _ = child;
            summary(key)?;
        }
        CargoFeatureSpec::WeakForward { key, child } => {
            let _ = child;
            let dependency = summary(key)?;
            if !dependency.has_optional {
                return Err(ProtocolError::InvalidDocument);
            }
        }
    }
    Ok(())
}

#[cfg(feature = "artifact-host")]
fn parse_cargo_feature_spec(spec: &str) -> Result<CargoFeatureSpec<'_>> {
    if spec.is_empty() || spec.bytes().any(|byte| byte.is_ascii_control()) {
        return Err(ProtocolError::InvalidDocument);
    }
    if let Some(key) = spec.strip_prefix("dep:") {
        if valid_cargo_feature_atom(key) {
            return Ok(CargoFeatureSpec::StrongDependency(key));
        }
        return Err(ProtocolError::InvalidDocument);
    }
    if spec.contains(':') {
        return Err(ProtocolError::InvalidDocument);
    }
    if let Some((key, child)) = spec.split_once("?/") {
        if valid_cargo_feature_atom(key)
            && valid_cargo_feature_atom(child)
            && !child.contains('/')
            && !key.contains('?')
        {
            return Ok(CargoFeatureSpec::WeakForward { key, child });
        }
        return Err(ProtocolError::InvalidDocument);
    }
    if spec.contains('?') {
        return Err(ProtocolError::InvalidDocument);
    }
    if let Some((key, child)) = spec.split_once('/') {
        if valid_cargo_feature_atom(key) && valid_cargo_feature_atom(child) && !child.contains('/')
        {
            return Ok(CargoFeatureSpec::StrongForward { key, child });
        }
        return Err(ProtocolError::InvalidDocument);
    }
    if valid_cargo_feature_atom(spec) {
        Ok(CargoFeatureSpec::Local(spec))
    } else {
        Err(ProtocolError::InvalidDocument)
    }
}

#[cfg(feature = "artifact-host")]
fn valid_cargo_feature_atom(value: &str) -> bool {
    !value.is_empty()
        && value.bytes().all(|byte| {
            !byte.is_ascii_control()
                && !byte.is_ascii_whitespace()
                && !matches!(byte, b':' | b'/' | b'?')
        })
}

#[cfg(feature = "artifact-host")]
fn dependency_local_key(dependency: &CargoPackageDependency) -> &str {
    dependency.rename.as_deref().unwrap_or(&dependency.name)
}

#[cfg(feature = "artifact-host")]
fn normalize_cargo_external_name(value: &str) -> Option<String> {
    if value.is_empty()
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'_'))
    {
        return None;
    }
    Some(value.replace('-', "_"))
}

#[cfg(feature = "artifact-host")]
fn classify_cargo_dependency_kind(kind: Option<&str>) -> Result<ClassifiedCargoDependencyKind> {
    match kind {
        None => Ok(ClassifiedCargoDependencyKind::Normal),
        Some("build") => Ok(ClassifiedCargoDependencyKind::Build),
        Some("dev") => Ok(ClassifiedCargoDependencyKind::Dev),
        Some(_) => Err(ProtocolError::InvalidDocument),
    }
}

#[cfg(feature = "artifact-host")]
pub fn normalize_dependency_records(
    guest_metadata_json: &[u8],
    full_workspace_metadata_json: &[u8],
    production_cargo_lock_v4: &[u8],
    guest_resolution_cargo_lock_v4: &[u8],
    root_package_name: &str,
) -> Result<Vec<DependencyRecordV1>> {
    Ok(normalize_dependency_graph_v1(
        guest_metadata_json,
        full_workspace_metadata_json,
        production_cargo_lock_v4,
        guest_resolution_cargo_lock_v4,
        root_package_name,
    )?
    .records)
}

#[cfg(feature = "artifact-host")]
pub fn normalize_dependency_graph_v1(
    guest_metadata_json: &[u8],
    full_workspace_metadata_json: &[u8],
    production_cargo_lock_v4: &[u8],
    guest_resolution_cargo_lock_v4: &[u8],
    root_package_name: &str,
) -> Result<ArtifactDependencyGraphV1> {
    use std::collections::{BTreeMap, BTreeSet, VecDeque};

    validate_guest_resolution_lock_projection_v1(
        production_cargo_lock_v4,
        guest_resolution_cargo_lock_v4,
    )?;
    let production_lock = parse_cargo_lock_v4(production_cargo_lock_v4)?;
    let resolution_lock = parse_cargo_lock_v4(guest_resolution_cargo_lock_v4)?;
    let guest_metadata = parse_cargo_metadata_v1(guest_metadata_json)?;
    let full_metadata = parse_cargo_metadata_v1(full_workspace_metadata_json)?;
    if root_package_name.is_empty()
        || root_package_name
            .bytes()
            .any(|byte| byte.is_ascii_control() || byte.is_ascii_whitespace())
    {
        return Err(ProtocolError::InvalidDocument);
    }
    let guest_classification = classify_cargo_metadata_v1(&guest_metadata)?;
    let full_classification = classify_cargo_metadata_v1(&full_metadata)?;
    let package_by_id = &guest_classification.package_by_id;
    let node_by_id = &guest_classification.node_by_id;

    let roots = guest_metadata
        .packages
        .iter()
        .filter(|package| {
            package.name == root_package_name
                && package.source.is_none()
                && workspace_manifest(
                    &guest_metadata.workspace_root,
                    &package.manifest_path,
                    &package.name,
                )
        })
        .collect::<Vec<_>>();
    let [root] = roots.as_slice() else {
        return Err(ProtocolError::InvalidDocument);
    };

    let mut normalized_by_id = BTreeMap::new();
    let mut guest_id_by_normalized = BTreeMap::new();
    for package in &guest_metadata.packages {
        let normalized = normalize_package_id_from_locks(
            package,
            &guest_metadata.workspace_root,
            &production_lock,
            Some(&resolution_lock),
        )?;
        if normalized_by_id
            .insert(package.id.as_str(), normalized.clone())
            .is_some()
            || guest_id_by_normalized
                .insert(normalized, package.id.as_str())
                .is_some()
        {
            return Err(ProtocolError::InvalidDocument);
        }
    }

    let mut reached = BTreeSet::new();
    let mut pending = VecDeque::from([root.id.as_str()]);
    while let Some(id) = pending.pop_front() {
        if !reached.insert(id) {
            continue;
        }
        let dependencies = guest_classification
            .dependencies_by_parent
            .get(id)
            .ok_or(ProtocolError::InvalidDocument)?;
        for dependency in dependencies {
            if dependency.active
                && matches!(
                    dependency.kind,
                    ClassifiedCargoDependencyKind::Normal | ClassifiedCargoDependencyKind::Build
                )
            {
                pending.push_back(dependency.child_id);
            }
        }
    }

    let mut records = Vec::with_capacity(reached.len());
    let mut reached_protocol = 0_usize;
    for id in &reached {
        let node = node_by_id
            .get(id)
            .copied()
            .ok_or(ProtocolError::InvalidDocument)?;
        let package = package_by_id
            .get(id)
            .copied()
            .ok_or(ProtocolError::InvalidDocument)?;
        let features = node.features.clone();
        if features.iter().any(|feature| feature == "artifact-host") {
            return Err(ProtocolError::InvalidDocument);
        }
        if package.name == "heleos-pdf-protocol" {
            reached_protocol = reached_protocol
                .checked_add(1)
                .ok_or(ProtocolError::SizeLimit)?;
            if features != ["default"] {
                return Err(ProtocolError::InvalidDocument);
            }
        }
        if package.name == "toml" || package.name == "wasmparser" || package.name == "zlib-rs" {
            return Err(ProtocolError::InvalidDocument);
        }
        let mut edges = Vec::new();
        for dependency in guest_classification
            .dependencies_by_parent
            .get(id)
            .ok_or(ProtocolError::InvalidDocument)?
        {
            if !dependency.active || !reached.contains(dependency.child_id) {
                continue;
            }
            let normalized = normalized_by_id
                .get(dependency.child_id)
                .ok_or(ProtocolError::InvalidDocument)?;
            let kind = match dependency.kind {
                ClassifiedCargoDependencyKind::Normal => Some(DependencyKindV1::Normal),
                ClassifiedCargoDependencyKind::Build => Some(DependencyKindV1::Build),
                ClassifiedCargoDependencyKind::Dev => None,
            };
            if let Some(kind) = kind {
                edges.push(DependencyEdgeV1 {
                    kind,
                    id: normalized.clone(),
                });
            }
        }
        edges.sort_by(|left, right| edge_sort_key(left).cmp(&edge_sort_key(right)));
        edges.dedup();
        records.push(DependencyRecordV1 {
            id: normalized_by_id
                .get(id)
                .ok_or(ProtocolError::InvalidDocument)?
                .clone(),
            features,
            edges,
        });
    }
    if reached_protocol != 1 {
        return Err(ProtocolError::InvalidDocument);
    }
    records.sort_by(|left, right| left.id.as_bytes().cmp(right.id.as_bytes()));
    dependency_graph_sha256(&records)?;

    require_guest_graph_subset_of_full(
        &records,
        &full_metadata,
        &full_classification,
        &production_lock,
        root_package_name,
    )?;

    let mut registry_roots = Vec::new();
    for id in &reached {
        let package = package_by_id
            .get(id)
            .copied()
            .ok_or(ProtocolError::InvalidDocument)?;
        if package.source.as_deref()
            == Some("registry+https://github.com/rust-lang/crates.io-index")
        {
            let manifest = std::path::Path::new(&package.manifest_path);
            if !manifest.is_absolute()
                || manifest.file_name().and_then(|name| name.to_str()) != Some("Cargo.toml")
            {
                return Err(ProtocolError::InvalidDocument);
            }
            let physical_root = manifest
                .parent()
                .filter(|parent| parent.file_name().is_some())
                .ok_or(ProtocolError::InvalidDocument)?
                .to_path_buf();
            registry_roots.push(ArtifactRegistryRootV1 {
                normalized_dependency_id: normalized_by_id
                    .get(id)
                    .ok_or(ProtocolError::InvalidDocument)?
                    .clone(),
                physical_root,
            });
        }
    }
    registry_roots.sort_by(|left, right| {
        left.normalized_dependency_id
            .as_bytes()
            .cmp(right.normalized_dependency_id.as_bytes())
    });
    if registry_roots.windows(2).any(|pair| {
        pair[0].normalized_dependency_id.as_bytes() >= pair[1].normalized_dependency_id.as_bytes()
    }) {
        return Err(ProtocolError::InvalidDocument);
    }
    Ok(ArtifactDependencyGraphV1 {
        records,
        registry_roots,
    })
}

#[cfg(feature = "artifact-host")]
fn parse_cargo_metadata_v1(bytes: &[u8]) -> Result<CargoMetadata> {
    const MAX_METADATA_JSON_BYTES: usize = 32 * 1024 * 1024;
    if bytes.is_empty() || bytes.len() > MAX_METADATA_JSON_BYTES {
        return Err(ProtocolError::SizeLimit);
    }
    let metadata: CargoMetadata =
        serde_json::from_slice(bytes).map_err(|_| ProtocolError::InvalidDocument)?;
    validate_cargo_metadata_bounds(&metadata, MAX_METADATA_JSON_BYTES)?;
    if metadata.packages.is_empty()
        || metadata.resolve.nodes.is_empty()
        || metadata.workspace_root.is_empty()
        || !std::path::Path::new(&metadata.workspace_root).is_absolute()
    {
        return Err(ProtocolError::InvalidDocument);
    }
    Ok(metadata)
}

#[cfg(feature = "artifact-host")]
fn validate_cargo_metadata_bounds(
    metadata: &CargoMetadata,
    max_decoded_string_bytes: usize,
) -> Result<()> {
    const MAX_METADATA_ENTRIES: usize = 65_536;

    let mut declarations = 0_usize;
    let mut feature_keys = 0_usize;
    let mut feature_specs = 0_usize;
    let mut enabled_features = 0_usize;
    let mut resolve_dependencies = 0_usize;
    let mut dependency_kinds = 0_usize;
    let mut decoded_string_bytes = 0_usize;
    let mut add_string = |value: &str| -> Result<()> {
        if value.len() > max_decoded_string_bytes {
            return Err(ProtocolError::SizeLimit);
        }
        decoded_string_bytes = decoded_string_bytes
            .checked_add(value.len())
            .ok_or(ProtocolError::SizeLimit)?;
        if decoded_string_bytes > max_decoded_string_bytes {
            return Err(ProtocolError::SizeLimit);
        }
        Ok(())
    };

    if metadata.packages.len() > MAX_METADATA_ENTRIES
        || metadata.resolve.nodes.len() > MAX_METADATA_ENTRIES
    {
        return Err(ProtocolError::SizeLimit);
    }
    add_string(&metadata.workspace_root)?;
    for package in &metadata.packages {
        for value in [
            package.id.as_str(),
            package.name.as_str(),
            package.version.as_str(),
            package.manifest_path.as_str(),
        ] {
            add_string(value)?;
        }
        if let Some(value) = package.source.as_deref() {
            add_string(value)?;
        }
        if let Some(value) = package.checksum.as_deref() {
            add_string(value)?;
        }
        declarations = declarations
            .checked_add(package.dependencies.len())
            .ok_or(ProtocolError::SizeLimit)?;
        feature_keys = feature_keys
            .checked_add(package.features.0.len())
            .ok_or(ProtocolError::SizeLimit)?;
        for target in &package.targets {
            add_string(&target.name)?;
            add_string(&target.src_path)?;
            add_string(&target.edition)?;
            for value in target.kind.iter().chain(&target.crate_types) {
                add_string(value)?;
            }
            for value in &target.required_features {
                add_string(value)?;
            }
        }
        for dependency in &package.dependencies {
            add_string(&dependency.name)?;
            add_string(&dependency.req)?;
            for value in [
                dependency.source.as_deref(),
                dependency.kind.as_deref(),
                dependency.rename.as_deref(),
                dependency.target.as_deref(),
                dependency.registry.as_deref(),
                dependency.path.as_deref(),
            ]
            .into_iter()
            .flatten()
            {
                add_string(value)?;
            }
            for value in &dependency.features {
                add_string(value)?;
            }
        }
        for (feature, specs) in &package.features.0 {
            add_string(feature)?;
            feature_specs = feature_specs
                .checked_add(specs.len())
                .ok_or(ProtocolError::SizeLimit)?;
            for spec in specs {
                add_string(spec)?;
            }
        }
    }
    for node in &metadata.resolve.nodes {
        add_string(&node.id)?;
        enabled_features = enabled_features
            .checked_add(node.features.len())
            .ok_or(ProtocolError::SizeLimit)?;
        resolve_dependencies = resolve_dependencies
            .checked_add(node.deps.len())
            .ok_or(ProtocolError::SizeLimit)?;
        for feature in &node.features {
            add_string(feature)?;
        }
        for dependency in &node.deps {
            add_string(&dependency.name)?;
            add_string(&dependency.pkg)?;
            dependency_kinds = dependency_kinds
                .checked_add(dependency.dep_kinds.len())
                .ok_or(ProtocolError::SizeLimit)?;
            for kind in &dependency.dep_kinds {
                if let Some(value) = kind.kind.as_deref() {
                    add_string(value)?;
                }
                if let Some(value) = kind.target.as_deref() {
                    add_string(value)?;
                }
            }
        }
    }
    if [
        declarations,
        feature_keys,
        feature_specs,
        enabled_features,
        resolve_dependencies,
        dependency_kinds,
    ]
    .into_iter()
    .any(|count| count > MAX_METADATA_ENTRIES)
    {
        return Err(ProtocolError::SizeLimit);
    }
    Ok(())
}

#[cfg(feature = "artifact-host")]
fn normalize_package_id_from_locks(
    package: &CargoPackage,
    workspace_root: &str,
    production_lock: &ParsedCargoLockV4,
    resolution_lock: Option<&ParsedCargoLockV4>,
) -> Result<String> {
    if package.id.is_empty()
        || package.name.is_empty()
        || package.version.is_empty()
        || package
            .name
            .bytes()
            .chain(package.version.bytes())
            .any(|byte| byte.is_ascii_control() || byte.is_ascii_whitespace())
    {
        return Err(ProtocolError::InvalidDocument);
    }
    let identity = CargoLockIdentity {
        name: package.name.clone(),
        version: package.version.clone(),
        source: package.source.clone(),
    };
    let production = production_lock
        .packages
        .get(&identity)
        .ok_or(ProtocolError::InvalidDocument)?;
    let resolution = resolution_lock
        .map(|lock| {
            lock.packages
                .get(&identity)
                .ok_or(ProtocolError::InvalidDocument)
        })
        .transpose()?;
    match package.source.as_deref() {
        None => {
            if !workspace_manifest(workspace_root, &package.manifest_path, &package.name)
                || package.checksum.is_some()
                || production.checksum.is_some()
                || resolution.is_some_and(|package| package.checksum.is_some())
            {
                return Err(ProtocolError::InvalidDocument);
            }
            Ok(format!("workspace:{}@{}", package.name, package.version))
        }
        Some("registry+https://github.com/rust-lang/crates.io-index") => {
            let checksum = production
                .checksum
                .as_deref()
                .filter(|checksum| is_lower_sha256(checksum))
                .ok_or(ProtocolError::InvalidDocument)?;
            if resolution.is_some_and(|package| package.checksum.as_deref() != Some(checksum))
                || package
                    .checksum
                    .as_deref()
                    .is_some_and(|metadata_checksum| metadata_checksum != checksum)
            {
                return Err(ProtocolError::InvalidDocument);
            }
            Ok(format!(
                "registry:{}@{}#{}",
                package.name, package.version, checksum
            ))
        }
        _ => Err(ProtocolError::InvalidDocument),
    }
}

#[cfg(feature = "artifact-host")]
fn require_guest_graph_subset_of_full(
    guest_records: &[DependencyRecordV1],
    full_metadata: &CargoMetadata,
    full_classification: &ClassifiedCargoMetadata<'_>,
    production_lock: &ParsedCargoLockV4,
    root_package_name: &str,
) -> Result<()> {
    use std::collections::{BTreeMap, BTreeSet, VecDeque};

    let node_by_id = &full_classification.node_by_id;

    let roots = full_metadata
        .packages
        .iter()
        .filter(|package| {
            package.name == root_package_name
                && package.source.is_none()
                && workspace_manifest(
                    &full_metadata.workspace_root,
                    &package.manifest_path,
                    &package.name,
                )
        })
        .collect::<Vec<_>>();
    let [root] = roots.as_slice() else {
        return Err(ProtocolError::InvalidDocument);
    };
    let mut reached = BTreeSet::new();
    let mut pending = VecDeque::from([root.id.as_str()]);
    while let Some(id) = pending.pop_front() {
        if !reached.insert(id) {
            continue;
        }
        for dependency in full_classification
            .dependencies_by_parent
            .get(id)
            .ok_or(ProtocolError::InvalidDocument)?
        {
            if dependency.active
                && matches!(
                    dependency.kind,
                    ClassifiedCargoDependencyKind::Normal | ClassifiedCargoDependencyKind::Build
                )
            {
                pending.push_back(dependency.child_id);
            }
        }
    }

    let mut normalized_by_id = BTreeMap::new();
    let mut id_by_normalized = BTreeMap::new();
    for id in &reached {
        let package = full_classification
            .package_by_id
            .get(id)
            .copied()
            .ok_or(ProtocolError::InvalidDocument)?;
        let normalized = normalize_package_id_from_locks(
            package,
            &full_metadata.workspace_root,
            production_lock,
            None,
        )?;
        if normalized_by_id.insert(*id, normalized.clone()).is_some()
            || id_by_normalized.insert(normalized, *id).is_some()
        {
            return Err(ProtocolError::InvalidDocument);
        }
    }

    for guest in guest_records {
        let full_id = id_by_normalized
            .get(&guest.id)
            .copied()
            .ok_or(ProtocolError::InvalidDocument)?;
        let node = node_by_id
            .get(full_id)
            .copied()
            .ok_or(ProtocolError::InvalidDocument)?;
        let full_features = node
            .features
            .iter()
            .map(String::as_str)
            .collect::<BTreeSet<_>>();
        if full_features.len() != node.features.len()
            || node.features.iter().any(String::is_empty)
            || guest
                .features
                .iter()
                .any(|feature| !full_features.contains(feature.as_str()))
        {
            return Err(ProtocolError::InvalidDocument);
        }

        let mut full_edges = BTreeSet::new();
        for dependency in full_classification
            .dependencies_by_parent
            .get(full_id)
            .ok_or(ProtocolError::InvalidDocument)?
        {
            if !dependency.active {
                continue;
            }
            let kind = match dependency.kind {
                ClassifiedCargoDependencyKind::Normal => DependencyKindV1::Normal,
                ClassifiedCargoDependencyKind::Build => DependencyKindV1::Build,
                ClassifiedCargoDependencyKind::Dev => continue,
            };
            let target = normalized_by_id
                .get(dependency.child_id)
                .ok_or(ProtocolError::InvalidDocument)?;
            full_edges.insert((dependency_kind_ordinal(kind), target.as_str()));
        }
        if guest.edges.iter().any(|edge| {
            !full_edges.contains(&(dependency_kind_ordinal(edge.kind), edge.id.as_str()))
        }) {
            return Err(ProtocolError::InvalidDocument);
        }
    }
    Ok(())
}

#[cfg(feature = "artifact-host")]
const fn dependency_kind_ordinal(kind: DependencyKindV1) -> u8 {
    match kind {
        DependencyKindV1::Normal => 0,
        DependencyKindV1::Build => 1,
    }
}

fn hash_jcs_records<T: Serialize>(domain: &[u8], records: &[T]) -> Result<String> {
    let encoded = serde_jcs::to_vec(records).map_err(|_| ProtocolError::InvalidDocument)?;
    let mut hasher = Sha256::new();
    hasher.update(domain);
    hasher.update(encoded);
    Ok(lower_hex(&hasher.finalize()))
}

fn strictly_sorted_strings(values: &[String]) -> bool {
    !values.iter().any(String::is_empty)
        && !values
            .windows(2)
            .any(|pair| pair[0].as_bytes() >= pair[1].as_bytes())
}

fn edge_sort_key(edge: &DependencyEdgeV1) -> (u8, &[u8]) {
    let ordinal = match edge.kind {
        DependencyKindV1::Normal => 0,
        DependencyKindV1::Build => 1,
    };
    (ordinal, edge.id.as_bytes())
}

fn strictly_sorted_edges(edges: &[DependencyEdgeV1]) -> bool {
    edges.iter().all(|edge| !edge.id.is_empty())
        && !edges
            .windows(2)
            .any(|pair| edge_sort_key(&pair[0]) >= edge_sort_key(&pair[1]))
}

fn core_value_type_allowed(_: CoreValueTypeV1) -> bool {
    true
}

fn import_sort_key(
    record: &ImportRecordV1,
) -> (&[u8], &[u8], u8, &[CoreValueTypeV1], &[CoreValueTypeV1]) {
    (
        record.module.as_bytes(),
        record.name.as_bytes(),
        0,
        &record.params,
        &record.results,
    )
}

fn export_sort_key(
    record: &ExportRecordV1,
) -> (
    &[u8],
    u8,
    Vec<u64>,
    Vec<CoreValueTypeV1>,
    Vec<CoreValueTypeV1>,
) {
    match record {
        ExportRecordV1::Func {
            name,
            params,
            results,
        } => (
            name.as_bytes(),
            0,
            Vec::new(),
            params.clone(),
            results.clone(),
        ),
        ExportRecordV1::Memory {
            name,
            minimum_pages,
            maximum_pages,
            memory64,
            shared,
            page_size_log2,
        } => (
            name.as_bytes(),
            1,
            vec![
                *minimum_pages,
                maximum_pages.unwrap_or(u64::MAX),
                u64::from(*memory64),
                u64::from(*shared),
                u64::from(page_size_log2.unwrap_or(u32::MAX)),
            ],
            Vec::new(),
            Vec::new(),
        ),
        ExportRecordV1::Table {
            name,
            element,
            minimum_elements,
            maximum_elements,
            table64,
        } => (
            name.as_bytes(),
            2,
            vec![
                core_value_ordinal(*element),
                *minimum_elements,
                maximum_elements.unwrap_or(u64::MAX),
                u64::from(*table64),
            ],
            Vec::new(),
            Vec::new(),
        ),
    }
}

fn valid_export(record: &ExportRecordV1) -> bool {
    match record {
        ExportRecordV1::Func {
            name,
            params,
            results,
        } => {
            !name.is_empty()
                && params.iter().all(|value| core_value_type_allowed(*value))
                && results.iter().all(|value| core_value_type_allowed(*value))
        }
        ExportRecordV1::Memory {
            name,
            minimum_pages,
            maximum_pages,
            ..
        } => {
            !name.is_empty()
                && safe_u64(*minimum_pages)
                && maximum_pages
                    .is_none_or(|maximum| safe_u64(maximum) && maximum >= *minimum_pages)
        }
        ExportRecordV1::Table {
            name,
            element,
            minimum_elements,
            maximum_elements,
            ..
        } => {
            !name.is_empty()
                && matches!(
                    element,
                    CoreValueTypeV1::Funcref | CoreValueTypeV1::Externref
                )
                && safe_u64(*minimum_elements)
                && maximum_elements
                    .is_none_or(|maximum| safe_u64(maximum) && maximum >= *minimum_elements)
        }
    }
}

const fn core_value_ordinal(value: CoreValueTypeV1) -> u64 {
    match value {
        CoreValueTypeV1::I32 => 0,
        CoreValueTypeV1::I64 => 1,
        CoreValueTypeV1::F32 => 2,
        CoreValueTypeV1::F64 => 3,
        CoreValueTypeV1::V128 => 4,
        CoreValueTypeV1::Funcref => 5,
        CoreValueTypeV1::Externref => 6,
    }
}

#[cfg(feature = "artifact-host")]
#[derive(Clone, Deserialize)]
struct CargoMetadata {
    packages: Vec<CargoPackage>,
    workspace_root: String,
    resolve: CargoResolve,
}

#[cfg(feature = "artifact-host")]
#[derive(Clone, Deserialize)]
struct CargoPackage {
    id: String,
    name: String,
    version: String,
    source: Option<String>,
    checksum: Option<String>,
    manifest_path: String,
    #[serde(default)]
    targets: Vec<CargoTarget>,
    dependencies: Vec<CargoPackageDependency>,
    features: CargoFeatureTable,
}

#[cfg(feature = "artifact-host")]
#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
struct CargoPackageDependency {
    name: String,
    #[serde(deserialize_with = "deserialize_required_nullable")]
    source: Option<String>,
    req: String,
    #[serde(deserialize_with = "deserialize_required_nullable")]
    kind: Option<String>,
    #[serde(deserialize_with = "deserialize_required_nullable")]
    rename: Option<String>,
    optional: bool,
    uses_default_features: bool,
    features: Vec<String>,
    #[serde(deserialize_with = "deserialize_required_nullable")]
    target: Option<String>,
    #[serde(deserialize_with = "deserialize_required_nullable")]
    registry: Option<String>,
    #[serde(default)]
    path: Option<String>,
}

#[cfg(feature = "artifact-host")]
#[derive(Clone, Default)]
struct CargoFeatureTable(std::collections::BTreeMap<String, Vec<String>>);

#[cfg(feature = "artifact-host")]
impl<'de> Deserialize<'de> for CargoFeatureTable {
    fn deserialize<D>(deserializer: D) -> std::result::Result<Self, D::Error>
    where
        D: serde::Deserializer<'de>,
    {
        use serde::de::{Error as _, MapAccess, Visitor};

        struct FeatureTableVisitor;

        impl<'de> Visitor<'de> for FeatureTableVisitor {
            type Value = CargoFeatureTable;

            fn expecting(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
                formatter.write_str("a Cargo feature table with unique keys")
            }

            fn visit_map<A>(self, mut access: A) -> std::result::Result<Self::Value, A::Error>
            where
                A: MapAccess<'de>,
            {
                let mut features = std::collections::BTreeMap::new();
                while let Some((key, value)) = access.next_entry::<String, Vec<String>>()? {
                    if features.insert(key, value).is_some() {
                        return Err(A::Error::custom("duplicate Cargo feature key"));
                    }
                }
                Ok(CargoFeatureTable(features))
            }
        }

        deserializer.deserialize_map(FeatureTableVisitor)
    }
}

#[cfg(feature = "artifact-host")]
#[derive(Clone, Debug, Deserialize, Eq, PartialEq)]
#[serde(deny_unknown_fields)]
struct CargoTarget {
    kind: Vec<String>,
    crate_types: Vec<String>,
    name: String,
    src_path: String,
    edition: String,
    doc: bool,
    doctest: bool,
    test: bool,
    #[serde(default, rename = "required-features")]
    required_features: Vec<String>,
}

#[cfg(feature = "artifact-host")]
#[derive(Clone, Deserialize)]
struct CargoResolve {
    nodes: Vec<CargoNode>,
}

#[cfg(feature = "artifact-host")]
#[derive(Clone, Deserialize)]
struct CargoNode {
    id: String,
    features: Vec<String>,
    deps: Vec<CargoDependency>,
}

#[cfg(feature = "artifact-host")]
#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
struct CargoDependency {
    name: String,
    pkg: String,
    dep_kinds: Vec<CargoDependencyKind>,
}

#[cfg(feature = "artifact-host")]
#[derive(Clone, Deserialize)]
#[serde(deny_unknown_fields)]
struct CargoDependencyKind {
    #[serde(deserialize_with = "deserialize_required_nullable")]
    kind: Option<String>,
    #[serde(deserialize_with = "deserialize_required_nullable")]
    target: Option<String>,
}

#[cfg(feature = "artifact-host")]
fn deserialize_required_nullable<'de, D, T>(
    deserializer: D,
) -> std::result::Result<Option<T>, D::Error>
where
    D: serde::Deserializer<'de>,
    T: Deserialize<'de>,
{
    Option::<T>::deserialize(deserializer)
}

#[cfg(feature = "artifact-host")]
fn workspace_manifest(workspace_root: &str, manifest_path: &str, package_name: &str) -> bool {
    use std::path::{Component, Path};

    if package_name.is_empty()
        || package_name.contains(['/', '\\'])
        || package_name.bytes().any(|byte| byte.is_ascii_control())
    {
        return false;
    }
    let root = Path::new(workspace_root);
    let manifest = Path::new(manifest_path);
    let canonical_shape = |path: &Path| {
        path.is_absolute()
            && path.components().all(|component| {
                matches!(
                    component,
                    Component::Prefix(_) | Component::RootDir | Component::Normal(_)
                )
            })
    };
    canonical_shape(root)
        && canonical_shape(manifest)
        && manifest == root.join("crates").join(package_name).join("Cargo.toml")
}

pub fn encode_request_v1(request: &PdfRequestV1) -> Result<Vec<u8>> {
    validate_request(request)?;
    encode_framed(request, MAX_REQUEST_BYTES)
}

pub fn decode_request_v1(bytes: &[u8]) -> Result<PdfRequestV1> {
    let request = decode_framed(bytes, MAX_REQUEST_BYTES)?;
    validate_request(&request)?;
    Ok(request)
}

pub fn encode_response_v1(response: &PdfResponseV1, cap: usize) -> Result<Vec<u8>> {
    validate_response(response)?;
    encode_framed(response, checked_output_cap(cap)?)
}

pub fn decode_response_v1(bytes: &[u8], cap: usize) -> Result<PdfResponseV1> {
    let response = decode_framed(bytes, checked_output_cap(cap)?)?;
    validate_response(&response)?;
    Ok(response)
}

pub fn validate_response_echo(response: &PdfResponseV1, request: &PdfRequestV1) -> Result<()> {
    if response.protocol != request.protocol
        || response.input_sha256 != request.input_sha256
        || response.byte_length != request.byte_length
    {
        return Err(ProtocolError::InvalidDocument);
    }
    validate_response(response)
}

fn checked_output_cap(cap: usize) -> Result<usize> {
    if cap == 0 || cap > MAX_PROTOCOL_OUTPUT_BYTES {
        return Err(ProtocolError::SizeLimit);
    }
    Ok(cap)
}

fn encode_framed<T: Serialize>(value: &T, cap: usize) -> Result<Vec<u8>> {
    let mut encoded = serde_jcs::to_vec(value).map_err(|_| ProtocolError::InvalidDocument)?;
    let framed_len = encoded
        .len()
        .checked_add(1)
        .ok_or(ProtocolError::SizeLimit)?;
    if framed_len > cap {
        return Err(ProtocolError::SizeLimit);
    }
    encoded.push(b'\n');
    Ok(encoded)
}

fn decode_framed<T>(bytes: &[u8], cap: usize) -> Result<T>
where
    T: for<'de> Deserialize<'de> + Serialize,
{
    if bytes.is_empty() || bytes.len() > cap || bytes.last() != Some(&b'\n') {
        return Err(ProtocolError::SizeLimit);
    }
    let payload = &bytes[..bytes.len() - 1];
    if payload.is_empty() || payload.contains(&b'\r') || payload.contains(&b'\n') {
        return Err(ProtocolError::InvalidDocument);
    }
    let mut deserializer = serde_json::Deserializer::from_slice(payload);
    let value = T::deserialize(&mut deserializer).map_err(|_| ProtocolError::InvalidDocument)?;
    deserializer
        .end()
        .map_err(|_| ProtocolError::InvalidDocument)?;
    let canonical = serde_jcs::to_vec(&value).map_err(|_| ProtocolError::InvalidDocument)?;
    if canonical != payload {
        return Err(ProtocolError::InvalidDocument);
    }
    Ok(value)
}

fn validate_request(request: &PdfRequestV1) -> Result<()> {
    if request.protocol != PROTOCOL_VERSION
        || !is_lower_sha256(&request.input_sha256)
        || !safe_u64(request.byte_length)
        || request.limits.max_input_bytes == 0
        || !safe_u64(request.limits.max_input_bytes)
        || request.limits.max_pages == 0
        || request.limits.max_indirect_objects == 0
        || request.limits.max_nested_references == 0
        || request.limits.max_metadata_bytes == 0
        || !safe_u64(request.limits.max_metadata_bytes)
        || request.limits.max_page_axis_points == 0
    {
        return Err(ProtocolError::InvalidDocument);
    }
    Ok(())
}

fn validate_response(response: &PdfResponseV1) -> Result<()> {
    if response.protocol != PROTOCOL_VERSION
        || !is_lower_sha256(&response.input_sha256)
        || !safe_u64(response.byte_length)
    {
        return Err(ProtocolError::InvalidDocument);
    }
    if let PdfGuestOutcomeV1::Accepted { pages } = &response.outcome {
        for (expected_index, page) in pages.iter().enumerate() {
            let expected_index =
                u32::try_from(expected_index).map_err(|_| ProtocolError::InvalidDocument)?;
            if page.index != expected_index
                || !is_lower_sha256(&page.page_id)
                || !safe_u64(page.width_micropoints)
                || !safe_u64(page.height_micropoints)
                || page.width_micropoints == 0
                || page.height_micropoints == 0
                || !matches!(page.rotation_degrees, 0 | 90 | 180 | 270)
                || !safe_i64(page.transform.tx_micropoints)
                || !safe_i64(page.transform.ty_micropoints)
                || page.page_id != page_id_v1(&response.input_sha256, page.index)?
            {
                return Err(ProtocolError::InvalidDocument);
            }
        }
    }
    Ok(())
}

fn is_lower_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .as_bytes()
            .iter()
            .all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
}

const fn safe_u64(value: u64) -> bool {
    value <= JCS_SAFE_INTEGER_MAX
}

const fn safe_i64(value: i64) -> bool {
    value >= JCS_SAFE_INTEGER_MIN && value <= JCS_SAFE_INTEGER_MAX as i64
}

pub fn page_id_v1(input_sha256: &str, index: u32) -> Result<String> {
    use sha2::{Digest, Sha256};

    let digest = decode_sha256(input_sha256)?;
    let mut hasher = Sha256::new();
    hasher.update(b"heleos-page-v1\0");
    hasher.update(digest);
    hasher.update(index.to_be_bytes());
    Ok(lower_hex(&hasher.finalize()))
}

fn decode_sha256(value: &str) -> Result<[u8; 32]> {
    if !is_lower_sha256(value) {
        return Err(ProtocolError::InvalidDocument);
    }
    let mut result = [0_u8; 32];
    for (index, pair) in value.as_bytes().chunks_exact(2).enumerate() {
        result[index] = (hex_nibble(pair[0])? << 4) | hex_nibble(pair[1])?;
    }
    Ok(result)
}

fn hex_nibble(value: u8) -> Result<u8> {
    match value {
        b'0'..=b'9' => Ok(value - b'0'),
        b'a'..=b'f' => Ok(value - b'a' + 10),
        _ => Err(ProtocolError::InvalidDocument),
    }
}

fn lower_hex(bytes: &[u8]) -> String {
    const HEX: &[u8; 16] = b"0123456789abcdef";
    let mut value = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        value.push(char::from(HEX[usize::from(byte >> 4)]));
        value.push(char::from(HEX[usize::from(byte & 0x0f)]));
    }
    value
}

#[cfg(test)]
mod tests {
    use super::*;

    #[cfg(feature = "artifact-host")]
    const PRODUCTION_WORKSPACE_TOML: &[u8] = br#"[workspace]
resolver = "3"
members = [
  "crates/heleos-core",
  "crates/heleos-pdf-guest",
  "crates/heleos-pdf-protocol",
  "crates/heleos-test-fixtures",
]

[workspace.package]
edition = "2024"
rust-version = "1.96.1"
license = "LicenseRef-Proprietary"

[workspace.dependencies]
lopdf = { version = "=0.44.0", default-features = false }
serde = { version = "=1.0.229", features = ["derive"] }
serde_jcs = "=0.2.0"
serde_json = "=1.0.151"
sha2 = "=0.11.0"
thiserror = "=2.0.20"
toml = { version = "=1.1.4", default-features = false, features = ["parse", "serde", "std"] }
wasmparser = { version = "=0.254.0", default-features = false }
"#;

    #[cfg(feature = "artifact-host")]
    const GUEST_MANIFEST_TOML: &[u8] = br#"[package]
name = "heleos-pdf-guest"
version = "0.1.0"
edition.workspace = true
rust-version.workspace = true
license.workspace = true
build = false
autolib = false
autobins = false
autoexamples = false
autotests = false
autobenches = false

[lib]
path = "src/lib.rs"

[[bin]]
name = "heleos_pdf_guest"
path = "src/main.rs"

[dependencies]
heleos-pdf-protocol = { path = "../heleos-pdf-protocol" }
lopdf.workspace = true

[dev-dependencies]
heleos-test-fixtures = { path = "../heleos-test-fixtures" }
"#;

    #[cfg(feature = "artifact-host")]
    const PROTOCOL_MANIFEST_TOML: &[u8] = br#"[package]
name = "heleos-pdf-protocol"
version = "0.1.0"
edition.workspace = true
rust-version.workspace = true
license.workspace = true
build = false
autolib = false
autobins = false
autoexamples = false
autotests = false
autobenches = false

[lib]
path = "src/lib.rs"

[features]
default = []
artifact-host = ["dep:toml", "dep:wasmparser"]

[dependencies]
serde.workspace = true
serde_jcs.workspace = true
serde_json.workspace = true
sha2.workspace = true
thiserror.workspace = true
toml = { workspace = true, optional = true }
wasmparser = { workspace = true, optional = true }
"#;

    #[cfg(feature = "artifact-host")]
    const FIXTURES_MANIFEST_TOML: &[u8] = br#"[package]
name = "heleos-test-fixtures"
version = "0.1.0"
edition.workspace = true
rust-version.workspace = true
license.workspace = true
build = false
autolib = false
autobins = false
autoexamples = false
autotests = false
autobenches = false

[lib]
path = "src/lib.rs"

[dependencies]
heleos-pdf-protocol = { path = "../heleos-pdf-protocol" }
lopdf.workspace = true
"#;

    #[cfg(feature = "artifact-host")]
    const EXPECTED_RESOLUTION_ROOT: &str = r#"[workspace]
resolver = "3"
members = [
  "crates/heleos-pdf-guest",
  "crates/heleos-pdf-protocol",
  "crates/heleos-test-fixtures",
]
default-members = ["crates/heleos-pdf-guest"]

[workspace.package]
edition = "2024"
rust-version = "1.96.1"
license = "LicenseRef-Proprietary"

[workspace.dependencies]
lopdf = { version = "=0.44.0", default-features = false }
serde = { version = "=1.0.229", features = ["derive"] }
serde_jcs = "=0.2.0"
serde_json = "=1.0.151"
sha2 = "=0.11.0"
thiserror = "=2.0.20"
toml = { version = "=1.1.4", default-features = false, features = ["parse", "serde", "std"] }
wasmparser = { version = "=0.254.0", default-features = false }
"#;

    fn digest_hex(byte: char) -> String {
        std::iter::repeat_n(byte, 64).collect()
    }

    fn rejected_response() -> PdfResponseV1 {
        PdfResponseV1 {
            protocol: PROTOCOL_VERSION.to_owned(),
            input_sha256: digest_hex('a'),
            byte_length: 9,
            outcome: PdfGuestOutcomeV1::Rejected {
                reason: PdfGuestReasonV1::ActiveFeature(PdfActiveFeatureV1::JavaScript),
            },
        }
    }

    #[test]
    fn response_uses_frozen_adjacent_tags_and_leaf_spelling() {
        let encoded = encode_response_v1(&rejected_response(), 4096).expect("encode response");
        assert_eq!(
            encoded,
            concat!(
                "{\"byte_length\":9,\"input_sha256\":",
                "\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",",
                "\"outcome\":{\"outcome\":\"rejected\",\"value\":{\"reason\":",
                "{\"detail\":\"java_script\",\"kind\":\"active_feature\"}}},",
                "\"protocol\":\"heleos.pdf-probe/v1\"}\n"
            )
            .as_bytes()
        );
    }

    #[test]
    fn every_wire_integer_rejects_one_beyond_jcs_safe_range() {
        let mut response = rejected_response();
        response.byte_length = JCS_SAFE_INTEGER_MAX + 1;
        assert!(encode_response_v1(&response, 4096).is_err());

        let mut request = PdfRequestV1 {
            protocol: PROTOCOL_VERSION.to_owned(),
            input_sha256: digest_hex('b'),
            byte_length: 1,
            limits: PdfDocumentLimitsV1 {
                max_input_bytes: 1,
                max_pages: 1,
                max_indirect_objects: 1,
                max_nested_references: 1,
                max_metadata_bytes: 1,
                max_page_axis_points: 1,
            },
        };
        request.limits.max_metadata_bytes = JCS_SAFE_INTEGER_MAX + 1;
        assert!(encode_request_v1(&request).is_err());
    }

    #[test]
    fn worst_case_ten_thousand_page_response_fits_the_frozen_output_cap() {
        let input_sha256 = digest_hex('f');
        let pages = (0_u32..10_000)
            .map(|index| {
                Ok(PdfPageV1 {
                    index,
                    page_id: page_id_v1(&input_sha256, index)?,
                    width_micropoints: JCS_SAFE_INTEGER_MAX,
                    height_micropoints: JCS_SAFE_INTEGER_MAX,
                    unit: PageUnitV1::Point,
                    rotation_degrees: 270,
                    transform: PageTransformV1 {
                        m11: -1,
                        m12: -1,
                        m21: -1,
                        m22: -1,
                        tx_micropoints: JCS_SAFE_INTEGER_MIN,
                        ty_micropoints: JCS_SAFE_INTEGER_MAX as i64,
                    },
                })
            })
            .collect::<Result<Vec<_>>>()
            .expect("worst-case pages are valid");
        let response = PdfResponseV1 {
            protocol: PROTOCOL_VERSION.to_owned(),
            input_sha256,
            byte_length: JCS_SAFE_INTEGER_MAX,
            outcome: PdfGuestOutcomeV1::Accepted { pages },
        };

        let encoded = encode_response_v1(&response, MAX_PROTOCOL_OUTPUT_BYTES)
            .expect("the maximum valid response fits the frozen cap");
        assert!(encoded.len() <= MAX_PROTOCOL_OUTPUT_BYTES);
        assert_eq!(
            decode_response_v1(&encoded, MAX_PROTOCOL_OUTPUT_BYTES),
            Ok(response)
        );
    }

    #[test]
    fn record_domain_hashes_are_sorted_and_domain_separated() {
        let dependencies = vec![DependencyRecordV1 {
            id: "workspace:heleos-pdf-guest@0.1.0".to_owned(),
            features: vec![],
            edges: vec![DependencyEdgeV1 {
                kind: DependencyKindV1::Normal,
                id: "registry:lopdf@0.44.0#0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
                    .to_owned(),
            }],
        }];
        let imports = vec![ImportRecordV1 {
            module: "wasi_snapshot_preview1".to_owned(),
            name: "random_get".to_owned(),
            kind: ImportKindV1::Func,
            params: vec![CoreValueTypeV1::I32, CoreValueTypeV1::I32],
            results: vec![CoreValueTypeV1::I32],
        }];
        let exports = vec![ExportRecordV1::Func {
            name: "_start".to_owned(),
            params: vec![],
            results: vec![],
        }];

        let dependency = dependency_graph_sha256(&dependencies).expect("hash dependency records");
        let import = imports_sha256(&imports).expect("hash import records");
        let export = exports_sha256(&exports).expect("hash export records");
        assert!(is_lower_sha256(&dependency));
        assert!(is_lower_sha256(&import));
        assert!(is_lower_sha256(&export));
        assert_ne!(dependency, import);
        assert_ne!(import, export);

        assert!(
            exports_sha256(&[
                ExportRecordV1::Func {
                    name: "duplicate".to_owned(),
                    params: vec![],
                    results: vec![],
                },
                ExportRecordV1::Memory {
                    name: "duplicate".to_owned(),
                    minimum_pages: 1,
                    maximum_pages: None,
                    memory64: false,
                    shared: false,
                    page_size_log2: None,
                },
            ])
            .is_err()
        );
    }

    #[test]
    fn source_tree_framing_sorts_paths_and_rejects_cr_or_duplicate_paths() {
        let files = vec![
            SourceFileV1 {
                path: "z".to_owned(),
                content: b"last\n".to_vec(),
            },
            SourceFileV1 {
                path: "a".to_owned(),
                content: b"first\n".to_vec(),
            },
        ];
        let reversed = files.iter().cloned().rev().collect::<Vec<_>>();
        assert_eq!(
            source_tree_sha256(&files).expect("hash source tree"),
            source_tree_sha256(&reversed).expect("hash reversed source tree")
        );
        assert!(
            source_tree_sha256(&[
                SourceFileV1 {
                    path: "a".to_owned(),
                    content: b"one".to_vec(),
                },
                SourceFileV1 {
                    path: "a".to_owned(),
                    content: b"two".to_vec(),
                },
            ])
            .is_err()
        );
        assert!(
            source_tree_sha256(&[SourceFileV1 {
                path: "a".to_owned(),
                content: b"bad\r\n".to_vec(),
            }])
            .is_err()
        );
    }

    #[cfg(feature = "artifact-host")]
    #[test]
    fn guest_resolution_spec_is_exact_and_requires_hardened_explicit_targets() {
        let spec = guest_resolution_workspace_spec_v1(
            PRODUCTION_WORKSPACE_TOML,
            GUEST_MANIFEST_TOML,
            PROTOCOL_MANIFEST_TOML,
            FIXTURES_MANIFEST_TOML,
        )
        .expect("canonical three-member resolution spec validates");
        assert_eq!(spec.root_manifest_utf8_lf, EXPECTED_RESOLUTION_ROOT);
        assert_eq!(
            spec.files,
            [
                GuestResolutionFileV1 {
                    relative_path: "crates/heleos-pdf-guest/Cargo.toml",
                    source_tree_member: true,
                },
                GuestResolutionFileV1 {
                    relative_path: "crates/heleos-pdf-guest/src/lib.rs",
                    source_tree_member: true,
                },
                GuestResolutionFileV1 {
                    relative_path: "crates/heleos-pdf-guest/src/main.rs",
                    source_tree_member: true,
                },
                GuestResolutionFileV1 {
                    relative_path: "crates/heleos-pdf-protocol/Cargo.toml",
                    source_tree_member: true,
                },
                GuestResolutionFileV1 {
                    relative_path: "crates/heleos-pdf-protocol/src/lib.rs",
                    source_tree_member: true,
                },
                GuestResolutionFileV1 {
                    relative_path: "crates/heleos-test-fixtures/Cargo.toml",
                    source_tree_member: true,
                },
                GuestResolutionFileV1 {
                    relative_path: "crates/heleos-test-fixtures/src/lib.rs",
                    source_tree_member: false,
                },
            ]
        );

        for mutated in [
            String::from_utf8(GUEST_MANIFEST_TOML.to_vec())
                .expect("fixture manifest UTF-8")
                .replace("autolib = false", "autolib = true"),
            String::from_utf8(PROTOCOL_MANIFEST_TOML.to_vec())
                .expect("fixture manifest UTF-8")
                .replace("path = \"src/lib.rs\"", "path = \"src/other.rs\""),
            String::from_utf8(FIXTURES_MANIFEST_TOML.to_vec())
                .expect("fixture manifest UTF-8")
                .replace("lopdf.workspace = true", "lopdf.workspace = false"),
        ] {
            let result = if mutated.contains("name = \"heleos-pdf-guest\"") {
                guest_resolution_workspace_spec_v1(
                    PRODUCTION_WORKSPACE_TOML,
                    mutated.as_bytes(),
                    PROTOCOL_MANIFEST_TOML,
                    FIXTURES_MANIFEST_TOML,
                )
            } else if mutated.contains("name = \"heleos-pdf-protocol\"") {
                guest_resolution_workspace_spec_v1(
                    PRODUCTION_WORKSPACE_TOML,
                    GUEST_MANIFEST_TOML,
                    mutated.as_bytes(),
                    FIXTURES_MANIFEST_TOML,
                )
            } else {
                guest_resolution_workspace_spec_v1(
                    PRODUCTION_WORKSPACE_TOML,
                    GUEST_MANIFEST_TOML,
                    PROTOCOL_MANIFEST_TOML,
                    mutated.as_bytes(),
                )
            };
            assert!(result.is_err(), "accepted hardened-manifest drift");
        }
    }

    #[cfg(feature = "artifact-host")]
    #[test]
    fn guest_source_closure_is_exact_bounded_and_rejects_indirection() {
        let mut files = vec![
            SourceFileV1 {
                path: "crates/heleos-pdf-guest/Cargo.toml".to_owned(),
                content: GUEST_MANIFEST_TOML.to_vec(),
            },
            SourceFileV1 {
                path: "crates/heleos-pdf-guest/src/lib.rs".to_owned(),
                content: b"pub fn guest() {}\n".to_vec(),
            },
            SourceFileV1 {
                path: "crates/heleos-pdf-guest/src/main.rs".to_owned(),
                content: b"fn main() {}\n".to_vec(),
            },
            SourceFileV1 {
                path: "crates/heleos-pdf-protocol/Cargo.toml".to_owned(),
                content: PROTOCOL_MANIFEST_TOML.to_vec(),
            },
            SourceFileV1 {
                path: "crates/heleos-pdf-protocol/src/lib.rs".to_owned(),
                content: b"pub struct Protocol;\n".to_vec(),
            },
            SourceFileV1 {
                path: "crates/heleos-test-fixtures/Cargo.toml".to_owned(),
                content: FIXTURES_MANIFEST_TOML.to_vec(),
            },
            SourceFileV1 {
                path: "crates/heleos-test-fixtures/src/lib.rs".to_owned(),
                content: b"pub fn resolution_only() {}\n".to_vec(),
            },
        ];
        assert!(validate_guest_source_closure_v1(&files).is_ok());
        files.reverse();
        assert!(
            validate_guest_source_closure_v1(&files).is_ok(),
            "input enumeration order is not semantic"
        );

        for source in [
            "include_bytes!(\"outside\");\n",
            "include /* split */ ! (\"outside\");\n",
            "r#include_str!(\"outside\");\n",
            "#[path = \"outside.rs\"] mod outside;\n",
            "#[cfg_attr(any(), path = \"outside.rs\")] mod outside;\n",
            concat!(
                "macro_rules! load { ($name:ident) => { $name!(\"outside\") }; }\n",
                "load!(include_bytes);\n",
            ),
            concat!(
                "macro_rules! bind { ($name:ident) => { #[$name = \"outside.rs\"] mod outside; }; }\n",
                "bind!(path);\n",
            ),
            concat!(
                "macro_rules! load { ($($name:ident)*) => { $($name)*!(\"outside\") }; }\n",
                "const X: &[u8] = load!(include_bytes);\n",
            ),
            "use include as load; load!(\"outside.rs\");\n",
            "use include_bytes as load; const X: &[u8] = load!(\"outside\");\n",
            "use include_str as load; const X: &str = load!(\"outside\");\n",
        ] {
            let mut attacked = files.clone();
            attacked
                .iter_mut()
                .find(|file| file.path == "crates/heleos-pdf-guest/src/main.rs")
                .expect("guest main is present")
                .content = source.as_bytes().to_vec();
            assert!(
                validate_guest_source_closure_v1(&attacked).is_err(),
                "accepted source indirection: {source}"
            );
        }

        for source in [
            "fn chars<'a>(value: &'a str) -> char { let _ = b'x'; let _ = '\\n'; let _ = '\\''; let _ = '\\u{1f600}'; let _ = 'é'; let _ = value; 'b' }\n",
            "fn lifetimes<'a, 'b>(left: &'a str, right: &'b str) { let _ = (left, right); }\n",
        ] {
            let mut benign = files.clone();
            benign
                .iter_mut()
                .find(|file| file.path == "crates/heleos-pdf-guest/src/main.rs")
                .expect("guest main is present")
                .content = source.as_bytes().to_vec();
            assert!(
                validate_guest_source_closure_v1(&benign).is_ok(),
                "rejected bounded Rust char/lifetime syntax: {source}"
            );
        }

        let mut lifetime_attack = files.clone();
        lifetime_attack
            .iter_mut()
            .find(|file| file.path == "crates/heleos-pdf-guest/src/main.rs")
            .expect("guest main is present")
            .content = b"fn f<'a>() { include_bytes!(\"outside\"); } fn g<'b>() {}\n".to_vec();
        assert!(
            validate_guest_source_closure_v1(&lifetime_attack).is_err(),
            "lifetime punctuation must not bracket and hide an include-family identifier"
        );
    }

    #[cfg(feature = "artifact-host")]
    #[test]
    fn exact_live_guest_resolution_source_closure_is_admitted() {
        let workspace = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        let files = GUEST_RESOLUTION_FILES_V1
            .iter()
            .map(|expected| {
                Ok(SourceFileV1 {
                    path: expected.relative_path.to_owned(),
                    content: std::fs::read(workspace.join(expected.relative_path))?,
                })
            })
            .collect::<std::io::Result<Vec<_>>>()
            .expect("read exact governed source closure");
        validate_guest_source_closure_v1(&files)
            .expect("the exact seven governed source files pass closure validation");
    }

    #[cfg(feature = "artifact-host")]
    #[test]
    fn guest_resolution_cache_plan_and_domain_digests_are_exact() {
        let lock = br#"version = 4

[[package]]
name = "a"
version = "1.0.0"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

[[package]]
name = "ab"
version = "2.0.0"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"

[[package]]
name = "abc"
version = "3.0.0"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "cccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccccc"

[[package]]
name = "abcd"
version = "4.0.0"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "dddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddddd"

[[package]]
name = "heleos-pdf-guest"
version = "0.1.0"

[[package]]
name = "heleos-pdf-protocol"
version = "0.1.0"

[[package]]
name = "heleos-test-fixtures"
version = "0.1.0"
"#;
        let plan = guest_resolution_cache_plan_v1(lock, lock)
            .expect("canonical pruned lock produces a cache plan");
        assert_eq!(
            plan.sparse_config_cargo_home_relative_path,
            "registry/index/index.crates.io-1949cf8c6b5b557f/config.json"
        );
        assert_eq!(
            plan.sparse_index_entries,
            [
                GuestResolutionSparseIndexEntryV1 {
                    package_name: "a".to_owned(),
                    cargo_home_relative_path:
                        "registry/index/index.crates.io-1949cf8c6b5b557f/.cache/1/a".to_owned(),
                },
                GuestResolutionSparseIndexEntryV1 {
                    package_name: "ab".to_owned(),
                    cargo_home_relative_path:
                        "registry/index/index.crates.io-1949cf8c6b5b557f/.cache/2/ab".to_owned(),
                },
                GuestResolutionSparseIndexEntryV1 {
                    package_name: "abc".to_owned(),
                    cargo_home_relative_path:
                        "registry/index/index.crates.io-1949cf8c6b5b557f/.cache/3/a/abc".to_owned(),
                },
                GuestResolutionSparseIndexEntryV1 {
                    package_name: "abcd".to_owned(),
                    cargo_home_relative_path:
                        "registry/index/index.crates.io-1949cf8c6b5b557f/.cache/ab/cd/abcd"
                            .to_owned(),
                },
            ]
        );
        assert_eq!(plan.resolution_archives.len(), 4);
        assert_eq!(
            plan.resolution_archives[0],
            GuestResolutionArchiveV1 {
                normalized_dependency_id: concat!(
                    "registry:a@1.0.0#",
                    "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                )
                .to_owned(),
                cargo_home_relative_path:
                    "registry/cache/index.crates.io-1949cf8c6b5b557f/a-1.0.0.crate".to_owned(),
                unpacked_source_cargo_home_relative_path:
                    "registry/src/index.crates.io-1949cf8c6b5b557f/a-1.0.0".to_owned(),
                sha256: digest_hex('a'),
            }
        );

        let mut lock_hasher = Sha256::new();
        lock_hasher.update(b"heleos-pdf-guest-resolution-lock-v1\0");
        lock_hasher.update(lock);
        assert_eq!(
            guest_resolution_lock_sha256(lock).expect("hash validated lock"),
            lower_hex(&lock_hasher.finalize())
        );

        let canonical_plan = serde_jcs::to_vec(&plan).expect("cache plan has canonical JCS");
        let mut plan_hasher = Sha256::new();
        plan_hasher.update(b"heleos-pdf-guest-resolution-cache-plan-v1\0");
        plan_hasher.update(&canonical_plan);
        assert_eq!(
            guest_resolution_cache_plan_sha256(&plan).expect("hash validated cache plan"),
            lower_hex(&plan_hasher.finalize())
        );
    }

    #[cfg(feature = "artifact-host")]
    #[test]
    fn guest_resolution_cache_plan_hash_is_bounded_before_semantic_indexing() {
        const MAX_ENTRIES: usize = 65_536;
        const MAX_STRING_BYTES: usize = 16 * 1024 * 1024;
        const MAX_TOTAL_STRING_BYTES: usize = 32 * 1024 * 1024;

        let entry = GuestResolutionSparseIndexEntryV1 {
            package_name: "a".to_owned(),
            cargo_home_relative_path: "registry/index/index.crates.io-1949cf8c6b5b557f/.cache/1/a"
                .to_owned(),
        };
        let mut too_many = GuestResolutionCachePlanV1 {
            sparse_config_cargo_home_relative_path:
                "registry/index/index.crates.io-1949cf8c6b5b557f/config.json".to_owned(),
            sparse_index_entries: vec![entry; MAX_ENTRIES],
            resolution_archives: Vec::new(),
        };
        assert_ne!(
            guest_resolution_cache_plan_sha256(&too_many),
            Err(ProtocolError::SizeLimit),
            "the exact entry-count ceiling must reach semantic validation"
        );
        too_many
            .sparse_index_entries
            .push(too_many.sparse_index_entries[0].clone());
        assert_eq!(
            guest_resolution_cache_plan_sha256(&too_many),
            Err(ProtocolError::SizeLimit),
            "entry-count N + 1 must fail before set allocation"
        );

        let archive = GuestResolutionArchiveV1 {
            normalized_dependency_id: format!("registry:a@1.0.0#{}", digest_hex('a')),
            cargo_home_relative_path:
                "registry/cache/index.crates.io-1949cf8c6b5b557f/a-1.0.0.crate".to_owned(),
            unpacked_source_cargo_home_relative_path:
                "registry/src/index.crates.io-1949cf8c6b5b557f/a-1.0.0".to_owned(),
            sha256: digest_hex('a'),
        };
        let mut too_many_archives = GuestResolutionCachePlanV1 {
            sparse_config_cargo_home_relative_path:
                "registry/index/index.crates.io-1949cf8c6b5b557f/config.json".to_owned(),
            sparse_index_entries: Vec::new(),
            resolution_archives: vec![archive; MAX_ENTRIES],
        };
        assert_ne!(
            guest_resolution_cache_plan_sha256(&too_many_archives),
            Err(ProtocolError::SizeLimit),
            "the exact archive-count ceiling must reach semantic validation"
        );
        too_many_archives
            .resolution_archives
            .push(too_many_archives.resolution_archives[0].clone());
        assert_eq!(
            guest_resolution_cache_plan_sha256(&too_many_archives),
            Err(ProtocolError::SizeLimit),
            "archive-count N + 1 must fail before set allocation"
        );

        let mut long_string = GuestResolutionCachePlanV1 {
            sparse_config_cargo_home_relative_path: "a".repeat(MAX_STRING_BYTES),
            sparse_index_entries: Vec::new(),
            resolution_archives: Vec::new(),
        };
        assert_ne!(
            guest_resolution_cache_plan_sha256(&long_string),
            Err(ProtocolError::SizeLimit),
            "the exact per-string ceiling must reach semantic validation"
        );
        long_string.sparse_config_cargo_home_relative_path.push('a');
        assert_eq!(
            guest_resolution_cache_plan_sha256(&long_string),
            Err(ProtocolError::SizeLimit),
            "per-string N + 1 must fail before indexing"
        );

        let mut aggregate = GuestResolutionCachePlanV1 {
            sparse_config_cargo_home_relative_path: "a".repeat(MAX_STRING_BYTES),
            sparse_index_entries: vec![GuestResolutionSparseIndexEntryV1 {
                package_name: "b".repeat(MAX_STRING_BYTES),
                cargo_home_relative_path: String::new(),
            }],
            resolution_archives: Vec::new(),
        };
        assert_eq!(
            aggregate.sparse_config_cargo_home_relative_path.len()
                + aggregate.sparse_index_entries[0].package_name.len(),
            MAX_TOTAL_STRING_BYTES
        );
        assert_ne!(
            guest_resolution_cache_plan_sha256(&aggregate),
            Err(ProtocolError::SizeLimit),
            "the exact aggregate-string ceiling must reach semantic validation"
        );
        aggregate.sparse_index_entries[0]
            .cargo_home_relative_path
            .push('c');
        assert_eq!(
            guest_resolution_cache_plan_sha256(&aggregate),
            Err(ProtocolError::SizeLimit),
            "aggregate-string N + 1 must fail before JCS allocation"
        );

        let encoded_expansion = GuestResolutionCachePlanV1 {
            sparse_config_cargo_home_relative_path: "\u{0001}".repeat(12 * 1024 * 1024),
            sparse_index_entries: Vec::new(),
            resolution_archives: Vec::new(),
        };
        assert_eq!(
            guest_resolution_cache_plan_sha256(&encoded_expansion),
            Err(ProtocolError::SizeLimit),
            "encoded JCS N + 1 must fail before allocating the encoded document"
        );
    }

    #[cfg(feature = "artifact-host")]
    #[test]
    fn guest_lock_projection_allows_only_contextually_equal_deletions() {
        let production = br#"version = 4

[[package]]
name = "dep"
version = "1.0.0"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

[[package]]
name = "heleos-pdf-guest"
version = "0.1.0"
dependencies = ["dep 1.0.0 (registry+https://github.com/rust-lang/crates.io-index)", "heleos-pdf-protocol"]

[[package]]
name = "heleos-pdf-protocol"
version = "0.1.0"
dependencies = ["dep 1.0.0 (registry+https://github.com/rust-lang/crates.io-index)"]

[[package]]
name = "heleos-test-fixtures"
version = "0.1.0"
dependencies = ["dep 1.0.0 (registry+https://github.com/rust-lang/crates.io-index)"]
"#;
        let projected = br#"version = 4

[[package]]
name = "dep"
version = "1.0.0"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"

[[package]]
name = "heleos-pdf-guest"
version = "0.1.0"
dependencies = ["dep 1.0.0 (registry+https://github.com/rust-lang/crates.io-index)", "heleos-pdf-protocol"]

[[package]]
name = "heleos-pdf-protocol"
version = "0.1.0"
dependencies = ["dep 1.0.0 (registry+https://github.com/rust-lang/crates.io-index)"]
"#;
        assert!(validate_guest_resolution_lock_projection_v1(production, projected).is_ok());

        let added = String::from_utf8(projected.to_vec())
            .expect("lock fixture UTF-8")
            .replace(
                "dependencies = [\"dep 1.0.0 (registry+https://github.com/rust-lang/crates.io-index)\"]",
                "dependencies = [\"dep 1.0.0 (registry+https://github.com/rust-lang/crates.io-index)\", \"heleos-pdf-guest\"]",
            );
        assert!(
            validate_guest_resolution_lock_projection_v1(production, added.as_bytes()).is_err(),
            "an added dependency target is not a deletion-only projection"
        );

        for malformed in [
            String::from_utf8(production.to_vec())
                .expect("lock fixture UTF-8")
                .replace(
                    "dep 1.0.0 (registry+https://github.com/rust-lang/crates.io-index)",
                    "dep (registry+https://github.com/rust-lang/crates.io-index)",
                ),
            String::from_utf8(production.to_vec())
                .expect("lock fixture UTF-8")
                .replace(
                    "dep 1.0.0 (registry+https://github.com/rust-lang/crates.io-index)",
                    "  dep   1.0.0  ",
                ),
        ] {
            assert!(
                validate_guest_resolution_lock_projection_v1(
                    malformed.as_bytes(),
                    malformed.as_bytes(),
                )
                .is_err(),
                "accepted a noncanonical Cargo.lock dependency spelling"
            );
        }

        let contextual = br#"version = 4

[[package]]
name = "heleos-pdf-guest"
version = "0.1.0"
dependencies = ["sha2"]

[[package]]
name = "sha2"
version = "0.11.0"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
"#;
        let contextual_versioned = std::str::from_utf8(contextual)
            .expect("contextual lock fixture is UTF-8")
            .replace(
                "dependencies = [\"sha2\"]",
                "dependencies = [\"sha2 0.11.0\"]",
            );
        assert!(
            validate_guest_resolution_lock_projection_v1(
                contextual,
                contextual_versioned.as_bytes(),
            )
            .is_ok(),
            "contextually unambiguous name and name-version forms are semantically equal"
        );
    }

    #[cfg(feature = "artifact-host")]
    #[test]
    fn guest_lock_projection_has_bounded_work_for_large_valid_lock() {
        let mut lock = String::from("version = 4\n");
        for index in 0..16_384_u32 {
            use std::fmt::Write as _;

            write!(
                lock,
                "\n[[package]]\nname = \"package-{index:05}\"\nversion = \"1.0.0\"\n"
            )
            .expect("write bounded lock fixture");
        }
        validate_guest_resolution_lock_projection_v1(lock.as_bytes(), lock.as_bytes())
            .expect("large bounded lock projection validates without quadratic indexing");
    }

    #[cfg(feature = "artifact-host")]
    #[test]
    fn cargo_metadata_normalization_includes_normal_and_build_but_not_dev() {
        let metadata = br#"{
          "packages":[
            {"id":"path+file:///repo/crates/guest#heleos-pdf-guest@0.1.0","name":"heleos-pdf-guest","version":"0.1.0","source":null,"checksum":null,"manifest_path":"/repo/crates/heleos-pdf-guest/Cargo.toml",
             "targets":[{"kind":["lib"],"crate_types":["lib"],"name":"heleos_pdf_guest","src_path":"/repo/crates/heleos-pdf-guest/src/lib.rs","edition":"2024","doc":true,"doctest":true,"test":true,"required-features":[]}],
             "dependencies":[
               {"name":"heleos-pdf-protocol","source":null,"req":"*","kind":null,"rename":null,"optional":false,"uses_default_features":true,"features":[],"target":null,"registry":null,"path":"/repo/crates/heleos-pdf-protocol"},
               {"name":"lopdf","source":"registry+https://github.com/rust-lang/crates.io-index","req":"=0.44.0","kind":null,"rename":null,"optional":false,"uses_default_features":false,"features":[],"target":null,"registry":null},
               {"name":"lopdf","source":"registry+https://github.com/rust-lang/crates.io-index","req":"=0.44.0","kind":"build","rename":null,"optional":false,"uses_default_features":false,"features":[],"target":null,"registry":null},
               {"name":"lopdf","source":"registry+https://github.com/rust-lang/crates.io-index","req":"=0.44.0","kind":"dev","rename":null,"optional":false,"uses_default_features":false,"features":[],"target":null,"registry":null}
             ],"features":{}},
            {"id":"path+file:///repo/crates/protocol#heleos-pdf-protocol@0.1.0","name":"heleos-pdf-protocol","version":"0.1.0","source":null,"checksum":null,"manifest_path":"/repo/crates/heleos-pdf-protocol/Cargo.toml",
             "targets":[{"kind":["lib"],"crate_types":["lib"],"name":"heleos_pdf_protocol","src_path":"/repo/crates/heleos-pdf-protocol/src/lib.rs","edition":"2024","doc":true,"doctest":true,"test":true,"required-features":[]}],
             "dependencies":[],"features":{"default":[]}},
            {"id":"registry+https://github.com/rust-lang/crates.io-index#lopdf@0.44.0","name":"lopdf","version":"0.44.0","source":"registry+https://github.com/rust-lang/crates.io-index","manifest_path":"/cargo/lopdf/Cargo.toml",
             "targets":[{"kind":["lib"],"crate_types":["lib"],"name":"lopdf","src_path":"/cargo/lopdf/src/lib.rs","edition":"2021","doc":true,"doctest":true,"test":true,"required-features":[]}],
             "dependencies":[],"features":{"nom":[]}}
          ],
          "workspace_root":"/repo",
          "resolve":{"root":null,"nodes":[
            {"id":"path+file:///repo/crates/guest#heleos-pdf-guest@0.1.0","features":[],"deps":[
              {"name":"heleos_pdf_protocol","pkg":"path+file:///repo/crates/protocol#heleos-pdf-protocol@0.1.0","dep_kinds":[{"kind":null,"target":null}]},
              {"name":"lopdf","pkg":"registry+https://github.com/rust-lang/crates.io-index#lopdf@0.44.0","dep_kinds":[{"kind":null,"target":null},{"kind":"build","target":null},{"kind":"dev","target":null}]}
            ]},
            {"id":"path+file:///repo/crates/protocol#heleos-pdf-protocol@0.1.0","features":["default"],"deps":[]},
            {"id":"registry+https://github.com/rust-lang/crates.io-index#lopdf@0.44.0","features":["nom"],"deps":[]}
          ]}
        }"#;
        let production_lock = br#"version = 4

[[package]]
name = "heleos-pdf-guest"
version = "0.1.0"
dependencies = ["heleos-pdf-protocol", "lopdf 0.44.0"]

[[package]]
name = "heleos-pdf-protocol"
version = "0.1.0"

[[package]]
name = "lopdf"
version = "0.44.0"
source = "registry+https://github.com/rust-lang/crates.io-index"
checksum = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
"#;
        let records = normalize_dependency_records(
            metadata,
            metadata,
            production_lock,
            production_lock,
            "heleos-pdf-guest",
        )
        .expect("normalize bounded metadata");
        assert_eq!(records.len(), 3);
        assert_eq!(
            records[0].id,
            "registry:lopdf@0.44.0#0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
        );
        let root = records
            .iter()
            .find(|record| record.id == "workspace:heleos-pdf-guest@0.1.0")
            .expect("root record");
        assert_eq!(
            root.edges,
            vec![
                DependencyEdgeV1 {
                    kind: DependencyKindV1::Normal,
                    id: "registry:lopdf@0.44.0#0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef".to_owned(),
                },
                DependencyEdgeV1 {
                    kind: DependencyKindV1::Normal,
                    id: "workspace:heleos-pdf-protocol@0.1.0".to_owned(),
                },
                DependencyEdgeV1 {
                    kind: DependencyKindV1::Build,
                    id: "registry:lopdf@0.44.0#0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef".to_owned(),
                },
            ]
        );
        let graph = normalize_dependency_graph_v1(
            metadata,
            metadata,
            production_lock,
            production_lock,
            "heleos-pdf-guest",
        )
        .expect("normalization also retains active registry roots");
        assert_eq!(graph.records, records);
        assert_eq!(
            graph.registry_roots,
            vec![ArtifactRegistryRootV1 {
                normalized_dependency_id: concat!(
                    "registry:lopdf@0.44.0#",
                    "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
                )
                .to_owned(),
                physical_root: "/cargo/lopdf".into(),
            }]
        );

        let escaped_workspace_path = String::from_utf8(metadata.to_vec())
            .expect("metadata fixture is UTF-8")
            .replace(
                "/repo/crates/heleos-pdf-guest/Cargo.toml",
                "/repo/crates/heleos-pdf-guest/../heleos-pdf-guest/Cargo.toml",
            );
        assert!(
            normalize_dependency_graph_v1(
                escaped_workspace_path.as_bytes(),
                metadata,
                production_lock,
                production_lock,
                "heleos-pdf-guest",
            )
            .is_err(),
            "lexically escaping workspace manifest path was accepted"
        );

        let foreign_artifact_host = String::from_utf8(metadata.to_vec())
            .expect("metadata fixture is UTF-8")
            .replace(
                "\"features\":[\"nom\"]",
                "\"features\":[\"artifact-host\",\"nom\"]",
            );
        assert!(
            normalize_dependency_graph_v1(
                foreign_artifact_host.as_bytes(),
                foreign_artifact_host.as_bytes(),
                production_lock,
                production_lock,
                "heleos-pdf-guest",
            )
            .is_err(),
            "artifact-host was accepted on a reached non-protocol node"
        );
    }

    #[cfg(feature = "artifact-host")]
    fn weak_optional_metadata_fixture() -> (serde_json::Value, String) {
        let checksum = |byte: char| std::iter::repeat_n(byte, 64).collect::<String>();
        let flate2_checksum = checksum('a');
        let zlib_checksum = checksum('b');
        let guest_id = "path+file:///repo/crates/heleos-pdf-guest#heleos-pdf-guest@0.1.0";
        let protocol_id = "path+file:///repo/crates/heleos-pdf-protocol#heleos-pdf-protocol@0.1.0";
        let flate2_id = "registry+https://github.com/rust-lang/crates.io-index#flate2@1.1.10";
        let zlib_id = "registry+https://github.com/rust-lang/crates.io-index#zlib-rs@0.6.7";
        let target = |name: &str, src_path: &str| {
            serde_json::json!({
                "kind": ["lib"],
                "crate_types": ["lib"],
                "name": name,
                "src_path": src_path,
                "edition": "2024",
                "doc": true,
                "doctest": true,
                "test": true,
                "required-features": []
            })
        };
        let dependency = |name: &str, source: Option<&str>, optional: bool, path: Option<&str>| {
            serde_json::json!({
                "name": name,
                "source": source,
                "req": "*",
                "kind": null,
                "rename": null,
                "optional": optional,
                "uses_default_features": true,
                "features": [],
                "target": null,
                "registry": null,
                "path": path
            })
        };
        let metadata = serde_json::json!({
            "packages": [
                {
                    "id": guest_id,
                    "name": "heleos-pdf-guest",
                    "version": "0.1.0",
                    "source": null,
                    "checksum": null,
                    "manifest_path": "/repo/crates/heleos-pdf-guest/Cargo.toml",
                    "targets": [target("heleos_pdf_guest", "/repo/crates/heleos-pdf-guest/src/lib.rs")],
                    "dependencies": [
                        dependency(
                            "heleos-pdf-protocol",
                            None,
                            false,
                            Some("/repo/crates/heleos-pdf-protocol"),
                        ),
                        dependency(
                            "flate2",
                            Some("registry+https://github.com/rust-lang/crates.io-index"),
                            false,
                            None,
                        )
                    ],
                    "features": {}
                },
                {
                    "id": protocol_id,
                    "name": "heleos-pdf-protocol",
                    "version": "0.1.0",
                    "source": null,
                    "checksum": null,
                    "manifest_path": "/repo/crates/heleos-pdf-protocol/Cargo.toml",
                    "targets": [target("heleos_pdf_protocol", "/repo/crates/heleos-pdf-protocol/src/lib.rs")],
                    "dependencies": [],
                    "features": {"default": []}
                },
                {
                    "id": flate2_id,
                    "name": "flate2",
                    "version": "1.1.10",
                    "source": "registry+https://github.com/rust-lang/crates.io-index",
                    "manifest_path": "/cargo/flate2/Cargo.toml",
                    "targets": [target("flate2", "/cargo/flate2/src/lib.rs")],
                    "dependencies": [dependency(
                        "zlib-rs",
                        Some("registry+https://github.com/rust-lang/crates.io-index"),
                        true,
                        None,
                    )],
                    "features": {"runtime_detection": ["zlib-rs?/std"]}
                },
                {
                    "id": zlib_id,
                    "name": "zlib-rs",
                    "version": "0.6.7",
                    "source": "registry+https://github.com/rust-lang/crates.io-index",
                    "manifest_path": "/cargo/zlib-rs/Cargo.toml",
                    "targets": [target("zlib_rs", "/cargo/zlib-rs/src/lib.rs")],
                    "dependencies": [],
                    "features": {"std": []}
                }
            ],
            "workspace_root": "/repo",
            "resolve": {"nodes": [
                {"id": guest_id, "features": [], "deps": [
                    {"name": "heleos_pdf_protocol", "pkg": protocol_id,
                     "dep_kinds": [{"kind": null, "target": null}]},
                    {"name": "flate2", "pkg": flate2_id,
                     "dep_kinds": [{"kind": null, "target": null}]}
                ]},
                {"id": protocol_id, "features": ["default"], "deps": []},
                {"id": flate2_id, "features": ["runtime_detection"], "deps": [
                    {"name": "zlib_rs", "pkg": zlib_id,
                     "dep_kinds": [{"kind": null, "target": null}]}
                ]},
                {"id": zlib_id, "features": ["std"], "deps": []}
            ]}
        });
        let lock = format!(
            concat!(
                "version = 4\n\n",
                "[[package]]\nname = \"flate2\"\nversion = \"1.1.10\"\n",
                "source = \"registry+https://github.com/rust-lang/crates.io-index\"\n",
                "checksum = \"{}\"\n",
                "dependencies = [\"zlib-rs 0.6.7\"]\n\n",
                "[[package]]\nname = \"heleos-pdf-guest\"\nversion = \"0.1.0\"\n",
                "dependencies = [\"flate2 1.1.10\", \"heleos-pdf-protocol\"]\n\n",
                "[[package]]\nname = \"heleos-pdf-protocol\"\nversion = \"0.1.0\"\n\n",
                "[[package]]\nname = \"zlib-rs\"\nversion = \"0.6.7\"\n",
                "source = \"registry+https://github.com/rust-lang/crates.io-index\"\n",
                "checksum = \"{}\"\n"
            ),
            flate2_checksum, zlib_checksum
        );

        (metadata, lock)
    }

    #[cfg(feature = "artifact-host")]
    #[test]
    fn cargo_metadata_weak_optional_feature_edge_is_validated_but_inactive() {
        let (metadata, lock) = weak_optional_metadata_fixture();
        let metadata = serde_json::to_vec(&metadata).expect("serialize weak optional fixture");
        let records = normalize_dependency_records(
            &metadata,
            &metadata,
            lock.as_bytes(),
            lock.as_bytes(),
            "heleos-pdf-guest",
        )
        .expect("weak optional ghost metadata remains valid");
        assert_eq!(records.len(), 3);
        assert!(
            records
                .iter()
                .all(|record| !record.id.starts_with("registry:zlib-rs@")),
            "weak optional ghost was admitted to the active dependency graph"
        );
    }

    #[cfg(feature = "artifact-host")]
    fn normalize_feature_fixture(
        guest: &serde_json::Value,
        full: &serde_json::Value,
        lock: &str,
    ) -> Result<Vec<DependencyRecordV1>> {
        let guest = serde_json::to_vec(guest).expect("serialize guest feature fixture");
        let full = serde_json::to_vec(full).expect("serialize full feature fixture");
        normalize_dependency_records(
            &guest,
            &full,
            lock.as_bytes(),
            lock.as_bytes(),
            "heleos-pdf-guest",
        )
    }

    #[cfg(feature = "artifact-host")]
    fn rename_weak_optional_child(
        metadata: &mut serde_json::Value,
        lock: &mut String,
        package_name: &str,
        target_name: &str,
    ) {
        metadata["packages"][2]["dependencies"][0]["name"] = package_name.into();
        metadata["packages"][2]["features"]["runtime_detection"] =
            vec![format!("{package_name}?/std")].into();
        metadata["packages"][3]["name"] = package_name.into();
        metadata["packages"][3]["manifest_path"] =
            format!("/cargo/{package_name}/Cargo.toml").into();
        metadata["packages"][3]["targets"][0]["name"] = target_name.into();
        metadata["packages"][3]["targets"][0]["src_path"] =
            format!("/cargo/{package_name}/src/lib.rs").into();
        metadata["resolve"]["nodes"][2]["deps"][0]["name"] = target_name.into();
        *lock = lock.replace("zlib-rs", package_name);
    }

    #[cfg(feature = "artifact-host")]
    #[test]
    fn cargo_metadata_feature_classifier_rejects_bad_joins_and_feature_closure() {
        let rejects = |metadata: serde_json::Value, lock: &str, detail: &str| {
            assert!(
                normalize_feature_fixture(&metadata, &metadata, lock).is_err(),
                "feature classifier accepted {detail}"
            );
        };

        let (base, lock) = weak_optional_metadata_fixture();

        for field in ["source", "kind", "rename", "target", "registry"] {
            let mut metadata = base.clone();
            metadata["packages"][2]["dependencies"][0]
                .as_object_mut()
                .expect("dependency declaration is an object")
                .remove(field);
            rejects(
                metadata,
                &lock,
                &format!("a declaration missing required nullable field {field}"),
            );
        }
        for field in ["kind", "target"] {
            let mut metadata = base.clone();
            metadata["resolve"]["nodes"][2]["deps"][0]["dep_kinds"][0]
                .as_object_mut()
                .expect("resolve dep-kind is an object")
                .remove(field);
            rejects(
                metadata,
                &lock,
                &format!("a dep-kind missing required nullable field {field}"),
            );
        }

        let mut wrong_external_name = base.clone();
        wrong_external_name["resolve"]["nodes"][2]["deps"][0]["name"] = "wrong_external".into();
        rejects(wrong_external_name, &lock, "a mismatched external name");

        let mut missing_declaration = base.clone();
        missing_declaration["packages"][2]["dependencies"] = serde_json::json!([]);
        rejects(missing_declaration, &lock, "a missing declaration");

        let mut ambiguous_declaration = base.clone();
        let duplicate = ambiguous_declaration["packages"][2]["dependencies"][0].clone();
        ambiguous_declaration["packages"][2]["dependencies"]
            .as_array_mut()
            .expect("dependency declarations are an array")
            .push(duplicate);
        rejects(
            ambiguous_declaration,
            &lock,
            "two declarations for one complete join tuple",
        );

        let mut wrong_target = base.clone();
        wrong_target["packages"][2]["dependencies"][0]["target"] =
            "cfg(target_os = \"windows\")".into();
        rejects(wrong_target, &lock, "a declaration target mismatch");

        let mut no_dependency_target = base.clone();
        no_dependency_target["packages"][3]["targets"] = serde_json::json!([]);
        rejects(
            no_dependency_target,
            &lock,
            "a child without one dependency-capable target",
        );
        let mut wrong_target_kind = base.clone();
        wrong_target_kind["packages"][3]["targets"][0]["kind"] = serde_json::json!(["bin"]);
        wrong_target_kind["packages"][3]["targets"][0]["crate_types"] = serde_json::json!(["bin"]);
        rejects(
            wrong_target_kind,
            &lock,
            "a child whose selected target is not lib or proc-macro",
        );
        let mut wrong_target_crate_type = base.clone();
        wrong_target_crate_type["packages"][3]["targets"][0]["crate_types"] =
            serde_json::json!(["rlib"]);
        rejects(
            wrong_target_crate_type,
            &lock,
            "a dependency target with a mismatched crate type",
        );
        let mut ambiguous_dependency_targets = base.clone();
        let second_target = ambiguous_dependency_targets["packages"][3]["targets"][0].clone();
        ambiguous_dependency_targets["packages"][3]["targets"]
            .as_array_mut()
            .expect("targets are an array")
            .push(second_target);
        rejects(
            ambiguous_dependency_targets,
            &lock,
            "a child with two dependency-capable targets",
        );

        for malformed in [
            "dep:",
            "zlib-rs??/std",
            "zlib-rs?/",
            "/std",
            "zlib-rs//std",
            "unknown?/std",
            "unknown/std",
            "unknown",
        ] {
            let mut metadata = base.clone();
            metadata["packages"][2]["features"]["runtime_detection"] = vec![malformed].into();
            rejects(
                metadata,
                &lock,
                &format!("malformed feature spec {malformed}"),
            );
        }

        let mut duplicate_spec = base.clone();
        duplicate_spec["packages"][2]["features"]["runtime_detection"] =
            serde_json::json!(["zlib-rs?/std", "zlib-rs?/std"]);
        rejects(duplicate_spec, &lock, "a duplicate feature spec");

        let raw = serde_json::to_string(&base).expect("serialize raw duplicate-key fixture");
        let raw_duplicate_key = raw.replacen(
            "\"features\":{\"runtime_detection\":[\"zlib-rs?/std\"]}",
            concat!(
                "\"features\":{\"runtime_detection\":[\"zlib-rs?/std\"],",
                "\"runtime_detection\":[\"zlib-rs?/std\"]}"
            ),
            1,
        );
        assert_ne!(raw_duplicate_key, raw, "duplicate-key mutation applied");
        assert!(
            normalize_dependency_records(
                raw_duplicate_key.as_bytes(),
                raw_duplicate_key.as_bytes(),
                lock.as_bytes(),
                lock.as_bytes(),
                "heleos-pdf-guest",
            )
            .is_err(),
            "duplicate raw Cargo feature-table key was accepted"
        );

        let mut absent_enabled_feature = base.clone();
        absent_enabled_feature["resolve"]["nodes"][2]["features"] = serde_json::json!(["absent"]);
        rejects(
            absent_enabled_feature,
            &lock,
            "an enabled feature absent from the feature table",
        );

        let mut duplicate_enabled_feature = base.clone();
        duplicate_enabled_feature["resolve"]["nodes"][2]["features"] =
            serde_json::json!(["runtime_detection", "runtime_detection"]);
        rejects(
            duplicate_enabled_feature,
            &lock,
            "a duplicate enabled feature",
        );

        let mut omitted_local_feature = base.clone();
        omitted_local_feature["packages"][2]["features"]["runtime_detection"] =
            serde_json::json!(["helper", "zlib-rs?/std"]);
        omitted_local_feature["packages"][2]["features"]["helper"] = serde_json::json!([]);
        rejects(
            omitted_local_feature,
            &lock,
            "an omitted referenced local feature",
        );

        let mut dep_syntax_on_nonoptional = base.clone();
        dep_syntax_on_nonoptional["packages"][2]["dependencies"][0]["optional"] = false.into();
        dep_syntax_on_nonoptional["packages"][2]["features"]["runtime_detection"] =
            serde_json::json!(["dep:zlib-rs"]);
        rejects(
            dep_syntax_on_nonoptional,
            &lock,
            "dep: syntax naming a nonoptional dependency",
        );

        let mut weak_syntax_on_nonoptional = base.clone();
        weak_syntax_on_nonoptional["packages"][2]["dependencies"][0]["optional"] = false.into();
        rejects(
            weak_syntax_on_nonoptional,
            &lock,
            "weak syntax naming a nonoptional dependency",
        );
    }

    #[cfg(feature = "artifact-host")]
    #[test]
    fn cargo_metadata_feature_classifier_freezes_strong_weak_alias_and_kind_rules() {
        let (mut implicit, mut implicit_lock) = weak_optional_metadata_fixture();
        rename_weak_optional_child(&mut implicit, &mut implicit_lock, "codec", "codec");
        implicit["packages"][2]["features"]["codec"] = serde_json::json!(["dep:codec"]);
        implicit["resolve"]["nodes"][2]["features"] =
            serde_json::json!(["codec", "runtime_detection"]);
        let records = normalize_feature_fixture(&implicit, &implicit, &implicit_lock)
            .expect("implicit optional feature strongly activates its edge");
        assert!(
            records
                .iter()
                .any(|record| record.id.starts_with("registry:codec@"))
        );

        let (mut direct, mut direct_lock) = weak_optional_metadata_fixture();
        rename_weak_optional_child(&mut direct, &mut direct_lock, "codec", "codec");
        direct["packages"][2]["features"]["activate"] = serde_json::json!(["dep:codec"]);
        direct["resolve"]["nodes"][2]["features"] =
            serde_json::json!(["activate", "runtime_detection"]);
        assert!(
            normalize_feature_fixture(&direct, &direct, &direct_lock)
                .expect("dep:key strongly activates an optional edge")
                .iter()
                .any(|record| record.id.starts_with("registry:codec@"))
        );

        let (mut strong, mut strong_lock) = weak_optional_metadata_fixture();
        rename_weak_optional_child(&mut strong, &mut strong_lock, "codec", "codec");
        strong["packages"][2]["features"]["activate"] = serde_json::json!(["codec/std"]);
        strong["packages"][2]["features"]["codec"] = serde_json::json!(["dep:codec"]);
        strong["resolve"]["nodes"][2]["features"] =
            serde_json::json!(["activate", "codec", "runtime_detection"]);
        assert!(
            normalize_feature_fixture(&strong, &strong, &strong_lock)
                .expect("strong key/feature activates an optional edge")
                .iter()
                .any(|record| record.id.starts_with("registry:codec@"))
        );
        let mut missing_implicit = strong.clone();
        missing_implicit["resolve"]["nodes"][2]["features"] =
            serde_json::json!(["activate", "runtime_detection"]);
        assert!(
            normalize_feature_fixture(&missing_implicit, &missing_implicit, &strong_lock).is_err(),
            "strong forwarding invented an omitted same-name implicit feature"
        );

        let (mut nonoptional, mut nonoptional_lock) = weak_optional_metadata_fixture();
        rename_weak_optional_child(&mut nonoptional, &mut nonoptional_lock, "codec", "codec");
        nonoptional["packages"][2]["dependencies"][0]["optional"] = false.into();
        nonoptional["packages"][2]["features"]["runtime_detection"] = serde_json::json!([]);
        assert!(
            normalize_feature_fixture(&nonoptional, &nonoptional, &nonoptional_lock)
                .expect("a nonoptional declaration is unconditionally active")
                .iter()
                .any(|record| record.id.starts_with("registry:codec@"))
        );

        let (mut child_features_only, child_features_lock) = weak_optional_metadata_fixture();
        child_features_only["packages"][2]["dependencies"][0]["features"] =
            serde_json::json!(["std"]);
        child_features_only["packages"][2]["dependencies"][0]["uses_default_features"] =
            true.into();
        assert_eq!(
            normalize_feature_fixture(
                &child_features_only,
                &child_features_only,
                &child_features_lock,
            )
            .expect("declaration-level child features do not activate an optional edge")
            .len(),
            3
        );

        let (mut cycle, cycle_lock) = weak_optional_metadata_fixture();
        cycle["packages"][2]["features"]["a"] = serde_json::json!(["b"]);
        cycle["packages"][2]["features"]["b"] = serde_json::json!(["a"]);
        cycle["packages"][2]["features"]["runtime_detection"] =
            serde_json::json!(["a", "zlib-rs?/std"]);
        cycle["resolve"]["nodes"][2]["features"] =
            serde_json::json!(["a", "b", "runtime_detection"]);
        assert_eq!(
            normalize_feature_fixture(&cycle, &cycle, &cycle_lock)
                .expect("local feature cycles terminate within the exact enabled set")
                .len(),
            3
        );

        let (mut renamed, renamed_lock) = weak_optional_metadata_fixture();
        renamed["packages"][2]["dependencies"][0]["rename"] = "codec-wire".into();
        renamed["packages"][2]["features"]["runtime_detection"] =
            serde_json::json!(["codec-wire?/std"]);
        renamed["resolve"]["nodes"][2]["deps"][0]["name"] = "codec_wire".into();
        assert_eq!(
            normalize_feature_fixture(&renamed, &renamed, &renamed_lock)
                .expect("renamed dependency key joins its normalized external name")
                .len(),
            3
        );

        let (mut md5, mut md5_lock) = weak_optional_metadata_fixture();
        rename_weak_optional_child(&mut md5, &mut md5_lock, "md-5", "md5");
        assert_eq!(
            normalize_feature_fixture(&md5, &md5, &md5_lock)
                .expect("unrenamed package joins through its selected lib target name")
                .len(),
            3
        );

        let (mut disjoint_targets, disjoint_targets_lock) = weak_optional_metadata_fixture();
        disjoint_targets["packages"][2]["dependencies"][0]["target"] = "cfg(unix)".into();
        let mut windows_declaration = disjoint_targets["packages"][2]["dependencies"][0].clone();
        windows_declaration["target"] = "cfg(windows)".into();
        disjoint_targets["packages"][2]["dependencies"]
            .as_array_mut()
            .expect("dependency declarations are an array")
            .push(windows_declaration);
        disjoint_targets["resolve"]["nodes"][2]["deps"][0]["dep_kinds"] = serde_json::json!([
            {"kind": null, "target": "cfg(unix)"},
            {"kind": null, "target": "cfg(windows)"}
        ]);
        assert_eq!(
            normalize_feature_fixture(
                &disjoint_targets,
                &disjoint_targets,
                &disjoint_targets_lock,
            )
            .expect("same alias on disjoint target declarations remains admissible")
            .len(),
            3
        );

        let mut alias_collision = renamed.clone();
        let mut collision = alias_collision["packages"][2]["dependencies"][0].clone();
        collision["rename"] = "codec_wire".into();
        alias_collision["packages"][2]["dependencies"]
            .as_array_mut()
            .expect("dependency declarations are an array")
            .push(collision);
        assert!(
            normalize_feature_fixture(&alias_collision, &alias_collision, &renamed_lock).is_err(),
            "normalized aliases collided within one complete join tuple"
        );

        let (mut guest_strong, mut parity_lock) = weak_optional_metadata_fixture();
        rename_weak_optional_child(&mut guest_strong, &mut parity_lock, "codec", "codec");
        guest_strong["packages"][2]["features"]["activate"] = serde_json::json!(["dep:codec"]);
        guest_strong["resolve"]["nodes"][2]["features"] =
            serde_json::json!(["activate", "runtime_detection"]);
        let mut full_weak = guest_strong.clone();
        full_weak["packages"][2]["features"]
            .as_object_mut()
            .expect("feature table is an object")
            .remove("activate");
        full_weak["resolve"]["nodes"][2]["features"] = serde_json::json!(["runtime_detection"]);
        assert!(
            normalize_feature_fixture(&guest_strong, &full_weak, &parity_lock).is_err(),
            "guest active edge was not checked against the filtered full graph"
        );

        let (base, permutation_lock) = weak_optional_metadata_fixture();
        let baseline = normalize_feature_fixture(&base, &base, &permutation_lock)
            .expect("baseline feature fixture normalizes");
        let mut permuted = base.clone();
        permuted["packages"]
            .as_array_mut()
            .expect("packages are an array")
            .reverse();
        permuted["resolve"]["nodes"]
            .as_array_mut()
            .expect("nodes are an array")
            .reverse();
        let guest_node = permuted["resolve"]["nodes"]
            .as_array_mut()
            .expect("nodes are an array")
            .iter_mut()
            .find(|node| {
                node["id"]
                    .as_str()
                    .is_some_and(|id| id.contains("pdf-guest"))
            })
            .expect("guest node remains present");
        guest_node["deps"]
            .as_array_mut()
            .expect("resolve dependencies are an array")
            .reverse();
        let guest_package = permuted["packages"]
            .as_array_mut()
            .expect("packages are an array")
            .iter_mut()
            .find(|package| package["name"] == "heleos-pdf-guest")
            .expect("guest package remains present");
        guest_package["dependencies"]
            .as_array_mut()
            .expect("dependency declarations are an array")
            .reverse();
        assert_eq!(
            normalize_feature_fixture(&permuted, &permuted, &permutation_lock)
                .expect("metadata input permutations normalize identically"),
            baseline
        );

        let mut full_with_unreached = base.clone();
        let unused_id = "git+https://invalid.example/unused#unused@9.9.9";
        full_with_unreached["packages"]
            .as_array_mut()
            .expect("packages are an array")
            .push(serde_json::json!({
                "id": unused_id,
                "name": "unused",
                "version": "9.9.9",
                "source": "git+https://invalid.example/unused",
                "checksum": null,
                "manifest_path": "/unused/Cargo.toml",
                "targets": [{
                    "kind": ["lib"],
                    "crate_types": ["lib"],
                    "name": "unused",
                    "src_path": "/unused/src/lib.rs",
                    "edition": "2024",
                    "doc": true,
                    "doctest": true,
                    "test": true,
                    "required-features": []
                }],
                "dependencies": [],
                "features": {}
            }));
        full_with_unreached["resolve"]["nodes"]
            .as_array_mut()
            .expect("nodes are an array")
            .push(serde_json::json!({"id": unused_id, "features": [], "deps": []}));
        assert_eq!(
            normalize_feature_fixture(&base, &full_with_unreached, &permutation_lock)
                .expect("unreached full-workspace package does not require a lock join"),
            baseline
        );
    }

    #[cfg(feature = "artifact-host")]
    #[test]
    fn cargo_metadata_full_subset_skips_dev_before_reached_identity_lookup() {
        let (mut metadata, lock) = weak_optional_metadata_fixture();
        let mut dev_declaration = metadata["packages"][2]["dependencies"][0].clone();
        dev_declaration["kind"] = "dev".into();
        dev_declaration["optional"] = false.into();
        metadata["packages"][0]["dependencies"]
            .as_array_mut()
            .expect("guest declarations are an array")
            .push(dev_declaration);
        let zlib_id = metadata["packages"][3]["id"]
            .as_str()
            .expect("zlib package ID is a string")
            .to_owned();
        metadata["resolve"]["nodes"][0]["deps"]
            .as_array_mut()
            .expect("guest resolve dependencies are an array")
            .push(serde_json::json!({
                "name": "zlib_rs",
                "pkg": zlib_id,
                "dep_kinds": [{"kind": "dev", "target": null}]
            }));

        assert_eq!(
            normalize_feature_fixture(&metadata, &metadata, &lock)
                .expect("nonoptional dev-only child is validated but never reached")
                .len(),
            3
        );
    }

    #[cfg(feature = "artifact-host")]
    #[test]
    fn cargo_metadata_optional_feature_keys_allow_disjoint_nonoptional_declarations() {
        let (mut mixed_kind, mut mixed_kind_lock) = weak_optional_metadata_fixture();
        rename_weak_optional_child(&mut mixed_kind, &mut mixed_kind_lock, "codec", "codec");
        let mut dev = mixed_kind["packages"][2]["dependencies"][0].clone();
        dev["kind"] = "dev".into();
        dev["optional"] = false.into();
        mixed_kind["packages"][2]["dependencies"]
            .as_array_mut()
            .expect("declarations are an array")
            .push(dev);
        mixed_kind["packages"][2]["features"]["activate"] = serde_json::json!(["dep:codec"]);
        mixed_kind["resolve"]["nodes"][2]["features"] =
            serde_json::json!(["activate", "runtime_detection"]);
        mixed_kind["resolve"]["nodes"][2]["deps"][0]["dep_kinds"] = serde_json::json!([
            {"kind": null, "target": null},
            {"kind": "dev", "target": null}
        ]);
        assert!(
            normalize_feature_fixture(&mixed_kind, &mixed_kind, &mixed_kind_lock)
                .expect("optional normal and nonoptional dev declarations share a feature key")
                .iter()
                .any(|record| record.id.starts_with("registry:codec@"))
        );

        let (mut mixed_target, mut mixed_target_lock) = weak_optional_metadata_fixture();
        rename_weak_optional_child(&mut mixed_target, &mut mixed_target_lock, "codec", "codec");
        mixed_target["packages"][2]["dependencies"][0]["target"] = "cfg(unix)".into();
        let mut windows = mixed_target["packages"][2]["dependencies"][0].clone();
        windows["target"] = "cfg(windows)".into();
        windows["optional"] = false.into();
        mixed_target["packages"][2]["dependencies"]
            .as_array_mut()
            .expect("declarations are an array")
            .push(windows);
        mixed_target["resolve"]["nodes"][2]["deps"][0]["dep_kinds"] = serde_json::json!([
            {"kind": null, "target": "cfg(unix)"},
            {"kind": null, "target": "cfg(windows)"}
        ]);
        assert!(
            normalize_feature_fixture(&mixed_target, &mixed_target, &mixed_target_lock)
                .expect("weak optional and nonoptional complementary targets share a key")
                .iter()
                .any(|record| record.id.starts_with("registry:codec@"))
        );
    }

    #[cfg(feature = "artifact-host")]
    #[test]
    fn cargo_metadata_nonoptional_strong_forward_does_not_invent_same_name_feature() {
        let (mut metadata, mut lock) = weak_optional_metadata_fixture();
        rename_weak_optional_child(&mut metadata, &mut lock, "codec", "codec");
        metadata["packages"][2]["dependencies"][0]["optional"] = false.into();
        metadata["packages"][2]["features"] = serde_json::json!({
            "codec": [],
            "std": ["codec/std"]
        });
        metadata["resolve"]["nodes"][2]["features"] = serde_json::json!(["std"]);

        assert!(
            normalize_feature_fixture(&metadata, &metadata, &lock)
                .expect("unrelated same-name local feature remains disabled")
                .iter()
                .any(|record| record.id.starts_with("registry:codec@"))
        );
    }

    #[cfg(feature = "artifact-host")]
    #[test]
    fn cargo_metadata_rlib_dependency_target_joins_by_exact_target_name() {
        let (mut metadata, lock) = weak_optional_metadata_fixture();
        metadata["packages"][3]["targets"][0]["kind"] = serde_json::json!(["rlib"]);
        metadata["packages"][3]["targets"][0]["crate_types"] = serde_json::json!(["rlib"]);

        assert_eq!(
            normalize_feature_fixture(&metadata, &metadata, &lock)
                .expect("an exact rlib/rlib dependency target is admissible")
                .len(),
            3
        );
    }

    #[cfg(feature = "artifact-host")]
    #[test]
    fn cargo_metadata_duplicate_declaration_child_features_do_not_activate_or_reject() {
        let (mut metadata, lock) = weak_optional_metadata_fixture();
        metadata["packages"][2]["dependencies"][0]["features"] = serde_json::json!(["std", "std"]);
        let baseline = normalize_feature_fixture(&metadata, &metadata, &lock)
            .expect("duplicate declaration child features are Cargo evidence, not local specs");
        assert_eq!(baseline.len(), 3);

        metadata["packages"][2]["dependencies"][0]["features"] =
            serde_json::json!(["rust-allocator", "std", "std"]);
        assert_eq!(
            normalize_feature_fixture(&metadata, &metadata, &lock)
                .expect("declaration child-feature permutations do not activate the edge"),
            baseline
        );
    }

    #[cfg(feature = "artifact-host")]
    #[test]
    fn cargo_metadata_aggregate_limits_accept_n_and_reject_n_plus_one() {
        const N: usize = 65_536;
        const STRING_CAP: usize = 32 * 1024 * 1024;

        let (fixture, _) = weak_optional_metadata_fixture();
        let fixture = serde_json::to_vec(&fixture).expect("serialize metadata bound fixture");
        let base = parse_cargo_metadata_v1(&fixture).expect("parse metadata bound fixture");
        let declaration = base.packages[2].dependencies[0].clone();
        let resolve_dependency = base.resolve.nodes[2].deps[0].clone();
        let dependency_kind = resolve_dependency.dep_kinds[0].clone();

        let blank = || {
            let mut metadata = base.clone();
            for package in &mut metadata.packages {
                package.dependencies.clear();
                package.features.0.clear();
            }
            for node in &mut metadata.resolve.nodes {
                node.features.clear();
                node.deps.clear();
            }
            metadata
        };

        let mut packages = blank();
        let package = packages.packages[0].clone();
        packages.packages = vec![package.clone(); N];
        validate_cargo_metadata_bounds(&packages, STRING_CAP)
            .expect("exact package aggregate cap is accepted");
        packages.packages.push(package);
        assert_eq!(
            validate_cargo_metadata_bounds(&packages, STRING_CAP),
            Err(ProtocolError::SizeLimit)
        );

        let mut nodes = blank();
        let node = nodes.resolve.nodes[0].clone();
        nodes.resolve.nodes = vec![node.clone(); N];
        validate_cargo_metadata_bounds(&nodes, STRING_CAP)
            .expect("exact node aggregate cap is accepted");
        nodes.resolve.nodes.push(node);
        assert_eq!(
            validate_cargo_metadata_bounds(&nodes, STRING_CAP),
            Err(ProtocolError::SizeLimit)
        );

        let mut declarations = blank();
        declarations.packages[0].dependencies = vec![declaration.clone(); N];
        validate_cargo_metadata_bounds(&declarations, STRING_CAP)
            .expect("exact declaration aggregate cap is accepted");
        declarations.packages[0].dependencies.push(declaration);
        assert_eq!(
            validate_cargo_metadata_bounds(&declarations, STRING_CAP),
            Err(ProtocolError::SizeLimit)
        );

        let mut feature_keys = blank();
        feature_keys.packages[0].features.0 = (0..N)
            .map(|index| (format!("feature-{index:05}"), Vec::new()))
            .collect();
        validate_cargo_metadata_bounds(&feature_keys, STRING_CAP)
            .expect("exact feature-key aggregate cap is accepted");
        feature_keys.packages[0]
            .features
            .0
            .insert("feature-overflow".to_owned(), Vec::new());
        assert_eq!(
            validate_cargo_metadata_bounds(&feature_keys, STRING_CAP),
            Err(ProtocolError::SizeLimit)
        );

        let mut feature_specs = blank();
        feature_specs.packages[0]
            .features
            .0
            .insert("feature".to_owned(), vec!["child".to_owned(); N]);
        validate_cargo_metadata_bounds(&feature_specs, STRING_CAP)
            .expect("exact feature-spec aggregate cap is accepted");
        feature_specs.packages[0]
            .features
            .0
            .get_mut("feature")
            .expect("feature remains present")
            .push("overflow".to_owned());
        assert_eq!(
            validate_cargo_metadata_bounds(&feature_specs, STRING_CAP),
            Err(ProtocolError::SizeLimit)
        );

        let mut enabled_features = blank();
        enabled_features.resolve.nodes[0].features = vec!["feature".to_owned(); N];
        validate_cargo_metadata_bounds(&enabled_features, STRING_CAP)
            .expect("exact enabled-feature aggregate cap is accepted");
        enabled_features.resolve.nodes[0]
            .features
            .push("overflow".to_owned());
        assert_eq!(
            validate_cargo_metadata_bounds(&enabled_features, STRING_CAP),
            Err(ProtocolError::SizeLimit)
        );

        let mut resolve_dependencies = blank();
        resolve_dependencies.resolve.nodes[0].deps = vec![resolve_dependency.clone(); N];
        validate_cargo_metadata_bounds(&resolve_dependencies, STRING_CAP)
            .expect("exact resolve-dependency aggregate cap is accepted");
        resolve_dependencies.resolve.nodes[0]
            .deps
            .push(resolve_dependency);
        assert_eq!(
            validate_cargo_metadata_bounds(&resolve_dependencies, STRING_CAP),
            Err(ProtocolError::SizeLimit)
        );

        let mut dependency_kinds = blank();
        let mut dependency = base.resolve.nodes[2].deps[0].clone();
        dependency.dep_kinds = vec![dependency_kind.clone(); N];
        dependency_kinds.resolve.nodes[0].deps.push(dependency);
        validate_cargo_metadata_bounds(&dependency_kinds, STRING_CAP)
            .expect("exact dep-kind aggregate cap is accepted");
        dependency_kinds.resolve.nodes[0].deps[0]
            .dep_kinds
            .push(dependency_kind);
        assert_eq!(
            validate_cargo_metadata_bounds(&dependency_kinds, STRING_CAP),
            Err(ProtocolError::SizeLimit)
        );
    }

    #[cfg(feature = "artifact-host")]
    #[test]
    fn cargo_metadata_feature_validation_is_linear_at_independent_product_caps() {
        const N: usize = 65_536;

        let (mut metadata, _) = weak_optional_metadata_fixture();
        let declaration = metadata["packages"][2]["dependencies"][0].clone();
        metadata["packages"][2]["dependencies"] =
            serde_json::Value::Array(vec![declaration; N - 2]);
        let mut features = serde_json::Map::new();
        features.insert(
            "runtime_detection".to_owned(),
            serde_json::json!(["dep:zlib-rs"]),
        );
        for index in 0..(N - 3) {
            features.insert(
                format!("feature-{index:05}"),
                serde_json::json!(["dep:zlib-rs"]),
            );
        }
        metadata["packages"][2]["features"] = serde_json::Value::Object(features);
        let encoded = serde_json::to_vec(&metadata).expect("serialize product-work fixture");
        assert!(encoded.len() < 32 * 1024 * 1024);
        let parsed =
            parse_cargo_metadata_v1(&encoded).expect("product-work fixture meets all caps");
        assert_eq!(
            classify_cargo_metadata_v1(&parsed).map(|_| ()),
            Err(ProtocolError::InvalidDocument),
            "ambiguous declarations were accepted"
        );
    }

    #[cfg(all(feature = "artifact-host", unix))]
    fn artifact_build_inputs() -> ArtifactBuildInputsV1 {
        ArtifactBuildInputsV1 {
            cargo_proxy_invocation: "/controlled/bin/cargo".into(),
            cargo_resolved_identity: "/resolved/rustup".into(),
            workspace_root: "/physical/workspace".into(),
            cargo_home: "/physical/cargo".into(),
            target_root: "/physical/workspace-target".into(),
            rustc_sysroot: "/physical/rust".into(),
            rustup_home: "/physical/rustup".into(),
            registry_roots: vec![
                ArtifactRegistryRootV1 {
                    normalized_dependency_id: concat!(
                        "registry:a@1.0.0#",
                        "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
                    )
                    .to_owned(),
                    physical_root: "/physical/cargo/registry/src/a".into(),
                },
                ArtifactRegistryRootV1 {
                    normalized_dependency_id: concat!(
                        "registry:b@1.0.0#",
                        "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
                    )
                    .to_owned(),
                    physical_root: "/physical/cargo/registry/src/ab".into(),
                },
            ],
            system_root: None,
        }
    }

    #[cfg(all(feature = "artifact-host", unix))]
    #[test]
    fn cargo_build_trace_is_typed_complete_and_binds_the_guest_executable() {
        let checksum = digest_hex('a');
        let guest_id =
            "path+file:///physical/workspace/crates/heleos-pdf-guest#heleos-pdf-guest@0.1.0";
        let protocol_id =
            "path+file:///physical/workspace/crates/heleos-pdf-protocol#heleos-pdf-protocol@0.1.0";
        let fixture_id = "path+file:///physical/workspace/crates/heleos-test-fixtures#heleos-test-fixtures@0.1.0";
        let dep_id = "registry+https://github.com/rust-lang/crates.io-index#dep@1.0.0";
        let target = |kind: &str, crate_type: &str, name: &str, src_path: &str| {
            serde_json::json!({
                "kind": [kind],
                "crate_types": [crate_type],
                "name": name,
                "src_path": src_path,
                "edition": "2024",
                "doc": kind != "custom-build",
                "doctest": kind == "lib",
                "test": kind != "custom-build",
                "required-features": []
            })
        };
        let dependency = |name: &str, source: Option<&str>, path: Option<&str>| {
            serde_json::json!({
                "name": name,
                "source": source,
                "req": "*",
                "kind": null,
                "rename": null,
                "optional": false,
                "uses_default_features": true,
                "features": [],
                "target": null,
                "registry": null,
                "path": path
            })
        };
        let guest_lib = target(
            "lib",
            "lib",
            "heleos_pdf_guest",
            "/physical/workspace/crates/heleos-pdf-guest/src/lib.rs",
        );
        let guest_bin = target(
            "bin",
            "bin",
            "heleos_pdf_guest",
            "/physical/workspace/crates/heleos-pdf-guest/src/main.rs",
        );
        let protocol_lib = target(
            "lib",
            "lib",
            "heleos_pdf_protocol",
            "/physical/workspace/crates/heleos-pdf-protocol/src/lib.rs",
        );
        let fixture_lib = target(
            "lib",
            "lib",
            "heleos_test_fixtures",
            "/physical/workspace/crates/heleos-test-fixtures/src/lib.rs",
        );
        let dep_lib = target(
            "lib",
            "lib",
            "dep",
            "/physical/cargo/registry/src/index.crates.io-1949cf8c6b5b557f/dep-1.0.0/src/lib.rs",
        );
        let dep_build = target(
            "custom-build",
            "bin",
            "build-script-build",
            "/physical/cargo/registry/src/index.crates.io-1949cf8c6b5b557f/dep-1.0.0/build.rs",
        );
        let ignored_target = |kind: &str, name: &str| {
            let mut value = target(
                kind,
                "bin",
                name,
                &format!(
                    "/physical/cargo/registry/src/index.crates.io-1949cf8c6b5b557f/dep-1.0.0/{name}.rs"
                ),
            );
            value["required-features"] = serde_json::json!(["optional-target"]);
            value
        };
        let dep_test = ignored_target("test", "dep_test");
        let dep_example = ignored_target("example", "dep_example");
        let dep_bench = ignored_target("bench", "dep_bench");
        let dep_other_bin = ignored_target("bin", "dep_tool");
        let metadata = serde_json::to_vec(&serde_json::json!({
            "packages": [
                {"id": guest_id, "name": "heleos-pdf-guest", "version": "0.1.0", "source": null, "checksum": null, "manifest_path": "/physical/workspace/crates/heleos-pdf-guest/Cargo.toml", "targets": [guest_lib, guest_bin], "dependencies": [
                    dependency("heleos-pdf-protocol", None, Some("/physical/workspace/crates/heleos-pdf-protocol")),
                    dependency("dep", Some("registry+https://github.com/rust-lang/crates.io-index"), None)
                ], "features": {}},
                {"id": protocol_id, "name": "heleos-pdf-protocol", "version": "0.1.0", "source": null, "checksum": null, "manifest_path": "/physical/workspace/crates/heleos-pdf-protocol/Cargo.toml", "targets": [protocol_lib], "dependencies": [], "features": {"default": []}},
                {"id": fixture_id, "name": "heleos-test-fixtures", "version": "0.1.0", "source": null, "checksum": null, "manifest_path": "/physical/workspace/crates/heleos-test-fixtures/Cargo.toml", "targets": [fixture_lib], "dependencies": [], "features": {}},
                {"id": dep_id, "name": "dep", "version": "1.0.0", "source": "registry+https://github.com/rust-lang/crates.io-index", "checksum": null, "manifest_path": "/physical/cargo/registry/src/index.crates.io-1949cf8c6b5b557f/dep-1.0.0/Cargo.toml", "targets": [dep_lib, dep_build, dep_test, dep_example, dep_bench, dep_other_bin], "dependencies": [], "features": {"feature": [], "optional-target": []}}
            ],
            "workspace_root": "/physical/workspace",
            "resolve": {"nodes": [
                {"id": guest_id, "features": [], "deps": [
                    {"name": "heleos_pdf_protocol", "pkg": protocol_id, "dep_kinds": [{"kind": null, "target": null}]},
                    {"name": "dep", "pkg": dep_id, "dep_kinds": [{"kind": null, "target": null}]}
                ]},
                {"id": protocol_id, "features": ["default"], "deps": []},
                {"id": fixture_id, "features": [], "deps": []},
                {"id": dep_id, "features": ["feature"], "deps": []}
            ]}
        }))
        .expect("serialize synthetic Cargo metadata");
        let lock = format!(
            concat!(
                "version = 4\n\n",
                "[[package]]\nname = \"dep\"\nversion = \"1.0.0\"\n",
                "source = \"registry+https://github.com/rust-lang/crates.io-index\"\n",
                "checksum = \"{}\"\n\n",
                "[[package]]\nname = \"heleos-pdf-guest\"\nversion = \"0.1.0\"\n",
                "dependencies = [\"dep 1.0.0\", \"heleos-pdf-protocol\"]\n\n",
                "[[package]]\nname = \"heleos-pdf-protocol\"\nversion = \"0.1.0\"\n\n",
                "[[package]]\nname = \"heleos-test-fixtures\"\nversion = \"0.1.0\"\n"
            ),
            checksum
        );
        let artifact = |package_id: &str,
                        manifest_path: &str,
                        target: serde_json::Value,
                        features: Vec<&str>,
                        filenames: Vec<&str>,
                        executable: Option<&str>| {
            let opt_level = match target["kind"][0].as_str() {
                Some("custom-build" | "proc-macro") => "0",
                _ => "3",
            };
            serde_json::json!({
                "reason": "compiler-artifact",
                "package_id": package_id,
                "manifest_path": manifest_path,
                "target": target,
                "profile": {"opt_level": opt_level, "debuginfo": 0, "debug_assertions": false, "overflow_checks": false, "test": false},
                "features": features,
                "filenames": filenames,
                "executable": executable,
                "fresh": false
            })
        };
        let guest_wasm = "/physical/workspace-target/wasm32-wasip1/release/heleos_pdf_guest.wasm";
        let records = vec![
            artifact(
                dep_id,
                "/physical/cargo/registry/src/index.crates.io-1949cf8c6b5b557f/dep-1.0.0/Cargo.toml",
                dep_lib,
                vec!["feature"],
                vec!["/physical/workspace-target/wasm32-wasip1/release/deps/libdep.rlib"],
                None,
            ),
            artifact(
                dep_id,
                "/physical/cargo/registry/src/index.crates.io-1949cf8c6b5b557f/dep-1.0.0/Cargo.toml",
                dep_build,
                vec!["feature"],
                vec!["/physical/workspace-target/release/build/dep/build-script-build"],
                None,
            ),
            serde_json::json!({"reason":"build-script-executed","package_id":dep_id,"linked_libs":[],"linked_paths":[],"cfgs":["dep_cfg"],"env":[["DEP_MODE","fixed"]],"out_dir":"/physical/workspace-target/wasm32-wasip1/release/build/dep/out"}),
            artifact(
                protocol_id,
                "/physical/workspace/crates/heleos-pdf-protocol/Cargo.toml",
                protocol_lib,
                vec!["default"],
                vec![
                    "/physical/workspace-target/wasm32-wasip1/release/deps/libheleos_pdf_protocol.rlib",
                ],
                None,
            ),
            artifact(
                guest_id,
                "/physical/workspace/crates/heleos-pdf-guest/Cargo.toml",
                guest_lib,
                vec![],
                vec![
                    "/physical/workspace-target/wasm32-wasip1/release/deps/libheleos_pdf_guest.rlib",
                ],
                None,
            ),
            artifact(
                guest_id,
                "/physical/workspace/crates/heleos-pdf-guest/Cargo.toml",
                guest_bin,
                vec![],
                vec![guest_wasm],
                Some(guest_wasm),
            ),
            serde_json::json!({"reason":"build-finished","success":true}),
        ];
        let mut stdout = Vec::new();
        for record in records {
            serde_json::to_writer(&mut stdout, &record).expect("serialize Cargo trace record");
            stdout.push(b'\n');
        }
        let mut inputs = artifact_build_inputs();
        inputs.registry_roots = vec![ArtifactRegistryRootV1 {
            normalized_dependency_id: format!("registry:dep@1.0.0#{checksum}"),
            physical_root:
                "/physical/cargo/registry/src/index.crates.io-1949cf8c6b5b557f/dep-1.0.0".into(),
        }];
        let policy = artifact_build_policy_v1(&inputs).expect("construct synthetic build policy");
        let evidence = validate_guest_build_evidence_v1(
            &stdout,
            &metadata,
            &metadata,
            lock.as_bytes(),
            lock.as_bytes(),
            "heleos-pdf-guest",
            &policy,
        )
        .expect("complete typed Cargo evidence validates");
        assert_eq!(evidence.compiler_units.len(), 5);
        assert_eq!(evidence.build_scripts.len(), 1);
        assert_eq!(
            evidence.guest_resolution_lock_sha256,
            guest_resolution_lock_sha256(lock.as_bytes()).expect("hash synthetic pruned lock")
        );
        assert_eq!(
            evidence.guest_resolution_cache_plan_sha256,
            guest_resolution_cache_plan_sha256(
                &guest_resolution_cache_plan_v1(lock.as_bytes(), lock.as_bytes())
                    .expect("derive synthetic cache plan")
            )
            .expect("hash synthetic cache plan")
        );
        assert!(evidence.compiler_units.iter().any(|unit| {
            unit.normalized_dependency_id == "workspace:heleos-pdf-guest@0.1.0"
                && unit.target_kind == CargoTargetKindV1::Bin
                && unit.target_name == "heleos_pdf_guest"
        }));

        let mut selected_bin_metadata: serde_json::Value =
            serde_json::from_slice(&metadata).expect("parse synthetic metadata for mutation");
        let selected_bin = selected_bin_metadata["packages"][0]["targets"]
            .as_array_mut()
            .expect("guest targets are an array")
            .iter_mut()
            .find(|target| {
                target["kind"] == serde_json::json!(["bin"]) && target["name"] == "heleos_pdf_guest"
            })
            .expect("find selected guest bin");
        selected_bin["required-features"] = serde_json::json!(["selected-bin-feature"]);
        let selected_bin_metadata =
            serde_json::to_vec(&selected_bin_metadata).expect("serialize mutated guest metadata");
        let mut selected_bin_trace = Vec::new();
        for line in stdout[..stdout.len() - 1].split(|byte| *byte == b'\n') {
            let mut record: serde_json::Value =
                serde_json::from_slice(line).expect("parse synthetic trace mutation record");
            if record["reason"] == "compiler-artifact"
                && record["package_id"] == guest_id
                && record["target"]["kind"] == serde_json::json!(["bin"])
            {
                record["target"]["required-features"] = serde_json::json!(["selected-bin-feature"]);
            }
            serde_json::to_writer(&mut selected_bin_trace, &record)
                .expect("serialize synthetic trace mutation record");
            selected_bin_trace.push(b'\n');
        }
        assert!(
            validate_guest_build_evidence_v1(
                &selected_bin_trace,
                &selected_bin_metadata,
                &selected_bin_metadata,
                lock.as_bytes(),
                lock.as_bytes(),
                "heleos-pdf-guest",
                &policy,
            )
            .is_err(),
            "matching nonempty required-features on the selected guest bin were accepted"
        );

        let mutated_profile = String::from_utf8(stdout.clone())
            .expect("synthetic trace is UTF-8")
            .replacen("\"opt_level\":\"3\"", "\"opt_level\":\"2\"", 1);
        assert!(
            validate_guest_build_evidence_v1(
                mutated_profile.as_bytes(),
                &metadata,
                &metadata,
                lock.as_bytes(),
                lock.as_bytes(),
                "heleos-pdf-guest",
                &policy,
            )
            .is_err(),
            "non-frozen release profile was accepted"
        );

        let mut wrong_workspace_inputs = inputs.clone();
        wrong_workspace_inputs.workspace_root = "/physical/other-workspace".into();
        let wrong_workspace_policy = artifact_build_policy_v1(&wrong_workspace_inputs)
            .expect("construct policy with a rebound working directory");
        assert!(
            validate_guest_build_evidence_v1(
                &stdout,
                &metadata,
                &metadata,
                lock.as_bytes(),
                lock.as_bytes(),
                "heleos-pdf-guest",
                &wrong_workspace_policy,
            )
            .is_err(),
            "policy working directory was not bound to guest metadata workspace_root"
        );

        let mut wrong_cargo_inputs = inputs.clone();
        wrong_cargo_inputs.cargo_home = "/physical/other-cargo".into();
        let wrong_cargo_policy = artifact_build_policy_v1(&wrong_cargo_inputs)
            .expect("construct policy with a rebound Cargo home");
        assert!(
            validate_guest_build_evidence_v1(
                &stdout,
                &metadata,
                &metadata,
                lock.as_bytes(),
                lock.as_bytes(),
                "heleos-pdf-guest",
                &wrong_cargo_policy,
            )
            .is_err(),
            "policy Cargo home was not bound to cache-plan active roots"
        );

        let original_root =
            "/physical/cargo/registry/src/index.crates.io-1949cf8c6b5b557f/dep-1.0.0";
        let rebound_root =
            "/physical/cargo/registry/src/index.crates.io-1949cf8c6b5b557f/rebound-1.0.0";
        let rebound_metadata = String::from_utf8(metadata.clone())
            .expect("synthetic metadata is UTF-8")
            .replace(original_root, rebound_root);
        let rebound_stdout = String::from_utf8(stdout.clone())
            .expect("synthetic trace is UTF-8")
            .replace(original_root, rebound_root);
        let mut rebound_inputs = inputs.clone();
        rebound_inputs.registry_roots[0].physical_root = rebound_root.into();
        let rebound_policy = artifact_build_policy_v1(&rebound_inputs)
            .expect("construct policy with a rebound active source root");
        assert!(
            validate_guest_build_evidence_v1(
                rebound_stdout.as_bytes(),
                rebound_metadata.as_bytes(),
                rebound_metadata.as_bytes(),
                lock.as_bytes(),
                lock.as_bytes(),
                "heleos-pdf-guest",
                &rebound_policy,
            )
            .is_err(),
            "active registry root was not bound to its cache-plan source path"
        );

        let rejects = |mutated: &[u8]| {
            assert!(
                validate_guest_build_evidence_v1(
                    mutated,
                    &metadata,
                    &metadata,
                    lock.as_bytes(),
                    lock.as_bytes(),
                    "heleos-pdf-guest",
                    &policy,
                )
                .is_err(),
                "accepted mutated Cargo build evidence"
            );
        };
        let stdout_text = String::from_utf8(stdout.clone()).expect("Cargo trace fixture is UTF-8");
        let final_record = b"{\"reason\":\"build-finished\",\"success\":true}\n";
        let prefix = stdout
            .strip_suffix(final_record)
            .expect("synthetic trace has the exact final record");
        let mut injected_ignored_target = prefix.to_vec();
        serde_json::to_writer(
            &mut injected_ignored_target,
            &artifact(
                dep_id,
                "/physical/cargo/registry/src/index.crates.io-1949cf8c6b5b557f/dep-1.0.0/Cargo.toml",
                dep_example,
                vec!["feature"],
                vec![
                    "/physical/workspace-target/wasm32-wasip1/release/deps/dep_example.wasm",
                ],
                None,
            ),
        )
        .expect("serialize injected ignored target artifact");
        injected_ignored_target.push(b'\n');
        injected_ignored_target.extend_from_slice(final_record);
        rejects(&injected_ignored_target);
        for mutated in [
            stdout_text.replacen("\"fresh\":false", "\"fresh\":true", 1),
            stdout_text.replacen(
                "\"required-features\":[]",
                "\"required-features\":[\"unreviewed\"]",
                1,
            ),
            stdout_text.replacen("\"opt_level\":\"3\"", "\"opt_level\":\"2\"", 1),
            stdout_text.replacen("\"opt_level\":\"3\"", "\"opt_level\":\"0\"", 1),
            stdout_text.replacen("\"opt_level\":\"0\"", "\"opt_level\":\"2\"", 1),
            stdout_text.replacen("\"opt_level\":\"0\"", "\"opt_level\":\"3\"", 1),
            stdout_text.replacen("\"debuginfo\":0", "\"debuginfo\":1", 1),
            stdout_text.replacen("\"debug_assertions\":false", "\"debug_assertions\":true", 1),
            stdout_text.replacen("\"overflow_checks\":false", "\"overflow_checks\":true", 1),
            stdout_text.replacen("compiler-artifact", "compiler-message", 1),
            stdout_text.replacen("\"name\":\"dep\"", "\"name\":\"wrong\"", 1),
            stdout_text.replacen(
                "\"DEP_MODE\",\"fixed\"",
                "\"DEP_MODE\",\"/physical/workspace/secret\"",
                1,
            ),
            stdout_text.replacen(
                "/physical/workspace-target/release/build/dep/build-script-build",
                "/physical/workspace-target/wasm32-wasip1/release/build/dep/build-script-build",
                1,
            ),
            stdout_text.replacen(
                concat!(
                    "\"filenames\":[\"/physical/workspace-target/wasm32-wasip1/",
                    "release/deps/libdep.rlib\"]"
                ),
                concat!(
                    "\"filenames\":[\"/physical/workspace-target/wasm32-wasip1/",
                    "release/deps/libdep.rlib\",",
                    "\"/physical/workspace-target/other/libdep.rmeta\"]"
                ),
                1,
            ),
            stdout_text.replacen("heleos_pdf_guest.wasm", "wrong_guest_candidate.wasm", 1),
            stdout_text.replacen(
                "\"reason\":\"build-finished\",\"success\":true",
                "\"extra\":1,\"reason\":\"build-finished\",\"success\":true",
                1,
            ),
            stdout_text.replacen(
                "\"reason\":\"build-finished\",\"success\":true",
                "\"reason\":\"build-finished\",\"success\":false",
                1,
            ),
        ] {
            rejects(mutated.as_bytes());
        }
        let without_script = stdout_text
            .lines()
            .filter(|line| !line.contains("build-script-executed"))
            .collect::<Vec<_>>()
            .join("\n")
            + "\n";
        rejects(without_script.as_bytes());
        let after_finished =
            format!("{stdout_text}{{\"reason\":\"build-finished\",\"success\":true}}\n");
        rejects(after_finished.as_bytes());

        let mut record_boundary = b"{}\n".repeat(65_536);
        assert_ne!(
            validate_guest_build_evidence_v1(
                &record_boundary,
                &metadata,
                &metadata,
                lock.as_bytes(),
                lock.as_bytes(),
                "heleos-pdf-guest",
                &policy,
            ),
            Err(ProtocolError::SizeLimit),
            "exactly 65,536 LF records must reach semantic validation"
        );
        record_boundary.extend_from_slice(b"{}\n");
        assert_eq!(
            validate_guest_build_evidence_v1(
                &record_boundary,
                &metadata,
                &metadata,
                lock.as_bytes(),
                lock.as_bytes(),
                "heleos-pdf-guest",
                &policy,
            ),
            Err(ProtocolError::SizeLimit),
            "65,537 LF records must stop at the record-count boundary"
        );
    }

    #[cfg(all(feature = "artifact-host", unix))]
    #[test]
    fn artifact_build_policy_is_complete_sorted_and_last_match_wins() {
        let policy = artifact_build_policy_v1(&artifact_build_inputs())
            .expect("valid deterministic build inputs");

        assert_eq!(
            policy.cargo_proxy_invocation,
            std::path::Path::new("/controlled/bin/cargo")
        );
        assert_eq!(
            policy.cargo_resolved_identity,
            std::path::Path::new("/resolved/rustup")
        );
        assert_eq!(
            policy.working_directory,
            std::path::Path::new("/physical/workspace")
        );
        assert_eq!(
            policy.argv,
            [
                "+1.96.1",
                "build",
                "--locked",
                "-p",
                "heleos-pdf-guest",
                "--target",
                "wasm32-wasip1",
                "--release",
                "--message-format=json-render-diagnostics",
                "--quiet",
            ]
        );
        assert!(policy.env_clear);
        assert!(
            policy
                .environment
                .windows(2)
                .all(|pair| pair[0].name.as_bytes() < pair[1].name.as_bytes())
        );
        assert_eq!(
            policy
                .environment
                .iter()
                .map(|entry| entry.name.as_str())
                .collect::<Vec<_>>(),
            vec![
                "CARGO_ENCODED_RUSTFLAGS",
                "CARGO_HOME",
                "CARGO_INCREMENTAL",
                "CARGO_NET_OFFLINE",
                "CARGO_TARGET_DIR",
                "CARGO_TERM_COLOR",
                "HOME",
                "LANG",
                "LC_ALL",
                "PATH",
                "RUSTUP_HOME",
                "RUSTUP_TOOLCHAIN",
                "SOURCE_DATE_EPOCH",
                "TEMP",
                "TMP",
                "TMPDIR",
                "TZ",
                "USERPROFILE",
            ]
        );
        let value = |name: &str| {
            policy
                .environment
                .iter()
                .find(|entry| entry.name == name)
                .map(|entry| entry.value.as_str())
                .expect("complete frozen environment")
        };
        assert_eq!(value("CARGO_HOME"), "/physical/cargo");
        assert_eq!(value("CARGO_TARGET_DIR"), "/physical/workspace-target");
        assert_eq!(value("HOME"), "/physical/cargo");
        assert_eq!(value("USERPROFILE"), "/physical/cargo");
        assert_eq!(value("PATH"), "/physical/rust/bin:/usr/bin:/bin");
        assert_eq!(value("RUSTUP_HOME"), "/physical/rustup");
        assert_eq!(value("RUSTUP_TOOLCHAIN"), "1.96.1");
        assert_eq!(value("CARGO_NET_OFFLINE"), "true");
        assert_eq!(value("CARGO_INCREMENTAL"), "0");
        assert_eq!(value("CARGO_TERM_COLOR"), "never");
        assert_eq!(value("LANG"), "C");
        assert_eq!(value("LC_ALL"), "C");
        assert_eq!(value("TZ"), "UTC");
        assert_eq!(value("SOURCE_DATE_EPOCH"), "0");
        for name in ["TMPDIR", "TMP", "TEMP"] {
            assert_eq!(value(name), "/physical/workspace-target/.heleos-tmp");
        }
        let rustflags = policy
            .environment
            .iter()
            .find(|entry| entry.name == "CARGO_ENCODED_RUSTFLAGS")
            .expect("controlled rustflags")
            .value
            .split('\u{1f}')
            .collect::<Vec<_>>();
        assert_eq!(rustflags.first(), Some(&"--remap-path-scope=object"));
        let cargo_home = rustflags
            .iter()
            .position(|value| *value == "/physical/cargo=/heleos/cargo")
            .expect("broad Cargo-home mapping");
        let registry_a = rustflags
            .iter()
            .position(|value| value.starts_with("/physical/cargo/registry/src/a=/heleos/registry/"))
            .expect("first textual-prefix registry mapping");
        let registry_ab = rustflags
            .iter()
            .position(|value| {
                value.starts_with("/physical/cargo/registry/src/ab=/heleos/registry/")
            })
            .expect("longer textual-prefix registry mapping");
        assert!(cargo_home < registry_a, "nested package root must win last");
        assert!(
            registry_a < registry_ab,
            "longer textual prefix must win last"
        );
        assert!(
            policy
                .scan_prefixes
                .contains(&b"/physical/workspace".to_vec())
        );
        assert!(
            policy
                .scan_prefixes
                .contains(&b"\\physical\\workspace".to_vec())
        );

        let mut reversed = artifact_build_inputs();
        reversed.registry_roots.reverse();
        assert_eq!(
            artifact_build_policy_v1(&reversed).expect("input order is irrelevant"),
            policy
        );
    }

    #[cfg(all(feature = "artifact-host", unix))]
    #[test]
    fn artifact_build_policy_rejects_ambiguous_or_injected_inputs() {
        let mut wrong_proxy = artifact_build_inputs();
        wrong_proxy.cargo_proxy_invocation = "/controlled/bin/rustup".into();
        assert!(artifact_build_policy_v1(&wrong_proxy).is_err());

        let mut relative = artifact_build_inputs();
        relative.workspace_root = "relative/workspace".into();
        assert!(artifact_build_policy_v1(&relative).is_err());

        let mut duplicate_role = artifact_build_inputs();
        duplicate_role.target_root = duplicate_role.workspace_root.clone();
        assert!(artifact_build_policy_v1(&duplicate_role).is_err());

        let mut injected = artifact_build_inputs();
        injected.registry_roots[0]
            .normalized_dependency_id
            .push('\u{1f}');
        assert!(artifact_build_policy_v1(&injected).is_err());

        let mut path_list_injection = artifact_build_inputs();
        path_list_injection.target_root = "/physical/target:injected".into();
        assert!(artifact_build_policy_v1(&path_list_injection).is_err());

        let mut malformed_registry = artifact_build_inputs();
        malformed_registry.registry_roots[0].normalized_dependency_id =
            format!("registry:@1.0.0#{}", "a".repeat(64));
        assert!(artifact_build_policy_v1(&malformed_registry).is_err());

        let mut normalized_collision = artifact_build_inputs();
        normalized_collision
            .registry_roots
            .push(ArtifactRegistryRootV1 {
                normalized_dependency_id: format!("registry:c@1.0.0#{}", "c".repeat(64)),
                physical_root: "/physical\\cargo\\registry\\src\\a".into(),
            });
        assert!(artifact_build_policy_v1(&normalized_collision).is_err());

        let mut unexpected_platform = artifact_build_inputs();
        unexpected_platform.system_root = Some("/windows".into());
        assert!(artifact_build_policy_v1(&unexpected_platform).is_err());

        use std::os::unix::ffi::OsStringExt;
        let mut non_utf8 = artifact_build_inputs();
        non_utf8.target_root = std::ffi::OsString::from_vec(vec![b'/', 0xff]).into();
        assert!(artifact_build_policy_v1(&non_utf8).is_err());
    }

    #[cfg(all(feature = "artifact-host", windows))]
    #[test]
    fn windows_artifact_build_policy_has_exact_platform_environment() {
        let inputs = ArtifactBuildInputsV1 {
            cargo_proxy_invocation: r"C:\proxy\cargo.exe".into(),
            cargo_resolved_identity: r"C:\rustup\rustup.exe".into(),
            workspace_root: r"D:\workspace".into(),
            cargo_home: r"D:\cargo".into(),
            target_root: r"D:\target".into(),
            rustc_sysroot: r"D:\rust".into(),
            rustup_home: r"D:\rustup".into(),
            registry_roots: vec![ArtifactRegistryRootV1 {
                normalized_dependency_id: format!("registry:a@1.0.0#{}", "a".repeat(64)),
                physical_root: r"D:\cargo\registry\src\a".into(),
            }],
            system_root: Some(r"C:\Windows".into()),
        };
        let policy = artifact_build_policy_v1(&inputs).expect("Windows policy is complete");
        let value = |name: &str| {
            policy
                .environment()
                .iter()
                .find(|entry| entry.name() == name)
                .map(ArtifactEnvironmentV1::value)
                .expect("frozen Windows environment")
        };
        assert_eq!(value("PATH"), r"D:\rust\bin;C:\Windows\System32;C:\Windows");
        assert_eq!(value("SystemRoot"), r"C:\Windows");
        assert_eq!(value("WINDIR"), r"C:\Windows");
        assert_eq!(value("ComSpec"), r"C:\Windows\System32\cmd.exe");
        assert_eq!(value("PATHEXT"), ".COM;.EXE;.BAT;.CMD");
        assert_eq!(
            policy.cargo_proxy_invocation(),
            std::path::Path::new(r"C:\proxy\cargo.exe")
        );

        let mut absent_system_root = inputs;
        absent_system_root.system_root = None;
        assert!(artifact_build_policy_v1(&absent_system_root).is_err());
    }

    #[cfg(all(feature = "artifact-host", unix))]
    #[test]
    fn physical_prefix_scanner_rejects_custom_section_leaks() {
        let policy = artifact_build_policy_v1(&artifact_build_inputs())
            .expect("valid policy supplies the complete prefix set");

        let leaked = b"/physical/workspace";
        let mut wasm = b"\0asm\x01\0\0\0".to_vec();
        let section_len = 1_usize
            .checked_add(1)
            .and_then(|length| length.checked_add(leaked.len()))
            .expect("small custom section");
        wasm.push(0);
        wasm.push(u8::try_from(section_len).expect("one-byte section length"));
        wasm.push(1);
        wasm.push(b'x');
        wasm.extend_from_slice(leaked);

        assert!(verify_no_physical_prefixes_v1(&wasm, &policy).is_err());

        let clean = b"\0asm\x01\0\0\0";
        assert!(verify_no_physical_prefixes_v1(clean, &policy).is_ok());

        let mut invalid_policy = policy;
        invalid_policy.scan_prefixes = vec![Vec::new()];
        assert!(verify_no_physical_prefixes_v1(clean, &invalid_policy).is_err());
    }
}
