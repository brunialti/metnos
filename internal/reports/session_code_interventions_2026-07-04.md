# Interventi sul codice — sessione 3-4 luglio 2026

**Branch:** `session/detection-lexicon-i18n` (non pushato) · **Autore:** agente
(sessione Opus) · **Ambito:** executor remoti, placement guidato dalla chat,
rimozione planner legacy, rilievi review, predisposizione multi-utente, fix
client. Esclusi i commit puramente doc/report.

## Sintesi

Sessione avviata sulla validazione W3.3 degli executor remoti (client 0.2.6→0.2.7)
e proseguita su sei filoni di codice: (A) esecuzione sul device + placement dalla
chat, (B) rimozione del planner legacy, (C) rilievi della review composer/engine/
remote, (D) ripulitura prompt, (E) predisposizione multi-utente dei device, (F)
fix di robustezza del client (shim). Suite Python `3188 passed, 0 failed` da repo
root; e2e client verdi; client 0.2.8 firmato e mirrorato. Nessun trailer
`Co-Authored-By`.

## Commit di codice (cronologico)

| commit | area | descrizione |
|--------|------|-------------|
| `483feaa` | A | C7 opt-1: `list_dirs` sul device via `path_alias` nello shim |
| `8e48d97` | A | placement R1 chat-driven — «esegui sul mio PC» (ADR 0034) |
| `347f65f` | A | placement R1 hardening — risoluzione a `run_turn`, gate eleggibilità, tag reale |
| `5e6e828` | A | placement — osservazioni assessment esterno #1-5 |
| `3ee8573` | B | neutralizza il planner legacy — declino motore = esito ONESTO |
| `0893171` | B | robustezza engine — no-declino su intent vuoto + de-silenzia fast-call |
| `af6c7b8` | B | CANCELLA il planner legacy — −3407 LOC dalla coda di `run_turn` |
| `924ef53` | B | pulizia post-rimozione — sonda, `mode/--mode/_bypass`, drift installer |
| `75c1439` | D | ripulitura anglicismi `synt_code` IT (§7.8) + `lang_state` |
| `f16e0ef` | C | review F1/F2/F3/F5 — platforms, device_ok, owner-filter, purge invocations |
| `35fcb7a` | E | predisposizione multi-utente — device→`users.id` reale |
| `7ae6187` | F | client shim auto-rigenerante (0.2.8) |

---

## A. Executor sul device + placement guidato dalla chat (ADR 0034/0181)

- **`483feaa`** — C7: `list_dirs` eseguibile sul device aggiungendo `path_alias.py`
  al bundle shim (`agent_server.shim_bundle`), modulo flat stdlib-only.
- Nuovo **`runtime/target_device.py`**: resolver deterministico (§7.9, no LLM):
  nome device SOLO se preceduto da preposizione locativa (match più lungo vince);
  marcatore locale/server; destinazione appiccicosa; controllo connessione sempre
  (offline→`unreachable`); `DEVICE_ELIGIBLE`.
- Nuovo **`runtime/chat_target_store.py`**: destinazione appiccicosa per
  `sender_id` (sqlite co-locato con `devices.db`).
- **`agent_runtime.run_turn`**: risoluzione target UNA volta prima di fast_path ed
  engine (entrambi i path instradano); tag `📍<nome>` reale solo su esecuzione
  device (`_ran_on_device`), esposto in `_turn_json`. Hardening #1-5: sticky-
  offline→server, nomi duplicati→ambiguo, owner-filter, fallthrough, threading.

## B. Rimozione del PLANNER legacy (ADR 0181-ext)

- **`3ee8573`** — neutralizzazione: il declino del motore diventa esito ONESTO
  (`ERR_QUERY_NOT_UNDERSTOOD`) invece di un piano degenere.
- **`0893171`** — CAUSA RADICE del fallthrough intermittente al legacy:
  `_llm_call_fast` aveva `except: return ""` che inghiottiva i singhiozzi di
  connessione LLM → intent None → motore declina. De-silenziata (+retry) e intent
  vuoto reso non-fatale (`intent_raw={}`).
- **`af6c7b8`** — cancellazione fisica del planner legacy: **−3407 LOC** dalla coda
  di `run_turn` (righe 6211-9615), tutte revertibili.
- **`924ef53`** — pulizia residui: sonda gated, flag `mode/--mode/_bypass`, drift
  installer.

## C. Rilievi review composer/engine/remote — `f16e0ef` (+guard `cccd866`)

- **F1**: `executors/list_dirs/manifest.toml` `platforms=["linux","windows"]` (era
  default `["linux"]` → device Windows rifiutato da `choose_placement`).
- **F2**: `[placement] device_ok=true` nei 3 manifest (get_files/compute_files_loc/
  list_dirs); `invoke_executor` legge il manifest, `DEVICE_ELIGIBLE` resta come
  compat-shim.
- **F3**: owner-filter (`owner_user_id == actor`) anche in `invoke_executor` prima
  di `choose_placement`.
- **F5**: `invocations.purge_invocations(older_than_days)` (solo terminali
  done/failed, mai in-volo) + wire nel `task_state_reaper`
  (`METNOS_INVOCATIONS_RETENTION_DAYS`).
- Guard `test_target_device.py::DeviceEligibleManifestTests`. Manifest re-firmati
  (§7.10).

## D. Prompt — `75c1439`

Ripulitura anglicismi del `runtime/prompts/it/synt_code.j2` (§7.8) + `lang_state
.json` IT/EN sincronizzato per proteggere dal ri-traduttore. Origine anglicismi
tracciata come TODO Fable (qualità traduzioni prompt).

## E. Predisposizione multi-utente dei device — `35fcb7a`

Chiude «`owner_user_id` sempre 'host'» (rilievo F4/A3): il Modello 1 (device
remote-executor) ora riferisce un VERO `users.id` (ADR 0083), scelto dall'admin al
pairing e visibile nel profilo utente.

- **`devices.py`**: migrazione idempotente owner `'host'`→id host reale (devices +
  device_tokens, in `_migrate`); helper `host_user_id()`, `owner_user()`
  (device→utente = identificazione), `list_by_owner()`, **`owner_id_for_actor()`**
  (resolver centrale actor→owner: device_id→owner del device / user→utente /
  host→host).
- **`http_routes_admin.py`**: `_resolve_pairing_owner()` (valida owner→`users.id`,
  400 se inesistente, default host); endpoint token+join catturano/validano owner;
  lista device mostra owner risolto + dropdown utenti; profilo utente elenca i suoi
  device.
- **`http_routes_agent.py`**: `/agent/devices/me` espone l'utente risolto (id/nome/
  ruolo/autonomia) — aggancio per i futuri profili di sicurezza.
- **`agent_runtime.py`**: i 2 filtri owner (`run_turn` + `invoke_executor`) usano
  `owner_id_for_actor()`. CRITICO: senza, dopo la migrazione (owner=uuid) il
  vecchio confronto `owner_user_id=='host'` avrebbe filtrato via PC-ROBERTO e rotto
  il placement chat-driven.
- templates `devices.html` (picker+colonna) + `user_detail.html` (sezione device);
  nuovo `test_devices_owner.py` (migrazione, resolver, isolamento due-owner).

Validato dal vivo: PC-ROBERTO migrato a owner=Roberto, compare nel profilo;
pairing owner valido→ok / invalido→400; turno device reale ancora `📍 PC-ROBERTO`
DOPO la migrazione.

## F. Fix client Rust — shim auto-rigenerante (0.2.8) — `7ae6187`

Bug C7 emerso validando `list_dirs` sul PC: il client scaricava lo shim UNA volta
per processo e lo memoizzava (`runner.rs:250`). Un modulo runtime aggiunto al
bundle server DOPO l'avvio del client non arrivava mai ai client vivi →
`ModuleNotFoundError: path_alias` (onesto §2.8).

- **`client-rs/src/runner.rs`**: su output executor non-JSON con import fallito
  (`ModuleNotFoundError`/`ImportError`), il client rigenera lo shim (`ensure_shim`
  ri-fetcha e sovrascrive atomicamente) e riprova UNA volta. Costo zero sul
  percorso felice; ogni altro output non-JSON resta un errore invariato.
- **`Cargo.toml`** 0.2.7→0.2.8; build+firma+mirror di entrambi i target.
- Nuovo **`scripts/e2e-shim-selfheal.sh`**: e2e REALE anti-gaming — corrompe lo
  shim MENTRE lo stesso client è vivo+memoizzato (rimuove `executor_helpers.py` fra
  due invocazioni), poi verifica l'auto-guarigione con assert sul log (refetch+
  retry avvenuto) e sul ripristino del modulo.

---

## Validazione

- Suite Python: **3188 passed, 26 skipped, 0 failed** (da repo root, 124s).
- e2e client: `e2e-remote-executor.sh` (fasi A-F verdi, nessuna regressione dal
  refactor di `execute()`) + `e2e-shim-selfheal.sh` (verde).
- `cargo test` 5/5. Client 0.2.8 firmato+mirrorato (linux musl + windows gnu).
- Turni reali su prod dopo i fix: device (`📍 PC-ROBERTO`) + normale (locale) OK.

## Gap onesto residuo / follow-up

- **`list_dirs` sul PC non ancora validato dal vivo**: il fix radice c'è ma il
  client 0.2.7 in esecuzione sul PC ha lo shim stantio in memoria. Serve
  **riavvio** (ri-fetcha il bundle corretto → sblocco immediato) o **reinstall a
  0.2.8** (anti-ricorrenza; self_update binario ⛔).
- **Owner nei turni end-to-end**: `actor` non diventa ancora globalmente l'utente
  owner del device connesso nei domini a valle (persons/autonomia). La
  predisposizione fornisce dato+resolver+UI; il cablaggio actor→owner e i «profili
  di sicurezza» sono il passo successivo (`owner_id_for_actor` è il gancio pronto).
- **F6/F7** (exactly-once + sandbox tier) prima dei mutanti remoti (C7 R3).
