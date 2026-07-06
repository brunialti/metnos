# SPEC IMPLEMENTATIVA — Fase 7 / W4: sandbox Windows forte (AppContainer + capability→ACL)

> **Destinatario**: LLM esecutore (Opus). File:riga verificati il 7/7/2026, criteri di done, piano test.
> **Doc padre**: `internal/design/remote-executors.html` §15.5 (W4, ~1-2 settimane), §16.2 (Job Object), tabella §932/937.
> **Autore analisi**: Fable, 7/7/2026.
> **VINCOLO SUPREMO**: mai regressioni sul path Linux e sul flusso W3.3 validato (job-object, timeout onesto, lock, revoca→403, self-update ADR 0184). Ogni incremento spedibile da solo; fallback SEMPRE dichiarato (§2.8), mai silenzioso.

---

## 0. Stato attuale (fatti verificati, 7/7/2026)

- **`client-rs/src/sandbox_windows.rs` (340 LOC)** — contenimento di RISORSE, non isolamento:
  - Job Object: `CreateJobObjectW` → `SetInformationJobObject` con `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE` + `JOB_OBJECT_LIMIT_PROCESS_MEMORY` (default 512MB, `DEFAULT_MEM_LIMIT_MB:49`) + `JOB_OBJECT_LIMIT_ACTIVE_PROCESS` (`:33-37,:76-91`).
  - Flusso: spawn `CREATE_SUSPENDED|CREATE_NO_WINDOW` → `AssignProcessToJobObject` → `ResumeThread` → wait con deadline → timeout: `TerminateJobObject` (header `:18-21`). `FreeConsole` fatto (B6, 3/7).
  - **NON c'è**: token ristretto (il commento `:12` lo cita ma `CreateRestrictedToken` NON è nel file), isolamento filesystem, isolamento rete. `exec.capabilities` arriva al runner (`runner.rs:163`) ma **NON è tradotto in nulla** (header `:13-14`: «capability→ACL arriva con AppContainer in W4»).
- **`client-rs/src/runner.rs` (545 LOC)** — orchestrazione: claim → `assert_stdlib_only` (`pyenv.rs:420`, chiamato `runner.rs:251`) → shim → `run_sandboxed` → spool → consegna. Il campo `sandbox` del result oggi vale `"job-object"`/`"none"` (`:211`) — l'ONESTÀ sul livello di sandbox è già un contratto del result.
- **Capabilities nel manifest** (`[[capabilities]]` name+hint): es. `fs:read` con `hint=["~/**","/tmp/**"]`, `fs:write`, `network.read` con `hint=["https://*"]`, `exec_subprocess`. Su Linux gli hint diventano bind di bwrap (`sandbox_linux.rs` — usare la STESSA derivazione hint→root).
- **Executor device-abili oggi**: 13 (files/dirs ×9 + get_files, compute_files_loc, find_packages, get_processes). I MUTANTI (write/move/delete) girano già sul device SENZA isolamento fs — è il rischio che W4 chiude.

## 1. Obiettivo W4

1. **Profilo AppContainer** per il processo executor sul device Windows: filesystem e rete NEGATI per default.
2. **Traduzione capability→permessi**: gli hint del manifest diventano ACL `GrantAccess` al SID del container (fs) e capability SID di rete (net).
3. **Livello dichiarato, mai silenzioso**: `result.sandbox ∈ {"appcontainer","job-object","none"}` + `sandbox_downgrade_reason` quando si degrada. Il server può (fase 2, opzionale) rifiutare mutanti sotto un livello minimo per-executor.

## 2. Design

### 2.1 API Windows (crate `windows-sys`, verificare le feature; alcune funzioni vivono in `userenv.dll`/`advapi32`)
- Profilo: `CreateAppContainerProfile(name, displayName, desc, capabilities, count)` / `DeriveAppContainerSidFromAppContainerName` / `DeleteAppContainerProfile` (userenv). Nome profilo: `"Metnos.Executor"` (UNO per client, non per-invocazione: creazione idempotente al primo uso, cleanup all'unpair/revoca).
- Spawn nel container: `STARTUPINFOEXW` + `InitializeProcThreadAttributeList` + `UpdateProcThreadAttribute(PROC_THREAD_ATTRIBUTE_SECURITY_CAPABILITIES)` con `SECURITY_CAPABILITIES{AppContainerSid, Capabilities[], CapabilityCount}`; `CreateProcessW` con `EXTENDED_STARTUPINFO_PRESENT` (che si COMBINA con l'attuale `CREATE_SUSPENDED|CREATE_NO_WINDOW` e col Job Object: AppContainer e Job Object COESISTONO — tenere entrambi).
- ACL fs: `GetNamedSecurityInfoW` → `SetEntriesInAclW` (una `EXPLICIT_ACCESS_W` con `GRANT_ACCESS` al SID container, `SUB_CONTAINERS_AND_OBJECTS_INHERIT`) → `SetNamedSecurityInfoW` sulle DIRECTORY-radice derivate dagli hint. Registrare le ACL aggiunte (file di stato client `state.rs`) per la RIMOZIONE all'unpair.
- Rete: capability SID well-known `internetClient` (`WinCapabilityInternetClientSid` via `DeriveCapabilitySidsFromName` o SID literal `S-1-15-3-1`) SOLO se il manifest dichiara `network.*`. Senza → l'AppContainer nega la rete per default (guadagno gratis).

### 2.2 Mappatura capability→permessi (tabella di verità)
| Capability manifest | AppContainer |
|---|---|
| `fs:read` hint | ACL `GENERIC_READ|GENERIC_EXECUTE` sulle radici derivate dagli hint (stessa funzione hint→root di sandbox_linux; `~` = home utente device) |
| `fs:write` hint | ACL `GENERIC_READ|GENERIC_WRITE|DELETE` sulle radici |
| `network.read`/`network.*` | capability SID `internetClient` |
| `exec_subprocess` | niente di specifico (il figlio eredita il container); NOTA: subprocess che tocca path fuori-grant fallirà — onesto |
| (sempre, implicite) | READ+EXECUTE su: dir runtime python-build-standalone, dir shim, dir executor scaricati; WRITE su: `spool/`, TEMP del container, dir output di lavoro |

**TEMP**: dentro AppContainer `%TEMP%` è rediretto per-container (`AppData\Local\Packages\<profile>\AC\Temp`) — verificare che `run_stdio`/executor non assumano il TEMP utente.

### 2.3 Fallback dichiarato
`create_appcontainer()` può fallire (edizioni vecchie, policy, FS non-NTFS per le ACL). Politica: **degrada a job-object e DICHIARA** — `result.sandbox="job-object"`, `sandbox_downgrade_reason="<errore>"`. MAI bloccare di default. Gate opzionale server-side (fase W4.4): campo manifest `[placement] min_sandbox="appcontainer"` → il server non instrada mutanti a device che hanno riportato downgrade (il profilo device — `devices.profile_json` — guadagna `sandbox_level` dall'ultimo heartbeat/result).

## 3. Incrementi (ognuno spedibile e testabile da solo)

### W4.1 — Spike di fattibilità (gate: `METNOS_SANDBOX_APPCONTAINER=1`, default OFF)
Nuovo modulo `appcontainer.rs` (tenere `sandbox_windows.rs` intatto): crea profilo, spawn `python -c "open(<path-fuori-grant>)"` → DENIED; con ACL grant → OK; rete senza capability → DENIED. Binario di test manuale o sotto flag.
**Done**: sul PC Windows reale i 3 assert passano; runbook aggiornato (`internal/design/e2e-windows-runbook.md`).

### W4.2 — Traduzione capability→ACL
Funzione `grant_for_capabilities(sid, caps: &[Capability]) -> Vec<AclGrant>` + derivazione hint→root CONDIVISA con Linux (estrarre in modulo comune se oggi è dentro sandbox_linux). Stato ACL persistito per cleanup. Unit test Rust compile-gated `#[cfg(windows)]`.
**Done**: write_files con `fs:write hint=["~/Documents/**"]` scrive in Documents e FALLISCE (onesto, error_class chiaro) su `C:\Windows\...`.

### W4.3 — Integrazione in `run_sandboxed`
Path Windows: AppContainer (se gate ON e profilo ok) DENTRO il Job Object esistente; `result.sandbox="appcontainer"`; downgrade dichiarato. `runner.rs` invariato nella forma (`run_sandboxed` firma stabile).
**Done**: e2e runbook completo: read-only + mutante + undo round-trip (il flusso ADR 0183 deve restare bit-perfetto) con `sandbox="appcontainer"` nei result; suite Python invariata (i result con campo nuovo non rompono parser — verificare `remote_exec.py`).

### W4.4 — Policy server + cleanup
`sandbox_level` nel profilo device; gate opzionale `min_sandbox` per-manifest (default: nessun gate); `DeleteAppContainerProfile`+rimozione ACL all'unpair/revoca; promozione gate ON di default SOLO dopo ≥1 settimana di esercizio senza downgrade sul PC reale.
**Done**: revoca → profilo e ACL rimossi (verificato con `icacls`); decisione Roberto sul default ON.

## 4. Sottigliezze / rischi (leggere PRIMA di codificare)
- **Loopback**: AppContainer NEGA loopback (127.0.0.1) senza esenzione `CheckNetIsolation`. L'executor NON parla col server (è il runner, FUORI dal container, a consegnare i result) → nessuna esenzione necessaria. NON aggiungerla «per sicurezza».
- **windows-sys vs windows**: le API userenv/ACL potrebbero mancare in `windows-sys` — preferire dichiarazioni FFI manuali minime alla dipendenza dal crate `windows` (pesante; il binario oggi è musl-2.45MB su Linux e piccolo su Windows: tenerlo tale).
- **Path con hint glob**: derivare la RADICE stabile (fino al primo wildcard), mai ACL su glob. Radici inesistenti → skip onesto (non crearle).
- **Il python runtime è CONDIVISO** fra invocazioni: le ACL di lettura sul runtime si mettono UNA volta (idempotente), non per-invocazione (costo `SetNamedSecurityInfoW` su alberi grandi).
- **NON toccare** `sandbox_linux.rs`, il protocollo wire, la coda: W4 è interamente client-side + 2 campi additivi (result.sandbox già esiste; `sandbox_downgrade_reason` e `profile.sandbox_level` sono nuovi ma additivi).
- Test CI: il codice è compile-gated Windows; la CI Linux deve continuare a compilare (`cargo check --target x86_64-pc-windows-gnu` già nel flusso di build client — verificare in `build-client.sh`).

## 5. Validazione finale
`e2e-windows-runbook.md` esteso con la sezione W4 (spike, grant, denied, mutante+undo, revoca+cleanup) eseguita sul PC-ROBERTO reale; `scripts/e2e-remote-executor.sh` (Linux) resta verde; suite Python invariata; un turno reale «scrivi un file sul mio pc» → result `sandbox="appcontainer"`.
