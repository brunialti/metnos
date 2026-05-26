# Metnos — Linee guida di progetto

> **OBBLIGO**: leggere integralmente all'inizio di ogni sessione. Codifica decisioni architetturali, convenzioni di codice e norme di processo gia' stabilite. Quando un punto e' obsoleto o errato, AGGIORNALO subito invece di lavorarci attorno.
>
> Mantenuto da: agente. Aggiornamento ad ogni sessione che fissa una nuova norma duratura.
> Ultimo aggiornamento: 2026-05-26 v13 (ADR 0162: ClusterLLM + Pronoia classify_fail + retry on repeat — bucket emergente embedding-based, granularita' feedback ✓/↻/✗). Light compact §10.6 26/5: entry ADR 0157/0158/0159/0160/0161/0162 ridotte a forma sintetica (file paths + costanti + env). Dettagli espansi negli ADR.
> Norme recenti: dettagli in ADR 0150/0151/0152/0154/0155/0156/0157/0158/0159/0160/0161/0162 e §10.6. Storia in `git log CLAUDE.md`.

---

## 1. Cos'e' Metnos

Assistente personale self-hosted (su `.33`, Strix Halo 96GB unified). Microarchitettura a executor sintetizzati al volo via synt multistage; runtime ReAct con planner LLM (Gemma 4 26B middle/wise locale + Sonnet/GPT-5 frontier come fallback). Canali: **Telegram** + **HTTP porta 8770** (htmx + Jinja2 + uPlot, ADR 0078). Pipeline immagini in-process: SigLIP-base + RetinaFace+ArcFace + EXIF (ADR 0086/0117). Lingua principale: italiano; corpus doc bilingue IT+EN. Etimologia: `mētis + noûs`. Process name: `myclaw`. Dominio: `metnos.com`.

ADR registry canonico: `decisions/` (relative alla repo root; `0001-0161`, `0055`/`0115`/`0116`/`0121` skipped — fonte unica per "perche' abbiamo scelto cosi'").

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
- **4° livello descriptor** (ADR 0156, 21/5/2026 v3): schema POSIZIONALE `verb_object[_qualifier[_descriptor]]`, separatore unico `_`. Descriptor kebab-case interno `[a-z0-9]+(-[a-z0-9]+)*` max 30 char. **Regole d'oro**: (R1) il 4° livello ESTENDE, non RIMPIAZZA il 3° — descriptor solo se qualifier presente; (R2) **un livello alla volta**: una proposta non puo' introdurre 3°+4° nuovi insieme (`validate_name(name, live_canonicals=catalog_3level)` rifiuta 4-livello se canonical 3° non in catalog); (R3) descriptor = MODIFICATORE COMPORTAMENTALE a parita' args — OK `_dry-run`/`_per-language`/`_v2`/`_unified`; ERRORE `_nightly` (timing→`tasks`), `_invoice-lifecycle` (dominio→`proposed_action`). Enforce R1+R2 via `naming_grammar.validate_name` + GBNF v3 (`canonical-4-with-descriptor` enum filtrato a 3-level live). R3 e' euristica prompt-side (vaglio judge a valle).
- **Governance estensione vocab §2.2** (ADR 0156): proporre un nuovo token (verbo, oggetto o qualifier) richiede 3 criteri congiunti: (1) **necessario** — nessun token della stessa classe e' semanticamente equivalente al proposto (solo lessicalmente diverso); (2) **generale** — cattura una semantica riusabile e compositiva (no narrow domain-specific); (3) **comprensibile** — un LLM medium (Gemma 26B) comprende il significato senza spiegazione ulteriore. Le proposte introspettive (telos lenses, introvertiva) che richiedono estensione vocab DEVONO scrivere "RICHIEDE estensione vocab §2.2: <criterio_1>, <criterio_2>, <criterio_3>" nel rationale, oggetto di review umana al digest.

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
- **FOTO/EXIF/GPS** → `get_files` (post-rename ADR §2.2, era `get_files_metadata`).
- **IDENTITÀ/PROFILO/IO** → `read_persons(name="${RUNTIME:actor}")` per "chi sono io"; `read_persons(name=X)` per "dimmi tutto su X"; `read_persons(role="guest")` per lista paired. Distinto da `get_persons` (scheda registro biometrico). Vedi ADR 0163.
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
Tipi: `user`, `feedback`, `project`, `reference`. Indice in `~/.claude/projects/-opt-myclaw/memory/MEMORY.md` (max ~150 char/riga, max ~200 righe). Dettaglio in file separati. **Session log pattern**: ogni sessione multi-step crea `project_session_<DD_M_YYYY>.md` con cose-fatte + cose-da-fare + BG attivi; entry top in MEMORY.md marcata ⏰ con "LEGGI PRIMA"; vecchi session log eliminati a fine sessione.

### 10.6 Meccanismi anti-regressione (indice)

> Una riga per meccanismo. Dettagli: ADR registry `decisions/` (relative). Solo le voci da memorizzare al call-site (file/funzione/policy) restano qui.

- **Pipeline shape FSM** (ADR 0154): `runtime/pipeline_shape.py` invariante `E+ (F|A)?` + pre-execution hook in `agent_runtime` + planner prompt 0-PRE bilingue.
- **Planner choice > runtime override** (ADR 0155): runtime non sovrascrive il planner se non via auto_remediation / vaglio / fast-path pre-planner. Vietato interceptor pattern-match.
- **Naming Authority centralizzata** (ADR 0156, 21/5/2026): `runtime/naming_grammar.py` parsa `vocab.py` ed espone validator deterministico (§7.9) + GBNF generator. Vincola executor_target a catalog vivo (anti-hallucination) e new_op_name a `<verb>_<object>[_<qualifier>][#<kebab-descriptor>]`. Opt-in `METNOS_TELOS_GRAMMAR=1`. Riusabile da: telos lenses (`telos_lenses/*.py`), introvertiva, synt stage 1, skill importer. Bench 21/5: Gemma+GBNF 64% rate-utile, 0% anti-pattern, 100% naming compliance (batte Sonnet 4.6).
- **Alignment Engine — formula v1.3** (ADR 0157, 22/5/2026): `runtime/alignment_engine.py`. `expected_alignment = (α·top + γ·rest)·urgency·confidence - bother_cost`, α=2.0/γ=0.5 (vincolo α>3γ). `compose()` deterministico; `estimate_fit()` LLM judge Gemma 26B (~9s/call). CLI `--backfill` (LLM) o `--recompose` (no-LLM, ricalcola da `alignment_per_telos`). Confidence 0.8, bother_cost 0 in MVP.
- **TELOS.md v1.2 (6 telos)** (ADR 0157, 22/5/2026): rimosso `t.coltivazione_strumenti` (clausola anti-rinuncia di runtime, non un fine ultimo — produceva fit moderato come rumore di fondo). Pesi ridistribuiti: t.tempo 0.25, t.puntualita 0.20, t.protezione 0.20, t.ordine 0.15, t.discrezione 0.10, t.parsimonia 0.10 (somma 1.0). La semantica anti-rinuncia rimane nel runtime come policy deterministica synt_multistage, non pesata.
- **Dashboard `/admin/proposals/telos`** (ADR 0157, 22/5/2026): triage proposte introspettive. `runtime/telos_proposals_store.py` (filtri+enrichment turn log) + handler `http_routes_admin.py` + template `proposals_telos.html` (htmx, accept/reject/stage). Decisioni append-only `~/.local/share/metnos/telos_decisions.jsonl` (LWW per prop_id). Cutoff UI `min_alignment=0.30`.
- **Telos engine fase 4 — TELOS.md iniettato nel system prompt PLANNER** (22/5/2026): `telos_loader.render_planner_block(lang)` produce blocco compatto con telos ordinati per peso desc + quartetto §6 DEVI/NON DEVI/OK/ERRORE. Wire-in `agent_runtime._render_telos_block(lang)` + slot `{% if telos_block %}` in `prompts/{it,en}/planner/_footer.j2`. Degrade graceful se TELOS.md mancante (stringa vuota). Hot-reload mtime cache.
- **Telos engine — 10 lenti laterali** (ADR 0156, 21/5/2026 v8): `runtime/telos_lenses/` pacchetto modulare. 10 lenti: scamper, oulipo, inverse_rl, endgame_book, analogy_transfer, boden_transformational, pattern_language, generative_design, counterfactual, constitutional. `compression` scartata v8 (viola §2.2). Framework `_base.run_lens` + SHARED_PREAMBLE/NAMING_SCHEMA/OUTPUT_FORMAT §6. Env `METNOS_TELOS_LENS_<NAME>=1`. Concept-only in `LENSES_NO_GRAMMAR`. LensCtx.previous_proposals anti-fixation (SCAMPER unici 26%→87%).
- **Smoke battery** (`runtime/smoke.py`, ADR 0114 L5): OBBLIGATORIA prima di `./deploy.sh`, dopo synth, in cron daily, su tocchi a `prefilter.py`/`agent_runtime.py`/`synt_multistage.py`/`loader.py`.
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
- **CIFS/SMB mount via admin → sudoer** (ADR 0087): `runtime/safety/canonicalize.py` + `runtime/cifs_helper.py` + `runtime/system/sudoer.py` placeholder `${METNOS_CIFS_CREDS}`. Niente password in argv. [Naming: `verb_unique/` rinominato `system/` 24/5/2026 ADR 0160.]
- **Admin esposto al PLANNER** (ADR 0088): `admin` con `EXPOSE_TO_PLANNER=True`, vaglio always-on. HMAC consent token TTL 600s in `~/.local/share/metnos/.admin_consent_key`.
- **Credenziali UX 3 strati** (ADR 0089+0091): `extract_credentials` regex + dialog `needs_inputs` con `orchestrate_needs_inputs` + CLI `metnos-cli credentials`. Binding: `_BINDING_STRONG/_WEAK` + helper `<kind>_helper.py`.
- **Credenziali single store** (ADR 0131): `runtime/credentials.py` Fernet+HKDF, domain `smtp_<account>`. Fallback `.env` Layer 2. CLI `python3 -m credentials_migrate`.
- **Engine UI dichiarativo** (ADR 0090): `get_inputs(title, dialog=[...], fmt=...)`. Storage `runtime/dialog_pending.py`. Adapters Telegram + HTTP `/agent/dialog/<id>/{form,submit,cancel}`.
- **Prompt-as-data + multilingua** (ADR 0092): `runtime/prompts/<lang>/<role>.j2`. `prompt_loader.get(role, lang, **vars)`. Invariante "sub-dir lingua secondaria stesso set di `it/`" enforced al boot. CLI `metnos-prompts`. Report user-facing via `i18n.sqlite` chiavi `MSG_*`.
- **Async indexing build** (ADR 0093): systemd transient unit `metnos-build-<sha8>-<idx>`. Atomic write + resume da checkpoint. Disable test `METNOS_HTTP_DISABLE_BUILD_TASKS=1`.
- **Fast path deterministico** (ADR 0094): `runtime/fast_path.py` short-circuit query triviali PRIMA del PLANNER. Tabella chiusa `_FAST_PATTERNS`. ZERO LLM. Estendere SOLO con mapping 1:1.
- **Multi-tool fast-path memoization L2** (ADR 0150): `runtime/multi_tool_paths.py` sqlite TTL active-days, env `METNOS_MULTI_TOOL_FAST_PATH=1`. Bridge L2→L3 `runtime/jobs/multi_tool_promote.py` (uses>=50).
- **Invariante executor > fast-path bidirezionale** (ADR 0150 ext): `multi_tool_paths.try_match`+`record_path` ricevono `available_tool_names`; se synth equivalente esiste, demote silenzioso. Planner `_core.j2` sezione "EXECUTOR > FAST-PATH".
- **args_extractor V1.5 hybrid** (ADR 0149 ext + 0150 ext): `runtime/args_extractor.py` regex chiusa + memoization `args_observed` da `canonical_query_log` + LLM fallback opt-in `METNOS_CQ_ARGS_LLM=1`.
- **Config persistente runtime.toml** (Fase 12): `runtime/runtime_settings.py` legge `~/.config/metnos/runtime.toml`. Override hierarchy `env METNOS_* > toml > default`. `ensure_default_config()` integrato in `install/setup.sh`.
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
- **Skill importer agentskills.io → executor** (ADR 0123): pipeline 5-stadi. CLI `metnos-skills`. Mapping `runtime/skill_vocab_map.json`. Smoke `runtime/smoke_imports.py`. Storage canonical `<USER_DATA>/executors/skills/<skill>/` (ADR 0160 rename da `_imports/`), legacy READ-ONLY su `_imports/`. Helper centralizzato `runtime/skills_paths.py` (dual-root scan). Skill registry `runtime/skill_registry.py` espone `list_skills(lang=...)`, `enable/disable`, gating loader via `is_skill_enabled()`.
- **Proposal auto-evaluator + ETA instrumentation** (ADR 0122): `runtime/proposals_eta_index.py` + scheduler v2 daily@04:30. `runtime/proposal_evaluator.py` 6 killer + 7 signal → verdict. CLI `python -m admin.proposals_cli evaluate`.
- **Unified image enrichment index** (ADR 0117, supersedes 0086 per image): UN asse `unified/` per corpus. Schema v4 `runtime/index_schema.py`. Pipeline EXIF+ArcFace+VLM+BGE-M3 via `runtime/build_runner_unified.py`.
- **Prompt architecture A+B+C**: split `planner.j2` in `_core`+sezioni+`_footer`; `prompt_loader.compose()`; linter `runtime/prompts_lint.py`. Daemon `i18n_translate_pending.py` daily@02:00. §6.1.
- **Pattern intent-implicit** (ADR 0129): `vocab.detect_implicit_actions(query)` deterministico §7.9. Wiring: `intent_extractor` → `agent_runtime` → `orchestration._orchestrate_implicit_actions` (`_ACTION_TEMPLATES`). `_handle_needs_inputs` propaga `decision="needs_inputs"` (OAuth flow).
- **Backend tree allineato agli OBJECTS** (ADR 0130): `runtime/backends/<OBJECT_PLURALE>/<provider>.py`. Auto-default `_default_client()` per dispatcher via filesystem check. Retry §7.9 3× su transient. `send_messages` timeout_s=90.
- **Plugin esterni per backend** (ADR 0132, **DEPRECATED 24/5/2026** superseded ADR 0123+0136+0160): canale rimpiazzato dalle skill imported con provider qualifier + env `METNOS_HIDE_EXECUTORS`. `runtime/plugin_loader.py` rimosso.
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
- **GitHub provider first-party** (ADR 0141): 13 executor `*_github`. Watcher scheduler v2 + Q&A learning (`runtime/jobs/github_dedup.py`). Config `~/.config/metnos/github_watched_repos.json`.
- **consult_frontier system verb** (ADR 0142): `executors/consult_frontier/` modo A single-call + modo B agentic tool use (5 tool read-only). Tier config `~/.config/metnos/llm_tiers.toml`. Cache disk TTL + cost estimator + fallback chain.
- **Install-on-demand pattern §7.3** (ADR 0143 TODO): `runtime/system_binaries.py` whitelist auto-derive. Errore `binary_missing+suggested_install` → runtime auto-injecta admin step. Sudoers NOPASSWD `apt-get install -y *`. `runtime/system/admin.py` whitelist guard (ADR 0160 rename).
- **PLANNER split call grammar GBNF opt-in** (ADR 0151): `runtime/planner_split.py::chat_with_tools_split` 2-call. Env `METNOS_PLANNER_SPLIT=1`. 1.72× speedup. Selector history-aware + soft-gate verb-canonical.
- **i18n pipeline strutturale** (ADR 0152): subset 65 chiavi nel synt stage 5; `_i18n_error_for_class` Google API; daemon `_materialize_auto_synth_stubs` con `auto_translated` flag.
- **delete_files executor** (19/5/2026 v4): `executors/delete_files/` + `backends/files/local.py::delete_files` reversible `restore_blob_backup`. Riempie gap catalog (PLANNER sceglieva erroneamente delete_dirs per file).
- **Unified change_intent lifecycle** (ADR 0158, 22/5/2026): single object/FSM/UI `/admin/changes`. Stati `proposed→accepted→applied→observed→finalized` (+ staged|rejected|failed|rolled_back). 6 kind: create/extend_executor, dedupe_executors, materialize_pipeline, cache/reject_pattern. Storage `~/.local/state/metnos/change_intents.sqlite`. Jobs: `change_intent_materialize.py` daily@01:00 (dedup fingerprint cross-source), `change_applier.py` every_10m, `change_observer.py` daily@03:15 (grace 7gg `METNOS_CHANGE_GRACE_DAYS`). Soft-deprecation `/admin/{proposals,promotions}` banner redirect.
- **Shape FSM normalization** (23/5/2026): `agent_runtime.TurnLog.write()` normalizza `chosen_tool="final_answer"` su ultimo step terminale con `chosen_tool==""`. Copre LLM no-tool-call/ProviderError. Coerente con regex lint `^E*F?$` (small-talk F-only legittimo).
- **Inproc tool catalog injection** (23/5/2026): `loader._inject_inproc_tool_specs` + `BUILTIN_INPROC_SPECS` espone tool moduli runtime al catalog `/admin/executors` (es. `recurring_tasks.*_tasks`). Risolve gap §2.2 `tasks` 0-producer.
- **Env `METNOS_HIDE_EXECUTORS` + `METNOS_LOADER_VERIFY`** (23/5/2026): loader exclude lista + disable verify firma (test only). Pair per test E2E che forzano PLANNER a usare skill imported nascondendo builtin equivalenti.
- **`tool_grammar.filter_pool_for_grammar` canonical-aware** (23/5/2026): provider-suffixed NON rimosso se canonical equivalente assente dal pool (es. nascosto via HIDE_EXECUTORS). Altrimenti marker filter ADR 0136 svuotava il pool.
- **E2E driver baseline** (23/5/2026): `server._copy_db_with_wal` (SQLite backup API, WAL pending) + `_seed_i18n_baseline` SEMPRE (1000+ MSG_*/ERR_* runtime-essential) + lint regex `^E*F?$`.
- **Judge prompt safety-aware** (23/5/2026): `prompts/{it,en}/e2e_judge.j2` riconosce consenso utente (signature unknown, mount, sudoer) come VALID answer (ok=true, score≥0.7).
- **`describe_entries.max_tokens` adattivo** (23/5/2026): scala con N entries — N≤3:200, N≤10:300, N>10:400 (era 600 fisso). Misurato: query "appuntamenti domani" 68s→30s (-56%) e/o `read_events` skip-describe pattern. Override esplicito via arg.
- **Safety net 7-layer skill imported** (ADR 0159, 24/5/2026): L1 sign verify; L2 affinity Jaccard ≥0.5 (≥0.85 binding-suffixed) via `loader.check_affinity_pair`; L3 efficacy ager (deprecate 30g, archive 14g); L4 sandbox planned (Fase C, trigger ≥5 skill OR ≥1 guest); L5 smoke at-import via `skill_admission._run_smoke_for_plan`; L6 LLM semantic verifier Gemma 26B default-ON (bypass `--skip-l6`); vaglio.judge runtime + `skill_audit/<YYYY-MM-DD>.jsonl` sharded daily. Builtin handcrafted skip L2/L6/audit.
- **Strato 3 escalation UI dopo ≥3 ✗ consecutive** (task #30, 24/5/2026): `agent_runtime._orchestrate_strato3_escalation` early-exit prima del PLANNER loop quando `count_consecutive_errors_for_query >= 3`. Dialog `get_inputs` 4-choice (synth/frontier/reformulate/abandon) → on_complete `strato3_choice_dispatch` in `orchestration.py` mappa scelta → nuova `run_turn(chosen_query, allow_disambig_synth=False)`. Determinismo §7.9 (no LLM nell'escalation path). Strato 1 (soft prompt) + Strato 2 (hard constraint ≥2 ✗) restano upstream in `_render_rejected_pipelines_block`.
- **`dialog_pending.DIALOG_DIR` §7.11** (23/5/2026): era `Path.home()/.local/share/metnos/get_inputs` hardcoded → ora `_C.PATH_USER_DATA / "get_inputs"`. Senza, test E2E scrivevano dialog OAuth in LIVE storage → cross-contamination state tra test (Step 2/2 MSG_OAUTH_PROMPT_SERVICES leaked dalle query google ai test fast_path).
- **`*_tasks` conditional injection** (23/5/2026): in `agent_runtime` i 6 builtin scheduler v2 (create/list/delete/read/set_tasks + read_tasks_history) iniettati nel pool PLANNER SOLO se query contiene marker scheduling (`_TASKS_MARKERS` in `tool_grammar.py`: task/promemoria/ricordami/schedule/etc.). Senza, PLANNER LLM li selezionava su query ambigue (caso live 23/5: «cerca mail bookings» → read_tasks_history). Stesso filter aggiunto in `filter_pool_for_grammar` per grammar mode.
- **Skill importer R1+R2+R3** (ADR 0159 wiring, 24/5/2026): R1 `skill_description_llm.generate_description_or_fallback` wired in `cli/skills_cli._cmd_import` pre-codegen (5s budget env `METNOS_SKILL_LLM_TIMEOUT_S`, audit `skill_descriptions_audit.jsonl`). R2 `importer_verb_verify.check_plan` gate post-translate (`aligned=False` → reject prefix `verb_boundary:`). R3 fallback locali `_METNOS_VERBS/OBJECTS/QUALIFIERS` rimossi: import diretto da `vocab.*` (§7.3 single source). Perf: cache mtime, jinja singleton, smoke batch.
- **Locale-aware skill bundle + rename _imports→skills** (ADR 0160, 24/5/2026): pattern bundle-per-locale per feature locali. Canonico `executors/skills/it_locale/` raggruppa N feature IT in un namespace. SKILL.md campi nuovi: `lang`/`trust`/`auto_enable`/`distribution`/`feature_modules`. Bundle helper-library senza manifest.toml root; `vendors.json` lazy via `__file__.parent.parent` (§7.11), override env. Aggiungere feature = file in `scripts/` + append `feature_modules:`. Rename `_imports/` → `skills/` (ADR 0123): loader scansiona entrambi, write in `PATH_SKILLS_USER` (vedi `skills_paths.py`).
- **ClusterLLM + Pronoia classify_fail + retry on repeat** (ADR 0162, 26/5/2026): estende ADR 0161. `runtime/praxis_cluster.py` cluster emergente BGE-M3 + cosine (`COSINE_HIGH=0.90`/`COSINE_LOW=0.75`) + LLM judge zona grigia. Champion/challenger `composite_score=0.5·succ+0.3·speed+0.2·align`, swap se `2·Δsucc+Δlat_norm>0.15`. `runtime/pronoia_classify_fail.py` dispatch ✗ in {format/args/pipeline}_fail. ↻ = Mētis re-propose inline + soft anti_skill TTL 1h. Counters separati: `_touch_skill` solo ts_last_used; `update_skill_metrics(success)` uses/ok/fail. Audit `skill_versions` table. Batch daily@03:30 `praxis_cluster_merge.py` + @04:00 `praxis_template_refresh.py`. Renderer `${stepN.@count}` cascata available_total→ok_count→used→len(prima list dict). Constants `runtime/praxis_constants.py` env-tunable. Endpoint `GET /admin/skills/{id}/history`. Pattern `NAME_TO_WEB_REVERSE_IMAGE` in praxis_propose.j2. 33/33 test. Env: `METNOS_CLUSTER_*`/`METNOS_PRAXIS_W_*`/`METNOS_PRONOIA_CLASSIFY_FAIL`/`METNOS_REEXECUTE_ON_REPEAT`/`METNOS_EXPLORATION_EPSILON`. Feedback whitelist: {ok|error|repeat}.
- **Praxis Engine — pentade greca cognitiva** (ADR 0161, 25/5/2026): rimpiazza PLANNER step-by-step con 5 moduli `runtime/{praxis,praxis_propose,praxis_executor,pronoia,aporia}.py`. **Mētis** propone framework 1-shot wise GBNF; **Noûs** esegue deterministico (resolve from_step/${FILLER}/${stepN.field}, vaglio.judge); **Praxis** sqlite `~/.local/share/metnos/praxis.sqlite` cache O(1) intent_sig + auto-promote 2-3 ✓ + anti_skill TTL 30gg; **Pronoia** recovery {wrong_tool/wrong_args/missing_input} 1×/turno; **Aporia** sqlite vicolo cieco onesto: classify root_cause + suggested_action. Cascata: fast_path → intent_extractor → Praxis.try_match → Mētis → Noûs → Pronoia → Aporia. Bench 35q: 33/35 (94%), 12.5s mean (vs 76.3s PLANNER, 6× speedup), Gemma 26B locale. Feedback ✓✗↻ via `turn_feedback.apply_feedback`. Deprecations marker `# DEPRECATED-PRAXIS`: planner_split, tool_grammar, planner/_core.j2 step-aware, multi_tool_paths, agent_runtime.run_turn step loop — rimozione post-MVP convergence ≥90%. Prompts: `prompts/{it,en}/{praxis_propose,pronoia_recovery}.j2`. Wire-in `agent_runtime.run_turn` pre-PLANNER. §7.9 deterministico tranne Mētis + filler.

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

- ADR registry: `decisions/` (`0001-0161`, `0055`/`0115`/`0116`/`0121` skipped) — dettagli implementativi e razionale.
- Architettura canonica: `docs/it/architecture/` (+ EN bridge simmetrico).
- Memorie persistenti: `~/.claude/projects/-opt-myclaw/memory/MEMORY.md` (path Claude harness, indipendente dal rename Metnos).
- Repertorio prompt: `runtime/prompts/<lang>/*.j2` (ADR 0092).
</content>
</invoke>