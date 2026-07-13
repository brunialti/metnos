# Handoff: sites / Booking E2E (2026-07-13) — RISOLTO (13/7 sera)

> **Resolution (2026-07-13 evening).** Resource provenance + relevance
> filtering implemented in `session_broker.py` (structured
> `blocked_requests` observations; implicit `selector_missing` fallback
> proposes only first-party/credential-origin hosts; explicit
> `required_hosts` evidence unchanged). Goal-step audit enriched (kind,
> resolved role/name, confidence, model_selected, url before/after,
> pre-dispatch-blocked). Required tests 1-4 added, 5-6 already covered.
> Fresh Booking E2E `727f9ba37b3d4732`: no manual dialog, `logged_in=true`,
> `act_sites` navigated account menu → "Bookings & Trips" → `goal_complete`
> with NO goal-scoring change needed, `read_sites` returned the trips page,
> session closed. Full suite 3820 passed / 27 skipped. Details: ADR 0188
> amendment + memory `project_session_13_7_2026`.

## Executive summary

The Booking end-to-end command is **not complete yet**:

```text
accedi al sito booking.com e trova le mie prenotazioni
```

Two formerly independent failures have been isolated and corrected in the
current dirty worktree:

1. the email factor resolver now retrieves and submits the six-character code
   deterministically;
2. a late promotional overlay is now dismissed through a bounded, safe and
   site-independent procedure.

The current blocker is downstream of login. When goal navigation can no longer
resolve the next control, resource fallback proposes a newly generated
third-party advertising iframe host:

```text
c5716d6e87ae0df75263f06ede4fb69f.safeframe.googlesyndication.com
```

This produces another approval dialog. It must **not** be solved by approving
or hardcoding that hostname: the random host is unrelated to the user's goal
and makes persistent credential mandates impossible.

## Reproduction evidence

### Initial factor failure

- Failed session: `51476e082a66e3aeefd9dc05b4b3c6a7`
- Turn: `d0507ba4f9a24b83`
- Audit sequence:
  - `factor_prepare`
  - `factor_pending`
  - `factor_resolving`
  - `factor_resolution status=ambiguous`
- The page was the Booking six-field email verification form.

A safe mailbox probe printed no code or message content. It showed that each
new Booking message contained:

- one strong six-character candidate in the subject;
- one false four-letter candidate in body prose, caused by the permissive
  expression that accepted an arbitrary word immediately after `code`.

### Factor correction proven on the real flow

- Turn: `1d1588961e1c4bdd`
- Session: `cc097f845140392a956073e73eb92514`
- Audit:
  - `factor_resolution status=found`
  - `relevant_messages=1`
  - `candidate_messages=1`
  - `candidate_count=1`
  - `expected_length=6`
  - `numeric_only=false`
  - `factor_submit`
  - `login_attempt outcome=true`
  - `login_phase complete`

The login result was `logged_in=true`. No factor value left the broker.

### Late-overlay failure

The same turn reached `act_sites` but failed with:

```text
target_changed / click_actionability_timeout
```

Screenshot:

```text
/home/roberto/.local/share/metnos/sites-shots/host/
cc097f845140392a956073e73eb92514_1783958597652.png
```

It shows an authenticated Booking page covered by a centered promotional
modal ("Exclusive extras just for you!", safe exit "Got it"). The target had
been selected before the modal became actionable, so Playwright correctly
reported that no click was dispatched.

### Overlay correction proven

A synthetic browser test used a generic fixed layer without `role=dialog` and
returned:

```text
before_topmost=false, dismissed=true, after_topmost=true
```

The latest real flow also records:

```text
overlay_dismiss method=label outcome=true
```

### Current blocker

- Turn: `53908fad14c34ac3`
- Session: `15a89bc3b25ad1155f3359402c091660`
- Pending dialog: `26ea006e2a3f4635`
- Result:
  - `open_sites`: success
  - `login_sites`: success (`logged_in=true`)
  - `act_sites`: pauses for a resource allowlist approval

Do not approve/resume this dialog as a fix. It requests the random safeframe
host quoted above. The underlying browser session was closed after collecting
the evidence, so the next agent must start a fresh E2E rather than resume it.

The audit shows three goal-plan clicks reported as successful, followed by the
safe overlay dismissal. It does not record each resolved candidate name, plan
kind or URL transition, so it is not currently possible to reconstruct which
three controls were clicked from audit alone. This is an observability gap.

## Root cause in code

The remaining defect is in the resource-discovery policy, not in credentials,
OTP, Playwright availability or LLM availability.

Relevant functions:

- `runtime/playwright_sidecar/session_broker.py:2260`
  `_blocked_hosts_for_action`
- `runtime/playwright_sidecar/session_broker.py:2274`
  `_prepare_resource_expansion`
- `runtime/playwright_sidecar/session_broker.py:2322`
  `_prepare_action_with_resource_fallback`

Today `blocked_requests` is effectively:

```python
dict[host, set[resource_type]]
```

After `selector_missing`, every blocked `document`, `script`, `stylesheet`,
`xhr` or `fetch` host can become a resource-reload proposal. The broker does
not know whether a blocked document was:

- a top-level navigation required by the selected target;
- a first-party application frame;
- an unrelated third-party advertising iframe.

The random safeframe document therefore becomes indistinguishable from a
resource that could reveal the requested navigation control.

There is already one sound special case: `selector_ambiguous` expands only
stylesheet hosts. The overly broad path is the generic `selector_missing`
branch.

## Proposed general solution

### 1. Preserve resource provenance

Replace the host-to-types observation with bounded structured evidence, for
example:

```python
blocked_requests[host] = {
    "types": {"document"},
    "main_frame": False,
    "navigation": True,
    "top_host": "www.booking.com",
    "parent_host": "www.booking.com",
}
```

The route handler can derive this from the Playwright request/frame relation.
Keep only bounded booleans/hosts; do not store URLs with tokens or page data.

### 2. Classify relevance without vendor lists

For an implicit resource fallback, a host is relevant only if at least one of
these is true:

- it is the root credential domain or a subdomain of it;
- it is the current top-level host or a subdomain of the configured root;
- it is an explicitly approved credential origin;
- it is the exact destination host of a broker-resolved DOM target;
- it is the exact unique popup/top-level redirect host observed after a click.

The configured root (`entry["domain"]`) is sufficient for conservative
first-party matching (`host == root` or `host.endswith("." + root)`). No
Booking, Google or advertising denylist is required.

A cross-site subframe document must never become relevant merely because a
selector was missing. If all observed hosts are irrelevant, return no resource
expansion and continue to the existing bounded model fallback on the DOM that
is already loaded.

### 3. Keep explicit cross-site transitions gated

Do not weaken the allowlist boundary. A candidate link, form, popup or
top-level redirect to a new host must retain the exact one-shot gate already
implemented. The change applies only to *implicit* resource discovery with no
target-to-host causal evidence.

### 4. Improve goal-flow observability

For each prepared/executed goal step, audit safe metadata:

- `plan_kind` (`goal_navigation`, `goal_continuation`, etc.);
- normalized resolved role;
- bounded resolved accessible name, or a non-reversible label identifier;
- confidence and `model_selected`;
- scrubbed URL before/after;
- whether the action was pre-dispatch-blocked.

Do not audit form values, OTPs, credential fields or raw authenticated page
content. This will make repeated wrong navigation detectable without a
screenshot-only investigation.

### 5. Re-observe the current navigation logic

After filtering the safeframe, verify whether the goal state machine naturally
selects the account/profile reveal and then the bookings/trips entry. Do not
add a Booking-specific menu path. If it still fails, inspect the bounded
candidate list and fix the general goal-alias/reveal scoring.

## Required tests

Add these before another real E2E:

1. A blocked random cross-site subframe `document` does not produce a resource
   gate after `selector_missing`.
2. A first-party subdomain script/document can still produce an exact resource
   gate when it is plausibly needed.
3. An explicitly observed popup/top-level destination on a new host still
   produces a gate and excludes unrelated telemetry hosts.
4. Randomized safeframe-like hostnames never enter a persistent mandate solely
   through resource discovery (property test, no vendor string assertion).
5. A late generic modal appears after target selection; recovery dismisses it,
   re-enumerates the DOM and dispatches the target once.
6. Two equally strong OTP candidates remain `ambiguous`; form length/type may
   filter candidates but never select by trial.
7. Final real E2E criterion: no manual dialog, `login_sites logged_in=true`,
   `act_sites` completes, `read_sites` returns the bookings page/content, and
   all host sessions are closed afterward.

## Current implementation in the worktree

Relevant new or modified areas:

- `runtime/playwright_sidecar/factor_resolvers.py`
  - exact mailbox binding;
  - pre-submit UID cursor;
  - issuer correlation;
  - ranked syntax evidence;
  - OTP form constraints;
  - bounded polling and safe diagnostics.
- `runtime/playwright_sidecar/credential_injection.py:493`
  - internal email-factor integration and safe audit.
- `runtime/playwright_sidecar/session_broker.py:1796`
  - structural/ARIA safe overlay dismissal and bounded replan.
- `runtime/playwright_sidecar/action_resolver.py:78`
  - translated safe overlay exits.
- `runtime/detection_lexicon_seed.py:385`
  - `sites.overlay_dismiss_target` vocabulary.
- `runtime/mail_client.py`
  - exact account resolver and bounded IMAP I/O.
- `runtime/tests/test_factor_resolvers.py`
- `runtime/tests/test_sites_security.py`

Relevant tests currently pass:

```text
154 passed
```

The changed Python modules compile cleanly and the synthetic overlay browser
probe passed. A full repository test run and public export/check have **not**
been performed after the latest overlay changes.

## Constraints and warnings

- The repository worktree was already heavily dirty. Do not revert unrelated
  changes and do not infer authorship from the overall diff.
- Playwright is local to Metnos:
  - Python: `/home/roberto/.local/share/metnos/.venv/bin/python`
  - browsers: `/home/roberto/.local/share/metnos/playwright-browsers`
- Do not use the Suprastructure Python environment.
- Do not print or persist OTP values, mailbox contents or credential values.
- Do not hardcode Booking selectors, Booking labels, safeframe hosts or ad
  provider domains.
- Do not solve the current issue by raising the host cap or approving random
  third-party hosts.
- Preserve the drop-in executor contract: planner inputs/outputs must not need
  to know about the internal intelligent login/action procedures.

## Recommended next action

Implement resource provenance and relevance filtering first. Then run the
synthetic resource tests and one fresh Booking E2E. Only if the resource gate
is gone but goal navigation still fails should the next agent modify goal
candidate scoring.
