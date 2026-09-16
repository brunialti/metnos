# Metnos — Indice meccanismi anti-regressione

> Estratto da CLAUDE.md §10.6 (2026-06-05) per mantenere CLAUDE.md snello.
> Una riga per meccanismo, solo call-site essenziale (file/funzione/policy).
> Dettagli, env vars, bench numbers, date → ADR (`decisions/`).
> Aggiornare QUI (non in CLAUDE.md) quando si aggiunge/rimuove un meccanismo.

**Naming / vocab / grammatica**
- **Avvio e manutenzione separati dalla certificazione** (ADR 0225):
  `executor_birth_admin_preflight._attest_service_startup_v1`,
  `metnos_http_server.maintenance_middleware`, `sandbox.mail_extras`;
  test preflight/launch, HTTP maintenance e selezione SMTP per invocazione.
- **Ammissione linguistica versionata e atomica** (ADR 0219-0220, RM-0005):
  `i18n_registry` censisce risorse e lease; `i18n_materializer` enumera prompt,
  contratti, messaggi/UI, lessico, documenti, device e Tutor;
  `i18n_pipeline` conserva struttura e identità canoniche;
  `i18n_activation.gate` rilegge gli artefatti promossi, verifica equivalenza,
  firme, corpus pubblico e assenza di nuove forme private prima della modifica
  firmata di `instance_lang`. Il job notturno è limitato e non attiva. Il lint
  vieta override per utente/turno; il device non incorpora prosa. Guard:
  `tests/runtime/i18n/`, in particolare la fixture end-to-end in
  `test_i18n_activation.py`.
- **Action vocabulary localizzato e completo** (RM-0005 F2/F3/F6/F8):
  `vocab.action_surfaces` nel detection lexicon conserva le superfici per
  lingua; `VOCAB_ACTION_*_BOUNDARY` nel catalogo i18n conserva i confini. Il
  prefilter deriva le sole forme monolessematiche non ambigue dalla risorsa
  attiva, synt rende una sola lingua e il bootstrap accoda entrambi i cataloghi.
  Un mapping tradotto parziale o con collisioni non viene promosso; il gate
  riporta copertura nativa e polisemie. Prove:
  `test_action_vocabulary_i18n.py`, `test_detection_translate_daemon.py` e
  `test_verb_canonical_sot.py`.
- **Executor Standard v1** (ADR 0193): `EXECUTOR_STANDARD.md` e' il contratto
  normativo `metnos.executor/1.0`; `runtime/executor_standard.py` valida i claim
  meccanici, compresi i nomi chiusi di `runtime/policy.py::CAPABILITY_REGISTRY`.
  Legacy senza dichiarazione ammessi durante la migrazione; una dichiarazione
  presente e' bloccante nel loader se incompleta o sconosciuta.
- **Assi executor distinti** (ADR 0195): appartenenza, origine e trasporto non
  sono sinonimi. I 16 `*_github` sono builtin Metnos con
  `origin="handcrafted"`, senza `[provenance].imported_from`; il codegen GitHub
  e `test_builtin_executor_contracts.py` bloccano la regressione. Inventario
  live verificato: 115/115 standard e firmati, origine 98 handcrafted + 17
  runtime builtin in-process.
- **Politica centrale di esecuzione executor** (ADR 0196):
  `executor_scheduler.invoke_scheduled` avvolge il choke-point universale;
  default classe 0 e pool trasversale spento, metriche e retropressione sempre
  applicate. Le classi 1-3 richiedono prova di equivalenza; gli effetti non
  read-only richiedono anche una chiave d'isolamento e l'identita' runtime.
  `generated_executor_contract.py` vincola tutti i generatori alla stessa
  intestazione e politica seriale; il modello non puo' promuoversi da solo.
- **Visita ricorsiva comune** (ADR 0204-0205):
  `runtime/parallel_walk.py` e' l'unico visitor ricorsivo per gli alberi del
  filesystem; callback `accept`/`transform`/`descend`, frontiera dinamica,
  ricomposizione ordinata, symlink non seguiti ed errori espliciti. Il budget
  totale d'istanza deriva centralmente da CPU visibili e `max_workers`; arriva
  agli executor tramite `executor_workers.assigned_workers` e ogni altro
  limite puo' soltanto ridurlo. Guard: `test_parallel_walk.py`,
  `test_parallel_recursive_executors.py`, test di chiusura device e prove di
  equivalenza nei manifest. `find_urls` mantiene la BFS specializzata ma usa
  `executor_workers.map_ordered`; guard ermetica
  `test_find_urls_parallel_recursive.py`.
- **Duplicati esatti senza cap di sorgente** (ADR 0205):
  `find_files_hash` applica dimensione -> campione negativo -> SHA-256 completo;
  `max_results` limita solo l'output e `source_complete` distingue ogni limite
  della sorgente. Cache per utente senza path in chiaro, valida soltanto sulla
  firma `stat`; guard `test_find_files_hash.py` e prova di nascita concorrente.
- **Naming Authority** (ADR 0156): `runtime/naming_grammar.py` valida nome + genera GBNF da `vocab.py`. Single source per stage 1/telos/skill importer.
- **Manifest linter strutturale** (ADR 0169): `runtime/manifest_lint.py` deterministico (§7.9) — check FORMA scheda-tool (CAPITOLI, PATTERN-budget, PATTERN-args ⊆ schema, output-shape §2.6, affinity-overlap, NON→sibling). Wired synt stage 5.5. CLI `--all`.
- **Constrained generation** (ADR 0133): `runtime/tool_grammar.py` GBNF per ogni step. Loop-detect `runtime/loop_detect.py`. Opt-in `METNOS_GRAMMAR=1`.
- **Grammar pool extensions** (ADR 0135): `final_answer` synthetic from step≥2; `_parse_tool_call_tolerant` JSON recovery; `_FROM_STEP_HELPERS` esclusi al primo step.
- **Skill dormancy + provider qualifier** (ADR 0136): `Executor.dormant` se skill senza credenziali (`runtime/skill_credentials.py`). Gate pool provider: `tool_grammar.provider_gate_names` su `detection_lexicon provider.markers` (chiavi da `vocab.PROVIDER_SUFFIXES`).
- **Invocazioni skill-backed: sandbox+placement** (10/7/2026): `sandbox.invocation_skills` (5 segnali, SoT `vocab.PROVIDER_SKILLS`, guard `test_provider_skills_cover_suffixes`) → bind RW skill home + rete in bwrap (`skill_extras`) e pin server in `invoke_executor` (mai device per backend provider). Test `tests/runtime/safety/test_sandbox_skill_backed.py`. Senza: OAuth-in-loop (token invisibile alla sandbox, dal 9/7 = bubblewrap) e misroute su device.
- **Vocab extension persons+tasks** (ADR 0137): OBJECTS 17→19. Synonyms IT+EN in `vocab.py`.
- **filter_lists + tassonomia liste** (ADR 0138): `filter_lists` (set ops bi-list) vs `filter_entries` (1L predicati). Wire `_resolve_from_step` Layer 5.
- **Builtin scheduler v2 canonical** (ADR 0133 ext): `create/list/delete/read/set_tasks` con fallback `id→name`.
- **`*_tasks` conditional injection**: iniettati nel pool PLANNER solo se query ha marker scheduling (`_TASKS_MARKERS` in `tool_grammar.py`).

**Planner / Praxis / runtime flow**
- **LRE: ammissione ed esecuzione centralizzate** (ADR 0213-0214, RM-0004):
  `runtime/lre_submission.py` classifica il piano finalizzato e converge su
  `admission.submit_candidate`; `runtime/engine/dispatch.py` applica lo stesso
  controllo a scorciatoie, cache e ripresa prima dell'unico call site statico
  di `Executor.run()`. Lo schema v7 congela letterali e collocazione, mentre lo
  store owner-scoped conserva CAS, lease, fencing, eventi, outbox e completezza
  verificabile. Un'azione lunga riconosciuta non ricade mai nell'esecuzione in
  linea se l'ammissione fallisce. Guard: `tests/runtime/durable_workloads/`,
  `tests/runtime/test_lre_submission.py` e
  `tests/runtime/engine/test_engine_core.py`.
- **LRE inattivo senza contesa e progressi verificabili** (ADR 0213, 16/9/2026):
  domanda in sola lettura, presenza aggiornata anche da disabilitato,
  manutenzione indipendente dall'esecuzione e consumi sconosciuti bloccanti.
  Guard: `test_service_idle.py`, `test_execution_accounting_safety.py`,
  `test_service_parallel_progress.py`, `test_durable_console_behavior.py`.
- **Tutor F2 pre-planner senza contaminazione** (ADR 0197-0198, RM-0003):
  `runtime/tutor_boundary.py` è l'unico adapter HTTP/Telegram; il detector
  richiede due segnali dal `detection_lexicon`, esclude allegati/segreti e
  chiarisce le query miste senza eseguirle. L'innesto precede i consumer
  pending ma non li consuma. `runtime/tutor/catalog.py` ammette soltanto il
  catalogo SQLite firmato, read-only e con last-known-good; audience derivata
  solo dal principal autenticato. Il corpus unifica manifest ammessi,
  documentazione allowlist e guide curate; `concept_id` applica fallback
  lingua per singolo concetto verso EN. `tutor-exclude` impedisce che roadmap
  pubblica diventi capacità corrente. BGE-M3 e segnale lessicale derivato
  scelgono la fonte senza `affinity`/`exact`; mode e composer locali usano lo
  slot centrale `llm` e non hanno strumenti. Procedure admin deterministiche.
  Guard: `tests/runtime/tutor/test_tutor_f1.py` (cross-lingua reale,
  shape/firma/recovery, exclusion, separazione operativa, HTTP/Telegram senza
  planner).
- **Praxis Engine — pentade** (ADR 0161): cascata `fast_path→intent_extractor→Praxis.try_match→Mētis→Noûs→Pronoia→Aporia`. `runtime/{praxis,praxis_propose,praxis_executor,pronoia,aporia}.py`, wire pre-PLANNER `agent_runtime.run_turn`.
- **ClusterLLM + classify_fail** (ADR 0162): estende 0161. `runtime/praxis_cluster.py` BGE-M3 + cosine + champion/challenger. `runtime/pronoia_classify_fail.py` dispatch ✗. Constants in `runtime/praxis_constants.py`.
- **Fastpath L0 lifecycle AUTO** (11/6, decisione Roberto — NIENTE bottone approvazione; copertura di classe 12/6): auto-produzione `engine/fastpath.record_success` ← `dispatch._maybe_record_fastpath` da OGNI turno-successo la cui query esatta non è in cache 0a — engine/recovery (origin `auto`), hit L1 (`autopath`: bug live «controlla tutte le mailbox», la famiglia con skill L1 non registrava mai), hit 0b (`cosine`, promozione a 0a); MAI da hit 0a (esclusi undo_last_turn/get_inputs + literal temporale ISO assoluto `_has_absolute_temporal_literal` → replay stantio); pertinenza 0b solo `query_specific=0` (`engine/executor.is_query_specific`, condiviso L1; include `time_window/time_windows` literal — misura 12/6: pivot «oggi/ieri» cosine 0.9722 > soglia 0.92 > parafrasi 0.946, nessuna soglia li separa); aging+morte `fastpath.prune` ← `task_state_reaper` (grazia 14gg / stale 30gg / cap LRU 500, env `METNOS_FASTPATH_*`; C1 tool∉catalog, C2-provenienza ESATTA via tabella `promotions` (fp_id+canonical_hash → executor promosso in catalog ∧ ∉piano; immune se il piano lo usa), C2 famiglia `{intent_verb}_{intent_object}[_*]` §2.2 non nel piano); C1 hit-time guard in `dispatch.run_turn` (delete + fall-through, self-healing); valvole: `POST /admin/praxis/fastpaths/{id}/delete` + feedback ✗ `turn_feedback.apply_feedback` → `fastpath.delete_by_query` (LWW: un fastpath colpito rinfresca last_used e vince in cascata → senza valvola sarebbe immortale).
- **Promozione fastpath L0 → executor synt** (mandato 11/6): job `fastpath_promotion` daily@03:50 ← `task_fastpath_promotion` → `engine/fastpath_promote.run_nightly`. GATING CONSERVATIVO cluster-based (shape `compute_framework_hash` + intent; SOLO multi-step; ≥3 distinti; usi≥15; età≥30g; no meta-tool; dedupe vs catalog-famiglia + generalize pendente; cap 3 nuove/notte; catalog incompleto→0 emissioni; env `METNOS_FP_PROMOTE_*`). Tier 1: proposta `proposals_state` kind=`fastpath_promote`; approve admin → marker `synt_pending/` (`fastpath_promote.on_proposal_approved` ← route legacy + `apply_decision_unified`) → `telos_synth_consumer`. Tier 2 AUTO: flag `METNOS_FASTPATH_AUTOPROMOTE` OFF default + floor `METNOS_FP_AUTOPROMOTE_*` (≥5/usi≥50/n_seen≥3 notti) + cap 1 tentativo/notte + mai scavalcare reject/block umano; synt completa via `handle_synth_request`. Provenienza `fastpath.record_promotion` solo candidati 'free'; composizioni = nome umano, no auto.
- **persons aggregator + ${RUNTIME:*}** (ADR 0163): `get_persons` (scheda) vs `read_persons` (profilo JOIN). `${RUNTIME:key}` in `praxis_executor.py`, whitelist `{actor,lang,channel}`; `compute_intent_sig` con scope marker.
- **Pipeline shape FSM** (ADR 0154): `runtime/pipeline_shape.py` invariante `E+ (F|A)?` + hook in `agent_runtime`.
- **Planner choice > runtime override** (ADR 0155): runtime non sovrascrive il planner (eccetto auto_remediation / vaglio / fast-path). Vietato interceptor pattern-match.
- **Fast path deterministico** (ADR 0094): `runtime/fast_path.py` short-circuit pre-PLANNER. Tabella chiusa `_FAST_PATTERNS`. ZERO LLM.
- **args_extractor regex** (ADR 0149, ridotto 11/6/2026): `runtime/args_extractor.py::regex_extract` deterministico §7.9 — strip degli args query-derived prima della registrazione canonical (single source vs memoization letterale).
- **PLANNER split GBNF** (ADR 0151): `runtime/planner_split.py::chat_with_tools_split` 2-call. Opt-in `METNOS_PLANNER_SPLIT=1`. 1.72× speedup.
- **Pattern intent-implicit** (ADR 0129): `vocab.detect_implicit_actions(query)` deterministico. Wire `intent_extractor → agent_runtime → orchestration._orchestrate_implicit_actions`.
- **Compound query decomposition** (4/6): intent LLM → `actions=[{verb,object}]` per CLAUSOLA (`intent_extractor.j2` it+en); `dispatch` rank pool per-PAIR (object reale per clausola); `proposer` salta verb-filter se `len(actions)>=2`. No dizionari sinonimi; `detect_canonical_verbs_all` = fallback lessicale.
- **Guard deterministici di struttura compound** (ADR 0174/0175): `dispatch._apply_deterministic_structure_guards` (su L0/L1/L3) = `_align_framework_objects` (ri-allinea tool-fratelli/oggetto-estraneo all'intent; v3 `_align_foreign_producers_v3`: un produttore con oggetto preso SOLO da una clausola CONSUMER — es. `read_files` per «salvali in un csv» — è un FANTASMA del proposer flaky → RIALLINEA se solo-produttore o DROP se ORFANO+oggetto-produttore già coperto, via `_step_is_consumed`+`_remap_step_refs`; ESCLUDE `entries` meta-oggetto pipe; guard `test_align_foreign_producers_v3.py`) → `_enforce_missing_clauses` (appende clausole RICHIESTE scoperte; produttori {find/read/get/list} INTERSCAMBIABILI per copertura, no produttore spurio) → `_ensure_extract_clause` (la clausola «estrai» è un TRANSFORM INTERMEDIO: INSERISCE `extract_entries` dopo l'ultimo produttore PRIMA del consumer mutante + rewiring `from_step`, non in coda; riempie `fields` deterministicamente quando la query li espone e altrimenti delega l'inferenza bounded al drop-in) → `_conform_to_intent_order`. `derive_tool_name` query-aware sul qualifier (`_FORMAT_HINTS` «foglio»→spreadsheet) + generico `<verb>_entries` anche per `extract`. Banco `tests/benchmarks/compound_extract_create_bench.py` (produttore→extract→create). Guard `test_ensure_extract_clause.py`.
- **extract_entries con schema esplicito o inferito** (bug live 22/6 + sites 13/7): `compound_decomposer.derive_extract_fields(query)` mantiene la precedenza deterministica quando la clausola espone i campi. Se `fields` e' omesso, `extract_entries` esegue una sola inferenza locale bounded (max 8 chiavi normalizzate e validate) e poi applica la normale estrazione tipizzata; un `fields` esplicito malformato fallisce chiuso. Il guard sites inserisce idempotentemente `read_sites -> extract_entries -> describe_entries`, senza costanti per sito, record o campo. Guard `test_ensure_extract_clause.py` + `test_sites_structured_extraction.py`.
- **Hedge anti producer-bias** (9/6): `proposer_metis._n_candidates` N=2 se intent.verb side-effecting (`_is_action_verb`, SoT `vocab.ACTIONS−SAFE_VERBS` — sostituisce gating-confidence morto B2); `_generate_grammar_multi` spende il budget SOLO come hedge pool-verbo se cand1 non azione-first E query con target literal (`_has_explicit_target` §4.2); telos-rank verb-match +0.2 decide + malus −0.3 step consecutivi duplicati. Prompt: `engine_proposer.j2` (it+en) pattern C azione-first su target nominato + regola FILLER-non-config-lookup.
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
- **Synth admission 4 layers** (ADR 0114): L2 affinity Jaccard ≥0.5 (`loader._check_affinity_overlap`); L3 ager (`executor_aging.py`, handcrafted mai demoted); L5 smoke fail-closed; L6 LLM verifier con schema tipizzato (`synt_stage6_verify.validate_stage6_verdict`). Linter, smoke e revisore non sono disattivabili tramite ambiente; test di regressione in `test_synth_stage6_verify.py` e `test_skill_admission_smoke_case.py`.
- **Safety net 7-layer skill imported** (ADR 0159): L1 sign + L2 Jaccard ≥0.5 (≥0.85 binding) + L3 ager + L4 sandbox (planned) + L5 smoke + L6 LLM verifier + audit JSONL sharded.
- **Skill importer 5-stage + R1+R2+R3** (ADR 0123+0159 wiring): CLI `metnos-skills`. Mapping `runtime/skill_vocab_map.json`. R1 `skill_description_llm` pre-codegen; R2 `importer_verb_verify.check_plan` gate; R3 zero-fallback su `vocab.*`.
- **Locale-aware skill bundle + rename → skills/** (ADR 0160): pattern bundle-per-locale `executors/skills/<locale>/`. Helper `runtime/skills_paths.py` dual-root scan. SKILL.md fields: `lang/trust/auto_enable/distribution/feature_modules`.
- **Skill registry**: `runtime/skill_registry.py` espone `list_skills/enable/disable`, gating via `is_skill_enabled()`.
- **Tassonomia skill 3-tier + confine skill↔backend** (ADR 0170): `tier ∈ {core, first_party, imported}` (`skills_catalog.skill_tier`). Backend=COME (config, `backend_resolver`), skill=SE/QUALI (attivazione/fiducia/packaging); ortogonali, dipendenza dichiarata UNA volta al backend, skill aggrega. Mono→multi provider = +backend +skill, 0 executor (`*_issues` resta canonico, provider→resolver). google-workspace = Tier 2 vendorizzata `executors/skills/google-workspace/`. Tier 3 = sandbox 7-layer (ADR 0159); pubblico spedisce solo Tier 1+2.
- **Sandbox per-skill foundation** (ADR 0140 ext): `Executor.sandbox_profile/provenance/is_imported`. Audit `runtime/skill_audit.py`. Watchdog `jobs/skill_sandbox_watchdog.py`.
- **Autorita' provider dichiarativa** (ADR 0193): `runtime/capabilities.py::effective_capabilities` risolve `when={arg,values}` fail-closed; `executor_standard._validate_authority` blocca binding provider mancanti/eccedenti/ignoti; `sandbox.invocation_skills` usa solo `provider:access` per executor conformi e mantiene i 5 segnali storici esclusivamente per i legacy. Guard: `test_capability_registry.py`, `test_executor_standard.py`, `test_sandbox_skill_backed.py`, `test_provider_axis_naming.py`.
- **Catalog invariants al load**: `runtime/loader.py` rifiuta synth con collision verso handcrafted. `_gc_collisions` sposta i rejected in tmp.
- **No synth ridondanti**: stage 1 NAMING preferisce canonical esistente se intent coperto.
- **Synth_request short-circuit** (ADR 0076): `handle_synth_request` skip pre-cascata su catalog match.

**Backend / executor / domini**
- **Backend tree per OBJECT** (ADR 0130): `runtime/backends/<OBJECT>/<provider>.py`. Retry 3× su transient.
- **Backend resolver uniforme** (ADR 0165): provider=config non intento; `backend_resolver.py` risolve `client/account/provider` deterministico (no enum all'LLM). 4ª eccezione §4.1/0155 (valori-config, non forma/flusso).
- **Ri-risoluzione slot query-specific su piani SERVITI** (12/6, bug live mailbox-24h): `engine/executor.resolve_query_canonical_args` = catena resolver PURI (query, config) — `mail_account_resolver` (account nominato VINCE sul literal ereditato; «tutta/all»→`all`) + `time_window_resolver` (NL IT+EN→`today|yesterday|last-Nh|last-Nd`; schema-gated su `args_schema.time_window`, solo verbi read/find/get/list, mai spurio, mai su `since`/`before`). Applicata (1) a ESECUZIONE in `Executor.run` per ogni step/layer — un piano champion L1 / L0 e' template di STRUTTURA, gli slot si ri-riempiono dalla query ATTUALE; (2) a RECORD in `dispatch._canonical_framework_for_record` ← `_maybe_record_fastpath` — lo store L0 riflette cio' che esegue (§2.8) e `query_specific` e' calcolato sul piano vero. Guard: account nominato e «senza tempo» in `test_served_plan_reresolution.py`.
- **Resolver completamento-args mail + robustezza read** (ADR 0176, 21-22/6): la famiglia resolver puri completa gli args che l'LLM OMETTE dalla query. **`from_contains_resolver`** (3° resolver, gemello di account/time_window): «da/from <NomeProprio>» o «(fattur*/pagament*/ordin*/ricevut*/bollett*/invoice/receipt/…) <NomeProprio>» CAPITALIZZATO → `from_contains`; solo read_messages, from/subject vuoti, no stopword/giorno/mese/account, candidato UNICO, minuscolo→noop (`test_from_contains_resolver.py`). Estensioni copertura: `time_window_resolver` «(dell')ultimo mese/anno» singolare→`last-1m`/`1y` (settimana NO=calendario); `mail_account_resolver` possessivo plurale «(le) mie email»→`account=all`. Backend `email_metnos`: `account="all"` **sort GLOBALE per data** (era per-account → recenti oltre i cap a valle §2.1); `read_messages.py` `default=_json_safe` (mai crashare su mail con campo bytes non-UTF8 §2.8/§7.3, re-sign). Notice §2.7 `truncated_what="input_sources"` legge i campi INPUT (`available_input_total`/`cap_value`), non l'output. Wire `engine/executor._apply_pure_resolvers`.
- **Clausola «ordina/raggruppa per X» onorata end-to-end** (12/6, bug live T38/T39 «ordinate per mailbox» ignorata): `runtime/ordering_clause.py` — `detect` (regex chiuse IT+EN, mode sort|group, key, desc; «per favore/cortesia» esclusi) + `resolve_field` (chiave-utente→campo reale: esatto > famiglie sinonimi chiuse > substring; condivisa da `sort_entries` §2.4 e `describe_entries`) + `apply_to_framework` (inietta `sort_entries(by=key)` prima del presenter terminale, rinumera `from_step`/`${stepN.*}`, `group_by=key` su describe; idempotente). Applicata in `dispatch._apply_ordering_clause` su TUTTI i layer (fastpath/autopath/engine/recovery): il piano cachato è template, la clausola si ri-deriva dalla query a ogni esecuzione (self-healing dei piani memoizzati sbagliati, niente invalidazione). `describe_entries._build_group_directive`: sezioni DETERMINISTICHE (campo, valori in ordine, conteggi) che VINCONO sul raggruppamento intrinseco per tema. Marker `_ordering_clause` → `is_query_specific` (0a-only, mai ereditato via cosine 0b). Confine §2.2: presentazione = `sort` (in-memory), MAI `order` (persistente). Guard: `test_ordering_clause.py` (27 test).
- **Plugin esterni** (ADR 0132 **DEPRECATED**): superseded da skill imported + `METNOS_HIDE_EXECUTORS`. `plugin_loader.py` rimosso.
- **Indici di dominio** (ADR 0086, image superseded by 0117): pattern `{create,find}_<dom>_indices`. Storage `~/.local/share/metnos/index/<dom>/<sha8>/<idx>/`.
- **Unified image enrichment index** (ADR 0117): single asse `unified/` per corpus. Schema v4 in `runtime/index_schema.py`. Pipeline EXIF+ArcFace+VLM+BGE-M3.
- **Intelligent path-aware indexing** (ADR 0166): `folder_path_context` (`create_images_indices.py`) → `path_context` fuso nell'embedding; parse temporale + escape coseno `find_images_indices.py`; re-embed `runtime/jobs/reembed_path_context.py`.
- **Taglio di rilevanza adattivo** (ADR 0169): `relevance_cut.py::adaptive_relevance_threshold` — taglio RELATIVO per-query `μ+3σ`+floor (coseno denso in banda stretta). Wire `find_images_indices`. Riusabile da ogni retrieval scored.
- **Spreadsheet LOCALE di default** (ADR 0169): `local.py::{create,write,append,read}_spreadsheet` (.xlsx/.csv); i 3 `*_files_spreadsheet` default `client="local"` (§10.3), Google opt-in; `spreadsheet_id`==PATH.
- **Guard refusal-in-args** (ADR 0169): `agent_runtime.validate_args` + `_LLM_REFUSAL_MARKERS` (IT+EN) — rifiuto LLM come VALORE di un arg = step malformato (§2.8). Deterministico §7.9.
- **Named persons registry** (ADR 0113): `~/.local/share/metnos/persons.sqlite` (slug case+accent-insensitive). 4 executor `*_persons` con ambiguity → dialog `kind="choice_with_preview"`.
- **GitHub provider first-party** (ADR 0141): 13 executor `*_github`. Watcher scheduler v2 + dedup `jobs/github_dedup.py`. Config `~/.config/metnos/github_watched_repos.json`.
- **consult_frontier system verb** (ADR 0142): `executors/consult_frontier/` modo A single-call + modo B agentic tool use. Tier config `~/.config/metnos/llm_tiers.toml`.
- **delete_files executor**: `executors/delete_files/` + `backends/files/local.py::delete_files` reversible.

**Crawler / web**
- **Pipeline web/news/scuola** (ADR 0081+0082+0098+0101+0105+0108): `{find_urls,read_urls_html,read_urls_pdf,login_urls}`. Tier config `~/.config/metnos/{owned_domains,trusted_origins}.json`.
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
- **Sites intelligenti drop-in** (ADR 0188): `session_broker.op_login` + `credential_injection.perform_login` implementano consenso/scroll/landing/ingresso/username/continue/password/2FA con budget e postcondizione strutturale stabile, immune sia ai remount SPA transitori sia al form ancora visibile nel primo frame post-submit; ogni fallimento conserva evidenza redatta e chiude salvo handoff continuabile. `act_sites(search)` mantiene un goal post-login e riosserva menu bounded sotto un gate batch. Per richieste strutturate il runtime puo' reclutarlo con un target tipizzato ottenuto da un reducer locale bounded, estrattivo e `think=false`, senza esporre sintassi al planner. Modello solo su ID enumerati e mai dopo fill; origine delegata via token one-shot ricontrollato; guard in `tests/runtime/sites/test_sites_security.py` e `tests/runtime/sites/test_sites_structured_extraction.py`.
- **Executor intelligenti a mandato ristretto** (ADR 0189): stesso contratto I/O e stessa autorita' di un executor normale; ciclo interno bounded, deterministic-first, postcondizione obbligatoria e handoff esplicito. Il catalogo per dominio e' generato da `scripts/generate_executor_catalog.py`; guard in `tests/runtime/executors/test_executor_catalog_docs.py`.

**UI / output / i18n**
- **Engine UI dichiarativo** (ADR 0090): `get_inputs(title, dialog=[...], fmt=...)`. Storage `runtime/dialog_pending.py` (path da `_C.PATH_USER_DATA`). Adapters Telegram + HTTP.
- **Trasferimento conversazione fra browser** (ADR 0201): owner, `conversation_id` e lease `device_token` sono identità distinte. Un conflict offre sempre annulla / attiva la conversazione corrente / continua quella precedente; takeover one-shot owner-bound con ricontrollo del vecchio writer in `BEGIN IMMEDIATE`. Lease e conflitti sono indipendenti per `(user_id, channel)`; anche puntatore, token, command buffer e cronologia nel browser sono sotto `user_scope` (`metnos_chat_history:v3:<user_scope>:<conversation_id>`), con import legacy riservato all'host. `/agent/turns/recent` è owner+conversation-bound; `/agent/turn/submit` rifiuta lease revocate o discordanti. Guard: `test_active_sessions.py`, `test_http_session_endpoints.py`, `test_chat_session_integration.py`, matrice Playwright in `test_chat_dialog_lifecycle_browser.py`.
- **Ogni scritta della chat è i18n** (ADR 0201): `chat.html`, `dialog_form.html` e `base_bare.html` usano lingua runtime e `msg()` per label, attributi accessibili, testo JS ed errori locali; niente dizionari per-lingua nel client. `test_chat_i18n_compliance.py` vieta prosa statica nei nodi/sink visibili; `test_seed_i18n_gate_keys.py` deriva le chiavi da entrambi i template e richiede IT+EN.
- **Output formatter deterministico** (ADR 0095): `runtime/output_format.py` channel-agnostic markdown. NIENTE LLM.
- **Channel-aware HTML** (ADR 0109+0110): `runtime/html_sanitizer.py::{to_safe_html, to_safe_html_full}`. Dispatch in `http_routes_agent::_safe_final_html`.
- **Prompt-as-data + multilingua** (ADR 0092): `runtime/prompts/<lang>/<role>.j2`. `prompt_loader.get/compose()`. CLI `metnos-prompts`. Sub-dir lingua secondaria deve avere stesso set di `it/` (boot check).
- **Token-data nei prompt non-Jinja** (§7.11-date): `date_tokens.py::substitute_date_tokens` (`{{current_year}}`/`{{current_date}}`) ai render `.yaml` (`prompt_loader._render_yaml_section`) + manifest (`engine/proposer._render_tool_pool`); manifest col token LETTERALE (no re-sign §7.10).
- **Prompt architecture A+B+C + linter** (§6.1): split `planner.j2` in `_core` + sezioni + `_footer`. Linter `runtime/prompts_lint.py`. Daemon `i18n_translate_pending.py`.
- **Proposer static-first prompt-cache (ottimizzazione A, 10/6/2026)**: `engine_proposer.j2` `layout: static_first` + marker `{# STATIC-END #}`; `prompt_loader.get_split` → testa statica=SYSTEM (byte-identica), coda per-query (intent/pool/excluded/query)=USER → llama-server riusa il prefisso dal checkpoint `n_before_user` (prompt_n 5521→1277, call 8.15s→2.26s; resiste a slot-theft via host prompt-cache — niente priming/id_slot). Guard `prompts_lint._check_l6_static_first` (anchor engine_proposer ogni lingua, no `{{var}}`/`{%` prima del marker, `{{ user_query }}` nella coda).
- **Report runtime user-facing i18n** (ADR 0104): chiavi `MSG_*` in `i18n.sqlite` IT+EN.
- **i18n pipeline strutturale** (ADR 0152): subset chiavi nel synt stage 5; daemon `_materialize_auto_synth_stubs` con `auto_translated` flag.
- **Thinking-leak scrubber** (ADR 0102): `_scrub_thinking_leak` in `agent_runtime.py` ramo `final_kind=="answer"`.
- **Describe_entries max_tokens adattivo**: scala con N entries (override esplicito via arg).
- **Describe testo DETERMINISTICO (12/6/2026)**: `llm_helpers._call_llm_proc` — processo `llama-completion` monouso (stesso GGUF via `/props`, template via `/apply-template` enable_thinking=false, temp=0 seed §11); il llama-server condiviso NON e' riproducibile (stato di processo, logits ±0.1, cross-backend). Opt-in `call_llm(deterministic=True)` da `describe_entries` (gate `METNOS_DESCRIBE_DETERMINISTIC`, default ON); fallback HTTP onesto `meta.deterministic=false`. Guard `tests/test_describe_determinism.py`. Companion fix: `max_query_chars` pass-through (bundle 24K non piu' troncato a 12K a meta' JSON).

**Telos / introspettiva**
- **Alignment Engine v1.3** (ADR 0157): `runtime/alignment_engine.py`. Formula `expected = (α·top + γ·rest)·urgency·confidence - bother_cost`. CLI `--backfill`/`--recompose`.
- **TELOS.md v1.2 (6 telos)** (ADR 0157): pesi 0.25/0.20/0.20/0.15/0.10/0.10. Anti-rinuncia come policy runtime, non pesata.
- **TELOS planner injection**: `telos_loader.render_planner_block(lang)` + slot in `prompts/{it,en}/planner/_footer.j2`. Hot-reload mtime cache.
- **Dashboard `/admin/proposals/telos`** (ADR 0157): triage proposte. Store `runtime/telos_proposals_store.py`. Decisioni JSONL append-only LWW. Cutoff `min_alignment=0.30`.
- **Telos engine 10 lenti laterali** (ADR 0156 v8): `runtime/telos_lenses/` modulare. Framework `_base.run_lens` + SHARED_PREAMBLE/NAMING_SCHEMA/OUTPUT_FORMAT §6. Env per-lens.
- **Store telos = unione candidati** (12/6/2026): `telos_proposals_store._iter_merged_rows` legge TUTTI i file candidati (recomposed > rescored > raw) dedup per ts — MAI tornare al solo-primo-file (536 proposte post-snapshot erano invisibili al loop).
- **Cluster = unità di decisione telos** (mandato 12/6/2026): `telos_proposals_store.recompose_clusters` (1 head per signature_relaxed, head=azionabile+EA max) + `cluster_score(ea_max, n_lenti)` bonus SOLO per lenti DISTINTE (+0.05, cap +0.20); hub `/admin/proposals` con `group_clusters` serve head (`proposals_unified._load_telos`); gate `proposal_actions.on_accept` cluster-aware (stessa formula); azione cluster `/cluster/{action}` su membri RELAXED, effetto operativo 1 solo (`apply_decision(run_on_accept=False)` sui non-head).
- **Dedup generativo telos (target, lens)** (12/6/2026): `telos_introspect._persist` skip se coppia già nello store (ripetizione intra-lente ≠ evidenza; sui dati reali 1017→75, −93%); ritorna bool, `run_all_telos` riporta `persisted_total` (§2.8).
- **Decisioni hub unified: source granulare normalizzato** (12/6/2026): `proposals_unified.apply_decision_unified` accetta `"telos:<lens>"`/`"introvertiva:<kind>"` → family (prima: 400 unknown source su OGNI bottone del hub).

**Scheduler / lifecycle / unified changes**
- **Scheduler v2 asyncio co-host** (ADR 0112): `runtime/scheduler_v2/` single Task. Trigger grammar `daily@HH:MM`/`every_N{s,m,h}`/`at:<ISO>`/`cron:<5-field>`. Callbacks via `builtin_callbacks.install_default_callbacks`.
- **Scheduler gate user-activity** (ADR 0074): task notturni age-based sospesi se user idle. Sorgente turns JSONL.
- **Scheduler circuit-breaker** (ADR 0168): N=3 fail consecutivi → auto-disable + notifica owner 3-opzioni; col `consecutive_failures`; `daemon._fire_entry::on_circuit_break` → `recurring_tasks._notify_circuit_break`.
- **Push schedulato onesto + notify-once issue** (12/6, bug live maintenance github su 0 issue aperte): (1) `agent_runtime.pipeline_effect_counts` (items/mutations/failures dai result reali) + `_detect_false_success` → notice in `TurnLog.write` se il final CLAIMA successo su pipeline vuota (§2.8); (2) `recurring_tasks._scheduled_push_is_noop` → run schedulato a vuoto (0 items o 0 mutazioni effettive, no errori/dialog) = 0 push, solo log in runs.output; (3) `write_issues` skip-known: issue gia' in `issue_qa` (repo+issue_number) senza avanzamento stato (new<prepared<approved<posted) = no-op `skipped_known` (ok_count=solo scritti, `overwrite=true` forza) → idempotente col trigger every_30m, notifica UNA volta. Guard `test_scheduled_push_honesty.py` + `test_issue_maintenance_flow.py`.
- **Guard issue trattate A MONTE del costo LLM** (12/6, costo frontier ricorrente su issue note): `runtime/treated_issues_guard.py` — nei SOLI turni schedulati (`scheduled_turn_scope()` settato da `recurring_tasks._run_user_query_callback` attorno a `run_turn`), `filter_treated_issue_entries` droppa dalle `entries` dei builtin LLM-costosi (classify/describe/extract_entries) i record-issue gia' trattati in `issue_qa` (status prepared/approved/posted; `new`=frontier giu' → ritrattare) PRIMA di qualsiasi chiamata LLM/frontier — il dedup `write_issues` resta backstop a valle. Identity: repo+issue_number o kind=github_issue+number+html_url. Call-site: `agent_runtime._invoke_builtin_handler` (engine v2/piani serviti) + 2 siti diretti planner loop. Deterministico §7.9, fail-open §2.8, annotazione onesta `skipped_known`+`note`. Guard `test_treated_issues_guard.py`.
- **Nightly maintenance orchestrator** (ADR 0167 ext): 15 task housekeeping = 1 entry `nightly_maintenance` (daily@03:00), sequenza GPU-safe `nightly_orchestrator.py::run_nightly` (error-isolation §2.8); ordine `NIGHTLY_SEQUENCE`; `install_default_jobs` auto-pulisce le standalone obsolete (idempotente).
- **Async indexing build** (ADR 0093): systemd transient unit. Atomic write + resume checkpoint.
- **Proposals cleanup** (ADR 0096): `runtime/proposals_cleanup.py` 4 op (move + UPDATE, NIENTE delete).
- **Lifecycle summary** (ADR 0097): `runtime/lifecycle_summary.py` aggregatore READ-ONLY ager.
- **Inactivity-decay esenta gli handcrafted** (bug delete_persons 13/6, ROOT): `executor_aging.apply_executor_ager` salta `not _is_synth(source)` (contatore `handcrafted_skipped`) — SOLO i synth invecchiano per inattivita'. Simmetrico con `apply_feedback_ager` (efficacy) e col docstring del modulo; prima la decay notturna deprecava handcrafted core a basso uso (`delete_persons` 30gg) → fuori dal catalog composer (`filter_for_visibility`) → pool di routing senza l'unico provider → misroute silenzioso a un fratello (`delete_persons`→`delete_credentials`, falso successo §2.8). Data-fix one-off: undeprecate degli handcrafted on-disk deprecati per inattivita'. Guard `test_introvertive_loop_stress.py::test_handcrafted_never_ages_by_inactivity`.
- **Routing bench = catalog composer** (§11 fidelity, 13/6): `tests/benchmarks/routing_subset_bench.py` usa `filter_for_visibility(load_catalog, VISIBILITY_COMPOSER)` (non più RAW): un executor routable deprecato spariva dal pool reale ma restava nel bench → guard verde mentre prod regrediva. Gold enrollment `delete_persons`/`get_persons` aggiunti.
- **Strato-3 anti-deadlock su routing-change** (bug delete_persons 13/6): `agent_runtime._strato3_routing_changed` — prima di escalare al dialog 5-azioni (gate `consec≥3`), ricostruisce la pipeline che il sistema proporrebbe ORA con le funzioni di produzione (`build_routing_pool` + `get_proposer().propose`, NESSUNA esecuzione, propose memoizzato → 0 costo extra nel turno reale) e confronta la firma col rejected-set: se è NUOVA (mai bocciata) NON escala → i ✗ stantii di un routing già corretto (fix intent/vocab/undeprecate) non bloccano più in eterno. Fail-safe §2.8 (in dubbio escala). Guard `test_strato3_routing_change_guard.py`.
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
- **Distribuzione public-subset** (ADR 0145 ext): `/opt/metnos` = baseline completo (`decisions/` TRACCIATO, `docs/` ignorato); repo pubblico = export deterministico `scripts/export-public.sh` (git ls-files − `tests/{e2e,runtime,benchmarks,stress,internal}`/decisions/docs/CLAUDE.md/binari; IP funzionali→localhost; manifest firmati+`.sig` preservati). Audit `scripts/scrub-scan.sh [--strict]`. ADR/docs NON pubblici.

**PLANNER difese specifiche**
- **PLANNER skip describe after health** (ADR 0111): 4 difese post `get_processes(include_health=true)`. Safety net `_prepend_health_block_if_any`.

**Smoke / E2E / test infra**
- **Quality gate E2E consecutivo** (ADR 0192): `tests/e2e/run.sh --quality-gate` + `quality_gate.py` richiedono 2 run consecutivi con zero errori/failure, 100% success, copertura eseguita >=95%, matrice stabile e almeno 280 test; soglie versionate in `quality_targets.json`.
- **Corpus outcome evidence-based** (ADR 0192): `tests/e2e/corpus/extract.py::_infer_success` non equipara piu' `final_kind=answer` al successo; servono risultati executor esplicitamente riusciti, e `Corpus.positives` esclude i mutanti.
- **E2E environment parity** (ADR 0192): `E2EServer` usa la `.venv` Metnos installata e `sandbox._build_bwrap_args` monta read-only il Python environment attivo; fixture realistiche preservano via symlink indici e workspace immagini senza copiarli.
- **Executor-manifest gate** (7/7/2026): `tests/runtime/skills/test_executor_manifests_gate.py` fa girare `run_all_tests.py` DENTRO pytest → la suite executor-manifest è parte del baseline tracciato, non più orfana (era referenziata solo in `export-public.sh` → 12 regressioni silenziose accumulate). ~30s.
- **Smoke battery** (`runtime/smoke.py`, ADR 0114 L5): OBBLIGATORIA pre `./deploy.sh`, post synth, daily, e tocchi a `prefilter.py`/`agent_runtime.py`/`synt_multistage.py`/`loader.py`.
- **E2E driver baseline**: `server._copy_db_with_wal` + `_seed_i18n_baseline` sempre + lint regex `^E*F?$`.
- **Judge prompt safety-aware**: `prompts/{it,en}/e2e_judge.j2` riconosce consensi (signature/mount/sudoer) come ok.
- **Env test-only**: `METNOS_HIDE_EXECUTORS` + `METNOS_LOADER_VERIFY` per E2E che forzano skill imported.
- **`tool_grammar.filter_pool_for_grammar` canonical-aware**: provider-suffixed NON rimosso se canonical equivalente assente (compat HIDE_EXECUTORS).

**Strato 3 escalation**
- **Escalation UI ≥3 ✗** (task #30): `agent_runtime._orchestrate_strato3_escalation` early-exit; dialog 4-choice → `strato3_choice_dispatch` (`orchestration.py`); strati 1+2 `_render_rejected_pipelines_block`.


**Filiera proposte + guardie 2/7/2026 (ADR 0180 + review Fable)**
- **Anti-doppia-esecuzione fall-through**: `pipeline_effects.committed_mutations` + guardia su hit L0/L1 in errore (`dispatch._leg_committed_mutations`) — side-effect già committati → errore onesto, mai re-plan.
- **Qspec outbound esteso**: `CONTENT_ARG_KEYS` += email/domain (share_*) + summary/attendees/location/description (create_events/calendars) — literal = 0a-only.
- **Aging autopath su segnale vero**: `autopath._touch_served` su lookup; demote ancora `ts_last_used`; finestra observations pota SOLO `verdict IS NULL`.
- **Reject umano mai potato**: `proposals_state.prune_old` esclude `last_action='reject'` (R1 e R2).
- **Killer `layer_overlap`** (`proposal_evaluator._check_layer_overlap`): default-bake a parità di chiavi → superseded-by-L0; multi-step sotto `METNOS_HIGHLY_REQUESTED_FREQ_60D` (default 30) → covered-by-L1, solo con evidenza.
- **Cap esplicito conteggi**: `args_extractor._extract_count` + `detection_lexicon count.cap_pattern` {it,en} — mai il primo intero della query in max_results.
- **Segregazione modalità immagini**: `routing_pool._gate_image_modality` — tool object=images fuori dal pool se nessuna clausola/testo nomina le immagini.
- **Alberi di sistema protetti**: `vaglio.guard_check` blocca executor MUTANTI con path-arg in `platform_policy.protected_paths()` (letture libere).
- **`compress_` mutante**: in `pipeline_effects.MUTATING_TOOL_PREFIXES` (archivio = side-effect); limite noto: `extract_files` non copribile per prefisso.
- **Accept-pipeline esegue**: `change_applier.apply_materialize_pipeline` = turno reale in `scheduled_turn_scope`; effect onesto, observer giudica.
- **Adapter telos cluster-head**: `change_intent_adapters.telos.iter_telos` proietta solo `ACTIONABLE_NAME_STATUS`, score=`cluster_score`.
- **Alignment v1.4 fit con segno**: `alignment_engine.compose` — penalità `α·Σ peso·|fit⁻|` senza gate.

**Dialog/UX scelte + Drive 5/7/2026 (mandato Fable, Fase 0)**
- **Scelte cliccabili anche mono-step (HTTP)**: `channels.inline_ui.all_choice_like` (kind cliccabili, senza cap-24 Telegram) nei due decider fmt — `orchestration.invoke_get_inputs_internal` + `get_inputs._decide_fmt`.
- **Form dict-choices**: `dialog_form.html` rende value/label dai dict ADR 0127 (≤6 → radio, oltre select); prima il submit non validava mai (repr Python).
- **Resume reader mostra i DATI**: `orchestration._shape_result_for_chat` — `entries` (§2.6 reader) → `_fmt_reader_entries` (tabella/lista, cap 20 + MSG_TOP_OF); `results` (mutante) resta «✓».
- **find Drive senza cartelle**: `gw.find` esclude mimeType folder (per le cartelle c'è find_dirs) + preferenza NOME ESATTO fra i fullText-fuzzy; trashed=false nel CLI `drive_search`.

**Engine guard-chain hardening 5/7/2026 (CP1·M0 ADR 0177)**
- **Contratto pipeline guard (T3)**: `dispatch.GUARD_PIPELINE` dichiarativa (14 guard, ordine+gate-v3) + `test_guard_pipeline_contract.py` blocca nomi/ordine.
- **Registro ArgTransform (T3 estensione, 7/7/2026)**: `executor.ARG_TRANSFORM_PIPELINE` — unifica la famiglia resolver deterministica pre-esecuzione (8 entry: 5 query-det + 3 exec-only) in un registro tipizzato gemello di `GUARD_PIPELINE`. Driver unico `apply_arg_transforms(scope=…)`; il campo `scope` rende STRUTTURALE il confine query-det (riapplicabile a esecuzione + record L0) vs exec-only (solo esecuzione) — prima era un commento violabile → avvelenamento L0. `test_argtransform_pipeline.py` blocca contratto+idempotenza. Irregolari (from_step/fillers/placeholders/scope_args/matrix) restano cablati. Spec: `internal/design/spec_argtransform_registry.md`.
- **Idempotenza guard su cache-hit (T4)**: stesso test, double-apply full-chain+per-guard su corpus incorporato; sweep live 33/33.
- **Arg install-root fantasma**: `args_resolver` non ricorda e non inietta un path dentro PATH_ROOT come scope-default (le due protezioni causali). La riparazione a valle `dispatch._overwrite_phantom_install_args` è RITIRATA il 6/8/2026: 0 spari in 22 giorni di journal reale — la condizione scritta nella guardia stessa — e nessun piano avvelenato in L0/L1.
- **Conformità in uscita del piano**: `dispatch.strip_unknown_args` (ULTIMA della pipeline) toglie da ogni step gli arg che il suo tool non dichiara. Chiude il cricchetto dell'esenzione `guard_owned`, che è per nome nudo e cresce a ogni guardia nuova.
- **Rinumerazione degli step**: `dispatch.insert_steps` è l'unica porta per inserire uno step (rimappa `from_step`, `${stepN}` e `final_message` insieme); un test statico vieta `steps.insert` diretto.
- **Spari delle guardie**: `engine/guard_stats.py` persiste spari e attraversamenti per guardia (acceso di default). Senza il dato nessuna guardia si può ritirare, quindi il loro numero può solo salire.
- **Guardie dormienti nel riepilogo notturno**: `guard_stats.dormant()` (silenzio + massa + tempo: 0 spari nella finestra, ≥`METNOS_GUARD_DORMANT_MIN_SEEN` attraversamenti, ≥`METNOS_GUARD_DORMANT_DAYS` giorni di osservazione) → sezione in `lifecycle_summary`. È una lista di CANDIDATE, mai un verdetto: zero spari può voler dire «non serve più» o «il piano arriva sano perché c'è», e il ritiro resta il protocollo in quattro passi. Serve a rendere il «meno uno» un evento ordinario invece che un progetto — in due mesi ne era stata ritirata una sola.
- **Il piping non è un piano incompleto**: `engine/validator._check_args` — un `requires_one_of` è soddisfatto anche quando lo step porta solo `from_step`/`entries` e il gruppo ha un arg-lista (`from_step_projection.carries_upstream_payload` + `projection_can_fill`). Prima §4.1 veniva bocciata su nove gruppi del catalogo (read_files, delete_files, get_urls…) e ogni turno fresco con un consumatore piped pagava un re-propose LLM (i turni serviti da L0/L1 non passano dal Validator: per questo il difetto era invisibile). Un gruppo di soli scalari resta violato: il contesto scalare ha il suo controllo.
- **Ri-proposta accettata solo se migliora**: `dispatch.run_turn` (ramo Validator) confronta gli errori del piano rifatto con quelli dell'originale e sostituisce solo se scendono. Il re-propose è cieco (esclude l'impronta, non spiega l'errore): senza il confronto un piano corretto veniva scambiato con uno peggiore (misurato: find_files → find_files_hash → «Nessun risultato»). Stesso criterio del re-propose dei verbi scoperti.
- **find degenere = list (§2.2)**: `dispatch._degenerate_find_to_list` (ultimo) — intento LIST + find_files(base_path) senza selettore → list_dirs (device-eligible).
- **Equivalenza files↔dirs**: `dispatch._fs_equivalent` in align pass-1, foreign-producers v3, enforce_missing_objects, chunk-matching di fill — il piano list_dirs non viene demolito da un intent (…, files).
- **Sibling deterministico**: `compound_decomposer.derive_tool_name` — ordine PRODUCER fisso (find,read,get,list) al posto del SET (§11); fallback suffissi ESCLUDE varianti provider senza marker.
- **Delete-mail coperta dal move**: `dispatch._dropped_required_verbs` — delete su messages soddisfatta da `move_messages` (§5), niente re-append su hit.
- **Sink senza path estratti**: `dispatch._fill_clause_args` non riempie path/base_path/paths su create/write (l'output path resta al default §10.3).
- **Install-root mai scope-default**: `args_resolver._is_install_root_path` filtra cattura E iniezione dei default appresi (avvelenamento auto-rinforzante).
- **Path Windows/UNC estratti**: `args_extractor._PATH_RE` — segmenti intermedi con spazi, finale senza (no over-capture); assoluto estraneo all'host MAI fuso col CWD (`path_alias.normalize_input_path`).
- **Simbionte path_alias↔list_dirs**: il bundle di list_dirs co-loca `path_alias.py` via symlink → ogni edit di runtime/path_alias.py OBBLIGA il re-sign di list_dirs (altrimenti scartato in silenzio → misroute al fratello).
- **create_spreadsheet suffisso**: output senza estensione nota → `.xlsx` (§2.4).
- **Testa manifest over-budget visibile**: `engine.proposer._render_tool_pool` WARN (1×/tool) se la testa §2.5 supera HEAD_MAX (i sintetizzati/importati sfuggono al test statico).
- **Verbo canonico NUDO nell'affinity = primato di famiglia (NON ripulire)**: `prefilter.affinity_score` (path bag-of-words, usato da `routing_pool` quando l'intent è debole) conta i tag-verbo a peso 4, a differenza di `rank_with_intent` e `affinity_phrase_score` che li escludono via `_GENERIC_AFFINITY_VERBS`. Sembra sporcizia — 49 manifest su 119 li dichiarano — ma è il segnale additivo su cui il qualifier costruisce. Misurato 6/8 su `scripts/bench_prefilter_corpus.py` (223 query reali): toglierli a TUTTI costa 17 top-1 (97→80, i fratelli provider scavalcano il default); toglierli ai soli 4 fratelli qualificati con default esistente è neutro sul corpus ma appiattisce `read_files_csv` su `read_files` per «leggi il csv» (24→20 contro 21). Prima di «normalizzare» un'affinity, rifare la misura.

**Cache-validity ADR 0182 (5/7/2026)**
- **Firme del mondo sui piani cachati**: `engine/cache_validity.py` — `tools_sig` (digest §7.10 dei tool referenziati) + `pool_sig` (famiglie candidati intent); stamp a `fastpath.record_success(catalog=…)` / `autopath.record_observation(catalog=…)`.
- **Verifica a lettura L0/L1**: `dispatch` valida al hit (`cache_validity.validate`, C1 esplicita per tool assenti) — mismatch → L0 morte+fall-through / L1 fall-through (refresh sig alla ri-promozione).
- **Epoch nella LRU proposer**: `MetisProposer._cache_key` include `catalog_epoch` — mai un framework di un mondo passato dal retry-path.
- **Mai load_catalog implicito nelle firme**: catalogo sempre esplicito dal chiamante (il fallback timbrava il DB aging `first_seen` — scovato dai test lifecycle).
- **Finalizer unico (T5)**: `engine/executor._finalize_answer_text` — sola fonte del testo `answer` (render→bullets→zero-i18n→synth); `test_finalizer_unico.py` vieta blocchi gemelli (1 sede `_render_is_degenerate`, 2 call-site).

**Remote mutanti + undo round-trip ADR 0183 (5/7/2026)**
- **Ricevute inverse generali per insiemi** (ADR 0216): le mutazioni di
  membership registrano prima/dopo e il delta effettivo; l'inverso applica
  soltanto quel delta senza sovrascrivere variazioni estranee. Gate:
  `tests/runtime/backends/test_exact_undo_receipts.py`.
- **Esito undo per singola esecuzione** (ADR 0217): un manifest firmato puo'
  dichiarare `undo.outcome=per_execution`; il risultato deve essere
  `reversible|no_effect|irreversible`. Il runtime non conosce nomi di executor
  e una ricevuta assente/malformata fallisce chiusa. Anche un turno chiuso
  senza ricevuta resta barriera e il secondo undo non raggiunge turni piu'
  vecchi. Gate: `test_undo_execution_outcome.py`.
- **Stato esatto e segreti fuori dal journal** (ADR 0217): snapshot generici
  prima/dopo si ripristinano solo con compare-and-swap; lo stato sensibile usa
  `protected_undo`, cifrato, legato ad attore/namespace e alla retention undo.
  `undo.jsonl` conserva soltanto handle opaco e digest. Gate:
  `test_set_signatures_undo.py`, `test_protected_undo.py` e
  `test_login_urls.py`.
- **Stop Windows per coorte verificata** (ADR 0217/0221): la ricevuta lega
  pacchetto, confine di attivazione e identita' PID+creation-time preesistenti;
  l'inverso segue i passaggi fra processi usando soltanto identita' del pacchetto
  o la sua radice registrata da Windows. Nessun arresto per nome, percorso libero
  o PID solo; senza attestazione `restored=true` l'undo fallisce. La persistenza
  resta irreversibile finche' la registrazione di startup non ha identita'
  equivalente. Verifica: prove Rust, compilazione incrociata Windows,
  `test_helper_wire_contract.py` e `test_run_processes.py`.
- **Scrittore undo al choke-point**: `agent_runtime._undo_pending/_undo_done` in `invoke_executor` (pending pre-exec, done post-ok, campo `device`) — la regressione af6c7b8 (writer nel planner cancellato) non può ripetersi: `test_undo_chokepoint.py` fa il round-trip reale.
- **Scrittore uso-executor al choke-point**: `executor_scheduler.ExecutorScheduler.invoke` → `executor_aging.record_invocation` — l'ALTRO scrittore che af6c7b8 aveva cancellato col planner, rimasto morto un mese (124 righe su 193 senza `last_used_at`, mentre aging e change_observer decidevano su quei numeri). Sta nello scheduler perché è l'unico punto attraversato da sottoprocesso, remoto, builtin e onda parallela: nell'anello del motore non passano né i turni serviti da L0 né i builtin. Discriminante `code_path` (gli slot interni del Tutor non hanno ciclo di vita), niente registrazione sotto pytest; `test_executor_usage_recording.py`.
- **Reverse sullo stesso host §2.9**: record con `device` → `undo_last_turn._reverse_on_device` accoda le chiamate-reverse AL device (`reverse_patterns.build_remote_reverse_calls`, deterministico); mai apply_patterns sul filesystem del server per op remote.
- **Target inverso soltanto dal contratto undo** (ADR 0215): `engine.executor._resolve_implicit_reverse_target` puo' colmare un target omesso esclusivamente da un precedente `_undo.reverse_pattern`, con executor esatto, chiamata unica, proiezione sullo schema e precedenza dell'esplicito. Gate: `test_engine_from_step_safety.py::test_implicit_reverse_target_*`.
- **delete_files senza device_ok**: restore_blob_backup non remotabile → placement lo tiene locale; il reverse dell'undo passa da enqueue diretto (asimmetria voluta).
- **Lazy-gw su TUTTI i dispatcher files/dirs**: find/read/write_files + create/delete/find_dirs — `test_device_shim_closure.py` importa i 9 nel layout device (repo bandito da sys.path); un import eager nuovo fallisce lì, non con un ModuleNotFoundError remoto.
- **Chiusura shim stdlib-only**: `test_agent_server_remote.test_shim_bundle_signed` valida gli import a module-load del local.py SPEDITO contro la whitelist della chiusura.
- **Provider remoto privilegiato senza comando** (ADR 0212): profilo manifest chiuso e dichiarativo, mandato server per invocazione, doppia firma client/server e host di interfaccia tipizzata senza rami per pacchetto. Assembly figlio diretto e tipo sono dati firmati; metodi, proprietà e limiti restano nel codice comune. Guard: `test_managed_dependencies.py`, `test_invocations.py`, `test_helper_wire_contract.py` e prove Rust in `helper-rs/src/{protocol,pairing,provider}.rs`.
- **Self-update firmato+idempotente (ADR 0184)**: `client selfupdate.rs` — descrittore firmato con chiave server (verify pubkey pinnata), no-loop per sha dell'exe, swap con ripristino su fallimento; e2e `c7-validate-selfupdate.sh` (swap+respawn-che-esegue+un-solo-swap).

**Lingua unica dell'istanza ADR 0219 (23/8/2026)**
- **Autorità firmata al riavvio**: `runtime.config::{INSTANCE_LANG,REQUESTED_LANG,LOCALIZATION_STATE}` deriva dal documento Ed25519 atomico; documento alterato o tag BCP-47 invalido ricade senza bloccare il boot. Gate: `test_instance_language_config.py`.
- **Nessun override per richiesta**: `i18n.language_context` può propagare soltanto `INSTANCE_LANG`; una preferenza utente, un canale o un payload non possono sostituirla. La rimozione dei chiamanti residui appartiene a RM-0005/F1.
**Learning-loop W1 ADR 0185 (5/7/2026)**
- **Seed shadow solo da ripetizione reale**: `autopath.seed_from_run` — soglie n_steps/n_obs, no-op se autopath active esiste; il ✓ umano conferma (shadow→0), mai degrado inverso. Test `test_learning_loop.py`.
- **Lacuna→proposta senza resurrezione**: `learning_loop.propose_from_lacuna` nel choke-point `_record_lacuna` — dedup fingerprint + stato REJECTED preservato dall'upsert (testato); classi d'uso (wrong_args) MAI proposte.
- **get_processes onesto §2.8**: snapshot grezzo vuoto = ERR_EXT_TOOL_FAILED con ragione (mai «ok 0») — è ciò che ha scovato SystemRoot mancante sul device.
- **Manutenzione=comandi NL (ADR 0186)**: domini esterni via `run_user_query` schedulato (mai job bespoke — github_watcher ritirato, riga scheduler morta rimossa); organi interni=builtin in `NIGHTLY_SEQUENCE`; aging esente alla fonte (executor_aging PROTECTED_NAMES+handcrafted).
**Mandati credenziale ADR 0190 (12/7/2026)**
- **Default in ogni modalita'**: scope cifrato `sites.read` sul binding applicato alle query interattive e schedulate; la query puo' restringere, l'ampliamento interattivo resta one-shot, revoca immediata.
- **Autorita' task per intersezione**: il task aggiunge un envelope subordinato con actor/query-hash/host esatti; nessun token browser persistito e nessuna autorita' creata dal task.
- **Task mai sospeso su dialogo sites**: host o azione fuori envelope -> `mandate_scope_exceeded`, fail-closed; configurazione soltanto in un run interattivo.
- **Topologia verificata**: il mandato usa solo `session_open`, `approved_*` e `credential_origin_approval` dell'audit; host meramente osservati esclusi.
- **Continuazione risultati bounded**: load-more/next contestuale, max 6, stop su contenuto invariato/ripetuto, aggregazione pagine senza duplicati.
**Stealth Sites ADR 0191 (15/7/2026)**
- **Superficie e tecniche ortogonali**: `sites_browser_mode=headless|side` e il registro `STEALTH_TECHNIQUES` attraversano replay/client/server e sono fissati nella sessione. Settings li raggruppa in Website browsing; `test_sites_stealth.py` verifica mode, binding e UI.
- **Variante esatta, nessun fallback**: `session_broker.op_open` passa `(browser_mode, launch_browser_required(effective_techniques))`; CONTEXT/BEHAVIOR non abilitano WebDriver e side senza display fallisce esplicitamente.
**Provenienza args — marcatura config + clamp backend (6/7/2026)**
- **Politica marcatura `runtime_resolved`**: 20 config-args marcati / 10 esenti intent-bearing; tabella fonte-unica `tests/runtime/infra/test_config_args_marking_policy.py` (6 test: nuovi config-args fuori tabella FALLISCONO; multi-provider files mai marcato; marcato mai required; `n_unmarked_config==0`), regole in `arg_provenance.is_intent_bearing_config`.
- **Clamp enum-aware `resolve_backend_arg`**: il DEFAULT per-object non scavalca l'enum del TOOL (share_files gw-only rompeva su ogni share senza marker drive); l'ESPLICITO non è clampato (errore onesto «client non applicabile» §2.8). Callsite unico engine/executor.py con `args_schema`; `test_backend_resolver_enum_clamp.py`.
- **Cap anti-runaway describe map-reduce**: `describe_entries._MR_MAX_ENTRIES` (env `METNOS_DESCRIBE_MR_MAX_ENTRIES`, default 100, 0=illimitato §2.4) limita il MAP alle prime N entries — senza tetto: 1759 chiamate LLM/~20min su «/tmp» (6/7). Nota utente NEL summary (MSG_DESCRIBE_TRUNCATED; il notice runtime salta i PROCESSOR) + campi §2.7. Test `test_describe_entries_cap.py::test_mapreduce_cap_*`.

**Tutor F3/F4 e identità delle fonti (ADR 0202-0203, 28/7/2026)**
- **Sonde chiuse dalla fonte**: `tutor.probes._REGISTRY` + `KnowledgeUnit.probe_refs`; audience prima dell'esecuzione, cache per utente/attore/ruolo/lingua, limiti e stati espliciti. Gate: `test_tutor_f3_f4.py::test_f3_probe_*`.
- **Consegna letterale monouso**: `tutor.handoff.create_pending` + `orchestration._process_tutor_handoff`; owner, conversazione, hash clausola/catalogo, nonce e TTL, reclamo atomico prima di `run_turn`. Gate: test `test_f3_*handoff*` e certificatore F3.
- **Apprendimento privato dopo il mode gate**: `tutor.gaps` + `tutor.associations`; niente query in chiaro, scope utente, TTL/cap, hash fonte+fingerprint embedder, feedback negativo rimuove, replay controfattuale. Gate: test F4 + `scripts/certify_tutor_f4.py`.
- **Nome documento non è placement file**: `published_docs.resolve_reference` attesta nome/percorso/URL della sola pubblicazione; `tutor.service` vincola il retrieval allo `source_ref`, mentre modifica/uso operativo resta `ACT`. Gate: test `published_docs`, Tutor source binding e caso reale in `scripts/certify_tutor_f3.py`.

**Virtualizzazione LLM: fast a tre livelli (ADR 0207, 5/8/2026)**
- **Registro workload chiuso**: `runtime/llm_workloads.py` associa ogni workload a `fast.micro|fast.procedural|fast.fidelity|wise|creative|frontier`; nomi ignoti falliscono. Gate: `test_llm_six_tier_contracts.py`.
- **Policy soltanto nel tier**: i consumer possono impostare tetto output, deadline, grammatica e tool schema, mai `temperature`/`think`/`reasoning_budget`; il guard AST copre runtime, executor e diagnostica distribuita. Gate: `test_llm_virtualization_boundaries.py`.
- **Default e UI onesti**: i tre livelli `fast` ereditano il binding centrale e hanno policy esplicite nel router; `creative` può riusare il solo binding fisico di `wise` mantenendo la sua policy. `frontier` assente non ripiega sul locale. Installer, pagina Modelli e metadati Synt condividono lo stesso vocabolario. Gate: `test_llm_six_tier_contracts.py` + `test_virt_configuration_view.py`.
- **I test di nascita girano davvero**: `tests/runtime/executors/test_birth_tests_all_green.py` esegue `runtime/test_runner.py` su ogni executor che DICHIARA lo standard. La firma applica gia' la conformita' strutturale, i test di nascita no: erano scritti, firmati e mai eseguiti (`get_location` rosso 1/4 da ignoto, scoperto 17/8/2026). 27 s per 84 executor. Gate: il test stesso.
- **Un'azione approvata torna dove appartiene**: `engine/dispatch._inject_gate_resume_if_paused` annota `target_device` sul callback del passo sospeso; `orchestration._process_gate_dispatch` e `_process_resume_executor_with_values` lo passano a `invoke_executor`. Senza, ogni ripresa girava sul server: conferma chiesta al PC, installazione tentata sul server con apt (turno a97056e1). Gate: `test_gate_resume_keeps_device.py`.
- **Polarita' sintattica condivisa e chiusa** (ADR 0215): `detection_lexicon.polarity_state_at` usa risorse native revisionate per negazione, inibizione, contrasto, coordinazione negativa, sequenza e invocazione. Lo stato `unavailable` nega i percorsi sensibili; la destinazione e' una sequenza ordinata e una revoca finale vieta sticky/default anche senza device registrati. Gate: `test_detection_lexicon.py`, `test_detection_translate_daemon.py`, `test_target_device.py` e `test_admin_catalog_guard.py`.
- **Ingresso tecnico LRE chiuso e riprendibile** (ADR 0215): `dispatch._explicit_start_lre_framework` ammette un solo profilo dal contratto registrato e soli path assoluti, passa da `get_approval`; `turn_id` e `source_request_id` attraversano i rami di resume. Gate: `test_engine_core.py::test_explicit_start_lre_*`.
- **Consenso dal cancello canonico, non dalla disambiguazione**: un executor che chiede approvazione usa `gate_dispatch` (esegue SOLO su approvazione), mai `resume_executor_with_values` (invoca sempre). Il token di consenso e' legato alla scheda letta. Gate: `test_install_packages.py::test_il_consenso_passa_dal_cancello_canonico`.
- **Direzione dell'operazione dalla richiesta**: `install_direction_resolver` allinea `uninstall` a cio' che la query dice, per qualunque schema che dichiari quel booleano; forme nel lessico i18n. Il modello sbagliava 12 volte su 12 con tre stesure diverse del manifest. Gate: `test_install_direction_resolver.py`.
- **Sul server solo l'amministratore**: `invocation_scope._server_authority_denial` applica ADR 0209 D4 alla capability `system:admin`, non a una lista di nomi. Il gemello (nessuno installa su un dispositivo altrui, admin compreso) e' strutturale nel filtro proprietario prima del placement. Gate: `test_install_authority_and_undo.py`.
- **Un'operazione irreversibile puo' dichiarare il proprio rimedio**: sezione `[undo]` del manifest firmato; `undo_last_turn` la propone come domanda e non la esegue mai da solo. Nessun dominio cablato nell'undo. Gate: `test_install_authority_and_undo.py`.
