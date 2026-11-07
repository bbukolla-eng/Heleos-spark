#![forbid(unsafe_code)]

//! Black-box receipt contract; synthetic transcripts are not native execution evidence.

use std::{
    fs,
    io::Cursor,
    path::Path,
    process::{Command, Output},
};

use heleos_core::Sha256Digest;
use serde_json::{Value, json};

const WINDOWS_STORE_SENTINEL: &str =
    "store::tests::windows_checked_close_releases_handles_in_three_real_rename_phases";

struct FixtureResult {
    output: Output,
    candidate_sha: String,
    manifest_bytes: Vec<u8>,
    transcript_bytes: Vec<(String, Vec<u8>, Vec<u8>)>,
}

fn sha256(bytes: &[u8]) -> String {
    Sha256Digest::hash_reader(Cursor::new(bytes))
        .unwrap()
        .to_string()
}

fn verify_seven_suite_transcripts(
    core_store_test_name: &str,
    mutate: impl FnOnce(&mut Value, &Path),
) -> FixtureResult {
    let root = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .unwrap();
    let head = Command::new("git")
        .args(["rev-parse", "HEAD"])
        .current_dir(&root)
        .output()
        .unwrap();
    assert!(head.status.success(), "git HEAD must resolve");
    let candidate_sha = String::from_utf8(head.stdout).unwrap().trim().to_owned();
    assert_eq!(candidate_sha.len(), 40);

    let evidence = tempfile::tempdir().unwrap();
    let evidence_root = evidence.path().canonicalize().unwrap();
    let cases: [(&str, &[&str], &str); 7] = [
        (
            "core-backup-restore",
            &["-p", "heleos-core", "--test", "backup_restore"],
            "native_backup_restore_round_trip",
        ),
        (
            "core-store",
            &["-p", "heleos-core", "--lib", "store::tests"],
            core_store_test_name,
        ),
        (
            "core-backup",
            &["-p", "heleos-core", "--lib", "backup::tests"],
            "backup::tests::native_authenticated_restore",
        ),
        (
            "platform-fs",
            &["-p", "heleos-platform-fs", "--lib"],
            "tests::native_platform_file_lifecycle",
        ),
        (
            "cli-unit",
            &["-p", "heleos-cli", "--bin", "heleos"],
            "tests::native_cli_argument_contract",
        ),
        (
            "cli-integration",
            &["-p", "heleos-cli", "--test", "cli"],
            "native_cli_recovery_round_trip",
        ),
        (
            "workspace-all",
            &["--workspace", "--all-targets", "--all-features"],
            "native_workspace_feature_contract",
        ),
    ];
    let mut suites = Vec::new();
    for (id, selectors, test_name) in cases {
        let mut run_argv = vec!["cargo", "+1.96.1", "test", "--frozen"];
        run_argv.extend_from_slice(selectors);
        let mut list_argv = run_argv.clone();
        list_argv.extend_from_slice(&["--", "--list"]);
        let list_path = format!("{id}.list.txt");
        let run_path = format!("{id}.run.txt");
        fs::write(
            evidence_root.join(&list_path),
            format!("{test_name}: test\n\n1 test, 0 benchmarks\n"),
        )
        .unwrap();
        fs::write(
            evidence_root.join(&run_path),
            format!(
                "\nrunning 1 test\ntest {test_name} ... ok\n\n\
                 test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s\n"
            ),
        )
        .unwrap();
        suites.push(json!({
            "id": id,
            "run_argv": run_argv,
            "list_argv": list_argv,
            "list_exit_code": 0,
            "run_exit_code": 0,
            "list_path": list_path,
            "run_path": run_path,
        }));
    }
    let mut manifest = json!({
        "schema": "heleos.native-suite-transcripts/v1",
        "candidate_sha": candidate_sha,
        "platform": "windows-x86_64",
        "filesystem": "NTFS",
        "suites": suites,
    });
    mutate(&mut manifest, &evidence_root);
    let manifest_path = evidence_root.join("manifest.json");
    fs::write(&manifest_path, serde_json::to_vec(&manifest).unwrap()).unwrap();
    assert!(manifest_path.is_absolute());
    let manifest_bytes = fs::read(&manifest_path).unwrap();
    let transcript_bytes = manifest["suites"]
        .as_array()
        .unwrap()
        .iter()
        .map(|suite| {
            (
                suite["id"].as_str().unwrap().to_owned(),
                fs::read(evidence_root.join(suite["list_path"].as_str().unwrap())).unwrap(),
                fs::read(evidence_root.join(suite["run_path"].as_str().unwrap())).unwrap(),
            )
        })
        .collect();

    let output = Command::new(env!("CARGO_BIN_EXE_verify-provenance"))
        .arg("verify-native-suite")
        .arg(&manifest_path)
        .current_dir(&root)
        .output()
        .unwrap();
    FixtureResult {
        output,
        candidate_sha,
        manifest_bytes,
        transcript_bytes,
    }
}

#[test]
fn complete_native_suite_transcript_produces_bound_receipt() {
    // Missing dispatch, incorrect transcript totals, or a receipt bound to the
    // wrong candidate/platform must fail this operator-visible contract.
    let FixtureResult {
        output,
        candidate_sha,
        ..
    } = verify_seven_suite_transcripts(WINDOWS_STORE_SENTINEL, |_, _| {});
    assert!(
        output.status.success(),
        "complete native-suite transcripts must produce a passing receipt; exit={:?}; stdout={}; stderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr),
    );
    let receipt: Value = serde_json::from_slice(&output.stdout).unwrap();
    assert_eq!(receipt["status"], "pass");
    assert_eq!(receipt["candidate_sha"], candidate_sha);
    assert_eq!(receipt["platform"], "windows-x86_64");
    assert_eq!(receipt["filesystem"], "NTFS");
    assert_eq!(receipt["suite_count"], 7);
    assert_eq!(receipt["total_listed"], 7);
    assert_eq!(receipt["total_passed"], 7);
    let mut canonical = serde_jcs::to_vec(&receipt).unwrap();
    canonical.push(b'\n');
    assert_eq!(output.stdout, canonical, "receipt must be canonical JSON");
}

#[test]
fn missing_windows_store_lifecycle_sentinel_is_rejected_with_matching_counts() {
    // Both core-store transcripts name the same harmless substitute, so every
    // suite still exits zero with exactly one listed and one passing test.
    // Totals alone cannot establish that Windows handle release was exercised.
    let FixtureResult { output, .. } =
        verify_seven_suite_transcripts("store::tests::native_store_opens_database", |_, _| {});
    assert!(
        !output.status.success(),
        "native-suite transcripts missing the exact Windows Store lifecycle sentinel must be rejected even with matching counts; exit={:?}; stdout={}; stderr={}",
        output.status.code(),
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr),
    );
}

#[test]
fn native_suite_receipt_rejects_changes_to_exact_ordered_suite_matrix() {
    // Every case retains successful transcripts and the exact Store sentinel.
    // Accepting a changed ID, command, order, or basename would attest execution
    // of a different matrix even though all listed/passed totals still agree.
    let mut baseline = Value::Null;
    let FixtureResult { output, .. } =
        verify_seven_suite_transcripts(WINDOWS_STORE_SENTINEL, |manifest, _| {
            baseline = manifest.clone();
        });
    assert!(output.status.success(), "unmodified matrix must pass");

    struct Case {
        name: String,
        manifest: Value,
        alias: Option<(String, String)>,
    }

    let mut cases = Vec::new();
    for (index, suite) in baseline["suites"].as_array().unwrap().iter().enumerate() {
        let id = suite["id"].as_str().unwrap();
        let mut renamed = baseline.clone();
        renamed["suites"][index]["id"] = json!(format!("{id}-substitute"));
        cases.push(Case {
            name: format!("{id}: changed suite ID with original commands and files"),
            manifest: renamed,
            alias: None,
        });

        for field in ["run_argv", "list_argv"] {
            let argv = suite[field].as_array().unwrap();
            // Bind every token, including executable, pinned toolchain, test
            // subcommand, frozen resolution, selectors, and list-only suffix.
            for (token_index, token) in argv.iter().enumerate() {
                let mut changed = baseline.clone();
                changed["suites"][index][field][token_index] = if token == "--frozen" {
                    json!("--locked")
                } else {
                    json!("unexpected-token")
                };
                cases.push(Case {
                    name: format!("{id}: {field}[{token_index}] changed from {token}"),
                    manifest: changed,
                    alias: None,
                });
            }
            for append in [false, true] {
                let mut changed = baseline.clone();
                let changed_argv = changed["suites"][index][field].as_array_mut().unwrap();
                if append {
                    changed_argv.push(json!("unexpected-extra-token"));
                } else {
                    changed_argv.pop().unwrap();
                }
                cases.push(Case {
                    name: format!(
                        "{id}: {field} {} token",
                        if append { "extra" } else { "missing" }
                    ),
                    manifest: changed,
                    alias: None,
                });
            }
        }

        for field in ["list_path", "run_path"] {
            let original = suite[field].as_str().unwrap().to_owned();
            let alias = format!("alias-{original}");
            let mut changed = baseline.clone();
            changed["suites"][index][field] = json!(alias);
            cases.push(Case {
                name: format!("{id}: {field} uses a same-byte alias basename"),
                manifest: changed,
                alias: Some((original, alias)),
            });
        }
    }
    let mut swapped = baseline.clone();
    swapped["suites"].as_array_mut().unwrap().swap(3, 4);
    cases.push(Case {
        name: "platform-fs and cli-unit: swapped complete non-core records".to_owned(),
        manifest: swapped,
        alias: None,
    });

    let mut accepted = Vec::new();
    for case in cases {
        let FixtureResult { output, .. } =
            verify_seven_suite_transcripts(WINDOWS_STORE_SENTINEL, |manifest, evidence| {
                *manifest = case.manifest;
                if let Some((original, alias)) = case.alias {
                    fs::copy(evidence.join(&original), evidence.join(&alias)).unwrap();
                    assert_eq!(
                        fs::read(evidence.join(original)).unwrap(),
                        fs::read(evidence.join(alias)).unwrap()
                    );
                }
            });
        if output.status.success() {
            accepted.push(format!(
                "{}; exit={:?}; stdout={}; stderr={}",
                case.name,
                output.status.code(),
                String::from_utf8_lossy(&output.stdout),
                String::from_utf8_lossy(&output.stderr),
            ));
        }
    }
    assert!(
        accepted.is_empty(),
        "every changed matrix must be rejected; accepted {} malformed cases:\n{}",
        accepted.len(),
        accepted.join("\n"),
    );
}

#[test]
fn native_suite_receipt_binds_exact_manifest_and_ordered_transcript_bytes() {
    // A receipt that omits source digests or reorders suite records cannot be
    // checked against the exact files supplied to the verifier.
    let fixture = verify_seven_suite_transcripts(WINDOWS_STORE_SENTINEL, |_, _| {});
    assert!(
        fixture.output.status.success(),
        "complete fixture must pass"
    );
    let receipt: Value = serde_json::from_slice(&fixture.output.stdout).unwrap();
    let expected_suites: Vec<Value> = fixture
        .transcript_bytes
        .iter()
        .map(|(id, list, run)| {
            json!({
                "id": id,
                "list_sha256": sha256(list),
                "run_sha256": sha256(run),
                "listed": 1,
                "passed": 1,
            })
        })
        .collect();
    assert_eq!(receipt["schema"], "heleos.native-suite-receipt/v1");
    assert_eq!(receipt["manifest_sha256"], sha256(&fixture.manifest_bytes));
    assert_eq!(receipt["suites"], json!(expected_suites));
    let mut canonical = serde_jcs::to_vec(&receipt).unwrap();
    canonical.push(b'\n');
    assert_eq!(
        fixture.output.stdout, canonical,
        "receipt must be canonical JSON"
    );
}

struct TranscriptCase {
    name: &'static str,
    list: String,
    run: String,
}

fn list_transcript(names: &[&str]) -> String {
    let lines: String = names.iter().map(|name| format!("{name}: test\n")).collect();
    let noun = if names.len() == 1 { "test" } else { "tests" };
    format!("{lines}\n{} {noun}, 0 benchmarks\n", names.len())
}

fn run_transcript(names: &[&str]) -> String {
    let lines: String = names
        .iter()
        .map(|name| format!("test {name} ... ok\n"))
        .collect();
    let noun = if names.len() == 1 { "test" } else { "tests" };
    format!(
        "\nrunning {} {noun}\n{lines}\n\
         test result: ok. {} passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.01s\n",
        names.len(),
        names.len(),
    )
}

fn assert_transcript_cases_rejected(cases: Vec<TranscriptCase>) {
    let mut accepted = Vec::new();
    for case in cases {
        let FixtureResult { output, .. } =
            verify_seven_suite_transcripts(WINDOWS_STORE_SENTINEL, |_, evidence| {
                // A non-core suite leaves the exact Store sentinel intact.
                // Manifest commands, basenames, order, and zero exits also stay valid.
                fs::write(evidence.join("platform-fs.list.txt"), &case.list).unwrap();
                fs::write(evidence.join("platform-fs.run.txt"), &case.run).unwrap();
            });
        if output.status.success() {
            accepted.push(format!(
                "{}; exit={:?}; stdout={}; stderr={}",
                case.name,
                output.status.code(),
                String::from_utf8_lossy(&output.stdout),
                String::from_utf8_lossy(&output.stderr),
            ));
        }
    }
    assert!(
        accepted.is_empty(),
        "malformed transcripts must reject; accepted {} cases:\n{}",
        accepted.len(),
        accepted.join("\n"),
    );
}

#[test]
fn native_suite_receipt_rejects_different_test_name_multisets() {
    // Count-only comparison misses both substituted names and unequal duplicate
    // frequencies, even when the distinct name sets and summary totals agree.
    assert_transcript_cases_rejected(vec![
        TranscriptCase {
            name: "equal counts with a different passed test name",
            list: list_transcript(&["tests::alpha"]),
            run: run_transcript(&["tests::beta"]),
        },
        TranscriptCase {
            name: "equal counts and name sets with unequal duplicate frequencies",
            list: list_transcript(&["tests::alpha", "tests::alpha", "tests::beta"]),
            run: run_transcript(&["tests::alpha", "tests::beta", "tests::beta"]),
        },
    ]);
}

#[test]
fn native_suite_receipt_rejects_failed_or_ignored_test_lines_despite_matching_ok_counts() {
    // A parser that only extracts ok lines silently discards reported failures
    // and ignores. Keep the summary claiming success to isolate this defect.
    let names = &["tests::alpha"];
    let cases = [
        ("failed test line", "test tests::hidden ... FAILED\n"),
        ("ignored test line", "test tests::hidden ... ignored\n"),
        (
            "ignored test line with reason",
            "test tests::hidden ... ignored, requires unavailable device\n",
        ),
    ]
    .into_iter()
    .map(|(name, extra)| TranscriptCase {
        name,
        list: list_transcript(names),
        run: run_transcript(names).replace("\ntest result:", &format!("\n{extra}test result:")),
    })
    .collect();
    assert_transcript_cases_rejected(cases);
}

#[test]
fn native_suite_receipt_rejects_forged_or_missing_transcript_summaries() {
    // Parsed list/ok lines still match in every case. A successful manifest
    // exit cannot excuse inconsistent totals or nonzero failure/ignore/measure counts.
    let names = &["tests::alpha"];
    let list = list_transcript(names);
    let run = run_transcript(names);
    let mut cases = Vec::new();
    for (name, from, to) in [
        ("inflated list test total", "1 test,", "2 tests,"),
        ("zero list test total", "1 test,", "0 tests,"),
        ("nonzero list benchmarks", "0 benchmarks", "1 benchmarks"),
        ("missing list summary", "1 test, 0 benchmarks\n", ""),
    ] {
        cases.push(TranscriptCase {
            name,
            list: list.replace(from, to),
            run: run.clone(),
        });
    }
    for (name, from, to) in [
        (
            "inflated running total",
            "running 1 test",
            "running 2 tests",
        ),
        ("inflated passed total", "1 passed;", "2 passed;"),
        ("zero passed total", "1 passed;", "0 passed;"),
        ("nonzero failed total", "0 failed;", "1 failed;"),
        ("nonzero ignored total", "0 ignored;", "1 ignored;"),
        ("nonzero measured total", "0 measured;", "1 measured;"),
        (
            "failed result label",
            "test result: ok.",
            "test result: FAILED.",
        ),
    ] {
        cases.push(TranscriptCase {
            name,
            list: list.clone(),
            run: run.replace(from, to),
        });
    }
    cases.push(TranscriptCase {
        name: "missing run result summary",
        list,
        run: "\nrunning 1 test\ntest tests::alpha ... ok\n".to_owned(),
    });
    assert_transcript_cases_rejected(cases);
}
