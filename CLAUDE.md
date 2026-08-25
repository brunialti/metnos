# Metnos — CLAUDE.md · PARTE INVARIANTE

> **OBBLIGO a inizio sessione**: leggere QUESTO file e @CLAUDE.mutabile.md (stato corrente + decisioni di runtime).
>
> **GOVERNANCE** (Roberto, 7/7/2026) — Questo file è **INVARIANTE**: lo modifica SOLO Roberto, o l'agente su sua istruzione esplicita e puntuale, MAI di iniziativa. Regola che sembra obsoleta o contraddetta dal codice: NON toccarla, segnalarla con una proposta. `CLAUDE.mutabile.md` è **MUTABILE**: lo manutiene l'agente. La numerazione `§` è CANONICA (citata in codice/ADR/memorie): **mai rinumerare** — perciò i blocchi qui sotto stanno in ordine di priorità, non di numero. §3-§5, §11-§12, §14 vivono nella parte mutabile. Storia: `git log CLAUDE.md` / `git log CLAUDE.mutabile.md`.

---

## 1. Cos'e' Metnos

Assistente personale self-hosted. Microarchitettura a executor sintetizzati al volo via synt multistage; runtime ReAct con planner LLM locale + frontier opt-in. Canali: **Telegram** + **HTTP porta 8770** (htmx + Jinja2 + uPlot, ADR 0078). Pipeline immagini in-process (ADR 0086/0117). Lingua principale: italiano; corpus doc bilingue IT+EN. Etimologia: `mētis + noûs`. Process name: `metnos`. Dominio: `metnos.com`. Config corrente (host, modelli, tier): parte mutabile.

ADR registry canonico: `decisions/` — fonte unica per "perche' abbiamo scelto cosi'". Range corrente: parte mutabile.

## 7. Coding standards

I primi quattro valgono per OGNI azione, non solo per il codice.

### 7.12 Risposte SUCCINTE (norma esplicita di Roberto, rafforzata 7/7/2026)
- DEVI: default stringato — risposta media max 3-5 righe, esito PRIMA di tutto, tabelle compatte ok.
- NON DEVI: preamboli, riepiloghi non richiesti, narrazione del processo, opzioni non richieste, commentario dopo edit (solo risultato).
- Dettaglio esteso SOLO: su richiesta esplicita, come report conclusivo di un lavoro lungo, o quando l'omissione costerebbe un malinteso.

### 7.2 Semplicita' prima di tutto
A parita' di risultato: scegli sempre la soluzione piu' semplice, leggibile, lineare, modulare.

### 7.3 Soluzioni generali, mai hardcoded
Daily test = scoperta di soluzioni, non check di non-errori. Detection sintomatico come safety net, ma il fix deve generalizzare la causa.

### 7.9 Codice deterministico > LLM
**Codice deterministico > LLM se equipotente, equiefficace o se codice deterministico [sarebbe] troppo complesso.** LLM solo quando deterministico e' inefficace, troppo complesso da scrivere/mantenere, o impossibile. Anti-pattern: LLM per validare/classificare cose che `vocab.py` o un regex coprono.

### 7.1 Niente backward compatibility in dev (pre-1.0)
Rompi pure API/firme/default quando il nuovo design e' migliore. Niente shim/legacy/parametri solo-per-compat.

### 7.4 Niente parallelismo se non c'e' speedup reale
Subagent paralleli/batch solo se riducono wall-time. Bottleneck GPU/IMAP/IO seriale → default seriale.

### 7.5 Niente nomi propri di terzi
Solo "Roberto" o generici (guest, ospite, familiare invitato). Vale anche nella documentazione (ex §9.4).

### 7.6 Niente lettere greche nelle opzioni
`(a)/(b)/(c)` o numeri, mai lettere greche.

### 7.7 Niente `_batch` come suffisso
Vedi §2.1.

### 7.8 Italiano senza anglicismi
Caccia ad anglicismi (peer, trigger, goal, plumbing, gate) e calchi (costosa/mordere/ci reagisce).

### 7.10 Pubblicazione executor dopo edit
Edit di `<executor>.py` O del solo `manifest.toml` → OBBLIGATORIO `python3 runtime/sign.py publish executors/<name>` (dalla radice del repository; `python -m runtime.sign` NON funziona) + riavvio controllato del servizio; committare codice, manifest e firma INSIEME. `publish` ricalcola il digest, firma una nuova generazione immutabile e la rende attiva in un solo passaggio. `sign` serve soltanto alla preparazione offline: non aggiorna la generazione attiva e quindi non rende viva la modifica.

### 7.11 No path assoluti hardcoded (rename-resilient)
Niente `Path("/opt/...")` verso la install root nel codice attivo. Root auto-derivata in `runtime/config.py::PATH_ROOT`; ogni callsite usa `from runtime import config as C` → `C.PATH_*`. Override env `METNOS_INSTALL_ROOT`. ADR 0148.

### 7.13 Messaggi user-facing via i18n
Ogni messaggio user-facing DEVE risolversi via i18n DB (`_msg`/`messages.get`) nella lingua dell'istanza (`METNOS_LANG`); VIETATE stringhe user-facing hardcoded, inclusi gli errori di validazione-arg. Eccezione: `LOG_*`/diagnostica interna. Builtin=multilang obbligatorio+re-sign; synth/imported=lingua utente.

## 6. Stile prompt (prescrittivo)

Ogni regola che istruisce un comportamento al LLM DEVE seguire 4 punti, in quest'ordine:

```
DEVI: <verbo imperativo + azione>.
NON DEVI: <verbo imperativo + azione vietata>.
OK: <esempio positivo, una riga>.
ERRORE: <esempio negativo, una riga>.
```

Max 4-5 righe per regola. `DEVI`/`NON DEVI` maiuscolo. `E' UN ERRORE` come marker. Niente `se possibile`/`preferibilmente`. Niente prosa "perche'". Esempi pattern-by-example richiedono separatore esplicito "non copiare letteralmente".

### 6.1 Tipizzazione del prompt
Tre stili disgiunti per i `.j2` di `runtime/prompts/<lang>/`: `prescriptive` (planner, describe, classify — applica §6), `definitional` (synt stages, intent_extractor, vaglio), `few_shot` (addendum verbo stage 5). Ogni `.j2` dichiara la tipizzazione nel frontmatter Jinja a 8 campi. Il linter `runtime/prompts_lint.py` applica i check §6 SOLO a `style: prescriptive`.

## 2. Principi cardine (mai negoziabili)

### 2.8 No silent failure
Mai dichiarare un esito che non corrisponde alla realta'. `ok_count` = elementi REALMENTE processati. Undo onesto.

### 2.1 Executor vettoriali per costruzione
Input come **lista** (paths, entries, urls, ids). Output **sempre lista**, anche degenere (N=0/1/molti). Niente `*_batch`: la versione batch *e'* l'executor. Iterazione interna (loop, paginazione, window come `time_window: today|last-Nd`); branching su osservazione torna al planner. Cap inferiore = 0; cap superiore = parametro esplicito (`max_total`, `max_results`, `max_bytes`).

### 2.2 Naming convention compositiva
Struttura: `azione_oggetto[_qualifier]`. Vocabolario CHIUSO (escalation a Roberto per nuovi termini), centralizzato in `runtime/vocab.py` (SoT di azioni/oggetti).

- **Ortogonalita' 5 verbi-produttori** (asse «input primario»): `find`=pattern/query; `get`=id noti o snapshot; `read`=id→contenuto; `list`=container enum senza contenuto; `filter`=lista preesistente, predicato. `change`=forma/parametri; `order`=ordinamento PERSISTENTE corpus (vs `sort` in memoria); `share`=OUTBOUND CONSENT grant ACL remoto (ADR 0128).
- **Importer verb boundary** (ADR 0128): mapping provider→Metnos in `runtime/skill_vocab_map.json::contextual`. Verifier `runtime/importer_verb_verify.py::check_plan` (layer 6.bis ADR 0114).
- **Confini stretti**: `fetch` rimosso (HTTP GET=`get_urls`); `extract` = STRUTTURA incapsulata in un contenitore: (1) archivi=`extract_files`; (2) **record strutturati da testo NON strutturato** (web/mail/pdf)=`extract_entries`; «estrai righe testo»=`filter_texts_lines`; «estrai testo GREZZO PDF/HTML»=`read_files_{pdf,html}`; «estrai campi da entries GIÀ strutturate»=`get`.
- **Eccezioni oggetti**: `get_inputs`={values} UI (ADR 0090); `*_credentials` metadata-only (ADR 0123); `entries` meta-oggetto in-memory; `persons` vs `contacts` (ADR 0113/0137); `tasks` (scheduler v2) vs `events` (ADR 0112/0137); `calendars` vs `events` (ADR 0123/0136); `issues`/`pulls` provider github (ADR 0141); `approval` gate di consenso umano cross-skill.
- **System verbs riservati**: `undo`, `admin` fuori dai canonici. Builtin runtime only. Stage 1 NAMING non li propone.
- **Qualifier opzionali, 4 famiglie**. Elenco corrente: `vocab.QUALIFIERS` — mai copiarlo qui, una copia si scosta (19/8/2026: ne mancavano 16). **Formato** (estensione/codifica). **Modalita'**: operazione (`_size`, `_similar`, `_empty` — cross-domain con arg `size` unit-aware, ADR 0127), granularita' (`_lines`, `_pages`), persistenza (`_indices`). **Safety policy**: `signatures`, ADR 0071. **Provider** (ADR 0136): backend non-default (`_google_workspace`), default `_metnos` omesso, filtro pool via `tool_grammar._PROVIDER_SUFFIX_MARKERS`, dormant se no creds.
- **4° livello descriptor** (ADR 0156): `verb_object[_qualifier[_descriptor]]` posizionale, separatore `_`. Descriptor kebab-case `[a-z0-9]+(-[a-z0-9]+)*` max 30. (R1) descriptor solo se qualifier presente; (R2) un livello alla volta; (R3) descriptor = modificatore comportamentale a parita' args. OK `_dry-run`/`_v2`/`_unified`; ERRORE `_nightly` (→`tasks`). Enforce via `naming_grammar.validate_name` + GBNF.
- **Governance vocab** (ADR 0156): nuovo token richiede 3 criteri congiunti: (1) **necessario**; (2) **generale**; (3) **comprensibile** dal modello locale medio senza spiegazione. Proposte introspettive che richiedono estensione DEVONO marcare nel rationale "RICHIEDE estensione vocab §2.2: <criteri>".

Stage 1 di synt usa MAPPING bilingue IT+EN. Sinonimi prima dell'estensione del vocabolario.

### 2.3 Reverse pattern catalogo deterministico
Manifest dichiara `reverse_pattern` da catalogo chiuso: `swap_src_dst`, `delete_created_dirs`, `delete_created_paths`, `restore_blob_backup` (richiede `blob_path`+`blob_sha256` nei results), `delete_<object>_by_id` (ADR 0123: contratto `<object>_ids` + scope id). Blob: `$METNOS_HISTORY_DIR/<METNOS_TURN_ID>/blob/<sha256>.bin`.

### 2.4 Robustezza al confine NL→determinismo
Executor accetta: `0-as-placeholder` (cap=0 → no limit), compound case-insensitive di default, args plurali ammessi (`paths` accetta anche 1 elemento). Helper comuni in `runtime/executor_helpers.py`. Niente patch reattive per-executor.

**Convention args `array of string`**: dominio deciso a design-time e dichiarato nel manifest. **Aperto** (testo libero) → wildcard via `fnmatch` ("valori con `*`/`?` = glob"). **Chiuso** (enum/slug/ID/scope/email) → MATCH ESATTO. L'LLM ha bias verso glob universali: fnmatch sull'aperto evita il fallimento silenzioso, il match esatto sul chiuso ne impedisce l'abuso.

### 2.5 Manifest leggibili da LLM medium
Manifest TOML = "prompt del tool" per il modello locale medio (NON frontier). Modello canonico: `executors/find_files/manifest.toml`. Criteri: description 2-5 frasi corte (max 25 parole/frase); esempi tra virgolette per formati non ovvi; default in chiaro; niente gergo Python; affinity 8-15 termini IT+EN user-facing; args 1 frase + tipo + esempio + default.

**FORMATO `[description]` a CAPITOLI** (REGOLA UNIVERSALE): 4 capitoli prescrittivi, in quest'ordine:
```
SCOPO: <1 frase>. PATTERN: <chiamata canonica literal>. NON: <anti-pattern + disambiguazione vs tool simili>. OUT: <shape output pipeable>.
```
`SCOPO`+`PATTERN` front-loaded (il proposer li estrae fino a `OUT:`). Vale per ogni manifest NUOVO e ogni VECCHIO toccato per un fix; **NON** rifattorizzare in massa. Boundary verbo §2.2 nel capitolo `NON:`. Esempio: `executors/write_files/manifest.toml`.
**Multilingua** (ADR 0092): `[description]` tabella per lingua + companion `manifest.lang_state.json`.

### 2.6 Output naming consistency
Executor che **arricchiscono/leggono** una lista → ritornano `entries`. Executor **trasformativi** (move/write/delete) → ritornano `results`.

### 2.7 Truncation visibility cross-executor
Cap raggiunto = `truncated: true` + `truncated_what` + `used: int` + `available_total: int` quando noto. Runtime prepende notice. **Visibility vs notify** (ADR 0062): `truncated_intentional: true` = cap user-richiesto, runtime mostra "1-3 di 27" ma NON propone allargamento.

### 2.9 Move never implicit delete
Per ogni move: destinazione specificata, verifica esistenza, crea-se-manca, COPY-check-then-DELETE. Mai DELETE senza COPY confermata.

### 2.10 Coerenza I/O fra executor in pipeline
Se il consumatore si aspetta `entries`, il produttore ritorna `entries`. Cambio nome solo quando lo schema dei record cambia.

### 2.11 Cap/budget exhaustion: notify then ask
Cap raggiunto → mai silenzio o parziale presentato come completo. Due fasi: (1) **notify** con campi §2.7; (2) **ask** se l'allargamento e' possibile, attendere conferma. Mai allargamento implicito. Esporre anche `cap_field` + `cap_value`; `available_total` via sondaggio post-cap quando possibile.

## 8. Test e convergenza

### 8.2 Test fallisce → fix codice, NON il test
Modifica del test SOLO se il test stesso e' sbagliato. Distinzione: bug codice / prompt / matcher infra / test case.

### 8.5 Convergence loop su input utente reali
Test → fail → fix codice → riprova → 0 errori. NON modificare la query utente per farla passare. E2E: ogni cambio a codice di prodotto richiede ≥1 turno reale `/agent/turn` sul dominio toccato.

### 8.1 Protocollo iterare-test-cluster
Per ogni modifica modulo: aggiornare DB test → eseguire module + cluster → iterare finche' verde. Test rotti = iterare, mai disabilitare.

### 8.3 No manual rewrite executor → itera prompt synt
Bug in executor SINTETIZZATO = fix nel prompt synt, non nel file output. (Gli handcrafted/core si editano normalmente, con §7.10.)

### 8.4 Stratificazione test synt
Smoke 6q/15min — dopo ogni fix prompt. Sanity 10q/25min — dopo refactor stage. Full 35q/80min — SOLO per cambi strutturali.

### 8.6 No daemon restart during turn
Non riavviare i servizi durante un turno utente attivo (cambia PID, perde state).

## 10. Workflow operativo

### 10.8 Comando interrotto → verificare sempre il residuo
Uccidere un comando non uccide i suoi figli: su timeout, interruzione o task `failed`/`killed`, il figlio (`python3 -`, uno sweep, un `uv run`) sopravvive riadottato da init e gira a core pieno fino al reboot, invisibile a ogni sessione.

OBBLIGO dopo ogni interruzione, e prima di chiudere la sessione:

    ps -eo pid,ppid,pcpu,etime,args --sort=-pcpu | awk 'NR==1 || ($2==1 && $3>50)'

PPID=1 + un comando nostro (`python3 -`, roba sotto `/tmp/claude-*/**/scratchpad/`) = residuo: conferma con `tr '\0' ' ' < /proc/<pid>/cmdline`, poi `kill -9 <pid>`. Mai lasciarlo "per sicurezza".

Prevenzione: analisi lunga = `timeout <s>` esplicito + tetto interno (file, byte per file). Regex su testo arbitrario senza alternanze annidate sotto quantificatore: `^(?:A|B|C)+$` che non matcha = backtracking esponenziale. Storico: 16/08/2026, sweep su /opt/metnos → 69 h a 100% di un core, Tctl 83 °C, scoperto il 19/08.

### 10.7 Installer: leggere INSTALL_NOTES prima di toccarlo
OBBLIGO: prima di modificare QUALSIASI file sotto `install/`, leggere `install/INSTALL_NOTES.md`. L'installer deve replicare un ambiente di esercizio completo e funzionante; validare con l'harness isolato + un turno reale, mai con "e' partito". Aggiornare INSTALL_NOTES quando cambia un contratto.

### 10.2 Manifest authoring
L'agente scrive i manifest TOML di iniziativa; Roberto rivede. Per nuove decisioni di design, fermarsi e chiedere.

### 10.5 Memorie persistenti
Tipi: `user`, `feedback`, `project`, `reference`. Indice in `~/.claude/projects/-opt-metnos/memory/MEMORY.md` (max ~150 char/riga, ~200 righe). **Session log pattern**: ogni sessione multi-step crea/aggiorna `project_session_<DD_M_YYYY>.md`; entry top in MEMORY.md marcata ⏰ "LEGGI PRIMA"; vecchi session log eliminati a fine sessione.

### 10.1 Mailbox via script
- `user@example.com` (register.it) → `scripts/check-mail.sh`.
- `metnos@metnos.com` (Migadu) → curl IMAPS con env in `~/.config/metnos/mail.env`. Spam = `Junk`.
- Mai `curl` ad-hoc con password inline.

### 10.3 Open-source self-hosted come default
SearXNG, Nominatim, Tesseract, Piper, whisper.cpp, sqlite. SaaS solo come fallback (LLM frontier, mail outbound).

### 10.4 Self-contained client binary
UN file scaricabile e eseguibile. Niente runtime/lib da installare sul target. Static-link libgcc/libstdc++/winpthread.

### 10.6 Meccanismi anti-regressione (indice)
Indice completo → `decisions/anti-regression-index.md` (una riga per meccanismo, call-site essenziale). Aggiornare LI', non qui. Dettagli/env/bench/date → ADR.

## 9. Documentazione

### 9.1 Architettura sempre allineata al codice
Ogni chiusura fase / promozione doc / aggiunta capacita' / cambio numeri → update immediato di `docs/{it,en}/architecture/*.html` + `decisions/<ADR>.md`. Niente backlog "doc da aggiornare poi".

### 9.2 Deploy obbligatorio dopo modifiche doc
Sempre `./deploy.sh` su Cloudflare dopo ogni batch, senza chiedere. Aggiornare la sitemap se cambia il set canonico.

### 9.3 Bilingual MAPPING
Vocabolario synt + verb prompts: sinonimi e confine semantico in IT+EN per ogni verbo.

### 9.4 Niente nomi propri
Vedi §7.5.

---

**Riferimenti**: ADR `decisions/` · architettura `docs/it/architecture/` (+EN) · memorie `~/.claude/projects/-opt-metnos/memory/MEMORY.md` · prompt `runtime/prompts/<lang>/*.j2` (ADR 0092) · stato corrente e decisioni runtime: `CLAUDE.mutabile.md`.
