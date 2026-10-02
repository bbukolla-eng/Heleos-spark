//! Executable fixture for native Windows runner integration tests.
#[cfg(not(windows))]
fn main() {}

#[cfg(windows)]
fn main() {
    use std::{
        fs,
        io::{Read, Write},
        path::Path,
        process::Command,
        time::Duration,
    };
    let args: Vec<String> = std::env::args().skip(1).collect();
    match args[0].as_str() {
        "roots" => {
            fs::write("allowed/proposal", b"checkout").unwrap();
            fs::write(
                Path::new(&std::env::var("HOME").unwrap()).join("probe"),
                b"home",
            )
            .unwrap();
            fs::write(
                Path::new(&std::env::var("TEMP").unwrap()).join("probe"),
                b"tmp",
            )
            .unwrap();
            for path in [&args[1], &args[2], "../sibling", "../task.canonical.json"] {
                assert!(
                    fs::write(path, b"forbidden").is_err(),
                    "outside write unexpectedly succeeded"
                );
            }
            let child = Command::new(std::env::current_exe().unwrap())
                .args(["denied-child", &args[2]])
                .status()
                .unwrap();
            assert!(child.success());
            assert!(fs::rename("..", "../../replaced-run").is_err());
        }
        "denied-child" => {
            assert!(fs::write(&args[1], b"forbidden child").is_err());
        }
        "timeout" => {
            fs::write("allowed/parent-started", b"started").unwrap();
            let mut child = Command::new(std::env::current_exe().unwrap())
                .arg("sleep-child")
                .spawn()
                .unwrap();
            std::thread::sleep(Duration::from_secs(8));
            fs::write("allowed/parent-survived", b"bad").unwrap();
            let _ = child.wait();
        }
        "sleep-child" => {
            fs::write("allowed/child-started", b"started").unwrap();
            std::thread::sleep(Duration::from_secs(8));
            fs::write("allowed/child-survived", b"bad").unwrap();
        }
        "argv-output" => {
            fs::write("allowed/argv.json", serde_json::to_vec(&args[1..]).unwrap()).unwrap();
            let mut input = Vec::new();
            std::io::stdin().read_to_end(&mut input).unwrap();
            fs::write("allowed/prompt", input).unwrap();
            std::io::stdout().write_all(&vec![b'x'; 20_000]).unwrap();
            std::io::stderr().write_all(&vec![b'y'; 20_000]).unwrap();
        }
        "hardlink" => fs::hard_link("allowed/base.txt", "allowed/alias").unwrap(),
        "forbidden-case" => {
            fs::create_dir("allowed/SECRET").unwrap();
            fs::write("allowed/SECRET/key", b"forbidden").unwrap();
        }
        "symlink" => std::os::windows::fs::symlink_file("base.txt", "allowed/alias").unwrap(),
        "exit7" => {
            std::process::exit(7);
        }
        _ => panic!("unknown synthetic provider mode"),
    }
}
