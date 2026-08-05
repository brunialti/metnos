# ADR 0169 — Taglio di rilevanza adattivo + spreadsheet locale di default + guard refusal-in-args

**Date**: 2026-06-02
**Status**: accepted
**Related**: ADR 0117 (unified image enrichment index), ADR 0166 (intelligent path-aware indexing), ADR 0130 (backend tree per OBJECT), ADR 0136 (provider qualifier), §2.8 (no silent failure), §7.2 (semplicità), §7.3 (universalità), §7.9 (deterministico > LLM), §10.3 (open-source self-hosted come default)
**Origin**: regressione live 2/6 — query compound "cerca foto con persone in montagna, metti in uno spreadsheet il path e la descrizione, invia lo spreadsheet alla mia email" → 3 cause-radice indipendenti.

## Context

La query compound esponeva tre bug in sottosistemi distinti:

1. **find_images_indices ritornava 31062 entries** (≈intero corpus) per "persone in montagna". Diagnosi empirica (`/tmp/diag` su 31445 foto reali): la similarità coseno query↔corpus di BGE-M3 **NON si distribuisce su [0,1]**, collassa in banda stretta ad alta media (μ=0.601, σ=0.043, p99=0.716, max=0.773). Una soglia ASSOLUTA è priva di senso: `cos>=0.40` → 99.9% del corpus, `cos>=0.55` → 91.2%. Il filtro pre-esistente (ramo "espansa" con hardcoded `cos>=0.25`, ramo non-espansa `cos>=text_score_min=0.40`, escape `cos>=0.55`) lasciava passare tutto. Il segnale di rilevanza NON è il valore assoluto ma la POSIZIONE RELATIVA: gli outlier nella coda superiore per-query.

2. **Nessun creatore di spreadsheet LOCALE**. `create_files_spreadsheet`/`write_files_spreadsheet` erano Google-only (`client` default `google_workspace`, unico backend). Per allegare un foglio a un'email serve un file LOCALE. Il planner sceglieva `write_files_spreadsheet` (Google, richiede `spreadsheet_id` Drive di un foglio esistente) → errore `client 'local'` non applicabile. Violazione §10.3 (self-hosted default) + §2.2 (canonical = provider `_metnos`/local, Google = suffisso/opt-in).

3. **Rifiuto LLM trapelato in un arg**. Dovendo riempire `spreadsheet_id` (un foglio ancora da creare, nessun valore valido), il PLANNER emetteva un RIFIUTO come VALORE: `spreadsheet_id="Non posso eseguire questa operazione. Come modello linguistico, non ho accesso..."`. Garbage passato all'executor (§2.8).

Il knee/elbow globale — proposto come alternativa parameter-free — è stato **misurato e scartato**: su distribuzione plateau-dominata cade nel punto sbagliato (keep=1 su "montagna", keep=14849 su "mare"), instabile e inservibile.

## Decision

Tre soluzioni sistemiche (no patch, no soglie hard-coded di dominio).

### 1. Taglio di rilevanza adattivo — `runtime/relevance_cut.py` (funzione core riusabile)

`adaptive_relevance_threshold(scores, *, sigma=3.0, floor)` → soglia `t = max(floor, μ + sigma·σ)` calcolata sulla distribuzione PER-QUERY. Regola dei 3 sigma: un match è rilevante se è un outlier statisticamente significativo sopra lo sfondo. NON è un valore di dominio: μ,σ sono per-query, sigma è la soglia di significatività statistica. `floor` (= `text_score_min`) resta come pavimento assoluto anti-rumore per query senza match reali. Sotto 8 candidati la statistica si disattiva (solo floor).

È una **funzione core**, NON un executor builtin: il taglio è intrinseco al retrieval (avviene dentro l'executor mentre produce risultati onesti), non un'azione utente in pipeline. Riusabile da qualsiasi retrieval scored (immagini, URL semantici, ranking affinity).

Wire: `find_images_indices.py` blocco `if query_text and text_score_min>0.0` — gating sul COSENO (semantica pura); il bm25 resta nel composito SOLO per il ranking (per query come "persone in montagna" "persone" matcha ~tutto il corpus → come gate inquina, come tie-break ordina). Validato: "persone in montagna" 31062 → **107**, top-100 invariati. 5 query eterogenee: 0.2–2.8% del corpus vs 99% della soglia fissa.

### 2. Spreadsheet LOCALE = backend di default (§10.3 / §2.2)

Nuove funzioni in `runtime/backends/files/local.py`: `create_spreadsheet`/`write_spreadsheet`/`append_spreadsheet`/`read_spreadsheet` (.xlsx via openpyxl, .csv via stdlib). Per il backend locale `spreadsheet_id` == il PATH del file. `create` accetta `values` (righe iniziali) → crea+popola in un colpo (flusso `find → create_files_spreadsheet(values=...) → send_messages(attachments)`). Reverse `delete_created_paths`. Output di default in `~/.local/share/metnos/spreadsheets/<title>.xlsx`.

I 3 dispatcher (`create/write/read_files_spreadsheet`) ora hanno `_HANDLERS={"local":…, "google_workspace":…}` e **default `client="local"`**. Google Sheets = opt-in (`client="google_workspace"`, richiede OAuth + range A1). Manifest riallineati allo standard CAPITOLI §2.5 (SCOPO/PATTERN/NON/OUT) con boundary nel capitolo NON e versione 0.2.0. `read_files_xlsx`/`read_files_csv` (format-qualified) coesistono come scorciatoie esplicite.

### 3. Guard refusal-in-args — `agent_runtime.validate_args`

Detector universale deterministico (§7.9): scansiona OGNI valore stringa (e item di lista) degli args per marker auto-referenziali di rifiuto/meta-commento LLM (`_LLM_REFUSAL_MARKERS`, IT+EN: "come modello linguistico", "non posso eseguire questa", "as a language model", "i do not have access", …). Hit → fallimento di validazione → lo step malformato non raggiunge l'executor. Nessun valore di arg legittimo è un rifiuto auto-referenziale del modello. Difesa-in-profondità: la causa (bug 2) è risolta, questo è il safety-net (§7.3).

### 2-bis. Disambiguazione create vs write nel SCOPO (il re-test live l'ha smascherata)

Dopo il fix backend, il re-test live mostrava il planner ancora su `write_files_spreadsheet` (inventando un path `/documenti/foto_montagna.xlsx` per il `spreadsheet_id` required → ERR_PATH_NOT_FOUND). Causa: il Proposer (`engine/proposer.py::_render_tool_pool`) rende SCOPO+PATTERN+NON ma **tronca a 260 char**, quindi il capitolo `NON:` (dove vivevano i confini "NON per foglio esistente") **non raggiungeva l'LLM**; e il vecchio SCOPO di write ("scrive righe") combaciava con "metti il path e la descrizione". Fix: **disambiguazione front-loaded nel SCOPO** (entro i 260 char): create = "crea e POPOLA un NUOVO foglio … metti/salva dati da zero"; write = "MODIFICA un foglio che ESISTE GIA' (richiede il path) … NON inventare il path". Affinity per-verbo resa disgiunta (rimosso "salva celle" da write). Regola di authoring §2.5: la disambiguazione decisiva sta nel SCOPO, non nel NON (che il Proposer può troncare).

### 2-ter. `Framework.from_dict` robusto (engine/types.py)

Il re-test ha anche fatto emergere un crash del Proposer Mētis: uno step o un filler emesso dall'LLM come STRINGA invece che oggetto → `'str'.get(...)` AttributeError in `_parse_candidates` → planner a mani vuote. Fix: `from_dict` ignora gli elementi non-dict di `steps`/`fillers` (§2.8/§7.3, tollera output LLM malformato).

### 2-quater. `write` = upsert + target non-richiesto al planner (fix d'ISTANZA, verb-contract rinviato)

Anche con la disambiguazione del SCOPO, "**metti/salva** in uno spreadsheet" continuava a fallire: il planner sceglie `write_files_spreadsheet` (il prefilter `_VERB_TO_CANONICAL` mappa `metti/salva→write`, hardcoded e context-free) e, vedendo `spreadsheet_id`, **lo CHIEDE sempre** (`get_inputs`), anche per un foglio da creare. Causa linguistica (osservazione di Roberto): "metti/salva … IN uno spreadsheet" PRESUPPONE che il contenitore esista. Né il lessico né lo SCOPO da soli battono questo prior dell'LLM.

Fix d'istanza (la generalizzazione al verbo è rinviata "con calma", duplicazione accettata):
1. **`write` = upsert** (§2.4): `backends/files/local.py::write_spreadsheet` con `spreadsheet_id` opzionale — se omesso auto-nome, se il path manca CREA il file. `write_files` (testo) era già upsert. Accomoda la presupposizione fallita: qualunque verbo (create/write) scelga il planner, esce un `.xlsx` locale.
2. **target non esposto al planner**: `spreadsheet_id` marcato `runtime_resolved` (escluso dalla lista args del Proposer) **E rimosso da ogni menzione nel SCOPO**. Apprendimento chiave (per il futuro verb-contract): per sopprimere la domanda l'arg va tolto da ENTRAMBI — args-list (`runtime_resolved`) E testo SCOPO — altrimenti l'LLM lo richiede comunque. Resta iniettabile da from_step / args_extractor.

Risultato (validato in-process): "metti/salva/esporta … in uno spreadsheet" → `write_files_spreadsheet(values=…)` → upsert crea il file, **niente get_inputs**. Costo accettato: per `client=google_workspace` l'id Drive deve arrivare da from_step (l'LLM non lo chiede più). Da generalizzare in `VERB_CONTRACTS` (write=upsert, target runtime-resolved) — vedi proposta Manifest Authority.

### 2-quinquies. Pipe `entries`+`columns` per list→matrice (find→spreadsheet)

Sbloccato lo step spreadsheet, il foglio reale conteneva **placeholder letterali**: il planner metteva `${2.entries.0.path}` nelle celle (e solo per l'entry 0) perche' NON sa esprimere "una LISTA di record → una matrice di righe" coi placeholder scalari. Fix universale §2.10: gli executor spreadsheet consumano `entries` (lista di record, popolata da `from_step`) + `columns` (campi→colonne) e **costruiscono la matrice nel backend** (`local._entries_to_values`, con synonym-map di campo bilingue `descrizione↔description`, `percorso↔path`, …). `entries` e' `runtime_resolved` (iniettato da `from_step`, non chiesto all'LLM); `columns` e' visibile. PATTERN aggiornato: `write_files_spreadsheet(from_step=N, columns=["path","descrizione"])`; capitolo NON: "NON costruire values con placeholder su una lista". Validato il flusso completo `find → resolve_from_step → entries → matrice → .xlsx` con dati VERI. Resta non-deterministica la *decomposizione compound* del planner (a volte fa solo find) — convergenza separata.

## Consequences

- find_images_indices ritorna un conteggio onesto (`available_total` = outlier reali) → il dialog cap §2.11 diventa sensato ("100 di 107", non "100 di 31062").
- "metti in uno spreadsheet … invia per email" funziona self-hosted senza OAuth.
- Rifiuti LLM intercettati prima dell'executor su QUALSIASI executor.
- Regressioni bloccate: `tests/e2e/scenarios/test_spreadsheet_local_relevance_cut.py` (16 test deterministici) + manifest tests dei 3 executor spreadsheet.
- `relevance_cut` è candidato per generalizzare ad altri retrieval scored (follow-up).

## 3. Manifest linter strutturale + wiring synt (3/6)

`runtime/manifest_lint.py` — linter deterministico (§7.9) della "scheda-tool". Nasce dalla constatazione che i bug di stasera erano tutti di FORMA della scheda. **Come evita la trappola semantica senza capire la semantica**: codifica gli INVARIANTI STRUTTURALI la cui violazione *causa* la trappola (le "ombre strutturali"): `PATTERN:` oltre il budget Proposer (`TOOL_DESC_BUDGET=260`, single-source); arg del PATTERN non nello schema; `runtime_resolved` citato in contesto d'USO nel testo visibile (l'apprendimento 2/6, con ECCEZIONE «OMETTI <arg>» = gestione corretta); output-shape per verbo (§2.6); affinity-overlap fra verbi diversi; NON→sibling esistente. Cio' che resta irriducibilmente semantico (bias-verbo `metti→write`) NON viene deciso: si astiene e lo lascia al verifier L6 LLM (ADR 0114) — deterministico (forma) + LLM (significato) complementari.

**Severita'**: `error` (difetti genuini: pattern_args, resolved_hidden) blocca i synth; `warn` (smell: budget, output_shape, chapters, affinity) solo logga (§2.5 vieta il refactor di massa dei legacy).

**Wiring synt** (`synt_multistage` "stage 5.5", PRE-stage6, §7.9 deterministico-prima-dell'LLM): assembla il manifest dagli stage output (name/args/description/affinity) → lint → su `error` `rejected_lint_structural`. Disable `METNOS_SYNT_LINT_DISABLED=1`.

**Lezione (falsi positivi al 1° run su 72 manifest)**: i 4 ERROR iniziali erano del LINTER, non dei manifest — events «OMETTI client» (gestione corretta, non trappola) e write_files `default=` nella prosa ARGS (non in una chiamata). Fix del CHECKER (§8.2): `_pattern_call_args` estrae solo dalle chiamate `name(...)`; `resolved_hidden` esclude il contesto di omissione. Baseline finale: **0 error / 70 warn** sul catalog (i warn = legacy non-CAPITOLI).

**Follow-up rinviato** (scelta utente "con calma"): i `VERB_CONTRACTS` comportamentali (write=upsert, ecc.) — non universali perche' backend-condizionali. Il linter copre la meta' STRUTTURALE; la meta' comportamentale resta fix-d'istanza finche' non si consolida.
