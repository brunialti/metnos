---
id: 0070
title: Admin → (scheduler?) → sudoer chain — shell orchestration via NL intent
date: 2026-05-02
status: accepted
area: runtime, executor, security
related:
  - 0010  # synthesis cascade with non-renunciation telos
  - 0013  # builtin executors third category
  - 0058  # intent extractor LLM-based
  - 0067  # introvertiva MVP — events.turn_id refactor
  - 0068  # recurring tasks callback registry
  - 0069  # builtin + verb-unique fourth category
complements:
  - 0069
---

## Context

After the diagnostic of 2 May 2026 (timezone bug in the recurring
scheduler) and the ensuing reflection on system-administration capabilities
for Metnos, we settled on a model where the user expresses **an intent in
natural language** and Metnos translates it into shell commands &mdash; never
the reverse. The user passing a literal shell command is rejected on the
spot with a canonical phrase, before the planner is even invoked. See
project memory `metnos_shell_exec_analysis.md` for the seven-iteration
discussion that led here.

The architecture that emerged is a chain of two privileged primitives,
optionally interleaved with the existing scheduler:

```
admin   →   (scheduler?)   →   sudoer
deliberative   temporal           executive
```

The two new primitives `admin` and `sudoer` belong to the *builtin +
verb-unique* category formalised in ADR 0069: their verbs are outside the
closed vocabulary, they are invisible to the PLANNER, they are invoked
only by the system dispatcher.

This ADR specifies:
1. the four-act flow that `admin` runs internally;
2. the chain composition with the scheduler;
3. the graylist / whitelist / blacklist curation model with mnestoma-style
   reinforcement and decay;
4. the three-option approval card surfaced to the user;
5. the three-level wait prompt that hides the latency.

The companion ADR 0071 specifies the deterministic safety tools and the
seed bootstrap that this ADR depends on.

## Decision

### Two builtin + verb-unique primitives

**`admin`** is the deliberative primitive. It receives the user's natural
language utterance and produces a validated argv list, ready to execute,
plus a reversibility tag and an approval token. It does not execute
anything itself.

**`sudoer`** is the executive primitive. It receives a validated argv list
plus a one-time secret slot for the sudo password (when required), executes
the command in `subprocess.run(argv, shell=False)` inside a `bwrap`
sandbox profile, and returns a structured result. Before executing, it
**re-validates** the command against the deterministic safety tools (see
below) to honour any blacklist edits Roberto made between the planning
and the firing.

The existing **`scheduler`** (ADR 0068) can be inserted between `admin`
and `sudoer` for time-deferred execution. The chain `admin → scheduler →
sudoer` becomes the standard shape for «do this at time T» requests.

### The four-act flow inside `admin`

```
[pre-1] Syntactic gate (no LLM, no tool):
        if utterance matches shell pattern (sudo /; ;; |; &&; $(; >; <;
        ./; known binary at start of line),
        emit canonical phrase:
            "Mi spiace, non accetto comandi diretti. Dimmi cosa intendi
            fare e vedo se posso farlo io senza infrangere vincoli di
            sicurezza."
        STOP. Audit kind = `gate_rejected_literal`.

[1+2+3] Single LLM call (tier=middle, think=false, ~400 tokens):
        prompt + JSON-schema-guided output. Four possible kinds:
          - {kind: "literal_command", reason}     → STOP, gate_rejected_llm
          - {kind: "translated", argv: [...]}     → continue
          - {kind: "unknown", reason}             → STOP with "non so fare"
          - {kind: "impossible", reason}          → STOP with motivated NO

[4]   Deterministic safety tools (ADR 0071), in order:
        compute_signature(argv)            → signature
        find_forbidden_paths(argv)         → if negate, STOP "Legge 1"
        find_blacklist(signature)          → if negate, STOP with reason
        find_whitelist(signature)          →
            if allow=true (permanent or graylist hit):
                silent → emit approval token, signal sudoer
            else (miss):
                show approval card to user
        compute_reversibility(sig, argv)   → tag for the card

[5]   Approval card (only on whitelist miss):
        three options:
          - approve         → graylist insert/update (uses+=1), proceed
          - reject_once     → STOP, no side effect
          - block_forever   → blacklist insert, STOP
```

### The wait prompt at three levels

Because the flow takes 5–10 s in simple cases and up to 20–30 s when an
unknown card is shown, `admin` emits a status message on the user's
channel before each heavy step. Three deterministic levels, chosen by the
flow branch:

- **after [pre-1], always** (low intensity):
  > «Sto valutando se posso fare quello che mi chiedi senza forzare i
  > vincoli di sicurezza, mi prendo qualche secondo.»
- **before the LLM call, when the intent looks non-trivial** (medium):
  > «Il comando che mi stai chiedendo richiede un'analisi più attenta
  > del solito, ti aggiorno appena ho una proposta concreta.»
- **before showing an unknown card** (high):
  > «Non riconosco il comando che dovrei eseguire. Ti chiedo come
  > trattarlo, una volta sola se vuoi.»

One emit per phase, idempotent per turn. The prompts are templated in
`runtime/messages.py` (ADR 0004) under the keys `MSG_ADMIN_WAIT_LOW`,
`MSG_ADMIN_WAIT_MEDIUM`, `MSG_ADMIN_WAIT_HIGH`.

### Graylist mnestoma-style curation

The lifecycle of a signature mirrors the lifecycle of a mnest (ADR 0009):

```
unknown ──[user approves once]──> graylist (uses=1, last_used=now,
                                             weight=1.0)
            │
            ├─[reuse within 30 d, silent]─> uses += 1
            │                                   │
            │                                   ▼
            │                          uses ≥ 5 in 30 d
            │                                   │
            │                                   ▼
            │                          whitelist (permanent)
            │                                   │
            │                                   ▼
            │                          uses ≥ 10 with same
            │                          verb:bin:target_kind
            │                                   │
            │                                   ▼
            │                          Synt proposes promotion
            │                          to a dedicated executor
            │                          (handcrafted in pool)
            │
            ├─[user blocks forever]──> blacklist (permanent)
            │
            └─[no use for 30 d]──────> back to unknown
                                        (next request triggers card again)
```

The `apply_ager` nightly job (ADR 0009 + builtin scheduler) curates both
mnests and safety signatures: same physics, two domains. Decay parameters
are configurable in the manifest of `apply_ager`; defaults are 30 days
TTL for graylist inactivity, threshold 5 for graylist→whitelist promotion,
threshold 10 with same `verb:bin:target_kind` for whitelist→executor
promotion via Synt.

### Approval card with three options

The card surfaces:
- the **exact argv** that will be executed (rendered as a single line
  string for readability), not the original NL intent;
- the **reversibility tag** (`reversible` / `irreversible` / `unknown`);
- the **sudo flag** if password will be requested;
- the **chain shape** if scheduler is interleaved (e.g.
  «alle 23:00 di stasera»).

Three buttons: **Approva** / **Rifiuta una volta** / **Blocca per
sempre**. The fourth historical option («metti in whitelist e esegui» vs
«esegui solo ora») is fused into Approva: the side effect on the lists
is automatic in function of `uses` (graylist insert if first time;
graylist `uses+=1` if subsequent silent; promotion to whitelist if
`uses≥5`).

### Re-validation in `sudoer` at fire time

For chains delayed via the scheduler, `sudoer` re-runs the deterministic
safety checks **at fire time**, not just at planning time:

```
sudoer at fire:
  [tool] find_forbidden_paths(argv)   → STOP if negate
  [tool] find_blacklist(signature)    → STOP if negate
  [tool] compute_sanity_check(intent_text, argv,
                              age_chain, system_state)  ← LLM fast,
                                                          ADR 0071
         smell == "urgent_review"  → STOP, notify user
         smell == "suspicious"     → audit warning, continue
         smell == "ok"             → continue
  [exec] subprocess.run(argv, shell=False) inside bwrap
         + secret slot for sudo password if required
  [audit] events.turn_id structured (ADR 0067)
```

If a chain is revoked at fire time (forbidden, blacklist hit, or sanity
check `urgent_review`), `sudoer` notifies the user explicitly:

> «Avevi pianificato `<rendered argv>` alle `<HH:MM>`, ma ho trovato una
> regola che lo blocca. Non eseguito.»

Trust is earned on honest refusals as much as on successful executions.

### Sanity check activation heuristic

`compute_sanity_check` (LLM fast, ~800 ms) is invoked only when it adds
value:

```
if scheduler_delay_minutes < 5 AND reversibility_class == 'reversible':
    skip
elif reversibility_class in ('irreversible', 'unknown'):
    invoke
elif scheduler_delay_minutes >= 5:
    invoke
else:
    skip
```

The sanity check can never authorise what the deterministic tools have
blocked: it can only add blocks. It is a **second opinion**, not a primary
guard. The deterministic tools remain authoritative.

## Alternatives considered

**(a) Five to seven dedicated handcrafted executors instead of a generic
chain** (`set_clocks`, `restart_services`, `install_packages`, etc.).
Rejected as the primary architecture because it requires writing all
seven by hand at t0 and does not extend gracefully to new commands.
However: this approach is preserved as the *promotion path* &mdash; when a
graylist signature exceeds threshold with the same `verb:bin:target_kind`,
the Synt promotes it to a dedicated handcrafted executor. The chain
covers the long tail; promotions form the nucleus over time.

**(b) Single LLM call replacing all four acts (no syntactic gate, no
deterministic safety tools).** Rejected: the syntactic gate is
deterministic and free, and the safety tools are auditable and replicable.
A single LLM call would be opaque and would lose defence-in-depth.

**(c) Auto-whitelist on every approval.** Rejected: a fast click would
sediment into permanent policy. Three rejected explicitly during the
analysis; only graylist with mnestoma-style reinforcement survives.

## Consequences

**What this opens.** Metnos can now act on the host as a real personal
assistant: timezone, services, packages, journal, disks, processes. The
non-renunciation telos (Law 4) is honoured: refusal becomes the exception,
not the default.

**What this closes.** The temptation to expose a generic `shell_exec`:
the verbs `admin` and `sudoer` exist only to the dispatcher, not to the
planner.

**Work items spawned.**
- ADR 0071 (deterministic safety tools and seed bootstrap) is a
  precondition.
- `runtime/builtins/admin.py` (~400 LOC) and `runtime/builtins/sudoer.py`
  (~250 LOC).
- Three message keys in `runtime/messages.py` for the wait prompts.
- Extension of the `apply_ager` job to cover safety signatures (~30 LOC
  reusing the mnest decay logic).
- Synt promotion criterion (~50 LOC in `runtime/synt.py` to detect
  graylist signatures over threshold and propose dedicated executors).

**What becomes more expensive.** First weeks of usage are noisy: the user
sees many «unknown» cards while the graylist fills. Mitigated by the
extended seed in ADR 0071 (~200 whitelist + ~40 blacklist entries).

**What becomes easier.** Adding administrative capabilities no longer
requires writing code: the user expresses an intent, the chain handles
the rest, and recurring patterns auto-promote to handcrafted executors
through the existing Synt loop. Coltivazione strumenti applied to the
sysadmin domain.
