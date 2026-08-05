# Audit manifest executor — reperti + piano di fix

> **Prodotto il 22/7/2026** da una revisione a due livelli sugli **83 manifest** di `executors/`:
> (1) il **linter esistente** `runtime/manifest_lint.py` per il livello meccanico; (2) **sei revisioni
> adversariali per dominio**, ciascuna con lettura di `manifest.toml` + `<name>.py` + `manifest.lang_state.json`
> per il livello semantico e la coerenza manifest↔codice. SOLA LETTURA: nessun manifest è stato modificato.
> Documento di CONSEGNA a un agente per i fix.
>
> **Come usarlo.** Ogni reperto è marcato `[TESTO]` (fraseggio model-facing: description/affinity/i18n — va
> calibrato per il modello locale MEDIO, non frontier) o `[STRUTT]` (args/enum/schema/capabilities/
> reverse_pattern/`[execution]`/flag). **Prima i §Temi trasversali M1-M8**: quasi tutti i reperti gravi
> discendono da poche radici; alcune radici sono fix di CODICE RUNTIME (undo_last_turn, coerce_args,
> _OBJECT_REGISTRY), non di manifest. Poi la lista per-executor. Dopo OGNI manifest toccato: re-sign
> obbligatorio `python3 runtime/sign.py sign executors/<name>` (§7.10) + commit manifest+sig insieme; per i
> fix di undo/consenso, ≥1 turno reale `/agent/turn` (§8.5) con backup dello store; mai purge su store reali.
>
> **Converge con l'audit del codice** `internal/design/audit_metnos_multidominio_21_7.md`: [M1] estende
> [T4] (undo gw), [M5] estende [SAFE-P0] (`_confirmed`), [M4] estende [SAFE/ADR0193], [M6] estende [T2/NL].

---

## Sommario

- **Linter meccanico** (già in `$CLAUDE_JOB_DIR/tmp/manifest_lint_full.txt`, rigenerabile con
  `python3 -m runtime.manifest_lint`): 153 warning `length` (testa/desc/arg oltre soglia), 7 `output_shape`
  (§2.6), 4 `non_refs` (riferimenti morti nel NON: `compress_files`→extract_files, `create_calendars`→
  list_calendars, `create_events`→extract_entries, +1). **L'unico ERROR** (`get_approval`, PATTERN con arg
  `args`) è un **FALSO POSITIVO**: `args` è la property annidata di `on_approve`, non un arg top-level.
- **Semantico** (6 domini): ~70 reperti. **1 P0**, ~13 P1, il resto P2/P3. La distribuzione è concentrata:
  8 temi trasversali coprono la maggioranza.
- **Radice dominante**: `reverse_pattern` dichiarati fuori dal catalogo chiuso o incoerenti con i result
  emessi → **undo disonesto** su tutto il fronte mutante/biometrico (§2.3/§2.8). È un difetto sistemico
  amplificato da due punti di codice runtime, non un errore isolato per-manifest.
- **Nota di metodo (falsi positivi utili)**: il linter meccanico da solo avrebbe mandato l'agente a
  «sistemare get_approval» (falso) e a tagliare 153 descrizioni; il livello semantico ha trovato i difetti
  che contano (undo rotto, autorità sovra-concessa, falso successo) che il linter non vede.

---

## Temi trasversali (M1-M8)

### M1 · `reverse_pattern` rotto → UNDO DISONESTO  **[headline — §2.3/§2.8]**
Radice architetturale (CODICE, non manifest): `undo_last_turn.py:175` è un `if ex.reverse_pattern: apply_patterns() else: module.reverse()` **binario**. Se `reverse_pattern` è truthy ma NON nel catalogo chiuso
(`reverse_patterns.py::PATTERNS`), `apply_patterns()` ritorna «unknown» e il `module.reverse()` (priority-2,
che spesso ESISTE e funziona) **non viene mai raggiunto**. Aggravante: `executor_standard.py:306` OBBLIGA un
`reverse_pattern` quando `revertible=true` → gli autori inventano etichette bespoke fuori catalogo → auto-sabotaggio.

Casi confermati (leggendo manifest+codice+catena undo):
- **[P0][STRUTT] `delete_persons`** · `reverse_pattern="restore_person"` (fuori catalogo). Il `.py:102` HA un
  `reverse()` funzionante (ripristina da blob biometrico) ma è OSCURATO. `revertible=true` è FALSO. Scenario:
  «cancella l'enrollment di tutti» (`all=true`) → purge registro volti → «annulla» fallisce (`undone=0`), il
  blob esiste ma nessuno lo rilegge.
- **[P1][STRUTT] `delete_events`** · `reverse_pattern="restore_event"` fuori catalogo; `.py:160` ha il
  `reverse()` (re-inserisce il VEVENT ICS) oscurato. Falso per il path LOCAL.
- **[P1][STRUTT] `create_calendars`** · `reverse_pattern="delete_calendars_by_id"` ma `"calendars"` è
  ASSENTE da `_OBJECT_REGISTRY` (`reverse_patterns_patch.py:43-48`) → pattern mai registrato → undo fallisce.
  (Contrasto: `create_events`→`delete_events_by_id` è registrato e funziona.)
- **[P1][STRUTT] `change_files_format`** · `delete_created_paths` ma i result riga-riuscita sono
  `{src,dst,ok,elapsed_ms,size_bytes_*}` senza `created`/`path` → `_delete_created_paths` salta tutto → no-op.
- **[P1][STRUTT] `set_signatures`** · `delete_created_paths` ma il `.py` NON tocca il filesystem (fa
  `store.upsert_user` sulla curatela blacklist/whitelist comandi) e ritorna dict senza `results`/`path` →
  «annulla» lascia la blacklist. Reverse di dominio proprio sbagliato.
- **[P2][STRUTT] `create_files_doc`** · `delete_files_by_id` → `build_undo_calls` instrada a `delete_files`
  che è LOCALE (`enum=["local"]`, accetta solo `paths`) → `paths=None` → undo failed, il Google Doc resta.
- **[P2][STRUTT] `create_files_spreadsheet`** · il manifest dichiara `delete_created_paths` ma il codice/`_undo`
  del ramo gw emette `delete_files_by_id` → sul ramo Google si applica il pattern del manifest su un result
  senza `path`/`created` → no-op; il ramo LOCAL funziona.
- **[P2][STRUTT] `write_files_doc`** · `revertible=false` nel manifest MA `reverse()` implementato nel codice +
  commento «reversibile» → append annullabile ma mai annullato (undo_last_turn salta a `revertible=false`).
- **[P2][STRUTT] `compute_signatures`/`get_signatures`** · `reverse_pattern="module.reverse"` (fuori
  catalogo, e nessun `def reverse()` nei due `.py`); `get_signatures` è verbo READ → non dovrebbe essere
  `revertible`.
- **[P2][STRUTT] `write_files`** (mode=overwrite default) · su file preesistente sovrascrive con
  `created=false` e **nessun blob-backup** → `revertible=true` falso sul contenuto originale.
- **[P2][STRUTT] `move_files`** (overwrite=true) · `unlink`/`rmtree` del dst preesistente senza blob-backup →
  `swap_src_dst` lossy (ribalta il source, il dst clobberato è perso). §2.9 rispettato sul source, violato sul
  destination. [già in audit codice [SAFE-4]].

**MODELLO SANO da copiare**: `write_files_spreadsheet` fa il multistage `["restore_blob_backup",
"delete_created_paths"]` correttamente (blob-backup dei byte precedenti su overwrite/append, emette
`path`+`prev_blob_path` o `created`+`path`). `compress_files`/`create_dirs` corretti.

**FIX RADICE (ordine)**:
1. (CODICE) `undo_last_turn` deve cadere su `module.reverse()` quando `reverse_pattern` è truthy-ma-ignoto →
   sana `delete_persons` (P0), `delete_events`, `compute_signatures/get_signatures`, `write_files_doc` insieme.
2. (CODICE) aggiungere `"calendars"` a `_OBJECT_REGISTRY` (delete_calendars accetta già `calendar_ids`).
3. (CODICE-EXECUTOR) emettere `created`/`path` nei result dei creatori-locali (`change_files_format`); blob-backup
   su overwrite in `write_files`/`move_files` (copiare `write_files_spreadsheet`); reverse dedicato per
   `set_signatures` (ripristina `previous_kind`) o `revertible=false`.
4. (MANIFEST) allineare i `reverse_pattern` per-ramo dove il codice diverge (`create_files_spreadsheet`),
   e onestà `revertible=false` dove non c'è reverse reale.
5. Verifica: turno reale «crea/cancella X» + «annulla» per ogni dominio toccato (§8.5), store con backup.

### M2 · OUT/schema mentita rispetto al codice  **[§2.6/§2.10 — falso successo/pipe vuota]**
Il capitolo OUT e/o `schema_inline` promettono `entries=[...]` ma il `.py` ritorna dict flat top-level → un
`from_step` che si aspetta `entries` riceve vuoto, presentato come successo.
- **[P1][TESTO] `describe_numbers`** · OUT `entries=[{n,mean,...}]` ma ritorna `{ok,n,n_missing,statistics:{...}}`.
- **[P1][STRUTT] `compute_signatures`/`get_signatures`** · OUT/schema `entries` ma dict flat per-op/kind
  (`blacklist`/`whitelist`/`forbidden`/`seed_diff` top-level; solo `graylist`/`all`/`promotion_candidates`
  danno `entries`).
- **[P2][TESTO] `undo_last_turn`** · OUT `entries=[...]` ma `{undone_count,skipped_count,details,turn_id}`.
- **[P2][TESTO] `move_files`** · OUT `results=[{path,dirs_created}]` ma `results=[{src,dst}]`+`dirs_created`
  top-level.
- **[P2][STRUTT] `create_images_indices`** · `schema_inline={ok,schema_version?,error?}` ma ritorna 13 campi.
- **[P2][STRUTT] `set_signatures`** · `schema` dichiara `entries` mai prodotto.
**FIX**: allineare OUT/`schema_inline` alla forma reale (per-op se dispatched). Tutti [TESTO] salvo lo
schema formale [STRUTT].

### M3 · Args letti dal codice ma NON dichiarati nel manifest  **[rischio drop Guard #0]**
Il `.py` legge un arg che lo schema non dichiara → il Guard #0 (`coerce_args`) lo droppa come fuori-schema
(salvo i `_`-prefissi, che invece sopravvivono — vedi M5), rendendo la funzione irraggiungibile dal planner.
- **[P2][STRUTT] `find_images_indices`** · `names_op` (and/or) letto (`.py:922`) e non dichiarato; la
  description di `names` afferma «AND» soltanto → l'intento OR è irrappresentabile / droppato.
- **[P2][STRUTT] `login_sites`** · `_credential_mode`, `_otp_session_vars` letti e non dichiarati (mentre
  `act_sites` dichiara i suoi runtime-arg) → rischio rottura del resume 2FA/mandato (ADR 0190).
- **[P2][STRUTT] `consult_frontier`** · `max_tokens` letto (`.py:608,654`, default 4096) e non dichiarato.
- **[P2][STRUTT] `undo_last_turn`** · `_actor` (autorità isolamento) letto e non dichiarato (i fratelli
  dichiarano `actor` `runtime_resolved`).
- **[P2][STRUTT] `delete_persons`/`set_persons`** · `dry_run` letto e non dichiarato → droppato dal Guard #0
  (funziona solo via env `METNOS_DRY_RUN`).
**FIX**: dichiarare l'arg (con `runtime_resolved=true` per i `_`/identità), o rimuovere la lettura morta.

### M4 · `provider:access` senza clausola `when` → autorità sovra-concessa  **[estende ADR 0193]**
`capabilities.effective_capabilities` appende la `provider:access` INCONDIZIONATA quando manca `when`; il
binding monta home-credenziale RW + rete legata a OGNI invocazione.
- **[P1][STRUTT] `send_messages`** · `provider:access=google-workspace` senza `when` → su istanza con Google
  accoppiato, ogni invio (anche SMTP puro/Telegram) monta il token Google RW; l'unico ramo Gmail è
  `in_reply_to`. I fratelli read/move/set gate-ano con `when={arg=client,values=[google_workspace]}`.
- **[P2][STRUTT] `find_images_web`** · `provider:access=google-workspace` senza `when`, legata anche in modo
  TESTO (SearXNG, zero Google); e il ramo reverse usa Vision (credenziale distinta da Drive/Gmail).
**FIX**: condizionare la `provider:access` al ramo che la usa (`when` sul client/arg), come i fratelli.

### M5 · Consenso `_confirmed` non guard-owned  **[estende [SAFE-P0] audit codice]**
`coerce_args.py:67` preserva ogni chiave `_`-prefissa → `_confirmed`/`overwrite_confirmed` emessi dal
planner/da un replay cache/da injection saltano il gate umano.
- **[P1][STRUTT] `delete_calendars`** · cancellazione irreversibile a cascata sugli eventi; il gate è
  `a.get("_confirmed")`. Il manifest (giustamente) non lo dichiara, ma il coerce non lo difende. Aggravante:
  il resume legittimo mette `_confirmed:true` negli args → se quel piano entra in cache, un hit successivo
  cancella senza ri-confermare.
- **[P2][STRUTT] `set_credentials`** · `overwrite_confirmed` è arg PUBBLICO visibile all'LLM (mitigato:
  `replace=true` fa già lo stesso; `revertible=false`).
**FIX**: (CODICE) `coerce_args` con allowlist `_*` runtime-owned (drop di `_confirmed` dal planner; solo il
resume runtime lo re-inietta) — è lo stesso fix [T1] dell'audit codice.

### M6 · Affinity che contaminano il routing  **[estende [T2/NL] audit codice]**
- **Nomi propri di terzi (§7.5)**: `login_urls` affinity contiene «spaggiari» (prodotto reale) + «scuola»/
  «banca» (bias di settore). **[P2][TESTO]**
- **Nomi hardcoded (regola «mai liste nomi»)**: `find_persons_indices` ha «carol»/«bob». **[P2][TESTO]**
- **Termini rubati ai fratelli**: `get_urls`→«leggi»/«pagina» (invade read_urls_html); `find_dirs`→«list»/
  «elenca» (invade list_dirs); `find_images_web`→nomi-immagine nudi (invade find_images_indices locale);
  `find_contacts`→«persona»/«amici» (invade persons); `delete_events`→verbi delete nudi (co-causa misroute
  «cancella enrollment»); `group_entries`→group-by (ma fa merge+dedup); `get_persons`→bare «chi». **[P2][TESTO]**
- **Termini MANCANTI**: `get_files` affinity è 100% foto/EXIF ma è il SoT di **size** e **date** — nessun
  termine size/dimensione/byte/data. **[P2][TESTO]**
- **Boundary NON non-netti**: `get_persons`↔`read_persons` (identità vs biometria, non si citano),
  `describe_numbers`↔`compute_entries`, `read_files`↔i 5 format-specifici, `sort_entries`↔`order_*`
  (persistente §2.2), `login_urls`↔`login_sites`. **[P2][TESTO]**
**FIX**: tutti [TESTO] — togliere nomi propri/hardcoded/rubati, aggiungere i mancanti, NON reciproci fra
fratelli. La contaminazione è FUNZIONE+boundary (memoria `feedback_contamination_is_function_not_prompt`), non
liste-sinonimi: dove il confine è strutturale (get vs read persons) preferire un boundary deterministico.

### M7 · i18n incompleto  **[ADR 0092/§7.13 — debito noto]**
- **[P2][STRUTT] `get_proposals`** · prosa user-facing IT hardcoded in `summary`/`detail_md` («Cosa propongo:»,
  «Vuoi che proceda?») → istanza `METNOS_LANG=en` emette italiano.
- **[P2][STRUTT] `consult_frontier`** · `tier.description` hardcoda i nomi dei modelli frontier
  («wise (Opus 4.7) | middle (Sonnet 4.6) | …») → viola la regola tier-astratti (`feedback_llm_tiers_pure_abstract`)
  ed è già stale.
- **[P2][TESTO] `send_messages`** · le description annidate di `messages.items.properties.*` (to/subject/body/…)
  sono solo IT, senza tabella it/en.
- **[P3][TESTO] `set_signatures`** · affinity quasi solo IT (mancano set/authorize/allow/deny/blacklist…).
**FIX**: instradare i template via i18n DB (`_msg`/`messages.get`); tier astratti; tabelle it/en sugli arg
annidati. Coerente col debito lessicale a 2 locali (sanare su 3ª lingua).

### M8 · Cap, arg fantasma, incoerenze puntuali
- **[P1][STRUTT] `read_files_ocr`** · nessun cap su executor costoso (tesseract+VLM per file), mentre tutti i
  fratelli ce l'hanno → sfonda il `timeout_s` del loader senza notice §2.7. FIX: `max_files` + troncamento §2.7.
- **[P1][STRUTT] `find_files`** (CANONICO) · arg fantasma `[args.properties.kind]` con descrizione stub «t»,
  mai letto dal backend → il modello che tutti copiano contiene un arg inerte. FIX: rimuovere il blocco.
- **[P1][STRUTT] `read_files_xlsx`** · la description dice «indice come stringa» ma il codice accetta l'indice
  solo se `int` → `sheet="0"` fallisce su file valido. FIX: coercire `sheet.isdigit()→int` o correggere il testo.
- **[P1][STRUTT] `delete_images_indices`** · `_VALID_IDX_FULL=("scene","persons","gps")` ma l'indice attivo
  post-ADR0117 è `unified/` → «libera spazio» ritorna `deleted:[],freed_bytes:0` mentre l'indice resta (falso
  successo §2.8). FIX (CODICE-EXECUTOR): targettare `unified`; enum manifest da allineare.
- **[P2][STRUTT] §2.4 dominio non dichiarato**: i `paths`-array di get/read_files* e compute_files_loc
  accettano path esatti (no glob nel codice) ma non lo dichiarano; `filter_entries.where_in/where_not_in` sono
  dominio chiuso ma il codice applica `fnmatch` non documentato. FIX: dichiarare «match esatto / no glob» o
  documentare la tolleranza glob.
- **[P2][STRUTT] `change_files_format`** · capabilities `metnos:read/write` (anomale vs `fs:read/write`+hint
  dei fratelli) e nessuna validazione dello scope `dst` prima di ffmpeg → scrittura possibile fuori scope.
- **[P2][STRUTT] `read_files_ocr`** · capability VLM mancante: il fallback chiama
  `http://127.0.0.1:8081` ma le capabilities non dichiarano `network:http` → bloccato sotto sandbox stretta.
- **[P2][STRUTT] `consult_frontier`** · `fs:read $HOME/**` + `local_context.files` inviano contenuto di file
  privati a un provider ESTERNO con `critical=false` → verificare che `llm:online` gate-i il consenso outbound.
- **[P2][STRUTT] `get_file_dates`** · directory VUOTA (nessun manifest/`.py`, il loader la salta) ma
  `prompts/{it,en}/synt_code_addendum_get.yaml` e `smoke.py` la citano come executor vivo → lo stage 5 CODE
  crede esista. FIX: rimuovere la dir + aggiornare addendum/smoke.
- **[P2] `sort_entries`** · `top` dichiara `minimum=1` ma il codice tratta `top=0` come «no cap» (§2.4
  0-as-placeholder) → il manifest vieta un valore supportato. FIX: `minimum=0`.

---

## Falsi positivi e manifest SANI (anti-rumore — non toccare)

- **`get_approval`**: l'ERROR del linter è falso (`args` = property annidata di `on_approve`); il manifest è
  coerente, `_pre_approved`/`actor`/`channel` sono `runtime_resolved` (proposer-hidden + Guard#0-dropped) →
  il bypass consenso NON passa dal manifest. server-only + non-critical corretto.
- **`skills`**: NON è un executor — è la root del bundle-skill (`executors/skills/google-workspace/…`) e un
  oggetto vocab. Nessun `manifest.toml`+`.py`. Segnalare a chi ha compilato la lista, niente da revisionare.
- **`*_credentials` metadata-only ROBUSTO** (ADR 0123): find/set/delete_credentials chiamano
  `assert_no_secrets_in_return` su ogni return; `find_credentials` espone solo NOMI di campo; `set_credentials`
  stascia i cleartext out-of-band (0600). Solo un docstring stale in set_credentials.
- **Esemplari** (modelli da imitare): `write_files_spreadsheet` (multistage undo), `write_images_google_photos`
  (`revertible=false` onesto, upload non annullabile), `get_images_google_photos` (reverse `delete_created_paths`
  corretto per i download), `compress_files`, `create_dirs`, `read_messages` (disambiguazione casella-vs-oggetto
  magistrale), `get_places`, `find_packages`, `find_urls`, `read_contacts`, `read_events`, `find_events_empty`,
  `list_dirs`, `read_files_csv`, `read_files_doc`, `compute_files_loc`, `get_location`, `get_inputs`.

---

## Reperti per-executor (indice completo per chi fixa)

**Gruppo A (file lettura/enum/firme)**: find_files [M8 kind], get_files [M6 size/date], read_files [M6 NON],
read_files_xlsx [M8 sheet], read_files_ocr [M8 cap · M8 VLM-cap], read_files_spreadsheet [M6 overlap],
compute_signatures/get_signatures [M2 OUT · M1 reverse], get_file_dates [M8 dir vuota], find_dirs [M6 affinity],
path-array [M8 §2.4]. i18n del gruppo: FEDELE.

**Gruppo B (file mutanti)**: change_files_format [M1 · M8 cap], set_signatures [M1 · M2 · M7 affinity],
write_files [M1 overwrite], move_files [M1 overwrite · M2 OUT], create_files_doc [M1], create_files_spreadsheet
[M1], write_files_doc [M1 contraddizione], share_files [M6 affinity revoke], delete_dirs [M8 if_empty_only
fantasma]. Esemplari: write_files_spreadsheet, compress_files, create_dirs.

**Gruppo C (entries/runtime/gate)**: describe_numbers [M2 · M6 NON], consult_frontier [M7 tier · M3 max_tokens ·
M8 esfiltrazione], undo_last_turn [M2 · M3 _actor], get_proposals [M7 prosa IT], group_entries [M6],
get_inputs [M8 timeout default], filter_entries [M8 §2.4 glob], sort_entries [M8 top · M6 order]. Sani:
get_approval, get_location, get_now, get_processes. skills = non-executor.

**Gruppo D (mail/web/luoghi)**: send_messages [M4 · M7 nested], get_urls [M6 affinity], find_places [M8 geo
hint], find_images_web [M4 · M6], move_messages [M6 desc Gmail · M3/§2.4 client], read_urls_html
[M3 auth_cookies incoerente], set_messages [M6 affinity IMAP]. Barriera EMAIL solida (§5 onorato). Esemplari:
read_messages, get_places, find_packages, find_urls.

**Gruppo E (sites/foto/indici)**: delete_images_indices [M8 subdir sbagliate · M2 output], find_images_indices
[M3 names_op], login_sites [M3 runtime-args], create_images_indices [M2 schema], login_urls [M6 spaggiari · M6
overlap], find_persons_indices [M6 carol/bob], find_images_google_photos [M6 overlap]. Esemplari:
write_images_google_photos, get_images_google_photos, read_sites, delete_sites, find_images_indices (boundary OK).

**Gruppo F (persone/credenziali/calendario)**: delete_persons [M1 P0 · M3 dry_run], delete_events [M1 · M6
affinity], create_calendars [M1], delete_calendars [M5 _confirmed], get_persons↔read_persons [M6 NON],
find_contacts [M6 affinity], set_credentials [M5 overwrite_confirmed · docstring stale], set_persons [M3
dry_run · M6 replace distruttivo], create_events [M8 _undo key]. Sani: *_credentials metadata-only,
read_contacts, read_events, find_events_empty, delete_credentials (revertible=false onesto).

---

## Piano d'attacco consigliato

1. **M1 fix radice (CODICE)** — il più alto ritorno: `undo_last_turn` fallback a `module.reverse()` su pattern
   ignoto + `"calendars"` in `_OBJECT_REGISTRY`. Sana il P0 (`delete_persons`) e 4-5 P1/P2 insieme, prima di
   toccare un solo manifest. Poi i creatori-locali che non emettono `created`/`path` e il blob-backup su
   overwrite (write_files/move_files, copiando write_files_spreadsheet).
2. **M5 (CODICE)** — allowlist `_*` in `coerce_args` (è lo stesso fix [T1]/[SAFE-P0] dell'audit codice: farlo
   una volta chiude sia il bypass consenso sia lo spoofing `_actor`).
3. **M4** — clausola `when` su `send_messages`/`find_images_web` (autorità).
4. **M2** — allineare OUT/schema al codice sui 6 tool che mentono (falso successo/pipe vuota).
5. **M3** — dichiarare gli args letti-non-documentati (rischio drop Guard #0).
6. **M8** — arg fantasma `find_files` (canonico!), `read_files_xlsx`, cap `read_files_ocr`,
   `delete_images_indices` subdir.
7. **M6, M7** — igiene affinity/i18n (testo model-facing): togliere nomi propri/hardcoded/rubati, tier
   astratti, prosa via i18n. Da calibrare per il modello locale MEDIO.
8. Rigirare `python3 -m runtime.manifest_lint` per i 153 length/7 output_shape/4 non_refs residui, a valle dei
   fix semantici (molti si risolvono insieme).

**Regole operative**: dopo OGNI manifest toccato → `python3 runtime/sign.py sign executors/<name>` da repo root
+ commit manifest+sig insieme (§7.10); senza firma il loader lo scarta in silenzio (e oggi è muto — vedi audit
codice [SYNT-4]). Per i fix di undo/consenso: ≥1 turno reale «crea/cancella X → annulla» (§8.5) con backup dello
store, mai purge sui registri reali. Il testo model-facing è «prompt del tool» per il modello locale medio:
frasi corte, esempi, niente gergo (§2.5).
