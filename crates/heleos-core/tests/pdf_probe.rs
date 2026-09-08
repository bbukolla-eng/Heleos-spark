#![forbid(unsafe_code)]

use std::fs::{self, File, OpenOptions};
use std::io::{Cursor, Read, Write};
use std::path::{Path, PathBuf};
use std::process::{Command, ExitStatus, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, mpsc};
use std::thread;
use std::time::{Duration, Instant};

use heleos_core::{
    ApprovedPdfGuest, IngestOutcome, PageMetadata, PageTransform, PageUnit, PdfActiveFeature,
    PdfInspection, PdfLimitKind, PdfLimits, PdfProbe, PdfProbeOutcome, PdfProbeProvenance,
    PdfQuarantine, PdfQuarantineReason, PdfSandboxConfig, PutOutcome, RevisionId, Sha256Digest,
    Vault, VaultConfig, VaultOpenMode, VaultWriteBudget, WasiPdfProbe, canonical_json, page_id,
};
use heleos_pdf_protocol::{
    ArtifactBuildInputsV1, ArtifactBuildPolicyV1, ArtifactDependencyGraphV1, CoreValueTypeV1,
    DependencyKindV1, DependencyRecordV1, ExportRecordV1, GuestBuildEvidenceV1,
    GuestResolutionCachePlanV1, GuestResolutionWorkspaceSpecV1, ImportRecordV1,
    MAX_GUEST_WASM_BYTES, PROTOCOL_VERSION, PdfActiveFeatureV1, PdfDocumentLimitsV1,
    PdfGuestOutcomeV1, PdfGuestReasonV1, PdfRequestV1, PdfResponseV1, SourceFileV1,
    artifact_build_policy_v1, decode_request_v1, decode_response_v1, dependency_graph_sha256,
    encode_request_v1, encode_response_v1, exports_sha256, guest_resolution_cache_plan_sha256,
    guest_resolution_cache_plan_v1, guest_resolution_lock_sha256,
    guest_resolution_workspace_spec_v1, imports_sha256, normalize_dependency_graph_v1,
    source_tree_sha256, validate_guest_build_evidence_v1,
    validate_guest_resolution_lock_projection_v1, validate_guest_source_closure_v1,
    validate_pdf_guest_module_policy_v1, verify_no_physical_prefixes_v1,
};
use serde::Deserialize;

fn digest(byte: u8) -> Sha256Digest {
    Sha256Digest::from_bytes([byte; 32])
}

fn provenance() -> PdfProbeProvenance {
    PdfProbeProvenance {
        parser_name: "lopdf".to_owned(),
        parser_version: "0.44.0".to_owned(),
        guest_wasm_sha256: digest(0x11),
        guest_source_tree_sha256: digest(0x22),
        guest_dependency_graph_sha256: digest(0x33),
        protocol_version: PROTOCOL_VERSION.to_owned(),
    }
}

#[allow(dead_code)]
fn assert_pdf_probe_signature<T: PdfProbe>() {
    let _method: fn(
        &T,
        heleos_core::VerifiedObject,
        RevisionId,
        heleos_core::PdfLimits,
    ) -> heleos_core::Result<PdfProbeOutcome> = T::probe;
}

#[test]
fn public_outcomes_have_frozen_adjacent_jcs_shapes() {
    let content = digest(0x44);
    let revision = RevisionId::from(content);
    let accepted = PdfProbeOutcome::Accepted(PdfInspection {
        revision_id: revision,
        content_sha256: content,
        byte_length: 7,
        provenance: provenance(),
        pages: vec![PageMetadata {
            index: 0,
            page_id: page_id(content, 0),
            width_micropoints: 612_000_000,
            height_micropoints: 792_000_000,
            unit: PageUnit::Point,
            rotation_degrees: 0,
            transform: PageTransform {
                m11: 1,
                m12: 0,
                m21: 0,
                m22: -1,
                tx_micropoints: 0,
                ty_micropoints: 792_000_000,
            },
        }],
    });
    let encoded = canonical_json(&accepted).expect("accepted outcome canonicalizes");
    assert_eq!(
        String::from_utf8(encoded).expect("JCS is UTF-8"),
        concat!(
            "{\"outcome\":\"accepted\",\"value\":{\"byte_length\":7,",
            "\"content_sha256\":\"4444444444444444444444444444444444444444444444444444444444444444\",",
            "\"pages\":[{\"height_micropoints\":792000000,\"index\":0,",
            "\"page_id\":\"a35e4cdf18f6bbf3648f458d83723d937766981656788836f0c5c21373090176\",",
            "\"rotation_degrees\":0,\"transform\":{\"m11\":1,\"m12\":0,\"m21\":0,",
            "\"m22\":-1,\"tx_micropoints\":0,\"ty_micropoints\":792000000},",
            "\"unit\":\"pt\",\"width_micropoints\":612000000}],",
            "\"provenance\":{\"guest_dependency_graph_sha256\":",
            "\"3333333333333333333333333333333333333333333333333333333333333333\",",
            "\"guest_source_tree_sha256\":\"2222222222222222222222222222222222222222222222222222222222222222\",",
            "\"guest_wasm_sha256\":\"1111111111111111111111111111111111111111111111111111111111111111\",",
            "\"parser_name\":\"lopdf\",\"parser_version\":\"0.44.0\",",
            "\"protocol_version\":\"heleos.pdf-probe/v1\"},",
            "\"revision_id\":\"4444444444444444444444444444444444444444444444444444444444444444\"}}"
        )
    );

    let quarantine = PdfProbeOutcome::Quarantined(PdfQuarantine {
        content_sha256: content,
        byte_length: 7,
        provenance: provenance(),
        reason: PdfQuarantineReason::LimitExceeded(PdfLimitKind::Pages),
    });
    let value = serde_json::to_value(quarantine).expect("quarantine serializes");
    assert_eq!(value["outcome"], "quarantined");
    assert!(value["value"].get("revision_id").is_none());
    assert_eq!(value["value"]["reason"]["kind"], "limit_exceeded");
    assert_eq!(value["value"]["reason"]["detail"], "pages");
}

#[test]
fn quarantine_reason_mapping_is_single_and_exhaustive() {
    let cases = [
        (
            PdfQuarantineReason::BadMagic,
            IngestOutcome::QuarantinedCorrupt,
        ),
        (
            PdfQuarantineReason::Corrupt,
            IngestOutcome::QuarantinedCorrupt,
        ),
        (
            PdfQuarantineReason::InvalidGeometry,
            IngestOutcome::QuarantinedCorrupt,
        ),
        (
            PdfQuarantineReason::Encrypted,
            IngestOutcome::QuarantinedEncrypted,
        ),
        (
            PdfQuarantineReason::UnsupportedUserUnit,
            IngestOutcome::QuarantinedUnsupported,
        ),
        (
            PdfQuarantineReason::ActiveFeature(PdfActiveFeature::OpenAction),
            IngestOutcome::QuarantinedSuspicious,
        ),
        (
            PdfQuarantineReason::SandboxTrap,
            IngestOutcome::QuarantinedSuspicious,
        ),
        (
            PdfQuarantineReason::ProtocolBreach,
            IngestOutcome::QuarantinedSuspicious,
        ),
        (
            PdfQuarantineReason::LimitExceeded(PdfLimitKind::InputBytes),
            IngestOutcome::QuarantinedLimit,
        ),
    ];

    for (reason, expected) in cases {
        let quarantine = PdfQuarantine {
            content_sha256: digest(0x55),
            byte_length: 1,
            provenance: provenance(),
            reason,
        };
        assert_eq!(quarantine.ingest_outcome(), expected);
    }
}

fn request() -> PdfRequestV1 {
    PdfRequestV1 {
        protocol: PROTOCOL_VERSION.to_owned(),
        input_sha256: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa".to_owned(),
        byte_length: 9,
        limits: PdfDocumentLimitsV1 {
            max_input_bytes: 9,
            max_pages: 2,
            max_indirect_objects: 20,
            max_nested_references: 4,
            max_metadata_bytes: 1024,
            max_page_axis_points: 1000,
        },
    }
}

#[test]
fn protocol_request_is_exact_canonical_jcs_lf_eof() {
    let encoded = encode_request_v1(&request()).expect("request encodes");
    assert_eq!(
        encoded,
        concat!(
            "{\"byte_length\":9,\"input_sha256\":",
            "\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",",
            "\"limits\":{\"max_indirect_objects\":20,\"max_input_bytes\":9,",
            "\"max_metadata_bytes\":1024,\"max_nested_references\":4,",
            "\"max_page_axis_points\":1000,\"max_pages\":2},",
            "\"protocol\":\"heleos.pdf-probe/v1\"}\n"
        )
        .as_bytes()
    );
    assert_eq!(
        decode_request_v1(&encoded).expect("request decodes"),
        request()
    );
}

#[test]
fn protocol_rejects_noncanonical_duplicate_unknown_crlf_and_trailing_input() {
    let canonical = encode_request_v1(&request()).expect("request encodes");
    let mut no_lf = canonical.clone();
    no_lf.pop();
    let trailing = [canonical.as_slice(), b"x"].concat();
    let crlf = [&canonical[..canonical.len() - 1], b"\r\n".as_slice()].concat();
    let mut duplicate = br#"{"byte_length":9,"byte_length":9,"input_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","limits":{"max_indirect_objects":20,"max_input_bytes":9,"max_metadata_bytes":1024,"max_nested_references":4,"max_page_axis_points":1000,"max_pages":2},"protocol":"heleos.pdf-probe/v1"}"#.to_vec();
    duplicate.push(b'\n');
    let mut unknown = br#"{"byte_length":9,"input_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","limits":{"max_indirect_objects":20,"max_input_bytes":9,"max_metadata_bytes":1024,"max_nested_references":4,"max_page_axis_points":1000,"max_pages":2},"protocol":"heleos.pdf-probe/v1","unknown":0}"#.to_vec();
    unknown.push(b'\n');
    let mut reordered = br#"{"protocol":"heleos.pdf-probe/v1","byte_length":9,"input_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","limits":{"max_indirect_objects":20,"max_input_bytes":9,"max_metadata_bytes":1024,"max_nested_references":4,"max_page_axis_points":1000,"max_pages":2}}"#.to_vec();
    reordered.push(b'\n');

    for bytes in [no_lf, trailing, crlf, duplicate, unknown, reordered] {
        assert!(decode_request_v1(&bytes).is_err(), "accepted {bytes:?}");
    }
}

#[test]
fn protocol_response_round_trips_rejection_and_enforces_output_cap() {
    let response = PdfResponseV1 {
        protocol: PROTOCOL_VERSION.to_owned(),
        input_sha256: request().input_sha256,
        byte_length: 9,
        outcome: PdfGuestOutcomeV1::Rejected {
            reason: PdfGuestReasonV1::BadMagic,
        },
    };
    let encoded = encode_response_v1(&response, 4096).expect("response encodes");
    assert_eq!(
        decode_response_v1(&encoded, 4096).expect("response decodes"),
        response
    );
    assert!(encode_response_v1(&response, encoded.len() - 1).is_err());
    assert!(decode_response_v1(&encoded, encoded.len() - 1).is_err());
}

#[test]
fn fixture_contract_is_reproducible_and_contains_no_project_content() {
    let bytes = heleos_test_fixtures::pdf_two_pages();
    assert!(bytes.windows(5).any(|window| window == b"%PDF-"));
    assert_ne!(
        bytes,
        heleos_test_fixtures::pdf_two_pages_metadata_variant()
    );
    assert_eq!(
        Sha256Digest::hash_reader(Cursor::new(&bytes)).expect("fixture hashes"),
        Sha256Digest::hash_reader(Cursor::new(heleos_test_fixtures::pdf_two_pages()))
            .expect("fixture reproduces")
    );
    assert!(!String::from_utf8_lossy(&bytes).contains("Ignore previous instructions"));
}

#[test]
fn fixture_vector_hashes_are_frozen() {
    let mut vectors = vec![
        ("pdf_two_pages", heleos_test_fixtures::pdf_two_pages()),
        (
            "pdf_two_pages_metadata_variant",
            heleos_test_fixtures::pdf_two_pages_metadata_variant(),
        ),
        ("pdf_bad_magic", heleos_test_fixtures::pdf_bad_magic()),
        (
            "pdf_corrupt_truncated",
            heleos_test_fixtures::pdf_corrupt_truncated(),
        ),
        (
            "pdf_encrypted_password",
            heleos_test_fixtures::pdf_encrypted_password(),
        ),
        (
            "pdf_encrypted_empty_password",
            heleos_test_fixtures::pdf_encrypted_empty_password(),
        ),
        (
            "pdf_all_rotations",
            heleos_test_fixtures::pdf_all_rotations(),
        ),
        ("pdf_page_count(2)", heleos_test_fixtures::pdf_page_count(2)),
        ("pdf_page_count(3)", heleos_test_fixtures::pdf_page_count(3)),
        (
            "pdf_ten_thousand_pages",
            heleos_test_fixtures::pdf_ten_thousand_pages(),
        ),
    ];
    for feature in [
        PdfActiveFeatureV1::OpenAction,
        PdfActiveFeatureV1::AdditionalActions,
        PdfActiveFeatureV1::JavaScriptAbbreviation,
        PdfActiveFeatureV1::JavaScript,
        PdfActiveFeatureV1::Launch,
        PdfActiveFeatureV1::Uri,
        PdfActiveFeatureV1::GoToRemote,
        PdfActiveFeatureV1::SubmitForm,
        PdfActiveFeatureV1::ImportData,
        PdfActiveFeatureV1::RichMedia,
        PdfActiveFeatureV1::EmbeddedFiles,
        PdfActiveFeatureV1::AssociatedFiles,
        PdfActiveFeatureV1::Xfa,
        PdfActiveFeatureV1::AcroForm,
    ] {
        vectors.push((
            match feature {
                PdfActiveFeatureV1::OpenAction => "pdf_active_feature(open_action)",
                PdfActiveFeatureV1::AdditionalActions => "pdf_active_feature(additional_actions)",
                PdfActiveFeatureV1::JavaScriptAbbreviation => {
                    "pdf_active_feature(java_script_abbreviation)"
                }
                PdfActiveFeatureV1::JavaScript => "pdf_active_feature(java_script)",
                PdfActiveFeatureV1::Launch => "pdf_active_feature(launch)",
                PdfActiveFeatureV1::Uri => "pdf_active_feature(uri)",
                PdfActiveFeatureV1::GoToRemote => "pdf_active_feature(go_to_remote)",
                PdfActiveFeatureV1::SubmitForm => "pdf_active_feature(submit_form)",
                PdfActiveFeatureV1::ImportData => "pdf_active_feature(import_data)",
                PdfActiveFeatureV1::RichMedia => "pdf_active_feature(rich_media)",
                PdfActiveFeatureV1::EmbeddedFiles => "pdf_active_feature(embedded_files)",
                PdfActiveFeatureV1::AssociatedFiles => "pdf_active_feature(associated_files)",
                PdfActiveFeatureV1::Xfa => "pdf_active_feature(xfa)",
                PdfActiveFeatureV1::AcroForm => "pdf_active_feature(acro_form)",
            },
            heleos_test_fixtures::pdf_active_feature(feature),
        ));
    }
    vectors.extend([
        (
            "pdf_encoded_javascript_name",
            heleos_test_fixtures::pdf_encoded_javascript_name(),
        ),
        (
            "pdf_active_feature_object_stream",
            heleos_test_fixtures::pdf_active_feature_object_stream(),
        ),
        (
            "pdf_harmless_feature_words",
            heleos_test_fixtures::pdf_harmless_feature_words(),
        ),
        (
            "pdf_prompt_injection",
            heleos_test_fixtures::pdf_prompt_injection(),
        ),
        (
            "pdf_metadata_bytes(512)",
            heleos_test_fixtures::pdf_metadata_bytes(512),
        ),
        (
            "pdf_name_bytes(512)",
            heleos_test_fixtures::pdf_name_bytes(512),
        ),
        (
            "pdf_escaped_string_bytes(512)",
            heleos_test_fixtures::pdf_escaped_string_bytes(512),
        ),
        (
            "pdf_escaped_name_bytes(512)",
            heleos_test_fixtures::pdf_escaped_name_bytes(512),
        ),
        (
            "pdf_nested_dictionaries(8)",
            heleos_test_fixtures::pdf_nested_dictionaries(8),
        ),
        (
            "pdf_object_count(6)",
            heleos_test_fixtures::pdf_object_count(6),
        ),
        (
            "pdf_reference_depth(3)",
            heleos_test_fixtures::pdf_reference_depth(3),
        ),
        (
            "pdf_fractional_nonzero_boxes",
            heleos_test_fixtures::pdf_fractional_nonzero_boxes(),
        ),
        (
            "pdf_malformed_media_box",
            heleos_test_fixtures::pdf_malformed_media_box(),
        ),
        (
            "pdf_missing_media_box",
            heleos_test_fixtures::pdf_missing_media_box(),
        ),
        (
            "pdf_non_finite_media_box",
            heleos_test_fixtures::pdf_non_finite_media_box(),
        ),
        (
            "pdf_single_page_geometry(reversed)",
            heleos_test_fixtures::pdf_single_page_geometry("10 0 1 10", None, None, None),
        ),
        (
            "pdf_single_page_geometry(user_unit_one)",
            heleos_test_fixtures::pdf_single_page_geometry("0 0 10 10", None, None, Some("1")),
        ),
        (
            "pdf_single_page_geometry(user_unit_two)",
            heleos_test_fixtures::pdf_single_page_geometry("0 0 10 10", None, None, Some("2")),
        ),
        (
            "pdf_single_page_geometry(user_unit_malformed)",
            heleos_test_fixtures::pdf_single_page_geometry("0 0 10 10", None, None, Some("(bad)")),
        ),
        (
            "pdf_page_tree_count_mismatch",
            heleos_test_fixtures::pdf_page_tree_count_mismatch(),
        ),
        (
            "pdf_page_tree_repeated_child",
            heleos_test_fixtures::pdf_page_tree_repeated_child(),
        ),
        (
            "pdf_page_tree_cycle",
            heleos_test_fixtures::pdf_page_tree_cycle(),
        ),
        (
            "pdf_page_tree_missing_child",
            heleos_test_fixtures::pdf_page_tree_missing_child(),
        ),
        (
            "pdf_page_tree_wrong_type",
            heleos_test_fixtures::pdf_page_tree_wrong_type(),
        ),
        (
            "pdf_page_tree_parent_mismatch",
            heleos_test_fixtures::pdf_page_tree_parent_mismatch(),
        ),
        (
            "pdf_page_tree_scalar_child",
            heleos_test_fixtures::pdf_page_tree_scalar_child(),
        ),
        (
            "pdf_input_payload_bytes(37)",
            heleos_test_fixtures::pdf_input_payload_bytes(37),
        ),
        (
            "pdf_xref_stream_decompression_bomb(20000)",
            heleos_test_fixtures::pdf_xref_stream_decompression_bomb(20_000),
        ),
        (
            "pdf_object_stream_decompression_bomb(20000)",
            heleos_test_fixtures::pdf_object_stream_decompression_bomb(20_000),
        ),
    ]);
    let actual = vectors
        .into_iter()
        .map(|(name, bytes)| {
            (
                name,
                Sha256Digest::hash_reader(Cursor::new(bytes))
                    .expect("fixture vector hashes")
                    .to_string(),
            )
        })
        .collect::<Vec<_>>();
    let expected = [
        (
            "pdf_two_pages",
            "1cfaddb891bec4532e7d21bc861b6a78f72c8eb58c4b4fbf2c51023faf08f12a",
        ),
        (
            "pdf_two_pages_metadata_variant",
            "8df595fe0a31fd5dac0e9d19cf787bb180a9980fb0a3e9538ffff0ef48d1f4c9",
        ),
        (
            "pdf_bad_magic",
            "bff376318cad9bd34124cd250dfbfdc833ee76769dd319b101061563436c2d0d",
        ),
        (
            "pdf_corrupt_truncated",
            "76ad4814c09e5a0ccdfd119b7a1d91a828bedbbea53a1d0ef7e747578c8621ff",
        ),
        (
            "pdf_encrypted_password",
            "9607f6d1b984ba02c79404966a493d5384717f8683fb1c04c951ee1dd1f4b4cc",
        ),
        (
            "pdf_encrypted_empty_password",
            "e8219804fe4af14c0f3891defbcef7cc92141be4e7977cadfc596a0646669fdd",
        ),
        (
            "pdf_all_rotations",
            "1294cb3f017c7bc6d58412d625c30017c40e007d47c0a9e31cc7ca21a56b8799",
        ),
        (
            "pdf_page_count(2)",
            "ad53e2ec9fed18a823ae2f6059e1c705675fd2851d2bd9ebe0a75296fb36e5ac",
        ),
        (
            "pdf_page_count(3)",
            "61e37267fee164185b54dd501e09eb2992fac3a14cd6cea664cc1a3136ac8f74",
        ),
        (
            "pdf_ten_thousand_pages",
            "aa762961b059d4861f58280251342543c060ae089258bc3a8f5effb7530d7ffe",
        ),
        (
            "pdf_active_feature(open_action)",
            "c84201bf36540cd2034851b9709587849f1cc481fd85deba4fe6f5f89fac562c",
        ),
        (
            "pdf_active_feature(additional_actions)",
            "d8ea51dc0abb9d0926a9fe146008a4f1dd62a1e70351f433c45730bb5aae9db7",
        ),
        (
            "pdf_active_feature(java_script_abbreviation)",
            "390680b7f32d3cbf6df2ff07958baf11c849c8585b1fd2c9e830d3c5c0b2acfa",
        ),
        (
            "pdf_active_feature(java_script)",
            "b399c95e3b606d7f751f4b25577633a754b0736df3a08a77a82f8bdca82c1c3a",
        ),
        (
            "pdf_active_feature(launch)",
            "12e3f3d2353274780401143499400bed61f99a2e651f3e791107688213e5ce4b",
        ),
        (
            "pdf_active_feature(uri)",
            "a35bb929702ce63fe7c78225064530b9cf90187091d25d10a922f215b0c7bd36",
        ),
        (
            "pdf_active_feature(go_to_remote)",
            "b95092246e83448f2db9e0121481cf5c3563ec9772d4227dc3783af19780cb73",
        ),
        (
            "pdf_active_feature(submit_form)",
            "cebb9280f800e7cfe6e644134373f624b70352d2178ae62e82a0f78916e12343",
        ),
        (
            "pdf_active_feature(import_data)",
            "866530effd3f90edab2391b1a28bd96b80161aa94cc9d056e72c3d2d4aa96cf6",
        ),
        (
            "pdf_active_feature(rich_media)",
            "67b22f5799cf7509c1b8d120dbe82fc60f4021243e4e5836a97c552e8eb81a61",
        ),
        (
            "pdf_active_feature(embedded_files)",
            "dba6f7c5e82b81f31e544f5bc094471b107dcb06768dbb740569230247a20c24",
        ),
        (
            "pdf_active_feature(associated_files)",
            "123317a087caa00bd180673f4f7b502f603804e20da2b665ba102b157ba480d6",
        ),
        (
            "pdf_active_feature(xfa)",
            "381260d73fdcd134c2f8690980973f16e653c108d7897a08d89e6709c980c743",
        ),
        (
            "pdf_active_feature(acro_form)",
            "550cf29dfce92f5aaa2d6dad2232fec3193fb6fba8194a834ff79c60225d05e6",
        ),
        (
            "pdf_encoded_javascript_name",
            "e88ac86244bce493b5fe968043528517e343341ee39ebd0d9db5ce0f9118ac77",
        ),
        (
            "pdf_active_feature_object_stream",
            "107438f5be59675d63bf40da08a3875c6ab7f665f97aff3f903f38839be0d086",
        ),
        (
            "pdf_harmless_feature_words",
            "e4d884df16d9593b3cd7dfbd138b1f507cea353b35b2ed980a25750d4bda3af8",
        ),
        (
            "pdf_prompt_injection",
            "ac78045a91747805f9d35ffc7f7c296c0d15876fe353148b8684fb5c667b12c9",
        ),
        (
            "pdf_metadata_bytes(512)",
            "f3d76c6cc80b883819ef35bcb26862cd93e5b305677a07ae9a18e5d266832086",
        ),
        (
            "pdf_name_bytes(512)",
            "cbeca6d81202e310cf153957cb2b1da54722dd3affad5d573f9299c03ffacd9a",
        ),
        (
            "pdf_escaped_string_bytes(512)",
            "e83c74b0501b938204b1119c18ea7c1432277ad39680742312642dfef6a6c048",
        ),
        (
            "pdf_escaped_name_bytes(512)",
            "a1d9de1496f38932ab2baf551461c41a346929cecfbf4fe2ffcb5cd3e204b516",
        ),
        (
            "pdf_nested_dictionaries(8)",
            "49acff2782ede4842eeb0890fdf2a24570c6da40ab76ebd72b64010b842c9915",
        ),
        (
            "pdf_object_count(6)",
            "3a9c96b92b1631dd9c578806f24a23a08057775ae641f202def5f76185d2473c",
        ),
        (
            "pdf_reference_depth(3)",
            "6bfebeca2cff079b611c31b3752083ae2da830f0f0ae87cff741f24d8c96d1c7",
        ),
        (
            "pdf_fractional_nonzero_boxes",
            "b792f479c5ba5dd35f7ab056a30bf6b0b41e602870d91fae8e53a3114bd8b3ff",
        ),
        (
            "pdf_malformed_media_box",
            "06e09c8965f82135a99950f278cbca48ddaa2a5cd869c00bcfbf8da7745fab45",
        ),
        (
            "pdf_missing_media_box",
            "e9882ac45bfe388354115ff479fa0ce48124c45e757d756fe60ab7340d9a0522",
        ),
        (
            "pdf_non_finite_media_box",
            "03c4ab41a5099592ecfd321bf9b983d9611a7995b1bf3cab00fc67fc38c06a0b",
        ),
        (
            "pdf_single_page_geometry(reversed)",
            "11e77ae0dd7dd4e6ae137b15fb9fd7eda40c3d663c5c502f6836ac7a7b8091cb",
        ),
        (
            "pdf_single_page_geometry(user_unit_one)",
            "852d6efc8018322b910c611c904beb2caba2dd4adcfea376b00d2fa452e7f071",
        ),
        (
            "pdf_single_page_geometry(user_unit_two)",
            "9bf10c41504792af8bc40ed6dcf9b5a21ae754325d1aa59e3eed0a88649db7f5",
        ),
        (
            "pdf_single_page_geometry(user_unit_malformed)",
            "785996e6d35cd96c7f1dc4de597a59476158f83721cf15943c94499867211ebb",
        ),
        (
            "pdf_page_tree_count_mismatch",
            "e57d0207589de77ce567a903ace6c71dbb0c13c7e24ba68dcfc86e6690021de8",
        ),
        (
            "pdf_page_tree_repeated_child",
            "e7efff28f44a6d18747db959fc89d14494545f89606d2f371c79f2686623ca7a",
        ),
        (
            "pdf_page_tree_cycle",
            "ae8a804d590d23b400f3a00603b73f07612046e78d26963633f31b9454758bb3",
        ),
        (
            "pdf_page_tree_missing_child",
            "209e5d48a561520a644859b44e0ed2a68b23baddb682dff114970c37b69d8338",
        ),
        (
            "pdf_page_tree_wrong_type",
            "99cc5b1d0073ce56b2912e61c87c73195f07dbb8e979e103bfe9e1d9146627ee",
        ),
        (
            "pdf_page_tree_parent_mismatch",
            "40f6941bfa3c76872a985a2296daebe16857e451ded902183b4229a30dc29be6",
        ),
        (
            "pdf_page_tree_scalar_child",
            "e5d78b9b699aec0b123917c72bf243b345c92bde8bca10868b9a24ef06e7ae52",
        ),
        (
            "pdf_input_payload_bytes(37)",
            "7b77652f79b896515555d2f10b23a8d03465787a37f8c6ff3f7cc01242cf289c",
        ),
        (
            "pdf_xref_stream_decompression_bomb(20000)",
            "5cc59e9ea39b82a82dbc68d74ebcb45cb9496d45f0a62364cdd3eb9b2bfc95ea",
        ),
        (
            "pdf_object_stream_decompression_bomb(20000)",
            "772fdbf0238e0d5f9e92f181ef95bfba0e147446edd2854cd024cc27aebb1557",
        ),
    ];
    assert_eq!(actual.len(), expected.len());
    for ((actual_name, actual_digest), (expected_name, expected_digest)) in
        actual.iter().zip(expected)
    {
        assert_eq!(actual_name, &expected_name);
        assert_eq!(
            actual_digest, expected_digest,
            "fixture drift: {actual_name}"
        );
    }
}

#[test]
fn tracked_guest_source_allowlist_has_exact_lf_attributes_and_bytes() {
    let workspace = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .expect("canonicalize source workspace");
    let source = validate_approved_guest_source(workspace.clone())
        .expect("validate approved source allowlist");
    let mut expected_paths = source
        .spec
        .files
        .iter()
        .filter(|file| file.source_tree_member)
        .map(|file| file.relative_path)
        .collect::<Vec<_>>();
    expected_paths.sort();
    assert_eq!(expected_paths.len(), 6);

    let attributes = read_bounded_regular_nofollow(&workspace.join(".gitattributes"), 16 * 1024)
        .expect("read exact attributes file");
    assert!(!attributes.contains(&b'\r'));
    assert_eq!(attributes.last(), Some(&b'\n'));
    let mut actual_lines = std::str::from_utf8(&attributes)
        .expect("attributes are UTF-8")
        .lines()
        .map(str::to_owned)
        .collect::<Vec<_>>();
    actual_lines.sort();
    let expected_lines = expected_paths
        .iter()
        .map(|path| format!("/{path} text eol=lf"))
        .collect::<Vec<_>>();
    assert_eq!(actual_lines, expected_lines);
    for file in &source.files {
        if source
            .spec
            .files
            .iter()
            .find(|expected| expected.relative_path == file.path)
            .is_some_and(|expected| expected.source_tree_member)
        {
            assert!(
                !file.content.contains(&b'\r'),
                "source-tree member contains CR: {}",
                file.path
            );
        }
    }

    let mut command = Command::new("git");
    command
        .current_dir(&workspace)
        .args(["-c", "core.attributesFile=", "check-attr", "--all", "--"])
        .args(&expected_paths)
        .env_clear();
    let output = command.output().expect("run git check-attr");
    assert!(output.status.success(), "git check-attr must succeed");
    assert!(output.stderr.is_empty(), "git check-attr must be quiet");
    let output = String::from_utf8(output.stdout).expect("git check-attr output is UTF-8");
    let mut actual = output.lines().map(str::to_owned).collect::<Vec<_>>();
    actual.sort();
    let mut expected = expected_paths
        .iter()
        .flat_map(|path| [format!("{path}: eol: lf"), format!("{path}: text: set")])
        .collect::<Vec<_>>();
    expected.sort();
    assert_eq!(actual, expected);
}

#[test]
fn tracked_guest_build_is_reproducible_across_distinct_roots() {
    let scratch = tempfile::tempdir().expect("create private launcher scratch");
    let result = run_tracked_guest_build(scratch.path());
    let cleanup = scratch.close();
    assert!(cleanup.is_ok(), "launcher scratch cleanup must be explicit");
    if let Err(error) = result {
        panic!("{error}");
    }
}

#[test]
fn tracked_guest_executes_public_two_page_ten_thousand_page_and_rejection_paths() {
    let Ok(path) = std::env::var("HELEOS_PDF_GUEST") else {
        return;
    };
    let supplied = PathBuf::from(path);
    let supplied = if supplied.is_absolute() {
        supplied
    } else {
        Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../..")
            .join(supplied)
    };
    let supplied = supplied
        .canonicalize()
        .expect("canonicalize explicitly supplied tracked guest");
    let wasm = read_bounded_regular_nofollow(&supplied, MAX_GUEST_WASM_BYTES)
        .expect("read explicitly supplied tracked guest");
    let guest = ApprovedPdfGuest::load_tracked(&wasm).expect("load tracked guest trust root");

    let staging = tempfile::Builder::new()
        .prefix("heleos-pdf-e2e-staging-")
        .tempdir()
        .expect("create E2E staging parent");
    heleos_core::apply_private_permissions(staging.path()).expect("harden E2E staging parent");
    let staging_path = staging.path().canonicalize().expect("canonicalize staging");
    let probe = WasiPdfProbe::new(
        guest,
        PdfSandboxConfig {
            staging_parent: staging_path.clone(),
        },
    )
    .expect("construct tracked guest probe");

    let vault_parent = tempfile::Builder::new()
        .prefix("heleos-pdf-e2e-vault-")
        .tempdir()
        .expect("create E2E vault parent");
    heleos_core::apply_private_permissions(vault_parent.path()).expect("harden E2E vault parent");
    let vault_root = vault_parent
        .path()
        .canonicalize()
        .expect("canonicalize vault parent")
        .join("vault");
    let vault = Vault::open(VaultConfig {
        root: vault_root,
        open_mode: VaultOpenMode::CreateNew,
    })
    .expect("create E2E vault");

    for (bytes, expected_pages, expected_reason) in [
        (heleos_test_fixtures::pdf_two_pages(), Some(2), None),
        (
            heleos_test_fixtures::pdf_ten_thousand_pages(),
            Some(10_000),
            None,
        ),
        (
            heleos_test_fixtures::pdf_bad_magic(),
            None,
            Some(PdfQuarantineReason::BadMagic),
        ),
    ] {
        let length = u64::try_from(bytes.len()).expect("fixture length fits u64");
        let budget = VaultWriteBudget::new(length, length).expect("construct E2E vault budget");
        let stored = match vault
            .put_reader(Cursor::new(&bytes), budget)
            .expect("store E2E fixture")
        {
            PutOutcome::Stored(stored) => stored,
            PutOutcome::QuotaRejected { .. } => panic!("E2E fixture was unexpectedly rejected"),
        };
        let verified = vault
            .open_verified(stored.digest)
            .expect("open E2E verified object");
        let outcome = probe
            .probe(
                verified,
                RevisionId::from(stored.digest),
                PdfLimits::default(),
            )
            .expect("execute tracked guest at public boundary");
        match (outcome, expected_pages, expected_reason) {
            (PdfProbeOutcome::Accepted(inspection), Some(expected), None) => {
                assert_eq!(inspection.pages.len(), expected);
            }
            (PdfProbeOutcome::Quarantined(quarantine), None, Some(expected)) => {
                assert_eq!(quarantine.reason, expected);
            }
            (unexpected, _, _) => panic!("unexpected tracked-guest result: {unexpected:?}"),
        }
        assert_eq!(
            fs::read_dir(&staging_path)
                .expect("read E2E staging parent")
                .count(),
            0,
            "tracked guest must explicitly clean staging after every outcome"
        );
    }

    drop(probe);
    drop(vault);
    assert!(
        vault_parent.close().is_ok(),
        "E2E vault cleanup must be explicit"
    );
    assert!(
        staging.close().is_ok(),
        "E2E staging cleanup must be explicit"
    );
}

#[cfg(unix)]
#[test]
fn bounded_subprocess_timeout_terminates_pipe_holding_descendants() {
    let scratch = tempfile::tempdir().expect("create process-tree scratch");
    let pid_file = scratch.path().join("descendant.pid");
    let mut command = Command::new("/bin/sh");
    command.args([
        "-c",
        "trap '' TERM; (trap '' TERM; /bin/sleep 3) & echo $! > \"$1\"; wait",
        "heleos-process-tree-test",
    ]);
    command.arg(&pid_file);
    let started = Instant::now();
    let result = run_bounded(&mut command, Duration::from_millis(50));
    assert!(
        result
            .as_ref()
            .is_err_and(|error| error == "bounded subprocess exceeded its deadline"),
        "timeout must retain its typed launcher result: {result:?}"
    );
    assert!(
        started.elapsed() < Duration::from_secs(2),
        "a descendant retaining the pipes must not extend the deadline"
    );
    let cleanup = scratch.close();
    assert!(cleanup.is_ok(), "process-tree scratch cleanup must succeed");
}

#[cfg(windows)]
#[test]
fn bounded_subprocess_timeout_terminates_windows_pipe_holding_descendants() {
    let system_root = PathBuf::from(std::env::var_os("SystemRoot").expect("SystemRoot is set"))
        .canonicalize()
        .expect("canonicalize SystemRoot");
    let system32 = system_root.join("System32");
    let mut command = Command::new(system32.join("cmd.exe"));
    command
        .args([
            "/D",
            "/S",
            "/C",
            "start \"\" /B ping.exe -n 4 127.0.0.1 & ping.exe -n 4 127.0.0.1",
        ])
        .env_clear()
        .env("SystemRoot", &system_root)
        .env("WINDIR", &system_root)
        .env("ComSpec", system32.join("cmd.exe"))
        .env("PATHEXT", ".COM;.EXE;.BAT;.CMD")
        .env("PATH", &system32)
        .current_dir(&system32);
    let started = Instant::now();
    let result = run_bounded_with_options(
        &mut command,
        Duration::from_millis(50),
        PROCESS_STREAM_CAP,
        Some(&system_root),
    );
    assert!(
        result
            .as_ref()
            .is_err_and(|error| error == "bounded subprocess exceeded its deadline"),
        "Windows timeout must retain its typed launcher result: {result:?}"
    );
    assert!(
        started.elapsed() < Duration::from_secs(2),
        "Windows descendants retaining pipes must not extend the deadline"
    );
}

#[cfg(unix)]
#[test]
fn bounded_subprocess_overflow_terminates_pipe_holding_descendants() {
    let mut command = Command::new("/bin/sh");
    command.args([
        "-c",
        "trap '' TERM; (trap '' TERM; /bin/sleep 3) & printf '%0128d' 0; wait",
    ]);
    let started = Instant::now();
    let result = run_bounded_with_stream_cap(&mut command, Duration::from_secs(2), 64);
    assert!(
        result
            .as_ref()
            .is_err_and(|error| error == "bounded subprocess exceeded its stream cap"),
        "overflow must retain its typed launcher result: {result:?}"
    );
    assert!(
        started.elapsed() < Duration::from_secs(2),
        "a descendant retaining the pipes must not extend overflow cancellation"
    );
}

#[test]
fn artifact_publication_is_no_replace_and_cleans_every_error_path() {
    let candidate = b"validated-artifact-candidate";

    let wrong = tempfile::tempdir().expect("create wrong-winner publication root");
    let wrong_path = wrong
        .path()
        .canonicalize()
        .expect("canonicalize wrong root");
    write_new_synced(
        &wrong_path.join("heleos_pdf_guest.wasm"),
        b"wrong-existing-winner",
    )
    .expect("create wrong existing winner");
    let wrong_dir = cap_std::fs::Dir::open_ambient_dir(&wrong_path, cap_std::ambient_authority())
        .expect("open wrong-winner capability");
    let wrong_result = publish_candidate_no_replace(
        &wrong_path,
        &wrong_dir,
        candidate,
        PublicationFaults::default(),
        |_| Ok(()),
    );
    assert_eq!(
        wrong_result,
        Err("existing generated artifact differs and was left untouched".to_owned())
    );
    assert_eq!(
        fs::read(wrong_path.join("heleos_pdf_guest.wasm")).expect("read preserved winner"),
        b"wrong-existing-winner"
    );
    assert_eq!(
        fs::read_dir(&wrong_path)
            .expect("read wrong-winner directory")
            .count(),
        1
    );
    drop(wrong_dir);
    assert!(wrong.close().is_ok(), "wrong-winner root cleans up");

    let post_link = tempfile::tempdir().expect("create post-link publication root");
    let post_link_path = post_link
        .path()
        .canonicalize()
        .expect("canonicalize post-link root");
    let post_link_dir =
        cap_std::fs::Dir::open_ambient_dir(&post_link_path, cap_std::ambient_authority())
            .expect("open post-link capability");
    let post_link_result = publish_candidate_no_replace(
        &post_link_path,
        &post_link_dir,
        candidate,
        PublicationFaults {
            fail_after_link: true,
            fail_cleanup: false,
            substitute_after_link: false,
        },
        |_| Ok(()),
    );
    assert_eq!(
        post_link_result,
        Err("injected generated artifact post-link failure".to_owned())
    );
    assert_eq!(
        fs::read(post_link_path.join("heleos_pdf_guest.wasm"))
            .expect("read post-link published winner"),
        candidate
    );
    assert_eq!(
        fs::read_dir(&post_link_path)
            .expect("read post-link directory")
            .count(),
        1,
        "post-link failure must remove its staging link"
    );
    publish_candidate_no_replace(
        &post_link_path,
        &post_link_dir,
        candidate,
        PublicationFaults::default(),
        |_| Ok(()),
    )
    .expect("an existing equal winner is accepted without replacement");
    drop(post_link_dir);
    assert!(post_link.close().is_ok(), "post-link root cleans up");

    let cleanup = tempfile::tempdir().expect("create cleanup-fault publication root");
    let cleanup_path = cleanup
        .path()
        .canonicalize()
        .expect("canonicalize cleanup-fault root");
    let cleanup_dir =
        cap_std::fs::Dir::open_ambient_dir(&cleanup_path, cap_std::ambient_authority())
            .expect("open cleanup-fault capability");
    let cleanup_result = publish_candidate_no_replace(
        &cleanup_path,
        &cleanup_dir,
        candidate,
        PublicationFaults {
            fail_after_link: true,
            fail_cleanup: true,
            substitute_after_link: false,
        },
        |_| Ok(()),
    );
    assert_eq!(
        cleanup_result,
        Err("generated artifact error-path cleanup failed".to_owned()),
        "cleanup uncertainty overrides the post-link result"
    );
    let mut names = fs::read_dir(&cleanup_path)
        .expect("read cleanup-fault directory")
        .map(|entry| entry.expect("read cleanup-fault entry").file_name())
        .collect::<Vec<_>>();
    names.sort();
    assert_eq!(
        names.len(),
        2,
        "the injected fault retains one staging link"
    );
    for name in names {
        if name != "heleos_pdf_guest.wasm" {
            cleanup_dir
                .remove_file(name)
                .expect("test removes injected retained staging link");
        }
    }
    cleanup_dir
        .remove_file("heleos_pdf_guest.wasm")
        .expect("test removes injected published winner");
    drop(cleanup_dir);
    assert!(cleanup.close().is_ok(), "cleanup-fault root cleans up");
}

#[cfg(unix)]
#[test]
fn artifact_publication_rejects_post_link_namespace_substitution() {
    let parent = tempfile::tempdir().expect("create substitution publication parent");
    let parent_path = parent.path().canonicalize().expect("canonicalize parent");
    let destination = parent_path.join("publication");
    fs::create_dir(&destination).expect("create publication destination");
    let directory = cap_std::fs::Dir::open_ambient_dir(&destination, cap_std::ambient_authority())
        .expect("open publication capability");
    let candidate = b"namespace-substitution-candidate";
    let result = publish_candidate_no_replace(
        &destination,
        &directory,
        candidate,
        PublicationFaults {
            fail_after_link: false,
            fail_cleanup: false,
            substitute_after_link: true,
        },
        |_| Ok(()),
    );
    assert!(
        result.is_err(),
        "substituted publication namespace must fail"
    );
    assert_eq!(
        fs::read_dir(&destination)
            .expect("read replacement namespace")
            .count(),
        0,
        "the replacement namespace must not receive the artifact"
    );
    let moved = destination.with_extension("substituted-original");
    assert_eq!(
        fs::read(moved.join("heleos_pdf_guest.wasm")).expect("read retained original winner"),
        candidate
    );
    assert_eq!(
        fs::read_dir(&moved)
            .expect("read retained original namespace")
            .count(),
        1,
        "staging must be removed from the retained original namespace"
    );
    drop(directory);
    fs::remove_dir_all(&moved).expect("remove retained original namespace");
    fs::remove_dir(&destination).expect("remove replacement namespace");
    assert!(parent.close().is_ok(), "substitution parent cleans up");
}

#[test]
fn supplemental_seed_snapshot_binds_content_and_rejects_root_config() {
    let scratch = tempfile::tempdir().expect("create seed-snapshot scratch");
    let root = scratch.path().canonicalize().expect("canonicalize scratch");
    for relative in ["registry/cache", "registry/index", "registry/src"] {
        fs::create_dir_all(root.join(relative)).expect("create seed snapshot subtree");
    }
    for (relative, bytes) in [
        ("registry/cache/archive.crate", b"archive".as_slice()),
        ("registry/index/config.json", b"config".as_slice()),
        ("registry/src/package.rs", b"source!".as_slice()),
    ] {
        write_new_synced(&root.join(relative), bytes).expect("write seed snapshot input");
    }
    let before = snapshot_seed_cargo_inputs(&root).expect("capture first seed snapshot");
    let mut changed = OpenOptions::new()
        .write(true)
        .truncate(true)
        .open(root.join("registry/src/package.rs"))
        .expect("open seed input for same-length mutation");
    changed
        .write_all(b"mutate!")
        .and_then(|_| changed.sync_all())
        .expect("mutate seed snapshot input");
    drop(changed);
    let after = snapshot_seed_cargo_inputs(&root).expect("capture changed seed snapshot");
    assert_ne!(
        before, after,
        "same-length content mutation must be detected"
    );

    write_new_synced(&root.join("config.toml"), b"[net]\noffline = false\n")
        .expect("create forbidden root Cargo config");
    assert_eq!(
        snapshot_seed_cargo_inputs(&root),
        Err("seed Cargo root config appeared during snapshot".to_owned())
    );
    assert!(scratch.close().is_ok(), "seed snapshot scratch cleans up");
}

fn prepare_negative_cache_fixture(
    scratch: &Path,
) -> (
    ValidatedGuestSource,
    ToolchainInputs,
    GuestResolutionCachePlanV1,
    Vec<u8>,
) {
    reject_inherited_build_authority().expect("negative cache fixture rejects inherited authority");
    let scratch = scratch
        .canonicalize()
        .expect("canonicalize negative cache scratch");
    let source_workspace = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .expect("canonicalize negative cache source workspace");
    let source =
        validate_approved_guest_source(source_workspace).expect("validate negative cache source");
    let toolchain = discover_toolchain(&scratch).expect("discover negative cache toolchain");
    let target = create_canonical_directory(&scratch.join("seed-target"), "negative seed target")
        .expect("create negative seed target");
    let temp = create_canonical_directory(&target.join("tmp"), "negative seed temp")
        .expect("create negative seed temp");
    let before = snapshot_seed_cargo_inputs(&toolchain.seed_cargo_home)
        .expect("snapshot negative seed cache before resolution");
    let resolution_root =
        materialize_resolution_workspace(&scratch.join("seed-resolution"), &source)
            .expect("materialize negative seed resolution");
    let resolution = normalize_resolution_workspace(
        resolution_root,
        &source,
        &toolchain,
        &toolchain.seed_cargo_home,
        &target,
        &temp,
    )
    .expect("normalize negative seed resolution");
    let after = snapshot_seed_cargo_inputs(&toolchain.seed_cargo_home)
        .expect("snapshot negative seed cache after resolution");
    assert_eq!(
        before, after,
        "negative seed resolution mutates seed inputs"
    );
    let plan = guest_resolution_cache_plan_v1(&source.production_lock, &resolution.pruned_lock)
        .expect("derive negative cache plan");
    assert_eq!(
        plan.resolution_archives.len(),
        FROZEN_RESOLUTION_ARCHIVE_COUNT
    );
    (source, toolchain, plan, resolution.pruned_lock)
}

fn assert_missing_cache_input_fails_offline(
    base: &Path,
    source: &ValidatedGuestSource,
    toolchain: &ToolchainInputs,
    plan: &GuestResolutionCachePlanV1,
    missing_relative: &str,
) {
    let cargo_home =
        create_canonical_directory(&base.join("cargo-home"), "negative isolated Cargo home")
            .expect("create negative isolated Cargo home");
    materialize_verified_cache(&toolchain.seed_cargo_home, &cargo_home, plan)
        .expect("materialize negative isolated cache");
    fs::remove_file(cargo_home.join(missing_relative)).expect("remove exact planned cache input");

    let target = create_canonical_directory(&base.join("target"), "negative resolution target")
        .expect("create negative resolution target");
    let temp = create_canonical_directory(&target.join("tmp"), "negative resolution temp")
        .expect("create negative resolution temp");
    let resolution = materialize_resolution_workspace(&base.join("resolution"), source)
        .expect("materialize negative resolution workspace");
    let result =
        project_resolution_workspace(resolution, source, toolchain, &cargo_home, &target, &temp);
    assert!(
        result.is_err(),
        "offline Cargo unexpectedly resolved with missing planned input {missing_relative}"
    );
}

#[test]
fn inactive_cpufeatures_cache_inputs_are_required_and_unplanned_entries_fail_preflight() {
    let scratch = tempfile::tempdir().expect("create cache-negative scratch");
    let scratch_path = scratch.path().canonicalize().expect("canonicalize scratch");
    let (source, toolchain, plan, _pruned_lock) = prepare_negative_cache_fixture(&scratch_path);
    let cpufeatures_archive = plan
        .resolution_archives
        .iter()
        .find(|archive| {
            archive
                .normalized_dependency_id
                .starts_with("registry:cpufeatures@0.3.1#")
        })
        .expect("cache plan contains inactive cpufeatures archive")
        .cargo_home_relative_path
        .clone();
    let cpufeatures_index = plan
        .sparse_index_entries
        .iter()
        .find(|entry| entry.package_name == "cpufeatures")
        .expect("cache plan contains cpufeatures sparse index entry")
        .cargo_home_relative_path
        .clone();
    assert_missing_cache_input_fails_offline(
        &scratch_path.join("missing-archive"),
        &source,
        &toolchain,
        &plan,
        &cpufeatures_archive,
    );
    assert_missing_cache_input_fails_offline(
        &scratch_path.join("missing-index"),
        &source,
        &toolchain,
        &plan,
        &cpufeatures_index,
    );

    assert!(
        plan.resolution_archives.iter().all(|archive| !archive
            .normalized_dependency_id
            .starts_with("registry:bytes@")),
        "bytes must remain a full-workspace-only cache identity"
    );
    let injected_home = create_canonical_directory(
        &scratch_path.join("injected/cargo-home"),
        "injected isolated Cargo home",
    )
    .expect("create injected Cargo home");
    let snapshot = materialize_verified_cache(&toolchain.seed_cargo_home, &injected_home, &plan)
        .expect("materialize injected isolated cache");
    let archive_parent = Path::new(&plan.resolution_archives[0].cargo_home_relative_path)
        .parent()
        .expect("planned archive has a parent");
    let injected = archive_parent.join("bytes-1.12.1.crate");
    write_new_synced(&injected_home.join(&injected), b"unplanned-full-only")
        .expect("inject full-only cache entry");
    assert!(
        validate_pruned_cache(&injected_home, &plan, &snapshot, false).is_err(),
        "unplanned full-workspace-only cache entry passed preflight"
    );
    assert!(scratch.close().is_ok(), "cache-negative scratch cleans up");
}

#[test]
fn build_target_precondition_rejects_zero_extra_preexisting_and_rebound_temp_inventory() {
    let scratch = tempfile::tempdir().expect("create target-negative scratch");
    let root = scratch.path().canonicalize().expect("canonicalize scratch");

    let zero =
        create_canonical_directory(&root.join("zero"), "zero target").expect("create zero target");
    assert!(
        validate_build_target_precondition(&zero, &zero.join(".heleos-tmp")).is_err(),
        "target with zero temp children passed preflight"
    );

    let extra = create_canonical_directory(&root.join("extra"), "extra target")
        .expect("create extra target");
    let extra_temp = create_canonical_directory(&extra.join(".heleos-tmp"), "extra temp")
        .expect("create extra temp");
    write_new_synced(&extra.join("preexisting-output"), b"unexpected")
        .expect("create extra target entry");
    assert!(
        validate_build_target_precondition(&extra, &extra_temp).is_err(),
        "target with an extra preexisting entry passed preflight"
    );

    let populated = create_canonical_directory(&root.join("populated"), "populated target")
        .expect("create populated target");
    let populated_temp =
        create_canonical_directory(&populated.join(".heleos-tmp"), "populated temp")
            .expect("create populated temp");
    write_new_synced(&populated_temp.join("preexisting"), b"unexpected")
        .expect("populate build temp");
    assert!(
        validate_build_target_precondition(&populated, &populated_temp).is_err(),
        "nonempty preexisting temp passed preflight"
    );

    let rebound = create_canonical_directory(&root.join("rebound"), "rebound target")
        .expect("create rebound target");
    let rebound_temp = create_canonical_directory(&rebound.join(".heleos-tmp"), "rebound temp")
        .expect("create rebound temp");
    let identity = validate_build_target_precondition(&rebound, &rebound_temp)
        .expect("valid target precondition succeeds");
    fs::rename(&rebound_temp, rebound.join("old-temp")).expect("move original temp identity");
    fs::create_dir(&rebound_temp).expect("create rebound temp identity");
    assert!(
        validate_build_target_after_build(
            &rebound,
            &rebound_temp,
            identity,
            &rebound.join("wasm32-wasip1/release/heleos_pdf_guest.wasm"),
        )
        .is_err(),
        "rebound build temp identity passed post-build validation"
    );

    let whole_root = create_canonical_directory(&root.join("whole-root"), "whole target")
        .expect("create whole target");
    let whole_temp = create_canonical_directory(&whole_root.join(".heleos-tmp"), "whole temp")
        .expect("create whole temp");
    let whole_identity = validate_build_target_precondition(&whole_root, &whole_temp)
        .expect("valid whole-root target precondition succeeds");
    let retained_root = root.join("whole-root-retained");
    fs::rename(&whole_root, &retained_root).expect("retain original whole target identity");
    fs::create_dir(&whole_root).expect("create substituted whole target");
    fs::rename(
        retained_root.join(".heleos-tmp"),
        whole_root.join(".heleos-tmp"),
    )
    .expect("move original temp identity into substituted target");
    let substituted_artifact = whole_root.join("wasm32-wasip1/release/heleos_pdf_guest.wasm");
    fs::create_dir_all(
        substituted_artifact
            .parent()
            .expect("substituted artifact has a parent"),
    )
    .expect("create substituted artifact parent");
    write_new_synced(&substituted_artifact, b"otherwise-valid-candidate")
        .expect("write substituted artifact");
    assert!(
        validate_build_target_after_build(
            &whole_root,
            &whole_root.join(".heleos-tmp"),
            whole_identity,
            &substituted_artifact,
        )
        .is_err(),
        "substituted target root preserving the original temp identity passed post-build validation"
    );
    assert!(scratch.close().is_ok(), "target-negative scratch cleans up");
}

#[test]
fn build_candidate_read_remains_bound_to_the_checked_target_capability() {
    let scratch = tempfile::tempdir().expect("create candidate-read scratch");
    let root = scratch.path().canonicalize().expect("canonicalize scratch");
    let target = create_canonical_directory(&root.join("target"), "candidate target")
        .expect("create candidate target");
    let temp = create_canonical_directory(&target.join(".heleos-tmp"), "candidate temp")
        .expect("create candidate temp");
    let identity = validate_build_target_precondition(&target, &temp)
        .expect("candidate target precondition succeeds");
    let artifact = target.join("wasm32-wasip1/release/heleos_pdf_guest.wasm");
    fs::create_dir_all(artifact.parent().expect("candidate artifact has a parent"))
        .expect("create candidate artifact parent");
    let cargo_produced = b"cargo-produced-candidate";
    write_new_synced(&artifact, cargo_produced).expect("write Cargo-produced candidate");
    let retained = root.join("target-retained");
    let mut swapped = false;
    let result =
        validate_build_target_after_build_with_hook(&target, &temp, identity, &artifact, || {
            fs::rename(&target, &retained)
                .map_err(|_| "retain checked target root failed".to_owned())?;
            fs::create_dir_all(
                artifact
                    .parent()
                    .ok_or_else(|| "substituted candidate has no parent".to_owned())?,
            )
            .map_err(|_| "create substituted candidate parent failed".to_owned())?;
            write_new_synced(&artifact, b"attacker-substituted-candidate")?;
            swapped = true;
            Ok(())
        });
    assert!(swapped, "after-inventory substitution hook did not run");
    assert!(
        result.is_err(),
        "after-inventory target substitution passed retained-capability candidate validation"
    );
    assert_eq!(
        fs::read(retained.join("wasm32-wasip1/release/heleos_pdf_guest.wasm"))
            .expect("read retained Cargo-produced candidate"),
        cargo_produced,
    );
    assert_eq!(
        fs::read(&artifact).expect("read substituted ambient candidate"),
        b"attacker-substituted-candidate",
    );
    assert!(scratch.close().is_ok(), "candidate-read scratch cleans up");
}

fn make_test_executable(path: &Path, bytes: &[u8]) {
    write_new_synced(path, bytes).expect("write test executable");
    #[cfg(unix)]
    {
        use std::os::unix::fs::PermissionsExt;

        let mut permissions = fs::metadata(path)
            .expect("read test executable metadata")
            .permissions();
        permissions.set_mode(0o700);
        fs::set_permissions(path, permissions).expect("make test executable executable");
    }
}

#[test]
fn tool_proxy_identity_accepts_same_file_hardlinks() {
    let scratch = tempfile::tempdir().expect("create hardlink-proxy scratch");
    let root = scratch.path().canonicalize().expect("canonicalize scratch");
    let cargo = root.join(if cfg!(windows) { "cargo.exe" } else { "cargo" });
    let rustup = root.join(if cfg!(windows) {
        "rustup.exe"
    } else {
        "rustup"
    });
    let rustc = root.join(if cfg!(windows) { "rustc.exe" } else { "rustc" });
    make_test_executable(&cargo, b"same rustup proxy inode");
    fs::hard_link(&cargo, &rustup).expect("create same-file rustup hardlink");
    fs::hard_link(&cargo, &rustc).expect("create same-file rustc hardlink");
    let recorded = cargo.canonicalize().expect("canonicalize Cargo proxy");
    let proxies = ToolProxySet {
        cargo_proxy_invocation: cargo,
        rustc_proxy_invocation: rustc,
        rustup_proxy_invocation: rustup,
        cargo_resolved_identity: recorded,
        marker: executable_identity_marker(&root.join(if cfg!(windows) {
            "cargo.exe"
        } else {
            "cargo"
        }))
        .expect("capture same-file marker"),
    };
    assert!(
        validate_tool_proxy_set(&proxies).is_ok(),
        "same-file hardlink proxy was rejected by path spelling"
    );
    assert!(scratch.close().is_ok(), "hardlink-proxy scratch cleans up");
}

#[test]
fn tool_proxy_identity_rejects_post_discovery_replacement() {
    let scratch = tempfile::tempdir().expect("create replaced-proxy scratch");
    let root = scratch.path().canonicalize().expect("canonicalize scratch");
    let cargo = root.join(if cfg!(windows) { "cargo.exe" } else { "cargo" });
    let rustc = root.join(if cfg!(windows) { "rustc.exe" } else { "rustc" });
    let rustup = root.join(if cfg!(windows) {
        "rustup.exe"
    } else {
        "rustup"
    });
    make_test_executable(&cargo, b"discovered rustup proxy inode");
    fs::hard_link(&cargo, &rustc).expect("create discovered rustc proxy");
    fs::hard_link(&cargo, &rustup).expect("create discovered rustup proxy");
    let recorded = cargo.canonicalize().expect("canonicalize Cargo proxy");
    let proxies = ToolProxySet {
        cargo_proxy_invocation: cargo.clone(),
        rustc_proxy_invocation: rustc,
        rustup_proxy_invocation: rustup,
        cargo_resolved_identity: recorded,
        marker: executable_identity_marker(&cargo).expect("capture discovered proxy marker"),
    };
    validate_tool_proxy_set(&proxies).expect("initial proxy identity validates");
    fs::remove_file(&cargo).expect("remove discovered Cargo proxy");
    make_test_executable(&cargo, b"replacement executable inode");
    assert!(
        validate_tool_proxy_set(&proxies).is_err(),
        "post-discovery executable replacement passed identity validation"
    );
    assert!(scratch.close().is_ok(), "replaced-proxy scratch cleans up");
}

type LauncherResult<T> = std::result::Result<T, String>;

const PROCESS_STREAM_CAP: usize = 32 * 1024 * 1024;
const PROCESS_POLL_INTERVAL: Duration = Duration::from_millis(10);
const TOOL_PROBE_TIMEOUT: Duration = Duration::from_secs(30);
const BUILD_TIMEOUT: Duration = Duration::from_secs(15 * 60);
const TRACKED_GUEST_MANIFEST: &str = include_str!("../../../artifacts/pdf-guest/manifest.toml");
const FROZEN_RESOLUTION_ARCHIVE_COUNT: usize = 67;
const FROZEN_ACTIVE_IDENTITY_COUNT: usize = 58;
const FROZEN_ACTIVE_REGISTRY_COUNT: usize = 56;
const FROZEN_COMPILER_UNIT_COUNT: usize = 68;
const FROZEN_BUILD_SCRIPT_COUNT: usize = 9;
const FROZEN_RESOLUTION_LOCK_SHA256: &str =
    "355ee328894390cc65ea62ff25020a67cfd614042864f41a0cebe1eeb6f3ff52";
const FROZEN_CACHE_PLAN_SHA256: &str =
    "809419491262af6647fc246b0e9f556ae9deeba8570a80b87cac1029bc52580c";
const FROZEN_BUILD_EVIDENCE_SHA256: &str =
    "f57df7089b3c6d1234f31d436fb514bc746b6aaa201c3b290a0811c3a6f62d83";
const BUILD_EVIDENCE_DOMAIN: &[u8] = b"heleos-pdf-guest-build-evidence-v1\0";

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
struct BuiltGuest {
    bytes: Vec<u8>,
    imports: Vec<ImportRecordV1>,
    exports: Vec<ExportRecordV1>,
    graph: ArtifactDependencyGraphV1,
    policy: ArtifactBuildPolicyV1,
    evidence: GuestBuildEvidenceV1,
}

#[derive(Debug)]
struct ResolutionEvidence {
    pruned_lock: Vec<u8>,
    locked_metadata: Vec<u8>,
}

#[derive(Debug)]
struct ResolutionProjection {
    root: PathBuf,
    pruned_lock: Vec<u8>,
}

#[derive(Debug)]
struct IsolatedBuildCopy {
    workspace_source: ValidatedGuestSource,
    cargo_home: PathBuf,
    build_target: PathBuf,
    build_temp: PathBuf,
    metadata_target: PathBuf,
    metadata_temp: PathBuf,
    resolution_target: PathBuf,
    resolution_temp: PathBuf,
    resolution: ResolutionProjection,
    cache_plan: GuestResolutionCachePlanV1,
    cache_input_snapshot: Vec<SeedSnapshotRecord>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct BuildTargetIdentity {
    target_device: u64,
    target_file_id: u64,
    temp_device: u64,
    temp_file_id: u64,
}

#[derive(Clone, Debug, Eq, Ord, PartialEq, PartialOrd)]
struct SeedSnapshotRecord {
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

fn validate_approved_guest_source(workspace: PathBuf) -> LauncherResult<ValidatedGuestSource> {
    let production_manifest =
        read_bounded_regular_nofollow(&workspace.join("Cargo.toml"), 1024 * 1024)?;
    let guest_manifest = read_bounded_regular_nofollow(
        &workspace.join("crates/heleos-pdf-guest/Cargo.toml"),
        1024 * 1024,
    )?;
    let protocol_manifest = read_bounded_regular_nofollow(
        &workspace.join("crates/heleos-pdf-protocol/Cargo.toml"),
        1024 * 1024,
    )?;
    let fixtures_manifest = read_bounded_regular_nofollow(
        &workspace.join("crates/heleos-test-fixtures/Cargo.toml"),
        1024 * 1024,
    )?;
    let spec = guest_resolution_workspace_spec_v1(
        &production_manifest,
        &guest_manifest,
        &protocol_manifest,
        &fixtures_manifest,
    )
    .map_err(|_| "approved guest workspace manifests violate the frozen spec".to_owned())?;
    let files = read_and_validate_guest_inventory(&workspace, &spec, false)?;
    validate_guest_source_closure_v1(&files)
        .map_err(|_| "approved seven-file guest source closure was rejected".to_owned())?;
    let production_lock =
        read_bounded_regular_nofollow(&workspace.join("Cargo.lock"), 16 * 1024 * 1024)?;
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
) -> LauncherResult<Vec<SourceFileV1>> {
    use std::collections::{BTreeMap, BTreeSet};

    use cap_fs_ext::DirExt;

    let root = cap_std::fs::Dir::open_ambient_dir(workspace, cap_std::ambient_authority())
        .map_err(|_| "guest inventory capability root open failed".to_owned())?;
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
            return Err("guest inventory spec path has unexpected depth".to_owned());
        }
        if components[0] != "crates" || components[2] != "src" && components.len() == 4 {
            return Err("guest inventory spec path has unexpected shape".to_owned());
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
            .ok_or_else(|| "governed crate root inventory is absent".to_owned())?
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
            content: read_cap_relative_file(&root, expected.relative_path, 8 * 1024 * 1024)?,
        });
    }
    Ok(files)
}

fn require_exact_cap_entries(
    directory: &cap_std::fs::Dir,
    expected: &[&str],
) -> LauncherResult<()> {
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
        .collect::<LauncherResult<Vec<_>>>()?;
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
) -> LauncherResult<Vec<u8>> {
    use cap_fs_ext::{DirExt, FollowSymlinks, OpenOptionsFollowExt};

    let path = Path::new(relative);
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
            u64::try_from(cap).map_err(|_| "capability file cap does not fit u64".to_owned())? + 1,
        )
        .read_to_end(&mut content)
        .map_err(|_| "capability file read failed".to_owned())?;
    if content.len() != declared
        || file
            .metadata()
            .map_err(|_| "capability file metadata recheck failed".to_owned())?
            .len()
            != before.len()
    {
        return Err("capability file changed while reading".to_owned());
    }
    Ok(content)
}

fn materialize_resolution_workspace(
    destination: &Path,
    source: &ValidatedGuestSource,
) -> LauncherResult<PathBuf> {
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
) -> LauncherResult<()> {
    let copied = read_and_validate_guest_inventory(workspace, &source.spec, true)?;
    if copied != source.files {
        return Err("materialized guest source differs from the approved bytes".to_owned());
    }
    validate_guest_source_closure_v1(&copied)
        .map_err(|_| "materialized guest source closure was rejected".to_owned())?;
    if read_bounded_regular_nofollow(&workspace.join("Cargo.toml"), 1024 * 1024)?
        != source.spec.root_manifest_utf8_lf.as_bytes()
        || read_bounded_regular_nofollow(&workspace.join("Cargo.lock"), 16 * 1024 * 1024)?
            != expected_lock
    {
        return Err("guest-resolution root manifest or lock differs".to_owned());
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
) -> LauncherResult<ResolutionEvidence> {
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
) -> LauncherResult<ResolutionProjection> {
    validate_materialized_resolution(&root, source, &source.production_lock)?;
    let _discarded = cargo_metadata_unlocked(toolchain, &root, cargo_home, target, temp)
        .map_err(|error| format!("guest-resolution unlocked {error}"))?;
    let pruned_lock = read_bounded_regular_nofollow(&root.join("Cargo.lock"), 16 * 1024 * 1024)?;
    validate_guest_resolution_lock_projection_v1(&source.production_lock, &pruned_lock).map_err(
        |_| "guest-resolution lock is not the frozen deletion-only projection".to_owned(),
    )?;
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
) -> LauncherResult<Vec<u8>> {
    validate_materialized_resolution(&projection.root, source, &projection.pruned_lock)?;
    let locked_metadata = cargo_metadata(toolchain, &projection.root, cargo_home, target, temp)
        .map_err(|error| format!("guest-resolution locked {error}"))?;
    if read_bounded_regular_nofollow(&projection.root.join("Cargo.lock"), 16 * 1024 * 1024)?
        != projection.pruned_lock
    {
        return Err("locked guest metadata mutated the frozen resolution lock".to_owned());
    }
    Ok(locked_metadata)
}

fn write_new_synced(path: &Path, bytes: &[u8]) -> LauncherResult<()> {
    let parent = path
        .parent()
        .ok_or_else(|| "generated file has no parent".to_owned())?;
    fs::create_dir_all(parent).map_err(|_| "generated file parent creation failed".to_owned())?;
    let mut file = OpenOptions::new()
        .write(true)
        .create_new(true)
        .open(path)
        .map_err(|_| "generated file create-new failed".to_owned())?;
    file.write_all(bytes)
        .and_then(|_| file.flush())
        .and_then(|_| file.sync_all())
        .map_err(|_| "generated file write/sync failed".to_owned())?;
    Ok(())
}

fn run_tracked_guest_build(scratch: &Path) -> LauncherResult<()> {
    reject_inherited_build_authority()?;
    let scratch = scratch
        .canonicalize()
        .map_err(|_| "launcher scratch identity validation failed".to_owned())?;
    if !scratch.is_dir() {
        return Err("launcher scratch is not a directory".to_owned());
    }
    let source_workspace = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .map_err(|_| "source workspace identity validation failed".to_owned())?;
    let source = validate_approved_guest_source(source_workspace)?;
    reject_cargo_configs(&source.workspace, None)?;
    let toolchain = discover_toolchain(&scratch)?;
    reject_cargo_configs(&source.workspace, Some(&toolchain.seed_cargo_home))?;
    let seed_target = create_canonical_directory(&scratch.join("seed-target"), "seed target")?;
    let seed_temp = create_canonical_directory(&seed_target.join("tmp"), "seed temp")?;
    let seed_cache_before = snapshot_seed_cargo_inputs(&toolchain.seed_cargo_home)?;
    let full_metadata = cargo_metadata(
        &toolchain,
        &source.workspace,
        &toolchain.seed_cargo_home,
        &seed_target,
        &seed_temp,
    )
    .map_err(|error| format!("seed full-workspace {error}"))?;
    let seed_resolution =
        materialize_resolution_workspace(&scratch.join("seed-resolution"), &source)?;
    let seed_resolution = normalize_resolution_workspace(
        seed_resolution,
        &source,
        &toolchain,
        &toolchain.seed_cargo_home,
        &seed_target,
        &seed_temp,
    )?;
    let seed_graph = normalize_dependency_graph_v1(
        &seed_resolution.locked_metadata,
        &full_metadata,
        &source.production_lock,
        &seed_resolution.pruned_lock,
        "heleos-pdf-guest",
    )
    .map_err(|_| "seed dependency graph normalization failed".to_owned())?;
    let seed_cache_after = snapshot_seed_cargo_inputs(&toolchain.seed_cargo_home)?;
    if seed_cache_before != seed_cache_after {
        return Err("seed list/metadata commands mutated validated Cargo inputs".to_owned());
    }
    let seed_cache_plan =
        guest_resolution_cache_plan_v1(&source.production_lock, &seed_resolution.pruned_lock)
            .map_err(|_| "seed guest-resolution cache plan derivation failed".to_owned())?;
    let seed_resolution_lock_sha256 = guest_resolution_lock_sha256(&seed_resolution.pruned_lock)
        .map_err(|_| "seed guest-resolution lock hashing failed".to_owned())?;
    let seed_cache_plan_sha256 = guest_resolution_cache_plan_sha256(&seed_cache_plan)
        .map_err(|_| "seed guest-resolution cache-plan hashing failed".to_owned())?;
    if seed_cache_plan.resolution_archives.len() != FROZEN_RESOLUTION_ARCHIVE_COUNT
        || seed_graph.records.len() != FROZEN_ACTIVE_IDENTITY_COUNT
        || seed_graph.registry_roots.len() != FROZEN_ACTIVE_REGISTRY_COUNT
        || seed_resolution_lock_sha256 != FROZEN_RESOLUTION_LOCK_SHA256
        || seed_cache_plan_sha256 != FROZEN_CACHE_PLAN_SHA256
    {
        return Err("seed resolution/graph evidence differs from the frozen golden".to_owned());
    }
    let first_copy = prepare_isolated_build_copy(
        scratch.join("first-root/short"),
        &source,
        &toolchain,
        &seed_cache_plan,
        &seed_resolution.pruned_lock,
    )?;
    let second_copy = prepare_isolated_build_copy(
        scratch.join("second-root/with/a/distinct/longer-prefix"),
        &source,
        &toolchain,
        &seed_cache_plan,
        &seed_resolution.pruned_lock,
    )?;
    validate_independent_cache_inputs(&first_copy, &second_copy)?;
    if first_copy.resolution.pruned_lock != second_copy.resolution.pruned_lock
        || first_copy.cache_plan != second_copy.cache_plan
        || first_copy.cache_plan != seed_cache_plan
        || guest_resolution_lock_sha256(&first_copy.resolution.pruned_lock)
            .map_err(|_| "first isolated resolution-lock hashing failed".to_owned())?
            != seed_resolution_lock_sha256
        || guest_resolution_cache_plan_sha256(&first_copy.cache_plan)
            .map_err(|_| "first isolated cache-plan hashing failed".to_owned())?
            != seed_cache_plan_sha256
    {
        return Err("distinct guest-resolution lock/cache-plan evidence differs".to_owned());
    }

    let first_full_metadata =
        supplemental_full_metadata(&first_copy, &toolchain, &toolchain.seed_cargo_home)?;
    let second_full_metadata =
        supplemental_full_metadata(&second_copy, &toolchain, &toolchain.seed_cargo_home)?;
    validate_pruned_cache(
        &first_copy.cargo_home,
        &first_copy.cache_plan,
        &first_copy.cache_input_snapshot,
        true,
    )?;
    let first_guest_metadata = lock_resolution_workspace(
        &first_copy.resolution,
        &first_copy.workspace_source,
        &toolchain,
        &first_copy.cargo_home,
        &first_copy.resolution_target,
        &first_copy.resolution_temp,
    )?;
    validate_pruned_cache(
        &first_copy.cargo_home,
        &first_copy.cache_plan,
        &first_copy.cache_input_snapshot,
        true,
    )?;
    validate_pruned_cache(
        &second_copy.cargo_home,
        &second_copy.cache_plan,
        &second_copy.cache_input_snapshot,
        true,
    )?;
    let second_guest_metadata = lock_resolution_workspace(
        &second_copy.resolution,
        &second_copy.workspace_source,
        &toolchain,
        &second_copy.cargo_home,
        &second_copy.resolution_target,
        &second_copy.resolution_temp,
    )?;
    validate_pruned_cache(
        &second_copy.cargo_home,
        &second_copy.cache_plan,
        &second_copy.cache_input_snapshot,
        true,
    )?;
    let first_graph = normalize_dependency_graph_v1(
        &first_guest_metadata,
        &first_full_metadata,
        &first_copy.workspace_source.production_lock,
        &first_copy.resolution.pruned_lock,
        "heleos-pdf-guest",
    )
    .map_err(|_| "first isolated dependency graph normalization failed".to_owned())?;
    let second_graph = normalize_dependency_graph_v1(
        &second_guest_metadata,
        &second_full_metadata,
        &second_copy.workspace_source.production_lock,
        &second_copy.resolution.pruned_lock,
        "heleos-pdf-guest",
    )
    .map_err(|_| "second isolated dependency graph normalization failed".to_owned())?;
    if first_graph.records != second_graph.records || first_graph.records != seed_graph.records {
        return Err("isolated guest dependency graphs differ".to_owned());
    }
    validate_active_registry_roots_against_plan(&first_graph, &first_copy)?;
    validate_active_registry_roots_against_plan(&second_graph, &second_copy)?;

    let tracked: TrackedGuestManifestV1 = toml::from_str(TRACKED_GUEST_MANIFEST)
        .map_err(|_| "tracked guest manifest parsing failed".to_owned())?;
    let placeholder = manifest_is_placeholder(&tracked);
    if !placeholder
        && (tracked.dependencies != first_graph.records
            || tracked.guest_resolution_lock_sha256 != seed_resolution_lock_sha256)
    {
        return Err("tracked dependency graph differs from the isolated guest graph".to_owned());
    }

    let first = build_isolated_guest(
        first_copy,
        &toolchain,
        first_graph,
        &first_guest_metadata,
        &first_full_metadata,
    )?;
    let second = build_isolated_guest(
        second_copy,
        &toolchain,
        second_graph,
        &second_guest_metadata,
        &second_full_metadata,
    )?;
    if first.bytes != second.bytes {
        return Err(format!(
            "distinct-root guest bytes differ: first={} second={}",
            hash_bytes(&first.bytes)?,
            hash_bytes(&second.bytes)?
        ));
    }
    if first.imports != second.imports
        || first.exports != second.exports
        || first.graph.records != second.graph.records
        || first.evidence != second.evidence
        || first.evidence.guest_resolution_lock_sha256 != seed_resolution_lock_sha256
        || first.evidence.guest_resolution_cache_plan_sha256 != seed_cache_plan_sha256
    {
        return Err("distinct-root normalized records/build evidence differ".to_owned());
    }
    validate_frozen_build_evidence(&first.evidence)?;
    validate_frozen_build_evidence(&second.evidence)?;

    let candidate = candidate_manifest(&source, &first)?;
    if placeholder {
        return Err(format!(
            "verified reproducible candidate; replace the zero placeholder with:\n{}",
            render_manifest(&candidate)?
        ));
    }
    if tracked != candidate {
        return Err(format!(
            "tracked manifest differs from verified candidate (tracked_wasm={}, candidate_wasm={})",
            tracked.wasm_sha256, candidate.wasm_sha256
        ));
    }

    publish_generated_guest(
        &source.workspace,
        &first.bytes,
        &first.policy,
        &second.policy,
    )?;
    Ok(())
}

fn manifest_is_placeholder(manifest: &TrackedGuestManifestV1) -> bool {
    manifest.wasm_byte_length == 0
        && manifest.wasm_sha256 == "0".repeat(64)
        && manifest.guest_resolution_lock_sha256 == "0".repeat(64)
        && manifest.dependencies.is_empty()
        && manifest.imports.is_empty()
        && manifest.exports.is_empty()
}

fn reject_inherited_build_authority() -> LauncherResult<()> {
    const EXACT: &[&str] = &[
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
        "CARGO_ENCODED_RUSTFLAGS",
    ];
    for (name, _) in std::env::vars_os() {
        let name = name
            .to_str()
            .ok_or_else(|| "non-UTF-8 inherited environment name".to_owned())?;
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
        if EXACT.contains(&name)
            || name.starts_with("CARGO_PROFILE_RELEASE_")
            || target_override
            || compiler_family
        {
            return Err(format!("inherited build authority is forbidden: {name}"));
        }
    }
    Ok(())
}

fn discover_toolchain(scratch: &Path) -> LauncherResult<ToolchainInputs> {
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
    let (cargo_proxy_invocation, rustup_proxy_invocation, cargo_resolved_identity, proxy_marker) =
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
        let cmd = system32.join("cmd.exe");
        let taskkill = system32.join("taskkill.exe");
        for executable in [&cmd, &taskkill] {
            let metadata = fs::symlink_metadata(executable)
                .map_err(|_| "Windows system executable metadata failed".to_owned())?;
            if !metadata.is_file()
                || metadata.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0
                || executable
                    .canonicalize()
                    .map_err(|_| "Windows system executable identity failed".to_owned())?
                    != *executable
            {
                return Err("Windows system executable is indirect".to_owned());
            }
        }
        Some(root)
    };
    #[cfg(not(windows))]
    let system_root = None;

    let rustc_proxy = seed_cargo_home.join("bin").join(rustc_name);
    let proxies = ToolProxySet {
        cargo_proxy_invocation,
        rustc_proxy_invocation: rustc_proxy,
        rustup_proxy_invocation,
        cargo_resolved_identity,
        marker: proxy_marker,
    };
    validate_tool_proxy_set(&proxies)?;
    let clean_tool_context = CleanToolContext {
        proxies: &proxies,
        cargo_home: &seed_cargo_home,
        rustup_home: &rustup_home,
        temp: scratch,
        system_root: system_root.as_deref(),
    };

    let cargo_probe = run_clean_tool(
        &clean_tool_context,
        &proxies.cargo_proxy_invocation,
        &["+1.96.1", "-Vv"],
        TOOL_PROBE_TIMEOUT,
    )?;
    let cargo_version = successful_utf8(cargo_probe, "Cargo version probe")?;
    if !cargo_version.lines().any(|line| line == "release: 1.96.1")
        || !cargo_version.lines().any(|line| line.starts_with("host: "))
    {
        return Err("Cargo proxy did not report the pinned 1.96.1 toolchain".to_owned());
    }

    let sysroot_probe = run_clean_tool(
        &clean_tool_context,
        &proxies.rustc_proxy_invocation,
        &["+1.96.1", "--print", "sysroot"],
        TOOL_PROBE_TIMEOUT,
    )?;
    let sysroot_text = successful_utf8(sysroot_probe, "rustc sysroot probe")?;
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

fn executable_identity_marker(path: &Path) -> LauncherResult<ExecutableIdentityMarker> {
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
    let file = cap_std::fs::File::from_std(file);
    let metadata = file
        .metadata()
        .map_err(|_| "tool executable handle metadata failed".to_owned())?;
    Ok(ExecutableIdentityMarker {
        device: MetadataExt::dev(&metadata),
        file_id: MetadataExt::ino(&metadata),
        byte_length: metadata.len(),
    })
}

fn validate_tool_proxy_set(proxies: &ToolProxySet) -> LauncherResult<()> {
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
            return Err("tool proxy spelling differs from the frozen identity".to_owned());
        }
        let resolved = proxy
            .canonicalize()
            .map_err(|_| "tool proxy resolution failed".to_owned())?;
        if executable_identity_marker(&resolved)? != proxies.marker {
            return Err("tool proxy does not name the recorded executable identity".to_owned());
        }
    }
    if proxies
        .cargo_resolved_identity
        .canonicalize()
        .map_err(|_| "recorded Cargo identity resolution failed".to_owned())?
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
) -> LauncherResult<BoundedOutput> {
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

#[cfg(unix)]
fn run_bounded(command: &mut Command, timeout: Duration) -> LauncherResult<BoundedOutput> {
    run_bounded_with_system_root(command, timeout, None)
}

fn run_bounded_with_system_root(
    command: &mut Command,
    timeout: Duration,
    system_root: Option<&Path>,
) -> LauncherResult<BoundedOutput> {
    run_bounded_with_options(command, timeout, PROCESS_STREAM_CAP, system_root)
}

#[cfg(unix)]
fn run_bounded_with_stream_cap(
    command: &mut Command,
    timeout: Duration,
    stream_cap: usize,
) -> LauncherResult<BoundedOutput> {
    run_bounded_with_options(command, timeout, stream_cap, None)
}

fn run_bounded_with_options(
    command: &mut Command,
    timeout: Duration,
    stream_cap: usize,
    system_root: Option<&Path>,
) -> LauncherResult<BoundedOutput> {
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
    let stdout_overflow = overflow.clone();
    let stderr_overflow = overflow.clone();
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
                    .map_err(|_| "bounded subprocess cancellation status failed".to_owned())?
                {
                    break 'cancellation status;
                }
                if grace_started.elapsed() >= Duration::from_millis(100) {
                    terminate_process_tree(child.id(), true, system_root)?;
                    let hard_started = Instant::now();
                    loop {
                        if let Some(status) = child.try_wait().map_err(|_| {
                            "bounded subprocess hard-cancellation status failed".to_owned()
                        })? {
                            break 'cancellation status;
                        }
                        if hard_started.elapsed() >= Duration::from_secs(1) {
                            return Err(
                                "bounded subprocess tree resisted forced termination".to_owned()
                            );
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
        .map_err(|_| "bounded stdout did not drain after process-tree termination".to_owned())?
        .map_err(|_| "bounded stdout read failed".to_owned())?;
    let stderr = stderr_receiver
        .recv_timeout(drain_deadline)
        .map_err(|_| "bounded stderr did not drain after process-tree termination".to_owned())?
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
) -> LauncherResult<()> {
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
        let system_root = system_root.ok_or_else(|| {
            "Windows process-tree termination lacks validated SystemRoot".to_owned()
        })?;
        let system32 = system_root.join("System32");
        let taskkill = system32.join("taskkill.exe");
        let mut command = Command::new(taskkill);
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

fn successful_utf8(output: BoundedOutput, label: &str) -> LauncherResult<String> {
    if !output.status.success() {
        return Err(format!(
            "{label} failed: stdout_bytes={} stdout_sha256={} stderr_bytes={} stderr_sha256={}",
            output.stdout.len(),
            hash_bytes(&output.stdout)?,
            output.stderr.len(),
            hash_bytes(&output.stderr)?
        ));
    }
    if !output.stderr.is_empty() {
        return Err(format!("{label} emitted unexpected stderr"));
    }
    String::from_utf8(output.stdout).map_err(|_| format!("{label} emitted non-UTF-8 stdout"))
}

fn reject_cargo_configs(workspace: &Path, cargo_home: Option<&Path>) -> LauncherResult<()> {
    for ancestor in workspace.ancestors() {
        for relative in [".cargo/config", ".cargo/config.toml"] {
            let candidate = ancestor.join(relative);
            match fs::symlink_metadata(&candidate) {
                Ok(_) => {
                    return Err("uncontrolled Cargo config exists in workspace ancestry".to_owned());
                }
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
) -> LauncherResult<Vec<u8>> {
    cargo_metadata_with_lock_mode(toolchain, workspace, cargo_home, target, temp, true)
}

fn cargo_metadata_unlocked(
    toolchain: &ToolchainInputs,
    workspace: &Path,
    cargo_home: &Path,
    target: &Path,
    temp: &Path,
) -> LauncherResult<Vec<u8>> {
    cargo_metadata_with_lock_mode(toolchain, workspace, cargo_home, target, temp, false)
}

fn cargo_metadata_with_lock_mode(
    toolchain: &ToolchainInputs,
    workspace: &Path,
    cargo_home: &Path,
    target: &Path,
    temp: &Path,
    locked: bool,
) -> LauncherResult<Vec<u8>> {
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
            hash_bytes(&output.stdout)?,
            output.stderr.len(),
            hash_bytes(&output.stderr)?
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
) -> LauncherResult<()> {
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

fn prepare_isolated_build_copy(
    base: PathBuf,
    approved_source: &ValidatedGuestSource,
    toolchain: &ToolchainInputs,
    seed_cache_plan: &GuestResolutionCachePlanV1,
    seed_pruned_lock: &[u8],
) -> LauncherResult<IsolatedBuildCopy> {
    let workspace = base.join("workspace");
    let cargo_home = base.join("cargo-cache");
    let build_target = base.join("target-output");
    let metadata_target = base.join("full-metadata-target");
    let resolution_target = base.join("resolution-target");
    fs::create_dir_all(&base).map_err(|_| "isolated build root creation failed".to_owned())?;
    let base = canonical_directory(&base, "isolated build root")?;
    let workspace = create_canonical_directory(&workspace, "isolated workspace")?;
    let cargo_home = create_canonical_directory(&cargo_home, "isolated Cargo home")?;
    let build_target = create_canonical_directory(&build_target, "isolated build target")?;
    let metadata_target = create_canonical_directory(&metadata_target, "isolated metadata target")?;
    let resolution_target =
        create_canonical_directory(&resolution_target, "isolated resolution target")?;
    let build_temp =
        create_canonical_directory(&build_target.join(".heleos-tmp"), "isolated build temp")?;
    let metadata_temp =
        create_canonical_directory(&metadata_target.join("tmp"), "isolated metadata temp")?;
    let resolution_temp =
        create_canonical_directory(&resolution_target.join("tmp"), "isolated resolution temp")?;

    copy_build_workspace(&approved_source.workspace, &workspace)?;
    let workspace_source = validate_approved_guest_source(workspace.clone())?;
    if workspace_source.spec != approved_source.spec
        || workspace_source.files != approved_source.files
        || workspace_source.production_lock != approved_source.production_lock
    {
        return Err("isolated source/workspace copy differs from the approved source".to_owned());
    }
    reject_cargo_configs(&workspace, Some(&cargo_home))?;
    let cache_input_snapshot =
        materialize_verified_cache(&toolchain.seed_cargo_home, &cargo_home, seed_cache_plan)?;
    reject_cargo_configs(&workspace, Some(&cargo_home))?;
    let resolution_root =
        materialize_resolution_workspace(&base.join("guest-resolution"), &workspace_source)?;
    let resolution = project_resolution_workspace(
        resolution_root,
        &workspace_source,
        toolchain,
        &cargo_home,
        &resolution_target,
        &resolution_temp,
    )?;
    validate_pruned_cache(&cargo_home, seed_cache_plan, &cache_input_snapshot, true)?;
    let cache_plan =
        guest_resolution_cache_plan_v1(&workspace_source.production_lock, &resolution.pruned_lock)
            .map_err(|_| "isolated guest-resolution cache plan derivation failed".to_owned())?;
    if resolution.pruned_lock != seed_pruned_lock || cache_plan != *seed_cache_plan {
        return Err("isolated unlocked resolution differs from the seed evidence".to_owned());
    }
    Ok(IsolatedBuildCopy {
        workspace_source,
        cargo_home,
        build_target,
        build_temp,
        metadata_target,
        metadata_temp,
        resolution_target,
        resolution_temp,
        resolution,
        cache_plan,
        cache_input_snapshot,
    })
}

fn supplemental_full_metadata(
    copy: &IsolatedBuildCopy,
    toolchain: &ToolchainInputs,
    seed_cargo_home: &Path,
) -> LauncherResult<Vec<u8>> {
    reject_cargo_configs(&copy.workspace_source.workspace, Some(seed_cargo_home))?;
    let copied = read_and_validate_guest_inventory(
        &copy.workspace_source.workspace,
        &copy.workspace_source.spec,
        false,
    )?;
    if copied != copy.workspace_source.files {
        return Err("isolated workspace changed before supplemental metadata".to_owned());
    }
    validate_guest_source_closure_v1(&copied)
        .map_err(|_| "isolated workspace source closure changed".to_owned())?;
    let before = snapshot_seed_cargo_inputs(seed_cargo_home)?;
    let metadata = cargo_metadata(
        toolchain,
        &copy.workspace_source.workspace,
        seed_cargo_home,
        &copy.metadata_target,
        &copy.metadata_temp,
    )
    .map_err(|error| format!("supplemental full-workspace {error}"));
    let after = snapshot_seed_cargo_inputs(seed_cargo_home)?;
    if before != after {
        return Err("supplemental full metadata mutated the validated seed inputs".to_owned());
    }
    metadata
}

fn validate_independent_cache_inputs(
    first: &IsolatedBuildCopy,
    second: &IsolatedBuildCopy,
) -> LauncherResult<()> {
    let second_records = second
        .cache_input_snapshot
        .iter()
        .map(|record| (record.relative_path.as_str(), record))
        .collect::<std::collections::BTreeMap<_, _>>();
    if first.cache_input_snapshot.len() != second_records.len() {
        return Err("isolated cache input inventories differ".to_owned());
    }
    for first_record in &first.cache_input_snapshot {
        let second_record = second_records
            .get(first_record.relative_path.as_str())
            .copied()
            .ok_or_else(|| "isolated cache input path differs".to_owned())?;
        if first_record.kind != second_record.kind
            || first_record.byte_length != second_record.byte_length
            || first_record.sha256 != second_record.sha256
            || (first_record.device == second_record.device
                && first_record.file_id == second_record.file_id)
        {
            return Err(
                "isolated cache inputs are not byte-equal and physically independent".to_owned(),
            );
        }
    }
    Ok(())
}

fn validate_active_registry_roots_against_plan(
    graph: &ArtifactDependencyGraphV1,
    copy: &IsolatedBuildCopy,
) -> LauncherResult<()> {
    let archives = copy
        .cache_plan
        .resolution_archives
        .iter()
        .map(|archive| (archive.normalized_dependency_id.as_str(), archive))
        .collect::<std::collections::BTreeMap<_, _>>();
    if archives.len() != copy.cache_plan.resolution_archives.len() {
        return Err("cache plan contains duplicate normalized identities".to_owned());
    }
    for root in &graph.registry_roots {
        let archive = archives
            .get(root.normalized_dependency_id.as_str())
            .copied()
            .ok_or_else(|| "active registry identity is absent from cache plan".to_owned())?;
        let expected = copy
            .cargo_home
            .join(&archive.unpacked_source_cargo_home_relative_path);
        let canonical = expected
            .canonicalize()
            .map_err(|_| "active planned registry source is missing".to_owned())?;
        if canonical != expected || !canonical.is_dir() || root.physical_root != canonical {
            return Err("active registry root differs from its cache-plan source path".to_owned());
        }
    }
    Ok(())
}

fn snapshot_seed_cargo_inputs(seed_cargo_home: &Path) -> LauncherResult<Vec<SeedSnapshotRecord>> {
    const ENTRY_CAP: usize = 131_072;
    const CONTENT_CAP: u64 = 16 * 1024 * 1024 * 1024;

    use cap_fs_ext::DirExt;

    let root = cap_std::fs::Dir::open_ambient_dir(seed_cargo_home, cap_std::ambient_authority())
        .map_err(|_| "seed Cargo snapshot root open failed".to_owned())?;
    for config in ["config", "config.toml"] {
        match root.symlink_metadata(config) {
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {}
            Ok(_) => return Err("seed Cargo root config appeared during snapshot".to_owned()),
            Err(_) => return Err("seed Cargo root config inspection failed".to_owned()),
        }
    }
    let registry = root
        .open_dir_nofollow("registry")
        .map_err(|_| "seed Cargo registry root is missing or indirect".to_owned())?;
    let mut records = vec![
        SeedSnapshotRecord {
            relative_path: "config".to_owned(),
            kind: 0,
            byte_length: 0,
            sha256: String::new(),
            device: 0,
            file_id: 0,
            link_count: 0,
        },
        SeedSnapshotRecord {
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
        let directory_metadata = directory
            .dir_metadata()
            .map_err(|_| "seed Cargo snapshot subtree metadata failed".to_owned())?;
        records.push(SeedSnapshotRecord {
            relative_path: format!("registry/{name}"),
            kind: 1,
            byte_length: 0,
            sha256: String::new(),
            device: cap_fs_ext::MetadataExt::dev(&directory_metadata),
            file_id: cap_fs_ext::MetadataExt::ino(&directory_metadata),
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

fn snapshot_cap_directory(
    directory: &cap_std::fs::Dir,
    relative: &str,
    records: &mut Vec<SeedSnapshotRecord>,
    total_bytes: &mut u64,
    entry_cap: usize,
    content_cap: u64,
) -> LauncherResult<()> {
    use cap_fs_ext::{DirExt, FollowSymlinks, OpenOptionsFollowExt};

    let mut names = directory
        .entries()
        .map_err(|_| "seed Cargo snapshot enumeration failed".to_owned())?
        .map(|entry| {
            entry
                .map_err(|_| "seed Cargo snapshot entry failed".to_owned())?
                .file_name()
                .into_string()
                .map_err(|_| "seed Cargo snapshot name is not UTF-8".to_owned())
        })
        .collect::<LauncherResult<Vec<_>>>()?;
    names.sort();
    for name in names {
        if records.len() >= entry_cap {
            return Err("seed Cargo snapshot exceeded its entry cap".to_owned());
        }
        let child_relative = if relative.is_empty() {
            name.clone()
        } else {
            format!("{relative}/{name}")
        };
        let metadata = directory
            .symlink_metadata(&name)
            .map_err(|_| "seed Cargo snapshot metadata failed".to_owned())?;
        if metadata.is_dir() {
            let child = directory
                .open_dir_nofollow(&name)
                .map_err(|_| "seed Cargo snapshot directory is indirect".to_owned())?;
            records.push(SeedSnapshotRecord {
                relative_path: child_relative.clone(),
                kind: 1,
                byte_length: 0,
                sha256: String::new(),
                device: cap_fs_ext::MetadataExt::dev(&metadata),
                file_id: cap_fs_ext::MetadataExt::ino(&metadata),
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
                .ok_or_else(|| "seed Cargo snapshot byte count overflow".to_owned())?;
            if *total_bytes > content_cap {
                return Err("seed Cargo snapshot exceeded its content cap".to_owned());
            }
            let mut options = cap_std::fs::OpenOptions::new();
            options.read(true).follow(FollowSymlinks::No);
            let mut file = directory
                .open_with(&name, &options)
                .map_err(|_| "seed Cargo snapshot file open failed".to_owned())?;
            let before = file
                .metadata()
                .map_err(|_| "seed Cargo snapshot file metadata failed".to_owned())?;
            let digest = Sha256Digest::hash_reader(&mut file)
                .map_err(|_| "seed Cargo snapshot file hashing failed".to_owned())?
                .to_string();
            let after = file
                .metadata()
                .map_err(|_| "seed Cargo snapshot file metadata recheck failed".to_owned())?;
            if before.len() != metadata.len() || after.len() != before.len() {
                return Err("seed Cargo snapshot file changed while hashing".to_owned());
            }
            records.push(SeedSnapshotRecord {
                relative_path: child_relative,
                kind: 2,
                byte_length: before.len(),
                sha256: digest,
                device: cap_fs_ext::MetadataExt::dev(&before),
                file_id: cap_fs_ext::MetadataExt::ino(&before),
                link_count: cap_fs_ext::MetadataExt::nlink(&before),
            });
        } else {
            return Err("seed Cargo snapshot contains a symlink or special entry".to_owned());
        }
    }
    Ok(())
}

fn build_isolated_guest(
    copy: IsolatedBuildCopy,
    toolchain: &ToolchainInputs,
    mut graph: ArtifactDependencyGraphV1,
    guest_metadata: &[u8],
    full_metadata: &[u8],
) -> LauncherResult<BuiltGuest> {
    let workspace = &copy.resolution.root;
    let cargo_home = &copy.cargo_home;
    let target = &copy.build_target;
    let temp = &copy.build_temp;
    validate_materialized_resolution(
        workspace,
        &copy.workspace_source,
        &copy.resolution.pruned_lock,
    )?;
    reject_cargo_configs(workspace, Some(cargo_home))?;
    validate_pruned_cache(
        cargo_home,
        &copy.cache_plan,
        &copy.cache_input_snapshot,
        true,
    )?;
    let temp_identity = validate_build_target_precondition(target, temp)?;
    for registry in &mut graph.registry_roots {
        let canonical = registry
            .physical_root
            .canonicalize()
            .map_err(|_| "isolated registry root identity failed".to_owned())?;
        if canonical != registry.physical_root || !canonical.is_dir() {
            return Err("isolated registry root is not canonical".to_owned());
        }
        registry.physical_root = canonical;
    }

    let inputs = ArtifactBuildInputsV1 {
        cargo_proxy_invocation: toolchain.proxies.cargo_proxy_invocation.clone(),
        cargo_resolved_identity: toolchain.proxies.cargo_resolved_identity.clone(),
        workspace_root: workspace.clone(),
        cargo_home: cargo_home.clone(),
        target_root: target.clone(),
        rustc_sysroot: toolchain.rustc_sysroot.clone(),
        rustup_home: toolchain.rustup_home.clone(),
        registry_roots: graph.registry_roots.clone(),
        system_root: toolchain.system_root.clone(),
    };
    validate_tool_proxy_set(&toolchain.proxies)?;
    let policy = artifact_build_policy_v1(&inputs)
        .map_err(|_| "isolated artifact build policy rejected validated roots".to_owned())?;
    if policy.cargo_proxy_invocation() != toolchain.proxies.cargo_proxy_invocation
        || policy.cargo_resolved_identity() != toolchain.proxies.cargo_resolved_identity
        || policy.working_directory() != workspace
        || !policy.env_clear()
    {
        return Err("artifact policy changed validated launcher identities".to_owned());
    }
    let temp_utf8 = temp
        .to_str()
        .ok_or_else(|| "isolated build temp is not UTF-8".to_owned())?;
    for name in ["TEMP", "TMP", "TMPDIR"] {
        if policy
            .environment()
            .iter()
            .find(|entry| entry.name() == name)
            .map(|entry| entry.value())
            != Some(temp_utf8)
        {
            return Err("artifact policy changed the isolated build temp".to_owned());
        }
    }

    validate_tool_proxy_set(&toolchain.proxies)?;
    let mut command = Command::new(policy.cargo_proxy_invocation());
    command
        .current_dir(policy.working_directory())
        .args(policy.argv())
        .env_clear();
    for entry in policy.environment() {
        command.env(entry.name(), entry.value());
    }
    let output = run_bounded_with_system_root(
        &mut command,
        BUILD_TIMEOUT,
        toolchain.system_root.as_deref(),
    );
    validate_tool_proxy_set(&toolchain.proxies)?;
    let output = output?;
    if !output.status.success() {
        return Err(format!(
            "controlled guest build failed: stdout_bytes={} stdout_sha256={} stderr_bytes={} stderr_sha256={}",
            output.stdout.len(),
            hash_bytes(&output.stdout)?,
            output.stderr.len(),
            hash_bytes(&output.stderr)?
        ));
    }
    if !output.stderr.is_empty() {
        return Err(format!(
            "controlled guest build emitted stderr: bytes={} sha256={}",
            output.stderr.len(),
            hash_bytes(&output.stderr)?
        ));
    }
    let evidence = validate_guest_build_evidence_v1(
        &output.stdout,
        guest_metadata,
        full_metadata,
        &copy.workspace_source.production_lock,
        &copy.resolution.pruned_lock,
        "heleos-pdf-guest",
        &policy,
    )
    .map_err(|_| "controlled Cargo JSON build evidence was rejected".to_owned())?;
    validate_materialized_resolution(
        workspace,
        &copy.workspace_source,
        &copy.resolution.pruned_lock,
    )?;
    validate_pruned_cache(
        cargo_home,
        &copy.cache_plan,
        &copy.cache_input_snapshot,
        true,
    )?;
    let artifact = target.join("wasm32-wasip1/release/heleos_pdf_guest.wasm");
    let bytes = validate_build_target_after_build(target, temp, temp_identity, &artifact)?;
    let (imports, exports) = validate_pdf_guest_module_policy_v1(&bytes)
        .map_err(|_| "built guest module policy validation failed".to_owned())?;
    verify_no_physical_prefixes_v1(&bytes, &policy)
        .map_err(|_| "built guest contains a physical build prefix".to_owned())?;
    Ok(BuiltGuest {
        bytes,
        imports,
        exports,
        graph,
        policy,
        evidence,
    })
}

fn validate_build_target_precondition(
    target: &Path,
    temp: &Path,
) -> LauncherResult<BuildTargetIdentity> {
    use cap_fs_ext::{DirExt, MetadataExt};

    if canonical_directory(target, "isolated build target")? != target
        || canonical_directory(temp, "isolated build temp")? != temp
        || temp != target.join(".heleos-tmp")
    {
        return Err("isolated target/temp identity differs from the frozen layout".to_owned());
    }
    let root = cap_std::fs::Dir::open_ambient_dir(target, cap_std::ambient_authority())
        .map_err(|_| "isolated target capability open failed".to_owned())?;
    let root_metadata = root
        .dir_metadata()
        .map_err(|_| "isolated target metadata failed".to_owned())?;
    let names = root
        .entries()
        .map_err(|_| "isolated target enumeration failed".to_owned())?
        .map(|entry| {
            entry
                .map_err(|_| "isolated target entry failed".to_owned())?
                .file_name()
                .into_string()
                .map_err(|_| "isolated target entry is not UTF-8".to_owned())
        })
        .collect::<LauncherResult<Vec<_>>>()?;
    if names != [".heleos-tmp"] {
        return Err("isolated target does not contain exactly the frozen temp child".to_owned());
    }
    let metadata = root
        .symlink_metadata(".heleos-tmp")
        .map_err(|_| "isolated build temp metadata failed".to_owned())?;
    let directory = root
        .open_dir_nofollow(".heleos-tmp")
        .map_err(|_| "isolated build temp is indirect".to_owned())?;
    if !metadata.is_dir()
        || directory
            .entries()
            .map_err(|_| "isolated build temp enumeration failed".to_owned())?
            .next()
            .is_some()
    {
        return Err("isolated build temp is not an empty direct directory".to_owned());
    }
    Ok(BuildTargetIdentity {
        target_device: MetadataExt::dev(&root_metadata),
        target_file_id: MetadataExt::ino(&root_metadata),
        temp_device: MetadataExt::dev(&metadata),
        temp_file_id: MetadataExt::ino(&metadata),
    })
}

fn validate_build_target_after_build(
    target: &Path,
    temp: &Path,
    identity: BuildTargetIdentity,
    artifact: &Path,
) -> LauncherResult<Vec<u8>> {
    validate_build_target_after_build_with_hook(target, temp, identity, artifact, || Ok(()))
}

fn validate_build_target_after_build_with_hook<F>(
    target: &Path,
    temp: &Path,
    identity: BuildTargetIdentity,
    artifact: &Path,
    after_inventory: F,
) -> LauncherResult<Vec<u8>>
where
    F: FnOnce() -> LauncherResult<()>,
{
    use cap_fs_ext::{DirExt, MetadataExt};

    let root = cap_std::fs::Dir::open_ambient_dir(target, cap_std::ambient_authority())
        .map_err(|_| "built target capability open failed".to_owned())?;
    let root_metadata = root
        .dir_metadata()
        .map_err(|_| "built target metadata failed".to_owned())?;
    let metadata = root
        .symlink_metadata(".heleos-tmp")
        .map_err(|_| "built target temp metadata failed".to_owned())?;
    let directory = root
        .open_dir_nofollow(".heleos-tmp")
        .map_err(|_| "built target temp is indirect".to_owned())?;
    if temp != target.join(".heleos-tmp")
        || MetadataExt::dev(&root_metadata) != identity.target_device
        || MetadataExt::ino(&root_metadata) != identity.target_file_id
        || !metadata.is_dir()
        || MetadataExt::dev(&metadata) != identity.temp_device
        || MetadataExt::ino(&metadata) != identity.temp_file_id
        || directory
            .entries()
            .map_err(|_| "built target temp enumeration failed".to_owned())?
            .next()
            .is_some()
    {
        return Err("build rebound or populated the frozen temp directory".to_owned());
    }
    let mut records = Vec::new();
    let mut total = 0_u64;
    snapshot_cap_directory(
        &root,
        "",
        &mut records,
        &mut total,
        131_072,
        2 * 1024 * 1024 * 1024,
    )?;
    let relative_artifact = artifact
        .strip_prefix(target)
        .map_err(|_| "built guest artifact escaped the target".to_owned())?
        .to_str()
        .ok_or_else(|| "built guest artifact path is not UTF-8".to_owned())?
        .replace('\\', "/");
    let artifact_record = records
        .iter()
        .find(|record| record.relative_path == relative_artifact)
        .ok_or_else(|| "built target does not contain the exact guest candidate".to_owned())?;
    if artifact_record.kind != 2
        || artifact_record.byte_length == 0
        || artifact_record.byte_length > MAX_GUEST_WASM_BYTES as u64
    {
        return Err("built target does not contain the exact bounded guest candidate".to_owned());
    }
    after_inventory()?;
    let bytes = read_checked_build_candidate(
        &root,
        &relative_artifact,
        MAX_GUEST_WASM_BYTES,
        artifact_record,
    )?;

    let root_after = root
        .dir_metadata()
        .map_err(|_| "built target metadata recheck failed".to_owned())?;
    let temp_after = root
        .symlink_metadata(".heleos-tmp")
        .map_err(|_| "built target temp metadata recheck failed".to_owned())?;
    let temp_after_directory = root
        .open_dir_nofollow(".heleos-tmp")
        .map_err(|_| "built target temp became indirect".to_owned())?;
    if MetadataExt::dev(&root_after) != identity.target_device
        || MetadataExt::ino(&root_after) != identity.target_file_id
        || !temp_after.is_dir()
        || MetadataExt::dev(&temp_after) != identity.temp_device
        || MetadataExt::ino(&temp_after) != identity.temp_file_id
        || temp_after_directory
            .entries()
            .map_err(|_| "built target temp re-enumeration failed".to_owned())?
            .next()
            .is_some()
    {
        return Err("build rebound the retained target or temp after candidate read".to_owned());
    }

    if canonical_directory(target, "reopened isolated build target")? != target {
        return Err("built target path became indirect after candidate read".to_owned());
    }
    let reopened = cap_std::fs::Dir::open_ambient_dir(target, cap_std::ambient_authority())
        .map_err(|_| "built target path reopen failed".to_owned())?;
    let reopened_metadata = reopened
        .dir_metadata()
        .map_err(|_| "built target path metadata recheck failed".to_owned())?;
    if MetadataExt::dev(&reopened_metadata) != identity.target_device
        || MetadataExt::ino(&reopened_metadata) != identity.target_file_id
    {
        return Err("built target path no longer names the retained target".to_owned());
    }
    Ok(bytes)
}

fn read_checked_build_candidate(
    root: &cap_std::fs::Dir,
    relative: &str,
    cap: usize,
    expected: &SeedSnapshotRecord,
) -> LauncherResult<Vec<u8>> {
    use cap_fs_ext::{DirExt, FollowSymlinks, MetadataExt, OpenOptionsFollowExt};

    let path = Path::new(relative);
    let parent = path
        .parent()
        .ok_or_else(|| "built candidate has no parent".to_owned())?;
    let name = path
        .file_name()
        .ok_or_else(|| "built candidate has no name".to_owned())?;
    let mut directory = root
        .try_clone()
        .map_err(|_| "built target capability clone failed".to_owned())?;
    for component in parent.components() {
        directory = directory
            .open_dir_nofollow(component.as_os_str())
            .map_err(|_| "built candidate parent is missing or indirect".to_owned())?;
    }
    let mut options = cap_std::fs::OpenOptions::new();
    options.read(true).follow(FollowSymlinks::No);
    let mut file = directory
        .open_with(name, &options)
        .map_err(|_| "built candidate open failed".to_owned())?;
    let before = file
        .metadata()
        .map_err(|_| "built candidate metadata failed".to_owned())?;
    let declared = usize::try_from(before.len())
        .map_err(|_| "built candidate length does not fit memory".to_owned())?;
    if !before.is_file()
        || declared == 0
        || declared > cap
        || before.len() != expected.byte_length
        || MetadataExt::dev(&before) != expected.device
        || MetadataExt::ino(&before) != expected.file_id
        || MetadataExt::nlink(&before) != expected.link_count
    {
        return Err("built candidate identity differs from the checked inventory".to_owned());
    }
    let mut bytes = Vec::with_capacity(declared);
    (&mut file)
        .take(
            u64::try_from(cap).map_err(|_| "built candidate cap does not fit u64".to_owned())? + 1,
        )
        .read_to_end(&mut bytes)
        .map_err(|_| "built candidate read failed".to_owned())?;
    let after = file
        .metadata()
        .map_err(|_| "built candidate metadata recheck failed".to_owned())?;
    if bytes.len() != declared
        || after.len() != before.len()
        || MetadataExt::dev(&after) != expected.device
        || MetadataExt::ino(&after) != expected.file_id
        || MetadataExt::nlink(&after) != expected.link_count
        || hash_bytes(&bytes)? != expected.sha256
    {
        return Err("built candidate changed while reading".to_owned());
    }
    Ok(bytes)
}

fn canonical_directory(path: &Path, label: &str) -> LauncherResult<PathBuf> {
    let canonical = path
        .canonicalize()
        .map_err(|_| format!("{label} identity validation failed"))?;
    if canonical != path || !canonical.is_dir() {
        return Err(format!("{label} is not a canonical directory"));
    }
    Ok(canonical)
}

fn create_canonical_directory(path: &Path, label: &str) -> LauncherResult<PathBuf> {
    fs::create_dir_all(path).map_err(|_| format!("{label} creation failed"))?;
    canonical_directory(path, label)
}

fn copy_build_workspace(source: &Path, destination: &Path) -> LauncherResult<()> {
    use cap_fs_ext::DirExt;

    let source = cap_std::fs::Dir::open_ambient_dir(source, cap_std::ambient_authority())
        .map_err(|_| "workspace source capability open failed".to_owned())?;
    let destination = cap_std::fs::Dir::open_ambient_dir(destination, cap_std::ambient_authority())
        .map_err(|_| "workspace destination capability open failed".to_owned())?;
    for file in [
        "Cargo.toml",
        "Cargo.lock",
        "rust-toolchain.toml",
        "rustfmt.toml",
        ".gitattributes",
    ] {
        copy_cap_file(&source, &destination, file)?;
    }
    let source_crates = source
        .open_dir_nofollow("crates")
        .map_err(|_| "workspace crates capability open failed".to_owned())?;
    destination
        .create_dir("crates")
        .map_err(|_| "workspace crates destination creation failed".to_owned())?;
    let destination_crates = destination
        .open_dir_nofollow("crates")
        .map_err(|_| "workspace crates destination open failed".to_owned())?;
    copy_cap_tree(&source_crates, &destination_crates)?;

    // Older workspace revisions do not have the verification package. Cargo
    // metadata still rejects a declared package that is absent from this copy.
    let mut source_verification = source;
    for name in ["tests", "verification"] {
        match source_verification.symlink_metadata(name) {
            Ok(metadata) if metadata.is_dir() => {}
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(()),
            _ => return Err("workspace verification directory is missing or indirect".to_owned()),
        }
        source_verification = source_verification
            .open_dir_nofollow(name)
            .map_err(|_| "workspace verification capability open failed".to_owned())?;
    }
    let mut destination_verification = destination;
    for name in ["tests", "verification"] {
        destination_verification
            .create_dir(name)
            .map_err(|_| "workspace verification destination creation failed".to_owned())?;
        destination_verification = destination_verification
            .open_dir_nofollow(name)
            .map_err(|_| "workspace verification destination open failed".to_owned())?;
    }
    copy_cap_tree(&source_verification, &destination_verification)
}

fn copy_cap_tree(source: &cap_std::fs::Dir, destination: &cap_std::fs::Dir) -> LauncherResult<()> {
    use cap_fs_ext::DirExt;

    let mut names = source
        .entries()
        .map_err(|_| "workspace capability enumeration failed".to_owned())?
        .map(|entry| {
            entry
                .map_err(|_| "workspace capability entry failed".to_owned())?
                .file_name()
                .into_string()
                .map_err(|_| "workspace source contains a non-UTF-8 name".to_owned())
        })
        .collect::<LauncherResult<Vec<_>>>()?;
    names.sort();
    for name in names {
        let metadata = source
            .symlink_metadata(&name)
            .map_err(|_| "workspace capability metadata failed".to_owned())?;
        if metadata.is_dir() {
            let source_child = source
                .open_dir_nofollow(&name)
                .map_err(|_| "workspace source directory is indirect".to_owned())?;
            destination
                .create_dir(&name)
                .map_err(|_| "workspace destination directory creation failed".to_owned())?;
            let destination_child = destination
                .open_dir_nofollow(&name)
                .map_err(|_| "workspace destination directory open failed".to_owned())?;
            copy_cap_tree(&source_child, &destination_child)?;
        } else if metadata.is_file() {
            copy_cap_file(source, destination, &name)?;
        } else {
            return Err("workspace source contains a symlink or special file".to_owned());
        }
    }
    Ok(())
}

fn copy_cap_file(
    source: &cap_std::fs::Dir,
    destination: &cap_std::fs::Dir,
    name: &str,
) -> LauncherResult<()> {
    const COPY_FILE_CAP: usize = 512 * 1024 * 1024;

    use cap_fs_ext::{FollowSymlinks, OpenOptionsFollowExt};

    let mut source_options = cap_std::fs::OpenOptions::new();
    source_options.read(true).follow(FollowSymlinks::No);
    let mut source_file = source
        .open_with(name, &source_options)
        .map_err(|_| "workspace source file open failed".to_owned())?;
    let source_metadata = source_file
        .metadata()
        .map_err(|_| "workspace source file metadata failed".to_owned())?;
    let source_length = usize::try_from(source_metadata.len())
        .map_err(|_| "workspace source file length does not fit memory".to_owned())?;
    if !source_metadata.is_file() || source_length == 0 || source_length > COPY_FILE_CAP {
        return Err("workspace source file violates its copy bound".to_owned());
    }
    let mut bytes = Vec::with_capacity(source_length);
    (&mut source_file)
        .take(u64::try_from(COPY_FILE_CAP).map_err(|_| "copy cap does not fit u64".to_owned())? + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| "workspace source file read failed".to_owned())?;
    if bytes.len() != source_length
        || source_file
            .metadata()
            .map_err(|_| "workspace source file metadata recheck failed".to_owned())?
            .len()
            != source_metadata.len()
    {
        return Err("workspace source file changed while copying".to_owned());
    }

    let mut destination_options = cap_std::fs::OpenOptions::new();
    destination_options
        .write(true)
        .create_new(true)
        .follow(FollowSymlinks::No);
    let mut destination_file = destination
        .open_with(name, &destination_options)
        .map_err(|_| "workspace destination file create-new failed".to_owned())?;
    destination_file
        .write_all(&bytes)
        .and_then(|_| destination_file.flush())
        .and_then(|_| destination_file.sync_all())
        .map_err(|_| "workspace destination file write/sync failed".to_owned())?;
    drop(destination_file);
    let copied = read_cap_relative_file(destination, name, COPY_FILE_CAP)?;
    if copied != bytes {
        return Err("workspace destination bytes differ".to_owned());
    }
    Ok(())
}

fn materialize_verified_cache(
    seed_cargo_home: &Path,
    destination_cargo_home: &Path,
    plan: &GuestResolutionCachePlanV1,
) -> LauncherResult<Vec<SeedSnapshotRecord>> {
    const COPY_FILE_CAP: usize = 512 * 1024 * 1024;

    let seed = cap_std::fs::Dir::open_ambient_dir(seed_cargo_home, cap_std::ambient_authority())
        .map_err(|_| "seed Cargo home capability open failed".to_owned())?;
    let destination =
        cap_std::fs::Dir::open_ambient_dir(destination_cargo_home, cap_std::ambient_authority())
            .map_err(|_| "isolated Cargo home capability open failed".to_owned())?;
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
) -> LauncherResult<()> {
    use cap_fs_ext::{FollowSymlinks, MetadataExt, OpenOptionsFollowExt};

    let source_bytes = read_cap_relative_file(source_root, relative, cap)?;
    if let Some(expected) = expected_sha256
        && hash_bytes(&source_bytes)? != expected
    {
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
        .map_err(|_| "isolated cache file create-new failed".to_owned())?;
    file.write_all(&source_bytes)
        .and_then(|_| file.flush())
        .and_then(|_| file.sync_all())
        .map_err(|_| "isolated cache file write/sync failed".to_owned())?;
    let metadata = file
        .metadata()
        .map_err(|_| "isolated cache file metadata failed".to_owned())?;
    if !metadata.is_file() || MetadataExt::nlink(&metadata) != 1 {
        return Err("isolated cache file identity is not independent".to_owned());
    }
    drop(file);
    if read_cap_relative_file(destination_root, relative, cap)? != source_bytes {
        return Err("isolated cache file bytes differ after copy".to_owned());
    }
    Ok(())
}

fn ensure_cap_parent(
    root: &cap_std::fs::Dir,
    relative: &str,
) -> LauncherResult<(cap_std::fs::Dir, std::ffi::OsString)> {
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
        .ok_or_else(|| "cache-plan path has no file name".to_owned())?
        .to_os_string();
    let mut directory = root
        .try_clone()
        .map_err(|_| "cache destination capability clone failed".to_owned())?;
    let parent = path
        .parent()
        .ok_or_else(|| "cache-plan path has no parent".to_owned())?;
    for component in parent.components() {
        let component = component.as_os_str();
        match directory.symlink_metadata(component) {
            Ok(metadata) if metadata.is_dir() => {}
            Ok(_) => return Err("cache destination parent is indirect or non-directory".to_owned()),
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                directory
                    .create_dir(component)
                    .map_err(|_| "cache destination parent creation failed".to_owned())?;
            }
            Err(_) => return Err("cache destination parent inspection failed".to_owned()),
        }
        directory = directory
            .open_dir_nofollow(component)
            .map_err(|_| "cache destination parent is indirect".to_owned())?;
    }
    Ok((directory, name))
}

fn snapshot_pruned_cargo_home(cargo_home: &Path) -> LauncherResult<Vec<SeedSnapshotRecord>> {
    const ENTRY_CAP: usize = 131_072;
    const CONTENT_CAP: u64 = 16 * 1024 * 1024 * 1024;

    let root = cap_std::fs::Dir::open_ambient_dir(cargo_home, cap_std::ambient_authority())
        .map_err(|_| "isolated Cargo snapshot root open failed".to_owned())?;
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

fn validate_pruned_cache(
    cargo_home: &Path,
    plan: &GuestResolutionCachePlanV1,
    input_snapshot: &[SeedSnapshotRecord],
    allow_sources: bool,
) -> LauncherResult<Vec<SeedSnapshotRecord>> {
    let current = snapshot_pruned_cargo_home(cargo_home)?;
    validate_pruned_cache_snapshot(plan, input_snapshot, &current, allow_sources)?;
    Ok(current)
}

fn validate_pruned_cache_snapshot(
    plan: &GuestResolutionCachePlanV1,
    input_snapshot: &[SeedSnapshotRecord],
    current: &[SeedSnapshotRecord],
    allow_sources: bool,
) -> LauncherResult<()> {
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
            .ok_or_else(|| "planned cache parent directory is missing".to_owned())?;
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
            return Err("planned cache input violates its type/hash/identity".to_owned());
        }
    }

    let current_by_path = current
        .iter()
        .map(|record| (record.relative_path.as_str(), record))
        .collect::<BTreeMap<_, _>>();
    if current_by_path.len() != current.len() {
        return Err("isolated cache snapshot contains duplicate paths".to_owned());
    }
    for input in input_snapshot {
        if current_by_path.get(input.relative_path.as_str()).copied() != Some(input) {
            return Err("planned cache input or parent changed identity/bytes".to_owned());
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
                || (record.kind == 2 && record.link_count != 1)
            {
                return Err("unpacked cache source has an invalid type or identity".to_owned());
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
            return Err("isolated Cargo home contains an unplanned entry".to_owned());
        }
    }
    Ok(())
}

fn insert_relative_parents(
    relative: &str,
    parents: &mut std::collections::BTreeSet<String>,
) -> LauncherResult<()> {
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

fn read_bounded_regular_nofollow(path: &Path, cap: usize) -> LauncherResult<Vec<u8>> {
    let mut options = OpenOptions::new();
    options.read(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.custom_flags(libc::O_NOFOLLOW);
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        const FILE_FLAG_OPEN_REPARSE_POINT: u32 = 0x0020_0000;
        options.custom_flags(FILE_FLAG_OPEN_REPARSE_POINT);
    }
    let mut file = options
        .open(path)
        .map_err(|_| "bounded no-follow file open failed".to_owned())?;
    let before = file
        .metadata()
        .map_err(|_| "bounded file metadata failed".to_owned())?;
    if !before.is_file() {
        return Err("bounded file is not regular".to_owned());
    }
    #[cfg(windows)]
    {
        use std::os::windows::fs::MetadataExt;
        const FILE_ATTRIBUTE_REPARSE_POINT: u32 = 0x400;
        if before.file_attributes() & FILE_ATTRIBUTE_REPARSE_POINT != 0 {
            return Err("bounded file is a Windows reparse point".to_owned());
        }
    }
    let declared = usize::try_from(before.len())
        .map_err(|_| "bounded file length does not fit memory".to_owned())?;
    if declared == 0 || declared > cap {
        return Err("bounded file length violates its cap".to_owned());
    }
    let read_cap = u64::try_from(cap)
        .ok()
        .and_then(|value| value.checked_add(1))
        .ok_or_else(|| "bounded file cap overflow".to_owned())?;
    let mut bytes = Vec::with_capacity(declared);
    (&mut file)
        .take(read_cap)
        .read_to_end(&mut bytes)
        .map_err(|_| "bounded file read failed".to_owned())?;
    if bytes.len() != declared {
        return Err("bounded file changed while reading".to_owned());
    }
    let mut tail = [0_u8; 1];
    if file
        .read(&mut tail)
        .map_err(|_| "bounded file EOF check failed".to_owned())?
        != 0
    {
        return Err("bounded file has trailing growth".to_owned());
    }
    let after = file
        .metadata()
        .map_err(|_| "bounded file metadata recheck failed".to_owned())?;
    if after.len() != before.len() {
        return Err("bounded file identity changed while reading".to_owned());
    }
    let canonical = path
        .canonicalize()
        .map_err(|_| "bounded file path identity failed".to_owned())?;
    if canonical != path {
        return Err("bounded file path is not canonical".to_owned());
    }
    Ok(bytes)
}

fn hash_bytes(bytes: &[u8]) -> LauncherResult<String> {
    Ok(Sha256Digest::hash_reader(Cursor::new(bytes))
        .map_err(|_| "byte hashing failed".to_owned())?
        .to_string())
}

fn validate_frozen_build_evidence(evidence: &GuestBuildEvidenceV1) -> LauncherResult<()> {
    if evidence.compiler_units.len() != FROZEN_COMPILER_UNIT_COUNT
        || evidence.build_scripts.len() != FROZEN_BUILD_SCRIPT_COUNT
        || evidence.guest_resolution_lock_sha256 != FROZEN_RESOLUTION_LOCK_SHA256
        || evidence.guest_resolution_cache_plan_sha256 != FROZEN_CACHE_PLAN_SHA256
    {
        return Err("guest build evidence differs from the frozen counts/digests".to_owned());
    }
    let canonical = serde_jcs::to_vec(evidence)
        .map_err(|_| "guest build evidence JCS serialization failed".to_owned())?;
    let mut framed = Vec::with_capacity(
        BUILD_EVIDENCE_DOMAIN
            .len()
            .checked_add(canonical.len())
            .ok_or_else(|| "guest build evidence framing overflow".to_owned())?,
    );
    framed.extend_from_slice(BUILD_EVIDENCE_DOMAIN);
    framed.extend_from_slice(&canonical);
    if hash_bytes(&framed)? != FROZEN_BUILD_EVIDENCE_SHA256 {
        return Err("guest build evidence differs from the frozen canonical golden".to_owned());
    }
    Ok(())
}

fn candidate_manifest(
    source: &ValidatedGuestSource,
    built: &BuiltGuest,
) -> LauncherResult<TrackedGuestManifestV1> {
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
        return Err("frozen source-tree allowlist did not contain exactly six files".to_owned());
    }
    let source_tree = source_tree_sha256(&source_members)
        .map_err(|_| "trusted source-tree hashing failed".to_owned())?;
    let dependency_graph = dependency_graph_sha256(&built.graph.records)
        .map_err(|_| "dependency record hashing failed".to_owned())?;
    let imports =
        imports_sha256(&built.imports).map_err(|_| "import record hashing failed".to_owned())?;
    let exports =
        exports_sha256(&built.exports).map_err(|_| "export record hashing failed".to_owned())?;
    Ok(TrackedGuestManifestV1 {
        schema: "heleos.pdf-guest-manifest/v1".to_owned(),
        protocol: PROTOCOL_VERSION.to_owned(),
        wasm_sha256: hash_bytes(&built.bytes)?,
        wasm_byte_length: u64::try_from(built.bytes.len())
            .map_err(|_| "guest length does not fit u64".to_owned())?,
        source_tree_sha256: source_tree,
        dependency_graph_sha256: dependency_graph,
        guest_resolution_lock_sha256: built.evidence.guest_resolution_lock_sha256.clone(),
        dependencies: built.graph.records.clone(),
        target: "wasm32-wasip1".to_owned(),
        profile: "release".to_owned(),
        rustc: "1.96.1".to_owned(),
        rust_path_remap: "heleos-rust-path-remap/v1".to_owned(),
        imports_sha256: imports,
        imports: built.imports.clone(),
        exports_sha256: exports,
        exports: built.exports.clone(),
        build_command: concat!(
            "cargo +1.96.1 build --locked -p heleos-pdf-guest --target wasm32-wasip1 ",
            "--release --message-format=json-render-diagnostics --quiet"
        )
        .to_owned(),
    })
}

fn render_manifest(manifest: &TrackedGuestManifestV1) -> LauncherResult<String> {
    let mut rendered = String::new();
    for (name, value) in [
        ("schema", manifest.schema.as_str()),
        ("protocol", manifest.protocol.as_str()),
        ("wasm_sha256", manifest.wasm_sha256.as_str()),
    ] {
        rendered.push_str(&format!("{name} = {}\n", toml_string(value)?));
    }
    rendered.push_str(&format!(
        "wasm_byte_length = {}\n",
        manifest.wasm_byte_length
    ));
    for (name, value) in [
        ("source_tree_sha256", manifest.source_tree_sha256.as_str()),
        (
            "dependency_graph_sha256",
            manifest.dependency_graph_sha256.as_str(),
        ),
        (
            "guest_resolution_lock_sha256",
            manifest.guest_resolution_lock_sha256.as_str(),
        ),
        ("target", manifest.target.as_str()),
        ("profile", manifest.profile.as_str()),
        ("rustc", manifest.rustc.as_str()),
        ("rust_path_remap", manifest.rust_path_remap.as_str()),
        ("imports_sha256", manifest.imports_sha256.as_str()),
        ("exports_sha256", manifest.exports_sha256.as_str()),
        ("build_command", manifest.build_command.as_str()),
    ] {
        rendered.push_str(&format!("{name} = {}\n", toml_string(value)?));
    }
    rendered.push_str("dependencies = [\n");
    for record in &manifest.dependencies {
        let features = render_string_array(&record.features)?;
        let edges = record
            .edges
            .iter()
            .map(|edge| {
                Ok(format!(
                    "{{ kind = {}, id = {} }}",
                    toml_string(match edge.kind {
                        DependencyKindV1::Normal => "normal",
                        DependencyKindV1::Build => "build",
                    })?,
                    toml_string(&edge.id)?
                ))
            })
            .collect::<LauncherResult<Vec<_>>>()?
            .join(", ");
        rendered.push_str(&format!(
            "  {{ id = {}, features = {features}, edges = [{edges}] }},\n",
            toml_string(&record.id)?
        ));
    }
    rendered.push_str("]\nimports = [\n");
    for record in &manifest.imports {
        rendered.push_str(&format!(
            "  {{ module = {}, name = {}, kind = \"func\", params = {}, results = {} }},\n",
            toml_string(&record.module)?,
            toml_string(&record.name)?,
            render_core_types(&record.params)?,
            render_core_types(&record.results)?
        ));
    }
    rendered.push_str("]\nexports = [\n");
    for record in &manifest.exports {
        rendered.push_str("  ");
        rendered.push_str(&render_export(record)?);
        rendered.push_str(",\n");
    }
    rendered.push_str("]\n");
    Ok(rendered)
}

fn render_export(record: &ExportRecordV1) -> LauncherResult<String> {
    Ok(match record {
        ExportRecordV1::Func {
            name,
            params,
            results,
        } => format!(
            "{{ kind = \"func\", name = {}, params = {}, results = {} }}",
            toml_string(name)?,
            render_core_types(params)?,
            render_core_types(results)?
        ),
        ExportRecordV1::Memory {
            name,
            minimum_pages,
            maximum_pages,
            memory64,
            shared,
            page_size_log2,
        } => {
            let maximum = maximum_pages
                .map(|value| format!(", maximum_pages = {value}"))
                .unwrap_or_default();
            let page_size = page_size_log2
                .map(|value| format!(", page_size_log2 = {value}"))
                .unwrap_or_default();
            format!(
                "{{ kind = \"memory\", name = {}, minimum_pages = {minimum_pages}{maximum}, memory64 = {memory64}, shared = {shared}{page_size} }}",
                toml_string(name)?
            )
        }
        ExportRecordV1::Table {
            name,
            element,
            minimum_elements,
            maximum_elements,
            table64,
        } => {
            let maximum = maximum_elements
                .map(|value| format!(", maximum_elements = {value}"))
                .unwrap_or_default();
            format!(
                "{{ kind = \"table\", name = {}, element = {}, minimum_elements = {minimum_elements}{maximum}, table64 = {table64} }}",
                toml_string(name)?,
                toml_string(core_type_name(*element))?
            )
        }
    })
}

fn render_core_types(values: &[CoreValueTypeV1]) -> LauncherResult<String> {
    render_string_array(
        &values
            .iter()
            .map(|value| core_type_name(*value).to_owned())
            .collect::<Vec<_>>(),
    )
}

const fn core_type_name(value: CoreValueTypeV1) -> &'static str {
    match value {
        CoreValueTypeV1::I32 => "i32",
        CoreValueTypeV1::I64 => "i64",
        CoreValueTypeV1::F32 => "f32",
        CoreValueTypeV1::F64 => "f64",
        CoreValueTypeV1::V128 => "v128",
        CoreValueTypeV1::Funcref => "funcref",
        CoreValueTypeV1::Externref => "externref",
    }
}

fn render_string_array(values: &[String]) -> LauncherResult<String> {
    Ok(format!(
        "[{}]",
        values
            .iter()
            .map(|value| toml_string(value))
            .collect::<LauncherResult<Vec<_>>>()?
            .join(", ")
    ))
}

fn toml_string(value: &str) -> LauncherResult<String> {
    serde_json::to_string(value).map_err(|_| "manifest string escaping failed".to_owned())
}

fn publish_generated_guest(
    source_workspace: &Path,
    candidate: &[u8],
    first_policy: &ArtifactBuildPolicyV1,
    second_policy: &ArtifactBuildPolicyV1,
) -> LauncherResult<()> {
    let (destination, directory) = open_generated_artifact_directory(source_workspace)?;
    publish_candidate_no_replace(
        &destination,
        &directory,
        candidate,
        PublicationFaults::default(),
        |bytes| validate_published_guest(bytes, candidate, first_policy, second_policy),
    )
}

#[derive(Clone, Copy, Debug, Default)]
struct PublicationFaults {
    fail_after_link: bool,
    fail_cleanup: bool,
    substitute_after_link: bool,
}

fn open_generated_artifact_directory(
    source_workspace: &Path,
) -> LauncherResult<(PathBuf, cap_std::fs::Dir)> {
    use cap_fs_ext::DirExt;

    let mut directory =
        cap_std::fs::Dir::open_ambient_dir(source_workspace, cap_std::ambient_authority())
            .map_err(|_| "generated artifact workspace capability open failed".to_owned())?;
    let mut destination = source_workspace.to_path_buf();
    for component in ["target", "wasm32-wasip1", "release"] {
        directory = match directory.open_dir_nofollow(component) {
            Ok(child) => child,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                directory.create_dir(component).map_err(|_| {
                    "generated artifact directory component creation failed".to_owned()
                })?;
                directory
                    .open_dir_nofollow(component)
                    .map_err(|_| "generated artifact directory component open failed".to_owned())?
            }
            Err(_) => {
                return Err("generated artifact directory component is indirect".to_owned());
            }
        };
        destination.push(component);
    }
    if destination
        .canonicalize()
        .map_err(|_| "generated artifact directory identity failed".to_owned())?
        != destination
    {
        return Err("generated artifact directory path is not canonical".to_owned());
    }
    Ok((destination, directory))
}

fn publish_candidate_no_replace(
    destination: &Path,
    directory: &cap_std::fs::Dir,
    candidate: &[u8],
    faults: PublicationFaults,
    validate: impl Fn(&[u8]) -> LauncherResult<()>,
) -> LauncherResult<()> {
    use cap_fs_ext::{FollowSymlinks, MetadataExt, OpenOptionsFollowExt};

    if candidate.is_empty() || candidate.len() > MAX_GUEST_WASM_BYTES {
        return Err("generated artifact candidate violates its size bound".to_owned());
    }
    #[cfg(not(unix))]
    let _ = faults.substitute_after_link;
    let final_name = "heleos_pdf_guest.wasm";
    let staging_name = format!(
        ".heleos_pdf_guest.{}.partial",
        uuid::Uuid::new_v4().simple()
    );
    let mut staging_created = false;
    let operation = (|| {
        let mut options = cap_std::fs::OpenOptions::new();
        options
            .write(true)
            .create_new(true)
            .follow(FollowSymlinks::No);
        let mut staging = directory
            .open_with(&staging_name, &options)
            .map_err(|_| "generated artifact staging create-new failed".to_owned())?;
        staging_created = true;
        staging
            .write_all(candidate)
            .and_then(|_| staging.flush())
            .and_then(|_| staging.sync_all())
            .map_err(|_| "generated artifact staging write/sync failed".to_owned())?;
        drop(staging);
        let staged = read_cap_relative_file(directory, &staging_name, MAX_GUEST_WASM_BYTES)?;
        if staged != candidate {
            return Err("generated artifact staging bytes differ".to_owned());
        }
        validate(&staged)?;

        let published_new = match directory.hard_link(&staging_name, directory, final_name) {
            Ok(()) => true,
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => false,
            Err(_) => return Err("generated artifact no-replace publication failed".to_owned()),
        };
        if published_new {
            sync_directory(destination)?;
        }
        #[cfg(unix)]
        if faults.substitute_after_link {
            let moved = destination.with_extension("substituted-original");
            fs::rename(destination, &moved)
                .map_err(|_| "injected publication namespace rename failed".to_owned())?;
            fs::create_dir(destination)
                .map_err(|_| "injected publication namespace replacement failed".to_owned())?;
        }
        if faults.fail_after_link {
            return Err("injected generated artifact post-link failure".to_owned());
        }
        let winner = read_cap_relative_file(directory, final_name, MAX_GUEST_WASM_BYTES)?;
        if winner != candidate {
            return Err("existing generated artifact differs and was left untouched".to_owned());
        }
        validate(&winner)?;
        directory
            .remove_file(&staging_name)
            .map_err(|_| "generated artifact staging cleanup failed".to_owned())?;
        staging_created = false;
        sync_directory(destination)?;
        let reopened_directory =
            cap_std::fs::Dir::open_ambient_dir(destination, cap_std::ambient_authority())
                .map_err(|_| "generated artifact directory reopen failed".to_owned())?;
        let retained_marker = directory
            .dir_metadata()
            .map_err(|_| "generated artifact retained directory metadata failed".to_owned())?;
        let reopened_marker = reopened_directory
            .dir_metadata()
            .map_err(|_| "generated artifact reopened directory metadata failed".to_owned())?;
        if retained_marker.dev() != reopened_marker.dev()
            || retained_marker.ino() != reopened_marker.ino()
        {
            return Err("generated artifact publication directory was substituted".to_owned());
        }
        let reopened =
            read_cap_relative_file(&reopened_directory, final_name, MAX_GUEST_WASM_BYTES)?;
        if reopened != candidate {
            return Err("reopened generated artifact differs".to_owned());
        }
        validate(&reopened)
    })();

    if staging_created {
        let cleanup = if faults.fail_cleanup {
            Err("injected generated artifact cleanup failure".to_owned())
        } else {
            directory
                .remove_file(&staging_name)
                .map_err(|_| "generated artifact error-path staging cleanup failed".to_owned())
                .and_then(|()| sync_directory(destination))
        };
        if cleanup.is_err() {
            return Err("generated artifact error-path cleanup failed".to_owned());
        }
    }
    operation
}

fn validate_published_guest(
    bytes: &[u8],
    candidate: &[u8],
    first_policy: &ArtifactBuildPolicyV1,
    second_policy: &ArtifactBuildPolicyV1,
) -> LauncherResult<()> {
    if bytes != candidate {
        return Err("existing generated artifact differs and was left untouched".to_owned());
    }
    validate_pdf_guest_module_policy_v1(bytes)
        .map_err(|_| "published guest module policy validation failed".to_owned())?;
    verify_no_physical_prefixes_v1(bytes, first_policy)
        .and_then(|_| verify_no_physical_prefixes_v1(bytes, second_policy))
        .map_err(|_| "published guest contains a physical build prefix".to_owned())
}

fn sync_directory(path: &Path) -> LauncherResult<()> {
    let directory = File::open(path).map_err(|_| "directory sync open failed".to_owned())?;
    match directory.sync_all() {
        Ok(()) => Ok(()),
        #[cfg(windows)]
        Err(error) if matches!(error.raw_os_error(), Some(1 | 5 | 6)) => Ok(()),
        Err(_) => Err("directory sync failed".to_owned()),
    }
}
