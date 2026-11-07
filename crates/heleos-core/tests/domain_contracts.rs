#![forbid(unsafe_code)]

use std::{io::Cursor, str::FromStr};

use heleos_core::{
    ActorId, Clock, DataClass, DocumentId, EvidenceId, HeleosError, IdGenerator, IngestEventId,
    IngestOutcome, JobId, JobState, PdfLimits, ProjectId, RevisionId, Sha256Digest, can_transition,
    canonical_document_ids, canonical_json, page_id,
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
fn digest_serde_uses_lowercase_hex_and_rejects_uppercase() {
    let digest =
        Sha256Digest::from_str("0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef")
            .expect("valid digest");
    let encoded = serde_json::to_string(&digest).expect("digest serializes");

    assert_eq!(
        encoded,
        "\"0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef\""
    );
    assert_eq!(
        serde_json::from_str::<Sha256Digest>(&encoded).expect("digest deserializes"),
        digest
    );
    assert!(
        serde_json::from_str::<Sha256Digest>(
            "\"0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF0123456789ABCDEF\""
        )
        .is_err()
    );
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
    assert_eq!(
        first.as_digest().to_string(),
        "a7c0c134d8a5b374df92a59aaaced89310e3567a07474b0582d30baed94b435f"
    );
    assert_eq!(
        page_id(content, 1).as_digest().to_string(),
        "c5b845cee03cc3b6093d4434fa4c746ef811baed7d859589dbbe81d2582a7478"
    );
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
fn uuid_backed_ids_reject_empty_text_and_round_trip_through_serde() {
    struct UuidIdContract {
        name: &'static str,
        parse_uuid: fn(&str) -> std::result::Result<Uuid, HeleosError>,
        serde_round_trip: fn(Uuid) -> Uuid,
    }

    let contracts = [
        UuidIdContract {
            name: "ProjectId",
            parse_uuid: |value| ProjectId::from_str(value).map(|id| *id.as_uuid()),
            serde_round_trip: |uuid| {
                let encoded = serde_json::to_value(ProjectId::from_uuid(uuid)).expect("serializes");
                *serde_json::from_value::<ProjectId>(encoded)
                    .expect("deserializes")
                    .as_uuid()
            },
        },
        UuidIdContract {
            name: "JobId",
            parse_uuid: |value| JobId::from_str(value).map(|id| *id.as_uuid()),
            serde_round_trip: |uuid| {
                let encoded = serde_json::to_value(JobId::from_uuid(uuid)).expect("serializes");
                *serde_json::from_value::<JobId>(encoded)
                    .expect("deserializes")
                    .as_uuid()
            },
        },
        UuidIdContract {
            name: "IngestEventId",
            parse_uuid: |value| IngestEventId::from_str(value).map(|id| *id.as_uuid()),
            serde_round_trip: |uuid| {
                let encoded =
                    serde_json::to_value(IngestEventId::from_uuid(uuid)).expect("serializes");
                *serde_json::from_value::<IngestEventId>(encoded)
                    .expect("deserializes")
                    .as_uuid()
            },
        },
        UuidIdContract {
            name: "EvidenceId",
            parse_uuid: |value| EvidenceId::from_str(value).map(|id| *id.as_uuid()),
            serde_round_trip: |uuid| {
                let encoded =
                    serde_json::to_value(EvidenceId::from_uuid(uuid)).expect("serializes");
                *serde_json::from_value::<EvidenceId>(encoded)
                    .expect("deserializes")
                    .as_uuid()
            },
        },
    ];
    let expected = Uuid::parse_str("8c2757af-021c-4e14-933f-48bd8c8d29cc").expect("valid UUID");

    for contract in contracts {
        assert!(
            (contract.parse_uuid)("").is_err(),
            "{} accepts empty text",
            contract.name
        );
        assert_eq!(
            (contract.parse_uuid)(&expected.to_string()).expect("parses UUID"),
            expected,
            "{} changes a parsed UUID",
            contract.name
        );
        assert_eq!(
            (contract.serde_round_trip)(expected),
            expected,
            "{} changes a serde UUID round trip",
            contract.name
        );
    }
}

#[test]
fn actor_ids_preserve_unicode_and_reject_empty_or_control_text() {
    let actor = ActorId::from_str("Miyuki \u{5C71}\u{7530}").expect("valid actor identifier");

    assert_eq!(actor.as_str(), "Miyuki \u{5C71}\u{7530}");
    assert!(ActorId::from_str("").is_err());
    assert!(ActorId::from_str("operator\nadmin").is_err());
}

#[test]
fn actor_ids_enforce_ascii_and_multibyte_utf8_byte_boundaries() {
    let ascii_128 = "a".repeat(128);
    let ascii_129 = "a".repeat(129);
    let multibyte_128 = "é".repeat(64);
    let multibyte_130 = "é".repeat(65);

    assert_eq!(
        ActorId::from_str(&ascii_128)
            .expect("128-byte ASCII actor ID is valid")
            .as_str(),
        ascii_128
    );
    assert!(ActorId::from_str(&ascii_129).is_err());
    assert_eq!(
        ActorId::from_str(&multibyte_128)
            .expect("128-byte multibyte actor ID is valid")
            .as_str(),
        multibyte_128
    );
    assert!(ActorId::from_str(&multibyte_130).is_err());
}

#[test]
fn canonical_json_matches_rfc_8785_numeric_and_string_escape_golden() {
    let value: serde_json::Value = serde_json::from_str(
        r#"{"numbers":[333333333.33333329,1E30,4.50,2e-3,0.000000000000000000000000001],"string":"\u20ac$\u000F\u000aA'\u0042\u0022\u005c\\\"\/","literals":[null,true,false]}"#,
    )
    .expect("RFC 8785 input is valid JSON");
    let expected = r#"{"literals":[null,true,false],"numbers":[333333333.3333333,1e+30,4.5,0.002,1e-27],"string":"€$\u000f\nA'B\"\\\\\"/"}"#;
    let bytes = canonical_json(&value).expect("serializes as JCS");

    assert_eq!(bytes, expected.as_bytes());
    assert_eq!(
        Sha256Digest::hash_reader(Cursor::new(bytes))
            .expect("canonical bytes can be hashed")
            .to_string(),
        "2d5e01a318d0f0879ab568c4be289c8b1f64ef8921a53c6277d5e069978baacb"
    );
}

#[test]
fn canonical_json_normalizes_negative_zero_and_utf16_property_order() {
    let negative_zero = json!({"negative_zero": -0.0_f64});
    let expected_negative_zero = r#"{"negative_zero":0}"#;
    let utf16_ordering = json!({
        "€": "Euro Sign",
        "\r": "Carriage Return",
        "דּ": "Hebrew Letter Dalet With Dagesh",
        "1": "One",
        "😀": "Emoji: Grinning Face",
        "": "Control",
        "ö": "Latin Small Letter O With Diaeresis",
    });
    let expected_utf16_ordering = r#"{"\r":"Carriage Return","1":"One","":"Control","ö":"Latin Small Letter O With Diaeresis","€":"Euro Sign","😀":"Emoji: Grinning Face","דּ":"Hebrew Letter Dalet With Dagesh"}"#;

    assert_ne!(
        serde_json::to_vec(&utf16_ordering).expect("ordinary JSON serializes"),
        expected_utf16_ordering.as_bytes(),
        "ordinary serde_json must not substitute for JCS UTF-16 key ordering"
    );

    for (value, expected, expected_hash) in [
        (
            negative_zero,
            expected_negative_zero.as_bytes(),
            "f2303fcbf6a2d8523a5e672226b90b8dd919606d4707574eee02b83573a968de",
        ),
        (
            utf16_ordering,
            expected_utf16_ordering.as_bytes(),
            "5e321556d22018a9656991a9e94f77ec175fa193e52a2429d312f8419ec8b08c",
        ),
    ] {
        let bytes = canonical_json(&value).expect("serializes as JCS");

        assert_eq!(bytes, expected);
        assert_eq!(
            Sha256Digest::hash_reader(Cursor::new(bytes))
                .expect("canonical bytes can be hashed")
                .to_string(),
            expected_hash
        );
    }
}

#[test]
fn canonical_json_rejects_non_finite_floats() {
    for value in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY] {
        assert!(canonical_json(&value).is_err());
    }
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
