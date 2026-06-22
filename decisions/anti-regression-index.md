# Metnos — Indice meccanismi anti-regressione

> Estratto da CLAUDE.md §10.6 (2026-06-05) per mantenere CLAUDE.md snello.
> Una riga per meccanismo, solo call-site essenziale (file/funzione/policy).
> Dettagli, env vars, bench numbers, date → ADR (`decisions/`).
> Aggiornare QUI (non in CLAUDE.md) quando si aggiunge/rimuove un meccanismo.

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
- **Guard deterministici di struttura compound** (ADR 0174/0175): `dispatch._apply_deterministic_structure_guards` (su L0/L1/L3) = `_align_framework_objects` (ri-allinea tool-fratelli/oggetto-estraneo all'intent; v3 `_align_foreign_producers_v3`: un produttore con oggetto preso SOLO da una clausola CONSUMER — es. `read_files` per «salvali in un csv» — è un FANTASMA del proposer flaky → RIALLINEA se solo-produttore o DROP se ORFANO+oggetto-produttore già coperto, via `_step_is_consumed`+`_remap_step_refs`; ESCLUDE `entries` meta-oggetto pipe; guard `test_align_foreign_producers_v3.py`) → `_enforce_missing_clauses` (appende clausole RICHIESTE scoperte; produttori {find/read/get/list} INTERSCAMBIABILI per copertura, no produttore spurio) → `_ensure_extract_clause` (la clausola «estrai» è un TRANSFORM INTERMEDIO: INSERISCE `extract_entries` dopo l'ultimo produttore PRIMA del consumer mutante + rewiring `from_step`, non in coda) → `_conform_to_intent_order`. `derive_tool_name` query-aware sul qualifier (`_FORMAT_HINTS` «foglio»→spreadsheet) + generico `<verb>_entries` anche per `extract`. Banco `bench/compound_extract_create_bench.py` (produttore→extract→create). Guard `test_ensure_extract_clause.py`.
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
- **Nightly maintenance orchestrator** (ADR 0167 ext): 14 task housekeeping = 1 entry `nightly_maintenance` (daily@03:00), sequenza GPU-safe `nightly_orchestrator.py::run_nightly` (error-isolation §2.8); ordine `NIGHTLY_SEQUENCE`; `install_default_jobs` auto-pulisce le standalone obsolete (idempotente).
- **Async indexing build** (ADR 0093): systemd transient unit. Atomic write + resume checkpoint.
- **Proposals cleanup** (ADR 0096): `runtime/proposals_cleanup.py` 4 op (move + UPDATE, NIENTE delete).
- **Lifecycle summary** (ADR 0097): `runtime/lifecycle_summary.py` aggregatore READ-ONLY ager.
- **Inactivity-decay esenta gli handcrafted** (bug delete_persons 13/6, ROOT): `executor_aging.apply_executor_ager` salta `not _is_synth(source)` (contatore `handcrafted_skipped`) — SOLO i synth invecchiano per inattivita'. Simmetrico con `apply_feedback_ager` (efficacy) e col docstring del modulo; prima la decay notturna deprecava handcrafted core a basso uso (`delete_persons` 30gg) → fuori dal catalog composer (`filter_for_visibility`) → pool di routing senza l'unico provider → misroute silenzioso a un fratello (`delete_persons`→`delete_credentials`, falso successo §2.8). Data-fix one-off: undeprecate degli handcrafted on-disk deprecati per inattivita'. Guard `test_introvertive_loop_stress.py::test_handcrafted_never_ages_by_inactivity`.
- **Routing bench = catalog composer** (§11 fidelity, 13/6): `bench/routing_subset_bench.py` usa `filter_for_visibility(load_catalog, VISIBILITY_COMPOSER)` (non più RAW): un executor routable deprecato spariva dal pool reale ma restava nel bench → guard verde mentre prod regrediva. Gold enrollment `delete_persons`/`get_persons` aggiunti.
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

