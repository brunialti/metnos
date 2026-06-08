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

