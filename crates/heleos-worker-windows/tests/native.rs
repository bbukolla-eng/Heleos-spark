#![cfg(windows)]
#![forbid(unsafe_code)]

use heleos_worker_windows::{CommandSpec, Error, Outcome, WritableRoots, execute};
use std::{
    io::{Read, Write},
    path::{Path, PathBuf},
    process::Command,
    time::{Duration, Instant},
};

fn fixture() -> (tempfile::TempDir, WritableRoots) {
    let base = tempfile::tempdir().unwrap();
    let base_path = base.path().canonicalize().unwrap();
    let paths = [
        base_path.join("checkout & literal"),
        base_path.join("home"),
        base_path.join("tmp"),
    ];
    for path in &paths {
        std::fs::create_dir(path).unwrap();
    }
    let roots = WritableRoots::prepare(&paths[0], &paths[1], &paths[2]).unwrap();
    (base, roots)
}

fn launch(
    roots: WritableRoots,
    mode: &str,
    mut env: Vec<(String, String)>,
    timeout: Duration,
    limit: usize,
) -> Result<Outcome, Error> {
    env.push(("WORKER_TEST_MODE".into(), mode.into()));
    for (key, path) in ["WORKER_CHECKOUT", "WORKER_HOME", "WORKER_TMP"]
        .iter()
        .zip(roots.paths())
    {
        env.push(((*key).into(), path.to_str().unwrap().into()));
    }
    let spec = CommandSpec::new(
        std::env::current_exe().unwrap().canonicalize().unwrap(),
        vec![
            "--exact".into(),
            "provider_fixture".into(),
            "--nocapture".into(),
            "--test-threads=1".into(),
        ],
        roots.paths()[0].clone(),
        env,
        Instant::now() + timeout,
        limit,
        roots,
    )?;
    execute(spec, b"literal stdin\0bytes".to_vec())
}

// Break caught: missing restricted-SID write checks, missing root inheritance,
// or granting the run root lets the real child modify protected evidence.
#[test]
fn native_containment_write_boundary_and_inherited_acl() {
    let (base, roots) = fixture();
    let base_path = base.path().canonicalize().unwrap();
    let outside = base_path.join("outside");
    let evidence = base_path.join("assignment.json");
    std::fs::write(&outside, b"outside frozen").unwrap();
    std::fs::write(&evidence, b"evidence frozen").unwrap();
    for path in roots.paths() {
        std::fs::create_dir(path.join("inherited")).unwrap();
    }
    let paths = roots.paths().clone();
    let result = launch(
        roots,
        "writes",
        vec![
            ("WORKER_OUTSIDE".into(), outside.to_str().unwrap().into()),
            ("WORKER_EVIDENCE".into(), evidence.to_str().unwrap().into()),
        ],
        Duration::from_secs(10),
        4096,
    )
    .unwrap();
    assert_eq!(result.exit_code, 0, "{:?}", result);
    assert!(!result.timed_out);
    for path in paths {
        assert_eq!(
            std::fs::read(path.join("inherited/allowed")).unwrap(),
            b"allowed"
        );
    }
    assert_eq!(std::fs::read(&outside).unwrap(), b"outside frozen");
    assert_eq!(std::fs::read(&evidence).unwrap(), b"evidence frozen");
}

// Break caught: waiting for pipes before enforcing the deadline deadlocks on a
// noisy provider, or failing to kill the job leaves its descendant alive.
#[test]
fn native_containment_timeout_kills_descendant_and_drains_output() {
    let (_base, roots) = fixture();
    let sentinel = roots.paths()[0].join("descendant-survived");
    let started = Instant::now();
    let result = launch(
        roots,
        "timeout",
        vec![("WORKER_SENTINEL".into(), sentinel.to_str().unwrap().into())],
        Duration::from_secs(2),
        256,
    )
    .unwrap();
    assert!(result.timed_out);
    assert!(started.elapsed() < Duration::from_secs(10));
    assert!(result.stdout.truncated);
    assert!(result.stderr.truncated);
    assert_eq!(result.stdout.bytes.len(), 256);
    assert_eq!(result.stderr.bytes.len(), 256);
    assert!(
        sentinel.with_extension("started").is_file(),
        "descendant must actually start"
    );
    std::thread::sleep(Duration::from_secs(4));
    assert!(!sentinel.exists(), "descendant escaped job teardown");
}

#[test]
fn native_containment_provider_exit_kills_lingering_descendant() {
    let (_base, roots) = fixture();
    let sentinel = roots.paths()[0].join("descendant-survived");
    let result = launch(
        roots,
        "exit-parent",
        vec![("WORKER_SENTINEL".into(), sentinel.to_str().unwrap().into())],
        Duration::from_secs(10),
        4096,
    )
    .unwrap();
    assert_eq!(result.exit_code, 0);
    assert!(!result.timed_out);
    assert!(
        sentinel.with_extension("started").is_file(),
        "descendant must actually start"
    );
    std::thread::sleep(Duration::from_secs(4));
    assert!(!sentinel.exists());
}

#[test]
fn native_containment_reparse_escape_denied() {
    let (base, roots) = fixture();
    let outside = base
        .path()
        .canonicalize()
        .unwrap()
        .join("outside-directory");
    std::fs::create_dir(&outside).unwrap();
    // Requires Windows Developer Mode or SeCreateSymbolicLinkPrivilege. Missing
    // privilege fails this native gate; it is never silently skipped.
    std::os::windows::fs::symlink_dir(&outside, roots.paths()[0].join("escape")).unwrap();
    let result = launch(roots, "escape", vec![], Duration::from_secs(10), 4096).unwrap();
    assert_eq!(result.exit_code, 0, "{result:?}");
    assert!(!outside.join("escape-write").exists());
}

#[test]
fn native_containment_rejects_reparse_roots_before_launch() {
    let (base, roots) = fixture();
    let link = base.path().canonicalize().unwrap().join("root-link");
    std::os::windows::fs::symlink_dir(&roots.paths()[0], &link).unwrap();
    assert!(WritableRoots::prepare(&link, &roots.paths()[1], &roots.paths()[2]).is_err());
}

// This is a real subprocess fixture using this test executable, never a shell.
#[test]
#[allow(clippy::zombie_processes)] // Intentional adversary: Job owner must reap its tree after this parent exits.
fn provider_fixture() {
    let Ok(mode) = std::env::var("WORKER_TEST_MODE") else {
        return;
    };
    let checkout = PathBuf::from(std::env::var_os("WORKER_CHECKOUT").unwrap());
    match mode.as_str() {
        "writes" => {
            for key in ["WORKER_CHECKOUT", "WORKER_HOME", "WORKER_TMP"] {
                std::fs::write(
                    PathBuf::from(std::env::var_os(key).unwrap()).join("inherited/allowed"),
                    b"allowed",
                )
                .unwrap();
            }
            for key in ["WORKER_OUTSIDE", "WORKER_EVIDENCE"] {
                assert_eq!(
                    std::fs::write(std::env::var_os(key).unwrap(), b"tampered")
                        .unwrap_err()
                        .kind(),
                    std::io::ErrorKind::PermissionDenied
                );
            }
            let mut stdin = Vec::new();
            std::io::stdin().read_to_end(&mut stdin).unwrap();
            assert_eq!(stdin, b"literal stdin\0bytes");
        }
        "escape" => assert_eq!(
            std::fs::write(checkout.join("escape/escape-write"), b"bad")
                .unwrap_err()
                .kind(),
            std::io::ErrorKind::PermissionDenied
        ),
        "descendant" => {
            let sentinel = PathBuf::from(std::env::var_os("WORKER_SENTINEL").unwrap());
            std::fs::write(sentinel.with_extension("started"), b"started").unwrap();
            std::thread::sleep(Duration::from_secs(4));
            std::fs::write(
                Path::new(&std::env::var_os("WORKER_SENTINEL").unwrap()),
                b"survived",
            )
            .unwrap();
        }
        "timeout" | "exit-parent" => {
            let mut child = Command::new(std::env::current_exe().unwrap())
                .args([
                    "--exact",
                    "provider_fixture",
                    "--nocapture",
                    "--test-threads=1",
                ])
                .env("WORKER_TEST_MODE", "descendant")
                .spawn()
                .unwrap();
            let marker = PathBuf::from(std::env::var_os("WORKER_SENTINEL").unwrap())
                .with_extension("started");
            let deadline = Instant::now() + Duration::from_secs(1);
            while !marker.exists() {
                assert!(Instant::now() < deadline, "descendant failed to start");
                std::thread::sleep(Duration::from_millis(5));
            }
            if mode == "timeout" {
                for _ in 0..128 {
                    std::io::stdout().write_all(&[b'o'; 8192]).unwrap();
                    std::io::stderr().write_all(&[b'e'; 8192]).unwrap();
                }
                child.wait().unwrap();
                std::thread::sleep(Duration::from_secs(30));
            }
        }
        _ => panic!("unknown mode"),
    }
}
