#![forbid(unsafe_code)]

use std::fs::File;
use std::io::{self, Read, Write};

use heleos_pdf_guest::inspect_pdf_v1;
use heleos_pdf_protocol::{
    MAX_PROTOCOL_OUTPUT_BYTES, MAX_REQUEST_BYTES, PdfResponseV1, decode_request_v1,
    encode_response_v1,
};

fn main() {
    if run().is_err() {
        std::process::exit(2);
    }
}

fn run() -> std::result::Result<(), ()> {
    let request_bytes = read_stdin_through_eof()?;
    let request = decode_request_v1(&request_bytes).map_err(|_| ())?;
    let pdf = read_pdf_exact(request.byte_length)?;
    let outcome = inspect_pdf_v1(&pdf, &request);
    let response = PdfResponseV1 {
        protocol: request.protocol,
        input_sha256: request.input_sha256,
        byte_length: request.byte_length,
        outcome,
    };
    let bytes = encode_response_v1(&response, MAX_PROTOCOL_OUTPUT_BYTES).map_err(|_| ())?;
    let mut stdout = io::stdout().lock();
    stdout.write_all(&bytes).map_err(|_| ())?;
    stdout.flush().map_err(|_| ())
}

fn read_stdin_through_eof() -> std::result::Result<Vec<u8>, ()> {
    let mut stdin = io::stdin().lock();
    let mut captured = Vec::with_capacity(MAX_REQUEST_BYTES.min(4096));
    let mut buffer = [0_u8; 4096];
    let mut overflow = false;
    loop {
        let read = stdin.read(&mut buffer).map_err(|_| ())?;
        if read == 0 {
            break;
        }
        if !overflow {
            let remaining = MAX_REQUEST_BYTES.saturating_sub(captured.len());
            if read > remaining {
                overflow = true;
            } else {
                captured.extend_from_slice(&buffer[..read]);
            }
        }
    }
    if overflow {
        return Err(());
    }
    Ok(captured)
}

fn read_pdf_exact(byte_length: u64) -> std::result::Result<Vec<u8>, ()> {
    let length = usize::try_from(byte_length).map_err(|_| ())?;
    let mut file = File::open("/input/input.pdf").map_err(|_| ())?;
    let mut bytes = Vec::with_capacity(length.min(1024 * 1024));
    let mut limited = (&mut file).take(byte_length.saturating_add(1));
    limited.read_to_end(&mut bytes).map_err(|_| ())?;
    if bytes.len() != length {
        return Err(());
    }
    Ok(bytes)
}
