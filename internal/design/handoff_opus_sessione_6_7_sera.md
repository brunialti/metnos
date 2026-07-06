# HANDOFF per Opus — sessione 6/7/2026 sera (Fable, risorse in esaurimento)

> **Chi sei**: LLM esecutore che subentra a Fable. **Prima di tutto**: leggi
> `CLAUDE.md` + `CLAUDE.mutabile.md` + il session log
> `~/.claude/projects/-opt-metnos/memory/project_session_6_7_2026.md` (ROUND 1-6).
> Questo file = stato ESATTO + prossimi passi con dettaglio operativo.
> File:riga verificati al momento della scrittura (post-commit sessione 6/7 sera).

## 1. Stato del sistema (rivalutato, sostituisce «HEAD 4cdae18, suite 3365»)

- **HEAD**: vedi `git log -1` — la sessione 6/7 sera ha committato su
  `session/detection-lexicon-i18n` (branch dev, MAI pushare: origin è pubblico
  snapshot-only). Base precedente: `4cdae18`.
- **Suite**: ~3445 test (era 3365; +80 della sessione). Run:
  `python3 -m pytest runtime/tests -q -p no:randomly` (~2min). 2 flaky
  order-dependent NOTI e pre-esistenti (verdi isolati):
  `test_i18n_db_v2::test_align_messages_in_sync_no_op`,
  `test_store_entries::TestDormancyGate::test_excluded_when_registry_empty`.
- **Prod**: live su `.33`, `metnos-http.service` (SYSTEM), riavviata con tutto.
  Restart: `sudo -n systemctl restart metnos-http.service` (passwordless).
- **PC device**: `PC-ROBERTO` (7bd3da08…), client 0.2.14, os windows. ⚠ Lo
  **shim runtime sul PC è STANTIO** (memoizzato 1×/processo, vedi §3.1):
  `backends/files/local.py` sul device NON ha ancora: campi strutturati
  expected/actual nei failed[], espansione glob §2.4, copy-mode di move,
  entry-passthrough `{dst}`. Si rinfresca al **restart del daemon** sul PC.
- **Downloads di Roberto**: 2 delete di massa reali accadute il 6/7
  (195 file/2.57GB + 488 file). Ripristinati: 455/488 automatici + script
  `internal/reports/restore_981_residui_copie.ps1` (33 dedup) +
  `internal/reports/restore_downloads_1ba8e2c4.py` (i 195, match size→blob,
  dry-run default) — **da eseguire SUL PC, decisione di Roberto**.

## 2. Cosa ha consegnato la sessione 6/7 sera (tutto committato, prod-live)

Dettaglio narrativo nel session log (ROUND 1-6). Sintesi per file:

1. **Recovery deterministici** `runtime/engine/recovery_metis.py`:
   `_fix_dir_passed_as_file` (delete/`*_files` su DIRECTORY → prepone
   find_files, campi strutturati O nome-tool come garanzia direzione) +
   `_fix_glob_passed_as_path` (glob literal → find_files patterns).
2. **Onestà §2.8** `runtime/agent_runtime.py`: split not-found/falliti per
   error_code (`*_NOT_FOUND` suffix rule); `MSG_MUTATE_TIMEOUT_UNCERTAIN` per
   step mutante in timeout; `result.message` dell'executor vince sul
   degenerate; `_undo_done` chiude anche su esito PARZIALE (ok_count>0).
3. **i18n §7.13**: `agent_runtime.py` = 0 `register_key_if_missing` inline;
   chiavi nel **seed** `install/data/i18n_seed.sqlite` (SoT, procedura
   INSTALL_NOTES:116) + guard `runtime/tests/test_seed_i18n_gate_keys.py`
   (_REQUIRED_HONESTY_KEYS et al.). ⚠ `remote_exec.py`/`change_intents_i18n`/
   `detection_lexicon` hanno ANCORA `_rk` inline builtin → sweep da fare (§4.6).
4. **Gate mutazioni di massa** `runtime/engine/dispatch.py::_insert_mass_mutation_gate`
   (+`_glob_paths_to_find_files`): delete/move di massa → `get_approval` con
   `guard_count`/`guard_threshold` (`METNOS_MASS_MUTATION_THRESHOLD`, default
   20, ≤0 off; skip turni schedulati). Cablato su fastpath/autopath/main/
   RECOVERY. `get_approval`: soglia + **fmt='form' su http** (marker
   `INLINE_FORM:` → chat monta l'iframe con Approva/Rifiuta).
5. **A.0 risultato-tardivo** `runtime/invocations.py`: colonne
   `abandoned_by_turn`, `origin_actor`, `origin_channel` (+migrazione
   additiva); `mark_abandoned` su timeout (`remote_exec.invoke_remote`);
   submit tardiva → `_close_late_undo` (undo.close_pending_for_turn) +
   `_notify_late_outcome` (A.2).
6. **A.2 notifiche prossima-visita** `runtime/user_notices.py` (append/drain
   per (channel,actor)) + drain in `TurnLog.write()` DOPO tutte le riscritture
   del final. Chiavi `MSG_LATE_RESULT_{DONE,FAILED}` in live+seed.
7. **Deadline scalate**: `remote_exec._scaled_timeout_s` (mutante >10 item →
   30+1s/item cap 600) usata da invoke_remote E dal reverse device di
   `executors/undo_last_turn` (che ora passa `deadline_ms` alla enqueue).
8. **Reverse restore robusto** `runtime/reverse_patterns.py`
   (restore_blob_backup remoto): BATCH a chunk 100 + `copy: true`
   (blob dedup) + template `{dst}` (entry-passthrough in
   `backends/files/local.py::_entry_fields`); `move()` supporta `copy`.
9. **Gate-resume**: salva `user_query_raw` (runtime_ctx) — la query CON
   destinazione; prompt gate dice il device vero (`target_device` in ctx).
10. **turn_id ai device**: `invoke_remote` inietta `METNOS_TURN_ID` via
    `env_injections` (client la applica già, nessun rebuild).

## 3. AREE APERTE — cosa fare, in ordine di valore (dettaglio per Opus)

### 3.1 Shim content-addressing (client 0.2.15) — ✅ FATTO (commit 685fe44, sera 6/7)
Implementato e VALIDATO sul PC reale: `runtime/shim_manifest.py` (SoT+sha,
cache mtime); poll annuncia `shim_sha256` nell'ENVELOPE (0.2.14-safe);
client 0.2.15 (wire/executors/runner) confronta e re-pull su drift; build+
mirror firmati, PC self-aggiornato. Prova live: dopo l'update, l'undo batch
{dst}+copy di 455 file è riuscito al PRIMO retry (Downloads 1→456).
RESIDUO COSMETICO: il final dell'undo dice «5 elementi» (conta le STAGE
del reverse, non i file: aggregazione in undo_last_turn/_reverse_on_device
o nel formatter del final — total_ok è giusto, è il rendering del final).

### 3.1.bis (storico — design originale, superato)
**Problema**: `client-rs/src/runner.rs:254-263` — lo shim (runtime subset sul
device: executor_helpers, messages, path_alias, backends/files/local.py,
platform_policy, config) è scaricato UNA volta per processo e NON è
content-addressed: ogni fix ai moduli runtime NON raggiunge i device finché il
daemon non si riavvia (bug mordente: 2 volte in questa sessione).
**Fix progettato**:
1. Server: calcola lo sha256 del bundle shim (canonical dei files, già
   assemblato in `runtime/agent_server.py::shim_bundle:342-374`); includilo
   (a) nella response `/agent/shim` (campo `sha256`) e (b) in OGNI invocation
   payload (`invocations.enqueue_invocation` → payload["shim_sha256"]) —
   cache in-process con invalidazione mtime dei 8 file.
2. Client (0.2.15): salva lo sha in `shim/.sha256` alla ensure_shim; in
   `runner.rs` prima di run_sandboxed confronta `inv.shim_sha256` (Option,
   ignorato se assente → compat) col salvato → mismatch = ri-`ensure_shim`.
   Struct `Invocation` in `wire.rs`: aggiungi campo `#[serde(default)]`.
3. Rollout: build (`client-rs`, cargo; mirroring: vedi INSTALL_NOTES §10.7 e
   gli script usati per 0.2.7→0.2.14 — cerca `mirror`/`selfupdate` in
   `scripts/`), bump versione, il PC si self-aggiorna (validato, ADR 0184).
**Test**: e2e Linux `scripts/e2e-remote-executor.sh` + unit ensure_shim.
⚠ Verificare che il payload firmato con campo nuovo non rompa la verifica
firma su client vecchi (payload è firmato server-side: i client 0.2.14
verificano i bytes ESATTI → campo extra ok perché firmato insieme).

### 3.2 Fase 7 — spec pronte per te in `internal/design/spec_fase7_*`
- **`spec_fase7_disconnect_robustezza.md`**: A.0 FATTO (questa sessione,
  v. §2.5-6) salvo: notifica IMMEDIATA telegram (oggi prossima-visita; il
  daemon telegram può pushare? valuta un notificatore nel daemon che polla
  user_notices — 20 righe); **A.1 differito esplicito** (device offline →
  chiedi «eseguo quando torna online?»; enqueue `deferred=1`+TTL; riusa
  needs_inputs; vedi spec punti A.1-A.2) — NON iniziato; **B robustezza**
  (B.1 LRU executed set client; B.3 backoff poll; B.4 stato `expired`);
  **C client macOS** (parità Linux-senza-bwrap; spec dettagliata).
- **`spec_fase7_w4_appcontainer.md`**: sandbox forte Windows (W4.1-4) —
  ortogonale, parallelizzabile.

### 3.3 Compound «file E directory» — ✅ FATTO nel recovery (5955098); residuo main-path
Il RECOVERY ora accoda find_dirs→delete_dirs quando intent.actions porta
{delete,dirs} (validato e2e: 24 file+2 dir → 0/0, gate incluso). RESIDUO:
se il proposer pianifica DIRETTAMENTE find_files→delete_files corretto
(niente recovery), la clausola dirs è ancora droppata → guarda il guard
`enforce_missing_clauses` in dispatch (perché non la impone?) o few_shot
planner. Anche: undo di delete_dirs (le dir rimosse non sono blob-backed).

### 3.3.bis (storico)
Query «cancella i file e le directory nella directory X»: le DIRECTORY non
vengono MAI toccate (find_files include_dirs=false; delete_dirs mai
pianificato). Piano corretto: find_files→delete_files→find_dirs→delete_dirs
(o delete_dirs force sui subdir dopo lo svuotamento). Strade: (a) prompt
planner few_shot per il pattern; (b) recovery/guard che, con intent.actions
contenenti {delete,files}+{delete,dirs} e UNA dir bersaglio, compone il piano
4-step. Attenzione al gate (le due delete vanno ENTRAMBE gated — oggi il gate
si ferma alla PRIMA azione distruttiva; estendere a multi-gate o gate unico
con somma). Test: la query esatta di Roberto su dir sacrificabile.

### 3.4 Approval-gate: rifiniture
- Il prompt del gate mostra `${stepN.@count}` NON risolto se il canale mostra
  il prompt PRIMA dell'esecuzione di find_files? No: il gate gira DOPO il
  producer (from_step) → @count risolto. MA per il caso inline-glob riscritto,
  verifica il rendering su Telegram (fmt telegram_inline: bottoni nativi).
- `guard_threshold` per-verbo/per-utente (W2 user_prefs?) — proposta, chiedi
  a Roberto.
- Turni schedulati: il gate skippa — Roberto potrebbe volere un
  consent-differito (accoda e chiedi al mattino). Proposta da fargli.

### 3.5 Undo — residui
- `latest_turn_done` è per-ACTOR ma «ultimo turno» può sorprendere (vedi
  docstring undo.py). Nessun bug aperto; monitorare.
- Blob sul device: crescono in `_history/<turn>/blob` — serve TTL/purge
  device-side (il server ha purge_older_than; il device NO). Piccolo: job nel
  client o comando NL schedulato (ADR 0186 style).

### 3.6 Sweep i18n §7.13 residuo
`register_key_if_missing` inline con testo bilingue resta in:
`runtime/remote_exec.py` (_register_i18n_keys — 3 chiavi, già nel seed),
`runtime/change_intents_i18n.py`, `runtime/detection_lexicon.py` (+ vedi grep).
Per i BUILTIN: sposta i testi nel seed + `msg()` puro + guard keys (stesso
pattern di questa sessione). Per synt_multistage è LEGITTIMO (scaffolding).
Collegato: `project_i18n_translation_quality_fable.md` (sweep prompt corpus).

### 3.6.bis Code aperte ROUND 9-10 (23:00-23:40, ultime della sessione)
1. **Task github (scheduler_v2 id=35)**: DOPPIO turno per fire (23:16:44 E
   23:16:45 — il secondo fallisce UNIQUE constraint da INSERT senza key).
   Perché due run? Guardare il daemon scheduler (max_concurrent/retry).
   Cache avvelenate purgate 2 volte (fastpath 268+313, autopath
   find_issues__54f2): se ricompare un piano find_entries-only per «trova
   issue github», la FONTE è a monte (routing pool OVERSIZED 33 per
   (find,issues) — vedi memoria routing_pool oversized).
2. **Anomalia e2e pv2** (turno conv pv2-e2e, 23:3x): gate glob-riscritto →
   find_files(base=/tmp/metnos_pv2, ["*"], recursive=false) ha visto SOLO
   desktop.ini mentre nel dir c'erano ANCHE 3 .txt appena creati → gate «1
   elementi» → delete solo desktop.ini (rifiutato) → final
   MUTATE_FAILED_NONE_DONE. I 3 .txt risultavano GIÀ spariti: sospetto un
   replay 0b/cache dal turno gemello pv-e2e o un doppio-run. Riprodurre con
   conversazioni e path VERGINI e tracciare match_source.
3. **change_intents_i18n**: bootstrap strutturato di chiavi bilingui nel
   sorgente — migrare le chiavi nel seed + msg() puro (stesso pattern della
   sessione), poi ritirare il bootstrap.
4. Pulizia schedule_entries morte (33/34/36 disabled) — decisione Roberto.

### 3.7 Cose piccole note
- Redactor troppo aggressivo sui path Windows nei turn record
  (`C:\User<REDACTED:cred>` — falso positivo pattern user). Solo estetica log.
- `_error "None"` nel driver restore: move_files device può rispondere done
  con result senza `error` per item falliti — arricchire (minore).
- `test_i18n_db_v2` + `test_store_entries` flaky order-dependent: fixare
  l'isolamento (pollution altrui, non di questa sessione).

## 4. Vincoli operativi (NON violare)
- §7.10: edit executor/manifest → `python3 runtime/sign.py sign executors/<n>`
  + restart + commit manifest+sig INSIEME.
- Ogni cambio funzionale → ≥1 turno reale `/agent/turn` (curl in
  `reference` gotcha del session log) + test cluster + full suite.
- MAI push del branch dev; MAI Co-Authored-By nei commit; chiedere PRIMA di
  committare (Roberto autorizza per sessione).
- Store reali MAI nei test (DB isolati, NOTICES_DIR incluso).
- Il gate di massa: NON abbassare la soglia default senza Roberto.
