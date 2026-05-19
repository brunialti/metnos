# Metnos — Linee guida di progetto

> **OBBLIGO**: leggere integralmente all'inizio di ogni sessione. Codifica decisioni architetturali, convenzioni di codice e norme di processo gia' stabilite. Quando un punto e' obsoleto o errato, AGGIORNALO subito invece di lavorarci attorno.
>
> Mantenuto da: agente. Aggiornamento ad ogni sessione che fissa una nuova norma duratura.
> Ultimo aggiornamento: 2026-05-19 v5 (Fase 13: multi-tool fast-path memoization L2 + bridge L2→L3 + composability + chain).
> Norme durature recenti (dettagli in ADR / git log):
> - Multi-tool fast-path memoization L2 (19/5 v5): `runtime/multi_tool_paths.py` + `runtime/jobs/multi_tool_promote.py`. TTL active-days, uses>=3 match, uses>=50 promotion. Composability fast-path-of-fast-path nel recording. Chain opt-in via resume_with_scratchpad. Env `METNOS_MULTI_TOOL_FAST_PATH=1`. ADR 0150 ext.
> - PLANNER split call grammar GBNF (19/5 v3): `planner_split.chat_with_tools_split` 2-call (SELECTOR enum + ARGS FILLER schema-tool). Opt-in `METNOS_PLANNER_SPLIT=1` default OFF pre-bench. Smoke 14-22× LLM speedup, risolve loop_break. Solo llamacpp. ADR 0151.
> - Giant prompt slimming sequenza b+c+d (19/5 v2): tool schema slim -65% chars; section pruning core-only -72%; fast_path `get_location`. Module 297/297 stabile.
> Storia precedente: `git log CLAUDE.md`. Voci 17/5 spostate in git log; canonical entry in §10.6 (ADR 0140-0143).

---

## 1. Cos'e' Metnos

Assistente personale self-hosted (su `.33`, Strix Halo 96GB unified). Microarchitettura a executor sintetizzati al volo via synt multistage; runtime ReAct con planner LLM (Gemma 4 26B middle/wise locale + Sonnet/GPT-5 frontier come fallback). Canali: **Telegram** + **HTTP porta 8770** (htmx + Jinja2 + uPlot, ADR 0078). Pipeline immagini in-process: SigLIP-base + RetinaFace+ArcFace + EXIF (ADR 0086/0117). Lingua principale: italiano; corpus doc bilingue IT+EN. Etimologia: `mētis + noûs`. Process name: `myclaw`. Dominio: `metnos.com`.

ADR registry canonico: `decisions/` (relative alla repo root; `0001-0148`, `0055`/`0115`/`0116`/`0121` skipped — fonte unica per "perche' abbiamo scelto cosi'").

## 2. Principi cardine (mai negoziabili)

### 2.1 Executor vettoriali per costruzione
Input come **lista** (paths, entries, urls, ids). Output **sempre lista**, anche degenere (N=0/1/molti). Niente `*_batch`: la versione batch *e'* l'executor. Iterazione interna (loop, paginazione, window come `time_window: today|last-Nd`); branching su osservazione torna al planner. Cap inferiore = 0; cap superiore = parametro esplicito (`max_total`, `max_results`, `max_bytes`).

### 2.2 Naming convention compositiva
Struttura: `azione_oggetto[_qualifier]`. Vocabolario CHIUSO (escalation a Roberto per nuovi termini), centralizzato in `runtime/vocab.py`.

- **23 azioni**: `read, write, move, delete, create, find, list, filter, sort, group, classify, get, set, send, describe, render, extract, compress, compute, compare, change, order, share`.
- **Ortogonalita' 5 verbi-produttori** (asse «com'e' fatto l'input primario»): `find`=pattern/query (discovery, sussume esistenza); `get`=id noti o snapshot; `read`=id sorgente→contenuto; `list`=container enum senza contenuto; `filter`=lista preesistente, riduce per predicato. Discrimine find vs get: pattern→`find`; id/stato→`get`. `change`=forma/parametri (resize/convert); `order`=ordinamento PERSISTENTE corpus (vs `sort` in memoria); `share`=OUTBOUND CONSENT grant ACL remoto (ADR 0128, distinto da `send`=copy outbound e `set`=upsert stato interno).
- **Importer verb boundary** (ADR 0128): vocab si applica anche a executor importati (ADR 0123). Mapping provider→Metnos per `(target_kind, side_effect)`: `gmail get MSG_ID`→`read_messages`; `docs append`→`write_files_text`; `gmail modify` (labels)→`set_messages`; `drive share`→`share_files`. Tabella in `runtime/skill_vocab_map.json::contextual`. Verifier `runtime/importer_verb_verify.py::check_plan` (layer 6.bis ADR 0114).
- **Confini stretti**: `fetch` rimosso (HTTP GET=`get_urls`); `extract` solo archivi (zip/tar/gz); «estrai righe testo»=`filter_texts_lines`; «estrai testo PDF/HTML»=`read_files_pdf/html`; «estrai campi entries»=`get`.
- **19 oggetti** (plurale): `files, dirs, packages, messages, events, contacts, places, processes, urls, numbers, images, signatures, texts, proposals, persons, tasks, inputs, credentials, entries`. Eccezioni: `get_inputs`={values:{var:value}} UI dichiarativa (ADR 0090); `*_credentials` espongono SOLO metadata, cleartext mai al PLANNER (ADR 0123); `entries` meta-oggetto in-memory turno (niente `find/read/get_entries`); `persons` registro nominale vs `contacts` (ADR 0113/0137); `tasks` scheduler v2 vs `events` (ADR 0112/0137).
- **System verbs riservati**: `undo`, `admin` fuori dai 22 verbi canonici. Builtin runtime only (`undo_last_turn`, `admin`); vaglio rifiuta `name` con questi prefissi. Stage 1 NAMING non li propone.
- **Qualifier opzionali, 4 famiglie**:
  - **formato/codifica**: `_csv, _xlsx, _ocr, _zip, _pdf, _xml, _html, _json, _text, _gz, _tar, _video, _audio, _image, _hash`.
  - **modalita'**: operazione (`_size, _format, _similar, _loc, _empty`); granularita' (`_lines, _paragraphs, _sentences, _pages, _segments`); mezzo persistente `_indices` (CLIP/ArcFace/EXIF/perceptual hash — ECCEZIONE: `find_<dom>_indices` ritorna entries del dominio). `_empty` (ADR 0127) stato "vuoto/sotto-soglia" cross-domain con arg canonical `size` (str unit-aware).
  - **safety policy** (oggetto `signatures`, ADR 0071): `_blacklist, _whitelist, _graylist, _forbidden, _seed, _diff, _sanity, _command, _reversibility, _promotion, _candidates`.
  - **provider** (15/5/2026): backend non-default. `_google_workspace` (skill gmail/drive/calendar ADR 0123), `_metnos` (default implicito omesso). `tool_grammar._PROVIDER_SUFFIX_MARKERS` filtra dal pool se query senza marker. Loader marca `dormant=True` se skill senza credenziali (`skill_credentials.py`).

Stage 1 di synt usa MAPPING bilingue IT+EN. Sinonimi prima dell'estensione del vocabolario.

### 2.3 Reverse pattern catalogo deterministico
Manifest dichiara `reverse_pattern` da catalogo chiuso: `swap_src_dst` (move/rename), `delete_created_dirs`, `delete_created_paths`, `restore_blob_backup` (richiede `blob_path`+`blob_sha256` nei results), `delete_<object>_by_id` (ADR 0123, contratto: `<object>_ids` + scope id, runtime costruisce `delete_<object>(ids=..., scope=...)`). Blob: `$METNOS_HISTORY_DIR/<METNOS_TURN_ID>/blob/<sha256>.bin`.

### 2.4 Robustezza al confine NL→determinismo
Executor accetta: `0-as-placeholder` (cap=0 → no limit), compound case-insensitive di default, args plurali ammessi (`paths` accetta anche 1 elemento). Helper comuni in `runtime/executor_helpers.py`. Niente patch reattive per-executor.

**Convention args `array of string` (15/5/2026)**: decidere a design-time il dominio.
- **Aperto** (testo libero: summary/subject/label) → tolleranza wildcard `*`/`?` via `fnmatch.fnmatchcase` case-insensitive. Description manifest dichiara "valori con `*` o `?` trattati come glob". Es: `filter_entries.where_in`.
- **Chiuso** (enum/slug/ID/scope OAuth/time canonical/email) → match esatto stretto. Description dichiara "MATCH ESATTO, wildcard NON supportati". Es: `delete_persons.chosen_slugs`, `set_credentials.scopes`, `create_events.attendees`.

Razionale breve: LLM ha bias verso glob universali; tolleranza fnmatch sul dominio aperto previene fallimento silenzioso, dichiarazione esplicita sul chiuso impedisce abuso.

### 2.5 Manifest leggibili da LLM medium
Manifest TOML = "prompt del tool" per Gemma 4 26B (NON Sonnet/Opus). Modello canonico: `executors/find_files/manifest.toml`. Criteri: description 2-5 frasi corte (max 25 parole/frase); forma prescrittiva §6 sui punti confondibili; esempi tra virgolette per formati non ovvi (`time_window="last-24h"`); default in chiaro; niente gergo Python; affinity 8-15 termini IT+EN user-facing; args 1 frase + tipo + esempio + default; boundary del verbo §2.2 con "USO CORRETTO"/"NON CONFONDERE CON"; output structure dichiarata per pipeable next-step. Anti-pattern: description 800 parole, pattern-by-example senza separatore "non copiare letteralmente".
**Multilingua** (ADR 0092): `[description]` tabella per lingua + companion `manifest.lang_state.json` traccia hash. Doc canonico: `docs/it/architecture/multilang.html`.

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

- **EMAIL/MAIL/IMAP** → `read_messages`/`send_messages`/`move_messages`/`find_messages`. Mai `move_files` su mail. Cancellazione = `move_messages(dst_folder="Trash")` (in Metnos `delete_messages` non esiste; vedi `mail.yaml::delete_mail_is_move_to_trash`).
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

Tre stili disgiunti per i `.j2` di `runtime/prompts/<lang>/`:
- `prescriptive`: planner (e sezioni split), describe_entries_*, classify_entries — applica §6 (DEVI/NON DEVI/OK/ERRORE per ogni regola comportamentale).
- `definitional`: synt stages 1-4, intent_extractor, vaglio — vocabolario chiuso + few-shot di output strutturato; niente §6.
- `few_shot`: 12 addendum verbo stage 5 — esempi codice; nessuna prosa prescrittiva.

Ogni `.j2` dichiara la tipizzazione in un frontmatter Jinja `{# --- ... --- #}` con 8 campi (`role`, `tier`, `lang`, `style`, `version`, `owner`, `updated`, `sha_prev`). Il linter `runtime/prompts_lint.py` (§10.6.45) applica i check §6 SOLO a `style: prescriptive`.

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
`(a)/(b)/(c)` o numeri, mai lettere greche.

### 7.7 Niente `_batch` come suffisso
Vedi 2.1.

### 7.8 Italiano senza anglicismi
Caccia ad anglicismi (peer, trigger, goal, plumbing, gate) e calchi (costosa/mordere/ci reagisce).

### 7.9 Codice deterministico > LLM
**Codice deterministico > LLM se equipotente, equiefficace o se codice deterministico [sarebbe] troppo complesso.** LLM solo quando deterministico e' inefficace, troppo complesso da scrivere/mantenere, o impossibile. Anti-pattern: LLM per validare/classificare cose che `vocab.py` o un regex coprono. LLM giustificato: intent extractor (parser linguistico equipotente troppo complesso).

### 7.10 Re-sign executor dopo edit del codice
Edit di `<executor>.py` cambia il digest sha256 del codice ma NON il `manifest.toml`. Al boot/reload, `runtime/loader.py::verify_executor` scarta silenziosamente l'executor se `declared digest != actual digest`. Workflow OBBLIGATORIO dopo ogni edit di un `.py` di executor: `python -m runtime.sign sign executors/<name>` (relative alla repo root) + restart `metnos-http.service`.

### 7.11 No path assoluti hardcoded (rename-resilient)

Niente `Path("/opt/myclaw/...")` o `Path("/opt/metnos/...")` nel codice
attivo. La install root si auto-deriva via `Path(__file__).resolve().parents[N]`
in `runtime/config.py::PATH_ROOT`; tutti i path derivati (`PATH_RUNTIME`,
`PATH_EXECUTORS`, `PATH_WORKSPACE`, `DB_*`) seguono. Override esplicito via
env `METNOS_INSTALL_ROOT` (con alias deprecato `METNOS_HOME`).

Razionale: la rinomina futura `/opt/myclaw → /opt/metnos` su `.33` deve
essere zero-config. Convenzione: ogni callsite con un path assoluto verso
la install root usa `from runtime import config as C` e poi `C.PATH_*`.
Per i sotto-directory `models/`, `decisions/`, `install/` derivati dalla
install root: `C.PATH_ROOT / "<sub>"`. ADR 0148 documenta le 8 categorie
di disalignment e lo stato del refactor R2.

### 7.12 Output stringato preferenziale
  - Risposta media: max 3-5 righe, no preamble, no riepilogo finale.
  - Tabelle compatte ok. Niente commentario dopo edit, solo risultato.

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
- `mykleos@knowcastle.com` (register.it) → `scripts/check-mail.sh` (relative).
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

> Una riga per meccanismo. Dettagli: ADR registry `decisions/` (relative). Solo le voci da memorizzare al call-site (file/funzione/policy) restano qui.

- **Smoke battery** (`runtime/smoke.py`): query "must work" + invariants. OBBLIGATORIA prima di `./deploy.sh`, dopo synth, in cron daily, su tocchi a `prefilter.py`/`agent_runtime.py`/`synt_multistage.py`/`loader.py`. Routing assertion `expected_first_tool` (ADR 0114 L5).
- **Catalog invariants al load** (`runtime/loader.py`): rifiuta synth con name collision verso handcrafted.
- **Prefilter precursor universale** (`prefilter.rank_with_intent`): inietta UN precursor per ogni verbo NON producer (`read, find, list, get`).
- **No synth ridondanti**: stage 1 NAMING preferisce il name canonico esistente quando l'intent e' coperto.
- **Prefilter primary tools per object** (ADR 0075): `_OBJECT_PRIMARY_TOOLS` in `prefilter.py` allineato ai 19 OBJECTS di `vocab.py`.
- **Adaptive re-rank intra-turno** (ADR 0072): `runtime/adaptive_rerank.py`, add-only, cap `2×k_max`.
- **Scheduler gate user-activity** (ADR 0074): task notturni age-based sospesi se ultima interazione utente precede ultima esecuzione. Sorgente: `~/.local/share/metnos/turns/*.jsonl`.
- **Synth_request short-circuit** (ADR 0076): `handle_synth_request` 2 short-circuit deterministici pre-cascata (`already_in_catalog` + alias canonico).
- **Introvertiva quality filters** (ADR 0077): `candidates_specialize` 6 filtri deterministici.
- **GC synth rifiutati** (ADR 0079): `loader.py::_gc_collisions` sposta i `rejected` in `/tmp/metnos_synth_gc_<ts>/`. Solo path dentro `SYNTHESIZED_EXECUTORS_DIR`.
- **PROJECT PATHS noti nel PLANNER** (ADR 0079): `runtime/project_paths.json` mappa progetti → root paths.
- **Pipeline web/news/scuola** (ADR 0081+0082+0098+0101+0105+0108): `find_urls`/`read_urls_html`/`read_urls_pdf`/`login_session`. Tier resolution `~/.config/metnos/{owned_domains,trusted_origins}.json`. UA `metnos-crawler/1.x`.
- **Indici di dominio** (ADR 0086, image SUPERSEDED-BY 0117): pattern `create_<dom>_indices`/`find_<dom>_indices`/`get`/`delete`. Storage `~/.local/share/metnos/index/<dom>/<sha8>/<idx>/` (path LOGICAL, no `.resolve()`).
- **CIFS/SMB mount via admin → sudoer** (ADR 0087): `runtime/safety/canonicalize.py` + `runtime/cifs_helper.py` + `verb_unique/sudoer.py` placeholder `${METNOS_CIFS_CREDS}`. Niente password in argv.
- **Admin esposto al PLANNER** (ADR 0088): `admin` con `EXPOSE_TO_PLANNER=True`, vaglio always-on. HMAC consent token TTL 600s in `~/.local/share/metnos/.admin_consent_key`.
- **Credenziali UX 3 strati** (ADR 0089+0091): `extract_credentials` regex + dialog `needs_inputs` con `orchestrate_needs_inputs` + CLI `metnos-cli credentials`. Binding: `_BINDING_STRONG/_WEAK` + helper `<kind>_helper.py`.
- **Credenziali single store** (ADR 0131): `runtime/credentials.py` Fernet+HKDF, domain `smtp_<account>`. Fallback `.env` Layer 2. CLI `python3 -m credentials_migrate`.
- **Engine UI dichiarativo** (ADR 0090): `get_inputs(title, dialog=[...], fmt=...)`. Storage `runtime/dialog_pending.py`. Adapters Telegram + HTTP `/agent/dialog/<id>/{form,submit,cancel}`.
- **Prompt-as-data + multilingua** (ADR 0092): `runtime/prompts/<lang>/<role>.j2`. `prompt_loader.get(role, lang, **vars)`. Invariante "sub-dir lingua secondaria stesso set di `it/`" enforced al boot. CLI `metnos-prompts`. Report user-facing via `i18n.sqlite` chiavi `MSG_*`.
- **Async indexing build** (ADR 0093): systemd transient unit `metnos-build-<sha8>-<idx>`. Atomic write + resume da checkpoint. Disable test `METNOS_HTTP_DISABLE_BUILD_TASKS=1`.
- **Fast path deterministico** (ADR 0094): `runtime/fast_path.py` short-circuit query triviali PRIMA del PLANNER. Tabella chiusa `_FAST_PATTERNS`. ZERO LLM. Estendere SOLO con mapping 1:1.
- **Multi-tool fast-path memoization L2** (ADR 0150, 19/5/2026 v4-v5): `runtime/multi_tool_paths.py` sqlite `~/.local/share/metnos/multi_tool_paths.sqlite` con tabella `system_active_days(date PK, day_rank monotono)`. TTL = N giorni di **attivita' effettiva** (default 30, env `METNOS_MTP_TTL_ACTIVE_DAYS`). Threshold `uses>=3`, cosine 0.88, env opt-in `METNOS_MULTI_TOOL_FAST_PATH=1`. Recording in `TurnLog.write()` NON skippa step con flag fast_path (composability fast-path-of-fast-path). Chain opt-in `METNOS_MULTI_TOOL_FAST_PATH_CHAIN=1` via `resume_with_scratchpad` quando `_query_has_continuation` rileva multi-verbo. Bridge L2→L3: `runtime/jobs/multi_tool_promote.py` daily callback `multi_tool_promote` (uses>=50 → proto-mnest in mnestoma, naming `<verb_last>_<obj_first>` via `multi_tool_paths.derive_synth_name`).
- **Invariante executor > fast-path bidirezionale** (19/5/2026 v5, ADR 0150 ext): `multi_tool_paths.try_match` + `record_path` accettano `available_tool_names: set`. Matcher: se `derive_synth_name(tools) ∈ catalog`, UPDATE state='demoted' e ritorna None (caller cade a L1/PLANNER). Recorder: se synth equivalente esiste, skip silenzioso (ritorna 0, nessuna riga inserita). Wire-in `agent_runtime._try_multi_tool_path_playback` e `TurnLog.write` passano `{e.name for e in catalog}`. Pianificatore: sezione "EXECUTOR > FAST-PATH" nei `_core.j2` IT+EN obbliga UN solo step quando il synth e' nel pool. Doc canonico: `docs/{it,en}/architecture/{mnestoma,mnestome}.html` §3-bis tip box bidirezionale.
- **args_extractor V1.5 hybrid** (ADR 0149 ext + 0150 ext, 19/5/2026 v5): `runtime/args_extractor.py` regex chiusa (PATH/URL/INT/EMAIL/FILE_EXT_GLOB/DATE/TIME_WINDOW) + memoization `args_observed` da `canonical_query_log` (nuova colonna, migrazione idempotente in `mnestoma.__init__`) + LLM fallback opt-in `METNOS_CQ_ARGS_LLM=1` (tier fast, cache disk `~/.cache/metnos/args_extractor_llm/`). Extension: "home" → ~/, "file PDF" → *.pdf (con sinonimi IT documenti/document), keywords oggi/ieri/domani/dopodomani IT+EN → ISO date, "questa settimana"/"last 7 days" → time_window canonical. `record_canonical_query` accetta `args_observed` kwarg; canonical_matcher carica via `_load_entries` e passa a `extract_args`.
- **Config persistente runtime.toml** (Fase 12, 19/5/2026 v5): `runtime/runtime_settings.py` legge `~/.config/metnos/runtime.toml` con sezioni `[fast_path]` e `[multi_tool_fast_path]`. Override hierarchy `env METNOS_* > toml > default`. Cache process-life con reload on mtime change. Typed accessors `canonical_query_enabled()`, `multi_tool_fast_path_enabled()`, etc. `ensure_default_config()` idempotente per primo boot, integrato in `install/setup.sh` come `step_runtime_config`. canonical_matcher / multi_tool_paths / args_extractor delegano le default lookups al runtime_settings (fallback statico se modulo non disponibile).
- **Output formatter deterministico** (ADR 0095): `runtime/output_format.py` channel-agnostic markdown. NIENTE LLM nel formatter.
- **Proposals cleanup** (ADR 0096): `runtime/proposals_cleanup.py` 4 op deterministiche, NIENTE delete (move + UPDATE). Task scheduler daily@06:00.
- **Lifecycle summary** (ADR 0097): `runtime/lifecycle_summary.py` aggregatore READ-ONLY dei 4 ager. Task daily@06:30.
- **Web crawl parallel + strategy** (ADR 0098): `runtime/host_throttle.py` thread-safe Semaphore per-host. BFS via ThreadPoolExecutor cap `min(64, cpu*4)`, per-host T1=2/T2=8/T3=16.
- **Runtime perf** (ADR 0099): `runtime/fast_path.py::try_seed_step` + `runtime/loader.py::_CATALOG_CACHE` + reasoning budget dinamico PLANNER (step1=768, step2+=256, solo `llamacpp`).
- **Executor perf parallelizzazione** (ADR 0100+0103): `HostThrottle` + ThreadPoolExecutor su `read_urls_html`/`read_urls_pdf`/`compute_files_loc`. Pattern §7.4: misurare prima di parallelizzare.
- **Crawler error_class + soft-fail** (ADR 0101): `read_urls_html._fetch_one` ritorna `(None, {"error","error_class"})`. Classi: forbidden/rate_limited/not_found/server_error/timeout/network/non_html/js_rendered/unknown.
- **Thinking-leak scrubber** (ADR 0102): `_scrub_thinking_leak` in `runtime/agent_runtime.py`. Trigger EN/IT chirurgico. Invocato in `TurnLog.write()` ramo `final_kind=="answer"` PRIMA dei prepend.
- **Report runtime user-facing i18n** (ADR 0104): chiavi `MSG_*` in `i18n.sqlite` IT+EN. Determinismo §7.9.
- **HTTP cache disk-based** (ADR 0105): `runtime/http_cache.py` storage `~/.cache/metnos/http/<sha[:2]>/<sha>.json`. TTL default 900s, env `METNOS_HTTP_CACHE_TTL_S`. Cleanup weekly 7d.
- **Vaglio safe-verb shortcut** (ADR 0107): `vocab.SAFE_VERBS` 11 verbi. In `vaglio.judge` post-guardia → `Verdict(judge_kind="safe-verb-shortcut", score=1.0)`.
- **Auto-degrade T2→T1 host_health** (ADR 0108): `runtime/host_health.py` sliding-window 60min. Soglia 3 → `~/.config/metnos/blocked_origins.json` TTL 24h.
- **Channel-aware HTML rendering** (ADR 0109+0110): `runtime/html_sanitizer.py::to_safe_html` (Telegram) + `to_safe_html_full` (HTTP). `http_routes_agent::_safe_final_html` dispatcha. `_flush_para` GFM soft break (`\n` line, `\n\n` paragrafo).
- **Sync `pairings.db` → `users.user_channels` al boot** (ADR 0083 multi-user): `runtime/users_pairings_sync.py::sync_pairings_to_user_channels()` deterministico (§7.9), idempotente.
- **Descrizione human-readable proposte introvertiva**: `runtime/http_routes_admin.py::_describe_proposal(kind, sig_key)` deterministico (§7.9), 6 chiavi i18n `MSG_PROP_*`. Render al volo, mai persisted.
- **Synth admission policy 4 layers** (ADR 0114): L2 affinity overlap guard Jaccard ≥0.5 (`loader.py::_check_affinity_overlap`); L3 efficacy ager (`runtime/executor_aging.py`, handcrafted MAI demoted); L5 smoke; L6 LLM semantic verifier (`runtime/synt_stage6_verify.py`, disable `METNOS_SYNT_STAGE6_DISABLED=1`).
- **Named persons registry + procedural index schema** (ADR 0113): `~/.local/share/metnos/persons.sqlite` (slug case+accent-insensitive). 4 executor `*_persons` con ambiguity → dialog `kind="choice_with_preview"`. Schema `runtime/index_schema.py` registry `IDX_TYPES` + 9 enrichments.
- **Scheduler v2 asyncio co-host** (ADR 0112): `runtime/scheduler_v2/` single Task nel loop `metnos-http.service`. Single-table `schedule_entries`. Trigger grammar: `daily@HH:MM`/`every_Ns/m/h`/`at:<ISO>`/`cron:<5-field>`. Callback registry `builtin_callbacks.py::install_default_callbacks`.
- **PLANNER skip describe_entries dopo health** (ADR 0111): 4 difese post `get_processes(include_health=true)`. Safety net write-time `_prepend_health_block_if_any`.
- **Skill importer agentskills.io → executor** (ADR 0123): pipeline 5-stadi. CLI `metnos-skills import|list|uninstall|status|evaluate`. Mapping `runtime/skill_vocab_map.json` (verb boundary §2.2). Helper `runtime/skill_wrapper.py`. `[provenance]` manifest. Helper `runtime/time_window_parser.py`. Smoke `runtime/smoke_imports.py`.
- **Proposal auto-evaluator + ETA instrumentation** (ADR 0122): `runtime/proposals_eta_index.py` + scheduler v2 daily@04:30. `runtime/proposal_evaluator.py` 6 killer + 7 signal → verdict. CLI `python -m admin.proposals_cli evaluate`.
- **Unified image enrichment index** (ADR 0117, supersedes 0086 per image): UN solo asse `unified/` per corpus. Schema v4 in `runtime/index_schema.py`. Storage `~/.local/share/metnos/index/image/<sha8>/unified/`. Pipeline: EXIF + ArcFace + VLM Qwen2-VL-7B + BGE-M3. Build `runtime/build_runner_unified.py` via `systemd-run --user`.
- **Prompt architecture A+B+C** (ADR-in-writing): hook `scripts/pre-commit-symmetry-it-en.sh`; daemon `runtime/jobs/i18n_translate_pending.py` daily@02:00. Split `planner.j2` in `_core` + sezioni (mail/calendar/web/photos/system/admin_shell) + `_footer`; `prompt_loader.compose(role, lang, sections)` LRU 3-layer; `_OBJECT_TO_SECTIONS` da `vocab.py`. Linter `runtime/prompts_lint.py` + CLI `metnos-prompts lint --strict`. §6.1 (tipizzazione prompt).
- **Pattern intent-implicit** (ADR 0129): `vocab.detect_implicit_actions(query)` deterministico §7.9. Wiring: `intent_extractor` → `agent_runtime` → `orchestration._orchestrate_implicit_actions` (`_ACTION_TEMPLATES`). `_handle_needs_inputs` propaga `decision="needs_inputs"` (OAuth flow).
- **Backend tree allineato agli OBJECTS** (ADR 0130): `runtime/backends/<OBJECT_PLURALE>/<provider>.py`. Auto-default `_default_client()` per dispatcher via filesystem check. Retry §7.9 3× su transient. `send_messages` timeout_s=90.
- **Plugin esterni per backend** (ADR 0132, scaffolding): `~/.local/share/metnos/plugins/<name>/{plugin.toml, <object>.py}`. `runtime/plugin_loader.py::load_plugins(object)`. Precedenza builtin>plugin. Wiring deferred.
- **Constrained generation tool_call** (ADR 0133): `runtime/tool_grammar.py` genera GBNF deterministico per ogni step. Pool filter `filter_pool_for_grammar` esclude escape-hatch senza marker semantico. Provider wiring `LlamaCppProvider.chat_with_tools(grammar=)`. Safety net `runtime/loop_detect.py::is_repeated_failure(threshold=2)`. Opt-in `METNOS_GRAMMAR=1`.
- **Builtin tool naming canonical §2.2** (ADR 0133 ext): builtin scheduler v2 al verbo canonico (`create_tasks`/`list_tasks`/`delete_tasks`/`read_tasks`/`set_tasks`). Handler con fallback `id → name`.
- **Semantic affinity fallback ibrido** (ADR 0134): `runtime/affinity_semantic.py` BGE-M3 ONNX int8 cache `~/.cache/metnos/affinity_emb/<sha16>.npz`. Fallback se `top_score < METNOS_SEMANTIC_THRESHOLD` (default 8). Re-rank `score = hard + alpha * max_cosine` (alpha 4.0). Opt-out `METNOS_SEMANTIC_MATCH=0`.
- **Grammar pool extensions** (ADR 0135): (a) `final_answer` synthetic tool nel pool GBNF da step≥2. (b) `_parse_tool_call_tolerant` recovery regex per JSON truncated. (c) `_FROM_STEP_HELPERS` esclusi dal pool al primo step §4.2.
- **Skill dormancy + provider qualifier** (ADR 0136): §2.2 4 famiglie qualifier (+`provider`). `tool_grammar._PROVIDER_SUFFIX_MARKERS` filtra pool grammar se query non contiene marker. `runtime/skill_credentials.py` mappa skill→check function §7.9. `Executor.dormant` calcolato al load. `prefilter._filter_dormant` skip dal pool top-K.
- **Vocab extension persons+tasks** (ADR 0137): OBJECTS 17→19. Distinzioni `persons` vs `contacts`, `tasks` vs `events`. `_OBJECT_TO_SECTIONS`/`_OBJECT_PRIMARY_TOOLS`/`OBJECT_DEFAULT_MUTATING_VERB`/synonyms IT+EN.
- **filter_lists set ops bi-list + tassonomia operatori liste** (ADR 0138): `filter_lists` (op intersect/union/difference/symdiff/overlap) args `op`+`on_keys`+`with_step`. Wire `agent_runtime._resolve_from_step` Layer 5. Tassonomia: `filter_entries` (1L predicati) / `filter_lists` (2L set ops) / `compute_entries` (1L scalari) / `compute_lists` (2L scalari, riservato).
- **LLM query expansion language-sensitive** (ADR 0139): `_expand_query_via_llm` Gemma 4 26B + cache `~/.cache/metnos/query_expansion_llm/<sha256(q)>.json`. Prompt EN + few-shot multi-lang per language fidelity. Supersedes corpus-token BGE-M3 expansion su query brevi mono-token.
- **Prefilter modulare + scaling validato 1000 tool** (ADR 0140): package `runtime/prefilter_strategies/` con 14 strategie via env `METNOS_PREFILTER`. Default `token_flat_v2`. Telemetria JSONL `~/.local/share/metnos/prefilter_telemetry.jsonl` + CLI `runtime/prefilter_stats.py` + `runtime/bench_prefilter_strategies.py`. Watchpoint: rifare bench quando vocab cresce 2x azioni / 3x oggetti.
- **Sandbox per-skill foundation** (ADR 0140 ext): `Executor.sandbox_profile`+`provenance`+`is_imported`. `runtime/skill_audit.py` JSONL append-only (no PII). Watchdog `runtime/jobs/skill_sandbox_watchdog.py` daily@06:35: trigger ≥5 skill third-party OR ≥1 guest paired → notifica admin per Fase C.
- **GitHub provider first-party** (ADR 0141): skill `~/.local/share/metnos/skills/github/` + `runtime/skill_wrapper_github.py` + `runtime/skill_credentials.py::_check_github_pat`. 13 executor `*_github`. Watcher scheduler v2 + Q&A learning loop (`runtime/jobs/github_dedup.py` BGE-M3 + 2 sqlite store). Config opt-in `~/.config/metnos/github_watched_repos.json`. Callbacks `github_analyze`+`github_send_reply` in `runtime/orchestration.py`.
- **consult_frontier system verb** (ADR 0142): `executors/consult_frontier/` modo A single-call + modo B agentic tool use (5 tool read-only). Tier config `~/.config/metnos/llm_tiers.toml`. Cache disk TTL + cost estimator + fallback chain.
- **Install-on-demand pattern §7.3** (ADR 0143 TODO): `runtime/system_binaries.py` whitelist auto-derive. Errore `binary_missing+suggested_install` → runtime auto-injecta admin step. Sudoers NOPASSWD `apt-get install -y *`. `verb_unique/admin.py` whitelist guard.
- **PLANNER split call grammar GBNF opt-in** (ADR 0151, 19/5/2026): `runtime/planner_split.py::chat_with_tools_split` 2-call. Opt-in `METNOS_PLANNER_SPLIT=1` default OFF. Bench ampio 25q×2run mostra 1.72× speedup (non 14-22× smoke). Update v4: selector prompt history-aware (no tool repeat step≥2), soft-gate verb-canonical (`agent_runtime._check_top_k_affinity_jaccard` + `prefilter._VERB_TO_CANONICAL`).
- **i18n pipeline strutturale** (ADR 0152, 19/5/2026 v4): (a) `i18n.keys_for_synth_context()` subset 65 chiavi nel synt stage 5 prompt; (b) `_google_api_runner._i18n_error_for_class` 8 error_class→ERR_*; (c) `synt_multistage._register_synth_keys` post-stage5 + daemon `_materialize_auto_synth_stubs` LLM+`auto_translated` flag; (d) 5 backend `runtime/backends/{files,messages}/*.py` migrati (~115 stringhe→i18n). Schema migration `auto_translated INT DEFAULT 0`.
- **delete_files executor** (19/5/2026 v4): `executors/delete_files/` + `backends/files/local.py::delete_files` reversible `restore_blob_backup`. Riempie gap catalog (PLANNER sceglieva erroneamente delete_dirs per file).

## 11. Decisioni di runtime

- **LLM tier**: 4 tier (fast/middle/wise/frontier). **SoT canonica**: `runtime/llm_router.py::DEFAULT_TIERS` + ADR 0146 (consolidamento 18/5/2026). I tre tier locali (fast/middle/wise) puntano tutti allo stesso `llama-server :8080` (Gemma 4 26B + drafter E2B speculative); la differenza fra tier sono i parametri per-call (`think`, `num_predict`). frontier = Anthropic Opus 4.7 opt-in. Niente piu' `qwen3:8b` (ADR 0044 superseded da 0106+0146).
- **Tool-use protocol**: nativo Ollama+Qwen+Gemma (tool_calls strutturati). NIENTE parser JSON fragile.
- **Data piping**: `from_step: int` (schema-guided) + `{{stepN.field}}` per scalari.
- **Intent extractor**: LLM-based gemma 4 26B middle, ~370ms/query, 100/100 su test corpus. Fallback bag-of-words. Bypass deterministico per undo.
- **Universal helpers**: `classify_entries`, `filter_entries`, `undo_last_turn` sempre. `describe_entries` SOLO se intent.verb NOT in action_verbs.
- **Reverse patterns**: `runtime/reverse_patterns.py` — 5 entry deterministiche (vedi §2.3).
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

- ADR registry: `decisions/` (`0001-0148`, `0055`/`0115`/`0116`/`0121` skipped) — dettagli implementativi e razionale.
- Architettura canonica: `docs/it/architecture/` (+ EN bridge simmetrico).
- Memorie persistenti: `~/.claude/projects/-opt-myclaw/memory/MEMORY.md` (path Claude harness, indipendente dal rename Metnos).
- Repertorio prompt: `runtime/prompts/<lang>/*.j2` (ADR 0092).
</content>
</invoke>