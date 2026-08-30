#![forbid(unsafe_code)]

mod args;

use clap::Parser;

const APPROVED_PDF_GUEST: &[u8] =
    include_bytes!(concat!(env!("OUT_DIR"), "/heleos_pdf_guest.wasm"));
const APPROVED_PDF_GUEST_MANIFEST: &str =
    include_str!(concat!(env!("OUT_DIR"), "/pdf_guest_manifest.toml"));

fn main() {
    let _arguments = args::Cli::parse();
    let _embedded_guest = (APPROVED_PDF_GUEST, APPROVED_PDF_GUEST_MANIFEST);
}
