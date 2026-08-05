# Audit DB i18n — copertura, dedup, chiavi orfane, punti appesi

> Prodotto il 22/7/2026 (rev. 2 — scan esteso a TUTTI i file git-tracked, .js incluso; UI riclassificate). SoT: `~/.local/share/metnos/i18n.sqlite` (`i18n(key,lang,text,needs_translation,source_lang,…)`, PK (key,lang)). Facciata `runtime/messages.py`→`runtime/i18n.py`; chiamate `_msg(...)` (1230), `i18n.get` (154), `messages.get` (27), `msg_get` (6). Analisi deterministica: SQL + scan di 1989 file tracciati (ogni estensione) per token `[A-Z_]+` e chiavi puntate incrociate con `executors/`; dup semantici via normalizzazione (case/spazi/placeholder) + Jaccard token. Editing SOLO via `python3 -m admin.i18n_cli`. SOLA LETTURA — nulla modificato.

## 0. Punti appesi / da decidere (leggere prima)

Domande aperte che richiedono una decisione, non un fix meccanico:
1. **Flag `needs_translation` stantìo?** — 1302 entries (739 EN, 563 IT) sono `pending`, ma i testi ESISTONO e molti sono già nella lingua giusta. **Prova che è stantìo**: `TEST_KEY` ha testo EN «Hello world» ed è marcato `pending → en` (inglese da tradurre in inglese), `source_lang=None`. Sembra un residuo del seeding (tutte le chiavi seedate `needs_translation=1` mai azzerato). Se confermato: azzerare per non far girare a vuoto il daemon (`jobs/i18n_translate_pending.py`). Se invece marca «auto-tradotto da rivedere», va tenuto ma allora manca `translated_by`/`auto_translated` (=0 ovunque). **DECISIONE**: stantìo → reset; reale → definire il workflow di revisione.
2. **Pagina proposte telos/unified: cablare o deprecare?** — 64 literal UI (`UI_PROP_TELOS_*`, `UI_PROP_UNIFIED_*`) sono nel DB e nel seed bundled, PROTETTI dal test `test_seed_i18n_gate_keys.py`, ma NESSUN template/route li rende (verificato su tutti i file). Sono la superficie i18n di una pagina admin mai cablata o scollegata da un refactor. **DECISIONE**: se la pagina va costruita → cablarli; se abbandonata → rimuovere chiavi + gate-test insieme. NON un DELETE cieco (il gate-test fallirebbe).
3. **Canale `prompt.*`/`tool.*` nel DB parallelo ai `.j2`?** — 5 record (`prompt.planner.system`, `prompt.synt.stage1`, `prompt.intent_extractor.system`, `tool.request_*_.description`) contengono prompt/descrizioni che vivono anche in `runtime/prompts/<lang>/*.j2` (ADR 0092). Verificare se il runtime li legge dal DB (allora sono la SoT e i .j2 sono la copia) o se sono un doppione stantìo. **DECISIONE**: eliminare il canale ridondante.
4. **Dup letterali: consolidare o tenere?** — alcuni sono label UI brevi legittimamente omonime; altri sono sinonimi d'errore veri (§6). Consolidare solo i secondi.
5. **Near-dup Jaccard (§7.2)**: contiene falsi positivi (token comuni, senso opposto). Sono CANDIDATI da vagliare a mano, non duplicati certi.

## 1. Copertura e simmetria

- **878 chiavi IT + 878 EN = 1756 entries**, 878 distinte, perfettamente simmetriche (0 solo-IT/solo-EN). Soglia >850 ✓.
- Nessun `text` NULL/vuoto nel campo testo corrente.
- **`i18n_cli validate`: 1302 issues** = tutte le entries `pending` (EN 739, IT 563); vedi punto appeso #1.

## 2. Sintesi findings

| Categoria | N | Stato | Azione |
|---|---|---|---|
| Referenziate letteralmente | 737 | vive | — |
| `<executor vivo>.description/affinity` (accesso dinamico) | 46 | vive | — |
| Chiavi i18n da RITIRO executor (`<name inesistente>.*`) | 24 | **peso morto** | eliminare |
| Literal UI di pagina NON cablata (`UI_PROP_TELOS/UNIFIED_*`) | 64 | protette gate-test, non rese | cablare o deprecare (#2) |
| Orfane VERE (placeholder seed) | 2 | spazzatura | eliminare |
| Namespace `prompt.*`/`tool.*` | 5 | dubbio | verificare (#3) |
| Dup LETTERALE (gruppi) | 24 (it 11/en 13) | misto | consolidare i veri |
| Dup SEMANTICO quasi-identici (gruppi) | 24 | misto | uniformare case/punteggiatura |

## 3. Orfane VERE (eliminare)

- `GREET` — IT «Ciao {name}, benvenuto!» / EN «Hi {name}, welcome!» — nessun call-site. Placeholder di seed.
- `TEST_KEY` — IT «Ciao mondo» / EN «Hello world» — nessun call-site. Placeholder di seed.

## 4. Chiavi da RITIRO di executor (peso morto)

`<name>.description`/`.affinity` per executor non più in `executors/`. Nessun call-site.

| Executor fantasma | Sotto-chiavi | Note |
|---|---|---|
| `change_images` | `affinity`, `description` | ritirato |
| `compress_dirs_gz` | `affinity`, `description` | →compress_files |
| `compress_files_gz` | `affinity`, `description` | →compress_files |
| `compute_files` | `affinity`, `description` | →compute_signatures? |
| `describe_dirs` | `affinity`, `description` | ritirato |
| `extract_files_zip` | `affinity`, `description` | →extract_files |
| `extract_lines_text` | `affinity`, `description` | →filter_texts_lines |
| `fetch_urls` | `affinity`, `description` | verbo rimosso →get_urls |
| `get_file_dates` | `affinity`, `description` | assorbito in get_files |
| `get_files_metadata` | `affinity`, `description` | →get_files |
| `list_processes` | `affinity`, `description` | →get_processes |
| `remove_dirs` | `affinity`, `description` | →delete_dirs |

**Fix**: `DELETE FROM i18n WHERE key LIKE '<name>.%'` per ciascuno (verificare che non sia un rename con la chiave nuova già viva).

## 5. Literal UI di pagina non cablata (decisione #2)

64 chiavi `UI_PROP_TELOS_*` (61) e `UI_PROP_UNIFIED_*` (3). Sono i literal della UI proposte telos/unified: presenti in DB + seed bundled, citate come gate-keys da `tests/test_seed_i18n_gate_keys.py`, ma NESSUN template/route le rende (né `chat.html` né `changes.html` né JS). **Contrasto**: le `UI_CHANGE_*` (stessa famiglia UI) SONO rese da `templates/changes.html` → vive, correttamente non-orfane. Elenco completo:
  - `UI_PROP_TELOS_BADGE_HIGH` — «alta» / «high»
  - `UI_PROP_TELOS_BADGE_PARTIAL_MATCH` — «match parziale» / «partial match»
  - `UI_PROP_TELOS_BADGE_PATERNALISM` — «paternalism» / «paternalism»
  - `UI_PROP_TELOS_BADGE_PIPELINE_OBSERVED` — «pipeline osservata» / «pipeline observed»
  - `UI_PROP_TELOS_BADGE_SPECULATIVE` — «speculativa» / «speculative»
  - `UI_PROP_TELOS_BADGE_TOP` — «top» / «top»
  - `UI_PROP_TELOS_BTN_ACCEPT` — «accept» / «accept»
  - `UI_PROP_TELOS_BTN_ACCEPT_CLUSTER` — «accept {n}» / «accept {n}»
  - `UI_PROP_TELOS_BTN_REJECT` — «reject» / «reject»
  - `UI_PROP_TELOS_BTN_REJECT_CLUSTER` — «reject {n}» / «reject {n}»
  - `UI_PROP_TELOS_BTN_STAGE` — «stage» / «stage»
  - `UI_PROP_TELOS_BY` — «da {who}» / «by {who}»
  - `UI_PROP_TELOS_COL_ACTIONS` — «azioni» / «actions»
  - `UI_PROP_TELOS_COL_EA` — «ea» / «ea»
  - `UI_PROP_TELOS_COL_EXAMPLE` — «esempio applicabile» / «applicable example»
  - `UI_PROP_TELOS_COL_IMPACT` — «impatto» / «impact»
  - `UI_PROP_TELOS_COL_ORIGIN` — «origine» / «origin»
  - `UI_PROP_TELOS_COL_PROPOSAL` — «proposta» / «proposal»
  - `UI_PROP_TELOS_CONFIRM_CLUSTER` — «Applico la decisione a tutte le {n} proposte del cluster (stesso target e tipo). Confermi?» / «Apply decision to all {n} proposals in the cluster (same target and type). Confirm?»
  - `UI_PROP_TELOS_CONFIRM_REDUNDANT` — «Questa proposta sembra ricreare un executor già esistente. Sei sicuro di voler accettare?» / «This proposal seems to recreate an existing executor. Are you sure you want to accept?»
  - `UI_PROP_TELOS_CONVERGENCE` — «{n} lenti concordano» / «{n} lenses converge»
  - `UI_PROP_TELOS_CURRENT_LATENCY` — «attuale: {s}s» / «current: {s}s»
  - `UI_PROP_TELOS_DECISION_ACCEPTED` — «accepted» / «accepted»
  - `UI_PROP_TELOS_DECISION_PENDING` — «pending» / «pending»
  - `UI_PROP_TELOS_DECISION_REJECTED` — «rejected» / «rejected»
  - `UI_PROP_TELOS_DECISION_STAGED` — «staged» / «staged»
  - `UI_PROP_TELOS_FILTER_ALL` — «tutte» / «all»
  - `UI_PROP_TELOS_FILTER_GROUP` — «collassa duplicati» / «collapse duplicates»
  - `UI_PROP_TELOS_FILTER_LENS` — «lente» / «lens»
  - `UI_PROP_TELOS_FILTER_ONLY_PENDING` — «solo pending» / «only pending»
  - `UI_PROP_TELOS_FILTER_TELOS` — «telos» / «telos»
  - `UI_PROP_TELOS_FIT_FIT` — «fit» / «fit»
  - `UI_PROP_TELOS_FIT_TELOS` — «telos» / «telos»
  - `UI_PROP_TELOS_FIT_WHY` — «perché» / «why»
  - `UI_PROP_TELOS_HALL_TOOLS` — «tool inesistenti citati» / «non-existent tools mentioned»
  - `UI_PROP_TELOS_LATENCY_NA` — «stima n/d» / «estimate n/a»
  - `UI_PROP_TELOS_LATENCY_SAME` — «stesso n° step (riformulazione)» / «same step count (reformulation)»
  - `UI_PROP_TELOS_LATENCY_SAVED` — «−{s}s stimati» / «−{s}s estimated»
  - `UI_PROP_TELOS_NAME_NEW_INVALID` — «nome invalido» / «invalid name»
  - `UI_PROP_TELOS_NAME_NEW_VALID` — «nome nuovo ok» / «new name ok»
  - `UI_PROP_TELOS_NAME_PARAMETRIC` — «estensione param» / «param extension»
  - `UI_PROP_TELOS_NAME_PIPELINE` — «target esistente» / «existing target»
  - `UI_PROP_TELOS_NAME_REDUNDANT` — «ridondante» / «redundant»
  - `UI_PROP_TELOS_NO_RESULTS` — «nessuna proposta con i filtri correnti» / «no proposal with current filters»
  - `UI_PROP_TELOS_N_OBSERVED` — «osservato {n}×» / «observed {n}×»
  - `UI_PROP_TELOS_PATH_CURRENT` — «path attuale» / «current path»
  - `UI_PROP_TELOS_PATH_NEW` — «path suggerito» / «suggested path»
  - `UI_PROP_TELOS_QUERY_NONE` — «nessuna query reale negli ultimi 14gg» / «no real query in the last 14 days»
  - `UI_PROP_TELOS_RATIONALE` — «rationale + breakdown allineamento» / «rationale + alignment breakdown»
  - `UI_PROP_TELOS_RESULTS_COUNT` — «{n} risultati» / «{n} results»
  - `UI_PROP_TELOS_STATS_ACCEPTED` — «accepted» / «accepted»
  - `UI_PROP_TELOS_STATS_PENDING` — «pending» / «pending»
  - `UI_PROP_TELOS_STATS_REJECTED` — «rejected» / «rejected»
  - `UI_PROP_TELOS_STATS_STAGED` — «staged» / «staged»
  - `UI_PROP_TELOS_STATS_TOTAL` — «totali» / «total»
  - `UI_PROP_TELOS_STEPS_LABEL` — «{a} → {b} step» / «{a} → {b} steps»
  - `UI_PROP_TELOS_TIER_INTERESTING` — «interessanti (0.30-0.45)» / «interesting (0.30-0.45)»
  - `UI_PROP_TELOS_TIER_TOP` — «top (≥0.45)» / «top (≥0.45)»
  - `UI_PROP_TELOS_TIER_WEAK` — «deboli (<0.30)» / «weak (<0.30)»
  - `UI_PROP_TELOS_TITLE` — «Proposte telos engine» / «Telos engine proposals»
  - `UI_PROP_TELOS_TOOL_MENTIONED` — «tool menzionati» / «tools mentioned»
  - `UI_PROP_UNIFIED_COL_SOURCE` — «sorgente» / «source»
  - `UI_PROP_UNIFIED_FILTER_SOURCE` — «sorgente» / «source»
  - `UI_PROP_UNIFIED_TITLE` — «Proposte (tutte le sorgenti)» / «Proposals (all sources)»

## 6. Namespace dinamici `prompt.*`/`tool.*` (decisione #3)

- `prompt.intent_extractor.system` — IT «Sei l'intent extractor di Metnos. Mappa la richiesta utente sul vocabolario chiuso di Metn…»
- `prompt.planner.system` — IT «Sei il pianificatore di Metnos. Risolvi la richiesta dell'utente chiamando i tool disponib…»
- `prompt.synt.stage1` — IT «SEI la fase 1 (NAMING + CLASSIFICATION) di synt-multistage.  OUTPUT OBBLIGATORIO: un solo …»
- `tool.request_location_from_user.description` — IT «USA QUESTO TOOL quando hai gia' chiamato get_location come precursor di una query LOCATION…»
- `tool.request_new_executor.description` — IT «USA QUESTO TOOL quando nessuno degli executor disponibili copre la richiesta utente (es. c…»

## 7. Duplicati

### 7.1 LETTERALI (stesso testo esatto, chiavi diverse) — TUTTI

**EN — 13 gruppi**

| Testo | Chiavi | Call-site |
|---|---|---|
| «Kind» | `MSG_LIFECYCLE_TABLE_KIND`, `UI_CHANGE_COL_KIND`, `UI_CHANGE_FILTER_KIND` | device_shim/messages_i18n.json, lifecycle_summary.py |
| «accepted» | `UI_CHANGE_BADGE_ACCEPTED`, `UI_PROP_TELOS_DECISION_ACCEPTED`, `UI_PROP_TELOS_STATS_ACCEPTED` | http_routes_admin.py, templates/changes.html |
| «pending» | `MSG_ADMIN_PENDING`, `UI_PROP_TELOS_DECISION_PENDING`, `UI_PROP_TELOS_STATS_PENDING` | admin_chat_commands.py, device_shim/messages_i18n.json |
| «rejected» | `UI_CHANGE_BADGE_REJECTED`, `UI_PROP_TELOS_DECISION_REJECTED`, `UI_PROP_TELOS_STATS_REJECTED` | http_routes_admin.py, templates/changes.html |
| «unknown error» | `MSG_ERR_UNKNOWN`, `MSG_ORCH_UNKNOWN_ERROR`, `MSG_UNKNOWN_ERROR` | internal/reports/multidim_findings_21_6.json, device_shim/messages_i18n.json |
| «  …(another {n} omitted)» | `MSG_OMITTED_OTHERS`, `MSG_OMITTED_OTHERS_F` | decisions/0104-runtime-reports-i18n-compliance.md, internal/reports/multidim_findings_21_6.json |
| «Cancel» | `MSG_BTN_CANCEL`, `MSG_LOCATION_BUTTON_CANCEL` | channels/inline_ui.py, device_shim/messages_i18n.json |
| «Reject» | `MSG_BTN_REJECT`, `UI_CHANGE_BTN_REJECT` | exec/get_approval/get_approval.py, agent_runtime.py |
| «Summary» | `MSG_TLDR_PREFIX`, `UI_CHANGE_COL_SUMMARY` | decisions/0104-runtime-reports-i18n-compliance.md, device_shim/messages_i18n.json |
| «applied» | `MSG_LIFECYCLE_KV_APPLIED`, `UI_CHANGE_BADGE_APPLIED` | device_shim/messages_i18n.json, lifecycle_summary.py |
| «source» | `UI_PROP_UNIFIED_COL_SOURCE`, `UI_PROP_UNIFIED_FILTER_SOURCE` | — |
| «staged» | `UI_PROP_TELOS_DECISION_STAGED`, `UI_PROP_TELOS_STATS_STAGED` | — |
| «telos» | `UI_PROP_TELOS_FILTER_TELOS`, `UI_PROP_TELOS_FIT_TELOS` | — |

**IT — 11 gruppi**

| Testo | Chiavi | Call-site |
|---|---|---|
| «errore sconosciuto» | `MSG_ERR_UNKNOWN`, `MSG_ORCH_UNKNOWN_ERROR`, `MSG_UNKNOWN_ERROR` | internal/reports/multidim_findings_21_6.json, device_shim/messages_i18n.json |
| «Annulla» | `MSG_BTN_CANCEL`, `MSG_LOCATION_BUTTON_CANCEL` | channels/inline_ui.py, device_shim/messages_i18n.json |
| «Rifiuta» | `MSG_BTN_REJECT`, `UI_CHANGE_BTN_REJECT` | exec/get_approval/get_approval.py, agent_runtime.py |
| «Rollback» | `UI_CHANGE_BTN_ROLLBACK`, `UI_CHANGE_TAB_ROLLED_BACK` | templates/changes.html |
| «Tipo» | `UI_CHANGE_COL_KIND`, `UI_CHANGE_FILTER_KIND` | templates/changes.html |
| «accepted» | `UI_PROP_TELOS_DECISION_ACCEPTED`, `UI_PROP_TELOS_STATS_ACCEPTED` | — |
| «pending» | `UI_PROP_TELOS_DECISION_PENDING`, `UI_PROP_TELOS_STATS_PENDING` | — |
| «rejected» | `UI_PROP_TELOS_DECISION_REJECTED`, `UI_PROP_TELOS_STATS_REJECTED` | — |
| «sorgente» | `UI_PROP_UNIFIED_COL_SOURCE`, `UI_PROP_UNIFIED_FILTER_SOURCE` | — |
| «staged» | `UI_PROP_TELOS_DECISION_STAGED`, `UI_PROP_TELOS_STATS_STAGED` | — |
| «telos» | `UI_PROP_TELOS_FILTER_TELOS`, `UI_PROP_TELOS_FIT_TELOS` | — |

Consolidamento REALE consigliato (non label UI omonime): `MSG_ERR_UNKNOWN`+`MSG_ORCH_UNKNOWN_ERROR`+`MSG_UNKNOWN_ERROR` (errore sconosciuto), `MSG_BTN_CANCEL`+`MSG_LOCATION_BUTTON_CANCEL`, `MSG_BTN_REJECT`+`UI_CHANGE_BTN_REJECT`.

### 7.2 SEMANTICI — quasi-identici (case/punteggiatura/placeholder) — TUTTI

**EN — 15 gruppi**

| Forma normalizzata | Varianti | Chiavi |
|---|---|---|
| «(the answer will be masked in the log)» | «↵(the answer will be masked in the log)» ⁄ «(the answer will be masked in the log)» | `MSG_DIALOG_MASKED_HINT`, `MSG_ORCH_MASKED_HINT` |
| «accept» | «Accept» ⁄ «accept» | `UI_CHANGE_BTN_ACCEPT`, `UI_PROP_TELOS_BTN_ACCEPT` |
| «actions» | «Actions» ⁄ «actions» | `UI_CHANGE_COL_ACTIONS`, `UI_PROP_TELOS_COL_ACTIONS` |
| «applied» | «Applied» ⁄ «applied» | `MSG_LIFECYCLE_KV_APPLIED`, `UI_CHANGE_BADGE_APPLIED`, `UI_CHANGE_TAB_APPLIED` |
| «failed» | «Failed» ⁄ «failed» | `UI_CHANGE_BADGE_FAILED`, `UI_CHANGE_TAB_FAILED` |
| «finalized» | «Finalized» ⁄ «finalized» | `UI_CHANGE_BADGE_FINALIZED`, `UI_CHANGE_TAB_FINALIZED` |
| «observing» | «Observing» ⁄ «observing» | `UI_CHANGE_BADGE_OBSERVED`, `UI_CHANGE_TAB_OBSERVED` |
| «origin» | «Origin» ⁄ «origin» | `UI_CHANGE_COL_ORIGIN`, `UI_PROP_TELOS_COL_ORIGIN` |
| «reject» | «Reject» ⁄ «reject» | `MSG_BTN_REJECT`, `UI_CHANGE_BTN_REJECT`, `UI_PROP_TELOS_BTN_REJECT` |
| «rejected» | «Rejected» ⁄ «rejected» | `UI_CHANGE_BADGE_REJECTED`, `UI_CHANGE_TAB_REJECTED`, `UI_PROP_TELOS_DECISION_REJECTED`, `UI_PROP_TELOS_STATS_REJECTED` |
| «retry» | «Retry» ⁄ «retry» | `MSG_CHAT_FB_RETRY`, `UI_CHANGE_BTN_RETRY` |
| «rolled back» | «Rolled back» ⁄ «rolled back» | `UI_CHANGE_BADGE_ROLLED_BACK`, `UI_CHANGE_TAB_ROLLED_BACK` |
| «source» | «Source» ⁄ «source» | `UI_CHANGE_FILTER_FAMILY`, `UI_PROP_UNIFIED_COL_SOURCE`, `UI_PROP_UNIFIED_FILTER_SOURCE` |
| «stage» | «Stage» ⁄ «stage» | `UI_CHANGE_BTN_STAGE`, `UI_PROP_TELOS_BTN_STAGE` |
| «staged» | «Staged» ⁄ «staged» | `UI_CHANGE_TAB_STAGED`, `UI_PROP_TELOS_DECISION_STAGED`, `UI_PROP_TELOS_STATS_STAGED` |

**IT — 9 gruppi**

| Forma normalizzata | Varianti | Chiavi |
|---|---|---|
| «(altre {} omesse)» | «  …(altre {n} omesse)» ⁄ «…(altre {n} omesse)» | `MSG_OMITTED_OTHERS_F`, `MSG_RESUME_ENTRIES_OMITTED` |
| «azioni» | «Azioni» ⁄ «azioni» | `UI_CHANGE_COL_ACTIONS`, `UI_PROP_TELOS_COL_ACTIONS` |
| «elementi» | «? elementi» ⁄ «elementi» | `MSG_AUTO_FINAL_COUNT_UNKNOWN`, `MSG_TRUNCATED_DEFAULT_WHAT` |
| «in osservazione» | «In osservazione» ⁄ «in osservazione» | `UI_CHANGE_BADGE_OBSERVED`, `UI_CHANGE_TAB_OBSERVED` |
| «origine» | «Origine» ⁄ «origine» | `UI_CHANGE_COL_ORIGIN`, `UI_PROP_TELOS_COL_ORIGIN` |
| «proposte» | «Proposte» ⁄ «proposte» | `MSG_OBJECT_PROPOSALS`, `UI_CHANGE_TAB_PROPOSED` |
| «riprova» | «Riprova» ⁄ «riprova» | `MSG_CHAT_FB_RETRY`, `UI_CHANGE_BTN_RETRY` |
| «rollback» | «Rollback» ⁄ «rollback» | `UI_CHANGE_BADGE_ROLLED_BACK`, `UI_CHANGE_BTN_ROLLBACK`, `UI_CHANGE_TAB_ROLLED_BACK` |
| «sorgente» | «Sorgente» ⁄ «sorgente» | `UI_CHANGE_FILTER_FAMILY`, `UI_PROP_UNIFIED_COL_SOURCE`, `UI_PROP_UNIFIED_FILTER_SOURCE` |

### 7.3 SEMANTICI — near-duplicate (Jaccard≥0.72, non identici) — TUTTI (vagliare, contiene falsi positivi)

**EN — 17 coppie**

| Sim | Chiave A / Testo | Chiave B / Testo |
|---|---|---|
| 1.0 | `UI_CHANGE_TAB_ROLLED_BACK`: «Rolled back» | `UI_CHANGE_BADGE_ROLLED_BACK`: «rolled back» |
| 1.0 | `MSG_PERSONS_LIST_HEADER`: «Enrolled persons ({n}):» | `MSG_PERSONS_LIST_EMPTY`: «No enrolled persons.» |
| 1.0 | `MSG_ORCH_MASKED_HINT`: «(the answer will be masked in the log)» | `MSG_DIALOG_MASKED_HINT`: «
(the answer will be masked in the log)» |
| 1.0 | `MSG_LIFECYCLE_TLDR_MNEST`: «mnest: {n} legacy_orphan decayed» | `MSG_LIFECYCLE_ROW_LEGACY_DECAYED`: «legacy_orphan mnest decayed» |
| 1.0 | `MSG_DOCS_DISCOVERED`: «📄 Documents discovered ({n})» | `MSG_SEARCH_DOCS_HEADER`: «Discovered documents ({n})» |
| 1.0 | `MSG_ADMIN_NO_USERS`: «No users registered.» | `MSG_ADMIN_USERS_HEADER`: «**Registered users**» |
| 1.0 | `ERR_ARG_NOT_LIST`: «Argument '{arg}' must be a list.» | `ERR_ARG_NOT_LIST_OF`: «Argument '{arg}' must be a list of {of}.» |
| 1.0 | `ERR_ARG_NOT_INT`: «Argument '{arg}' must be an integer.» | `ERR_ARG_NOT_NONNEGATIVE_INT`: «Argument '{arg}' must be an integer >= 0.» |
| 0.8 | `MSG_UPLOAD_NO_SIMILAR`: «No similar photos found in the archive.» | `MSG_UPLOAD_SIMILAR_COUNT`: «Similar photos in the archive: {n}.» |
| 0.8 | `ERR_UNDO_NOT_IN_CATALOG`: «Executor no longer in the catalog.» | `MSG_PROPOSALS_EXECUTOR_MISSING`: «(executor «{name}» is no longer in the catalog)» |
| 0.8 | `ERR_ARG_NOT_POSITIVE_INT`: «Argument '{arg}' must be a positive integer.» | `ERR_ARG_NOT_NONNEGATIVE_INT`: «Argument '{arg}' must be an integer >= 0.» |
| 0.8 | `ERR_ARG_NOT_INT`: «Argument '{arg}' must be an integer.» | `ERR_ARG_NOT_POSITIVE_INT`: «Argument '{arg}' must be a positive integer.» |
| 0.78 | `MSG_DEGENERATE_FINAL_ITEMS`: «Processed {n} items. No further action was completed this turn.» | `MSG_DEGENERATE_FINAL_ITEM_ONE`: «Processed 1 item. No further action was completed this turn.» |
| 0.78 | `MSG_COMPUTE_SIZE_TOTAL`: «Total size: {human} ({bytes} bytes) across {count} files.» | `MSG_COMPUTE_SIZE_TOTAL_DIRS`: «Total size: {human} ({bytes} bytes) across {count} files in {dirs} subfolders.» |
| 0.75 | `MSG_CONSENT_GATE_OUTBOUND_N`: «Automated outbound send: {n} items ready. Approve sending?» | `MSG_CONSENT_GATE_OUTBOUND_BRIEF`: «Automated outbound send: {n} items ({brief}). Approve sending?» |
| 0.75 | `ERR_ADMIN_USER_MISSING_SUB`: «Missing user sub-command. Type `/admin help`.» | `ERR_ADMIN_USER_UNKNOWN_SUB`: «Unrecognized user sub-command `{sub}`. Type `/admin help`.» |
| 0.73 | `ERR_ADMIN_FORBIDDEN`: «`/admin` commands are reserved for the host role. You are not authorized.» | `MSG_HELP_ADMIN_RESTRICTED`: «_`/admin` commands are reserved for the host role._» |

**IT — 14 coppie**

| Sim | Chiave A / Testo | Chiave B / Testo |
|---|---|---|
| 1.0 | `MSG_OMITTED_OTHERS_F`: «  …(altre {n} omesse)» | `MSG_RESUME_ENTRIES_OMITTED`: «…(altre {n} omesse)» |
| 1.0 | `MSG_LIFECYCLE_TLDR_MNEST`: «mnest: {n} legacy_orphan decaded» | `MSG_LIFECYCLE_ROW_LEGACY_DECAYED`: «legacy_orphan mnest decaded» |
| 1.0 | `MSG_DOCS_DISCOVERED`: «📄 Documenti scoperti ({n})» | `MSG_SEARCH_DOCS_HEADER`: «Documenti scoperti ({n})» |
| 1.0 | `ERR_ARG_NOT_LIST`: «L'argomento '{arg}' deve essere una lista.» | `ERR_ARG_NOT_LIST_OF`: «L'argomento '{arg}' deve essere una lista di {of}.» |
| 1.0 | `ERR_ARG_NOT_INT`: «L'argomento '{arg}' deve essere un intero.» | `ERR_ARG_NOT_NONNEGATIVE_INT`: «L'argomento '{arg}' deve essere un intero >= 0.» |
| 0.83 | `ERR_ARG_NOT_POSITIVE_INT`: «L'argomento '{arg}' deve essere un intero positivo.» | `ERR_ARG_NOT_NONNEGATIVE_INT`: «L'argomento '{arg}' deve essere un intero >= 0.» |
| 0.83 | `ERR_ARG_NOT_INT`: «L'argomento '{arg}' deve essere un intero.» | `ERR_ARG_NOT_POSITIVE_INT`: «L'argomento '{arg}' deve essere un intero positivo.» |
| 0.8 | `MSG_ORCH_MASKED_HINT`: «(la risposta sara' mascherata in registro)» | `MSG_DIALOG_MASKED_HINT`: «
(la risposta sara' mascherata nel registro)» |
| 0.78 | `MSG_COMPUTE_SIZE_TOTAL`: «Dimensione totale: {human} ({bytes} byte) su {count} file.» | `MSG_COMPUTE_SIZE_TOTAL_DIRS`: «Dimensione totale: {human} ({bytes} byte) su {count} file in {dirs} sottocartelle.» |
| 0.78 | `ERR_ARG_NOT_NONEMPTY_STRING`: «L'argomento '{arg}' deve essere una stringa non vuota.» | `ERR_ARG_EMPTY_LIST`: «L'argomento '{arg}' deve essere una lista non vuota.» |
| 0.75 | `MSG_CONSENT_GATE_OUTBOUND_N`: «Invio automatico verso l'esterno: {n} elementi pronti. Approvo l'invio?» | `MSG_CONSENT_GATE_OUTBOUND_BRIEF`: «Invio automatico verso l'esterno: {n} elementi ({brief}). Approvo l'invio?» |
| 0.75 | `ERR_ARG_NOT_STRING`: «L'argomento '{arg}' deve essere una stringa.» | `ERR_ARG_NOT_NONEMPTY_STRING`: «L'argomento '{arg}' deve essere una stringa non vuota.» |
| 0.75 | `ERR_ARG_NOT_LIST_OF`: «L'argomento '{arg}' deve essere una lista di {of}.» | `ERR_ARG_EMPTY_LIST`: «L'argomento '{arg}' deve essere una lista non vuota.» |
| 0.75 | `ERR_ARG_NOT_LIST`: «L'argomento '{arg}' deve essere una lista.» | `ERR_ARG_EMPTY_LIST`: «L'argomento '{arg}' deve essere una lista non vuota.» |

## 8. Riproducibilità

`sqlite3 ~/.local/share/metnos/i18n.sqlite`; conteggi/dup letterali via SQL GROUP BY; orfane via scan token su `git ls-files` (1989 file, ogni estensione) incrociato con `executors/`; semantici via normalizzazione + Jaccard. `python3 -m admin.i18n_cli {stats,validate,pending,list,get,set}` dalla dir `runtime/`.

## 9. Remediation verificata — 22/7/2026

Esito: reperti confermati e corretti. La baseline finale è **779 chiavi × 2
lingue = 1558 righe**, simmetrica IT/EN, senza testi nulli/vuoti e con
**0 pending / 0 issue validate** sia nel DB live sia nel seed bundled.
`PRAGMA integrity_check=ok` su entrambi; key-set live/seed identico. Le sole
6 differenze di testo fra live e seed sono intenzionali: descrizioni live
aggiornate o sanitizzate nel seed (`read_messages`, `move_files`,
`MSG_TASKS_LIST_ROW`), due lingue per chiave.

Decisioni sui punti appesi:

1. Le 1302 pending erano stato legacy stantio, non una coda di review:
   nessuna aveva `source_text_hash`, nessuna era `auto_translated`, 680
   avevano `source_lang=NULL` e 490 puntavano alla propria lingua. Sono state
   accettate come baseline tramite `admin.i18n_cli repair-pending
   --all-complete`, ricostruendo anche i link sorgente/hash su tutte le 779
   coppie complete; nessun testo è stato modificato dalla bonifica. Il dry-run
   finale di `align_messages` è 0 chiavi/0 righe sia live sia seed.
2. La superficie `UI_PROP_TELOS_*`/`UI_PROP_UNIFIED_*` è stata deprecata:
   64 chiavi rimosse da live e seed. La verifica ha corretto una premessa del
   report iniziale: nel checkout corrente non erano protette dal gate-test;
   il test proteggeva solo la pagina viva `/admin/changes` (`UI_CHANGE_*`).
3. Le 5 chiavi `prompt.*`/`tool.*` non avevano alcun reader runtime: rimosse.
   I prompt `.j2` restano l'unico canale operativo.
4. Rimossi `GREET`, `TEST_KEY` e i 24 metadati dei 12 executor ritirati.
   Ripuliti anche i loro nomi obsoleti dalle whitelist testuali del traduttore
   di prompt.
5. Consolidati soltanto i duplicati realmente equivalenti:
   `MSG_ERR_UNKNOWN` sostituisce le due varianti ORCH/generica,
   `MSG_BTN_CANCEL` sostituisce la variante location e `MSG_BTN_REJECT`
   sostituisce la variante della pagina changes. Le omonimie con semantica o
   contesto UI distinto restano separate.

Correzioni generali anti-ricorrenza:

- `register_key_if_missing` non usa più `needs_translation` come flag di
  review; una coppia bilingue viene scritta atomicamente e resta completa.
- `set_catalog_translations` è il percorso comune per seed/manifest/coppie
  editoriali; `i18n_migrate_manifests` lo usa evitando l'invalidazione tra
  due `set()` consecutivi.
- Le pending azionabili richiedono sorgente esplicita, diversa dal target e
  non vuota; il filtro precede `LIMIT`, eliminando la starvation della coda.
- Gli stub synth materializzati usano `auto_translated=1` per la review e
  `needs_translation=0`; hash/versioni sono aggiornati coerentemente.
- Il conteggio del one-shot riporta il numero reale di pending azionabili,
  non il precedente booleano mascherato `0/1`.
- Timer systemd e scheduler `every_6h` convergono ora sullo stesso callback;
  un `flock` interprocesso non bloccante impedisce due traduzioni concorrenti.
  Lo scheduler resta come fallback del timer, non come secondo motore.
- Lo skip idempotente drena davvero la pending; prima restava in coda per
  sempre. Ogni traduzione aggiorna `version_hash` e `source_text_hash` v2.
- Un gate deterministico rifiuta output che perdono/inventano placeholder e
  ritenta una volta. L'UPDATE finale è compare-and-set sul testo sorgente: se
  cambia durante la call LLM, la traduzione obsoleta non viene salvata.
- La primitive di enqueue è ora un upsert: può riaccodare un target esistente
  conservandone il testo di fallback, e rifiuta sorgente mancante/self-loop.
- Il timer è `Persistent=true`, il service ha timeout esplicito di 10 minuti e
  logga reason/error/pending azionabili. Il lock file è mode `0600` ed è
  rilasciato dal kernel anche su crash.
- Nuove guardie `test_i18n_catalog_hygiene.py` impediscono pending nel seed,
  namespace ritirati/ridondanti e perdita di simmetria.

Prove automatiche live: smoke IT→EN via timer con LLM locale `wise`,
placeholder `{name}`/`{count}` preservati, hash v2 e `translated_by` presenti,
evento audit JSONL `status=ok`, coda drenata; chiave di prova poi rimossa. Il
ramo scheduler reale è stato forzato una volta: run 227, `success`, 2 ms, zero
failure, prossimo fallback ricalcolato a +6 ore. Anche il fire systemd reale
delle 16:51 ha concluso `no_pending`, errori 0, senza alterare il catalogo.

Durante la verifica il timer ogni cinque minuti ha intercettato il nuovo filtro
prima del reset e ha processato 25 righe legacy: 14 testi sono rimasti
identici, 11 sono cambiati. Il timer è stato fermato, gli 11 testi sono stati
ripristinati via CLI da una snapshot precedente e verificati byte per byte.
Backup recuperabile del DB pre-bonifica:
`~/.local/share/metnos/i18n.sqlite.pre-audit-i18n-20260722.bak`.
