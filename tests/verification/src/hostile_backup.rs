//! Independent test encoder for the documented path-free HELEOSB1 grammar.
//! No production archive/manifest types, encoders, parsers, or constants are used.

use ed25519_dalek::Signer;
use heleos_core::Sha256Digest;

#[derive(Clone, Copy, Debug)]
pub enum Mutation {
    None,
    Magic,
    ManifestLength,
    TableLength,
    DatabaseLength,
    BlobLength,
    UnknownKind,
    DatabaseOrder,
    DuplicateDatabase,
    DuplicateBlob,
    BlobOrder,
    Signature,
    Signer,
    TableBytes,
    PayloadBytes,
    TruncatedPayload,
    TrailingData,
    NoncanonicalManifest,
    PayloadTotal,
}

#[derive(Clone)]
struct Entry {
    kind: u8,
    digest: Sha256Digest,
    length: u64,
    bytes: Vec<u8>,
}
impl Entry {
    fn new(kind: u8, bytes: Vec<u8>) -> Self {
        Self {
            kind,
            digest: Sha256Digest::hash_reader(bytes.as_slice()).unwrap(),
            length: bytes.len() as u64,
            bytes,
        }
    }
}

pub struct HostileBackup {
    database: Vec<u8>,
}
impl HostileBackup {
    pub fn new(database: Vec<u8>) -> Self {
        Self { database }
    }

    pub fn encode(&self, signer: &ed25519_dalek::SigningKey, mutation: Mutation) -> Vec<u8> {
        let database = Entry::new(0, self.database.clone());
        let mut entries = vec![database.clone()];
        match mutation {
            Mutation::DatabaseLength => entries[0].length = 16 * 1024 * 1024 * 1024 + 1,
            Mutation::BlobLength => {
                let mut blob = Entry::new(1, b"blob".to_vec());
                blob.length = 256 * 1024 * 1024 + 1;
                entries.push(blob);
            }
            Mutation::UnknownKind => entries[0].kind = 255,
            Mutation::DatabaseOrder => entries.insert(0, Entry::new(1, b"blob".to_vec())),
            Mutation::DuplicateDatabase => entries.push(database.clone()),
            Mutation::DuplicateBlob => {
                let blob = Entry::new(1, b"same blob".to_vec());
                entries.extend([blob.clone(), blob]);
            }
            Mutation::BlobOrder => {
                let mut blobs = [
                    Entry::new(1, b"alpha".to_vec()),
                    Entry::new(1, b"beta".to_vec()),
                ];
                blobs.sort_by(|left, right| right.digest.as_bytes().cmp(left.digest.as_bytes()));
                entries.extend(blobs);
            }
            _ => {}
        }
        let mut table = Vec::new();
        for entry in &entries {
            table.push(entry.kind);
            table.extend_from_slice(entry.digest.as_bytes());
            table.extend_from_slice(&entry.length.to_be_bytes());
        }
        let public_key = signer.verifying_key().to_bytes();
        let table_digest = Sha256Digest::hash_reader(table.as_slice()).unwrap();
        let signer_digest = Sha256Digest::hash_reader(public_key.as_slice()).unwrap();
        let mut payload_total: u64 = entries.iter().map(|entry| entry.length).sum();
        if matches!(mutation, Mutation::PayloadTotal) {
            payload_total += 1;
        }
        let database_length = if matches!(mutation, Mutation::DatabaseLength) {
            entries[0].length
        } else {
            database.length
        };
        // All keys are fixed and emitted in RFC 8785 order. Values are bounded integer
        // lengths or SHA-256 hex strings; the independent JCS encoder quotes strings.
        let mut manifest = format!(
            "{{\"database\":{{\"byte_length\":{},\"sha256\":{}}},\"decoded_payload_byte_length\":{},\"entry_count\":{},\"entry_table_byte_length\":{},\"entry_table_sha256\":{},\"schema\":\"heleos.backup-manifest/v1\",\"signer_key_sha256\":{}}}",
            database_length, serde_jcs::to_string(&database.digest.to_string()).unwrap(),
            payload_total, entries.len(), table.len(), serde_jcs::to_string(&table_digest.to_string()).unwrap(),
            serde_jcs::to_string(&signer_digest.to_string()).unwrap(),
        ).into_bytes();
        if matches!(mutation, Mutation::NoncanonicalManifest) {
            manifest.insert(0, b' ');
        }
        let mut message = b"heleos-backup-v1\0".to_vec();
        message.extend_from_slice(&manifest);
        let mut signature = signer.sign(&message).to_bytes();
        if matches!(mutation, Mutation::Signature) {
            signature[0] ^= 1;
        }
        let mut embedded_signer = public_key;
        if matches!(mutation, Mutation::Signer) {
            embedded_signer[0] ^= 1;
        }
        if matches!(mutation, Mutation::TableBytes) {
            table[1] ^= 1;
        }
        let mut output = b"HELEOSB1".to_vec();
        let manifest_length = if matches!(mutation, Mutation::ManifestLength) {
            u64::MAX
        } else {
            manifest.len() as u64
        };
        output.extend_from_slice(&manifest_length.to_be_bytes());
        output.extend_from_slice(&manifest);
        output.extend_from_slice(&embedded_signer);
        output.extend_from_slice(&signature);
        let table_length = if matches!(mutation, Mutation::TableLength) {
            u64::MAX
        } else {
            table.len() as u64
        };
        output.extend_from_slice(&table_length.to_be_bytes());
        output.extend_from_slice(&table);
        let payload_offset = output.len();
        for entry in &entries {
            output.extend_from_slice(&entry.bytes);
        }
        match mutation {
            Mutation::Magic => output[0] ^= 1,
            Mutation::PayloadBytes => output[payload_offset] ^= 1,
            Mutation::TruncatedPayload => {
                output.pop().unwrap();
            }
            Mutation::TrailingData => output.push(0x7f),
            _ => {}
        }
        output
    }
}
