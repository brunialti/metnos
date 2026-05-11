# ADR Index

Persistent log of architectural decisions for Metnos. See `README.md`
for format and discipline; see `_template.md` for the template.

## Decisions

- [0001](0001-undo-architecture.md) — Undo as a generic runtime hook with executor-supplied `reverse()`.
- [0002](0002-naming-convention-stringente.md) — Strict naming convention for executors (verb-noun, closed vocabulary).
- [0003](0003-lifecycle-executor.md) — Executor lifecycle, manifest schema, and birth tests.
- [0004](0004-repertorio-messaggi.md) — Centralized message repository (`runtime/messages.py`).
- [0005](0005-get-files-metadata-unico.md) — Single `get_files_metadata` with `fields[]` instead of N specialized executors.
- [0006](0006-rebrand-mykleos-to-metnos.md) — Public rebrand from Mykleos to Metnos; internal `myclaw` retained.
- [0007](0007-topologia-self-hosted-tre-assi-sicurezza.md) — Self-hosted topology on `.33`; three distinct safety axes.
- [0008](0008-microarchitettura-untrusted-triade-v11.md) — Prior microdesigns marked UNTRUSTED; v1.1 triad becomes canonical.
- [0009](0009-executor-mnest-mnestoma-naming-ontology.md) — Rename of the executor / mnest / mnestoma ontology (ex neuron / synapse).
- [0010](0010-synthesis-cascade-non-renunciation-telos.md) — Synthesis cascade with non-renunciation telos (compose before generate).
- [0011](0011-client-server-architecture-remote-executors.md) — Asymmetric client/server architecture for remote executors.
- [0012](0012-dialog-manager-three-line-card-authorization.md) — Dialog manager — three-line card pattern for extended authorization.
- [0013](0013-builtin-executors-third-category.md) — Builtin executors as a third category, runtime-shipped and non-synthesizable.
- [0014](0014-executor-granularity-heuristics.md) — Executor granularity — verb-noun, three uses, five-to-seven hops.
- [0015](0015-seed-executors-pool-composition.md) — Seed executor pool — 27 executors with OSS self-hosted backends.
- [0016](0016-open-source-self-hosted-default.md) — Open-source self-hosted as the default for recurring capabilities.
- [0017](0017-simplicity-as-binding-constraint.md) — Simplicity as a binding project constraint, not aesthetic preference.
- [0018](0018-general-fixes-over-symptomatic-patches.md) — General fixes over symptomatic patches; daily test as discovery.
- [0019](0019-feasibility-frame-single-person-plus-ai.md) — Feasibility frame — one author plus increasingly capable AI assistants.
- [0020](0020-manifest-authoring-delegated-to-claude.md) — Manifests and derived artifacts authored by Claude, reviewed by Roberto.
- [0021](0021-poc-as-architectural-cycle.md) — POC as a cycle on architecture, not as a build sprint.
- [0022](0022-poc-validates-microdesign-retroactively.md) — A validated POC retroactively approves the corresponding microdesign.
- [0023](0023-iter-test-cluster-development-protocol.md) — Development protocol — iterate, update DB tests, validate cluster.
- [0024](0024-test-failure-means-fix-code.md) — When a test fails, fix the code; do not modify the test.
- [0025](0025-three-tier-llm-architecture.md) — Three-tier LLM architecture (fast / middle / wise).
- [0026](0026-wise-tier-quality-floor-no-degradation.md) — Wise-tier quality floor — no silent degradation to fast.
- [0027](0027-provider-specific-prompt-repertoire.md) — Provider-specific prompt repertoire (short, prescriptive hints).
- [0028](0028-synt-generate-ux-self-improvement-message.md) — Synt generate UX — explicit self-improvement message on first run.
- [0029](0029-sqlite-test-framework-cluster-scope.md) — SQLite-backed test framework with module/cluster/system scope.
- [0030](0030-executor-diary-as-capability-registry.md) — Executor diary as the long-term capability registry.
- [0031](0031-no-backward-compatibility-pre-1.0.md) — No backward compatibility before 1.0; break freely when redesign is better.
- [0032](0032-docs-always-aligned-with-code.md) — Architettura and microdesign always aligned with code, no doc backlog.
- [0033](0033-sitemap-update-after-each-deploy.md) — Update the sitemap after each deploy that adds or removes pages.
- [0034](0034-three-level-routing-policy-executor-placement.md) — Three-level routing policy for executor placement (server vs device).
- [0035](0035-host-guest-model.md) — Host + guest model — Metnos as personal assistant with trusted invitees.
- [0036](0036-no-third-party-real-names-in-docs.md) — No third-party real names in docs, code, or examples.
- [0037](0037-self-contained-client-binary.md) — Self-contained client binary; no toolchain required on the target.
- [0038](0038-no-greek-letters-in-options.md) — No greek letters in option labels.
- [0039](0039-robust-executors-nl-deterministic-boundary.md) — Robust executors at the NL/deterministic boundary.
- [0040](0040-manifests-readable-by-medium-tier-llms.md) — Manifests written for medium-tier LLM readability.
- [0041](0041-executors-vectorial-by-default.md) — Executors vectorial by construction; single item is the degenerate case.
- [0042](0042-native-tool-use-as-planner-default.md) — Native tool-use as the planner default.
- [0043](0043-data-piping-stepn-field-syntax.md) — Data piping between ReAct steps via `{{stepN.field}}` syntax.
- [0044](0044-default-local-llm-qwen3-8b.md) — Default local LLM — `qwen3:8b` with `think=false`, `num_predict=400`.
- [0045](0045-executor-naming-convention-closed-vocabulary.md) — Executor naming convention — `verb_object[_qualifier]` with closed vocabulary.
- [0046](0046-rust-client-architecture-with-python-bootstrap.md) — Rust client + python-build-standalone + uv as the remote-executor architecture.
- [0047](0047-autonomous-deploy-after-doc-batches.md) — Autonomous deploy after each batch of doc modifications.
- [0048](0048-bilingual-canonical-corpus-it-en.md) — Canonical corpus in IT and EN, with separate roots and hreflang.
- [0049](0049-synt-multidimensional-analysis.md) — Multidimensional analysis of the synt synthesizer (post-28/4 reality check).
- [0050](0050-synt-stress-test-50-queries.md) — Synt stress test on 50 queries — convergence rate, LLM tier minimum, gap inventory.
- [0051](0051-synt-multistage-5-stages.md) — Multistage synth pipeline (5 stages, procedural → creative).
- [0052](0052-synt-multistage-validation-35-queries.md) — Multistage synth validation on the 35 non-proto-mnest stress queries.
- [0053](0053-synt-stage1-bilingual-mapping.md) — Stage 1 MAPPING bilingual amplification — closing the IT/EN vocabulary gap.
- [0054](0054-telos-cabled-via-request-new-executor.md) — Telos of non-renunciation cabled — `request_new_executor` builtin with auto sign+install and catalog reload.
- [0056](0056-tool-routing-budget-and-thinking.md) — Tool routing budget and reasoning budget — `k_max=8`, `think=True`, `num_predict=512` for the planner.
- [0057](0057-synt-stage5-modular-verb-prompts.md) — Stage 5 synt — modular per-verb prompt architecture.
- [0058](0058-intent-extractor-llm-based.md) — Intent extractor LLM-based for the prefilter (action → concept → canonical verb).
- [0059](0059-verb-prompt-philosophy-universal-pseudocode.md) — Verb-prompt philosophy — definition + invariants + domain (no per-verb pseudocode).
- [0060](0060-reverse-patterns-restore-blob-backup.md) — Reverse patterns — schema normalization and `restore_blob_backup` implemented.
- [0061](0061-test-runner-vector-matchers.md) — Test runner matchers — `field_eq`, `entries_field_eq`, metadata fallback chain for vector schema.
- [0062](0062-sort-entries-truncated-intentional.md) — `sort_entries` — user-requested `top=K` is objective truncation with `truncated_intentional` flag.
- [0063](0063-prefilter-universal-helper-verbs-exception.md) — Prefilter — universal-helper verbs are exception to the return-None on empty primary rule.
- [0064](0064-literal-path-arg-for-entries-consumers.md) — Literal `paths` argument for path-only entries consumers (atomic case).
- [0065](0065-undo-as-atomic-tool-in-rule-2ter.md) — `undo_last_turn` classified atomic in PLANNER rule §2-ter (no precursor injection).
- [0066](0066-synth-executors-in-user-data-dir.md) — Synthesized executors in user data dir (`~/.local/share/metnos/executors/`), separate from handcrafted pool.
- [0067](0067-introvertiva-mvp-events-turn-id.md) — Introvertiva MVP — `events.turn_id` refactor + `candidates_generalize` / `specialize` / `dedupe`.
- [0068](0068-recurring-tasks-callback-registry.md) — Recurring tasks — callback registry by string key (restart-safe scheduler).

> Note. ADR `0055` is intentionally skipped (placeholder reserved during the
> 29 April reorganisation; not used). The next ADR is `0069`.
