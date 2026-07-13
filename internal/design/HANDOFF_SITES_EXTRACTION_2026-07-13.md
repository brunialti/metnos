# Handoff: sites / structured extraction gap (2026-07-13)

## Objective

Make an authenticated read of a web page return **structured, typed records**
to the user, not a raw-text summary. Canonical failing intent:

```text
entra nel sito amazon e dimmi quali sono gli articoli nel carrello
```

The user expects a list of cart items (name, price, quantity). Today the
pipeline can at best hand the whole page text to `describe_entries`, which
summarises an unstructured blob. There is no explicit step that turns the page
into `[{nome, prezzo, quantita}]`. This handoff scopes that gap and the design
decision behind closing it.

This is a **routing/design** task, not a mechanical bug. A related, separate
runtime bug (`describe_entries` rejected on gate-resume) was already fixed this
session — see "Already fixed" below. Do not re-open it.

## Domain roles (verified from code — do not re-derive)

The four sites/helper steps have distinct, non-overlapping jobs. The user's
mental model conflated navigation with extraction; it is wrong.

| Step | Job | Output shape | Extracts records? |
|------|-----|--------------|-------------------|
| `act_sites(search=…)` | **Navigate** a post-login goal (reach the cart) | `results=[{session_id, ok, executed, primitive, url, observed_candidates}]` (`executors/act_sites/act_sites.py:114`) | **No** — navigation outcome only, no page content |
| `read_sites` | **Read** the current page | `entries=[{session_id, url, title, text, screenshot_path, sensitive}]` — `text` is the raw `innerText` blob of the whole page (`executors/read_sites/read_sites.py:11`) | **No** — one entry, full-page text |
| `extract_entries` | **Structure** unstructured text into typed records (§2.2) | `entries=[{<fields>...}]` | **Yes** — this is the missing step |
| `describe_entries` | **Tell** the user (NL summary of entries) | text/summary | No — presentation |

So the §2.2-correct chain for "tell me the records X on page Y" is:

```text
open_sites → login_sites → [act_sites: reach target page] → read_sites (raw text)
           → extract_entries(fields=[…]) → describe_entries
```

`extract_entries` is absent from every plan we observed for this class of query.

## Findings

### 1. The guard never inserts extraction

`runtime/engine/dispatch.py:2815` `_ensure_site_session_precursor` reconstructs
the canonical sites chain `open_sites → [login_sites] → [read_sites | act_sites]`.
It has no notion of a structured-extraction step, so `extract_entries` is never
added deterministically.

### 2. The planner rarely emits it on its own

`extract_entries` is a universal helper available to the proposer, but the local
medium model (Qwen) does not reliably emit `read_sites → extract_entries →
describe_entries` for NL like "dimmi gli articoli". There is no deterministic
guarantee. `describe_entries` is what the runtime appends as a terminal helper
when `intent.verb` is not an action verb — and it does **not** cover extraction.

### 3. `describe_entries` on raw text "works" but is the wrong contract

When the plan is `read_sites → describe_entries`, the model summarises the raw
page text. It sometimes yields a readable answer, but produces **no typed
records**, is fragile to page noise, and cannot feed a downstream
filter/sort/export. Note also: the engine **skips** `describe_entries` entirely
when the previous step self-presents via `final_message_hint`
(`runtime/engine/executor.py:1961`, reason `final_message_hint_present`) — so
whether the summary even runs is producer-dependent.

### 4. `extract_entries` is the right tool and already denoises — proven

Empirical test (local LLM only, **zero** contact with Amazon): a synthetic,
noisy cart page text fed to the real `handle_extract_entries` with
`fields=["nome","prezzo","quantita"]` returned clean records and dropped the
sponsored/menu/footer noise:

```json
{"nome": "Echo Dot (5a gen) … Alexa - Antracite", "prezzo": "34.99", "quantita": "1"}
{"nome": "Cavo USB-C Anker 2m (confezione da 2)",  "prezzo": "12.99", "quantita": "2"}
```

### 5. The real design nub: who supplies `fields`?

`extract_entries` **hard-requires** `fields: list[str]` and fails
`invalid_args` without it (`runtime/extract_entries.py:282-292`). The
`entries`/`from_step` wiring is NOT a problem — `extract_entries` is in
`_ENTRIES_CONSUMERS` (`runtime/engine/executor.py:383`), so the engine auto-wires
the previous step's list when `entries` is missing. The only missing input is
`fields`, and `fields` are query/domain-dependent (cart → nome/prezzo/quantità;
invoices → data/descrizione/importo). A purely deterministic guard can insert
the *step* but cannot deterministically know the *fields*.

## Proposed solutions (design decision — pick one)

- **(2a) — recommended.** Make `fields` **optional** in `extract_entries`: when
  absent, the executor infers the fields from the goal/query (it already makes
  an LLM call; this adds a bounded inference of the field set, or a
  schema-free "one record per item with salient fields" mode). Then extend the
  sites guard to insert `read_sites → extract_entries → describe_entries`
  deterministically for extraction-shaped intents ("dimmi/estrai/elenca
  <records> da <pagina>"). Deterministic where it must be (chain **shape**), LLM
  only where it is irreducible (field **discovery**). Aligns with §7.9 (code >
  LLM only when equipotent) and §2.2.
- **(b)** Leave it to the planner to emit `extract_entries(fields=…)`. Simpler,
  no new guard, but unreliable on the local medium model — expect misroutes.
- **(3)** Do nothing; accept implicit extraction inside `describe_entries`.
  Simplest, but violates §2.2 (no typed records) and is fragile.

Recommendation: **(2a)**. Open questions for whoever implements it:
- How does the guard detect an "extraction" intent without a synonym list
  (§7.3)? Prefer a boundary in `detection_lexicon` / a deterministic predicate,
  not a hardcoded verb list.
- Should `fields` inference live in `extract_entries` (per §7.9, keep the
  executor self-sufficient) or be a separate helper? Recommend inside the
  executor, so the drop-in stays a drop-in.
- Interaction with the `final_message_hint` skip (finding 3): if `read_sites`
  ever emits a hint, ensure the inserted `extract_entries`/`describe_entries`
  still run.

**Escalate the chosen option to Roberto before implementing** (§10.2: routing /
vocabulary changes are design decisions). Do not change the guard or add a
vocab token unilaterally.

## Required tests

1. Unit: `extract_entries` with `fields` omitted infers a sensible field set for
   a cart-like text and returns typed records (no `invalid_args`).
2. Unit: the sites guard, given an extraction intent + an `open/login/read`
   plan, inserts `extract_entries` between `read_sites` and `describe_entries`,
   idempotently (no double insert on re-run).
3. Engine: a `read_sites (text) → extract_entries → describe_entries` chain runs
   end to end with synthetic observations (stub `invoke`, no live site) and the
   final answer lists the structured items.
4. Property/§7.3: no hardcoded field names, no hardcoded site/vendor strings, no
   synonym list for intent detection.
5. E2E (§8.5) — **once, carefully**: a single real turn on a site the account
   owns. See the ban warning below; do **not** loop logins.

## Test example (Amazon) — concrete, with per-step cautions

Use this as the single real E2E, only **after** the offline tests above are
green. It exercises the full chain including the extraction step.

### Setup (already done, for reference)

- Credentials for `amazon.it` are in the vault (`turn:7acba80d`,
  `set_credentials amazon.it`). Verify with `metnos-cli credentials list`; do
  **not** re-enter or print them.

### Query

```text
entra nel sito amazon e dimmi quali sono gli articoli nel carrello
```

### Expected pipeline (target after the fix)

```text
open_sites(https://www.amazon.it)
  → login_sites
  → act_sites(search: reach the cart)          # navigate, no extraction
  → read_sites                                 # raw cart page text
  → extract_entries(fields inferred/nome,prezzo,quantita)
  → describe_entries                            # tell the user
```

### Expected output

A structured list of cart items, e.g.
`[{nome, prezzo, quantita}, …]`, surfaced as an NL summary. Empty cart → an
honest "carrello vuoto" (§2.8), never a fabricated item.

### Observed today (baseline, pre-fix)

`turn:520a574f` ran `open_sites` (ok) and then **paused on an allowlist gate**
for the ad host `aax-eu.amazon-adsystem.com` — it never reached
login/read/extract. That gate is the first thing to handle correctly (see
cautions).

### Cautions specific to this test

1. **Do NOT approve `amazon-adsystem.com` / `aax-*` (or any third-party
   ad/telemetry host) to force the flow.** It is unrelated to the cart goal —
   exactly the class of host the Booking handoff
   (`HANDOFF_SITES_BOOKING_2026-07-13.md`) says must never be allowlisted to
   "make it work". The cart must be reachable first-party
   (`amazon.it` / `www.amazon.it`). If it is not reachable without an ad host,
   that is a **finding to report**, not a gate to approve. Approving it also
   makes a persistent credential mandate impossible.
2. **One attempt, then stop.** Amazon's anti-automation is aggressive; repeated
   logins risk CAPTCHA walls, forced OTP, or a temporary account lock (this
   session already got the host `.33` IP-banned by the user's *router* for the
   same reason — see below). Never loop logins to "retry until it works".
3. **CAPTCHA / OTP / 2FA are expected** and must produce a bounded, honest
   handoff to the user (redacted screenshot, session kept), never a silent
   failure and never an auto-solve attempt.
4. **Never persist** cart page content, credentials, or OTPs beyond the bounded
   structured records the user asked for.
5. Amazon may land on a locale/consent interstitial before the cart; the
   overlay-dismissal and login-surface-wait already handle common cases, but if
   navigation stalls, inspect the bounded candidate list — do **not** add an
   Amazon-specific selector or menu path (§7.3).

## Constraints and warnings

- **Do NOT hammer real sites.** During this session, repeated automated logins
  got the source host `.33` temporarily IP-banned by the user's router (its
  `status.cgi` returned 503 to `.33` while `.137` got 200 — a per-source-IP
  brute-force block). Amazon will do the same or worse. Any live validation is
  **one** attempt, then stop. Prefer stubbed/offline engine tests.
- `extract_entries` LLM calls are fine (local Qwen, no external site contact).
- Playwright is local to Metnos:
  - Python: `/home/roberto/.local/share/metnos/.venv/bin/python`
  - browsers: `PLAYWRIGHT_BROWSERS_PATH=/home/roberto/.local/share/metnos/playwright-browsers`
- Do not print/persist credential values, OTPs, or authenticated page content
  beyond bounded structured records.
- Preserve the drop-in executor contract: planner I/O must not need to know the
  internal intelligent login/action procedures.

## Already fixed this session (context — uncommitted, dirty worktree)

These are DONE and validated by tests; do not revert or re-open. They live in
the current dirty worktree on branch `session/detection-lexicon-i18n`:

- **Guard derives `http://<ip>` from a bare IPv4** so `open_sites` is
  reconstructed for LAN panels (`runtime/engine/dispatch.py:2796`
  `_site_url_from_host_token`). Fixes `turn:e8d23c80`.
- **Cookieless login is confirmed by route navigation**, not only a changed
  cookie (`runtime/playwright_sidecar/credential_injection.py`
  `_post_submit_authenticated`); added `password_rejected` to the negative
  guard. Fixes `turn:133ae123` (FASTGate reused the same `sessionID`).
- **Bounded wait for the login surface on the initial SPA landing** (SPA lands
  on `/` then routes to `#/login`); removed the dead `settle_initial` param.
- **Gate-resume tail dispatches builtins universally**: new canonical
  `agent_runtime.invoke_tool_by_name` (builtin-first via
  `_BUILTIN_TOOL_HANDLERS`, then catalog) + augmented tail catalog via
  `_engine_v2_catalog_with_builtins`, used by
  `orchestration._process_resume_executor_gate_tail`. Fixes `turn:520a574f`
  ("Executor describe_entries non in catalog: rilancio annullato").

Tests: `runtime/tests/test_sites_security.py`,
`runtime/tests/test_orchestration.py` (regression
`test_resume_executor_gate_tail_invokes_universal_builtin`).

## Key files / functions

- `runtime/engine/dispatch.py:2815` — `_ensure_site_session_precursor` (where an
  extraction step would be inserted).
- `runtime/extract_entries.py:271` — `handle_extract_entries`; `fields` required
  at line 282; tool spec `EXTRACT_ENTRIES_TOOL`.
- `runtime/engine/executor.py:383` — `_ENTRIES_CONSUMERS` (auto-wire of
  `entries`); `:1961` — `describe_entries` skip on `final_message_hint`.
- `executors/read_sites/read_sites.py` — page-text producer.
- `executors/act_sites/act_sites.py` — navigation (not extraction).
- `CLAUDE.md` §2.2 (extract boundary), §7.3 (no hardcoding), §7.9 (code > LLM),
  §10.2 (design decisions to Roberto), §8.5 (E2E on every product change).

## Recommended next action

Confirm option (2a) with Roberto. Then: make `fields` optional in
`extract_entries` (infer when absent), add the guard insertion behind a
deterministic extraction-intent predicate, cover with the offline tests above,
and only then run one careful real E2E.
