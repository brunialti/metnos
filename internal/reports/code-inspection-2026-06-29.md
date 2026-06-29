# Code inspection — whole codebase (2026-06-29)

6-agent parallel sweep (planning core · runtime loop/HTTP/channels · executors+backends ·
install · support modules · cross-cutting dead/dup). All claims grep-verified at HEAD.
Dynamic dispatch (manifest-loaded executors, registries, env gates, ROUTES lists, Jinja)
accounted for. A prior audit exists: `internal/reports/multidim_findings_21_6.json`
(37 dead + 34 dup, report-only) — several items still live; re-verify before acting.

Confidence: H=grep-verified concrete · M=likely, needs a look · L=context-dependent.

---

## 🔴 LOCAL ERRORS (real bugs) — fix candidates first

1. **`runtime/engine/executor.py:705` `_resolve_fillers`** (H) — resolves only the FIRST
   `${FILLER:*}` then `re.sub`s that value over ALL fillers → a template with two distinct
   fillers (e.g. a `send` body) silently gets the wrong content. Fix: `sub(callable)`
   resolving each `m.group(1)`.
2. **`runtime/synt.py:1246`** (H, latent) — `RewardBreakdown(1.0,0.0,0.0,0.0,1.0)` passes 5
   args to an 8-required frozen dataclass → guaranteed `TypeError` the moment
   `specialize()` returns success (no live caller yet). Also `0.0` into `judge_reasoning:str`.
3. **`runtime/engine/fastpath.py:302` `_touch` broad `except: pass`** (H, correctness) —
   it is the only writer of `last_used`/`n_uses`; a persistent DB-lock failure leaves
   `last_used` NULL → `prune`'s never-reused rule then deletes ACTIVELY-hit fastpaths.
   Log instead of swallow.
4. **`runtime/http_routes_admin.py:1275` `admin_timer_action`** (H, §2.8) — "run now" does
   `res = info.fn(pl)` with no `is_async` check (its twin `admin_job_fire:613` checks);
   `nightly_maintenance` is async → returns an un-awaited coroutine, job never runs, flash
   says "eseguito → <coroutine…>". Mirror the 613 branch.
5. **`runtime/orchestration.py:444` (+542)** (H, §2.8/i18n) — returns
   `res.get("summary") or json.dumps(res)[:600]` to the chat channel, contradicting the
   module's own no-raw-leak backstop (702-708): user sees a raw `{...}` dict. Route through
   the clean shaper.
6. **Executors §2.4 cap-0 + `int()` crash** (H) — `find_places.py:44`, `read_files_csv.py:96`,
   `read_files_xlsx.py:101`, `filter_texts_lines.py:50` REJECT the documented `0`=unlimited
   placeholder (`if max<=0: ERR_ARG_RANGE`), and use bare `int(args.get(...))` (uncaught) →
   a null/non-numeric arg crashes the subprocess with empty stdout (§2.8 silent failure).
7. **`runtime/engine/autopath.py:321,376`** (H) — `record_observation`/`record_feedback`
   `_conn()` with no `try/finally`; `c.close()` skipped on sqlite error (trailing bare
   except only logs) → leaked conn + uncommitted write txn.
8. **`runtime/http_routes_admin.py:159,718` `SafetyStore`** (M) — conn opened in `__init__`,
   `close()` only on happy path inside `try`; an exception in `all_signatures()` leaks the
   handle (both `_summary_safety` + `admin_safety`).
9. **`runtime/channels/daemon.py:873` `_handle_dialog_callback`** (H) — re-inlines
   `_format_dialog_completion` (181) but with `new_state["values_collected"].get(...)`
   (direct subscript → latent `KeyError`) vs the helper's safe `(… or {})`. Call the helper.
10. **`executors/find_urls/find_urls.py:1937`** (M) — `truncated = len(entries) >= max_pages`
    should be `>`; exact-fill announces truncation with `available_total == used` (§2.11).
11. **`runtime/http_routes_agent.py:1555` `photo_web_proxy`** (M, security) — SSRF TOCTOU:
    `_validate_fetch_url` resolves+vets public IPs, then `opener.open(req)` re-resolves →
    DNS-rebinding can flip to a private/metadata IP. Pin the validated IP.
12. **`runtime/backends/messages/email_metnos.py:808`** (H bug/L impact) — dict literal keys
    `"error_code"` twice (typo; one was likely a different field).
13. **`runtime/engine/fastpath.py:186` 0a→0b fallthrough** (M, §2.8) — if the exact-hash row
    fails `Framework.from_dict`, it falls into the 0b cosine scan → can serve a *neighbor's*
    plan as this query's hit. Return None (clean miss) on 0a parse failure.
14. **i18n §11 — hardcoded user-facing strings** (M-H, widespread) — `backends/files/local.py:904`
    (move errors, core path), `recurring_tasks.py:872/950/1100/1112`, `agent_runtime.py:4393`,
    `http_routes_agent.py:526`, `daemon.py:1154/1614`, ~20 executor `*.error` literals, the
    executor `truncated_what` literals (mixed IT/EN). Route through `messages.py`/`_msg`.
15. **Stale model identity outside SoT** (M) — `llm_router.py:330` tells user to set
    `claude-opus-4-7` but `DEFAULT_TIERS` is `4-8`; `llm_provider.py:1080` `claude-haiku-4-5`
    in dead `make_provider_from_config`. Reference `DEFAULT_TIERS`.
16. **Doc-placement defects** (M, cosmetic) — `loader.py:554`, `prefilter.py:815`: a statement
    sits above the triple-quoted string → `__doc__ is None`. `dispatch.py:2211` comment says
    `METNOS_ERROR_FORM default ON` but it's OFF (safety gate). `naming_grammar.py:12` advertises
    nonexistent `suggest_canonical`.
17. **§8.6 restart-during-turn** (H — THIS SESSION's code) — `sidecar.py:329` (`_write_vlm_dropin`)
    & `:519` (`_photon_dropin`) unconditionally `systemctl --user restart metnos-http` when active;
    a post-install `python -m install.sidecar` can kill an in-flight turn. Gate / advise instead.
18. **`install/playwright_sidecar.py:252`** (H, §2.8) — `_install_unit` ignores `daemon-reload`/
    `enable` return codes and reports "installato e avviato (:8771)" with NO health probe (unlike
    searxng/photon). Add a `_wait_http` probe; honest status on failure.

---

## 🟡 DEAD CODE

- **`runtime/agent_runtime.py:~6066–9449` legacy PLANNER (~3.3K LOC)** (H, deadline) — gated
  `METNOS_PLANNER_LEGACY=0`; cron probe monitors for `total==0`. **30/6 (tomorrow) verdict →
  if 0, remove block + `legacy_planner_probe.py` + callsite.**
- **`runtime/proposals_unified.py` (whole module)** (H) — `/admin/proposals*` removed 13/6;
  0 runtime callers; only 2 tests import it. Delete module + those 2 tests + 2 stale
  anti-regression-index lines.
- **`runtime/channels/approval.py` (whole module)** (H) — `ApprovalRequest`/`render_approval_card`
  0 refs; real UI is `inline_ui.build_approval_keyboard` + `approval_registry`. Self-admitted
  v1.1 stub. Delete.
- **`runtime/nlu.py` + `runtime/poc/` (8 scripts)** (H product-dead, L should-remove) — inert,
  only poc/tests use it; excluded from public export. Keep as archive or move/delete.
- **`runtime/skill_wrapper_github.py`** (M) — 0 importers in repo or standard user-data skill
  path; verify no out-of-tree github executor before delete.
- **`runtime/platform_policy.py:62-103`** (H) — `current_os`/`protected_paths`/`is_protected_path`
  + `_PROTECTED_*` 0 callers; only `is_system_file` used. Docstring falsely claims it backs the
  write/move safety net (§2.8 drift). Remove or wire it in.
- **`runtime/config.py:124-146`** (H) — 7 path constants `DB_{SCRATCHPAD,PAIRINGS,APPROVALS,
  DEVICES,POLICY,OBSERVABILITY}`, `LOG_LOCATIONS_JSONL` have 0 refs; owners re-derive the same
  path (`devices.py:40`, `approval_registry.py:31`, `location_store.py:22`) — dead keys AND a
  §7.11 single-path-source violation. Make owners import the constant; drop the orphan `DB_POLICY`.
- **`install/downloads.py:295 fetch_all`** (H) — 0 callers. Remove.
- **`install/sidecar.py:580`** (H) — `"installed"` in the success set; no installer returns it.
- **`install/sidecar.py:208,414` `port`/`country` kwargs** (H) — never set by dispatch/CLI
  (registry calls `(yes=yes)`); a user can't pick Photon's country except via undocumented env.
  Parse `--port`/`--country` in `main()` or drop the kwargs.
- **~25 executors: `avail = sorted(_HANDLERS.keys())`** (H) — computed, never used.
- **`llm_provider.py:1062 make_provider_from_config`** (H), **`llm_router.py:262 reload_prompts`**
  (M), **`virt.get_llm`** (M), **`scheduler_v2/client.py` timer API** (M, tests-only) — 0 prod callers.
- **Planning-core vestiges** (H) — `synt.py:281 keywords_from_proto_name`, `prefilter.py:582
  _PIPELINE_HELPERS`, `routing_pool.py:130 recruited_objs`, `proposer_metis.py:35
  _render_prior_steps` (unused import), `vocab.py:816 _imports_root` (singular).
- **Small** — `daemon.py:130 _media_group_clear`, `orchestration.py:64 _safe_sender`,
  `speculation.py:130` unreachable `read_urls_html` arm, redundant re-imports
  (`http_routes_agent.py:2125`, `metnos_http_server.py:126`), `telos_proposals_store.UnifiedProposal`,
  `calibration_check.py`.

---

## 🟢 OPTIMIZATIONS (hot path first)

- **`llm_router.py:171 tier_endpoint`** (M, hot) — re-opens+parses the tiers TOML on EVERY
  `call_llm`; unlike `_prompts()` it's uncached. Cache with invalidation.
- **`runtime/engine/cluster.py:56 cosine` + autopath scan** (H, hot) — pure-Python dot over
  1024-dim, 200-row scan twice per miss, query BGE-embedded 3-4× per turn. numpy is a dep and
  vectors are L2-normalized → embed once, `np.dot`, thread `eb`+`best_cid` through record_*.
- **`runtime/engine/executor.py:97` `_build_runtime_resolvers`** (H) — opens fresh sqlite per
  step though `runtime_ctx` is turn-constant; a 12-step plan = ~12-24 identical queries. Resolve once.
- **`runtime/engine/{fastpath,autopath}.py _conn()`** (H) — full `executescript` DDL + migration
  PRAGMA on every call; `_DB_INIT_DONE` guard is vestigial (`if True:`). Restore a real run-once guard.
- **`scheduler_v2/storage.py:100`** (M) — `_init_schema`+`_migrate_additive` on every `get_storage()`;
  called repeatedly per turn. Init once.
- **`http_routes_agent.py:1753 _resolve_session_user_id`** (H) — N+1: loops all users ×
  `get_channel` (fresh sqlite each). Single reverse `SELECT … WHERE channel='http' AND recipient_id=?`.
- **`http_routes_admin.py:247 admin_changes`** (M) — materializes up to 5000 dataclasses to tally
  counts in Python; a `GROUP BY` suffices (§7.9).
- **`agent_runtime.py:6338`** (M) — renders the planner prompt TWICE for a prefix diff;
  `_render_project_paths_block`/`_render_users_known_block` uncached (read JSON + users DB N+1)
  called up to 3×/turn. Memoize; avoid second render.
- **`prefilter.py:456 affinity_score`** (M, hot) re-tokenizes static affinity+desc per query;
  **`vocab.py:1096 _BIGRAM_VERB_HINTS`** dict rebuilt per `detect_implicit_actions()`. Hoist/memoize.
- **`executors/get_files/get_files.py:53,125`** (M) — opens each image twice (EXIF + dimensions).
- **`install/sidecar.py:247`** (M) — searxng pip runs every invocation; gate on the import check.
- **`install/downloads.py:174`** (M) — `for fut in as_completed` is dedented OUTSIDE the
  `with ThreadPoolExecutor` → bar sits at 0% then snaps to 100% (live progress defeated on
  multi-GB GGUFs). Move the loop inside the `with`.
- **`llm_manager.py:539`** (M-L) — re-hashes the full (≤19 GB) GGUF on every reuse. Cache a
  `.sha256` sidecar keyed on size+mtime.

---

## 🔵 FACTOR-OUT (highest leverage)

- **Executor boilerplate → `runtime/executor_helpers.py`** (H, biggest surface):
  - `run_stdio(invoke)` — the `json.load(stdin)/JSONDecodeError/stdout.write(json.dumps(...))`
    `main()` copied in ~64-74 executors.
  - `dispatch_by_client(handlers, args, method)` — the `_HANDLERS`+`ERR_NOT_APPLICABLE`
    dispatcher in ~26 executors (also kills the dead `avail` line).
  - `apply_truncation(out, *, what_key, used, available_total, cap_field, cap_value, intentional)`
    — the §2.7 envelope in ~19 executors, resolving `what_key` via `_msg` (fixes the i18n drift).
  - `coerce_cap(args, key, default, *, zero_unlimited=True)` — 5 divergent variants in ~15
    executors (fixes the §2.4 cap-0 bug + int() crash above).
  - `vectorize(items, fn, *, output_key)` — the `results/failed/ok_count` envelope in ~36 executors.
- **`hashutil.sha256_file(path, *, chunk, max_bytes)`** (H) — file-streaming sha duplicated 4×:
  `install/downloads.py:62`, `install/sidecar.py:96`, `install/llm_manager.py:289`,
  `create_images_indices…:181`. (`hashutil` already owns the string variant.)
- **`install/sidecar.py` `_write_http_dropin(conf, env)`** (H) — `_write_vlm_dropin` +
  `_photon_dropin` are near-duplicates; one helper also fixes the §8.6 restart bug once.
- **`executor_helpers.catalog_names(catalog)`** (H) — catalog→name-set idiom copy-pasted ~11× in
  `engine/dispatch.py` (4 use a buggy getattr-only form yielding `{None}` on dict entries).
- **`timefmt.now_iso_z`** (H) — `time.strftime("%Y-%m-%dT%H:%M:%SZ", gmtime())` inlined 5× in
  `autopath.py`; also `synt._jaccard`→`loader.jaccard_affinity`, `routing_pool._tool_object` dup.
- **`make_sender_id(actor, channel)`** (H dup / M actionability — memory #131 deferred) —
  `{channel}:{actor}` / `actor or "host"` reconstructed in 6+ files. Verify fallback parity first.
- **Repeated invoke/SQL blocks** (H) — `orchestration.py:490/571/672` (`_lookup_and_invoke`),
  `http_routes_admin.py:596/1255` (`_invoke_builtin_callback`, is_async-aware — the dup is what
  let bug #4 diverge), `daemon.py` pairing-upsert reaching into private `pairing._open_db`,
  `telegram.py` multipart POST across 3 senders, base64url codec (`pairing.py`≡`devices.py`).

---

## STATUS — what was fixed (29/6, committed) vs deferred

**Done + tested green (full suite 2956/0 after each checkpoint):**
- **Batch 1** `cbc5a2c` — correctness bugs: `_resolve_fillers` multi-filler,
  `_touch` swallow→log+close, fastpath 0a→0b clean-miss, autopath conn leaks,
  `RewardBreakdown` args, admin async-timer await, orchestration raw-leak shaper,
  stale model id (DEFAULT_TIERS), email_metnos dup key, 2 docstring placements.
- **Batch 2** `8bcea32` — universal helpers `coerce_cap` (fixes the §2.4 cap-0 +
  int-crash in 4 executors, re-signed) + `catalog_names` (the {None} bug, ~11
  dispatch sites).
- **Batch 3** `ae13dc7` — install: `_write_http_dropin` (fixes §8.6 restart-during
  -turn once), drop dead "installed" status + wire `--port/--country`, playwright
  health probe, `downloads.fetch_all` removed, progress-loop moved inside executor.
- **Batch 5** `28d404c` — zero-ref dead code: `skill_wrapper_github.py`,
  `make_provider_from_config`, `_PIPELINE_HELPERS`, metis unused import.
- **Batch 6** `7118191` — `tier_endpoint` TOML parse cached (mtime-invalidated).

**RECONCILIATION — audit false-positives caught (verification before deletion):**
Several "dead" claims were WRONG — `testing/populate_cases.py` (a test-data
generator the agents' greps skipped) actually uses them. KEPT, not deleted:
- `channels/approval.py` (ApprovalRequest/render_approval_card) — used ×3 by populate_cases.
- `synt.keywords_from_proto_name` — used by a populate_cases synt case.
- `proposals_unified` — test-coupled (surgical removal needed, not a clean rm).
- `config.py` DB_* constants — 0 static refs but dynamic-getattr risk + low value.
- `routing_pool.recruited_objs` — write-only but hot path; left.
- `engine/fastpath._conn` `if True:` — DELIBERATE (docstring: avoids stale-flag
  bugs on bench DB reset); not an oversight. Left.
- Legacy planner (~3.3K LOC): probe shows `total:1` (upload_fallthrough) → NOT 0 →
  stays per protocol; the residual upload trigger needs investigation, not removal.

**DEFERRED (large-effort / lower-correctness-value — rationale):**
- **i18n §11 sweep** (~20 files): needs new ERR_*/MSG_* keys seeded IT+EN into the
  i18n DB + seed + re-sign executors. Real but mechanical+heavy; no behaviour bug,
  just hardcoded strings. (apply_truncation helper is the lever for the executor side.)
- **Executor boilerplate dedup** (run_stdio / dispatch_by_client / apply_truncation /
  vectorize across ~70 signed executors): large mechanical rewrite + 70 re-signs +
  full suite. High churn, dedup-only (no bug beyond the cap-0 already fixed). Best
  done incrementally + via the synt stage-5 prompt so NEW executors emit helper code.
- **Remaining optimizations** (cosine numpy/embed-once, per-step resolver, N+1
  `_resolve_session_user_id`, admin_changes GROUP BY, uncached block renderers):
  hot-path perf, each needs careful invalidation; no correctness impact.
- **Prior audit** `multidim_findings_21_6.json` (71 findings): cross-referenced by
  the sweep; its still-valid dead/dup items overlap this report. A full line-by-line
  re-verification at HEAD is its own pass (many likely fixed since 21/6).

## Suggested order of attack
1. **30/6**: legacy-planner removal (deadline-driven) + `proposals_unified` + `approval.py`.
2. **Quick correctness wins** (small, high-impact bugs): #1 `_resolve_fillers`, #3 `_touch`,
   #4 async-timer, #6 cap-0, #10 find_urls `>=`, #12 dup dict key, #17/#18 my sidecar/playwright,
   #15 stale model ids.
3. **Executor-helper consolidation** (closes the most duplication + the §2.4 bug + i18n drift).
4. **Hot-path optimizations** (tier_endpoint cache, cosine/embed-once, per-step resolver, _conn guard).
5. **i18n §11 sweep** + remaining dead code; reconcile `multidim_findings_21_6.json`.
