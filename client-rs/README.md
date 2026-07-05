# metnos-client

Client Rust per l'esecuzione remota di executor Metnos su un dispositivo appaiato
(PC di casa/ufficio). Bootstrap lazy del runtime Python via mirror server, sandbox
per piattaforma, **HTTP firmato Ed25519** (device + server) — NON mTLS.

Versione corrente: **0.2.9** (Cargo.toml + mirror `latest`). Su Windows
l'esecuzione usa Job Object per limitare risorse e spegnere l'intero albero dei
processi al timeout.

## Autenticazione e trasporto (stato reale)
- Trasporto attuale: **HTTP** dentro la LAN o una rete privata equivalente.
  TLS/mTLS e' un irrobustimento futuro del canale, non un requisito del client
  corrente.
- Ogni richiesta client→server è **firmata Ed25519** sui bytes esatti del body
  (header `X-Metnos-Device-Sig`); ogni invocazione server→client porta una
  `server_sig` verificata dal client contro la **pubkey server pinnata** prima
  dell'esecuzione (firma non valida → rifiuto, nessuna esecuzione).
- Cache executor content-addressed (manifest sha256 + code sha256), shim firmato.

## Build
```
cargo build --release --target x86_64-unknown-linux-musl    # Linux (static musl)
cargo build --release --target x86_64-pc-windows-gnu         # Windows (mingw-w64)
```
Distribuzione firmata + mirror: `scripts/build-client.sh <versione>` (firma
Ed25519 con la chiave server + pubblica nel mirror). macOS = tier-2 (build manuale).

## Layout (moduli reali)
- `src/main.rs` — entry + CLI (`whoami` / `register` / `run`).
- `src/config.rs` — path locali (XDG-style cross-platform), file di stato.
- `src/identity.rs` — chiave Ed25519 del device (gen al primo avvio, persistita).
- `src/pairing.rs` — flow `register`: token monouso + pubkey → device_id.
- `src/runner.rs` — loop `run`: flush spool → poll → verify server_sig → pull
  executor (cache-miss) → sandbox → spool result → consegna; heartbeat su task
  separato (0.2.7).
- `src/wire.rs` — tipi wire + canonical JSON (contratto cross-lang col server).
- `src/executors.rs` — pull + verifica firma di executor e shim.
- `src/pyenv.rs` — runtime python-build-standalone (download robusto a chunk,
  estrazione pure-Rust); Windows senza fallback al python di sistema.
- `src/sandbox_linux.rs` — bubblewrap se presente, altrimenti fallback diretto
  loggato (§2.8); kill d'albero via process-group.
- `src/sandbox_windows.rs` — **Job Object** (KILL_ON_JOB_CLOSE + cap mem/proc,
  spawn CREATE_SUSPENDED → assign → resume → wait/timeout → TerminateJobObject).
- `src/proclock.rs` — lock single-instance cross-platform (`fs2`).
- `src/state.rs` — stato appaiamento persistito.

## Stato
Stato operativo: Linux e Windows sono supportati per gli executor remoti di sola
lettura e autosufficienti. Restano fuori dal percorso ordinario gli executor che
modificano dati, richiedono dipendenze non impacchettate o pretendono isolamento
filesystem/rete piu' forte di quello disponibile oggi su Windows.

Prossimi irrobustimenti: AppContainer/ACL su Windows, aggiornamento automatico
del binario, TLS/mTLS del canale e gestione esplicita di piu' dispositivi per
utente. Vedi `internal/design/remote-executors.html`.
