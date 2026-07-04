# Azioni sulla review Composer/Engine/Remote — 2026-07-04

**Riferimento:** `internal/reports/composer_engine_remote_review_2026-07-04.md`
(review esterna di composer/engine/remote). **Metodo:** ogni rilievo (F1-F10,
D1-D6, P0-P4) è stato VERIFICATO contro il codice corrente; i veri e azionabili
sono stati corretti, gli altri rimandati con motivo. **Branch:**
`session/detection-lexicon-i18n` (non pushato). **Autore:** agente (sessione Opus).

La review esterna è risultata accurata e **conferma il lavoro della sessione**
(sezione «Conferme positive»: sticky-offline→server, nomi duplicati→ambiguous,
owner-filter chat, threading upload, resolver fail-closed — tutti verificati
corretti).

---

## ✅ Verificato VERO e FIXATO

Commit: `f16e0ef` (codice), `cccd866` (doc/test).

| # | Rilievo | Verifica | Fix |
|---|---|---|---|
| **F1** (alta) | `list_dirs` in `DEVICE_ELIGIBLE` ma il manifest non dichiarava `platforms` → default `["linux"]` → `choose_placement._check_platform` rifiuta un device **Windows** | VERO: `get_files`/`compute_files_loc` dichiaravano `["linux","windows"]`, `list_dirs` NO; `list_dirs.py` è stdlib puro (cross-platform) | `platforms = ["linux","windows"]` in `executors/list_dirs/manifest.toml` + **re-firma** §7.10. Verificato: `choose_placement` ora accetta `list_dirs` su device Windows-mock. |
| **F2** (architetturale) | Eleggibilità al device **hardcoded** (`target_device.DEVICE_ELIGIBLE`), i manifest non dichiarano `[placement]` | VERO: `DEVICE_ELIGIBLE` usato solo in `invoke_executor`; il loader PARSA già `[placement]` | `[placement] device_ok = true` (+ `scope="any"`) nei 3 manifest (get_files/compute_files_loc/list_dirs) + re-firma; `invoke_executor` legge `_plc.get("device_ok")` con `DEVICE_ELIGIBLE` come **compat-shim**. Nuovo executor remoto = si dichiara nel manifest, senza toccare codice centrale. |
| **F3** (media→alta multi-utente) | `run_turn` filtra i device per owner, ma `invoke_executor` richiama `devices.list_devices()` NON filtrata | VERO: `list_devices()` non filtrato nel blocco placement di `invoke_executor` | Filtro `owner_user_id == (actor or "host")` anche in `invoke_executor` prima di `choose_placement`. Mono-utente (host/host) invariato. |
| **F5** (resource-safety) | La tabella `invocations` è append-only (spool client e join session hanno GC, questa no) | VERO: nessun purge in `invocations.py`; `state_reaper` wire-a undo/join/turn ma non invocations | `invocations.purge_invocations(older_than_days=30)` (confronto su `delivered_epoch` numerico, purga solo terminali done/failed, mai gli in-volo) + wire nel `task_state_reaper` (env `METNOS_INVOCATIONS_RETENTION_DAYS`). |
| **D1** | `client-rs/README.md` obsoleto (mTLS, `transport.rs` inesistente, «W1-2 MVP», «Windows W3 futuro») | VERO: menziona mTLS/transport.rs/W1-2 | README riscritto allo stato reale: client **0.2.7**, **HTTP firmato Ed25519** (NON mTLS), moduli reali (runner/wire/sandbox_windows/proclock/pyenv), **Windows W3.3 chiuso**. |
| **D6** | `chat_driven_placement_R1_assessment.md` è storico (i suoi problemi sono chiusi) | VERO: le 5 osservazioni R1 fixate in `5e6e828` | Aggiunta nota di stato «STORICO/AGGIORNATO — 5 osservazioni FIXATE» in testa. |

**Extra**: aggiunto test-guard di regressione (`test_target_device.py::
DeviceEligibleManifestTests`): ogni executor in `DEVICE_ELIGIBLE` DEVE dichiarare
`platforms` (incl. windows) + `[placement] device_ok` — impedisce il ritorno di
F1/F2.

---

## ⏸ Verificato VERO ma NON fatto (con motivo)

| # | Rilievo | Perché rimandato |
|---|---|---|
| **F4** (media) | Endpoint admin token/join non propagano `owner_user_id` (default `host`) | **Prerequisito multi-utente**, non urgente in mono-utente. Serve design owner-nella-UI + validazione permessi + unicità nome per owner. |
| **F6** (alta prima dei mutanti) | Exactly-once non garantito se il client crasha dopo il side-effect e prima dello spool | **P3, prima dei mutanti remoti** — oggi la whitelist device è read-only, nessun executor mutante gira sul device. Da definire con R3 (idempotency-key `invocation_id`, `started` durable, o mutanti solo con reverse+audit+conferma). |
| **F7** (media) | Sandbox Linux remoto può degradare a `sandbox:"none"` senza bwrap | **Decisione di policy (Roberto)**: accettare `none` solo per read-only vs fail-closed per tutti. Non è un bug — l'esito è ONESTO (`sandbox:"none"` nel result, §2.8). |
| **F8** (bassa/media UX) | Un target device ESPLICITO su executor non impacchettabile gira in locale senza tag | Scelta di design (la stessa logica che protegge lo sticky implicito). Miglioria UX possibile (messaggio «questa operazione gira sul server») ma bassa priorità. |
| **F9** (media manutentiva) | `engine/dispatch.py` = lunga sequenza di guard con ordine semantico implicito | **P4, refactor grosso** (estrarre pipeline `pre_pool/post_propose/post_cache/pre_execute` + snapshot-test dell'ordine). Rischioso, non urgente; il sistema funziona. |
| **F10** (bassa) | `synt.Composer` (BFS mnestoma) non è remote-aware (placement/piattaforma/heartbeat/sandbox) | Documentato come aperto in `synt.html`; il default server-side è corretto oggi. Rilevante solo quando synt genererà catene operative sul device. |
| **D2/D3/D5** | `internal/design/remote-executors.html` mescola stati storici (mTLS come corrente, client 0.2.2/0.2.3/0.2.6, `sandbox_unavailable` pre-W3.1, W1-2) | Le **doc PUBBLICHE** (`remote_executors.html`, `sandbox.html`) sono **già corrette** (D3: 0.2.7, Job Object, W3.3 2026-07-03). Resta da storicizzare le sezioni vecchie del design INTERNO (sezione «Stato corrente normativo» in testa + resto in appendice). Doc-work medio, non bloccante. |

---

## ⚠ Note oneste (§2.8)

- **F1 non validato DAL VIVO su Windows**: la correzione è stata verificata via
  `loader.load_catalog` (platforms caricato) + `choose_placement` su device
  Windows-mock (accettato). Il test end-to-end REALE di `list_dirs` sul PC
  richiede che il client ri-scarichi lo shim con `path_alias` (C7) — quindi un
  **restart del client** sul PC. La correzione del *rifiuto piattaforma* è certa;
  l'esecuzione remota di `list_dirs` su Windows resta da confermare al prossimo
  reinstall.
- **P0.2** (test `list_dirs` remoto su Windows-mock): realizzato come **guard sul
  manifest** (platforms + device_ok per tutti i device-eligibili); un e2e completo
  con device Windows-mock attraverso la pipeline NON è stato aggiunto.
- **F2 compat-shim**: `DEVICE_ELIGIBLE` è mantenuto come fallback — la migrazione a
  puro-manifest-driven (rimozione del set) è lasciata a quando tutti i futuri
  executor remoti dichiareranno `device_ok`.

---

## Validazione (gate)

- **Suite completa: 3309 passed**, 27 skipped (falle residue = flaky pre-esistenti
  deselezionate). Mirati: `test_target_device + test_placement +
  test_agent_server_remote` 52/52; guard F1/F2 + purge unit verdi.
- Turni reali su prod (`.33`) dopo i fix: device («…sul PC-ROBERTO» →
  `target_device=PC-ROBERTO`, eseguito sul PC) + normale («che ore sono» → locale)
  OK. Health 200.
- Re-firma manifest eseguita (§7.10); tutti i cambiamenti in **commit isolati e
  revertibili**; branch non pushato.

## Commit

- `f16e0ef` — F1/F2/F3/F5 (codice + manifest re-firmati).
- `cccd866` — D1 README + D6 R1-note + test-guard F1/F2 + commit del report di
  review di riferimento.

## Follow-up suggeriti (dai rimandati)

1. **[multi-utente]** F4 (owner nei token/join) + completare l'isolamento owner
   end-to-end — prerequisito prima di aprire a guest.
2. **[prima dei mutanti]** F6 (policy exactly-once) + F7 (policy sandbox) prima di
   abilitare write/move/delete remoti (C7 R3).
3. **[manutenzione]** F9 (pipeline di guard in `dispatch.py`) + D2/D3/D5
   (storicizzare il design interno).
