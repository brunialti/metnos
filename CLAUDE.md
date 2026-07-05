# Metnos — Linee guida di progetto

> **OBBLIGO**: leggere integralmente all'inizio di ogni sessione. Codifica decisioni architetturali, convenzioni di codice e norme di processo. Punto obsoleto/errato → AGGIORNA subito.
>
> Mantenuto da: agente. Aggiornamento quando si fissa una nuova norma duratura. Storia in `git log CLAUDE.md`. Dettagli implementativi vivono negli ADR (`decisions/`), non qui.
> Ultimo: 2026-07-05 v22h (§11: **correzione prod = `METNOS_ENGINE=v3`** — NON metis; il drop-in proposer-hardening imposta v3, i guard compound sono v3-gated, i bench compound portati a v3. Compound «search/list X → build spreadsheet» a **errore=0** — turni 697d1d08/bb977a14: write spurio, read-decontam, exact-name find+read, extract su list[list] nel CONSUMER, provider-sink clause-scoped, drive search trashed=false; WARN runtime testa-manifest over-budget). v22g (§11: **igiene filiera proposte + regola dei livelli operativa** ADR 0180 — ritiro generatori specialize/generalize (introvertiva=solo dedupe) e adapter canonical/multi_tool (store senza scrittore); killer `layer_overlap` nell'auto-evaluator; adapter Telos a cluster-head; **accept di una pipeline = eseguirla una volta** in scheduled-scope; alignment v1.4 fit con segno; bonifica pending ~250→30). v22f (§11: **auto-composizione compound + disciplina cache** ADR 0174 — guard deterministici align/enforce anche sugli hit cache L0/L1, `_compute_intent_sig` compound-aware + object-boundary path-2, normalizzazione clausole store→entries pre-cache; grammar-on-verbs accantonata). v22e (§11: glossario livelli/unità a 4 termini + **rename unità L1 «skill»→«autopath»** — tabelle `autopaths`/`anti_autopaths`, migrazione DB preserva-dati; skill=bundle ADR 0170 invariato). v22d (§1/§2.5/§11: bonifica modello locale **Gemma 4 26B → Qwen 3.6 35B-A3B** Q4_K_M/MTP, allineato a `llm_router.DEFAULT_TIERS`+README; frontier=Opus opt-in, rimosso «GPT-5»). v22c (§11: **routing DETERMINISTICO** — seed fisso `METNOS_LLM_SEED` + affinity-match boost prefilter + guard su proposer Metis di prod; bonifica manifest §2.5 completata, 0 legacy). v22b (manutenzione: §10.6 indice meccanismi estratto in `decisions/anti-regression-index.md`, CLAUDE.md 407→272 righe). v22 (ADR 0170: tassonomia skill 3-tier + confine skill↔backend ortogonale + mono→multi provider; google-workspace vendorizzata Tier-2; 5 executor resi reversibili §2.3). v21: norma «Distribuzione public-subset». Norme correnti negli ADR 0150-0170; changelog completo in `git log CLAUDE.md`.

---

## 1. Cos'e' Metnos

Assistente personale self-hosted (su `.33`, Strix Halo 96GB unified). Microarchitettura a executor sintetizzati al volo via synt multistage; runtime ReAct con planner LLM (Qwen 3.6 35B-A3B fast/middle/wise locale + Opus frontier opt-in come fallback). Canali: **Telegram** + **HTTP porta 8770** (htmx + Jinja2 + uPlot, ADR 0078). Pipeline immagini in-process: SigLIP-base + RetinaFace+ArcFace + EXIF (ADR 0086/0117). Lingua principale: italiano; corpus doc bilingue IT+EN. Etimologia: `mētis + noûs`. Process name: `myclaw`. Dominio: `metnos.com`.

ADR registry canonico: `decisions/` (relative alla repo root; `0001-0180`, `0055`/`0115`/`0116`/`0121` skipped — fonte unica per "perche' abbiamo scelto cosi'").

## 2. Principi cardine (mai negoziabili)

### 2.1 Executor vettoriali per costruzione
Input come **lista** (paths, entries, urls, ids). Output **sempre lista**, anche degenere (N=0/1/molti). Niente `*_batch`: la versione batch *e'* l'executor. Iterazione interna (loop, paginazione, window come `time_window: today|last-Nd`); branching su osservazione torna al planner. Cap inferiore = 0; cap superiore = parametro esplicito (`max_total`, `max_results`, `max_bytes`).

### 2.2 Naming convention compositiva
Struttura: `azione_oggetto[_qualifier]`. Vocabolario CHIUSO (escalation a Roberto per nuovi termini), centralizzato in `runtime/vocab.py`.

- **23 azioni**: `read, write, move, delete, create, find, list, filter, sort, group, classify, get, set, send, describe, render, extract, compress, compute, compare, change, order, share`.
- **Ortogonalita' 5 verbi-produttori** (asse «input primario»): `find`=pattern/query; `get`=id noti o snapshot; `read`=id→contenuto; `list`=container enum senza contenuto; `filter`=lista preesistente, predicato. `change`=forma/parametri; `order`=ordinamento PERSISTENTE corpus (vs `sort` in memoria); `share`=OUTBOUND CONSENT grant ACL remoto (ADR 0128).
- **Importer verb boundary** (ADR 0128): mapping provider→Metnos in `runtime/skill_vocab_map.json::contextual`. Verifier `runtime/importer_verb_verify.py::check_plan` (layer 6.bis ADR 0114).
- **Confini stretti**: `fetch` rimosso (HTTP GET=`get_urls`); `extract` = STRUTTURA incapsulata in un contenitore (allargato 3/6, ADR-pending): (1) archivi zip/tar/gz=`extract_files`; (2) **record strutturati da testo NON strutturato** (web/mail/pdf)=`extract_entries` (es. eventi {summary,start,end}); «estrai righe testo»=`filter_texts_lines`; «estrai testo GREZZO PDF/HTML»=`read_files_{pdf,html}`; «estrai campi da entries GIÀ strutturate»=`get`.
- **23 oggetti**: `files, dirs, packages, messages, events, calendars, contacts, places, processes, urls, numbers, images, signatures, texts, proposals, persons, tasks, inputs, credentials, issues, pulls, entries, approval`. Eccezioni: `get_inputs`={values} UI (ADR 0090); `*_credentials` metadata-only (ADR 0123); `entries` meta-oggetto in-memory; `persons` vs `contacts` (ADR 0113/0137); `tasks` (scheduler v2) vs `events` (ADR 0112/0137); `calendars` vs `events` (provider google_workspace: create/delete_calendars ADR 0123/0136); `issues`/`pulls` provider github (ADR 0141); `approval` gate di consenso umano cross-skill (get_approval, 17/6/2026).
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
Manifest TOML = "prompt del tool" per il modello locale medium (Qwen 3.6 35B-A3B, NON Sonnet/Opus). Modello canonico: `executors/find_files/manifest.toml`. Criteri: description 2-5 frasi corte (max 25 parole/frase); esempi tra virgolette per formati non ovvi (`time_window="last-24h"`); default in chiaro; niente gergo Python; affinity 8-15 termini IT+EN user-facing; args 1 frase + tipo + esempio + default. Anti-pattern: description 800 parole, prosa colloquiale ("DEVI usarla per operazioni su X"), pattern-by-example senza separatore "non copiare letteralmente".

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
- **ENROLLMENT/ENROLLATO** («enroll/enrollment/enrollement/iscrizione biometrica») → dominio `*_persons`: elenco enrollati = `get_persons()` (final = `final_message_hint`, NIENTE describe); «cancella l'enrollment di X» = `delete_persons(names=["X"])`. MAI `*_credentials` (token/password servizi).
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

Ogni `.j2` dichiara la tipizzazione in un frontmatter Jinja `{# --- ... --- #}` con 8 campi (`role`, `tier`, `lang`, `style`, `version`, `owner`, `updated`, `sha_prev`). Il linter `runtime/prompts_lint.py` (indice §10.6) applica i check §6 SOLO a `style: prescriptive`.

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

> **Indice completo estratto** → `decisions/anti-regression-index.md` (~100 meccanismi, una riga ciascuno con call-site + ADR). Aggiornare LÌ, non qui, quando si aggiunge/rimuove un meccanismo. Regola d'oro invariata: una riga per meccanismo, call-site essenziale; dettagli/env/bench/date → ADR.

### 10.7 Installer: leggere INSTALL_NOTES prima di toccarlo
OBBLIGO: prima di modificare QUALSIASI file sotto `install/`, leggere `install/INSTALL_NOTES.md` (contratto env install↔runtime, tier pure-abstract, seed dati = i18n/embedder/executor, regole onestà/i18n/UI). L'installer deve **replicare un ambiente di esercizio completo e funzionante** da un checkout git, non solo far bootare il server: validare ogni modifica con l'harness isolato (utente dedicato + clone + porta alt) e un turno reale (`kind=answer`), mai con "è partito". Aggiornare INSTALL_NOTES quando cambia un contratto.

## 11. Decisioni di runtime

- **Glossario livelli/unità** (terminologia FISSATA 12/6, rename eseguito 14/6): **fastpath = L0** — cache della STESSA query (hash 0a + coseno 0b); può tenere args concreti query-specific. **autopath = L1** — piano GENERALIZZATO per un cluster (scheletro/framework senza args, promosso dal feedback ✓ umano esplicito — `MIN_OBS_PROMOTE`, default 1); store `autopath.sqlite` tabelle `autopaths`/`anti_autopaths` (rinominate da `skills`/`anti_skills`). **executor** = singolo tool sintetizzato (`verb_object`, manifest+`.py` firmato). **skill** = INSIEME di executor (bundle/capability, ADR 0170; es. gmail = read/send/move_messages) — **NON** il piano L1. Vietato usare «skill» per il piano L1 (collide col bundle): nel codice L1 = `autopath` ovunque.
- **LLM tier**: 4 tier (fast/middle/wise/frontier). **SoT canonica**: `runtime/llm_router.py::DEFAULT_TIERS` + ADR 0146 (consolidamento 18/5/2026). I tre tier locali (fast/middle/wise) puntano tutti allo stesso `llama-server :8080` (Qwen 3.6 35B-A3B Q4_K_M, MTP speculative; era Gemma 4 26B fino a 6/2026); la differenza fra tier sono i parametri per-call (`think`, `num_predict`). frontier = Anthropic Opus 4.7 opt-in. Modello locale virtualizzato in `DEFAULT_TIERS` (no nome hardcoded altrove); storico: qwen3:8b (ADR 0044) → Gemma 4 26B → Qwen 3.6 35B-A3B (superseded 0106+0146).
- **Tool-use protocol**: nativo Ollama+Qwen+Gemma (tool_calls strutturati). NIENTE parser JSON fragile.
- **Routing DETERMINISTICO** (8/6/2026): il routing (intent→prefilter→proposer wise) e' deterministico per costruzione. (1) **seed fisso** in `llm_provider.LlamaCppProvider.chat` (env `METNOS_LLM_SEED`, default 42; `-1`=random): a temperature=0 il llama-server resta non-deterministico per MTP/speculative col seed random — pinnarlo rende il routing riproducibile. (2) **affinity-match boost** nel prefilter (`prefilter.rank_with_intent`): i token-query che matchano l'affinity DISTINTIVA del tool (dato curato; split hyphen; esclusi i verbi generici `_GENERIC_AFFINITY_VERBS`) rompono i PAREGGI fra fratelli stesso-object (cap +3, §7.9 deterministico). (3) **proposer di PRODUZIONE = engine v3** (MetisV3, drop-in swappable con metis) + grammar+verb_filter ON via drop-in `/etc/systemd/system/metnos-http.service.d/proposer-hardening.conf` (`METNOS_ENGINE=v3`, verificato sul MainPID 5/7): i guard compound (`_ensure_extract_clause`/`_conform_to_intent_order`/`_fill_clause_args`/`_decontaminate_reader_qualifier`/`_scope_sink_provider_to_clause`) sono **v3-gated** (`is_v3()`), quindi i bench DEVONO girare con `METNOS_ENGINE=v3` per riflettere prod — girare `metis` misura un path morto (i bench compound portati a v3 il 5/7). Bonifica manifest §2.5 completata su questa base (0 legacy).
- **Describe DETERMINISTICO** (12/6/2026): il TESTO di `describe_entries` e' byte-riproducibile per costruzione — `call_llm(deterministic=True)` → processo `llama-completion` monouso (stesso GGUF via `/props`, template via `/apply-template` enable_thinking=false, temp=0 + seed §11). Il llama-server condiviso NON e' riproducibile a parita' di richiesta (stato interno di processo: logits ±0.1, cross-backend; seed/slot pinnato/KV erase NON bastano). NIENTE cache/template del contenuto: generazione LLM piena sui dati correnti; fallback HTTP onesto con `meta.deterministic=false`. Gate `METNOS_DESCRIBE_DETERMINISTIC` (default ON). Guard `runtime/tests/test_describe_determinism.py`.
- **Auto-composizione compound + disciplina cache** (18/6/2026, ADR 0174): i guard deterministici di struttura (`_align_framework_objects`+`_enforce_missing_clauses`) girano anche sugli HIT cache L0/L1 (`_apply_deterministic_structure_guards` in `engine/dispatch.py`), non più solo su L3 — un piano compound cachato read-only verrebbe altrimenti eseguito scavalcando i correttori. `_compute_intent_sig` (autopath) è compound-aware (hash di TUTTE le `actions`, back-compat mono) + object-boundary anche su path-2. Le clausole STORE dell'intent sono normalizzate a `entries` PRIMA di pool/cache (`_normalize_store_clauses` + detection_lexicon `object.store_sink`), tool-existence-safe (flippa solo clausole non-routabili). `grammar-on-verbs` accantonata (enforce+align bastano).
- **Igiene filiera proposte + regola dei livelli operativa** (2/7/2026, ADR 0180): generatori che violano la regola dei livelli RITIRATI — `specialize` (default-in-arg = compito di L0) e `generalize` (le catene reali le imparano L1 autopath e promoter ETA); introvertiva = solo `dedupe`. Adapter change_intent attivi: telos (a CLUSTER-HEAD, score=cluster_score, solo azionabili), introvertiva, synt, user_feedback — `canonical` e `multi_tool` ritirati (store senza scrittore). **Accept di una `materialize_pipeline` = ESEGUIRLA una volta** come turno reale in `scheduled_turn_scope` (consenso outbound + guardie attive): se funziona, L0/L1 imparano dal turno vero. Auto-evaluator: killer `layer_overlap` (default-bake→«superseded by L0»; multi-step non highly-requested→«covered by L1», solo con evidenza). `alignment_engine` v1.4: fit ∈ [-1,1], il conflitto fra telos sottrae senza gate.
- **Path di planning compound UNICO = engine** (24/6/2026, ADR 0177 D1): il DECOMPOSER deterministico (`compound_decomposer.decompose_query`) è stato ELIMINATO. Era un pre-stadio che decomponeva i compound (≥2 verbi) in step senza LLM, mitigatore del cold-start engine; divergeva dall'engine (S1) e ne mascherava i bug §2.8. Il bake `METNOS_DECOMPOSER=0` + il fix onestà `a139dcd` hanno provato che l'engine (proposer + cache L0/L1 + guard) copre il caso generale ed è onesto su extract→create. **Restano** in `compound_decomposer.py` gli helper condivisi (`PRODUCER_VERBS`, `derive_tool_name`, `split_query_chunks`, `derive_extract_fields`, `_send_has_explicit_recipient`) usati dai guard dell'engine — il modulo NON è più un planner. Niente flag, niente fallback: engine-only.
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

Server `runtime.metnos_http_server` su porta **8770** (separata da 8765 pairing). Stile aiohttp bare uniforme: ROUTES come tuple list, helper `_error()`, middleware `auth_middleware`. Tre ruoli: anonymous/user/admin. Admin key `~/.config/metnos/admin.key` (mode 0600), 256-bit hex, fingerprint sha256 nei log. Endpoint `/agent/{health,turn,devices/me}` + `/.well-known/metnos.json` + `/admin/{,changes,executors,executors/stats,runs,safety,turns}` (le route `/admin/proposals*` sono state sostituite dalla vista unificata `/admin/changes`, ADR 0158, 13/6/2026). Negotiation `Accept: text/html` (htmx + Jinja2 + uPlot CDN, no build step) vs JSON. ETag su collezioni admin. SSE su `/agent/turn` se `Accept: text/event-stream`. Vedi ADR 0078.

---

**Riferimenti**

- ADR registry: `decisions/` (`0001-0180`, `0055`/`0115`/`0116`/`0121` skipped) — dettagli implementativi e razionale.
- Architettura canonica: `docs/it/architecture/` (+ EN bridge simmetrico).
- Memorie persistenti: `~/.claude/projects/-opt-myclaw/memory/MEMORY.md` (path Claude harness, indipendente dal rename Metnos).
- Repertorio prompt: `runtime/prompts/<lang>/*.j2` (ADR 0092).
</content>
</invoke>