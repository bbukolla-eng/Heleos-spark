use std::{io::Cursor, str::FromStr};

use heleos_core::{
    ActorId, Clock, DataClass, DocumentId, IdGenerator, IngestOutcome, JobState, PdfLimits,
    RevisionId, Sha256Digest, can_transition, canonical_document_ids, canonical_json, page_id,
};
use serde_json::json;
use uuid::Uuid;

struct FixedClock(i64);

impl Clock for FixedClock {
    fn now_unix_ms(&self) -> i64 {
        self.0
    }
}

struct FixedIds(Uuid);

impl IdGenerator for FixedIds {
    fn next_uuid(&self) -> Uuid {
        self.0
    }
}

#[test]
fn digest_parser_accepts_only_lowercase_sha256_hex() {
    let parsed =
        Sha256Digest::from_str("0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
            .expect("lowercase SHA-256 hex is accepted");

    assert_eq!(
        parsed.to_string(),
        "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    );
    assert!(
        Sha256Digest::from_str("0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF")
            .is_err()
    );
    assert!(Sha256Digest::from_str("../../not-a-digest").is_err());
    assert!(Sha256Digest::from_str("abcd").is_err());
}

#[test]
fn digest_hash_reader_hashes_streamed_bytes() {
    let digest = Sha256Digest::hash_reader(Cursor::new(b"heleos".to_vec()))
        .expect("in-memory reader can be hashed");

    assert_eq!(
        digest.to_string(),
        "1186aa429981a2a4cff4b5f5d1287817d8bf28a1ab3fc51195cb2b85db2279d2"
    );
}

#[test]
fn page_ids_are_stable_and_page_index_sensitive() {
    let content =
        Sha256Digest::from_str("0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
            .expect("valid digest");

    let first = page_id(content, 0);
    assert_eq!(first, page_id(content, 0));
    assert_ne!(first, page_id(content, 1));
}

#[test]
fn identical_content_has_identical_document_and_revision_ids() {
    let content =
        Sha256Digest::from_str("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
            .expect("valid digest");

    let first = canonical_document_ids(content);
    let second = canonical_document_ids(content);

    assert_eq!(first, second);
    assert_eq!(first.0, DocumentId::from(content));
    assert_eq!(first.1, RevisionId::from(content));
}

#[test]
fn job_transitions_allow_only_the_declared_state_machine_edges() {
    let legal = [
        (JobState::Queued, JobState::Running),
        (JobState::Queued, JobState::Cancelled),
        (JobState::Running, JobState::Interrupted),
        (JobState::Running, JobState::Succeeded),
        (JobState::Running, JobState::Failed),
        (JobState::Running, JobState::Cancelled),
        (JobState::Interrupted, JobState::Running),
        (JobState::Interrupted, JobState::Cancelled),
    ];
    let states = [
        JobState::Queued,
        JobState::Running,
        JobState::Interrupted,
        JobState::Succeeded,
        JobState::Failed,
        JobState::Cancelled,
    ];

    for from in states {
        for to in states {
            assert_eq!(
                can_transition(from, to),
                legal.contains(&(from, to)),
                "unexpected transition: {from:?} -> {to:?}"
            );
        }
    }
}

#[test]
fn ingest_outcomes_round_trip_as_exact_persisted_values() {
    let outcomes = [
        (IngestOutcome::AcceptedNew, "accepted_new"),
        (IngestOutcome::AcceptedDuplicate, "accepted_duplicate"),
        (IngestOutcome::IdempotentReplay, "idempotent_replay"),
        (IngestOutcome::QuarantinedCorrupt, "quarantined_corrupt"),
        (IngestOutcome::QuarantinedEncrypted, "quarantined_encrypted"),
        (
            IngestOutcome::QuarantinedUnsupported,
            "quarantined_unsupported",
        ),
        (
            IngestOutcome::QuarantinedSuspicious,
            "quarantined_suspicious",
        ),
        (IngestOutcome::QuarantinedLimit, "quarantined_limit"),
        (IngestOutcome::Interrupted, "interrupted"),
        (IngestOutcome::DeniedConflict, "denied_conflict"),
    ];

    for (outcome, expected) in outcomes {
        assert_eq!(
            serde_json::to_string(&outcome).expect("serializes"),
            format!("\"{expected}\"")
        );
        assert_eq!(
            serde_json::from_str::<IngestOutcome>(&format!("\"{expected}\""))
                .expect("deserializes"),
            outcome
        );
    }
}

#[test]
fn restricted_data_classes_remain_distinct_from_public() {
    assert_ne!(DataClass::Secret, DataClass::Public);
    assert_ne!(DataClass::Internal, DataClass::Public);
    assert_ne!(DataClass::ProjectConfidential, DataClass::Public);
}

#[test]
fn injectable_clock_and_id_generator_return_their_fixed_values() {
    let expected_id = Uuid::parse_str("8c2757af-021c-4e14-933f-48bd8c8d29cc").expect("valid UUID");

    assert_eq!(
        FixedClock(1_725_000_000_123).now_unix_ms(),
        1_725_000_000_123
    );
    assert_eq!(FixedIds(expected_id).next_uuid(), expected_id);
}

#[test]
fn actor_ids_preserve_unicode_and_reject_empty_or_control_text() {
    let actor = ActorId::from_str("Miyuki \u{5C71}\u{7530}").expect("valid actor identifier");

    assert_eq!(actor.as_str(), "Miyuki \u{5C71}\u{7530}");
    assert!(ActorId::from_str("").is_err());
    assert!(ActorId::from_str("operator\nadmin").is_err());
}

#[test]
fn canonical_json_matches_jcs_golden_bytes_and_sha256() {
    let value = json!({"z": null, "b": [true, "text"], "a": 1});
    let bytes = canonical_json(&value).expect("serializes as JCS");

    assert_eq!(bytes, br#"{"a":1,"b":[true,"text"],"z":null}"#);
    assert_eq!(
        Sha256Digest::hash_reader(Cursor::new(bytes))
            .expect("canonical bytes can be hashed")
            .to_string(),
        "ea57652e7c6277aa666ed7e4593a20a13970c456da281f4d7e835a9f210959d3"
    );
}

#[test]
fn default_pdf_limits_match_foundation_hard_bounds() {
    let limits = PdfLimits::default();

    assert_eq!(limits.max_input_bytes, 256 * 1024 * 1024);
    assert_eq!(limits.max_pages, 10_000);
    assert_eq!(limits.max_indirect_objects, 250_000);
    assert_eq!(limits.max_nested_references, 64);
    assert_eq!(limits.max_metadata_bytes, 16 * 1024 * 1024);
    assert_eq!(limits.max_page_axis_points, 14_400);
    assert_eq!(limits.max_guest_memory_bytes, 768 * 1024 * 1024);
    assert_eq!(limits.max_instances, 1);
    assert_eq!(limits.max_tables, 4);
    assert_eq!(limits.max_fuel, 5_000_000_000);
    assert_eq!(limits.timeout_seconds, 120);
    assert_eq!(limits.max_protocol_output_bytes, 4 * 1024 * 1024);
}
