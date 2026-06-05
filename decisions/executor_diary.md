---
name: Diario degli executor di Metnos
description: Registro vivente degli executor desiderati o implementati. Vedi feedback_executor_diary_workflow per il protocollo di consultazione e aggiornamento.
type: project
originSessionId: e7251eba-8a6a-40ff-b5c7-9d04ad11c273
---
**Diario degli executor di Metnos.** Ogni entry cattura un executor immaginato, desiderato, in proof, implementato o abbandonato. Le motivazioni sono accumulate, non sostituite — ogni motivo e' un dato sulle vere necessita' del sistema. Vedi `feedback_executor_diary_workflow` per il protocollo.

## Indice rapido (al 27/4/2026 sera tardi — fine sessione marathon)

Naming compositivo `azione_oggetto[_modificatore]` adottato per i nuovi (vedi `feedback_executor_naming_convention`). Vecchi (fs_read, fs_write, time_read, web_fetch, pkg_*) da refactor in sessione dedicata.

| #  | Nome                    | Stato         | Capability         | Target_kind  |
|----|-------------------------|---------------|--------------------|--------------|
| 1  | fs_read                 | implementato  | fs:read            | path_glob    |
| 2  | fs_write                | implementato  | fs:write           | path_glob    |
| 49 | find_file               | implementato  | fs:read            | path_glob    |
| 50 | list_dir                | implementato  | fs:read            | path_glob    |
| 51 | create_dir              | implementato  | fs:write (critical)| path_glob    |
| 52 | move_file               | implementato  | fs:write (critical)| path_glob    |
| 53 | filter_entries          | implementato  | (puro)             | none         |
| 3  | time_read               | implementato  | time:read          | none         |
| 4  | web_fetch               | implementato  | network:http       | host         |
| 5  | format_json             | sintetizzato  | (parse/format puro)| none         |
| 6  | analyze_text_statistics | sintetizzato  | (text stats)       | none         |
| 7  | fs_list                 | desiderato    | fs:read            | path_glob    |
| 8  | fs_delete               | desiderato    | fs:write           | path_glob    |
| 9  | fs_move                 | desiderato    | fs:write           | path_glob    |
| 10 | shell_exec              | desiderato    | code:exec          | exact        |
| 11 | web_search              | desiderato    | network:http       | host         |
| 12 | geo_search              | desiderato    | network:http       | host         |
| 13 | llm_chat                | desiderato    | llm:local|online   | none         |
| 14 | llm_classify            | desiderato    | llm:local|online   | none         |
| 15 | llm_extract             | desiderato    | llm:local|online   | none         |
| 16 | mail_read               | desiderato    | mail:read          | exact        |
| 17 | mail_send               | desiderato    | mail:send          | exact        |
| 18 | mail_search             | desiderato    | mail:read          | exact        |
| 19 | mail_label              | desiderato    | mail:read          | exact        |
| 20 | channel_in              | desiderato    | channel:in         | exact        |
| 21 | channel_out             | desiderato    | channel:out        | exact        |
| 22 | parse_pdf               | desiderato    | (parsing locale)   | none         |
| 23 | parse_html              | desiderato    | (parsing locale)   | none         |
| 24 | calendar_create_event   | desiderato    | (calendar backend) | exact        |
| 25 | calendar_list_events    | desiderato    | (calendar backend) | none         |
| 26 | router                  | desiderato    | (meta-routing)     | none         |
| **Excel/foglio CRUD** |||||
| 27 | xlsx_read               | desiderato    | fs:read + parse    | path_glob    |
| 28 | xlsx_write              | desiderato    | fs:write           | path_glob    |
| 29 | xlsx_query              | desiderato    | fs:read + filter   | path_glob    |
| 30 | csv_read                | desiderato    | fs:read + parse    | path_glob    |
| 31 | csv_write               | desiderato    | fs:write           | path_glob    |
| 32 | csv_filter              | desiderato    | (transform locale) | none         |
| 33 | sheet_to_json           | desiderato    | (transform locale) | none         |
| **Matematica** |||||
| 34 | calc_eval               | desiderato    | (math puro)        | none         |
| 35 | unit_convert            | desiderato    | (math + tabella)   | none         |
| 36 | linear_solve            | desiderato    | (numerico)         | none         |
| 37 | matrix_op               | desiderato    | (numerico)         | none         |
| 38 | percentage_compute      | desiderato    | (math puro)        | none         |
| **Statistica** |||||
| 39 | stats_summary           | desiderato    | (stat descrittiva) | none         |
| 40 | stats_correlation       | desiderato    | (stat bivariata)   | none         |
| 41 | stats_regression        | desiderato    | (regressione)      | none         |
| 42 | stats_ttest             | desiderato    | (test ipotesi)     | none         |
| 43 | stats_histogram         | desiderato    | (binning)          | none         |
| 44 | stats_outlier_detect    | desiderato    | (z-score / IQR)    | none         |
| **Gestione pacchetti (cross-OS)** |||||
| 45 | pkg_install             | desiderato    | code:exec (critical)| exact (pkg) |
| 46 | pkg_uninstall           | desiderato    | code:exec (critical)| exact (pkg) |
| 47 | pkg_search              | desiderato    | code:exec          | exact (query)|
| 48 | pkg_list_installed      | desiderato    | code:exec          | none         |

**Stato "sintetizzato":** nato dalla cascata reattiva via synt.generate (Gemma 4 26B locale come tier wise), passa convention check + birth-test livello 2 LLM-generated, firmato Ed25519. Vive nel pool come ogni altro executor seed.

---

## Cluster Excel / spreadsheet CRUD

**Motivazioni comuni:**
- [26/4/2026 sera, agenda Roberto] Caso d'uso fondamentale per utente non-developer: leggere/scrivere/filtrare fogli Excel e CSV. Senza questi, Metnos non puo' rispondere a richieste tipo "fammi la somma della colonna B per gli ordini del mese" o "esporta i clienti attivi in CSV".
- Cluster denso (7 entry): tipico candidato a generalizzazione introvertiva (stadio 5 v1.2) — `xlsx_read` + `csv_read` + `sheet_to_json` potrebbero convergere in un unico `tabular_read(format=auto)` parametrico.

**Note di sintesi:**
- Per `xlsx_*` serve dipendenza esterna (`openpyxl` o `xlsx2csv`): primo executor che esce dalla stdlib whitelist. Il profilo sandbox dovra' dichiarare l'import esplicito.
- `csv_*` resta puramente stdlib (modulo `csv`).
- Gli operatori `_filter`/`_query` accettano un sottoinsieme di SQL-like predicato dichiarativo, non eval di espressioni Python (security).

## Cluster matematica

**Motivazioni comuni:**
- [26/4/2026 sera, agenda Roberto] Domande aritmetiche e algebriche elementari ricorrenti: l'utente non vuole che Metnos chieda al LLM "quanto fa X" — vuole che Metnos abbia uno strumento deterministico.
- Coppia con il cluster statistica: insieme costituiscono il toolkit "calcolo" di Metnos.

**Note di sintesi:**
- `calc_eval`: NON `eval()` di Python — un parser di espressioni con whitelist di operatori + funzioni (es. `sympy.parse_expr` con env ristretto, oppure parser ad-hoc).
- `unit_convert`: tabella locale (no API esterna) per le conversioni piu' comuni; cluster con `geo_search` se servono unita' geo-spaziali.
- `linear_solve`/`matrix_op`: candidato all'unica eccezione "non solo stdlib" del runtime (NumPy come dipendenza opzionale del profilo sandbox).

## Cluster statistica

**Motivazioni comuni:**
- [26/4/2026 sera, agenda Roberto] Estensione naturale del cluster matematica per analisi di dati. Apre la porta a casi tipo "la mia spesa mensile e' aumentata significativamente?" (ttest), "c'e' correlazione fra ore di sport e umore?" (correlation), "qual e' la spesa media e la sua varianza?" (summary).
- Si presta a piping di catena con `xlsx_read` -> `stats_summary` (dimostrazione del riuso compose: niente nuovo executor se hai gia' i due).

**Note di sintesi:**
- `stats_*` solo stdlib (`statistics` + `math`) per i casi base; per regressione e test piu' avanzati, profilo sandbox dichiara dipendenza opzionale (`scipy` o `numpy`).
- Output sempre in forma JSON serializzabile: niente oggetti pandas/scipy nel ritorno di `invoke()`.

## Cluster gestione pacchetti (cross-OS)

**Motivazioni comuni:**
- [27/4/2026, reality check] Capacita' di installare/disinstallare/cercare pacchetti software sul sistema. Necessita' tipica: "installa VLC sul mio laptop", "aggiorna Firefox sul fisso ufficio", "cosa ho di fotoritocco?".
- Tocca **due famiglie di OS** (vedi `project_metnos_client_architecture`): Linux (host primario .33 + altri host Linux) e Windows (laptop/desktop client). Ogni esecuzione e' potenzialmente *remota* tramite metnos-client sul target.

**Note di design:**
- **Backend per OS**:
  - Linux: priorita' `apt` (Debian/Ubuntu) → `dnf` (Fedora/RHEL) → `pacman` (Arch) → fallback `snap`/`flatpak` per app desktop. Detection automatica via `/etc/os-release`.
  - Windows: priorita' `winget` (default moderno) → `choco` (Chocolatey, fallback). MSI silenziato come ultima risorsa.
- **Architettura**: un singolo executor `pkg_install` (resp. uninstall/search/list) con dispatch interno backend-per-OS. Manifest dichiara `requires_target_host_os: [linux, windows]`. Sul lato runtime, il client remoto eredita la responsabilita' di scegliere il backend giusto.
- **Critical=true + approval=always per `pkg_install`/`pkg_uninstall`**: modificano stato di sistema, alcuni pacchetti hanno postinst script con effetti collaterali; richiedono privilegi (sudo/admin), quindi gia' di per se' un'azione "pesante".
- **`pkg_search` e `pkg_list_installed` sono read-only**: niente critical, niente approval (oltre alla quota network/IO).
- **Privilege handling**: per Linux, usare `sudo` solo se l'agente e' configurato per (policy-driven). Su Windows, eseguire con elevazione esplicita richiesta dal client (UAC). NON memorizzare password sudo nel manifest; delegare al sistema operativo del client (sudoers NOPASSWD ristretto a una whitelist di pkg manager, oppure prompt UAC).
- **Verifica**: dopo install, validare presenza con `which`/`Get-Command` o re-query del package manager. Output strutturato: `{installed_version, source, install_log_tail}`.

**Tensioni aperte:**
- Granularita': un solo executor con `action=install|uninstall|search|list` o quattro? Per ora 4 distinti (verb-noun, max 5-7 hop, capability differenti read vs write).
- `pkg_search` puo' overlappare con `web_search` per "cosa esiste per X?" → `pkg_search` e' specifico del manager (ritorna pacchetti reali installabili), `web_search` e' libero. Tenere distinti.
- Aggiornamenti (`pkg_upgrade`/`pkg_update_index`): non nel seed iniziale, ma da ricordare. Frequenza minore di install/search.

---

(scheduler e' builtin del runtime, non executor — vedi memoria `metnos_builtin_executors_proposals.md`)

---

## fs_read

**Stato:** implementato
**Capability:** fs:read
**Target_kind:** path_glob

**Descrizione:** Legge il contenuto di un file dal filesystem locale, restituisce testo o bytes (base64) con metadata.

**Motivazioni:**
- [22/4/2026, dialogo executor design] Operazione filesystem fondamentale; primo executor del seed pool.
- [25/4/2026, simulazione fatture] Necessario per leggere allegati e file utente in molti scenari quotidiani.
- [26/4/2026, audit + POC v1.1] Scelto come primo executor reale per validare lo schema manifest + sandbox + signature.

**Storia degli incontri:**
- 22/4: dialogo executor design (memoria `mykleos_executor_design_dialogue`)
- 25/4: simulazione walk-through fatture
- 26/4: audit di design lo raccomanda come azione #1
- 26/4: implementato in POC v1.1

**Implementazione:**
- Data: 2026-04-26
- Fase: POC v1.1, sessione "ciclo sull'architettura"
- Path: `/opt/myclaw/executors/fs_read/`

---

## fs_write

**Stato:** implementato
**Capability:** fs:write (critical=true)
**Target_kind:** path_glob

**Descrizione:** Scrive testo o bytes (da base64) in un file. Modi: overwrite, append, fail_if_exists.

**Motivazioni:**
- [22/4/2026, dialogo executor design] Pari a fs_read, operazione filesystem fondamentale.
- [26/4/2026, POC ciclo 4] Implementato come secondo executor per esercitare il flag `critical=true` e l'approval mode `per_target`.

**Storia degli incontri:**
- 22/4: seed pool
- 26/4: implementato in POC v1.1

**Implementazione:**
- Data: 2026-04-26
- Fase: POC v1.1
- Path: `/opt/myclaw/executors/fs_write/`

---

## time_read

**Stato:** implementato
**Capability:** time:read
**Target_kind:** none

**Descrizione:** Restituisce l'ora corrente come ISO 8601 in UTC o in un fuso orario IANA specificato.

**Motivazioni:**
- [22/4/2026, dialogo executor design] Capacita' atomica di base. Il sistema deve sempre sapere "che ora e'" per scheduling, log, decisioni temporali.
- [26/4/2026, POC ciclo 3] Implementato come terzo per esercitare un caso `target_kind=none` e validare il pre-filter su query semplici.

**Storia degli incontri:**
- 22/4: seed pool
- 26/4: implementato in POC v1.1

**Implementazione:**
- Data: 2026-04-26
- Fase: POC v1.1
- Path: `/opt/myclaw/executors/time_read/`

---

## web_fetch

**Stato:** implementato
**Capability:** network:http
**Target_kind:** host

**Descrizione:** Esegue HTTP/HTTPS GET o HEAD verso un host autorizzato; restituisce body + status + headers + content_type.

**Motivazioni:**
- [22/4/2026, dialogo executor design] Operazione di rete primaria. Senza HTTP non si raggiunge nulla di esterno.
- [25/4/2026, simulazione fatture] Necessario per pull di documenti, API, pagine.
- [26/4/2026, POC ciclo 5] Implementato come quarto per esercitare `target_kind=host`, hint con wildcard sub-host (`*.example.com`), e per esercitare il data piping (`{{step1.content}}`) verso fs_write.

**Storia degli incontri:**
- 22/4: seed pool
- 26/4: implementato in POC v1.1, validato data piping

**Implementazione:**
- Data: 2026-04-26
- Fase: POC v1.1
- Path: `/opt/myclaw/executors/web_fetch/`

---

## format_json

**Stato:** sintetizzato
**Capability:** (parse/format puro)
**Target_kind:** none

**Descrizione:** Formatta una stringa JSON con indentazione leggibile (default 4 spazi). Solo stdlib (`json`).

**Motivazioni:**
- [26/4/2026 sera, primo end-to-end di synt.generate via Gemma 4 26B locale] Caso scelto come prima sintesi reale: caso semplice, niente regex, niente I/O, niente fallback complessi. Ha esercitato la pipeline intera (compose fail -> generate stadio 2+3 -> profile pure -> birth-test 4/4 pass -> approve -> sign -> verify ok).

**Storia degli incontri:**
- 26/4 sera: nato dalla cascata reattiva su un proto-mnest sintetico `cli_test_input -> json_pretty_format`; capability_hint `[json, pretty, format]`; Gemma 4 26B in 65s wall (skeleton 36s + birth-tests 28s).

**Implementazione:**
- Data: 2026-04-26
- Fase: POC v1.1, primo executor sintetizzato
- Tier wise usato: llamacpp (Gemma 4 26B Q4_K_M)
- Birth-test 4/4 PASS: standard pretty-print, custom indent, malformed JSON, non-string input
- Path (test): `/tmp/synt-cli-*/executors/format_json/` (non in pool produzione, era smoke test)

---

## analyze_text_statistics

**Stato:** sintetizzato
**Capability:** (text stats)
**Target_kind:** none

**Descrizione:** Analizza un testo per calcolare totale parole, parole uniche, top-5 piu' frequenti. Solo stdlib (`json, re, collections`).

**Motivazioni:**
- [26/4/2026 sera, probe di Gemma 4 26B come tier wise] Caso di sintesi non banale per validare convention compliance, regex, output strutturato. Usato sia come probe diretto del provider, sia come smoke test della pipeline synt completa.

**Storia degli incontri:**
- 26/4: probe diretto a llama-server (38.7s, 1794 token out, code AST + smoke OK).
- 26/4 sera: rigirato attraverso synt.react() sul proto-mnest `user_input -> word_stats_analyzer` con tutti gli stadi 2-5 (skeleton + profilo + birth-test 4/4 PASS).

**Implementazione:**
- Data: 2026-04-26
- Fase: POC v1.1, secondo executor sintetizzato (probe + pipeline)
- Note: Gemma 4 talvolta over-escapa il regex `r'\\w+'` -> birth-test cattura il bug (ok=true ma metadata.total_words mismatch). Quando il regex e' corretto, tutti i test passano. Variabilita' deterministica accettabile, gestita dal birth-test livello 2.

---

## fs_list

**Stato:** desiderato
**Capability:** fs:read
**Target_kind:** path_glob

**Descrizione:** Elenca file e directory sotto un path, opzionalmente ricorsivo, con filtri glob e metadata (size, mtime, type).

**Motivazioni:**
- [22/4/2026, seed pool] Una delle 5 FS canoniche; senza listing non si esplora un filesystem.
- [25/4/2026, simulazione fatture] Per "trova tutti i PDF in ~/fatture/" serve listing.

**Storia degli incontri:**
- 22/4: seed pool

**Implementazione:**

---

## fs_delete

**Stato:** desiderato
**Capability:** fs:write (critical=true)
**Target_kind:** path_glob

**Descrizione:** Cancella file o directory (con flag recursive). Critical perche' irreversibile; per_target perche' ogni cancellazione ha contesto specifico.

**Motivazioni:**
- [22/4/2026, seed pool] FS canonica.
- [25/4/2026, simulazione fatture] Per "archivia e poi cancella" serve esplicitamente.

**Storia degli incontri:**
- 22/4: seed pool

**Implementazione:**

---

## fs_move

**Stato:** desiderato
**Capability:** fs:write (critical=true)
**Target_kind:** path_glob

**Descrizione:** Rinomina o sposta un file/directory. Atomico se sorgente e destinazione sullo stesso filesystem.

**Motivazioni:**
- [22/4/2026, seed pool] FS canonica.
- [25/4/2026, simulazione fatture] Pattern "scarica in tmp, sposta in archivio definitivo".

**Storia degli incontri:**
- 22/4: seed pool

**Implementazione:**

---

## shell_exec

**Stato:** desiderato
**Capability:** code:exec (critical=true, approval=always)
**Target_kind:** exact

**Descrizione:** Esegue un comando shell con argomenti, restituisce stdout/stderr/exit_code. Approval `always` per livello di rischio.

**Motivazioni:**
- [22/4/2026, seed pool] Escape hatch per operazioni che non hanno un executor dedicato. Ma e' anche la massima superficie di attacco.
- [25/4/2026, simulazione fatture] Tentazione frequente; va contenuta perche' risolve troppe cose con una capability sola.

**Storia degli incontri:**
- 22/4: seed pool — discusso come "necessario ma da gestire con prudenza"

**Implementazione:**

---

## web_search

**Stato:** desiderato
**Capability:** network:http
**Target_kind:** host (limitato a SearXNG self-hosted)

**Descrizione:** Esegue una query di ricerca web tramite SearXNG locale. Restituisce risultati strutturati (titolo, URL, snippet).

**Motivazioni:**
- [22/4/2026, seed pool] Capacita' "search the web" e' una delle piu' richieste in qualunque assistente AI.
- [22/4/2026, smoke test SearXNG OK] Stack OSS gia' attivo, basta wrappare.
- [feedback_open_source_first] Coerente col principio OSS self-hostato come default.

**Storia degli incontri:**
- 22/4: seed pool + smoke test SearXNG

**Implementazione:**

---

## geo_search

**Stato:** desiderato
**Capability:** network:http
**Target_kind:** host (limitato a Nominatim/OSM)

**Descrizione:** Geocodifica e reverse-geocoding tramite Nominatim (OpenStreetMap). Input: indirizzo libero o coordinate; output: lat/lon, indirizzo strutturato, bounding box.

**Motivazioni:**
- [22/4/2026, seed pool] "Dove si trova X?" e "che indirizzo ha questo punto?" sono primitive per molti task (calendario con luogo, viaggio, ecc.).
- [feedback_open_source_first] Nominatim/OSM e' OSS self-hostable.

**Storia degli incontri:**
- 22/4: seed pool

**Implementazione:**

---

## llm_chat

**Stato:** desiderato
**Capability:** llm:local oppure llm:online (in base a mode)
**Target_kind:** none

**Descrizione:** Chiamata LLM generica per generare testo a partire da un prompt. Distinto dal pianificatore: e' un tool che il pianificatore puo' usare per "scrivimi un riassunto", "traduci questo", "genera un'email" senza orchestrazione complessa.

**Motivazioni:**
- [22/4/2026, seed pool] Composizione "fetch -> sommario -> save" richiede un nodo LLM intermedio puro.
- [25/4/2026, simulazione fatture] Necessario per "estrai i dati salienti dalla fattura e formattali".

**Storia degli incontri:**
- 22/4: seed pool
- 25/4: simulazione walk-through

**Implementazione:** (parzialmente: `llm_provider.py` esiste come modulo runtime, ma non e' un executor invocabile dal pianificatore)

---

## llm_classify

**Stato:** desiderato
**Capability:** llm:local oppure llm:online
**Target_kind:** none

**Descrizione:** Classificazione testuale: input testo + lista categorie -> categoria scelta (con score). Specializzato per essere economico (modelli piccoli, prompt corto).

**Motivazioni:**
- [22/4/2026, seed pool] Operazione comune e specializzabile (modelli locali piccoli vanno bene). Distinguere e' meglio di chat generica per costo + affidabilita'.
- [25/4/2026, simulazione fatture] Per "questa email e' una fattura, una newsletter, un personale?" -> classify.

**Storia degli incontri:**
- 22/4: seed pool
- 25/4: simulazione

**Implementazione:**

---

## llm_extract

**Stato:** desiderato
**Capability:** llm:local oppure llm:online
**Target_kind:** none

**Descrizione:** Estrazione strutturata: input testo + JSON Schema desiderato -> JSON conforme allo schema, riempito coi dati estratti dal testo.

**Motivazioni:**
- [22/4/2026, seed pool] Ponte fra dati testuali (mail, pagine) e dati strutturati (calendario, fatture, db).
- [25/4/2026, simulazione fatture] Chiave: "estrai numero, data, importo, fornitore da questa fattura PDF".
- [26/4/2026, POC] Pattern di tool-use nativo gia' fa qualcosa di simile a livello pianificatore; llm_extract e' la versione "tool sintetico" per uso esplicito dentro una catena.

**Storia degli incontri:**
- 22/4: seed pool
- 25/4: simulazione fatture
- 26/4: tool-use nativo del pianificatore mostra che il pattern funziona

**Implementazione:**

---

## mail_read

**Stato:** desiderato
**Capability:** mail:read
**Target_kind:** exact (mailbox/folder)

**Descrizione:** Legge messaggi da una mailbox IMAP (o equivalente). Filtri per from/subject/date/unread; restituisce headers + body (plain o HTML) + allegati come riferimenti.

**Motivazioni:**
- [22/4/2026, seed pool] Mail e' una delle interfacce primarie dell'utente con il mondo. Senza accesso, l'assistente perde meta' della sua utilita'.
- [25/4/2026, simulazione fatture] Pattern "trova le fatture nell'inbox" e' uno dei casi guida.
- [reference_mail_check] La casella user@example.com (register.it IMAPS) e' gia' configurata via `~/.config/mykleos/mail.env`. Lo script `check-mail.sh` e' il prototipo di consumer.

**Storia degli incontri:**
- 22/4: seed pool
- 25/4: simulazione fatture
- 26/4: ricordo che esiste gia' uno script bash analogo (`check-mail.sh`) che e' di fatto un proto-executor

**Implementazione:**

---

## mail_send

**Stato:** desiderato
**Capability:** mail:send (critical=true)
**Target_kind:** exact (recipient)

**Descrizione:** Invia un messaggio via SMTP autenticato. To/CC/BCC, subject, body (plain o HTML), allegati. Approval per_target obbligatorio per ogni nuovo destinatario.

**Motivazioni:**
- [22/4/2026, seed pool] Capacita' di output verso il mondo esterno; effetto irreversibile, alta posta in gioco.
- [25/4/2026, simulazione fatture] Pattern "manda promemoria al fornitore" emerge naturalmente.
- [26/4/2026, POC test e2e ciclo 13] La query di test "manda una mail a Mario" ha mostrato che il sistema attualmente si arrende; presenza di mail_send sblocca questa famiglia di task.
- [26/4/2026, sessione check mail] Lo script `send-mail.sh` di Roberto (creato durante l'invio dell'audit per email) gia' implementa il backend SMTP di register.it; la promozione a executor formale e' principalmente questione di manifest + sandbox.

**Storia degli incontri:**
- 22/4: seed pool
- 25/4: simulazione fatture
- 26/4: POC test query "manda una mail" non gestita
- 26/4: creato `send-mail.sh` che e' un proto-executor; il manifest Metnos e' ancora da scrivere

**Implementazione:**

---

## mail_search

**Stato:** desiderato
**Capability:** mail:read
**Target_kind:** exact (mailbox)

**Descrizione:** Ricerca full-text dentro una mailbox: query + filtri data/from -> lista di message-id + snippet. Distinta da mail_read perche' specializzata per scoperta, non per lettura completa.

**Motivazioni:**
- [22/4/2026, seed pool] "Ho ricevuto una mail di X la settimana scorsa" e' un pattern di scoperta diverso da "leggi mail X".

**Storia degli incontri:**
- 22/4: seed pool

**Implementazione:**

---

## mail_label

**Stato:** desiderato
**Capability:** mail:read (write tag/folder, ma non muta contenuto)
**Target_kind:** exact (mailbox + label name)

**Descrizione:** Sposta o etichetta un messaggio in una cartella IMAP. Per archiviazione, organizzazione automatica.

**Motivazioni:**
- [22/4/2026, seed pool] Senza la capacita' di organizzare la mailbox, l'assistente puo' solo leggere, non aiutare a tenere ordine.

**Storia degli incontri:**
- 22/4: seed pool

**Implementazione:**

---

## channel_in

**Stato:** desiderato
**Capability:** channel:in
**Target_kind:** exact (channel id)

**Descrizione:** Riceve messaggi/eventi da un canale (Telegram, webhook locale, ecc.). Long-polling o webhook, restituisce messaggi nuovi.

**Motivazioni:**
- [22/4/2026, seed pool] Senza un canale di input vivo, l'assistente e' interrogato solo via CLI; perde il pattern conversazionale.
- [mykleos_topology_and_security] Telegram su .33 e' gia' parte della topologia self-hosted prevista.

**Storia degli incontri:**
- 22/4: seed pool
- 24/4: confermato Telegram come canale primario

**Implementazione:**

---

## channel_out

**Stato:** desiderato
**Capability:** channel:out
**Target_kind:** exact (channel id)

**Descrizione:** Invia un messaggio a un canale. Per Telegram: chat_id + testo (markdown opzionale) + allegati.

**Motivazioni:**
- [22/4/2026, seed pool] Speculare a channel_in; chiude il loop conversazionale.
- [25/4/2026, simulazione fatture] Pattern "ho fatto X, ti notifico su Telegram".
- [project_dialog_manager_authorization_ux] La carta a 3 righe per approval e' renderizzata via channel_out.

**Storia degli incontri:**
- 22/4: seed pool
- 25/4: simulazione fatture come canale di notifica
- 26/4: necessario per approval UX

**Implementazione:**

---

## parse_pdf

**Stato:** desiderato
**Capability:** (parsing locale, nessuna IO esterna)
**Target_kind:** none (input via path o bytes)

**Descrizione:** Estrae testo (e opzionalmente struttura: pagine, layout, tabelle) da un PDF locale. Usa pdfminer/Tesseract per OCR su scansioni.

**Motivazioni:**
- [22/4/2026, seed pool] Documenti utente sono in larga parte PDF; senza parsing, il contenuto resta opaco.
- [25/4/2026, simulazione fatture] Le fatture sono PDF; senza parse_pdf, llm_extract non ha materiale.
- [feedback_open_source_first] Tesseract e' OSS self-hostable.

**Storia degli incontri:**
- 22/4: seed pool
- 25/4: simulazione fatture (uno dei tre gap identificati)

**Implementazione:**

---

## parse_html

**Stato:** desiderato
**Capability:** (parsing locale)
**Target_kind:** none

**Descrizione:** Da HTML grezzo a testo strutturato + metadata. Wrapper su readability/trafilatura per "estrai l'articolo", o BeautifulSoup per query DOM mirate.

**Motivazioni:**
- [22/4/2026, seed pool] Output di web_fetch su pagine pubbliche e' HTML; serve un nodo che lo riduca a testo leggibile prima di passarlo a llm_chat o llm_extract.
- [26/4/2026, POC] In POC abbiamo testato web_fetch su httpbin.org/get che ritorna gia' JSON; pagine HTML reali esploderebbero il contesto LLM senza parse_html.

**Storia degli incontri:**
- 22/4: seed pool
- 26/4: limitazione del POC (testato solo su JSON endpoints)

**Implementazione:**

---

## calendar_create_event

**Stato:** desiderato
**Capability:** (calendar backend, nuova famiglia di capability da definire)
**Target_kind:** exact (calendar id)

**Descrizione:** Crea un evento in un calendario (Google Calendar, CalDAV, locale ICS). Title, start/end, description, attendees, location, reminder.

**Motivazioni:**
- [25/4/2026, simulazione fatture] Pattern "scadenza fattura -> ricordami il 25" richiede un calendario.
- [25/4/2026] Identificato come uno dei tre gap della simulazione.

**Storia degli incontri:**
- 25/4: simulazione fatture (calendar backend OOS)

**Implementazione:**

---

## calendar_list_events

**Stato:** desiderato
**Capability:** (calendar backend)
**Target_kind:** none (filtro per data range)

**Descrizione:** Elenca eventi in un range temporale. Restituisce lista con id, title, start/end, location.

**Motivazioni:**
- [25/4/2026, simulazione fatture] "Ho qualcosa in agenda venerdi'?" richiede listing prima di create.

**Storia degli incontri:**
- 25/4: simulazione fatture

**Implementazione:**

---

---

## router

**Stato:** desiderato
**Capability:** (meta-routing — nuova famiglia o builtin del runtime)
**Target_kind:** none

**Descrizione:** Dato un contesto (query utente o intent del componente chiamante) e l'intero catalogo, restituisce una lista curata di K executor candidati, ottimizzando il bilanciamento fra recall (non perdere executor necessari) e precision (non confondere il LLM con tools irrilevanti). Promuove e generalizza il prefilter attuale.

**Motivazioni:**
- [22/4/2026, dialogo executor design] In tutti i flussi che lavorano sugli executor (pianificatore agent_runtime, synt che cerca componenti riutilizzabili da comporre, vaglio che confronta proposta con alternative possibili), serve un meccanismo unico di selezione del sotto-catalogo.
- [26/4/2026, stress test D-tools] Con catalog grande la qualita' della selezione non e' lineare: K piccolo rischia di mancare l'executor giusto (ricall basso); K grande rallenta il LLM e introduce distrazione. Il sweet spot dipende dalla query (heuristica adattiva e' la prima soluzione decisa).
- [26/4/2026, riflessione architetturale di Roberto] Punto di attenzione esplicito: se il router crea una lista insufficiente -> il sistema fallisce o produce soluzioni subottimali; se la lista e' troppo grande -> il LLM lavora male. Deve gestire questo trade-off con una strategia esplicita.

**Strategie possibili (da decidere quando promuoviamo da prefilter a router):**
- **K adattivo (deciso v1.1)**: K varia in base a una metrica di confidenza del prefilter. Score top-1 >> top-2 -> bassa K; score concentrati -> alta K.
- **Re-ranking**: prefilter prende un wide pool (es. 50), poi un re-ranker piu' costoso (embedding o LLM mini) li ricolloca, top-K finale ridotto (es. 10).
- **Iterativo con feedback**: il pianificatore puo' segnalare "non trovo cio' che mi serve, espandi il pool"; il router risponde con K+10 al prossimo turno.
- **Multi-stage**: prefilter (bag-of-words, sub-ms) -> mid-filter (embedding semantica, ~50ms) -> top-K finale.

**Storia degli incontri:**
- 22/4: implicito nel dialogo design (prefilter come componente)
- 26/4 mattina: implementato in POC come `runtime/prefilter.py` con bag-of-words + min_score; usato solo dal pianificatore.
- 26/4 sera: stress test ha esposto il trade-off recall/precision; Roberto ha esplicitato la promozione a router come necessita'.

**Implementazione:** parziale — `runtime/prefilter.py` e' la versione minimale (1 strategia, bag-of-words). Promuovere a `router` significa: API uniforme `route(query, catalog, hints={confidence_target, max_k, ...})`, multipla strategy, accessibile da agent_runtime + synt + vaglio.

---

## pkg_install

**Stato:** desiderato
**Capability:** code:exec (critical=true, approval=always)
**Target_kind:** exact (nome pacchetto)
**OS supportati:** linux, windows

**Descrizione:** Installa un pacchetto software sul sistema target (locale o remoto via metnos-client). Detection automatica del package manager: Linux (apt/dnf/pacman/snap/flatpak), Windows (winget/choco). Output: versione installata, sorgente, tail del log.

**Motivazioni:**
- [27/4/2026, richiesta esplicita] Capacita' fondamentale per un assistente che vive sul desktop: "installa VLC", "metti Python 3.12", "ho bisogno di GIMP". Senza, ogni richiesta di setup cade fuori dal sistema.
- Il pattern "installa software su un host remoto" e' uno dei casi piu' naturali per esercitare la pipeline metnos-client (vedi `project_metnos_client_architecture`).

**Storia degli incontri:**
- 27/4: aggiunto al diario su richiesta utente, prima del reality check.

**Implementazione:**

---

## pkg_uninstall

**Stato:** desiderato
**Capability:** code:exec (critical=true, approval=always)
**Target_kind:** exact (nome pacchetto)
**OS supportati:** linux, windows

**Descrizione:** Disinstalla un pacchetto. Stesso dispatch backend di pkg_install. Su Linux, distingue purge (config rimosse) vs remove. Su Windows, winget/choco uninstall.

**Motivazioni:**
- [27/4/2026] Speculare a pkg_install. "Liberami spazio togliendo X" e "non uso piu' Y" sono richieste comuni.
- Critical perche' alcune disinstallazioni rimuovono config utente o dipendenze condivise.

**Storia degli incontri:**
- 27/4: aggiunto al diario.

**Implementazione:**

---

## pkg_search

**Stato:** desiderato
**Capability:** code:exec (read-only sul package manager)
**Target_kind:** exact (query)
**OS supportati:** linux, windows

**Descrizione:** Cerca pacchetti disponibili nel package manager configurato. Output: lista `[{nome, versione, descrizione, sorgente}]`. Read-only, nessuna modifica.

**Motivazioni:**
- [27/4/2026] "C'e' un pacchetto per X?" e' la domanda che precede pkg_install. Senza search, l'utente deve chiedere a `web_search` e poi indovinare il nome esatto, fragile.
- Distinto da `web_search`: ritorna solo pacchetti REALI installabili sul sistema, non risultati web generici.

**Storia degli incontri:**
- 27/4: aggiunto al diario.

**Implementazione:**

---

## pkg_list_installed

**Stato:** desiderato
**Capability:** code:exec (read-only)
**Target_kind:** none (filtro opzionale per pattern)
**OS supportati:** linux, windows

**Descrizione:** Elenca i pacchetti installati sul sistema, con versione. Filtro opzionale per pattern. Output strutturato JSON.

**Motivazioni:**
- [27/4/2026] "Cosa ho gia' installato?" e' base per ragionare su upgrade, conflitti, audit.
- Utile come step diagnostico prima di pkg_install (evitare reinstall) o di pkg_uninstall (verificare presenza).

**Storia degli incontri:**
- 27/4: aggiunto al diario.

**Implementazione:**

---

## find_file (era fs_find)

**Stato:** implementato (27/4 sera)
**Capability:** fs:read
**Target_kind:** path_glob
**Path:** `/opt/myclaw/executors/find_file/`

**Descrizione:** Ricerca file (o directory con include_dirs) per nome o pattern dentro un base_path autorizzato. Ricorsivo di default. Accetta pattern singolo, stringa multi-pattern (virgole/pipe) o array `patterns`. Match case-insensitive di default. Ritorna `entries: list[{path,name,type,mime,kind}]` (uniformato a list_dir, componibile con filter_entries) + `matches: list[str]` (path comodi).

**Motivazioni:**
- [27/4 sera, reality check live] "cerca il file diario_di_bordo" senza fs_find il bot indovina path o hallucina. Primitiva quotidiana.
- Multi-pattern (`*.jpg,*.png`) e case-insensitive richiesti dall'uso reale: l'LLM passa estensioni in lowercase anche su file `.JPG`.
- Output uniformato (entries) per essere chained con filter_entries.

**Storia:**
- 27/4 sera: implementato come `fs_find`, poi rinominato a `find_file` per naming compositivo. Output passato da `matches` a `entries+matches` per componibilita' con filter_entries.

---

## list_dir (era fs_list, #7 desiderato)

**Stato:** implementato (27/4 sera)
**Capability:** fs:read
**Target_kind:** path_glob
**Path:** `/opt/myclaw/executors/list_dir/`

**Descrizione:** Lista TUTTI i file/sottocartelle di una directory autorizzata. Per ogni elemento: name, type (file/dir/symlink), size, mtime ISO, mime, kind (image/video/audio/text/document/archive/binary/dir/symlink). Niente filtri (principio: uso generale; per filtrare → filter_entries). Sort name|mtime|size.

**Motivazioni:**
- Coppia con find_file: list_dir esamina una dir, find_file fa scan ricorsivo per pattern. Entrambi ritornano entries arricchite.
- [27/4 sera] L'arricchimento `mime+kind` derivato da `mimetypes.guess_type(name.lower())` permette filtraggio semantico downstream (es. `kind='image'`) senza che list_dir sappia cosa cerca l'utente.

**Storia:**
- 22/4: nel pool seed come fs_list (desiderato).
- 27/4 sera: implementato come `fs_list`, poi rinominato a `list_dir` (naming compositivo) e snellito (rimossi i filtri pattern/type/kind, delegati a filter_entries).

---

## create_dir (era fs_mkdir)

**Stato:** implementato (27/4 sera)
**Capability:** fs:write (critical=true)
**Target_kind:** path_glob
**Path:** `/opt/myclaw/executors/create_dir/`

**Descrizione:** Crea directory al path indicato. Default `parents=true` (crea intermedie come `mkdir -p`) ed `exist_ok=true` (non fallisce se gia' esiste).

**Motivazioni:**
- [27/4 sera, caso "ordina immagini per anno"] Per spostare file in sottocartelle anno serve creare le sottocartelle. Mancava negli executor — promosso al volo.
- Critical=true perche' modifica filesystem, ma l'effetto e' incrementale (no irreversibile come delete).

**Storia:**
- 27/4 sera: nato dal caso "ordina immagini" (live test). Implementato + rinominato dal nome originale fs_mkdir.

---

## move_file (era fs_move, #9 desiderato)

**Stato:** implementato (27/4 sera)
**Capability:** fs:write (critical=true)
**Target_kind:** path_glob (controlla src + dst)
**Path:** `/opt/myclaw/executors/move_file/`

**Descrizione:** Sposta o rinomina file/directory in un'unica call (src → dst può cambiare path E nome). Default `parents=true` (crea dir parent di dst) e `overwrite=false` (fallisce se dst esiste). Atomico same-fs via shutil.move. Critical+irreversibile.

**Motivazioni:**
- [22/4] Pool seed: fs canonica.
- [27/4 sera, caso "ordina immagini"] Test live esercita move+rename atomico (src=`~/images/063.JPG`, dst=`~/images/2024/240427_063.JPG`). Sostituisce due executor ipotetici (move_file + rename_file) con uno solo, piu' efficiente in batch.

**Storia:**
- 22/4: pool seed.
- 27/4 sera: implementato + rinominato. check_hints esteso per validare sia src che dst (entrambi devono essere in scope fs:write).

---

## filter_entries

**Stato:** implementato (27/4 sera)
**Capability:** (puro - nessuna)
**Target_kind:** none
**Path:** `/opt/myclaw/executors/filter_entries/`

**Descrizione:** Filtra una lista generica di `entries` (dict) in base a criteri: kind, type, mime_prefix, name_glob (multi via virgole), name_regex, size_min/max, mtime_after/before. AND tra criteri, OR fra valori dentro un criterio. Pensato per essere chained dopo list_dir/find_file via `entries={{stepN.entries}}`.

**Motivazioni:**
- [27/4 sera] Roberto: "due executor di uso generale: tutti i file di una dir + un altro per filtrare". Decoupling: list_dir restituisce tutto arricchito, filter_entries filtra. Composizione invece di feature creep su list_dir.
- Pure executor (no IO, no capability): caso candidato per synt.generate (ma scritto a mano nel POC).

**Storia:**
- 27/4 sera: nato dalla discussione di design durante reality check live. Primo executor "puro" composer del pool.

---

## Note di studio (ottimizzazione del diario)

Cluster di motivazioni emersi al 26/4/2026:

- **Pipeline "inbox -> azioni"**: mail_read + parse_pdf + llm_extract + calendar_create_event + mail_label + channel_out e' il vero use case del walk-through fatture. 6 executor che insieme servono a "il sistema mi aiuta a tenere ordine nella vita digitale".
- **Pipeline "scoperta -> sintesi -> output"**: web_search + web_fetch + parse_html + llm_chat + channel_out. Pattern di "ricerca + risposta" classico di ogni assistente.
- **Trio LLM**: llm_chat / llm_classify / llm_extract sono spesso citati insieme. Vale la pena pensarli come *un singolo modulo* con tre modalita', anziche' tre executor distinti? Decidere prima di implementarli.
- **shell_exec come ultima risorsa**: emerge ovunque manchi un executor dedicato. Resistere alla tentazione: ogni shell_exec che salta fuori dovrebbe puntare verso l'identificazione di un executor mancante.
