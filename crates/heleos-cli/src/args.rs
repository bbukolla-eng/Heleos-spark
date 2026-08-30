//! Typed command-line grammar.

use clap::Parser;

#[derive(Debug, Parser)]
#[command(
    name = "heleos",
    version,
    about = "Heleos Foundation data and recovery tools"
)]
pub(crate) struct Cli {}
