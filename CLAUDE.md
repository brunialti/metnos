# Metnos — Linee guida di progetto

> **OBBLIGO**: leggere integralmente all'inizio di ogni sessione. Codifica decisioni architetturali, convenzioni di codice e norme di processo gia' stabilite. Quando un punto e' obsoleto o errato, AGGIORNALO subito invece di lavorarci attorno.
>
> Mantenuto da: agente. Aggiornamento ad ogni sessione che fissa una nuova norma duratura.
> Ultimo aggiornamento: 2026-05-15 (rename `cancel_scheduled_task`→`delete_tasks_scheduled` §2.2 verb canonico, sezione planner scheduled_tasks opt-in, handler fallback id→name, fix calendar_id alias / attendees email / persons name resolution / vaglio REDACTED placeholder / find_images_indices resilient fallback / terminal-shortcircuit pattern. Aperto: regressione task auto extra-step describe da indagare).
> Ultimo aggiornamento precedente: 2026-05-14 sera (ADR 0133 constrained GBNF tool_call: discriminated union + B2 recursive + pool filter, 50%→100% conv, 70s→41s).
> Storia precedente: `git log /opt/myclaw/CLAUDE.md`.

---

## 1. Cos'e' Metnos

Assistente personale self-hosted (su `.33`, Strix Halo 96GB unified). Microarchitettura a executor sintetizzati al volo via synt multistage; runtime ReAct con planner LLM (Gemma 4 26B middle/wise locale + Sonnet/GPT-5 frontier come fallback). Canali: **Telegram** + **HTTP porta 8770** (htmx + Jinja2 + uPlot, ADR 0078). Pipeline immagini in-process: SigLIP-base + RetinaFace+ArcFace + EXIF (ADR 0086/0117). Lingua principale: italiano; corpus doc bilingue IT+EN. Etimologia: `mētis + noûs`. Process name: `myclaw`. Dominio: `metnos.com`.

ADR registry canonico: `/opt/myclaw/decisions/` (`0001-0133`, `0055`/`0115`/`0116`/`0121` skipped — fonte unica per "perche' abbiamo scelto cosi'").

## 2. Principi cardine (mai negoziabili)

### 2.1 Executor vettoriali per costruzione
Input come **lista** (paths, entries, urls, ids). Output **sempre lista**, anche degenere (N=0/1/molti). Niente `*_batch`: la versione batch *e'* l'executor. Iterazione interna (loop, paginazione, window come `time_window: today|last-Nd`); branching su osservazione torna al planner. Cap inferiore = 0; cap superiore = parametro esplicito (`max_total`, `max_results`, `max_bytes`).

### 2.2 Naming convention compositiva
Struttura: `azione_oggetto[_qualifier]`. Vocabolario CHIUSO (escalation a Roberto per nuovi termini), centralizzato in `runtime/vocab.py`.

- **23 azioni**: `read, write, move, delete, create, find, list, filter, sort, group, classify, get, set, send, describe, render, extract, compress, compute, compare, change, order, share`.
- **Ortogonalita' dei 5 verbi-produttori** (asse «com'e' fatto l'input primario»): `find` = pattern/query (discovery, sussume verifica esistenza); `get` = id noti o nessun arg (lookup/snapshot); `read` = id di sorgente, ritorna contenuto; `list` = container, enumera senza contenuto; `filter` = lista preesistente, riduce per predicato. Discrimine find vs get: pattern → `find`; id/stato → `get`. `change` = forma/parametri (resize/convert/rotate); `order` = ordinamento PERSISTENTE del corpus, distinto da `sort` in memoria del turno. `share` = OUTBOUND CONSENT (ADR 0128, 12/5/2026): grant access a un'entita' senza spostarla o duplicarla — crea un permission/ACL grant remoto. Distinto da `send` (outbound copy/notification: il destinatario riceve un OGGETTO) e da `set` (upsert idempotente di stato/labels interni al record).
- **Importer verb boundary** (ADR 0128): il vocab si applica integralmente anche agli executor importati via skill agentskills.io (ADR 0123). Provider-verb diversi mappano a Metnos-verbi diversi per `(target_kind, side_effect)`: `gmail get MSG_ID` (fetch content) → `read_messages`, NON `get_messages`. `docs append` (body modify) → `write_files_text`, NON `change_files_text`. `gmail modify` (state labels) → `set_messages`, NON `change_messages`. `drive share` (acl grant) → `share_files`, NON `set_files`. La tabella contestuale e' in `runtime/skill_vocab_map.json::contextual` (lookup `<domain>:<action>` → `{target_kind, side_effect, verb}`). Verifier deterministico `runtime/importer_verb_verify.py::check_plan` (§7.9, layer 6.bis di ADR 0114).
- **Confini stretti**: `fetch` rimosso (HTTP GET = `get_urls`); `extract` solo decompressione archivi (zip/tar/gz); «estrai righe da testo» = `filter_texts_lines`; «estrai testo da PDF/HTML» = `read_files_pdf/html`; «estrai campi da entries» = `get`.
- **17 oggetti** (plurale): `files, dirs, packages, messages, events, contacts, places, processes, urls, numbers, images, signatures, texts, proposals, inputs, credentials, entries`. Eccezioni: `get_inputs` ritorna `{values:{var:value}}` (UI dichiarativa, ADR 0090); `find_credentials`/`set_credentials`/`delete_credentials` espongono SOLO metadata (binding, fingerprint, scopes, age, status) — i valori cleartext non tornano mai al PLANNER (ADR 0123); `entries` (12/5/2026) e' meta-oggetto per pipeline in-memory dello stesso turno (compute_entries/sort_entries/filter_entries/group_entries) — NON una risorsa esterna, niente `find_entries`/`read_entries`/`get_entries`.
- **System verbs riservati**: `undo`, `admin` sono verbi-meta di sistema, fuori dai 22 verbi canonici §2.2. Riservati a builtin runtime (`undo_last_turn`, `admin`); NON utilizzabili da synt o user-domain executor (il vaglio rifiuta `name` che inizia con uno di questi verbi). Stage 1 NAMING non li propone come azione del nuovo executor: discriminano la chiusura del turno (`undo_last_turn`) o l'esecuzione di shell privilegiata (`admin`), non producono entita' del dominio utente.
- **Qualifier opzionali, 3 famiglie**:
  - **formato/codifica**: `_csv, _xlsx, _ocr, _zip, _pdf, _xml, _html, _json, _text, _gz, _tar, _video, _audio, _image, _hash`.
  - **modalita'**: operazione (`_size, _format, _similar, _loc, _empty`); granularita' (`_lines, _paragraphs, _sentences, _pages, _segments`); mezzo astratto persistente (`_indices` per CLIP/ArcFace/EXIF/perceptual hash/threading — ECCEZIONE: `find_<dom>_indices` ritorna entries del dominio, non un oggetto `indices`). `_empty` (ADR 0127, 12/5/2026): stato "vuoto/sotto-soglia" del dominio, cross-domain (events gap, files size<=th, messages body<=th, dirs vuote) — arg canonical `size` (str unit-aware).
  - **safety policy** (oggetto `signatures`, ADR 0071): `_blacklist, _whitelist, _graylist, _forbidden, _seed, _diff, _sanity, _command, _reversibility, _promotion, _candidates`.

Stage 1 di synt usa MAPPING bilingue IT+EN. Sinonimi prima dell'estensione del vocabolario.

### 2.3 Reverse pattern catalogo deterministico
Manifest dichiara `reverse_pattern` da catalogo chiuso: `swap_src_dst` (move/rename), `delete_created_dirs`, `delete_created_paths`, `restore_blob_backup` (richiede `blob_path`+`blob_sha256` nei results), `delete_<object>_by_id` (ADR 0123, contratto: `<object>_ids` + scope id, runtime costruisce `delete_<object>(ids=..., scope=...)`). Blob: `$METNOS_HISTORY_DIR/<METNOS_TURN_ID>/blob/<sha256>.bin`.

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
Edit di `<executor>.py` cambia il digest sha256 del codice ma NON il `manifest.toml`. Al boot/reload, `runtime/loader.py::verify_executor` scarta silenziosamente l'executor se `declared digest != actual digest`. Workflow OBBLIGATORIO dopo ogni edit di un `.py` di executor: `python /opt/myclaw/runtime/sign.py sign /opt/myclaw/executors/<name>` + restart `metnos-http.service`.

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

- **Smoke battery** (`runtime/smoke.py`): 8 query "must work" + invariants. OBBLIGATORIA prima di `./deploy.sh`, dopo synth-on-the-fly, in cron daily, quando si tocca `prefilter.py`/`agent_runtime.py`/`synt_multistage.py`/`loader.py`. Routing assertion `expected_first_tool` integrata (ADR 0114 L5).
- **Catalog invariants al load** (`runtime/loader.py`): rifiuta synth in `~/.local/share/metnos/executors/` con name collision verso handcrafted in `/opt/myclaw/executors/`.
- **Prefilter precursor universale**: `rank_with_intent` inietta automaticamente UN precursor per ogni verbo NON producer (`read, find, list, get`).
- **No synth ridondanti**: stage 1 NAMING preferisce il name canonico esistente quando l'intent e' coperto.
- **Prefilter primary tools per object** (ADR 0075): `_OBJECT_PRIMARY_TOOLS` in `prefilter.py` inietta nel top-K il tool canonico per ogni object. Mantenere allineato ai 16 OBJECTS di `vocab.py`.
- **Adaptive re-rank intra-turno** (ADR 0072): `runtime/adaptive_rerank.py` add-only, cap `2×k_max`.
- **Scheduler gate user-activity** (ADR 0074): task notturni age-based si sospendono se ultima interazione utente precede ultima esecuzione del task. Sorgente: `~/.local/share/metnos/turns/*.jsonl`.
- **Synth_request short-circuit** (ADR 0076): `handle_synth_request` esegue 2 short-circuit deterministici prima della cascata: `already_in_catalog` + redirect ad alias canonico stesso-object.
- **Introvertiva quality filters** (ADR 0077): `candidates_specialize` 6 filtri deterministici (validator naming, skip flow args/template/booleani, soglia `uses_min` default 10).
- **GC synth rifiutati** (ADR 0079): `runtime/loader.py::_gc_collisions` sposta i synth `rejected` in `/tmp/metnos_synth_gc_<ts>/<name>/`. Solo path dentro `SYNTHESIZED_EXECUTORS_DIR`.
- **PROJECT PATHS noti nel PLANNER** (ADR 0079): `runtime/project_paths.json` mappa progetti → `code_root`/`user_data_root`/`memory_root`. Espansione = entry JSON, niente codice.
- **Pipeline web/news/scuola** (ADR 0081+0082+0098+0101+0105+0108): `find_urls`/`read_urls_html`/`read_urls_pdf`/`login_session`. Tier resolution `~/.config/metnos/{owned_domains,trusted_origins}.json`. UA `metnos-crawler/1.x`. Discovery sitemap > RSS > BFS. Topic ranking BM25. Default tier T2; auto-degrade T2→T1 (host_health, TTL 24h). HTTP cache disk-based 900s. `read_urls_html` cattura `iframe_urls`/`linked_documents`, error_class on soft-fail.
- **Indici di dominio** (ADR 0086, SUPERSEDED-BY 0117 per image): pattern `create_<dom>_indices`/`find_<dom>_indices`/`get`/`delete`. Backend in-process. Storage `~/.local/share/metnos/index/<dom>/<sha8>/<idx>/`. Refresh incrementale di default; `force=true` per rebuild.
- **CIFS/SMB mount via admin → sudoer** (ADR 0087): canonicalize `mount`/`umount` (`runtime/safety/canonicalize.py`), seed v2 graylist, helper `runtime/cifs_helper.py` con temp file 0600 cifrato. Glue `verb_unique/sudoer.py`: placeholder `${METNOS_CIFS_CREDS}`. Niente password in argv.
- **Admin esposto al PLANNER** (ADR 0088): `admin` con `EXPOSE_TO_PLANNER=True`, vaglio always-on. Sudoer invisibile. Prefilter inietta admin score 15 su `_SHELL_INTENT_HINTS`. HMAC consent token TTL 600s in `~/.local/share/metnos/.admin_consent_key`.
- **Credenziali UX 3 strati** (ADR 0089+0091): Strato 1 `extract_credentials` regex IT+EN + redact; Strato 2 admin `decision="needs_inputs"` + payload `get_inputs` con `on_complete=save_credentials_and_resume` (orchestratore `runtime/orchestration.py::orchestrate_needs_inputs`); Strato 3 CLI `metnos-cli credentials add|list|remove|fingerprint`. Aggiungere binding = entry in `_BINDING_STRONG/_WEAK` + helper `<kind>_helper.py`.
- **Engine UI dichiarativo** (ADR 0090): `get_inputs(title, dialog=[...], fmt=auto|dialogue|form|voice|telegram_inline)`. 9 kind. Storage `runtime/dialog_pending.py`. Adapters Telegram + HTTP `/agent/dialog/<id>/{form,submit,cancel}`. Soglia form HTTP 2 step; Telegram inline keyboard se TUTTI gli step sono in `_INLINE_COMPATIBLE_KINDS = {yes_no, choice}`.
- **Prompt-as-data + multilingua** (ADR 0092): `runtime/prompts/<lang>/<role>.j2` (MiniJinja). `prompt_loader.get(role, lang, **vars)` con `lang` esplicito al call-site. Invariante "ogni sub-dir lingua secondaria stesso set di `it/`" enforced al boot da `validate_invariant()`. CLI `metnos-prompts`. Tre layer: prompt LLM + manifest `[description].<lang>` + `~/.local/share/metnos/i18n.sqlite` (chiavi `MSG_*`, latest-wins). Norma: TUTTI i report runtime user-facing usano `config.DEFAULT_LANG` via `i18n.sqlite`.
- **Async indexing build** (ADR 0093): systemd user transient unit `metnos-build-<sha8>-<idx>`. Atomic write tmp+rename. Resume da checkpoint ogni N=500 + SIGTERM handler. Notification via marker `/tmp/metnos_build_complete/`. Threshold sync vs async: stima >120s → orchestrator. Disable test `METNOS_HTTP_DISABLE_BUILD_TASKS=1`.
- **Fast path deterministico** (ADR 0094): `runtime/fast_path.py` short-circuit query triviali 1:1 PRIMA del PLANNER. Tabella chiusa `_FAST_PATTERNS` lookup O(1). ZERO LLM. Skip se `reference_images`. Estendere SOLO con mapping 1:1.
- **Output formatter deterministico** (ADR 0095): `runtime/output_format.py` channel-agnostic markdown. KV singoli `**label**: value`; gruppi correlati come bullets o KV multipli; lista→tabella quando record omogenei a >=3 attributi; cap-expand in blocco proprio separato da HR. NIENTE LLM nel formatter.
- **Proposals cleanup** (ADR 0096): `runtime/proposals_cleanup.py` orchestra 4 op deterministiche (archive_aged_synth_proposals 30gg + dedupe_introvertiva_candidates + keep_latest_n_per_kind n=3 + auto_decay_legacy_orphan_mnests). NIENTE delete (move + UPDATE). Task scheduler `proposals_cleanup` daily@06:00.
- **Lifecycle summary** (ADR 0097): `runtime/lifecycle_summary.py` aggregatore READ-ONLY dei 4 ager. Task `lifecycle_summary` daily@06:30. ZERO accoppiamento dei moduli ager.
- **Web crawl parallel + strategy** (ADR 0098): `_HostThrottle` thread-safe (Semaphore per-host + rate-limit lock). BFS via `ThreadPoolExecutor` cap globale `min(64, cpu*4)`, per-host T1=2/T2=8/T3=16. PLANNER hint URL esplicito + dati DENTRO la pagina → read_urls_html primo step. Auto_final prefer read over discovery.
- **Runtime perf** (ADR 0099): seed-step URL injection (`runtime/fast_path.py::try_seed_step`) + catalog cache (`runtime/loader.py::_CATALOG_CACHE`, `invalidate_catalog_cache()` per i test) + reasoning budget dinamico PLANNER (step 1 = 768, step 2+ = 256, solo `provider.name=="llamacpp"`).
- **Executor perf parallelizzazione** (ADR 0100+0103): `read_urls_html`/`read_urls_pdf`/`compute_files_loc` con `HostThrottle` per-host + `ThreadPoolExecutor` globale. Throttle rilasciato post-fetch. Helper centralizzato `runtime/host_throttle.py`. Pattern §7.4: prima di parallelizzare, misurare; se non c'e' win, NON applicare.
- **Crawler error_class + soft-fail** (ADR 0101): `read_urls_html._fetch_one` ritorna `(None, {"error","error_class"})`. Mappatura: 403→forbidden, 429→rate_limited, 404→not_found, 5xx→server_error, timeout/network/non_html/js_rendered/unknown. PLANNER: URL fallito in history → final_answer onesto + alternative; partial fail → usa entries successful + dichiara falliti per classe. Accept-Encoding gzip/deflate + decompressione.
- **Thinking-leak scrubber** (ADR 0102): `_scrub_thinking_leak` in `runtime/agent_runtime.py` accanto a `_scrub_credentials`. Regex line-by-line trigger EN (Wait/Actually/Let me/...) + IT chirurgico. Preserva substring legittime. Idempotente. Invocato in `TurnLog.write()` ramo `final_kind=="answer"` PRIMA di tutti i prepend.
- **Report runtime user-facing i18n** (ADR 0104): chiavi `MSG_*` in `i18n.sqlite` IT+EN. `agent_runtime` (auto-final/footer/undo/hallucination), `lifecycle_summary`, `output_format::format_tldr`, `orchestration` (health block + cap-expand resume + entries/documents block), `find_images_indices` via `from messages import get as msg`. Determinismo §7.9.
- **HTTP cache disk-based** (ADR 0105): `runtime/http_cache.py` storage `~/.cache/metnos/http/<sha[:2]>/<sha>.json` sharded. TTL default 900s, env `METNOS_HTTP_CACHE_TTL_S` o `cache_ttl_s` per-call (0 disabilita). Atomic write tmp+rename. Cleanup weekly 7d.
- **Vaglio safe-verb shortcut** (ADR 0107): `vocab.SAFE_VERBS` 11 verbi read-only/pure-compute. In `vaglio.judge` dopo guardia: action in SAFE_VERBS → `Verdict(judge_kind="safe-verb-shortcut", score=1.0)`. Guardia esegue PRIMA: `read_files` su `~/.ssh/id_rsa` resta bloccato.
- **Auto-degrade T2→T1 host_health** (ADR 0108): `runtime/host_health.py` tracker per-host 429/503 sliding-window 60min. Soglia 3 → host a `~/.config/metnos/blocked_origins.json` con TTL 24h. `is_blocked()` cleanup lazy. Storage volatile `~/.local/share/metnos/host_health.json`.
- **Channel-aware HTML rendering** (ADR 0109+0110): `runtime/html_sanitizer.py::to_safe_html` (subset Telegram) + `to_safe_html_full` (HTTP browser: + h1..h6/ul/ol/li/blockquote/hr/table/p/br). Escape iniziale + whitelist chiusa. `http_routes_agent::_safe_final_html` dispatcha; Telegram `chunk_html` preserva attributi `<a href>`. `_flush_para` joina righe consecutive con `<br>` (GFM soft break): markdown verso `to_safe_html_full` usa `\n` per line break, `\n\n` per paragrafo distinto.
- **Sync `pairings.db` → `users.user_channels` al boot** (ADR 0083 multi-user): helper `runtime/users_pairings_sync.py::sync_pairings_to_user_channels()` deterministico (§7.9). Hook al boot di `metnos_http_server.make_app` e `ChannelDaemon.__init__`. Idempotente (touch invece di re-link). Estensione UI: colonna `users.email TEXT NULL` (migration `PRAGMA table_info`), `update_user(...)` con sentinel `...`, endpoint `POST /admin/users/<id>/update`.
- **Descrizione human-readable proposte introvertiva**: `runtime/http_routes_admin.py::_describe_proposal(kind, sig_key)` deterministico (§7.9). Parsa `sig_key` (shape ADR 0077: `["dedupe", reason, a, b]` / `["generalize", [...]]` / `["specialize", exec, arg, val_json]`) via 6 chiavi i18n template (`MSG_PROP_*`). Render al volo nel handler `/admin/proposals`, mai persisted.
- **Synth admission policy 4 layers** (ADR 0114): L2 affinity overlap guard (`loader.py::_check_affinity_overlap`, Jaccard >=0.5 vs handcrafted o synth piu' vecchio → rejected); L3 efficacy ager (`runtime/executor_aging.py::apply_efficacy_ager`, handcrafted MAI demoted); L5 smoke battery con `expected_first_tool`; L6 LLM semantic verifier stage 6 (`runtime/synt_stage6_verify.py`, tier wise, fail-safe, disable via `METNOS_SYNT_STAGE6_DISABLED=1`). L1 vocab semantic gate deferred.
- **Named persons registry + composition filters + procedural index schema** (ADR 0113): registro nominale `~/.local/share/metnos/persons.sqlite` (slug case+accent-insensitive). 4 executor `set_persons`/`get_persons`/`delete_persons`/`find_persons_indices` con `name=` o `reference_images=`; ambiguity → dialog `kind="choice_with_preview"`. `min_face_pixels` per filtro composizione. `paths_filter` per pipeline compositive («Matteo al mare»). Schema procedurale `runtime/index_schema.py` con registry `IDX_TYPES` + 9 enrichments. `find_urls` topic = rank (non filter), arg esplicito `min_score`.
- **Scheduler v2 asyncio co-host** (ADR 0112): `runtime/scheduler_v2/` asyncio-native, single Task nel loop di `metnos-http.service`. Single-table `schedule_entries` (recurring + one-shot); `next_fire_at` materializzato, loop dorme fino a `MIN(next_fire_at)` cap 60s. Trigger grammar: `daily@HH:MM` (TZ locale DST `fold=0`), `every_Ns/m/h`, `at:<ISO>`, `cron:<5-field>`. IPC zero (clienti via `scheduler_v2.client`). Migration `migrate_v1.py` idempotente al boot. Callback registry `builtin_callbacks.py::install_default_callbacks`.
- **PLANNER skip describe_entries dopo health** (ADR 0111): 4 difese post `get_processes(include_health=true)`. L1 prompt HEALTH BLOCK in planner.j2. L2 `agent_runtime` inietta `health_context`; describe_entries pre-pend "STATO SERVER GIA' RIASSUNTO". L3 runtime auto-final §7.9 executor-step time. L4 safety net write-time `_prepend_health_block_if_any`: query NON contiene `_HEALTH_IMPERATIVE_KEYWORDS` → `final_message=""` PRIMA del prepend.
- **Skill importer agentskills.io → executor** (ADR 0123): pipeline 5-stadi (parser + translator + codegen Jinja + LLM stage 4 description + admission 5 layer). CLI `metnos-skills import|list|uninstall|status|evaluate`. Tabella `runtime/skill_vocab_map.json` mappa action+domain → azione_oggetto §2.2. Helper condivisi `runtime/skill_wrapper.py` (regola del 3 §7.2). `[provenance]` sez. manifest (`imported_from`/`source_version`/`source_sha256`/`imported_at`). Capability invariante `metnos:credentials_metadata_only` su credentials. Helper `runtime/time_window_parser.py` (canonical §2.1 + estensioni IT/EN + ISO + range italiano, TZ Europe/Rome). Smoke import-aware Opzione B: `runtime/smoke_imports.py` separato con `BATTERY_IMPORTS` concatenato da `smoke.py`.
- **Proposal auto-evaluator + ETA instrumentation** (ADR 0122): `runtime/path_shape.py` + `runtime/proposals_eta_index.py` + scheduler v2 task `proposals_eta_aggregate` daily@04:30. `synth_request.handle_synth_request(..., current_steps=None)` enrichment ETA + count_60d. `runtime/proposal_evaluator.py` 6 killer + 7 signal weighted score → verdict accept|gray|reject. CLI `python -m admin.proposals_cli evaluate`. HTTP `/admin/synth-proposals/{id}/evaluate`. Estende ADR 0114 con 5° gate preventivo a evaluator-time.
- **Unified image enrichment index** (ADR 0117, supersedes 0086 per image): UN solo asse `unified/` per corpus invece di 3 idx scene/persons/gps. Schema v4 in `runtime/index_schema.py`. Storage `~/.local/share/metnos/index/image/<sha8>/unified/{entries.jsonl, embeddings_text.npy, embeddings_face.npy, meta.json}`. Pipeline per-foto: EXIF + sha256 + ArcFace (RetinaFace+buffalo_l) + VLM Qwen2-VL-7B su :8081 + BGE-M3 text embedding. Tutti modelli locali §10.3. `find_images_indices` single-axis (drop `idx=`); `find_persons_indices` thin alias compositivo. Migration `runtime/index_schema_upgrade_v4.py` + standalone `runtime/build_runner_unified.py` via `systemd-run --user`.
- **Prompt architecture A+B+C** (10.6.45, ADR in scrittura, 11/5/2026): hook anti-drift IT/EN `scripts/pre-commit-symmetry-it-en.sh`; daemon scheduler v2 `i18n_translate_pending` daily@02:00 in `runtime/jobs/i18n_translate_pending.py` (cap N=20/fire, idempotenza `source_hash`, audit JSONL). Consolidamento Z.N → 7 nomi semantici in `planner.j2` IT+EN; frontmatter 8 campi `{# --- ... --- #}` su tutti i `.j2`. Split `planner.j2` in `_core` + 6 sezioni (mail/calendar/web/photos/system/admin_shell) + `_footer`; `prompt_loader.compose(role, lang, sections)` LRU 3-layer; `_OBJECT_TO_SECTIONS` 16 entries vocab.py; re-render 2-pass post-route_info in `agent_runtime`. Linter `runtime/prompts_lint.py` 5 check (frontmatter/hedge/LOC/trailing/symmetry) + CLI `metnos-prompts lint --strict` + smoke integration. Legacy `planner.j2` eliminato §7.1. §6.1 nuovo (tipizzazione prompt).
- **Pattern intent-implicit** (ADR 0129, 14/5/2026): `vocab.detect_implicit_actions(query)` deterministico §7.9 emette `implicit_actions=[{verb,object,strategy,confidence,...}]` quando una query multi-azione ha sostantivi orfani (N_objects > N_mutating). `_OBJECT_SYNONYMS_IT/EN` + `OBJECT_DEFAULT_MUTATING_VERB` + `_BIGRAM_VERB_HINTS["send"]` (cattura «email me/mandami una email»). Wiring: `intent_extractor.extract_intent` arricchisce intent; `agent_runtime` inietta `implicit_actions` in dialog_pending `on_complete`; `orchestration._orchestrate_implicit_actions` (lookup `_ACTION_TEMPLATES[(verb, object)]`) esegue deterministicamente post-dialog + send_messages notify se hint presente; guardia `propose_intent_no_write` skip in continuation post-dialog (history contiene `get_inputs` completed). `_handle_needs_inputs` propaga `decision="needs_inputs"` (OAuth flow) generale §7.3 per qualsiasi backend.
- **Backend tree allineato agli OBJECTS §2.2** (ADR 0130, 14/5/2026): `runtime/backends/<OBJECT_PLURALE>/<provider>.py`. Rinomine: `calendar→events`, `messaging→messages`, `web→urls`. Provider builtin: `events/{local_ics,google_workspace}.py`, `messages/{email_metnos,telegram_bot,gmail_google_workspace}.py`, `files/{local,google_workspace}.py` (folders come `create_dirs/find_dirs/delete_dirs` nello stesso modulo), `urls/{httpx_default,playwright_stub}.py`. Auto-default `_default_client()` in ogni dispatcher: filesystem check su `~/.local/share/metnos/skills/google-workspace/google_token.json` → `google_workspace`, fallback `local`/`metnos`. Retry §7.9 3× su transient (network/server_error/rate_limited/SSL ASN1/TLS handshake) nei wrapper `_run_calendar/_run_gmail/_run_drive`. `send_messages` timeout_s=90 (assorbe retry SMTP).
- **Credenziali single store** (ADR 0131, 14/5/2026): `runtime/credentials.py` Fernet+HKDF unica fonte segreti SMTP/IMAP. Domain `smtp_<account>` (5/5 account migrati). Fallback `.env` Layer 2 back-compat. CLI `python3 -m credentials_migrate`.
- **Plugin esterni per backend** (ADR 0132, 14/5/2026, scaffolding): `~/.local/share/metnos/plugins/<name>/{plugin.toml, <object>.py}`. `runtime/plugin_loader.py::load_plugins(object)` scan §7.9 cache. Precedenza builtin>plugin. Override env `METNOS_PLUGINS_ROOT`. Wiring deferred.
- **Constrained generation tool_call** (ADR 0133, 14-15/5/2026): `runtime/tool_grammar.py` genera GBNF deterministico per ogni step PLANNER vincolando `{"name", "arguments"}` ad args_schema del tool. **Discriminated union** `pairTool` lega name↔args (no mix-match). **B2 recursive** sub-rule `{tool}ObjD{d}I{i}` camelCase per nested object/array-of-object (cap depth=4). Workaround bug `b540-5755a100c`: rule names camelCase (underscore silently ignored), dependency closure (unused rules interfere). Provider wiring `LlamaCppProvider.chat_with_tools(grammar=)`: bypass tools, history flat, parser tolerant. **Pool filter** `filter_pool_for_grammar` esclude escape-hatch senza marker semantico (`request_new_executor` ≥3 canonical, `request_location_from_user` no proximity, `undo_last_turn` no undo, `*_<provider>` no provider) con word-boundary regex. **Validator** top-level required-only. **Strategia E loop-detect** `runtime/loop_detect.py::is_repeated_failure(threshold=2)` safety net. Opt-in `METNOS_GRAMMAR=1`. Bench 50%→100% convergenza, 70s→41s latency. 61 unit test.
- **Builtin tool naming canonical §2.2** (ADR 0133 extension, 15/5/2026): rinominati i builtin runtime con verbo canonico per coerenza con i 22 verbi (no `cancel`/`schedule` etc.). Es. `cancel_scheduled_task` → `delete_tasks_scheduled` (regressione: LLM matchava `delete_events` perche' `cancel` non in §2.2). Sezione planner opt-in `scheduled_tasks.yaml` (gated via `_OPT_IN_SECTIONS` in `prompt_loader.compose` per evitare bloat prompt default). Handler con fallback `id → name` se LLM emette id inventato.

## 11. Decisioni di runtime

- **LLM tier**: 3 puntatori (fast/middle/wise). Default v1.1: `qwen3:8b` think=false num_predict=400 (procedural), Gemma 4 26B (middle/wise per synt + planner).
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

- ADR registry: `/opt/myclaw/decisions/` (`0001-0133`, `0055`/`0115`/`0116`/`0121` skipped) — dettagli implementativi e razionale.
- Architettura canonica: `/opt/myclaw/docs/it/architecture/` (+ EN bridge simmetrico).
- Memorie persistenti: `~/.claude/projects/-opt-myclaw/memory/MEMORY.md`.
- Repertorio prompt: `/opt/myclaw/runtime/prompts/<lang>/*.j2` (ADR 0092).
</content>
</invoke>