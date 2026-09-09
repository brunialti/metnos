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
> 29 April reorganisation; not used).
>
> **This flat index stops at `0068`.** From `0069` onward the canonical ADR
> registry is the `decisions/` directory itself (one file per ADR), per
> `CLAUDE.md` §1 — not this list. Latest at time of writing: `0169`
> (taglio di rilevanza adattivo + spreadsheet locale default + guard
> refusal-in-args, sessione 2/6). Latest: `0191` (intelligent mandate-driven
> `sites` server robot: single general headless channel, one uniform obstruction
> handler, adaptive persistence within the owner's mandate, vocab-sourced
> goal-verb recognition; honest failure only on a real fingerprint wall or to
> protect the account). Prev `0190` (the encrypted credential mandate is the
> default for interactive and scheduled queries). ADRs
> `0115`, `0116`, `0121` are also skipped. Latest: `0193` establishes
> `EXECUTOR_STANDARD.md` as the versioned normative contract for every new or
> refactored executor; legacy executors migrate incrementally. Latest: `0194`
> clarifies that natural language precedes the typed executor schema and
> requires paraphrase coverage without constraining an intelligent executor to
> one path. Latest: `0195` separates product membership, origin, and transport;
> the 16 GitHub executors are builtin, handcrafted, and not imported. Latest:
> `0196` introduces one central, fail-closed executor execution policy: serial
> by default, hardware-bounded concurrency only by explicit class and
> equivalence evidence, with one binding contract shared by all generator paths.
> Latest: `0197` replaces Tutor phrase routing with a signed local BGE-M3
> index and uses the local LLM only to compose from audience-filtered retrieved
> context; administrative and safety procedures remain deterministic. Latest:
> `0198` replaces the card-bounded Tutor with one signed dynamic knowledge
> compiler over admitted manifests, allowlisted public documentation and
> curated procedures; language fallback is per concept, and F1 cards become a
> removable compatibility layer after equivalence certification. Latest:
> `0200` proposes the finalized RM-0001 design: personal knowledge remains
> outside planner/shared caches, typed values are injected after planning,
> low-risk owner learning becomes automatic after certification, and deletion
> invalidates queued compilation; executor experience and Leiden are excluded.
> Latest: `0201` separates the authenticated owner, logical conversation and
> browser writer lease. A session conflict now offers cancel, activate the
> current conversation, or atomically continue the previous device's
> conversation; the complete chat surface, embedded dialogs included, is
> guarded as catalog-driven i18n. Latest: `0202` adds closed read-only Tutor
> observations, a literal one-shot action handoff, and private per-user
> query-to-source learning after the mode gate. Latest: `0203` resolves an
> exact published filename, relative path, or canonical URL before routing and
> binds informational retrieval to that document without stealing mutations
> or unknown files from the ordinary engine.
> Latest: `0204` introduces the centrally budgeted deterministic parallel
> visitor used by every recursive filesystem executor, while the web crawler
> retains its rate-aware parallel BFS under the same budget. Latest: `0205`
> adds complete exact duplicate-file search with size/sample/SHA-256 stages,
> adaptive I/O and an opaque per-user cache; display limits no longer limit the
> compared source. Latest: `0206` makes Tutor live observations fail closed:
> a proposed view needs an independent full-coverage verification and a
> contrastive semantic primary before it may displace an ordinary read-only
> executor. Latest: `0207` replaces mixed per-call LLM tuning with five tiers
> (`fast`, `middle`, `wise`, `creative`, `frontier`) and three centrally
> configured `fast` levels (`micro`, `procedural`, `fidelity`); provider,
> model and decoding policy now belong only to the configured tier.
> Latest: `0213` freezes the internal durable-workload contracts and completes
> dormant F0-F2 storage foundations. It adds no public noun, route, worker or
> executor; claim, lease, fencing and activation remain later gates. Latest:
> `0214` proposes automatic, profile-free LRE admission from finalized long
> executor plans, with typed literal arguments, frozen placement and no inline
> fallback after a long action is recognized. Latest: `0215` establishes the
> external 24-flow bilingual logical certification and requires causal,
> domain-neutral repairs for syntax polarity, inverse targets and technical LRE
> resume identity; RM-0006 passed 96/96 cases plus five safe real probes.
> Latest: `0216` adopts exact, general receipts for effective membership
> deltas; container, mixed and sensitive undo branches remain specified but
> unimplemented pending explicit approval.
> Latest: `0217` adds a signed per-execution undo outcome, exact compare-and-
> swap receipts, encrypted actor-bound secret backups and kernel-bound Windows
> process stop; persistent startup and local directory deletion remain outside
> undo until they have equally strong storage identities.
> Latest: `0218` separates canonical `run` from web-session `open`, renames the
> launcher to `run_processes`, resolves localized program names only through
> authoritative unique package identities, and keeps routing/composition
> manifest- and capability-driven.
> Latest: `0220` admits every localization surface through one versioned
> registry, structural and semantic gates, public-only device/Tutor
> reconciliation and an explicit atomic instance activation.
> Latest: `0221` adds typed current-user Windows packaged-app activation from
> authoritative package/AUMID identities, with an exact post-activation process
> cohort and no command or application table. Latest: `0222` adds a
> capability-gated, signed `reverse` entrypoint for the same remote executor
> bundle, requires a semantic restoration attestation and preserves dialog turn
> identity through completion callbacks. Latest: `0223` proposes immutable
> generations for localized executor contracts, with one atomic current
> pointer, verified manifest bytes, pure signing and portable writer locking;
> technical code packaging remains outside its KISS boundary. It complements
> ADR 0220 without reopening RM-0005.
>
> `0224` establishes the single Executor Birth admission boundary. `0225`
> separates installed-service startup and authenticated maintenance from full
> certification, retaining signed code, identity and service confinement checks.
