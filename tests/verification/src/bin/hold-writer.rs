#![forbid(unsafe_code)]

use std::io::{BufRead, Write};

fn main() {
    let mut arguments = std::env::args_os().skip(1);
    let database = arguments.next().expect("one database path is required");
    assert!(arguments.next().is_none(), "unexpected argument");
    let mut store = heleos_core::Store::open_writer(database).expect("acquire writer lock");
    store.migrate().expect("migrate locked store");
    println!("ready");
    std::io::stdout().flush().unwrap();
    let mut line = String::new();
    std::io::stdin().lock().read_line(&mut line).unwrap();
    assert_eq!(line, "release\n");
    drop(store);
}
