---
id: 0088
title: admin exposed to PLANNER as ordinary tool with vaglio always-on
date: 2026-05-04
status: accepted
area: runtime, executor, security
related:
  - 0069  # builtin + verb-unique category (modified by this ADR)
  - 0070  # admin → sudoer chain
  - 0071  # safety signatures
  - 0079  # planner anti-collision guard
  - 0087  # CIFS mount via admin/sudoer
modifies:
  - 0069  # restricts the EXPOSE_TO_PLANNER=False invariant for `admin`
complements:
  - 0070
  - 0071
  - 0087
---

## Context

Live observation 4 May 2026 (turn `fc77604a`): the user typed «monta nella mia
area personale lo share `\\Public\\media\\Immagini` come utente roberto». The
planner did not have `admin` in its tool pool because ADR 0069 declared
`EXPOSE_TO_PLANNER=False` as an invariant of the verb-unique category. The
chain produced by the LLM was the next-best thing, and the wrong thing:

1. step 1 — `request_new_executor(expected_name="mount_share")`,
2. synth stage 1 rejected: «verbo `mount` fuori vocabolario»,
3. final_answer of surrender («non posso eseguire questa operazione»).

Yet the entire admin → sudoer chain (ADR 0070) is implemented and tested. The
safety taxonomy (ADR 0071) covers `mount.cifs:fs-mount-cifs` (ADR 0087, seed
v2). The encrypted credentials store (ADR 0082) holds the CIFS password. The
six-line dialogue between the user and the system that *should* execute the
intent is sitting dormant because no dispatcher wakes it up.

The original ADR 0069 took a strict line: the verbs of admin/sudoer must not
be visible to the planner, period. The reasoning was sound — exposing
privileged primitives to a stochastic LLM is dangerous, and the boundary
should be absolute. After three weeks of running with the strict line, the
cost shows: every shell-adjacent intent the user types (mount, kill,
systemctl, chmod) hits the synth pipeline, fails, and returns a surrender
message. The user sees a system that can read mail, summarise files,
schedule tasks — and cannot mount a NAS share.

The mitigation we have, that ADR 0069 did not consider, is the dialog
manager card (project memory `dialog_manager_authorization_ux`). Each
admin call already produces an approval card: «for «mount nas share» I
propose `mount -t cifs ...`, signature `mount:cifs:fs-mount-cifs`. Approve
once, reject once, block forever?». The user is the gate, not the LLM.
Exposing admin with always-on approval card preserves the boundary while
unlocking the capability.

## Decision

**Modify ADR 0069 invariant 2.** Verb-unique builtins MAY declare
`EXPOSE_TO_PLANNER=True` provided they also declare `MANIFEST_VIRTUAL`
(an in-code dict equivalent to the manifest TOML of a handcrafted
executor). The loader uses `MANIFEST_VIRTUAL` to construct an `Executor`
entry inside `Catalog.executors`, visible to the PLANNER like any other.

Concretely:

- `runtime/verb_unique/admin.py`:
  - flip `EXPOSE_TO_PLANNER` from `False` to `True`,
  - add `MANIFEST_VIRTUAL` (~1500 char description, args schema with
    `intent` + `command_proposed` + optional `actor_consent_token`,
    `affinity` covering shell verbs IT+EN, `capabilities=[admin.shell]`),
  - add `invoke()` planner-facing entrypoint that runs the safety flow
    on the argv supplied by the planner (no more LLM translation —
    the planner has already produced the argv), emits an approval
    card on miss, or skips it when a valid `actor_consent_token` is
    presented at the next turn.
- `runtime/verb_unique/sudoer.py`: unchanged. `EXPOSE_TO_PLANNER=False`
  remains; sudoer is invocable only from `builtins.admin` (and the
  scheduler).
- `runtime/loader.py`:
  - allow `EXPOSE_TO_PLANNER=True` on verb-unique builtins (with
    `MANIFEST_VIRTUAL` mandatory in that case),
  - inject planner-visible verb-unique entries into `Catalog.executors`
    after the GC pass, idempotent on reload,
  - the `register_verb_unique_builtin` callable preference is now
    `invoke > decide > execute`, so the planner-facing entry wins.
- `runtime/agent_runtime.py`:
  - special-case `chosen_name == "admin"`: dispatch via
    `loader.invoke_verb_unique` instead of subprocess executor; an
    `approval_required:True` reply is wrapped into an `expandable_caps`
    entry of `kind="admin_approval"` so the channel daemon resumes
    correctly,
  - the runtime closes the turn immediately when admin asks for
    approval; the next user turn drives the resolution.
- `runtime/channels/daemon.py` and `runtime/http_routes_agent.py`:
  - cap-pending consume path recognises `kind="admin_approval"` and
    invokes admin directly with the saved args + `actor_consent_token`,
    bypassing the planner round-trip,
  - the standard cap-expand path is unchanged.
- `runtime/prefilter.py`: shell-intent hints (mount, monta, kill,
  systemctl, chmod, ifconfig, apt, journalctl, …) trigger an explicit
  injection of `admin` into the top-K with score 15, above the regular
  primary boost. Both the intent path (`rank_with_intent`) and the
  bag-of-words fallback (`rank_adaptive`) honour this rule.

### Security invariants preserved

- **Vaglio always-on.** Every admin call goes through the deterministic
  safety classifier (`safety/canonicalize.py`). Whitelist hits execute
  silently — the same behaviour as ADR 0070. Graylist/unknown hits emit
  the approval card; the planner cannot bypass the user.
- **Sudoer remains invisible.** It is callable only from
  `builtins.admin` and the scheduler. The PLANNER has no notion of its
  existence.
- **Capability `admin.shell` required.** The manifest declares it; the
  vaglio runtime enforces capability checks before invocation.
- **HMAC-signed consent token.** When the user approves the card, the
  daemon does NOT replay the original argv blindly — the token is
  HMAC-SHA256 over `(signature, actor, expiry)`, with TTL 600 s. Admin
  re-validates the token at the second turn before invoking sudoer. A
  stolen pending file alone is not enough to fire the command; the
  attacker would also need the HMAC key (file mode 0600 under
  `~/.local/share/metnos/`).

## Alternatives considered

**(a) Keep ADR 0069 strict and write a `mount_shares` handcrafted
executor** (and `kill_processes`, `restart_services`, `set_files_permissions`,
`install_packages`, …). Rejected: this is exactly the explosion ADR 0087
warned against. Each new privileged verb-of-domain would need its own
manifest, its own credential UI, its own state-mode discussion. The chain
already covers the family; we just need the planner to see the entrypoint.

**(b) Expose admin only behind a feature flag.** Rejected: feature flags
encode «we are not sure»; we are sure. The approval card is the right gate
even with admin visible. No flag, just the new default.

**(c) Auto-call admin from prefilter when shell intent is detected.**
Rejected: this would pre-empt the planner entirely. Some shell-adjacent
queries are still fine for handcrafted tools (e.g. «kill processo zombie»
might be served by a future `delete_processes` executor — see executor
diary). The planner must remain the orchestrator; we only add admin to
its toolbox, with strong prefilter priority.

## Consequences

**What this opens.** The user can now mount NAS shares, kill stuck
processes, restart misbehaving services, change permissions on a folder,
inspect system logs, install Debian packages — all from chat, all behind
the same approval card. Future shell-adjacent intents (NFS, SSHFS, fstab
persistence, journald flushing) follow the same pattern with no
additional wiring beyond a seed entry in `safety/v1.toml`.

**What this closes.** The temptation to write a family of dedicated
executors for every privileged verb-of-domain. The chain proves itself
on a third non-trivial intent (after CIFS mount in ADR 0087).

**Work items spawned.**

- `runtime/verb_unique/admin.py`: ~270 LOC (`MANIFEST_VIRTUAL`, `invoke()`,
  `_decide_for_argv`, `_spawn_via_sudoer`, HMAC consent token helpers).
- `runtime/loader.py`: ~70 LOC (loosened invariant, `_inject_planner_visible_verb_unique`,
  `_build_admin_executor_from_manifest_virtual`, callable preference order).
- `runtime/agent_runtime.py`: ~55 LOC (admin special-case dispatch +
  approval pending wrap).
- `runtime/channels/daemon.py`: ~25 LOC (cap-pending kind detection,
  `_consume_admin_approval`).
- `runtime/http_routes_agent.py`: ~25 LOC (cap-pending 3-tuple, immediate-
  message branch for admin approval).
- `runtime/prefilter.py`: ~45 LOC (`_SHELL_INTENT_HINTS`,
  `_detect_shell_intent`, head-injection in both ranker paths).
- `runtime/tests/test_admin_exposure.py`: 5 tests (catalog membership,
  prefilter top-K, planner-mock invocation, approval card flow,
  cap-pending consume).
- ADR 0069 receives a header note marking «modified by 0088».

**What becomes more expensive.** Negligible. The catalog gains one
visible entry, the prefilter gains one branch, the runtime gains one
special-case. The vaglio surface (admin.shell capability) is unchanged.

**What becomes easier.** Adding a new privileged primitive that should
be planner-visible: declare `EXPOSE_TO_PLANNER=True` and
`MANIFEST_VIRTUAL` in its module, the loader does the rest. Adding a new
shell verb to the closed safety taxonomy: edit `safety_seeds/v*.toml`
and `canonicalize.py`, no code changes elsewhere.

**Carry-over.**

- A real CIFS mount end-to-end test with credentials saved via
  `cifs_helper.store_cifs_credentials` (today the smoke uses dummy
  username and asserts only that admin reaches the approval card stage).
- Synchronisation of the HMAC key (`~/.local/share/metnos/.admin_consent_key`)
  across nodes if multi-node admin chains become a use case (today
  irrelevant — admin runs only on `.33`).
