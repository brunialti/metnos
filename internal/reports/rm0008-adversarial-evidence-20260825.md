# RM-0008 — Base di prova della revisione adversarial (25 agosto 2026)

Allegato a `internal/roadmap/RM-0008-porta-unica-nascita-executor.md`.

La roadmap è stata consolidata in dossier esecutivo e ha perso le citazioni
puntuali su cui poggiano le 33 correzioni. Questo file le conserva: una riga per
fatto verificato sul codice vivo al 25 agosto 2026, con il punto esatto in cui è
osservabile. Serve a chi implementa (sa dove intervenire) e a chi certifica (può
rifare la verifica). Non contiene decisioni: quelle stanno nella roadmap.

Le righe descrivono lo stato **prima** dell'implementazione di RM-0008. Una riga
smentita da una verifica successiva va corretta qui, non cancellata.

## 1. Confine del publisher

| Fatto | Dove |
|---|---|
| 30 scope classificati `operational_producer`, 27 con autorità di pubblicazione, ritiro o rollback, su 12 moduli | `internal/reports/rm0007-m4-boundary-inventory.json` |
| un'operazione di riavvio pubblica contratti arbitrari (`sign_first`) | `runtime/stack_reconcile.py:462-487`, chiamata da `:805` e `:1041` |
| il ramo che estende un manifest qualsiasi, builtin compresi, e ripubblica | `runtime/change_applier_extend.py:87-167` |
| creazione di executor da intenzione di cambiamento | `runtime/change_applier.py:60-100` |
| approvazione che copia e pubblica fidandosi del solo `all_passed` | `runtime/synt.py:1593-1717` |
| la guardia statica del confine esiste, con 32 prove, ed è verde sul repository | `runtime/contract_boundary_guard.py`; `tests/runtime/contracts/test_contract_boundary_guard.py:598` |
| riattivazione usata dalla reinstallazione di una skill importata | `runtime/cli/skills_cli.py:113-129`; `runtime/contract_store.py:4296-4466` |
| rollback autentica firma, digest e nome, non l'ammissione | `runtime/contract_store.py:4472-4530` |

## 2. Controlli che cedono in permissivo

| Fatto | Dove |
|---|---|
| eccezione nello stadio 6 → il candidato viene ammesso con un log di avviso | `runtime/synt_multistage.py:607-615` |
| stadio 6 saltato se descrizione o codice sono vuoti | `runtime/synt_multistage.py:589` |
| controllo di sovrapposizione affinity indisponibile = passato | `runtime/jobs/promoter_promote.py:227` |
| batteria smoke indisponibile = passata | `runtime/jobs/promoter_promote.py:256` |
| il promoter si fida dello stato generico `synthesized` | `runtime/jobs/promoter_promote.py:288-299` |
| verdetto semantico non-dizionario = passato; manca `aligned` = passato | `runtime/skill_admission.py:481-485` |
| revisione degli importati su percorso ricostruito `<nome>/<nome>.py` | `runtime/skill_admission.py:686-690` |
| prove di nascita senza riepilogo e uscita 0 = passate | `runtime/synth_request.py:88` |
| interruttori d'ambiente che spengono i controlli | `METNOS_SYNT_STAGE6_DISABLED`, `METNOS_STAGE6_VERIFY_IMPORTED`, `METNOS_SMOKE_AT_IMPORT`, `METNOS_SYNT_LINT_DISABLED` |
| sostituzione del revisore con funzione arbitraria da ambiente | `runtime/skill_admission.py:444` |
| scelta del binding del revisore da ambiente | `LLM_VERIFY_MODELS`, `runtime/synt_stage6_verify.py:161-165` |
| scadenza dichiarata e mai applicata | `runtime/synt_stage6_verify.py:121-127` |
| kill-switch della grazia in sola osservazione salvo variabile | `runtime/jobs/promoter.py:369-375` |

## 3. Ordine, sandbox e prove

| Fatto | Dove |
|---|---|
| il candidato è pubblicato nello store **prima** delle prove di nascita | `runtime/synth_request.py:267` contro `:701` |
| il fallimento cancella la sola cartella di autoria, ingoiando l'errore | `runtime/synth_request.py:706-709` |
| il codice generato è eseguito sull'host senza sandbox durante la sintesi | `runtime/synt.py:887` |
| il modello scrive shell di setup/teardown nel manifest | `runtime/synth_request.py:245-248` |
| il runner esegue quella shell con l'ambiente dell'host | `runtime/test_runner.py:52-58` |
| nessuna scadenza su setup/teardown né sul candidato | `runtime/test_runner.py:57`, `:90-96` |
| il chiamante uccide il padre a 60 s: i discendenti sopravvivono (§10.8) | `runtime/synth_request.py:66-70` |
| la sandbox reale degrada in silenzio senza `bwrap` o con variabile | `runtime/sandbox.py:682` |
| primitiva riusabile: sessione POSIX, scadenza, limiti, uccisione del gruppo | `runtime/bounded_subprocess.py:55-67,129,221-227` |
| 24 manifest su 85 dichiarano shell di setup/teardown | `executors/*/manifest.toml` |

## 4. Identità e cache

| Fatto | Dove |
|---|---|
| il framing canonico length-delimited esiste già, su tre file fissi | `runtime/contract_store.py:72-76,678-705` |
| i file di codice non fanno parte della generazione immutabile | `GENERATION_FILES`, `runtime/contract_store.py:72-76` |
| il digest del codice è riverificato a ogni invocazione | `runtime/invocations.py:265-295` |
| la firma delle cache usa il digest del **codice**, non del manifest | `runtime/engine/cache_validity.py:46-53,108-116`; `runtime/loader.py:405,1956` |
| `pool_sig` firma i soli nomi della famiglia | `runtime/engine/cache_validity.py:147-164` |
| gli indici del prefiltro firmano i soli nomi | `runtime/prefilter_strategies/_catalog_sig.py:11-15`, usato da `bloom.py:57` e `cached_token_flat.py:43`; `trie.py:32-34`; `trie_v2.py:56-58` |
| l'indice FTS5 firma nome più digest del codice | `runtime/prefilter_strategies/fts5.py:31-35` |
| `generation_id` è già presente sull'oggetto executor | `runtime/loader.py:406,1957` |
| affinity e descrizione entrano nel punteggio di routing | `runtime/prefilter.py:533-546` |

## 5. Ciclo di vita, invecchiamento, riscontro

| Fatto | Dove |
|---|---|
| il ciclo di vita assente vale `active` | `runtime/loader.py:397,1610,1653` |
| stati candidati ammessi | `CANDIDATE_LIFECYCLES`, `runtime/executor_standard.py:26` |
| transizioni per manifest generati | `runtime/generated_executor_contract.py:20-25` |
| l'invecchiamento sovrascrive il ciclo di vita firmato, per nome e senza firma | `runtime/executor_aging.py:307-325`; applicato in `runtime/loader.py:1228` |
| lo stato di invecchiamento è indicizzato per nome e non si azzera alla rinascita | `runtime/executor_aging.py:69` (chiave primaria), `:169-206` |
| `undeprecate()` non ha alcun chiamante | `runtime/executor_aging.py:462` |
| il riscontro negativo demota per nome | `runtime/turn_feedback.py:180`; `runtime/executor_aging.py:802-858` |
| i lavori durevoli riverificano il ciclo di vita a ogni tentativo | `runtime/durable_workloads/compiler.py:531-559`; `runtime/durable_workloads/execution.py:226-244,719-742` |
| la promozione automatica non richiede approvazione umana | `runtime/jobs/promoter.py:415-482`, pianificata alle 04:45 |

## 6. Modelli, capacità, conservazione

| Fatto | Dove |
|---|---|
| i livelli fast, middle, wise e creative puntano allo stesso modello fisico | `runtime/llm_router.py:115-162` |
| il livello frontier è facoltativo e viene rimosso se non configurato | `runtime/llm_router.py:487-493` |
| il registro dei carichi non esprime un tier minimo né un'escalation | `runtime/llm_workloads.py:29-100` |
| 29 capacità con criticità, approvazione e tipo di bersaglio | `runtime/policy.py:48-215` |
| tabella autonomia × capacità | `runtime/policy.py:242-268` |
| capacità ammesse a livello ReadOnly: 8, fra cui tre non accettabili per un executor non provato (`metnos:cache`, `metnos:credentials_metadata_only`, `dialog.user_input`) | calcolo su `runtime/policy.py` |
| la richiesta di ampliamento del cap è comportamento del runtime | `runtime/agent_runtime.py:4399-4520` |
| la convenzione `entries`/`results` è dichiarata SHOULD e controllata solo sulla scheda, a severità di avviso | `runtime/manifest_lint.py:650-672` |
| la rotazione dell'audit cancella le generazioni più vecchie | `runtime/audit_jsonl.py:508-524` |
| conservazione delle proposte: 30 giorni, dedupe 7, ultime N per tipo | `runtime/proposals_cleanup.py:46,216,257` |

## 7. Stato misurato dell'installazione di riferimento

| Misura | Valore |
|---|---|
| generazioni nel deposito dei contratti | 122, nessuna con ciclo di vita diverso da `active` |
| proposte trattate dal promoter in tutta la storia | 8: 6 archiviate dal valutatore, 2 in attesa di revisione umana, **0 promosse** |
| cartelle di candidati sintetizzati | 12, ciascuna con il solo file `.py`, senza manifest né firma, ferme dal 18-27 maggio |
| manifest che dichiarano prove di nascita | 85 su 85 |
| layout dei manifest | `STORE_ONLY` |
