# Metnos — Linee guida di progetto

> **OBBLIGO**: leggere integralmente all'inizio di ogni sessione. Codifica decisioni architetturali, convenzioni di codice e norme di processo. Punto obsoleto/errato → AGGIORNA subito.
>
> Mantenuto da: agente. Aggiornamento quando si fissa una nuova norma duratura. Storia in `git log CLAUDE.md`. Dettagli implementativi vivono negli ADR (`decisions/`), non qui.
> Ultimo: 2026-06-05 v22 (ADR 0170: tassonomia skill 3-tier + confine skill↔backend ortogonale + mono→multi provider; google-workspace vendorizzata Tier-2; 5 executor resi reversibili §2.3). v21: norma «Distribuzione public-subset». Norme correnti negli ADR 0150-0170; changelog completo in `git log CLAUDE.md`.

---

## 1. Cos'e' Metnos

Assistente personale self-hosted (su `.33`, Strix Halo 96GB unified). Microarchitettura a executor sintetizzati al volo via synt multistage; runtime ReAct con planner LLM (Gemma 4 26B middle/wise locale + Sonnet/GPT-5 frontier come fallback). Canali: **Telegram** + **HTTP porta 8770** (htmx + Jinja2 + uPlot, ADR 0078). Pipeline immagini in-process: SigLIP-base + RetinaFace+ArcFace + EXIF (ADR 0086/0117). Lingua principale: italiano; corpus doc bilingue IT+EN. Etimologia: `mētis + noûs`. Process name: `myclaw`. Dominio: `metnos.com`.

ADR registry canonico: `decisions/` (relative alla repo root; `0001-0169`, `0055`/`0115`/`0116`/`0121` skipped — fonte unica per "perche' abbiamo scelto cosi'").

## 2. Principi cardine (mai negoziabili)

### 2.1 Executor vettoriali per costruzione
Input come **lista** (paths, entries, urls, ids). Output **sempre lista**, anche degenere (N=0/1/molti). Niente `*_batch`: la versione batch *e'* l'executor. Iterazione interna (loop, paginazione, window come `time_window: today|last-Nd`); branching su osservazione torna al planner. Cap inferiore = 0; cap superiore = parametro esplicito (`max_total`, `max_results`, `max_bytes`).

### 2.2 Naming convention compositiva
Struttura: `azione_oggetto[_qualifier]`. Vocabolario CHIUSO (escalation a Roberto per nuovi termini), centralizzato in `runtime/vocab.py`.

- **23 azioni**: `read, write, move, delete, create, find, list, filter, sort, group, classify, get, set, send, describe, render, extract, compress, compute, compare, change, order, share`.
- **Ortogonalita' 5 verbi-produttori** (asse «input primario»): `find`=pattern/query; `get`=id noti o snapshot; `read`=id→contenuto; `list`=container enum senza contenuto; `filter`=lista preesistente, predicato. `change`=forma/parametri; `order`=ordinamento PERSISTENTE corpus (vs `sort` in memoria); `share`=OUTBOUND CONSENT grant ACL remoto (ADR 0128).
- **Importer verb boundary** (ADR 0128): mapping provider→Metnos in `runtime/skill_vocab_map.json::contextual`. Verifier `runtime/importer_verb_verify.py::check_plan` (layer 6.bis ADR 0114).
- **Confini stretti**: `fetch` rimosso (HTTP GET=`get_urls`); `extract` = STRUTTURA incapsulata in un contenitore (allargato 3/6, ADR-pending): (1) archivi zip/tar/gz=`extract_files`; (2) **record strutturati da testo NON strutturato** (web/mail/pdf)=`extract_entries` (es. eventi {summary,start,end}); «estrai righe testo»=`filter_texts_lines`; «estrai testo GREZZO PDF/HTML»=`read_files_{pdf,html}`; «estrai campi da entries GIÀ strutturate»=`get`.
- **21 oggetti**: `files, dirs, packages, messages, events, contacts, places, processes, urls, numbers, images, signatures, texts, proposals, persons, tasks, inputs, credentials, issues, pulls, entries`. Eccezioni: `get_inputs`={values} UI (ADR 0090); `*_credentials` metadata-only (ADR 0123); `entries` meta-oggetto in-memory; `persons` vs `contacts` (ADR 0113/0137); `tasks` (scheduler v2) vs `events` (ADR 0112/0137); `issues`/`pulls` provider github (ADR 0141).
- **System verbs riservati**: `undo`, `admin` fuori dai 22 canonici. Builtin runtime only. Stage 1 NAMING non li propone.
- **Qualifier opzionali, 4 famiglie**:
  - **formato**: `_csv, _xlsx, _ocr, _zip, _pdf, _xml, _html, _json, _text, _gz, _tar, _video, _audio, _image, _hash`.
  - **modalita'**: op (`_size, _format, _similar, _loc, _empty`); granularita' (`_lines, _paragraphs, _sentences, _pages, _segments`); persistente `_indices` (eccezione: `find_<dom>_indices` ritorna entries). `_empty` (ADR 0127) cross-domain con arg `size` unit-aware.
  - **safety policy** (`signatures`, ADR 0071): `_blacklist, _whitelist, _graylist, _forbidden, _seed, _diff, _sanity, _command, _reversibility, _promotion, _candidates`.
  - **provider** (ADR 0136): backend non-default. `_google_workspace` (skill gmail/drive/calendar ADR 0123); `_metnos` default omesso. Filtro pool grammar via marker (`tool_grammar._PROVIDER_SUFFIX_MARKERS`). Dormant se no creds.
- **4° livello descriptor** (ADR 0156): schema `verb_object[_qualifier[_descriptor]]` posizionale, separatore `_`. Descriptor kebab-case `[a-z0-9]+(-[a-z0-9]+)*` max 30. **Regole**: (R1) descriptor solo se qualifier presente; (R2) un livello alla volta — no 3°+4° nuovi insieme; (R3) descriptor = modificatore comportamentale a parita' args. OK `_dry-run`/`_v2`/`_unified`; ERRORE `_nightly` (→`tasks`). Enforce via `naming_grammar.validate_name` + GBNF v3. R3 = euristica prompt + vaglio.
- **Governance vocab §2.2** (ADR 0156): nuovo token richiede 3 criteri congiunti: (1) **necessario** (no equivalente nella stessa classe); (2) **generale** (semantica riusabile); (3) **comprensibile** (Gemma 26B lo capisce senza spiegazione). Proposte introspettive che richiedono estensione DEVONO marcare nel rationale "RICHIEDE estensione vocab §2.2: <criteri>".

Stage 1 di synt usa MAPPING bilingue IT+EN. Sinonimi prima dell'estensione del vocabolario.

### 2.3 Reverse pattern catalogo deterministico
Manifest dichiara `reverse_pattern` da catalogo chiuso: `swap_src_dst` (move/rename), `delete_created_dirs`, `delete_created_paths`, `restore_blob_backup` (richiede `blob_path`+`blob_sha256` nei results), `delete_<object>_by_id` (ADR 0123, contratto: `<object>_ids` + scope id, runtime costruisce `delete_<object>(ids=..., scope=...)`). Blob: `$METNOS_HISTORY_DIR/<METNOS_TURN_ID>/blob/<sha256>.bin`.

### 2.4 Robustezza al confine NL→determinismo
Executor accetta: `0-as-placeholder` (cap=0 → no limit), compound case-insensitive di default, args plurali ammessi (`paths` accetta anche 1 elemento). Helper comuni in `runtime/executor_helpers.py`. Niente patch reattive per-executor.

**Convention args `array of string`**: decidere a design-time il dominio.
- **Aperto** (testo libero) → tolleranza wildcard `*`/`?` via `fnmatch.fnmatchcase`. Manifest dichiara "valori con `*`/`?` = glob". Es: `filter_entries.where_in`.
- **Chiuso** (enum/slug/ID/scope/email) → match esatto stretto. Manifest dichiara "MATCH ESATTO, wildcard NON supportati". Es: `delete_persons.chosen_slugs`, `set_credentials.scopes`.

Razionale: LLM ha bias verso glob universali; fnmatch su dominio aperto previene fallimento silenzioso, match esatto su chiuso impedisce abuso.

### 2.5 Manifest leggibili da LLM medium
Manifest TOML = "prompt del tool" per Gemma 4 26B (NON Sonnet/Opus). Modello canonico: `executors/find_files/manifest.toml`. Criteri: description 2-5 frasi corte (max 25 parole/frase); esempi tra virgolette per formati non ovvi (`time_window="last-24h"`); default in chiaro; niente gergo Python; affinity 8-15 termini IT+EN user-facing; args 1 frase + tipo + esempio + default. Anti-pattern: description 800 parole, prosa colloquiale ("DEVI usarla per operazioni su X"), pattern-by-example senza separatore "non copiare letteralmente".

**FORMATO `[description]` a CAPITOLI** (REGOLA UNIVERSALE, 2026-06-02): la description segue 4 capitoli prescrittivi, pattern-oriented, stringati, in quest'ordine:
```
SCOPO: <1 frase: cosa fa>. PATTERN: <forma di chiamata canonica, literal: tool(arg="...", arg=N)>. NON: <anti-pattern + disambiguazione vs tool simili>. OUT: <shape output pipeable>.
```
`SCOPO`+`PATTERN` front-loaded: il proposer li estrae fino a `OUT:` (`engine/proposer.py::_render_tool_pool`) — l'LLM medium copia la FORMA dal `PATTERN`, non inventa args. Vale per ogni manifest NUOVO e ogni VECCHIO toccato per un fix; **NON** rifattorizzare in massa. Generazione conforme: synt stage 4 (`synt_description.j2`) + importer (`skill_codegen._description_boilerplate`). Boundary verbo §2.2 nel capitolo `NON:` (es. "NON pull request -> find_pulls_github"). Esempio canonico: `executors/write_files/manifest.toml`.
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
Edit di `<executor>.py` cambia il digest sha256 ma NON il `manifest.toml` → `loader.py::verify_executor` scarta silenziosamente l'executor (`declared != actual`). OBBLIGATORIO dopo ogni edit `.py`: `python3 runtime/sign.py sign executors/<name>` (da repo root) + restart `metnos-http.service`. NB: `python -m runtime.sign` NON funziona (manca `__init__`/`__main__`); usare lo script diretto.

### 7.11 No path assoluti hardcoded (rename-resilient)
Niente `Path("/opt/...")` verso la install root nel codice attivo. Root auto-derivata via `Path(__file__).resolve().parents[N]` in `runtime/config.py::PATH_ROOT`; derivati (`PATH_RUNTIME/EXECUTORS/WORKSPACE`, `DB_*`) seguono. Override env `METNOS_INSTALL_ROOT` (alias deprecato `METNOS_HOME`). Convenzione: ogni callsite usa `from runtime import config as C` → `C.PATH_*`; sotto-dir derivate `C.PATH_ROOT / "<sub>"`. Razionale: rename `/opt/myclaw→/opt/metnos` zero-config. ADR 0148: 8 categorie disalignment + stato refactor R2.

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
- `user@example.com` (register.it) → `scripts/check-mail.sh` (relative).
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

> **Regola d'oro**: una riga per meccanismo, solo call-site essenziale (file/funzione/policy). Dettagli, env vars, bench numbers, date → ADR.

**Naming / vocab / grammatica**
- **Naming Authority** (ADR 0156): `runtime/naming_grammar.py` valida nome + genera GBNF da `vocab.py`. Single source per stage 1/telos/skill importer.
- **Manifest linter strutturale** (ADR 0169): `runtime/manifest_lint.py` deterministico (§7.9) — check FORMA scheda-tool (CAPITOLI, PATTERN-budget, PATTERN-args ⊆ schema, output-shape §2.6, affinity-overlap, NON→sibling). Wired synt stage 5.5. CLI `--all`.
- **Constrained generation** (ADR 0133): `runtime/tool_grammar.py` GBNF per ogni step. Loop-detect `runtime/loop_detect.py`. Opt-in `METNOS_GRAMMAR=1`.
- **Grammar pool extensions** (ADR 0135): `final_answer` synthetic from step≥2; `_parse_tool_call_tolerant` JSON recovery; `_FROM_STEP_HELPERS` esclusi al primo step.
- **Skill dormancy + provider qualifier** (ADR 0136): `Executor.dormant` se skill senza credenziali (`runtime/skill_credentials.py`). `tool_grammar._PROVIDER_SUFFIX_MARKERS` filtra pool.
- **Vocab extension persons+tasks** (ADR 0137): OBJECTS 17→19. Synonyms IT+EN in `vocab.py`.
- **filter_lists + tassonomia liste** (ADR 0138): `filter_lists` (set ops bi-list) vs `filter_entries` (1L predicati). Wire `_resolve_from_step` Layer 5.
- **Builtin scheduler v2 canonical** (ADR 0133 ext): `create/list/delete/read/set_tasks` con fallback `id→name`.
- **`*_tasks` conditional injection**: iniettati nel pool PLANNER solo se query ha marker scheduling (`_TASKS_MARKERS` in `tool_grammar.py`).

**Planner / Praxis / runtime flow**
- **Praxis Engine — pentade** (ADR 0161): cascata `fast_path→intent_extractor→Praxis.try_match→Mētis→Noûs→Pronoia→Aporia`. `runtime/{praxis,praxis_propose,praxis_executor,pronoia,aporia}.py`, wire pre-PLANNER `agent_runtime.run_turn`.
- **ClusterLLM + classify_fail** (ADR 0162): estende 0161. `runtime/praxis_cluster.py` BGE-M3 + cosine + champion/challenger. `runtime/pronoia_classify_fail.py` dispatch ✗. Constants in `runtime/praxis_constants.py`.
- **persons aggregator + ${RUNTIME:*}** (ADR 0163): `get_persons` (scheda) vs `read_persons` (profilo JOIN). `${RUNTIME:key}` in `praxis_executor.py`, whitelist `{actor,lang,channel}`; `compute_intent_sig` con scope marker.
- **Pipeline shape FSM** (ADR 0154): `runtime/pipeline_shape.py` invariante `E+ (F|A)?` + hook in `agent_runtime`.
- **Planner choice > runtime override** (ADR 0155): runtime non sovrascrive il planner (eccetto auto_remediation / vaglio / fast-path). Vietato interceptor pattern-match.
- **Fast path deterministico** (ADR 0094): `runtime/fast_path.py` short-circuit pre-PLANNER. Tabella chiusa `_FAST_PATTERNS`. ZERO LLM.
- **Multi-tool fast-path L2** (ADR 0150): `runtime/multi_tool_paths.py` sqlite TTL. Bridge L2→L3 `jobs/multi_tool_promote.py`. Executor>fast-path bidirezionale: `try_match`+`record_path` ricevono `available_tool_names`.
- **args_extractor V1.5** (ADR 0149+0150): `runtime/args_extractor.py` regex + memoization `args_observed` + LLM fallback opt-in.
- **PLANNER split GBNF** (ADR 0151): `runtime/planner_split.py::chat_with_tools_split` 2-call. Opt-in `METNOS_PLANNER_SPLIT=1`. 1.72× speedup.
- **Pattern intent-implicit** (ADR 0129): `vocab.detect_implicit_actions(query)` deterministico. Wire `intent_extractor → agent_runtime → orchestration._orchestrate_implicit_actions`.
- **Compound query decomposition** (4/6): intent LLM → `actions=[{verb,object}]` per CLAUSOLA (`intent_extractor.j2` it+en); `dispatch` rank pool per-PAIR (object reale per clausola); `proposer` salta verb-filter se `len(actions)>=2`. No dizionari sinonimi; `detect_canonical_verbs_all` = fallback lessicale.
- **Shape FSM normalization**: `TurnLog.write()` normalizza ultimo step a `final_answer` se vuoto. Lint regex `^E*F?$`.
- **Inproc tool catalog injection**: `loader._inject_inproc_tool_specs` + `BUILTIN_INPROC_SPECS` espone tool moduli runtime al catalog admin.
- **Adaptive re-rank intra-turno** (ADR 0072): `runtime/adaptive_rerank.py` add-only, cap `2×k_max`.

**Prefilter / affinity / matching**
- **Prefilter modulare 1000-tool scaling** (ADR 0140): `runtime/prefilter_strategies/` 14 strategie via env `METNOS_PREFILTER`. Telemetria JSONL + bench script.
- **Prefilter primary tools per object** (ADR 0075): `_OBJECT_PRIMARY_TOOLS` in `prefilter.py` allineato a OBJECTS.
- **Prefilter precursor universale**: `rank_with_intent` inietta precursor per verbi non-producer.
- **Semantic affinity fallback** (ADR 0134): `runtime/affinity_semantic.py` BGE-M3 ONNX int8 se top_score sotto soglia.
- **LLM query expansion** (ADR 0139): `_expand_query_via_llm` Gemma + cache disk. Sostituisce BGE-M3 expansion su query mono-token.
- **Vaglio safe-verb shortcut** (ADR 0107): `vocab.SAFE_VERBS` 11 verbi → `Verdict(judge_kind="safe-verb-shortcut")`.

**Synth admission / sandbox / skill importer**
- **Synth admission 4 layers** (ADR 0114): L2 affinity Jaccard ≥0.5 (`loader._check_affinity_overlap`); L3 ager (`executor_aging.py`, handcrafted mai demoted); L5 smoke; L6 LLM verifier (`synt_stage6_verify.py`).
- **Safety net 7-layer skill imported** (ADR 0159): L1 sign + L2 Jaccard ≥0.5 (≥0.85 binding) + L3 ager + L4 sandbox (planned) + L5 smoke + L6 LLM verifier + audit JSONL sharded.
- **Skill importer 5-stage + R1+R2+R3** (ADR 0123+0159 wiring): CLI `metnos-skills`. Mapping `runtime/skill_vocab_map.json`. R1 `skill_description_llm` pre-codegen; R2 `importer_verb_verify.check_plan` gate; R3 zero-fallback su `vocab.*`.
- **Locale-aware skill bundle + rename → skills/** (ADR 0160): pattern bundle-per-locale `executors/skills/<locale>/`. Helper `runtime/skills_paths.py` dual-root scan. SKILL.md fields: `lang/trust/auto_enable/distribution/feature_modules`.
- **Skill registry**: `runtime/skill_registry.py` espone `list_skills/enable/disable`, gating via `is_skill_enabled()`.
- **Tassonomia skill 3-tier + confine skill↔backend** (ADR 0170): `tier ∈ {core, first_party, imported}` (`skills_catalog.skill_tier`). Backend=COME (config, `backend_resolver`), skill=SE/QUALI (attivazione/fiducia/packaging); ortogonali, dipendenza dichiarata UNA volta al backend, skill aggrega. Mono→multi provider = +backend +skill, 0 executor (`*_issues` resta canonico, provider→resolver). google-workspace = Tier 2 vendorizzata `executors/skills/google-workspace/`. Tier 3 = sandbox 7-layer (ADR 0159); pubblico spedisce solo Tier 1+2.
- **Sandbox per-skill foundation** (ADR 0140 ext): `Executor.sandbox_profile/provenance/is_imported`. Audit `runtime/skill_audit.py`. Watchdog `jobs/skill_sandbox_watchdog.py`.
- **Catalog invariants al load**: `runtime/loader.py` rifiuta synth con collision verso handcrafted. `_gc_collisions` sposta i rejected in tmp.
- **No synth ridondanti**: stage 1 NAMING preferisce canonical esistente se intent coperto.
- **Synth_request short-circuit** (ADR 0076): `handle_synth_request` skip pre-cascata su catalog match.

**Backend / executor / domini**
- **Backend tree per OBJECT** (ADR 0130): `runtime/backends/<OBJECT>/<provider>.py`. Retry 3× su transient.
- **Backend resolver uniforme** (ADR 0165): provider=config non intento; `backend_resolver.py` risolve `client/account/provider` deterministico (no enum all'LLM). 4ª eccezione §4.1/0155 (valori-config, non forma/flusso).
- **Plugin esterni** (ADR 0132 **DEPRECATED**): superseded da skill imported + `METNOS_HIDE_EXECUTORS`. `plugin_loader.py` rimosso.
- **Indici di dominio** (ADR 0086, image superseded by 0117): pattern `{create,find}_<dom>_indices`. Storage `~/.local/share/metnos/index/<dom>/<sha8>/<idx>/`.
- **Unified image enrichment index** (ADR 0117): single asse `unified/` per corpus. Schema v4 in `runtime/index_schema.py`. Pipeline EXIF+ArcFace+VLM+BGE-M3.
- **Intelligent path-aware indexing** (ADR 0166): `folder_path_context` (`create_images_indices.py`) → `path_context` fuso nell'embedding; parse temporale + escape coseno `find_images_indices.py`; re-embed `jobs/reembed_path_context.py`.
- **Taglio di rilevanza adattivo** (ADR 0169): `relevance_cut.py::adaptive_relevance_threshold` — taglio RELATIVO per-query `μ+3σ`+floor (coseno denso in banda stretta). Wire `find_images_indices`. Riusabile da ogni retrieval scored.
- **Spreadsheet LOCALE di default** (ADR 0169): `local.py::{create,write,append,read}_spreadsheet` (.xlsx/.csv); i 3 `*_files_spreadsheet` default `client="local"` (§10.3), Google opt-in; `spreadsheet_id`==PATH.
- **Guard refusal-in-args** (ADR 0169): `agent_runtime.validate_args` + `_LLM_REFUSAL_MARKERS` (IT+EN) — rifiuto LLM come VALORE di un arg = step malformato (§2.8). Deterministico §7.9.
- **Named persons registry** (ADR 0113): `~/.local/share/metnos/persons.sqlite` (slug case+accent-insensitive). 4 executor `*_persons` con ambiguity → dialog `kind="choice_with_preview"`.
- **GitHub provider first-party** (ADR 0141): 13 executor `*_github`. Watcher scheduler v2 + dedup `jobs/github_dedup.py`. Config `~/.config/metnos/github_watched_repos.json`.
- **consult_frontier system verb** (ADR 0142): `executors/consult_frontier/` modo A single-call + modo B agentic tool use. Tier config `~/.config/metnos/llm_tiers.toml`.
- **delete_files executor**: `executors/delete_files/` + `backends/files/local.py::delete_files` reversible.

**Crawler / web**
- **Pipeline web/news/scuola** (ADR 0081+0082+0098+0101+0105+0108): `{find_urls,read_urls_html,read_urls_pdf,login_session}`. Tier config `~/.config/metnos/{owned_domains,trusted_origins}.json`.
- **Web crawl parallel** (ADR 0098): `runtime/host_throttle.py` Semaphore per-host. ThreadPoolExecutor cap.
- **Crawler error_class** (ADR 0101): `read_urls_html._fetch_one` ritorna `(None, {error, error_class})`.
- **HTTP cache disk** (ADR 0105): `runtime/http_cache.py` storage sharded sha. TTL via env.
- **Auto-degrade T2→T1** (ADR 0108): `runtime/host_health.py` sliding-window 60min → `~/.config/metnos/blocked_origins.json`.

**Credenziali / admin / sicurezza**
- **Admin esposto al PLANNER** (ADR 0088): `EXPOSE_TO_PLANNER=True`, vaglio always-on. HMAC consent token TTL 600s.
- **CIFS/SMB via admin → sudoer** (ADR 0087+0160): `runtime/safety/canonicalize.py` + `runtime/cifs_helper.py` + `runtime/system/sudoer.py`. Placeholder `${METNOS_CIFS_CREDS}`. No password in argv.
- **Install-on-demand** (ADR 0143 TODO): `runtime/system_binaries.py` whitelist. Error `binary_missing` → auto-inject admin step. Sudoers NOPASSWD `apt-get install -y *`. Whitelist guard in `runtime/system/admin.py`.
- **Credenziali UX 3 strati** (ADR 0089+0091): `extract_credentials` regex + dialog `needs_inputs` (`orchestrate_needs_inputs`) + CLI `metnos-cli credentials`. Binding `_BINDING_STRONG/_WEAK`.
- **Credenziali single store** (ADR 0131): `runtime/credentials.py` Fernet+HKDF, domain `smtp_<account>`. CLI `python3 -m credentials_migrate`.

**UI / output / i18n**
- **Engine UI dichiarativo** (ADR 0090): `get_inputs(title, dialog=[...], fmt=...)`. Storage `runtime/dialog_pending.py` (path da `_C.PATH_USER_DATA`). Adapters Telegram + HTTP.
- **Output formatter deterministico** (ADR 0095): `runtime/output_format.py` channel-agnostic markdown. NIENTE LLM.
- **Channel-aware HTML** (ADR 0109+0110): `runtime/html_sanitizer.py::{to_safe_html, to_safe_html_full}`. Dispatch in `http_routes_agent::_safe_final_html`.
- **Prompt-as-data + multilingua** (ADR 0092): `runtime/prompts/<lang>/<role>.j2`. `prompt_loader.get/compose()`. CLI `metnos-prompts`. Sub-dir lingua secondaria deve avere stesso set di `it/` (boot check).
- **Token-data nei prompt non-Jinja** (§7.11-date): `date_tokens.py::substitute_date_tokens` (`{{current_year}}`/`{{current_date}}`) ai render `.yaml` (`prompt_loader._render_yaml_section`) + manifest (`engine/proposer._render_tool_pool`); manifest col token LETTERALE (no re-sign §7.10).
- **Prompt architecture A+B+C + linter** (§6.1): split `planner.j2` in `_core` + sezioni + `_footer`. Linter `runtime/prompts_lint.py`. Daemon `i18n_translate_pending.py`.
- **Report runtime user-facing i18n** (ADR 0104): chiavi `MSG_*` in `i18n.sqlite` IT+EN.
- **i18n pipeline strutturale** (ADR 0152): subset chiavi nel synt stage 5; daemon `_materialize_auto_synth_stubs` con `auto_translated` flag.
- **Thinking-leak scrubber** (ADR 0102): `_scrub_thinking_leak` in `agent_runtime.py` ramo `final_kind=="answer"`.
- **Describe_entries max_tokens adattivo**: scala con N entries (override esplicito via arg).

**Telos / introspettiva**
- **Alignment Engine v1.3** (ADR 0157): `runtime/alignment_engine.py`. Formula `expected = (α·top + γ·rest)·urgency·confidence - bother_cost`. CLI `--backfill`/`--recompose`.
- **TELOS.md v1.2 (6 telos)** (ADR 0157): pesi 0.25/0.20/0.20/0.15/0.10/0.10. Anti-rinuncia come policy runtime, non pesata.
- **TELOS planner injection**: `telos_loader.render_planner_block(lang)` + slot in `prompts/{it,en}/planner/_footer.j2`. Hot-reload mtime cache.
- **Dashboard `/admin/proposals/telos`** (ADR 0157): triage proposte. Store `runtime/telos_proposals_store.py`. Decisioni JSONL append-only LWW. Cutoff `min_alignment=0.30`.
- **Telos engine 10 lenti laterali** (ADR 0156 v8): `runtime/telos_lenses/` modulare. Framework `_base.run_lens` + SHARED_PREAMBLE/NAMING_SCHEMA/OUTPUT_FORMAT §6. Env per-lens.

**Scheduler / lifecycle / unified changes**
- **Scheduler v2 asyncio co-host** (ADR 0112): `runtime/scheduler_v2/` single Task. Trigger grammar `daily@HH:MM`/`every_N{s,m,h}`/`at:<ISO>`/`cron:<5-field>`. Callbacks via `builtin_callbacks.install_default_callbacks`.
- **Scheduler gate user-activity** (ADR 0074): task notturni age-based sospesi se user idle. Sorgente turns JSONL.
- **Scheduler circuit-breaker** (ADR 0168): N=3 fail consecutivi → auto-disable + notifica owner 3-opzioni; col `consecutive_failures`; `daemon._fire_entry::on_circuit_break` → `recurring_tasks._notify_circuit_break`.
- **Nightly maintenance orchestrator** (ADR 0167 ext): 14 task housekeeping = 1 entry `nightly_maintenance` (daily@03:00), sequenza GPU-safe `nightly_orchestrator.py::run_nightly` (error-isolation §2.8); ordine `NIGHTLY_SEQUENCE`; `install_default_jobs` auto-pulisce le standalone obsolete (idempotente).
- **Async indexing build** (ADR 0093): systemd transient unit. Atomic write + resume checkpoint.
- **Proposals cleanup** (ADR 0096): `runtime/proposals_cleanup.py` 4 op (move + UPDATE, NIENTE delete).
- **Lifecycle summary** (ADR 0097): `runtime/lifecycle_summary.py` aggregatore READ-ONLY ager.
- **Proposal auto-evaluator** (ADR 0122): `proposals_eta_index.py` + `proposal_evaluator.py` 6 killer + 7 signal. CLI `admin.proposals_cli evaluate`.
- **Unified change_intent lifecycle** (ADR 0158): single object/FSM/UI `/admin/changes`. 6 kind. Storage sqlite. Jobs `change_intent_materialize/applier/observer`. Soft-deprecation `/admin/{proposals,promotions}`.
- **Note operative sessione 30/5** (ADR 0167): scheduler builtin (`nightly_aging` 03:30, `state_reaper` 03:40 UNICO; migrate SALTA builtin → `UPDATE schedule_entries`); reaper sempre WIRED (ogni cleanup/sweep/purge/gc ha chiamante reale); engine_proposer pattern H (`classify_entries(dimension=D)`→`filter_entries(where_field=D)`, mai `kind`/`type`). Altri → ADR 0167.

**Multi-user / sync / introvertiva**
- **Multi-user sync** (ADR 0083): `runtime/users_pairings_sync.py` idempotente al boot.
- **Introvertiva quality filters** (ADR 0077): `candidates_specialize` 6 filtri.
- **Describe proposte** : `http_routes_admin._describe_proposal` deterministico, 6 chiavi i18n `MSG_PROP_*`.

**Runtime perf**
- **Runtime perf** (ADR 0099): `fast_path.try_seed_step` + `loader._CATALOG_CACHE` + reasoning budget dinamico PLANNER.
- **Executor parallelism** (ADR 0100+0103): `HostThrottle` + ThreadPoolExecutor su `read_urls_html/pdf` + `compute_files_loc`. Pattern §7.4: misurare prima.

**Project paths / config**
- **PROJECT PATHS** (ADR 0079): `runtime/project_paths.json` mappa progetti → root.
- **Config persistente** (Fase 12): `runtime/runtime_settings.py` + `~/.config/metnos/runtime.toml`. Hierarchy `env > toml > default`.
- **Distribuzione public-subset** (ADR 0145 ext): `/opt/metnos` = baseline completo (`decisions/` TRACCIATO, `docs/` ignorato); repo pubblico = export deterministico `scripts/export-public.sh` (git ls-files − e2e/tests/bench/stress/internal/decisions/docs/CLAUDE.md/binari; IP funzionali→localhost; manifest firmati+`.sig` preservati). Audit `scripts/scrub-scan.sh [--strict]`. ADR/docs NON pubblici.

**PLANNER difese specifiche**
- **PLANNER skip describe after health** (ADR 0111): 4 difese post `get_processes(include_health=true)`. Safety net `_prepend_health_block_if_any`.

**Smoke / E2E / test infra**
- **Smoke battery** (`runtime/smoke.py`, ADR 0114 L5): OBBLIGATORIA pre `./deploy.sh`, post synth, daily, e tocchi a `prefilter.py`/`agent_runtime.py`/`synt_multistage.py`/`loader.py`.
- **E2E driver baseline**: `server._copy_db_with_wal` + `_seed_i18n_baseline` sempre + lint regex `^E*F?$`.
- **Judge prompt safety-aware**: `prompts/{it,en}/e2e_judge.j2` riconosce consensi (signature/mount/sudoer) come ok.
- **Env test-only**: `METNOS_HIDE_EXECUTORS` + `METNOS_LOADER_VERIFY` per E2E che forzano skill imported.
- **`tool_grammar.filter_pool_for_grammar` canonical-aware**: provider-suffixed NON rimosso se canonical equivalente assente (compat HIDE_EXECUTORS).

**Strato 3 escalation**
- **Escalation UI ≥3 ✗** (task #30): `agent_runtime._orchestrate_strato3_escalation` early-exit; dialog 4-choice → `strato3_choice_dispatch` (`orchestration.py`); strati 1+2 `_render_rejected_pipelines_block`.

## 11. Decisioni di runtime

- **LLM tier**: 4 tier (fast/middle/wise/frontier). **SoT canonica**: `runtime/llm_router.py::DEFAULT_TIERS` + ADR 0146 (consolidamento 18/5/2026). I tre tier locali (fast/middle/wise) puntano tutti allo stesso `llama-server :8080` (Gemma 4 26B + drafter E2B speculative); la differenza fra tier sono i parametri per-call (`think`, `num_predict`). frontier = Anthropic Opus 4.7 opt-in. Niente piu' `qwen3:8b` (ADR 0044 superseded da 0106+0146).
- **Tool-use protocol**: nativo Ollama+Qwen+Gemma (tool_calls strutturati). NIENTE parser JSON fragile.
- **Data piping**: `from_step: int` (schema-guided) + `{{stepN.field}}` per scalari.
- **Intent extractor**: LLM-based gemma 4 26B middle, ~370ms/query, 100/100 su test corpus. Fallback bag-of-words. Bypass deterministico per undo. Compound → lista ordinata `actions=[{verb,object}]` per clausola (routing pool per-clausola in dispatch, no dizionari sinonimi).
- **Universal helpers**: `classify_entries`, `filter_entries`, `extract_entries`, `undo_last_turn` sempre. `describe_entries` SOLO se intent.verb NOT in action_verbs. `extract_entries` (builtin inproc `runtime/extract_entries.py`, ADR-pending): testo non strutturato→record tipizzati via LLM (1:N), campi-data in ISO 8601; pipeable verso create_events/*_spreadsheet. Confine `extract` allargato §2.2.
- **Reverse patterns**: `runtime/reverse_patterns.py` — 5 entry deterministiche (vedi §2.3).
- **Platform policy**: `runtime/platform_policy.py` — system files cross-mount-safe + protected paths host-aware.
- **Messaggi**: `runtime/messages.py` — dizionario unico code→template `ERR_*/WARN_*/MSG_*/LOG_*`. Mai stringhe duplicate negli executor. **REGOLA (2026-05-29)**: ogni messaggio user-facing DEVE risolversi via i18n DB (`_msg`/`messages.get`) nella lingua dell'istanza (`METNOS_LANG`, `i18n.current_lang()`); VIETATE stringhe user-facing hardcoded, inclusi gli errori di validazione-arg (l'utente li vede su pipeline malformata). Eccezione: `LOG_*`/diagnostica interna mai mostrata. Builtin=multilang obbligatorio+re-sign; synth/imported=lingua utente (vedi memoria i18n-scope-by-executor-class).

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

- ADR registry: `decisions/` (`0001-0169`, `0055`/`0115`/`0116`/`0121` skipped) — dettagli implementativi e razionale.
- Architettura canonica: `docs/it/architecture/` (+ EN bridge simmetrico).
- Memorie persistenti: `~/.claude/projects/-opt-myclaw/memory/MEMORY.md` (path Claude harness, indipendente dal rename Metnos).
- Repertorio prompt: `runtime/prompts/<lang>/*.j2` (ADR 0092).
</content>
</invoke>