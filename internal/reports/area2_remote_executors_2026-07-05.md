# Report AREA 2 — Remote executors: read-only + MUTANTI con undo round-trip — 5/7/2026

**Riferimento**: mandato Fable Area 2 · ADR 0183 · Branch `session/detection-lexicon-i18n` (non pushato) · Prod live · Client 0.2.10 mirrorato.

## Esito: CONSOLIDATO ✓ (CP1-CP4) — validazione Windows reale = unico residuo, da coordinare

### CP1 — Shim a chiusura ad ALBERO (client 0.2.10)
`shim_bundle` spedisce `backends/{__init__,files/__init__,files/local}.py` + `platform_policy.py` + `config.py` (lista esplicita §7.2; chiusura a module-load misurata: stdlib + moduli già nel bundle). Client: `ensure_shim`→`shim_rel_path` valida ogni segmento (no `..`/`\`/`:`/assoluti) e ricrea l'albero; fn pura + unit; cargo 7/7 + clippy pulito. Buildato, firmato e **mirrorato** (Linux+Windows).

### CP2 — Lazy-gw sui dispatcher
`google_workspace` import LAZY con degrade STRUTTURATO (`ok:false`, mai traceback al runner §2.8) su **find/read/write_files** e — scovato dall'e2e mutanti — **create/delete/find_dirs** (il reverse `delete_dirs` moriva a module-load sul device). `test_device_shim_closure.py`: i **9** executor files/dirs importano e invocano nel layout device con repo bandito da sys.path.

### CP3 — Read-only remoti
`find_files`/`read_files`: `platforms` + `device_ok=true`. E2E `c7-validate-files-remote.sh` (client Rust vero): find filtra `*.txt` sul device, read porta il CONTENUTO, list regressione — tutti PASS.

### CP4 — MUTANTI + undo round-trip (ADR 0183)
- **Scoperta abilitante**: l'undo era MORTO in prod — la cancellazione del planner legacy (`af6c7b8`, notturno 4/7) aveva rimosso l'unico scrittore del log. Ora vive al **choke-point** `invoke_executor` (pending pre-exec + done post-ok + campo `device`), copre ogni path per costruzione.
- **Reverse device**: chiamate executor deterministiche (`build_remote_reverse_calls`) accodate ALLO STESSO device (§2.9) da `undo_last_turn`, attesa bounded, «eseguito-ma-fallito» ≠ «mai arrivato» con evidenza nelle stages; timeout → failed RITENTABILE.
- **delete server-only**: `restore_blob_backup` non remotabile (blob sul device, log sul server) → niente device_ok; con target device gira locale. Asimmetria voluta: la chat non cancella sul PC, l'undo sì ma solo ciò che ha creato lui.
- **E2E** `c7-validate-mutants-remote.sh` TUTTO PASS: write sul device → undo dal server → file RIMOSSO sul device; move → undo → TORNATO; gate delete → locale.
- **LIVE prod**: write via chat → «annulla» → «Annullato: write_files (1 elementi)» → file rimosso. Capability ripristinata.

## Prove del cancello §A
Suite area 495 pass (unico rosso = flaky prefilter pre-esistente, verde isolato) · e2e read-only + mutanti + A-D remote TUTTI VERDI · live server validato · i18n 2 chiavi IT+EN (DB prod + seed installer) · docs pubbliche IT+EN aggiornate e **deployate** · ADR 0183 + CLAUDE §11 v22j + indice +5 · 8 commit (`4592162`…`8b2cb12`) · working tree pulito.

## ⚠ Residui / follow-up
1. **PC-ROBERTO reale**: client 0.2.7 con shim stantio → i turni device falliscono ONESTI (visto live: ModuleNotFoundError nel result). Serve portare il PC a **0.2.10** (reinstall/installer) — da coordinare; poi ripetere la validazione mutanti sul Windows vero.
2. **Sticky-device + path server-like** (visto live): la destinazione appiccicosa manda al PC anche query con path del server (`/opt/metnos/...`→`C:\opt\...` not-found). Hint forma-path→host nel resolver placement = follow-up.
3. Blob round-trip per delete remoto (design esplicito, ADR 0183 D3) · ACL scrittura Windows = W4.
