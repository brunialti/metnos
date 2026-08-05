# ADR 0183 — Mutanti sui device remoti + undo round-trip (C7 Area-2 CP4)

- **Stato**: ACCETTATO (validato e2e + live, 5/7/2026).
- **Contesto**: mandato Fable Area 2. I read-only (find/read/list) girano sul device (CP1-3, shim ad albero + lazy-gw). I MUTANTI (write/move/delete) richiedevano: (a) un log undo funzionante, (b) un reverse che ribalti sullo STESSO host (§2.9), (c) una politica per i pattern non remotabili.

## Scoperta abilitante: l'undo era MORTO in prod
La cancellazione del planner legacy (`af6c7b8`, notturno 4/7) ha rimosso l'**unico scrittore** del log undo (viveva nel loop del planner). Il path engine non ne ha mai avuto uno: ogni mutazione era diventata silenziosamente non annullabile (§2.8). Nessun test lo copriva.

## Decisioni

### D1 — Scrittore al CHOKE-POINT
`agent_runtime.invoke_executor` scrive `pending` PRIMA dell'esecuzione e `done` dopo l'esito ok (`_undo_pending`/`_undo_done`, fail-open). Copre TUTTI i path per costruzione (engine, device, futuri). Il record `pending` porta **`device`** (id remoto) quando l'op gira su un device. Guard: mai record per `undo_last_turn` stesso (§4.5). Test: `test_undo_chokepoint.py` (round-trip reale write→undo→file rimosso).

### D2 — Reverse device = chiamate executor accodate ALLO STESSO device
`reverse_patterns.build_remote_reverse_calls(names, plan, results)`: traduzione DETERMINISTICA dei pattern fs puri-path in chiamate agli executor mutanti già firmati — NIENTE replica del catalogo in Rust (i rail coda-firmata + executor-firmati bastano):
- `swap_src_dst` (fs) → `move_files` — nome invariato: un gruppo per cartella-sorgente con `dst_template="<parent>/{name}"`; RENAME: chiamata singola con template letterale. Stringhe path ORIGINALI preservate (mai riscrivere i separatori). Le coppie IMAP restano server-side.
- `delete_created_paths` → `delete_files(paths=creati)` + `delete_dirs(force=false)` sui `dirs_created`.
- `delete_created_dirs` → `delete_dirs(force=false)`.
- `restore_blob_backup` → **NON remotabile** (il blob vive sul device, il log sul server): finisce in `unsupported`, riportato onesto.
`undo_last_turn._reverse_on_device`: enqueue + attesa sincrona bounded (`METNOS_UNDO_DEVICE_TIMEOUT_S`, default 25s); stati terminali `done/failed/error/denied/expired`; «eseguito-ma-fallito» distinto da «mai arrivato» con l'evidenza (`state` + `device_result` troncato) nelle `stages` del dettaglio. Timeout/offline → **failed ritentabile** (mai `undone` senza ribaltamento reale — principio 29/4).

### D3 — delete_files resta SERVER-only (CP4) → **CHIUSO 6/7: delete REMOTABILE**
~~restore_blob_backup non remotabile~~ → soluzione SENZA blob sul filo: il blob sta GIÀ sul device (`local.delete` lo scrive nella history del device prima dell'unlink, §2.9) ⇒ **il restore è un `move_files` device-locale blob→path** (executor già device_ok, COPY-check-DELETE). `build_remote_reverse_calls` traduce `restore_blob_backup` in move singole per file; righe senza blob_path → unsupported onesto. `delete_files` e `delete_dirs` (rmdir solo-vuote, non revertibile per contratto) ora `device_ok=true`. Validato e2e isolato E sul PC Windows reale (delete → undo → contenuto ripristinato bit-perfetto, verificato con read remoto).

### D4 — Manifest e chiusura
`write_files`/`move_files`: `platforms=["linux","windows"]` + `[placement] scope="any" device_ok=true` (come list_dirs/find/read). Sweep lazy-gw esteso a **create_dirs/delete_dirs/find_dirs** (avevano l'import eager di `google_workspace` — scovato dall'e2e mutanti: il reverse `delete_dirs` moriva a module-load sul device). `test_device_shim_closure.py` ora importa TUTTI i 9 executor files/dirs nel layout device.

## Prove
- e2e `scripts/c7-validate-mutants-remote.sh` (client Rust 0.2.10, server isolato): write sul device → undo dal server → **file rimosso sul device**; move → undo → **tornato alla sorgente**; gate delete → locale. TUTTI PASS.
- Live prod: write via chat → «annulla» → `Annullato: write_files (1 elementi)` → file rimosso (capability ripristinata).
- Unit 6 (`test_undo_chokepoint.py`) + chiusura device 3 + suite area 495.
- i18n: `ERR_UNDO_REMOTE_UNSUPPORTED`/`ERR_UNDO_DEVICE_UNREACHABLE` IT+EN nel DB prod E nel seed installer.

## Limiti onesti / follow-up
- **PC Windows reale**: client 0.2.7 con shim stantio → i mutanti falliscono onesti finché il client non riparte ≥0.2.10 (validazione Windows da coordinare).
- **Sticky-device + path server-like**: la destinazione appiccicosa instrada anche query con path palesemente del server (es. `/opt/metnos/...` → `C:\opt\...` not-found sul PC). Migliorabile con un hint forma-path→host nel resolver placement (fuori scope CP4).
- ACL di scrittura Windows (AppContainer) resta W4.
- Windows-side `delete_dirs` reverse su alberi con junction/ACL: comportamento = quello di `local.py` (`force=false`, solo directory vuote).
