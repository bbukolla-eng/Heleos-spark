#![forbid(unsafe_code)]

use std::process::Command;

#[test]
fn help_is_available_from_the_declared_heleos_binary() {
    let output = Command::new(env!("CARGO_BIN_EXE_heleos"))
        .arg("--help")
        .output()
        .expect("run the declared heleos binary");

    assert!(output.status.success(), "--help must succeed");
}
