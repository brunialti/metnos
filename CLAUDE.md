# Metnos — Linee guida di progetto

> **OBBLIGO**: leggere integralmente all'inizio di ogni sessione. Codifica decisioni architetturali, convenzioni di codice e norme di processo gia' stabilite. Quando un punto e' obsoleto o errato, AGGIORNALO subito invece di lavorarci attorno.
>
> Mantenuto da: agente. Aggiornamento ad ogni sessione che fissa una nuova norma duratura.
> Ultimo aggiornamento: 2026-05-11 (sessione 11 maggio: §6.1 nuovo — Tipizzazione del prompt. Tre stili disgiunti per i `.j2` di `runtime/prompts/<lang>/`: `prescriptive` (planner, describe/classify — §6 strict DEVI/NON DEVI/OK/ERRORE), `definitional` (synt stages 1-4, intent_extractor, vaglio — vocabolario chiuso + few-shot, no §6), `few_shot` (12 addendum verbo stage 5 — esempi codice). Frontmatter Jinja `{# --- ... --- #}` 8 campi (role/tier/lang/style/version/owner/updated/sha_prev). Linter §6 (Fase C4 futura) applichera' SOLO ai file `style: prescriptive`. + A3 daemon `i18n_translate_pending` scheduler v2: `runtime/jobs/i18n_translate_pending.py` callback `daily@02:00` cap N=20/fire, idempotenza su `source_hash`, audit JSONL append-only `~/.local/share/metnos/i18n_audit/<YYYY-MM-DD>.jsonl`, tier wise default override `METNOS_I18N_QUALITY`, migration idempotente colonne `translated_at_iso`+`translated_by`. 9/9 test PASS, scheduler v2 118 PASS.).
> Ultimo aggiornamento precedente: 2026-05-10 (sessione 10 maggio sera: §10.6.44 nuovo — ADR 0123 skill importer agentskills.io. Pipeline 5-stadi (parser + translator + codegen Jinja + LLM stage 4 description + admission 5-layer ADR 0114), CLI `metnos-skills import|list|uninstall|status|evaluate`. 16° OBJECT `credentials` per gestione token via 3 executor `find/set/delete_credentials` con capability invariante `metnos:credentials_metadata_only`. 5° reverse_pattern `delete_<object>_by_id` (catalogo §2.3). `runtime/smoke_imports.py` separato con `BATTERY_IMPORTS` concatenato da `smoke.py` (Opzione B: separation of concerns, niente polluzione BATTERY curata). Helper `runtime/time_window_parser.py` + `runtime/skill_wrapper.py` (5 helper condivisi: skill_home/subprocess_runner/run_api/classify_error/needs_inputs_oauth/check_credentials). Worktree `/tmp/skill_importer_work/` 5400 LOC + 200 test PASS. Demo end-to-end su SKILL.md hermes google-workspace: 24/24 sub-command importati (post chiusura 6 gap).).
> Ultimo aggiornamento precedente: 2026-05-10 (sessione 10 maggio: §10.6.43 nuovo — ADR 0122 proposal auto-evaluator + ETA instrumentation: `runtime/path_shape.py` (SHA-256(16 hex) sequenza chosen_tool produttiva), `runtime/proposals_eta_index.py` (sqlite p50/p95 per path_hash), task scheduler v2 `proposals_eta_aggregate` daily@04:30 (rolling 7g), `runtime/synth_request.py` enrichment (`handle_synth_request(..., current_steps=None)`), `runtime/proposal_evaluator.py` con 6 killer + 7 signal weighted score → verdict accept|gray|reject, CLI `python -m admin.proposals_cli evaluate`, HTTP `/admin/synth-proposals/{id}/evaluate`. Determinismo §7.9: niente LLM. 47 nuovi test verdi. Estende ADR 0114 con 5° gate preventivo a evaluator-time.).
> Ultimo aggiornamento precedente: 2026-05-09 (sessione 9 maggio: §10.6.42 nuovo — ADR 0117 unified image enrichment index: schema v4 (`runtime/index_schema.py::INDEX_SCHEMA_VERSION=4`, `is_unified_schema()`); UN solo `unified/` per corpus invece di 3 idx (scene/persons/gps eliminati); pipeline per-foto EXIF + ArcFace (RetinaFace+buffalo_l) + VLM Qwen2-VL-7B su :8081 (description/keywords/location/activity) + BGE-M3 text embedding; storage `~/.local/share/metnos/index/image/<sha8>/unified/` (entries.jsonl + embeddings_text.npy + embeddings_face.npy + meta.json); `find_images_indices` riscritto single-axis (drop `idx=`, args combinano AND in single pass); `find_persons_indices` thin alias compositivo; `create_images_indices` riscritto con pipeline completa; manifest descriptions stringato/affermativo/pattern; migration tool `runtime/index_schema_upgrade_v4.py` + `runtime/build_runner_unified.py`; live batch `metnos-build-unified-d789c4c0.service` v3→v4 di 30401 foto in 2s (aggregator); VLM enrichment full pass spawnato come `metnos-vlm-enrich-d789c4c0.service` (bench 4.01s/foto reali, ~34h su 30k; tutti modelli locali §10.3, zero API esterne); ADR 0086 marcato SUPERSEDED-BY 0117. Suite 985 PASS / 0 FAIL.

---

## 1. Cos'e' Metnos

Assistente personale self-hosted (su `.33`, Strix Halo 96GB unified). Microarchitettura a executor sintetizzati al volo via synt multistage; runtime ReAct con planner LLM (Gemma 4 26B middle/wise locale + Sonnet/GPT-5 frontier come fallback). Canali: **Telegram** + **HTTP porta 8770** (htmx + Jinja2 + uPlot, ADR 0078). Pipeline immagini in-process: SigLIP-base + RetinaFace+ArcFace + EXIF (ADR 0086). Lingua principale: italiano; corpus doc bilingue IT+EN. Etimologia: `mētis + noûs`. Process name: `myclaw`. Dominio: `metnos.com`.

ADR registry canonico: `/opt/myclaw/decisions/` (`0001-0123`, `0055`/`0115`/`0116`/`0121` skipped — fonte unica per "perche' abbiamo scelto cosi'").

## 2. Principi cardine (mai negoziabili)

### 2.1 Executor vettoriali per costruzione
Input come **lista** (paths, entries, urls, ids). Output **sempre lista**, anche degenere (N=0/1/molti). Niente `*_batch`: la versione batch *e'* l'executor. Iterazione interna (loop, paginazione, window come `time_window: today|last-Nd`); branching su osservazione torna al planner. Cap inferiore = 0; cap superiore = parametro esplicito (`max_total`, `max_results`, `max_bytes`).

### 2.2 Naming convention compositiva
Struttura: `azione_oggetto[_qualifier]`. Vocabolario CHIUSO (escalation a Roberto per nuovi termini), centralizzato in `runtime/vocab.py`.

- **22 azioni**: `read, write, move, delete, create, find, list, filter, sort, group, classify, get, set, send, describe, render, extract, compress, compute, compare, change, order`.
- **Ortogonalita' dei 5 verbi-produttori** (asse «com'e' fatto l'input primario»): `find` = pattern/query (discovery, sussume verifica esistenza); `get` = id noti o nessun arg (lookup/snapshot); `read` = id di sorgente, ritorna contenuto; `list` = container, enumera senza contenuto; `filter` = lista preesistente, riduce per predicato. Discrimine find vs get: pattern → `find`; id/stato → `get`. `change` = forma/parametri (resize/convert/rotate); `order` = ordinamento PERSISTENTE del corpus, distinto da `sort` in memoria del turno.
- **Confini stretti**: `fetch` rimosso (HTTP GET = `get_urls`); `extract` solo decompressione archivi (zip/tar/gz); «estrai righe da testo» = `filter_texts_lines`; «estrai testo da PDF/HTML» = `read_files_pdf/html`; «estrai campi da entries» = `get`.
- **16 oggetti** (plurale): `files, dirs, packages, messages, events, contacts, places, processes, urls, numbers, images, signatures, texts, proposals, inputs, credentials`. Eccezioni: `get_inputs` ritorna `{values:{var:value}}` (UI dichiarativa, ADR 0090); `find_credentials`/`set_credentials`/`delete_credentials` espongono SOLO metadata (binding, fingerprint, scopes, age, status) — i valori cleartext non tornano mai al PLANNER (10/5/2026).
- **Qualifier opzionali, 3 famiglie**:
  - **formato/codifica**: `_csv, _xlsx, _ocr, _zip, _pdf, _xml, _html, _json, _text, _gz, _tar, _video, _audio, _image, _hash`.
  - **modalita'**: operazione (`_size, _format, _similar, _loc`); granularita' (`_lines, _paragraphs, _sentences, _pages, _segments`); mezzo astratto persistente (`_indices` per CLIP/ArcFace/EXIF/perceptual hash/threading — ECCEZIONE: `find_<dom>_indices` ritorna entries del dominio, non un oggetto `indices`).
  - **safety policy** (oggetto `signatures`, ADR 0071): `_blacklist, _whitelist, _graylist, _forbidden, _seed, _diff, _sanity, _command, _reversibility, _promotion, _candidates`.

Stage 1 di synt usa MAPPING bilingue IT+EN. Sinonimi prima dell'estensione del vocabolario.

### 2.3 Reverse pattern catalogo deterministico
Manifest dichiara `reverse_pattern` da catalogo chiuso: `swap_src_dst` (move/rename), `delete_created_dirs`, `delete_created_paths`, `restore_blob_backup` (richiede `blob_path`+`blob_sha256` nei results). Blob: `$METNOS_HISTORY_DIR/<METNOS_TURN_ID>/blob/<sha256>.bin`.

### 2.4 Robustezza al confine NL→determinismo
Executor accetta: `0-as-placeholder` (cap=0 → no limit), compound case-insensitive di default, args plurali ammessi (`paths` accetta anche 1 elemento). Helper comuni in `runtime/executor_helpers.py`. Niente patch reattive per-executor.

### 2.5 Manifest leggibili da LLM medium
Manifest TOML = "prompt del tool" per il pianificatore (Gemma 4 26B think=true, NON Sonnet/Opus). Modello canonico: `executors/find_files/manifest.toml`. Criteri:
- Description 2-5 frasi corte (max 25 parole/frase), una frase per uso.
- Forma prescrittiva (§6) sui punti facili da confondere (DEVI/NON DEVI/OK/ERRORE).
- Esempi tra virgolette per formati non ovvi (`time_window="last-24h"`).
- Default in chiaro nella description, non solo nello schema.
- Niente gergo Python interno (BFS, async, generator).
- Affinity: 8-15 termini IT+EN user-facing, niente jargon interno.
- Args: 1 frase per arg, tipo + esempio + default.
- Boundary del verbo (§2.2): "USO CORRETTO" + "NON CONFONDERE CON".
- Output structure: dichiarare campi del dict di ritorno per pipeable next-step.
- Anti-pattern: NO description di 800 parole, NO esempi pattern-by-example senza separatore "non copiare letteralmente".
- **Multilingua (ADR 0092)**: `[description]` tabella per lingua (`it`, `en`, ...); companion `manifest.lang_state.json` traccia hash. Doc canonico: `docs/it/architecture/multilang.html`.

### 2.6 Output naming consistency
Executor che **arricchiscono/leggono** una lista → ritornano `entries`. Executor **trasformativi** (move/write/delete) → ritornano `results` (schema cambia).

### 2.7 Truncation visibility cross-executor
Cap raggiunto = `truncated: true` + `truncated_what` + `used: int` + `available_total: int` quando noto. Runtime prepende notice. **Visibility vs notify** (ADR 0062): `truncated_intentional: true` segnala cap user-richiesto (es. `top=K`) — runtime mostra "1-3 di 27" ma NON propone allargamento.

### 2.8 No silent failure
Mai dichiarare un esito che non corrisponde alla realta'. `ok_count` = elementi REALMENTE processati. Undo onesto.

### 2.9 Move never implicit delete
Per ogni move: destinazione specificata, verifica esistenza, crea-se-manca, COPY-check-then-DELETE. Mai DELETE senza COPY confermata.

### 2.10 Coerenza I/O fra executor in pipeline
Se il consumatore si aspetta `entries`, il produttore ritorna `entries`. Cambio nome solo quando lo schema dei record cambia.

### 2.11 Cap/budget exhaustion: notify then ask
Cap raggiunto (entries, max_total, quota, observation, context) → mai silenzio o parziale presentato come completo. Due fasi: (1) **notify** con campi §2.7; (2) **ask** se l'allargamento e' tecnicamente possibile, attendere conferma esplicita. Mai allargamento implicito. Convenzione: oltre a `truncated:true/truncated_what/used/available_total`, esporre `cap_field: "<arg>"` + `cap_value: int` per ricostruire la chiamata. `available_total` via sondaggio post-cap quando possibile.

## 3. Synth pipeline (5 stadi)

| Stage | Tipo | Tier | Output |
|-------|------|------|--------|
| 1 NAMING | procedurale | middle | `name`, `revertible`, `critical`, `target_kind` |
| 2 SIGNATURE | procedurale | middle | `args_schema`, `capabilities`, `reverse_pattern` |
| 3 TESTS | procedurale | middle | 4-6 test (caso felice, lista vuota, args invalidi, edge dominio) |
| 4 DESCRIPTION | creativo | middle | description (2-5 frasi) + 6-10 affinity keywords |
| 5 CODE | creativo+procedurale | wise | `<name>.py` con `def invoke()` |

Vincoli: vocabolario chiuso SOLO in stage 1; ogni stage vede la fetta minima di contesto; quality floor (no degradare a fast); sintesi locale only (no provider esterni). Validato 4/4 vs 32% single-prompt.

## 4. Planner ReAct

### 4.1 Da-piping fra step
Liste fra step → SEMPRE `from_step: N` (int): runtime espande dallo scratchpad → `entries`. Valori singoli (content, dst_template) → placeholder `{{stepN.field}}`. NIENTE `entries: "{{step1.entries}}"`.

### 4.2 Caso degenere N=1 con literal
Letterali (path, url) come typed list arg inline. NON usare `from_step=0/1` con history vuota. OK: `delete_files(paths=["/tmp/x.txt"])`. ERRORE: `delete_files(from_step=1)`.

### 4.3 Action verbs portano a termine
Verbo d'azione esplicito (`sposta, cancella, invia, scrivi, crea, scarica, comprimi, estrai`) → pattern: read/find/get → classify/filter (se serve) → VERBO_AZIONE → final_answer. Niente `describe_entries` PRIMA del verbo di azione.

### 4.4 Cap_steps + cap_same_executor
12 step max per turno. Stesso executor 3× di seguito = `cap_same` → loop_break. `DUPLICATE_CALL` → `final_answer`.

### 4.5 Undo
Se `undo_last_turn` ritorna `ok:true` con `undone_count >= 1`, step successivo DEVE essere `final_answer`. Mai due undo nello stesso turno.

## 5. Vincoli di dominio (hardcoded nel PLANNER prompt)

- **EMAIL/MAIL/IMAP** → `read_messages`/`send_messages`/`move_messages`/`delete_messages`. Mai `move_files` su mail.
- **FOTO/EXIF/GPS** → `get_files_metadata`.
- **POSIZIONE/DOVE-SONO** → `get_location`.
- **TEMPO/DATA-CORRENTE** → `get_now`.
- **DESTINAZIONE spam/cestino/archivio** → nome utente come `dst_folder` ("Posta indesiderata", "Junk"); l'executor risolve via `M.list`. Non hardcodare `INBOX.Junk`.

## 6. Stile prompt (prescrittivo)

Ogni regola che istruisce un comportamento al LLM (PLANNER, vaglio, synt stage, describe/classify) DEVE seguire 4 punti, in quest'ordine:

```
DEVI: <verbo imperativo + azione>.
NON DEVI: <verbo imperativo + azione vietata>.
OK: <esempio positivo, una riga>.
ERRORE: <esempio negativo, una riga>.
```

Max 4-5 righe per regola. `DEVI`/`NON DEVI` maiuscolo. `E' UN ERRORE` come marker. Niente `se possibile`/`preferibilmente`. Niente prosa "perche'". Esempi pattern-by-example richiedono separatore esplicito "non copiare letteralmente".

### 6.1 Tipizzazione del prompt

Tre stili disgiunti per i prompt `.j2` in `runtime/prompts/<lang>/`:
- `prescriptive`: planner, describe_entries_*, classify_entries — applica §6 rigoroso (DEVI/NON DEVI/OK/ERRORE per ogni regola comportamentale).
- `definitional`: synt_naming/signature/tests/description, intent_extractor, vaglio — vocabolario chiuso + few-shot di output strutturato; niente §6, niente prosa imperativa.
- `few_shot`: 12 addendum verbo di synt stage 5 — esempi codice per il LLM; nessuna prosa prescrittiva.

Ogni `.j2` dichiara la tipizzazione in un frontmatter Jinja `{# --- ... --- #}` con 8 campi: `role`, `tier`, `lang`, `style`, `version`, `owner`, `updated`, `sha_prev`. Esempio:

```jinja
{# --- role: planner / tier: middle / lang: it / style: prescriptive / version: 1 / owner: metnos / updated: 2026-05-11 / sha_prev: - --- #}
```

Il linter §6 (Fase C4, futura) applichera' DEVI/NON DEVI/OK/ERRORE SOLO ai file con `style: prescriptive`; gli altri due stili sono esentati per costruzione.

## 7. Coding standards

### 7.1 Niente backward compatibility in dev (pre-1.0)
Rompi pure API/firme/default quando il nuovo design e' migliore. Niente shim/legacy/parametri solo-per-compat.

### 7.2 Semplicita' prima di tutto
A parita' di risultato: scegli sempre la soluzione piu' semplice, leggibile, lineare, modulare.

### 7.3 Soluzioni generali, mai hardcoded
Daily test = scoperta di soluzioni, non check di non-errori. Detection sintomatico come safety net, ma il fix deve generalizzare la causa.

### 7.4 Niente parallelismo se non c'e' speedup reale
Subagent paralleli/batch solo se riducono wall-time. Bottleneck GPU/IMAP/IO seriale → default seriale.

### 7.5 Niente nomi propri di terzi
Solo "Roberto" o generici (guest, ospite, familiare invitato).

### 7.6 Niente lettere greche nelle opzioni
`(a)/(b)/(c)` o numeri, mai `α/β/γ`.

### 7.7 Niente `_batch` come suffisso
Vedi 2.1.

### 7.8 Italiano senza anglicismi
Caccia ad anglicismi (peer, trigger, goal, plumbing, gate) e calchi (costosa/mordere/ci reagisce).

### 7.9 Codice deterministico > LLM
**Codice deterministico > LLM se equipotente, equiefficace o se codice deterministico [sarebbe] troppo complesso.** LLM solo quando deterministico e' inefficace, troppo complesso da scrivere/mantenere, o impossibile. Anti-pattern: LLM per validare/classificare cose che `vocab.py` o un regex coprono. LLM giustificato: intent extractor (parser linguistico equipotente troppo complesso).

### 7.10 Re-sign executor dopo edit del codice
Ogni edit del file `<executor>.py` cambia il digest sha256 del codice ma NON il `manifest.toml` (e quindi non la firma). Al boot/reload, `runtime/loader.py` invoca `verify_executor` (`runtime/sign.py`): se `declared digest != actual digest` l'executor viene **scartato dal catalog** silenziosamente (`rejected[]`). Il prefilter non lo include, il PLANNER non lo vede, e — se la regola di routing che lo nomina e' nel prompt come `(VINCOLO DI DOMINIO)` — il PLANNER pickera' un altro tool dal pool sapendo la regola corretta (osservato 7/5/2026: `get_processes` scartato → `get_images_indices` pickato per "stato sistema"). Workflow OBBLIGATORIO dopo ogni edit di un `.py` di executor: `python /opt/myclaw/runtime/sign.py sign /opt/myclaw/executors/<name>` + restart `metnos-http.service`. Per audit completo: `verify_executor()` in loop su `executors/` + `~/.local/share/metnos/executors/`.

## 8. Test e convergenza

### 8.1 Protocollo iterare-test-cluster
Per ogni modifica modulo: aggiornare DB test → eseguire module + cluster → iterare finche' verde. Test rotti = iterare, mai disabilitare.

### 8.2 Test fallisce → fix codice, NON il test
Modifica del test SOLO se il test stesso e' sbagliato. Distinzione: bug codice / prompt / matcher infra / test case.

### 8.3 No manual rewrite executor → itera prompt synt
Bug in executor sintetizzato = fix nel prompt synt, non nel file output. Convergenza = Metnos genera codice corretto out-of-the-box.

### 8.4 Stratificazione test synt
Smoke 6q/15min — dopo ogni fix prompt. Sanity 10q/25min — dopo refactor stage. Full 35q/80min — SOLO per cambi strutturali.

### 8.5 Convergence loop su input utente reali
Test → fail → fix codice → riprova → 0 errori. NON modificare la query utente per farla passare.

### 8.6 No daemon restart during turn
Non riavviare telegram-daemon durante un turno utente attivo (cambia PID, perde state).

## 9. Documentazione

### 9.1 Architettura sempre allineata al codice
Ogni chiusura fase / promozione doc / aggiunta capacita' / cambio numeri → update immediato di `docs/it/architecture/*.html` + `docs/en/architecture/*.html` + `decisions/<ADR>.md`. Niente backlog "doc da aggiornare poi".

### 9.2 Deploy obbligatorio dopo modifiche doc
Sempre `./deploy.sh` su Cloudflare dopo ogni batch, senza chiedere. Aggiornare la sitemap se cambia il set canonico.

### 9.3 Bilingual MAPPING
Vocabolario synt + verb prompts: sinonimi e confine semantico in IT+EN per ogni verbo.

### 9.4 Niente nomi propri
Vedi 7.5.

## 10. Workflow operativo

### 10.1 Mailbox via script
- `mykleos@knowcastle.com` (register.it) → `/opt/myclaw/scripts/check-mail.sh`.
- `metnos@metnos.com` (Migadu) → curl IMAPS inline con env in `~/.config/metnos/mail.env`. Spam = `Junk`.
- Mai `curl` ad-hoc con password inline.

### 10.2 Manifest authoring
Scrivo i manifest TOML di iniziativa; Roberto rivede. Per nuove decisioni di design, fermarsi e chiedere.

### 10.3 Open-source self-hosted come default
SearXNG, Nominatim, Tesseract, Piper, whisper.cpp, sqlite. SaaS solo come fallback (LLM frontier, mail outbound).

### 10.4 Self-contained client binary
UN file scaricabile e eseguibile. Niente mingw / VC++ Redist / lib sul target. Static-link libgcc/libstdc++/winpthread.

### 10.5 Memorie persistenti
Tipi: `user`, `feedback`, `project`, `reference`. Indice in `~/.claude/projects/-opt-myclaw/memory/MEMORY.md` (max ~150 char/riga, max ~200 righe). Dettaglio in file separati.

### 10.6 Meccanismi anti-regressione (indice)

> Una riga per meccanismo. Dettagli: ADR registry `/opt/myclaw/decisions/`. Solo le voci da memorizzare al call-site (file/funzione/policy) restano qui.

- **Smoke battery** (`runtime/smoke.py`): 8 query "must work" + invariants. OBBLIGATORIA prima di `./deploy.sh`, dopo synth-on-the-fly, in cron daily, quando si tocca `prefilter.py`/`agent_runtime.py`/`synt_multistage.py`/`loader.py`.
- **Catalog invariants al load** (`runtime/loader.py`): rifiuta synth in `~/.local/share/metnos/executors/` con name collision verso handcrafted in `/opt/myclaw/executors/`.
- **Prefilter precursor universale**: `rank_with_intent` inietta automaticamente UN precursor per ogni verbo NON producer (`read, find, list, get`).
- **No synth ridondanti**: stage 1 NAMING preferisce il name canonico esistente quando l'intent e' coperto.
- **Prefilter primary tools per object** (ADR 0075): `_OBJECT_PRIMARY_TOOLS` in `prefilter.py` inietta nel top-K il tool canonico per ogni object (`processes→get_processes`, `messages→read_messages`, ...). Mantenere allineato ai 16 OBJECTS di `vocab.py`.
- **Adaptive re-rank intra-turno** (ADR 0072): `runtime/adaptive_rerank.py` add-only, cap `2×k_max`.
- **Scheduler gate user-activity** (ADR 0074): task notturni age-based si sospendono se ultima interazione utente precede ultima esecuzione del task. Sorgente: `~/.local/share/metnos/turns/*.jsonl`.
- **Synth_request short-circuit** (ADR 0076): `handle_synth_request` esegue 2 short-circuit deterministici prima della cascata: `already_in_catalog` + redirect ad alias canonico stesso-object.
- **Introvertiva quality filters** (ADR 0077): `candidates_specialize` 6 filtri deterministici (validator naming, skip flow args/template/booleani, soglia `uses_min` default 10).
- **GC synth rifiutati** (ADR 0079): `runtime/loader.py::_gc_collisions` sposta i synth `rejected` in `/tmp/metnos_synth_gc_<ts>/<name>/`. Solo path dentro `SYNTHESIZED_EXECUTORS_DIR`.
- **PROJECT PATHS noti nel PLANNER** (ADR 0079): `runtime/project_paths.json` mappa progetti → `code_root`/`user_data_root`/`memory_root`. Espansione = entry JSON, niente codice.
- **Pipeline web/news/scuola** (ADR 0081+0082+0098+0101+0105+0108): `find_urls`/`read_urls_html`/`read_urls_pdf`/`login_session` + helper `group_entries`. Tier resolution `~/.config/metnos/{owned_domains,trusted_origins}.json`. UA `metnos-crawler/1.x`. Discovery sitemap > RSS > BFS. Topic ranking BM25 (no LLM). Credenziali Fernet+HKDF (`runtime/credentials.py`). Default tier T2 (downgrade T1 solo per `blocked_origins.json`). HTTP cache disk-based 900s. Auto-degrade T2→T1 su 429/503 ripetute (host_health, TTL 24h). `read_urls_html` cattura `iframe_urls`/`linked_documents` + rileva JS-rendering (`js_rendered=true`). PLANNER hint Z/Z.ter/Z.quater per URL esplicito + soft-fail con `error_class`.
- **Indici di dominio** (ADR 0086): pattern `create_<dom>_indices`/`find_<dom>_indices`/`get`/`delete`. Backend in-process: SigLIP + RetinaFace+ArcFace + EXIF (`runtime/clip_embedding.py`, `runtime/face_embedding.py`). Storage `~/.local/share/metnos/index/<dom>/<sha8>/<idx>/`. Refresh incrementale di default; `force=true` per rebuild.
- **CIFS/SMB mount via admin → sudoer** (ADR 0087): canonicalize `mount`/`umount` (`runtime/safety/canonicalize.py`), seed v2 graylist, helper `runtime/cifs_helper.py` con temp file 0600 cifrato. Glue `verb_unique/sudoer.py`: placeholder `${METNOS_CIFS_CREDS}`. Niente password in argv.
- **Admin esposto al PLANNER** (ADR 0088): `admin` con `EXPOSE_TO_PLANNER=True`, vaglio always-on. Sudoer invisibile. Prefilter inietta admin score 15 su `_SHELL_INTENT_HINTS`. HMAC consent token TTL 600s in `~/.local/share/metnos/.admin_consent_key`.
- **Credenziali UX 3 strati** (ADR 0089+0091): Strato 1 `extract_credentials` regex IT+EN inline + redact; Strato 2 admin `decision="needs_inputs"` + payload `get_inputs` con `on_complete=save_credentials_and_resume` (auto-orchestra `runtime/orchestration.py::orchestrate_needs_inputs`); Strato 3 CLI `metnos-cli credentials add|list|remove|fingerprint`. Aggiungere binding = entry in `_BINDING_STRONG/_WEAK` + helper `<kind>_helper.py`.
- **Engine UI dichiarativo** (ADR 0090): `get_inputs(title, dialog=[{var,prompt,schema:{kind,...}}], fmt=auto|dialogue|form|voice|telegram_inline)`. 9 kind. Output `{values:{var:value}}`. Storage `runtime/dialog_pending.py`. Adapters Telegram + HTTP `/agent/dialog/<id>/{form,submit,cancel}`. **Soglia form HTTP** abbassata da 3→2 step (Roberto, 7/5/2026 notte): credentials user+pwd ora aprono form HTML con `type="password"` invece di sequenza dialogue. **Telegram inline keyboard** (8/5/2026 notte): `_decide_fmt(channel="telegram", auto)` ritorna `telegram_inline` se TUTTI gli step sono in `_INLINE_COMPATIBLE_KINDS = {yes_no, choice}` (multi_choice deferred). Daemon `runtime/channels/daemon.py::_build_dialog_keyboard` costruisce `OutboundMessage(buttons=...)` con callback_data `dlg:<dialog_id>:<step_idx>:<value>` (yes_no: yes/no; choice: c<idx>) o `dlg:<id>:cancel`. `_handle_dialog_callback` parsa, decodifica via lookup choices, riusa `dialog_pending.consume_pending_step` (single source of truth con dialogue path) + `_on_get_inputs_completed` per `on_complete` callback. Telegram limita callback_data a 64 byte: usiamo indice c0,c1,... invece del label completo. i18n: `MSG_BTN_YES/NO/CANCEL`, `MSG_DIALOG_EXPIRED/CANCELLED`. Test 16/16 (`runtime/tests/test_get_inputs_telegram_inline.py`).
- **Prompt-as-data + multilingua** (ADR 0092): `runtime/prompts/<lang>/<role>.j2` (MiniJinja). `prompt_loader.get(role, lang, **vars)` con `lang` esplicito al call-site. `keep_trailing_newline=True`. Invariante "ogni sub-dir lingua secondaria stesso set di `it/`" enforced al boot da `validate_invariant()`. CLI `metnos-prompts`. **Tre layer disgiunti**: prompt LLM + manifest description `[description].<lang>` + companion JSON + messaggi user-facing `~/.local/share/metnos/i18n.sqlite` (118 chiavi). Latest-wins. **Norma report user-facing**: TUTTI i report runtime user-facing usano `config.DEFAULT_LANG` via `i18n.sqlite` o tabella inline (NON ADR, NON commenti codice, NON test docstring).
- **Async indexing build** (ADR 0093): systemd user transient unit `metnos-build-<sha8>-<idx>`. Atomic write tmp+rename. Resume da checkpoint ogni N=500 + SIGTERM handler. Notification via marker `/tmp/metnos_build_complete/`. 3 async tasks in `runtime/http_async_tasks.py`. Threshold sync vs async: stima >120s → orchestrator. Disable test `METNOS_HTTP_DISABLE_BUILD_TASKS=1`.
- **Fast path deterministico** (ADR 0094): `runtime/fast_path.py` short-circuit query triviali 1:1 PRIMA del PLANNER. Tabella chiusa `_FAST_PATTERNS` lookup O(1). ZERO LLM. Step `fast_path=True`. Coverage iniziale `get_now`. Skip se `reference_images`. Estendere SOLO con mapping 1:1.
- **Output formatter deterministico** (ADR 0095): `runtime/output_format.py` channel-agnostic markdown. KV singoli `**label**: value`; gruppi correlati come bullets o KV multipli (NO slash ambigui); lista→tabella quando record omogenei a ≥3 attributi; cap-expand in blocco proprio separato da HR. NIENTE LLM nel formatter.
- **Proposals cleanup** (ADR 0096): `runtime/proposals_cleanup.py` orchestra 4 op deterministiche (archive_aged_synth_proposals 30gg + dedupe_introvertiva_candidates schema-aware + keep_latest_n_per_kind n=3 + auto_decay_legacy_orphan_mnests). NIENTE delete (move + UPDATE). Task scheduler `proposals_cleanup` daily@06:00.
- **Lifecycle summary** (ADR 0097): `runtime/lifecycle_summary.py` aggregatore READ-ONLY dei 4 ager (`apply_ager`/`apply_executor_ager`/`introvertiva_*`/`proposals_cleanup`). Task `lifecycle_summary` daily@06:30. ZERO accoppiamento dei moduli ager.
- **Web crawl parallel + strategy** (ADR 0098): `_HostThrottle` thread-safe (Semaphore per-host + rate-limit lock). BFS via `ThreadPoolExecutor` cap globale `min(64, cpu*4)`, per-host T1=2/T2=8/T3=16. PLANNER hint (Z) "URL esplicito + dati DENTRO la pagina → read_urls_html primo step". Auto_final prefer read over discovery quando read in history ha contenuto sostanziale.
- **Runtime perf** (ADR 0099): seed-step URL injection (`runtime/fast_path.py::try_seed_step`) + catalog cache (`runtime/loader.py::_CATALOG_CACHE`, hit ~1ms vs cold ~80ms; signature mtime manifest+py+sig+DB; `invalidate_catalog_cache()` per i test) + reasoning budget dinamico PLANNER (step 1 = 768, step 2+ = 256, solo `provider.name=="llamacpp"`).
- **Executor perf parallelizzazione** (ADR 0100): `read_urls_html`/`read_urls_pdf`/`compute_files_loc` con `_HostThrottle` per-host + `ThreadPoolExecutor` globale. Throttle rilasciato post-fetch. Output riordinato per indice originale. Fast-path sync N=1.
- **Crawler error_class + soft-fail** (ADR 0101): `read_urls_html._fetch_one` ritorna `(None, {"error","error_class"})`. Mappatura: 403→forbidden, 429→rate_limited, 404→not_found, 5xx→server_error, timeout/network/non_html/js_rendered/unknown. PLANNER (Z.ter/Z.quater): URL fallito in history → final_answer onesto + alternative; partial fail → usa entries successful + dichiara falliti per classe. Accept-Encoding gzip/deflate/identity + decompressione.
- **Thinking-leak scrubber** (ADR 0102): `_scrub_thinking_leak` in `runtime/agent_runtime.py` accanto a `_scrub_credentials`. Regex line-by-line trigger EN (Wait/Actually/Let me/...) + IT chirurgico (paren entire-line + meta-permission). Preserva substring legittime, «Riassumendo:», «Ti suggerisco» con contenuto reale. Idempotente. Invocato in `TurnLog.write()` ramo `final_kind=="answer"` PRIMA di tutti i prepend e PRIMA di `_elapsed`.
- **Executor perf round 2** (ADR 0103): estratto `_HostThrottle` in `runtime/host_throttle.py` (regola del 3 §7.2): 3 caller riusano `HostThrottle(per_host_limit, rate_limit_ms=0)`. Audit perf §7.4 negativo su find_dirs/get_files_metadata/move_files (overhead pool > guadagno I/O su SSD warm cache). Pattern §7.4: prima di parallelizzare, misurare; se non c'e' win, NON applicare.
- **Report runtime user-facing i18n** (ADR 0104): 55 chiavi `MSG_*` in `i18n.sqlite` IT+EN. `agent_runtime` (auto-final/footer-elapsed/undo/hallucination), `lifecycle_summary`, `output_format::format_tldr`, `orchestration` (health block + cap-expand resume + entries/documents block), `find_images_indices` (final_message_hint) via `from messages import get as msg`. Determinismo §7.9.
- **HTTP cache disk-based** (ADR 0105): `runtime/http_cache.py` storage `~/.cache/metnos/http/<sha[:2]>/<sha>.json` sharded. TTL default 900s, env `METNOS_HTTP_CACHE_TTL_S` o `cache_ttl_s` per-call (0 disabilita). Atomic write tmp+rename. Cleanup weekly 7d.
- **Vaglio safe-verb shortcut** (ADR 0107): `vocab.SAFE_VERBS` 11 verbi read-only/pure-compute. In `vaglio.judge` dopo guardia: action in SAFE_VERBS → `Verdict(judge_kind="safe-verb-shortcut", score=1.0)`. Risparmio ~3-5s/step su safe verbs con `JUDGE_KIND=llm-v1`. Guardia esegue PRIMA: `read_files` su `~/.ssh/id_rsa` resta bloccato.
- **Auto-degrade T2→T1 host_health** (ADR 0108): `runtime/host_health.py` tracker per-host 429/503 sliding-window 60min. Soglia 3 → host a `~/.config/metnos/blocked_origins.json` con TTL 24h. `is_blocked()` cleanup lazy → ritorno T2 automatico. Storage volatile `~/.local/share/metnos/host_health.json`.
- **Channel-aware HTML rendering** (ADR 0109+0110): `runtime/html_sanitizer.py::to_safe_html` (subset Telegram: b/i/code/pre/a) + `to_safe_html_full` (HTTP browser: + h1..h6/ul/ol/li/blockquote/hr/table/thead/tbody/tr/th/td/p/br). Mapping markdown: heading/hr/blockquote/list/table/code block/plain. Sicurezza: HTML escape iniziale, whitelist chiusa, no tag attivi. `http_routes_agent::_safe_final_html` dispatcha su `to_safe_html_full` (fallback `html.escape(md)` su eccezione). Telegram `chunk_html` preserva attributi (`<a href>`) su close+reopen via `_TAG_FULL_RE`; default 4096→4000; `time.sleep(0.05)` fra chunk. **GFM soft line break** (7/5/2026 notte): `_flush_para` joina le righe consecutive di un paragrafo con `<br>` invece di space; un singolo `\n` interno al testo diventa line break visibile in HTML (aderisce ad aspettativa user/Telegram-style). Implica che produttori di markdown verso `to_safe_html_full` POSSONO usare `\n` semplice tra righe correlate (un solo `<p>` con `<br>` interni, niente margin browser intermedio); usare `\n\n` SOLO per paragrafi semanticamente distinti. `<br>` letterale e' iniettato DOPO l'escape iniziale e `_apply_inline` non lo tocca. Template chat HTML aggiunge CSS `.msg p,h1..h6,ul,ol,table,blockquote,pre,hr { margin: .2em 0 }` + reset `:first-child`/`:last-child` per blocchi adiacenti compatti.
- **Sync `pairings.db` → `users.user_channels` al boot** (8/5/2026 notte): due storage paralleli storicamente disgiunti. `pairings.db` (Cap. 12 architettura) bootstrappa Telegram `default_chat_id` + `/pair PAIR.<token>`, source of truth per `autonomy_level` per `(channel, sender_id)`. `users.db/user_channels` (ADR 0083 multi-user) lega identita' a canali via `/start <token>`. Senza sync, `/admin/users/<id>` mostra `channels=[]` per host bootstrappato. Helper `runtime/users_pairings_sync.py::sync_pairings_to_user_channels()` deterministico (§7.9): iter su `pairings.db` con `revoked_at IS NULL`, prova match esistente (touch `verified_at`), poi (a) `actor` del pair = `name` di uno user → bind, (b) `paired_by="bootstrap"` + un solo host → bind al host (single-host policy). Skip canali non in `users.CHANNELS` (es. `fake/42` test). Hook al boot di `metnos_http_server.make_app` e `ChannelDaemon.__init__`. Idempotente (re-run touch invece di re-link). Estensione naturale UI: `users.email TEXT NULL` colonna (migration idempotente via `PRAGMA table_info`), `update_user(*, display_name, email, autonomy_level, notes)` con sentinel `...` per "non toccare", endpoint `POST /admin/users/<id>/update`, template `user_detail.html` form a griglia 140px label + 1fr input.

- **Descrizione human-readable proposte introvertiva** (8/5/2026 notte): `runtime/http_routes_admin.py::_describe_proposal(kind, sig_key)` deterministico (§7.9). Parsa il JSON `sig_key` (shape stabile per ADR 0077: `["dedupe", reason, a, b]` / `["generalize", [exec1, exec2, ...]]` / `["specialize", exec, arg, val_json]`), formatta via 6 chiavi i18n template (`MSG_PROP_DEDUPE_LEGACY`, `MSG_PROP_DEDUPE_GENERIC`, `MSG_PROP_GENERALIZE_SEQ`, `MSG_PROP_GENERALIZE_NOISE`, `MSG_PROP_SPECIALIZE`, `MSG_PROP_UNKNOWN`). Render al volo nel handler `/admin/proposals`, mai persisted (i template sono dati-shape della UI, non per-istanza). Modifica una traduzione = tutte le proposte di quel kind si aggiornano al prossimo render. Cancellazione proposta = niente da pulire nel DB linguistico. Template `proposals.html` mostra la frase + `<details><summary>signature</summary>` collassato col raw `sig_key` per ispezione.

- **Synth admission policy 4 layers** (ADR 0114, 8/5/2026 notte): quattro gate cumulativi contro synth difettosi, motivati dal bug live `find_texts` 8/5 (synth con affinity catch-all + description disallineata dal code che ha hijackato il routing PLANNER per query web). **Layer 2 — Affinity overlap guard**: `runtime/loader.py::_check_affinity_overlap(catalog)` invocato dopo load completo. Pairwise Jaccard su affinity: synth (manifest_path dentro `SYNTHESIZED_EXECUTORS_DIR`) con jaccard >=0.5 verso UN handcrafted (in `HANDCRAFTED_FAMILIES` lista canonica) o un altro synth piu' vecchio (mtime manifest) → rejected. Audit JSONL `~/.local/share/metnos/synth_audit/affinity_rejected.jsonl`. Soglia 0.5 calibrata su `find_texts` (5/9 termini overlap = 0.555). **Layer 3 — Efficacy ager**: `runtime/executor_aging.py::apply_efficacy_ager(...)`. Sorgente: turn JSONL `~/.local/share/metnos/turns/*.jsonl` filtrato per `chosen_tool`. Per ogni synth con invocations >=100: success_rate <0.20 → deprecated; success_rate <0.05 dopo altre 30 invocations post-demotion → archived. Idempotente. Handcrafted MAI demoted (skipped via `_is_synth(source)`); PROTECTED_NAMES sempre skipped. Configurabile via `METNOS_EFFICACY_*` env. Da wirare in scheduler v2 daily@04:30 (manuale, fuori scope PR). **Layer 5 — Smoke battery con expected_first_tool**: `runtime/smoke.py::BATTERY[]` con `expected_first_tool` + `expected_arg_keys` + `min_pass_rate`. Helper `_run_smoke_with_tool_assertion(case)` simula PLANNER usando intent_extractor BoW deterministico (`_bow_intent_for_smoke`) + `prefilter.rank_with_intent` + fallback `prefilter.rank` plain (NO LLM live). Asserisce `ranked[0].name == case["expected_first_tool"]`. 12 case totali con anti-regressione find_texts (`cerca su web ...` → find_urls, NO synth catch-all), health (`stato del sistema` → get_processes), persons (`trova foto di Matteo` → find_persons_indices). `run_smoke_routing_battery()` per cron. Skip gracefully se prefilter offline. **Layer 6 — LLM semantic verifier (stage 6 synt)**: `runtime/synt_stage6_verify.py::verify_semantic_alignment(description, code_body, ...)` (NB: nome flat, no sub-package, perche' `runtime/synt.py` esiste gia'). Usa LLM tier wise (Gemma 4 26B locale) con prompt strict JSON: `{"aligned": bool, "mismatch": "..."}`. Multi-model consensus optional via env `LLM_VERIFY_MODELS=m1,m2,m3` (majority wins; tie = fail-safe). Audit JSONL `~/.local/share/metnos/synth_audit/verify_<ts>_<hash>.jsonl`. Wired in `synt_multistage::run_full` post-stage 5 e pre-sign: misalignment → `final_state="rejected_semantic_drift"`. Determinismo §7.9: solo JSON parsing, retry 1x su malformed, fallback `aligned=False` (fail-safe). Disable via `METNOS_SYNT_STAGE6_DISABLED=1` (test/dev). Quattro layer cumulativi: nuovi synth devono passare TUTTI i gate; existing synth migrano gradualmente (L3 demota inefficaci, L2 rifiuta al prossimo load se affinity overlap). 1057 PASS / 0 FAIL (32 nuovi test: 7+6+5+14). Storage delta=0, d789c4c0 preservato (67667 entries persons + 30401 scene). **L1 (vocab semantic gate) NON in scope**: prossimo sprint, dizionario canonico per i 22 verbi del vocab chiuso.

- **Named persons registry + composition filters + procedural index schema** (ADR 0113, 8/5/2026 sera): registro nominale persistente `~/.local/share/metnos/persons.sqlite` (slug case+accent-insensitive, hyphen/slash come separatore, top-k cosine no-centroid, dedupe idempotente via UNIQUE(slug,sha256,face_box), incremental `mode="add"` default). 4 nuovi executor: `set_persons`/`get_persons`/`delete_persons`/`find_persons_indices` con `name=` (resolve_name token-anywhere) o `reference_images=`, ambiguity → dialog `kind="choice_with_preview"` (PR5: thumbnail volto via path#bbox=x,y,w,h, HTTP form + Telegram media group, fallback label-only oltre 10 opzioni). `min_face_pixels` (10000/40000/80000) per filtro composizione su `idx="persons"`, parità su `find_persons_indices`. SigLIP confidence floor 0.12 in `find_images_indices(idx="scene")`. `describe_entries` cap 20 + truncated visibility. `paths_filter` per pipeline compositive («Matteo al mare» step1 persons → step2 scene su paths-ristretti). Planner prompt regole `(W) PIPELINE COMPOSITIVA SOGGETTO+CONTESTO` e `(W.bis) SOGGETTO + COMPOSIZIONE` (single-step per «primo piano di X»). **Procedural index schema (PR4)**: `runtime/index_schema.py` con `INDEX_SCHEMA_VERSION=2` + `IDX_TYPES=["scene","persons","gps"]` registry estensibile + `ENRICHMENTS` 9 campi (`image_w/h`, `taken_at_iso`, `bbox_area_fraction`, `face_count_in_photo`, `is_grayscale`, `brightness_mean`, `is_blurry`, `frontal_score`). `create_images_indices(idx=None)` di default itera IDX_TYPES (build di tutte le dimensioni in parallelo via systemd transient units); `find_*_indices` su missing index spawnano build per TUTTE le dimensioni mancanti (build-all-on-missing — minimize round-trips). 10 nuovi filter args parità su entrambi find executors: `min_face_fraction`/`taken_after`/`taken_before`/`is_grayscale`/`min_face_count`/`max_face_count`/`exclude_blurry`/`min_brightness`/`max_brightness`/`min_frontal_score`. `runtime/index_schema_upgrade.py` al boot HTTP: schema v1 → v2 incremental (riusa `vectors.npy` + bbox, ricomputa solo enrichment fields mancanti). `find_images_indices._invoke_multi_dir` paths_filter dispatch per-dir. **find_urls topic ranking** (POST-PR4): rimosso drop implicito score=0; topic = rank, non filter; nuovo arg esplicito `min_score: float`. Fixato test_pipeline_smoke pre-esistente. ADR 0081 doc allineato. 1093 PASS / 0 FAIL post rollout. 9 executor re-firmati. PR6 corpus registry deferred, valutare.

- **Scheduler v2 asyncio co-host** (ADR 0112, 8/5/2026 mattina): rewrite di `runtime/scheduler.py` (eliminato) come `runtime/scheduler_v2/` (asyncio-native, single asyncio.Task nel loop di `metnos-http.service`, `ThreadPoolExecutor` `min(32, cpu*4)` per offload sync). Single-table `schedule_entries` (recurring + one-shot stessa riga, distinguish via `recurring` bool). `next_fire_at` materializzato, loop dorme fino a `MIN(next_fire_at)` cap 60s — no fixed-tick. Trigger grammar: `daily@HH:MM` (TZ locale, DST `fold=0`), `every_Ns/m/h`, `at:<ISO>`, `cron:<5-field>` (croniter opzionale). Crash trail: `runs` row con `status='running'` pre-fire, marcato `crashed` al boot se orfano. IPC zero: un solo processo possiede lo stato vivo, clienti scrivono SQLite via `scheduler_v2.client`, kick in-process via `daemon_handle` weakref (sblocca il loop sincronamente per same-process callers). Out-of-process: pickup al prossimo loop iter (max 60s). Migration tool `migrate_v1.py` dalle 2 vecchie DB (`recurring_tasks.db` + `state.sqlite`), idempotente, invocata al boot di `make_app`. Per never-fired daily tasks anchor = `start_of_local_day` (non `now`) cosi' target di oggi nel passato viene recuperato. Callback registry esplicito in `builtin_callbacks.py::install_default_callbacks`; le 7 task v1 (`task_apply_ager`, `task_apply_executor_ager`, `task_synt_suggest`, `task_introvertiva_propose`, `task_introvertiva_apply`, `task_proposals_cleanup`, `task_lifecycle_summary`) caricate da `_v1_tasks.pyc` (bytecode preservato) via `SourcelessFileLoader` — follow-up: estrarre a sorgenti in `runtime/jobs/`. Eliminato: `runtime/scheduler.py` (1112 LOC), `metnos-scheduler.service` (user systemd unit), `recurring_tasks.bootstrap_into_scheduler` stub, 245 righe di test fixture v1 in `populate_cases.py`. Aggiornati `runtime/observability.py` (path + schema query) + `runtime/testing/seed_modules.py` (modulo `scheduler` → `scheduler_v2`). 115 pytest verdi + 1 skip croniter. Verificato live: `user_controllo_versione_amd_rocm` (mai firato sotto v1 per drift bug, root cause della rewrite) prima fire 8/5 07:54:07 UTC via v2 + run_now, completato 07:55:33 status=success.

- **PLANNER skip describe_entries dopo health** (ADR 0111): quattro difese in profondita' contro doppio blocco contraddittorio "Stato server"+"non disponibile" o "Stato server"+riassunto LLM allucinato dopo `get_processes(include_health=true)`. Level 1 prompt `(Z.cinque) HEALTH BLOCK GIA' COMPLETO IN OBSERVATION` (`prompts/it/planner.j2`, stile §6). Level 2 `agent_runtime.py` inietta `health_context: <dict>` quando `from_step` source ha `health` non vuoto; `describe_entries` pre-pend "STATO SERVER GIA' RIASSUNTO (NON RIPETERE)" + `_fmt_health_block` riusato. Level 3 runtime auto-final §7.9 a executor-step time: `chosen_name=="get_processes"` ok+`health` non vuoto + `is_multistep` + intent.verb NON in `_ACTION_VERBS_PRED` + query NON contiene `_HEALTH_IMPERATIVE_KEYWORDS` (costante module-level: `kill/uccidi/ferma/termina/stop /spegni/manda/invia/scrivi/esegui/lancia/riavvia/restart`) → `final_message=""`. **Level 4 safety net write-time** (7/5/2026 notte): in `_prepend_health_block_if_any` (chiamato da `TurnLog.write()`), quando il blocco salute viene prepended e la query NON contiene `_HEALTH_IMPERATIVE_KEYWORDS`, `final_message` LLM viene zerato PRIMA del prepend. Funziona indipendentemente da `is_multistep`/`chosen_mode`/path multi-step (es. `get_processes → scratchpad_read` o single-step), copre i casi dove Level 3 non scatta. Risolve bug live 7/5 turno con riassunto LLM allucinato (uptime in giorni inventato, RAM/dischi diversi dal blocco prepended) sotto al blocco corretto. Convergenza verificata: 3/3 iter "stato sistema" con 9 invarianti deterministici (no duplicato, 3/3 servizi ✓, no leak EN, no allucinazioni note).

- **Skill importer agentskills.io → executor** (ADR 0123, 10/5/2026 sera): pipeline 5-stadi (`runtime/skill_parser.py` ParsedSkill + `runtime/skill_translator.py` ExecutorPlan + `runtime/skill_codegen.py` Jinja deterministico con template `manifest.toml.j2` + `executor.py.j2` + `runtime/skill_description_llm.py` LLM stage 4 via `synt_stage4_description_imported.j2` + `runtime/skill_admission.py` 5 layer ADR 0114). Tabella `runtime/skill_vocab_map.json` mappa action+domain → azione_oggetto §2.2 deterministicamente; qualifier non in vocab folded nel verbo base. **Helper condivisi** `runtime/skill_wrapper.py` (5 funzioni: `_skill_home`, `_subprocess_runner`, `_run_api`, `_classify_error` con ERROR_CLASS_TABLE, `_needs_inputs_oauth_setup`, `_check_credentials`) riusate dai template Jinja per regola del 3 §7.2. **CLI** `metnos-skills import|list|uninstall|status|evaluate` via `runtime/cli/skills_cli.py`. **Provenance** `[provenance]` sez. manifest con `imported_from=agentskills.io/<owner>/<skill>`, `source_version`, `source_sha256`, `imported_at` — unico segno distintivo da synth/handcrafted; admission applicata uguale. **16° OBJECT `credentials`** (§2.2) per gestione token via 3 executor `find/set/delete_credentials` con capability invariante `metnos:credentials_metadata_only` (helper `_assert_no_secrets_in_return` blocca return con campi value/token/secret/api_key/password). Storage Fernet+HKDF in `~/.local/share/metnos/credentials/<binding>.json`. Pending store out-of-band per resume needs_inputs (token opaco, TTL 600s) evita leak cleartext in dialog callback. **5° reverse_pattern `delete_<object>_by_id`** §2.3 catalogo (registered via `runtime/reverse_patterns_patch.py`): contratto `_undo` espone `<object>_ids` + scope id (`calendar_id`/`folder_id`/...); runtime costruisce `delete_<object>(ids=..., scope=...)` con group-by su scope_id. Generalizzazione famiglia per delete remoto cross-domain (events/messages/contacts/files). **Helper time_window deterministico** `runtime/time_window_parser.py` accetta canonical §2.1 (`last-Nd`/`next-Nd`/`today`) + estensioni (`yesterday`/`tomorrow`/`last-week`/`next-week`/`last/next/this-month`/`this-year`/...) + ISO singolo/range + italiano `DD/MM/YY` + range italiano «dal X al Y». TZ Europe/Rome aware. 35 test inclusi cross-anno + DST. **Smoke battery import-aware Opzione B** (ADR 0123 Layer 5): file separato `runtime/smoke_imports.py` con `BATTERY_IMPORTS = []` popolato dall'importer (1-2 case per executor: query parafrasata da affinity, expected_first_tool = name, expected_arg_keys = args required); `runtime/smoke.py` concatena `BATTERY = BATTERY + BATTERY_IMPORTS` via import lazy con try/except. Separation of concerns: BATTERY canonica resta curata; gli import si auto-popolano senza pollution. Idempotenza: rimuovere `smoke_imports.py` torna a comportamento pre-import. Audit per case via campo `imported_from`. Worktree `/tmp/skill_importer_work/` 5400 LOC + 200 test PASS. Demo SKILL.md hermes google-workspace: 24/24 sub-command importati (post-gap-1 closure). NON installato in `/opt/myclaw/executors/` al momento; promozione live = task separato (sign Ed25519 + stage in `~/.local/share/metnos/executors/_imports/`).

- **Proposal auto-evaluator + ETA instrumentation** (ADR 0122, 10/5/2026): instrumentation forward sui synth proposals con verdict deterministico ACCEPT/GRAY/REJECT. **path_shape** (`runtime/path_shape.py`): SHA-256(16 hex) della sequenza `chosen_tool` produttiva escludendo `final_answer`/`describe_entries`/`undo_last_turn`/`request_new_executor`/`scratchpad_read`/`@uploaded` e step in errore; accetta dict (turn JSONL) o dataclass-like (`StepLog`). **proposals_eta_index** (`runtime/proposals_eta_index.py`): SQLite `~/.local/share/metnos/proposals_eta.sqlite` schema `path_eta_index(path_hash PK, sample_count, p50_ms, p95_ms, last_seen, sample_steps_json)`; API upsert/lookup/aggregate_from_jsonls. **Scheduler v2** task `proposals_eta_aggregate` daily@04:30 (rolling 7 giorni). **synth_request** enrichment: `handle_synth_request(..., current_steps=None)` calcola path_hash + lookup ETA + count_60d, scrive nei proposal JSON. **proposal_evaluator** (`runtime/proposal_evaluator.py`): 6 killer (inflation vs vocab.py / affinity_overlap >=0.4 stretto vs HANDCRAFTED_FAMILIES + synth piu' vecchio / test_pass_rate stage3 + final_state synthesized / reversibility_parity vs path_steps / error_class_discriminability >=2 distinti / observation_schema_stability §2.6 entries vs results) + 7 signal weighted score (eta_speedup +2/-1, call_freq_60d +1.5/-0.5, decidability_pct via `_bow_intent_simple`+prefilter +1/-1 su 12 riformulazioni IT+EN, noising_top10_pct +1, pipeline_terminal +1, truncation_honest +1, token_saving_pct +1). Verdict: score>=4+no_killer→accept, -2<score<4+no_killer→gray, score<=-2 o killer→reject. Audit JSONL `~/.local/share/metnos/synth_audit/proposal_evaluator.jsonl`. CLI `python -m admin.proposals_cli evaluate <id>` + `aggregate-eta --days N`. HTTP `GET/POST /admin/synth-proposals/{id}/evaluate`. Determinismo §7.9: niente LLM nell'evaluator (BoW intent + prefilter equipotenti). Soglia affinity 0.4 stretta vs 0.5 al catalog load (ADR 0114 L2). 47 nuovi test verdi. Estende ADR 0114 (4 layer admission al catalog load → 5° gate preventivo a evaluator-time per le proposte fresche).

- **Unified image enrichment index** (ADR 0117, 9/5/2026): UN solo asse `unified/` per corpus invece dei 3 idx disgiunti scene/persons/gps di ADR 0086 (SUPERSEDED). Schema v4 in `runtime/index_schema.py` (`INDEX_SCHEMA_VERSION=4`, `is_unified_schema()`); storage `~/.local/share/metnos/index/image/<sha8>/unified/{entries.jsonl, embeddings_text.npy, embeddings_face.npy, meta.json}`. Pipeline per-foto end-to-end: EXIF + dims + sha256 + ArcFace (RetinaFace+`buffalo_l`, embedding 512d) + VLM Qwen2-VL-7B su :8081 (genera `description`/`keywords`/`location_hint`/`activity_hint`) + BGE-M3 text embedding su description. Tutti modelli **locali** §10.3 (zero API esterne, llama.cpp + insightface + sentence-transformers in-process). `find_images_indices` riscritto single-axis: drop arg `idx=` (deprecato, ignorato con warn), args (`name`/`query_text`/`reference_images`/`min_face_pixels`/`min_face_count`/`max_face_count`/`near_lat-lon-radius_km`/`time_window`/`paths_filter`/`top_k`) combinano AND in single pass invece di pipeline 2-step con paths_filter ponte. `find_persons_indices` ridotto a thin alias compositivo (`name=` lookup + `min_face_pixels=` close-up). `create_images_indices` riscritto: una sola pipeline EXIF+ArcFace+VLM+BGE, idempotent refresh skip su mtime/size invariati, `force=true` per rebuild. Migration v3→v4 in `runtime/index_schema_upgrade_v4.py` + standalone `runtime/build_runner_unified.py` (invocato via `systemd-run --user`). Live batch `metnos-build-unified-d789c4c0.service` migrato 30401 foto in 2s (aggregator-only); `metnos-vlm-enrich-d789c4c0.service` per VLM full pass (bench 4.01s/foto reali end-to-end → ~33.9h su 30k). Phase 11 cleanup: scene/persons/gps subdirs eliminati post-migration. Manifest descriptions stringato/affermativo con sezioni Pattern: + Anti-pattern: + bullet args (re-firmati 3 executor). 985 PASS / 0 FAIL.

## 11. Decisioni di runtime

- **LLM tier**: 3 puntatori (fast/middle/wise). Default v1.1: `qwen3:8b` think=false num_predict=400 (procedural), Gemma 4 26B (middle/wise per synt + planner).
- **Tool-use protocol**: nativo Ollama+Qwen+Gemma (tool_calls strutturati). NIENTE parser JSON fragile.
- **Data piping**: `from_step: int` (schema-guided) + `{{stepN.field}}` per scalari.
- **Intent extractor**: LLM-based gemma 4 26B middle, ~370ms/query, 100/100 su test corpus. Fallback bag-of-words. Bypass deterministico per undo.
- **Universal helpers**: `classify_entries`, `filter_entries`, `undo_last_turn` sempre. `describe_entries` SOLO se intent.verb NOT in action_verbs.
- **Reverse patterns**: `runtime/reverse_patterns.py` — 4 entry deterministiche.
- **Platform policy**: `runtime/platform_policy.py` — system files cross-mount-safe + protected paths host-aware.
- **Messaggi**: `runtime/messages.py` — dizionario unico code→template `ERR_*/WARN_*/MSG_*/LOG_*`. Mai stringhe duplicate negli executor.

## 12. Fasi di sviluppo

- **Fasi 1-5 chiuse** (codice + doc allineati): POC end-to-end / test framework v1.1 / synt 5 stadi / reality check + Telegram MVP / vaglio LLM + sandbox + dispatcher + capability.
- **Fase 6** voce — STANDBY (riusare satellite proprietario; non bloccante).
- **Fase 7** topic 1: client Rust per executor remoti — 10 decisioni chiuse, MVP 5 settimane.
- **Fase 7** topic 2+: multi-OS / multi-user / robustezza / disconnect-proof — DA AVVIARE.
- **Fase 8** stress logico — DOPO fase 7.

## 13. Quando aggiornare questo doc

OGNI volta che: nuova norma di codice/architettura/processo; chiusura fase o nuovo macro-topic; sezione contraddetta dal codice (aggiornare PRIMA della PR); nuova decisione di runtime (LLM tier, helper universale, vincolo dominio nel planner).

NON aggiornare per: bug fix puntuali (commit message); decisioni temporanee/sperimentali (`~/.claude/.../memory/`); stato di sessione (`metnos_session_*.md`); dettagli di un singolo ADR (vivono nell'ADR).

## 14. HTTP API (Phase 1)

Server `runtime.metnos_http_server` su porta **8770** (separata da 8765 pairing). Stile aiohttp bare uniforme: ROUTES come tuple list, helper `_error()`, middleware `auth_middleware`. Tre ruoli: anonymous/user/admin. Admin key `~/.config/metnos/admin.key` (mode 0600), 256-bit hex, fingerprint sha256 nei log. Endpoint `/agent/{health,turn,devices/me}` + `/.well-known/metnos.json` + `/admin/{,proposals,executors,executors/stats,runs,safety,turns}`. Negotiation `Accept: text/html` (htmx + Jinja2 + uPlot CDN, no build step) vs JSON. ETag su collezioni admin. SSE su `/agent/turn` se `Accept: text/event-stream`. Vedi ADR 0078.

---

**Riferimenti**

- ADR registry: `/opt/myclaw/decisions/` (`0001-0123`, `0055`/`0115`/`0116`/`0121` skipped) — dettagli implementativi e razionale.
- Architettura canonica: `/opt/myclaw/docs/it/architecture/` (+ EN bridge simmetrico).
- Memorie persistenti: `~/.claude/projects/-opt-myclaw/memory/MEMORY.md`.
- Repertorio prompt: `/opt/myclaw/runtime/prompts/<lang>/*.j2` (ADR 0092).
