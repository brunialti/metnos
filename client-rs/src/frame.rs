//! One message per line: where a request ends and the wait begins.
//!
//! Both sides speak over the same duplex pipe, so neither can close its end to
//! signal "I am done". Reading to end-of-stream would make the service wait for
//! a client that is itself waiting for the answer — a deadlock that looks like
//! a hang, not like a bug.
//!
//! A terminator byte settles it. The bodies are JSON, and JSON escapes literal
//! newlines inside strings, so `\n` can never appear inside a body: it marks
//! the end and nothing else.
//!
//! This file is BYTE-IDENTICAL in `helper-rs` and `client-rs`. The two are
//! separate programs on purpose — the service runs with system privileges and
//! must not link client code — and a framing that differs by one byte would
//! hang both sides with no message. `tests/runtime/remote/test_helper_wire_contract.py`
//! compares the two copies.

use std::io::{self, BufRead, BufReader, Read};

/// The byte that ends a message.
pub const TERMINATOR: u8 = b'\n';

/// Appends the terminator to a body, ready to be written.
pub fn framed(body: &[u8]) -> Vec<u8> {
    let mut out = Vec::with_capacity(body.len() + 1);
    out.extend_from_slice(body);
    out.push(TERMINATOR);
    out
}

/// Reads one message, and refuses anything that is not exactly one.
///
/// `max` is the largest body accepted, terminator excluded. A cap exists
/// because without one, whoever can open the pipe can grow the memory of a
/// privileged process by writing and never stopping.
///
/// A stream that ends without a terminator is an ERROR, not a short message:
/// accepting it would mean acting on a request that was cut in half.
pub fn read_frame<R: Read>(reader: R, max: usize) -> io::Result<Vec<u8>> {
    // One byte over the cap, so an oversized body is told apart from one that
    // fits exactly.
    let mut limited = BufReader::new(reader.take(max as u64 + 1));
    let mut buffer = Vec::new();
    limited.read_until(TERMINATOR, &mut buffer)?;

    if buffer.last() != Some(&TERMINATOR) {
        if buffer.len() > max {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!("message longer than {max} bytes"),
            ));
        }
        return Err(io::Error::new(
            io::ErrorKind::UnexpectedEof,
            "message without terminator: the other end closed early",
        ));
    }
    buffer.pop();
    Ok(buffer)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_body_survives_the_round_trip() {
        let corpo = br#"{"op":"install","package":"7zip.7zip"}"#;
        let letto = read_frame(&framed(corpo)[..], 4096).unwrap();
        assert_eq!(letto, corpo);
    }

    #[test]
    fn an_empty_body_is_a_message_too() {
        assert_eq!(read_frame(&framed(b"")[..], 4096).unwrap(), b"");
    }

    #[test]
    fn reading_stops_at_the_first_terminator() {
        // Whatever follows belongs to nobody: one connection carries one
        // request, and a second body must not be smuggled behind the first.
        let flusso = b"primo\nsecondo\n";
        assert_eq!(read_frame(&flusso[..], 4096).unwrap(), b"primo");
    }

    #[test]
    fn a_stream_that_ends_early_is_an_error() {
        let esito = read_frame(&b"mezza richiesta"[..], 4096);
        assert_eq!(esito.unwrap_err().kind(), io::ErrorKind::UnexpectedEof);
    }

    #[test]
    fn a_body_over_the_cap_is_refused() {
        let enorme = framed(&vec![b'x'; 200]);
        let esito = read_frame(&enorme[..], 64);
        assert_eq!(esito.unwrap_err().kind(), io::ErrorKind::InvalidData);
    }

    #[test]
    fn a_body_exactly_at_the_cap_is_accepted() {
        // The boundary is the case a cap gets wrong: 64 bytes with max 64 is
        // within the limit, and the extra byte read is the terminator.
        let al_limite = framed(&vec![b'x'; 64]);
        assert_eq!(read_frame(&al_limite[..], 64).unwrap().len(), 64);
    }

    #[test]
    fn no_bytes_at_all_is_an_error_not_an_empty_message() {
        let esito = read_frame(&b""[..], 4096);
        assert_eq!(esito.unwrap_err().kind(), io::ErrorKind::UnexpectedEof);
    }
}
