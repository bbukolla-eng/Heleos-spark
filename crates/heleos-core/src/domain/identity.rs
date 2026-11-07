use std::{fmt, str::FromStr};

use serde::{Deserialize, Deserializer, Serialize, Serializer, de};
use sha2::{Digest, Sha256};
use uuid::Uuid;

use crate::{HeleosError, Sha256Digest};

macro_rules! uuid_id {
    ($name:ident) => {
        #[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, PartialEq, Serialize)]
        #[serde(transparent)]
        pub struct $name(Uuid);

        impl $name {
            pub const fn from_uuid(value: Uuid) -> Self {
                Self(value)
            }

            pub const fn as_uuid(&self) -> &Uuid {
                &self.0
            }
        }

        impl From<Uuid> for $name {
            fn from(value: Uuid) -> Self {
                Self::from_uuid(value)
            }
        }

        impl FromStr for $name {
            type Err = HeleosError;

            fn from_str(value: &str) -> crate::Result<Self> {
                if value.is_empty() {
                    return Err(HeleosError::InvalidId);
                }
                Uuid::parse_str(value)
                    .map(Self::from_uuid)
                    .map_err(|_| HeleosError::InvalidId)
            }
        }
    };
}

uuid_id!(ProjectId);
uuid_id!(JobId);
uuid_id!(IngestEventId);
uuid_id!(EvidenceId);

#[derive(Clone, Debug, Eq, Hash, PartialEq)]
pub struct ActorId(String);

impl ActorId {
    pub const MAX_UTF8_BYTES: usize = 128;

    pub fn as_str(&self) -> &str {
        &self.0
    }
}

impl fmt::Display for ActorId {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl FromStr for ActorId {
    type Err = HeleosError;

    fn from_str(value: &str) -> crate::Result<Self> {
        if value.is_empty()
            || value.len() > Self::MAX_UTF8_BYTES
            || value.chars().any(char::is_control)
        {
            return Err(HeleosError::InvalidId);
        }
        Ok(Self(value.to_owned()))
    }
}

impl Serialize for ActorId {
    fn serialize<S: Serializer>(&self, serializer: S) -> std::result::Result<S::Ok, S::Error> {
        serializer.serialize_str(&self.0)
    }
}

impl<'de> Deserialize<'de> for ActorId {
    fn deserialize<D: Deserializer<'de>>(deserializer: D) -> std::result::Result<Self, D::Error> {
        let value = String::deserialize(deserializer)?;
        Self::from_str(&value).map_err(de::Error::custom)
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, PartialEq, Serialize)]
#[serde(transparent)]
pub struct DocumentId(Sha256Digest);

impl DocumentId {
    pub const fn as_digest(&self) -> &Sha256Digest {
        &self.0
    }
}

impl From<Sha256Digest> for DocumentId {
    fn from(value: Sha256Digest) -> Self {
        Self(value)
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, PartialEq, Serialize)]
#[serde(transparent)]
pub struct RevisionId(Sha256Digest);

impl RevisionId {
    pub const fn as_digest(&self) -> &Sha256Digest {
        &self.0
    }
}

impl From<Sha256Digest> for RevisionId {
    fn from(value: Sha256Digest) -> Self {
        Self(value)
    }
}

#[derive(Clone, Copy, Debug, Deserialize, Eq, Hash, PartialEq, Serialize)]
#[serde(transparent)]
pub struct SheetId(Sha256Digest);

impl SheetId {
    pub const fn as_digest(&self) -> &Sha256Digest {
        &self.0
    }
}

impl From<Sha256Digest> for SheetId {
    fn from(value: Sha256Digest) -> Self {
        Self(value)
    }
}

pub fn canonical_document_ids(content: Sha256Digest) -> (DocumentId, RevisionId) {
    (DocumentId::from(content), RevisionId::from(content))
}

pub fn page_id(content: Sha256Digest, zero_based_page_index: u32) -> SheetId {
    let mut hasher = Sha256::new();
    hasher.update(b"heleos-page-v1\0");
    hasher.update(content.as_bytes());
    hasher.update(zero_based_page_index.to_be_bytes());
    SheetId::from(Sha256Digest::from_bytes(hasher.finalize().into()))
}
