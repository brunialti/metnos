# Mandato Fable — Engine, Remote Executors (mutanti), Introversione, Manutenzione
### Modalità: UNA AREA ALLA VOLTA. Consolida. Poi la successiva.

Sei un ingegnere autonomo su **Metnos** (assistente self-hosted su `.33`, Strix Halo 96GB). Questo mandato copre quattro aree di **analisi profonda + intervento** più una fase di orientamento/chiusura punti aperti.

> **REGOLA OPERATIVA CENTRALE (non negoziabile).** Affronti **una sola area per volta**. Non apri l'area successiva finché quella corrente non è **CONSOLIDATA** (definizione sotto) e Roberto ha dato l'ok al passaggio. Niente fronti paralleli, niente «intanto tocco anche l'altra».

---

## A. Cosa significa «CONSOLIDATA» (cancello d'uscita di ogni area)
Un'area è consolidata SOLO quando TUTTI questi punti sono veri e li hai **dimostrati** (non asseriti):

1. **ADR** nuovo o aggiornato in `decisions/` che documenta analisi (con marcatura ✓ misurato / ⚠ ipotesi) e decisione.
2. **Test verdi**: suite dei moduli toccati + cluster + i bench pertinenti + il gate §2.8 `runtime/tests/test_compound_spreadsheet_execution.py`. Nessun test rosso nuovo. Test rotto = fixa il codice, non il test.
3. **Turno reale** per OGNI cambio funzionale: `agent_runtime.run_turn(q, actor="host", channel="http")` con env prod (`METNOS_ENGINE=v3` + `METNOS_PROPOSER_GRAMMAR=1` + `METNOS_PROPOSER_VERB_FILTER=1` + `METNOS_PREFILTER_RULES=1` + `METNOS_LLM_SEED=42`). Esito COME ATTESO, non «è partito». No gaming.
4. **Nessuna regressione** sulla classe compound «cerca/elenca X → costruisci foglio» (è a **errore=0**, 10 commit sopra `3494c76`): ri-verificala col gate §2.8 e col bench `compound_extract_create_bench.py` (8/8).
5. **Doc allineata**: `docs/it/architecture/*.html` + `docs/en/architecture/*.html` aggiornati + `./deploy.sh` (§9). Niente backlog «doc poi».
6. **Commit modulari** (messaggi in italiano, **niente `Co-Authored-By`**, branch dev **non pushato**).
7. **Firma**: ogni edit di `executors/<x>/<x>.py` o del suo `manifest.toml` → `python3 runtime/sign.py sign executors/<x>` + restart. Prod ricaricato (`sudo -n systemctl restart metnos-http.service`) e riverificato con un turno.
8. **Report d'area** (in `internal/reports/`): cosa fatto, cosa misurato ✓, cosa resta ⚠, prossimi passi.
9. **Memoria aggiornata**: `~/.claude/projects/-opt-metnos/memory/project_session_<data>.md` + una riga in `MEMORY.md`.

**Al cancello: FERMATI.** Presenta il report di consolidamento a Roberto e aspetta l'ok prima di aprire l'area successiva. Se un'area è troppo grande per un colpo solo (l'Engine lo è), usa i **checkpoint interni** indicati: consolida al checkpoint, fai il punto con Roberto, prosegui nell'area.

---

## B. Regole di processo — valgono in tutte le aree
- **Leggi PRIMA `/opt/metnos/CLAUDE.md` INTEGRALE.** Se un punto è obsoleto/errato → aggiornalo PRIMA della PR (§13).
- **Onestà §2.8**: mai dichiarare un esito non reale; `ok_count` = elementi REALMENTE processati; marca ✓/⚠ nei report.
- **No hardcoding §7.3**: forma astratta + regola sistemica prima del fix; lessici IT+EN in `detection_lexicon`, mai inline.
- **Deterministico > LLM §7.9**; **semplicità §7.2**; **no backward-compat §7.1** (pre-1.0, rompi pure).
- **Prompt §6** (ogni prompt LLM prescrittivo): DEVI/NON DEVI/OK/ERRORE; funzioni lingua-indipendenti in inglese imperativo breve.
- **Fermati e CHIEDI a Roberto** per: nuove decisioni di design, estensione vocabolario §2.2 (3 criteri: necessario+generale+comprensibile), azioni distruttive o outbound (cancellazioni Drive, invii mail/ACL), scelte di scope.

## C. Orientamento (fatti verificati, non regredire)
- Engine di PROD = **`METNOS_ENGINE=v3`** (drop-in `proposer-hardening.conf`, verificato sul MainPID). NON metis. I guard compound sono **v3-gated** (`is_v3()`); i bench compound girano in v3 (CLAUDE.md §11 corretto, commit `0b6b37a`).
- File chiave: `runtime/engine/{dispatch,proposer,executor}.py`; planner legacy VIVO in `runtime/agent_runtime.py`. Bench: `bench/compound_{dryrun,extract_create_bench,scaling_bench}.py`, `bench/routing_subset_bench.py`.
- La sessione appena chiusa ha AGGIUNTO guard clause-scoped a `dispatch.py` (`_decontaminate_reader_qualifier`, `_scope_sink_provider_to_clause`, extract STRUTTURALE, FIX-5b writer-coverage, `_clause_scoped_drive_term`): sono la conferma vivente che **la batteria guard È il planner compound** — includili nel consolidamento dell'Engine.

---

# FASE 0 — Orientamento + chiusura punti aperti (fai questa PRIMA)
Obiettivo: repo pulito e testa nel contesto, prima di aprire l'Engine. Consolida secondo §A (ADR non necessario; bastano commit + report).

1. **Residuo working-tree** (da altre sessioni/cron — TUOI da committare, feedback «own-cron-other-session»): `docs/*`, `internal/design/*`, `client-rs/README.md`, `internal/reports/*`, `executors/{find_files,read_files,read_files_doc}/manifest.lang_state.json` (bookkeeping i18n da `fa0e988`). **Leggi il diff, capisci di chi/cosa è**, committa in **commit modulari** (non mescolarli).
2. **`find` nome-esatto (`f5ded21`)**: `find` Drive ora preferisce il match col NOME ESATTO fra i risultati fullText fuzzy (dà il KAKEBO pulito, niente sbavatura «2025-26»), ma cambia la semantica «trova i doc su tema X». **CHIEDI a Roberto**: tenere la preferenza o `find` puramente vettoriale? Agisci sulla risposta.
3. **Issue B — form/colloquio disambiguazione (web chat)**: lamentela ricorrente («non usa form per selezione scelte, non gestisce il colloquio»). La macchina ESISTE (`orchestration.orchestrate_needs_inputs`→`dialog_pending.save_pending`; resume via `http_routes_agent._apply_dialog_pending` con fallback `alt_sender`; route `GET /agent/dialog/<id>/form`); la chat rende le scelte come TESTO, non pulsanti. **Riproduci** una disambiguazione GENUINA cross-dominio (es. «leggi le mail e i file pdf») per tracciare il gap render/colloquio; poi rendi le scelte cliccabili + irrobustisci il resume. (Il caso files/dirs `0264fcf9` è già chiuso alla radice.)
4. **697 live**: `elenca la cartella C:\Windows\System32\drivers\etc sul PC-ROBERTO e metti i path in uno spreadsheet` — piano PULITO (list_dirs→create, no write spurio, stabile ×3) ma non validato live perché il device PC-ROBERTO (`7bd3da08…`) era OFFLINE. Ri-prova a device connesso (coordina con Roberto).

**Cancello Fase 0** → report + ok → Area 1.

---

# AREA 1 — Engine (analisi + intervento)
**Ancore**: `decisions/0177-engine-architecture-review.md` (INTEGRALE) · memorie `project_compound_planning_refactor`, `project_legacy_planner_removal_deadline`, `project_negative_path_cache_invalidation`, `project_engine_v2_dead_in_prod`.

**Stato ✓ (non rifare)**: as-is + smell S1-S8 + target T1-T8 + migrazione M0-M7 mappati. FATTO: M1-UPLOAD (foto→`find_images_indices` su engine v3, gate verde). Decomposer deterministico ELIMINATO (path compound unico = engine).

**Analisi**: aggiorna ADR 0177 allo stato reale post-compound (S2 confermato dai nuovi guard). Verifica gli smell ⚠ leggendo il codice, misurando: S3 (idempotenza guard su cache-hit), S5 (5 percorsi del messaggio finale), S6 (12 decider first-match nell'intake).

**Intervento — checkpoint interni (consolida ad ognuno, fai il punto):**
- **CP1 · M0 (safety, cheap)**: **T4** test di idempotenza guard su cache-hit (`guard(guard(fw))==guard(fw)` su corpus di piani reali, includi i piani compound-spreadsheet) + **T3-test** contratto d'ordine esplicito della pipeline guard. Chiude il rischio silenzioso S3 senza toccare comportamento. → **Consolida qui e checkpoint con Roberto.**
- **CP2 · M2 (T5)**: **Finalizer unico** — una sola fonte del messaggio finale (zero-result→template→synth→describe→policy), de-duplicato, i18n-garantito. Sana S5.
- **CP3 · M3 (T3)**: consolida i **3 guard «produttore mancante»** in uno; estrai la catena resolver di `executor.run` in un **registry ordinato dichiarato**. Con i nuovi guard compound, valuta quali sono generalizzabili in un unico stadio clause-scoped.
- **CP4 · M1-RESUME (S7)**: il planner «legacy» (~3000 LOC in `agent_runtime`) è **VIVO** per il **resume-dialog** (`resume_with_scratchpad`) — NON codice morto. **Prima strumenta** quanto traffico ci cade (0 marker oggi; il monitor cron `~/.local/state/metnos/legacy_probe_monitor.log` deve confermare 0). Poi porta il resume sull'engine v3 (`seed_state` su dispatch/executor/proposer, ~90-120 LOC), e2e resume-dialog via engine, POI rendi incondizionato `METNOS_PLANNER_LEGACY=0` e rimuovi il blocco. Non zero-rischio.
- **CP5 · M4 (T2, incerto → spike dietro flag)**: **grammar-on-args** (GBNF) per vincolare il proposer a emettere arg-required + oggetto/verbo del pool; misura su banco QUANTI guard si possono spegnere. Prototipo, non big-bang.
- **CP6 · M5/M6/M7**: planning-boundary (T1), slim `executor.run` (T6), lessici→`detection_lexicon` (T8, a lotti, regressione-zero).

**Cancello Area 1** (o checkpoint interno) → report + ok → Area 2.

---

# AREA 2 — Remote executors (analisi + **implementazione dei MUTANTI**)
**Ancore**: memorie `project_c7_remote_mutating_executors` (scoping COMPLETO), `reference_remote_executors_design`, `project_session_3_7_2026` (W3.3), `project_list_dirs_remote_fix` · doc `docs/{it,en}/architecture/{remote_executors,sandbox}.html`.

**Stato ✓ (non rifare)**: W3.3 completo sul PC Windows reale (device `7bd3da08…`, client 0.2.7/0.2.9). Remoti live: `get_files`, `compute_files_loc`, `list_dirs`. Scoping C7 = **via (a) estendere lo SHIM** (gli executor file sono dispatcher sottili; la logica sta in `backends/files/local.py` 1911 LOC → forkarla violerebbe §7.1/7.2).

**Analisi (PRIMA di toccare codice)**: conferma le SOTTIGLIEZZE del memory — NON mettere `[placement] scope="device"` nei manifest di PROD; whitelist placement; §2.8 latente: `openpyxl`/`google.*` sono import LAZY non dichiarati → un ramo xlsx/google sul device darebbe ModuleNotFoundError malgrado il gate «stdlib-only». Mappa la chiusura di dipendenze reale per executor (tabella nel memory).

**Intervento — checkpoint interni:**
- **CP1 · Shim albero-package**: `shim_bundle` (`runtime/agent_server.py`) enumera la chiusura con sotto-path + firma; `ensure_shim` (client `client-rs/.../executors.rs`) ammette sottodir (rilassa il guard `/`, **TIENE** `..`). `pythonpath_sep()` già per-OS.
- **CP2 · Pulizia §7.2**: import `google_workspace` reso LAZY in find/read/write_files (il device non lo carica mai).
- **CP3 · Read-only remoti**: `find_files`/`read_files` (chiusura `backends.files.local` + `platform_policy` + `config` + `path_alias`). e2e sul PC reale.
- **CP4 · MUTANTI** (`write_files`/`move_files`/`delete_files`): in più (a) **undo round-trip remoto** — `results` con `blob_path`/`blob_sha256` deve tornare dal device (classe fix `53ba67e`, `restore_blob_backup`/`swap_src_dst` via server, §2.3); (b) **ACL di scrittura nel Job Object Windows** (non ancora costruite; AppContainer=W4). **Gate**: promuovi prima solo il ramo `client="local"` non-xlsx.

**Validazione**: e2e sul PC Windows reale (serve device connesso — coordina con Roberto); round-trip undo remoto verificato; doc remote_executors/sandbox aggiornate + deploy; ADR per shim-tree + mutanti.

**Cancello Area 2** → report + ok → Area 3.

---

# AREA 3 — Introversione (generazione proposte / teleologia)
**Ancore**: ADR **0156** (10 lenti + Naming Authority v3 + GBNF), **0157** (AlignmentEngine), **0180** (igiene filiera + regola dei livelli) · memorie `project_telos_engine_state`, `project_competitive_learning_loop`, `project_fable_review_queue` (§G aperto), `feedback_no_training_amplify_reality` · report `internal/reports/feasibility_learning_loop_2026-05-30.md`.

**Stato ✓ (non rifare)**: Telos fasi 1+2+4 chiuse; dashboard `/admin/changes`. Igiene 0180: generatori `specialize`/`generalize` ritirati (introvertiva=solo dedupe); adapter attivi = telos (a cluster-head), introvertiva, synt, user_feedback; auto-evaluator killer `layer_overlap`; `alignment_engine` v1.4. Accept di una `materialize_pipeline` = ESEGUIRLA una volta in `scheduled_turn_scope`.

**Analisi**: audit profondo della **regola dei livelli** (L0 fastpath=query stessa; L1 autopath=piano generalizzato per cluster; executor=tool singolo; skill=bundle) contro ogni generatore/adapter (no-sovrapposizione). **Generazione a secco** dei telos (§G): distribuzione EA reale, non simulata. Convergenza del loop notturno senza rigenerare le rifiutate (anti-resurrezione `rejected_targets`).

**Intervento — W1 skill-learning loop** (priorità 1 del competitive-learning-loop): trigger a 2 soglie su ESITO TURNO REALE — (a) turno OK engine `n_step≥SEED_STEPS(4)` → `autopath.seed_from_run` (shadow); (b) lacuna terminator `n_seen≥PROPOSE_SEEN(3)` → `change_intent`→synt dietro admission 6-layer; job review `every_72h`. Innesti: `engine/dispatch.py`, `engine/terminator.py::explain`, `introvertiva.py`, scheduler. Riusa `autopath.sqlite` + `change_intents.sqlite`. **Rispetta [[feedback-no-training-amplify-reality]]** (cache/seed, NON training ML; il loop NON estende il vocab §2.2 da solo).

**Decisioni aperte da CHIEDERE a Roberto** (report §8): auto-apply proposte synt vs triage `/admin/changes`; calibrazione soglie `SEED_STEPS`/`PROPOSE_SEEN`/`REVIEW_EVERY`; storage user-prefs (USER.md vs tabella); governance vocab.

**Cancello Area 3** → report + ok → Area 4.

---

# AREA 4 — Gestione / manutenzione
**Ancore**: `feedback_maintenance_via_scheduled_commands` (🚨 requisito Roberto), `project_github_domain`, `reference_aging_inactivity_trap`, `reference_nightly_fable_audit`, `project_fase2_issue_triage_procedure`.

**Principio 🚨 (non negoziabile)**: la manutenzione di un dominio esterno (caso: issue di un repo GitHub) NON è un programma/funzione core ad-hoc. È una **QUERY SCHEDULATA in linguaggio naturale** che compone gli **executor** via planner (`scheduler_v2` `callback_key="run_user_query"` + `payload={query}`). §2.1/§7.2/§8.3.

**Intervento**:
1. **Rimuovi l'ANTI-PATTERN**: `runtime/jobs/github_watcher.py` (+ `builtin_callbacks::github_watcher`, `github_watch_state`, `github_issue_qa_store`) → sostituiscilo con **comandi schedulati** (A: «leggi le issue nuove del repo, classificale, preparale per il trattamento»; B: «leggi le risposte approvate alle issue e postale»). Executor mancanti → sintetizza (`request_new_executor`), non hardcodare.
2. **Analizza i meccanismi di manutenzione**: aging-inattività (solo synth reattivi invecchiano; CRUD+core mai — `reference_aging_inactivity_trap`), audit notturni Fable (05:00; gotcha +x/fork da HEAD), scheduler v2. Verifica che siano ispezionabili/modificabili come comandi, non logica bespoke; **documenta il confine** comando-vs-bespoke.

**Validazione**: il flusso issue-repo gira come task schedulati reali (turno `kind=answer`), non «è partito»; ADR per la migrazione `github_watcher`→comandi; doc aggiornata.

**Cancello Area 4** → report finale complessivo.

---

## D. Ordine e cadenza (riassunto)
**Fase 0** (orientamento + punti aperti) → **Area 1 Engine** (CP1 M0 safety è il primo cancello) → **Area 2 Remote (mutanti)** → **Area 3 Introversione (W1)** → **Area 4 Manutenzione**.
Per ciascuna: leggi le ancore → ADR/analisi (✓/⚠) → intervento incrementale (test verdi + turno reale, no regressioni compound) → doc+deploy → report → **cancello + ok Roberto** → area successiva.

## E. Nota costo
Le finestre Fable sono care (feedback `fable_cost_window`). Concentra ogni sessione su UN cancello: portalo a consolidamento e fermati, invece di sfiorare più aree. Se una sessione si esaurisce a metà di un'area, lascia un report ⚠ con lo stato esatto (file:riga) per la ripresa.
