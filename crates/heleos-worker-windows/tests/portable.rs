use heleos_worker_windows::{CommandSpec, WritableRoots};
use heleos_worker_windows::{Error, capture, command_line, environment_block};
use std::{
    path::Path,
    time::{Duration, Instant},
};

fn strings(values: &[&str]) -> Vec<String> {
    values.iter().map(|s| (*s).into()).collect()
}

// Break caught: dropping empty arguments, interpreting metacharacters, or failing
// to double a backslash before a quote/closing delimiter changes argv.
#[test]
fn literal_microsoft_quoting_vectors() {
    for (input, expected) in [
        ("", "\"\"\0"),
        ("a", "\"a\"\0"),
        ("a b", "\"a b\"\0"),
        ("a\tb", "\"a\tb\"\0"),
        ("a\"b", "\"a\\\"b\"\0"),
        ("a\\", "\"a\\\\\"\0"),
        ("a\\\"b", "\"a\\\\\\\"b\"\0"),
        ("a\\\\b", "\"a\\\\b\"\0"),
        ("&|<>^%!", "\"&|<>^%!\"\0"),
    ] {
        assert_eq!(
            command_line(&strings(&[input])).unwrap(),
            expected.encode_utf16().collect::<Vec<_>>(),
            "{input:?}"
        );
    }
    assert_eq!(
        command_line(&strings(&["C:\\a b\\app.exe", "", "x"])).unwrap(),
        "\"C:\\a b\\app.exe\" \"\" \"x\"\0"
            .encode_utf16()
            .collect::<Vec<_>>()
    );
}

// Break caught: accepting NUL truncates what Windows executes, or an oversized
// command silently differs from its validated representation.
#[test]
fn command_line_rejects_nul_empty_program_and_oversize() {
    for args in [vec![], strings(&["a\0b"]), vec!["x".repeat(32767)]] {
        assert_eq!(command_line(&args), Err(Error::InvalidInput));
    }
}

// Break caught: ambient case aliases, unsorted entries, or a missing second NUL
// change the child's environment block.
#[test]
fn environment_is_sorted_and_double_terminated() {
    let env = vec![
        ("z".into(), "two=2".into()),
        ("A".into(), "1".into()),
        ("empty".into(), "".into()),
    ];
    assert_eq!(
        environment_block(&env).unwrap(),
        "A=1\0empty=\0z=two=2\0\0"
            .encode_utf16()
            .collect::<Vec<_>>()
    );
    assert_eq!(environment_block(&[]).unwrap(), vec![0, 0]);
}

#[test]
fn environment_rejects_aliases_and_malformed_entries() {
    for env in [
        vec![("PATH".into(), "1".into()), ("Path".into(), "2".into())],
        vec![("".into(), "1".into())],
        vec![("=X".into(), "1".into())],
        vec![("A\0".into(), "1".into())],
        vec![("A".into(), "1\0".into())],
        vec![("A".into(), "x".repeat(32767))],
    ] {
        assert_eq!(environment_block(&env), Err(Error::InvalidInput));
    }
}

// Break caught: stopping reads at the cap loses byte counts and can deadlock a
// provider with full pipes; retaining excess bytes defeats the output bound.
#[test]
fn capture_drains_past_independent_retention_limit() {
    let mut input = std::io::Cursor::new(b"abcdef");
    let result = capture(&mut input, 3).unwrap();
    assert_eq!(result.bytes, b"abc");
    assert_eq!(result.total_bytes, 6);
    assert!(result.truncated);
    assert_eq!(input.position(), 6);
    let result = capture(&b"abc"[..], 3).unwrap();
    assert!(!result.truncated);
    assert_eq!(result.bytes, b"abc");
}

#[test]
fn capture_rejects_unbounded_or_zero_limits() {
    assert_eq!(capture(&b"abc"[..], 0), Err(Error::InvalidInput));
    assert_eq!(capture(&b"abc"[..], usize::MAX), Err(Error::InvalidInput));
}

// Stable codes are the caller-facing evidence contract, independent of localized
// Win32 diagnostics.
#[test]
fn validation_and_platform_errors_have_stable_codes() {
    assert_eq!(Error::InvalidInput.code(), "invalid_input");
    assert_eq!(Error::Unavailable.code(), "containment_unavailable");
}

fn roots(base: &Path) -> WritableRoots {
    let canonical = base.canonicalize().unwrap();
    let base = canonical.as_path();
    let paths = [base.join("checkout"), base.join("home"), base.join("tmp")];
    for path in &paths {
        std::fs::create_dir_all(path).unwrap();
    }
    WritableRoots::prepare(&paths[0], &paths[1], &paths[2]).unwrap()
}

// Break caught: accepting relative, missing, or overlapping writable roots can
// grant a different write boundary than the controller intended.
#[test]
fn roots_reject_missing_relative_file_and_overlapping_directories() {
    let base = tempfile::tempdir().unwrap();
    let canonical = base.path().canonicalize().unwrap();
    let home = canonical.join("home");
    let tmp = canonical.join("tmp");
    std::fs::create_dir(&home).unwrap();
    std::fs::create_dir(&tmp).unwrap();
    let file = canonical.join("file");
    std::fs::write(&file, b"").unwrap();
    for path in [
        Path::new("relative"),
        &canonical.join("missing"),
        &file,
        &home,
        canonical.as_path(),
    ] {
        assert!(
            WritableRoots::prepare(path, &home, &tmp).is_err(),
            "{path:?}"
        );
    }
}

#[test]
fn command_rejects_bad_executable_environment_arguments_cwd_and_bounds() {
    let base = tempfile::tempdir().unwrap();
    let canonical_base = base.path().canonicalize().unwrap();
    let executable = std::env::current_exe().unwrap().canonicalize().unwrap();
    for bad in [
        canonical_base.join("missing"),
        canonical_base.clone(),
        "relative.exe".into(),
    ] {
        let roots = roots(base.path());
        assert!(
            CommandSpec::new(
                bad,
                vec![],
                roots.paths()[0].clone(),
                vec![],
                Instant::now() + Duration::from_secs(5),
                100,
                roots
            )
            .is_err()
        );
    }
    for name in ["shell.cmd", "shell.BAT"] {
        let bad = canonical_base.join(name);
        std::fs::write(&bad, b"test").unwrap();
        let roots = roots(base.path());
        assert!(
            CommandSpec::new(
                bad,
                vec![],
                roots.paths()[0].clone(),
                vec![],
                Instant::now() + Duration::from_secs(5),
                100,
                roots
            )
            .is_err()
        );
    }
    for (args, env, cwd, limit, deadline) in [
        (
            vec!["\0".into()],
            vec![],
            canonical_base.join("checkout"),
            100,
            Instant::now() + Duration::from_secs(5),
        ),
        (
            vec![],
            vec![("PATH".into(), "1".into()), ("Path".into(), "2".into())],
            canonical_base.join("checkout"),
            100,
            Instant::now() + Duration::from_secs(5),
        ),
        (
            vec![],
            vec![],
            canonical_base.clone(),
            100,
            Instant::now() + Duration::from_secs(5),
        ),
        (
            vec![],
            vec![],
            canonical_base.join("checkout"),
            0,
            Instant::now() + Duration::from_secs(5),
        ),
        (
            vec![],
            vec![],
            canonical_base.join("checkout"),
            usize::MAX,
            Instant::now() + Duration::from_secs(5),
        ),
        (
            vec![],
            vec![],
            canonical_base.join("checkout"),
            100,
            Instant::now() - Duration::from_secs(1),
        ),
    ] {
        assert!(
            CommandSpec::new(
                executable.clone(),
                args,
                cwd,
                env,
                deadline,
                limit,
                roots(base.path())
            )
            .is_err()
        );
    }
}

// Break caught: installing ACLs after materialization leaves non-inheriting
// existing content outside the intended restricting-SID grant.
#[test]
fn roots_require_empty_directories_before_acl_installation() {
    let base = tempfile::tempdir().unwrap();
    let first = roots(base.path());
    let paths = first.paths().clone();
    drop(first);
    std::fs::write(paths[0].join("preexisting"), b"data").unwrap();
    assert!(WritableRoots::prepare(&paths[0], &paths[1], &paths[2]).is_err());
}

#[cfg(not(windows))]
#[test]
fn explicit_execute_never_falls_back_on_other_platforms() {
    let base = tempfile::tempdir().unwrap();
    let roots = roots(base.path());
    let spec = CommandSpec::new(
        std::env::current_exe().unwrap().canonicalize().unwrap(),
        vec![],
        roots.paths()[0].clone(),
        vec![],
        Instant::now() + Duration::from_secs(5),
        1,
        roots,
    )
    .unwrap();
    assert!(matches!(
        heleos_worker_windows::execute(spec, vec![]),
        Err(Error::Unavailable)
    ));
}

#[cfg(unix)]
#[test]
fn roots_reject_symlinks_in_every_component() {
    let base = tempfile::tempdir().unwrap();
    let real = roots(base.path());
    let link = base.path().canonicalize().unwrap().join("link");
    std::os::unix::fs::symlink(&real.paths()[0], &link).unwrap();
    assert!(WritableRoots::prepare(&link, &real.paths()[1], &real.paths()[2]).is_err());
    std::fs::create_dir(real.paths()[0].join("inner")).unwrap();
    assert!(
        WritableRoots::prepare(&link.join("inner"), &real.paths()[1], &real.paths()[2]).is_err()
    );
}
