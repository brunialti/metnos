# metnos-client

Client Rust per esecuzione remota di executor Metnos. Lazy bootstrap di Python via mirror server, sandbox per piattaforma, mTLS verso il server `.33`.

## Build (Linux MVP)

```
cargo build --release --target x86_64-unknown-linux-gnu
cargo build --release --target x86_64-unknown-linux-musl
```

Windows cross: arriva alla W3 della roadmap (richiede `mingw-w64`).

## Layout

- `src/main.rs` — entry point, CLI.
- `src/config.rs` — path locali (XDG-style cross-platform), file di stato.
- `src/identity.rs` — chiave Ed25519 del device (gen al primo avvio, persistita).
- `src/pairing.rs` — flow `register`: token + pubkey → device_id.
- `src/transport.rs` — HTTP client verso il server.

## Stato

W1-2 MVP Linux in corso.
