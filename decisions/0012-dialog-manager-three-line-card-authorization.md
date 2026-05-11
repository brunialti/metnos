---
id: 0012
title: Dialog manager — three-line card pattern for extended authorization
date: 2026-04-24
status: accepted
area: ux
related:
  - 0007
---

## Context

When Metnos asks the user to grant an *extended* authorization —
something that derogates from the sandbox: read or write outside the
workspace, run a shell with a new binary, reach a domain not in the
allowlist, pair a new device — the dialog manager has to make the
request intelligible without being technical and without being
verbose. The reference channel mentally is Telegram DM, but the
pattern must apply on any channel.

This is the front line where the second axis of safety (perimeter)
lives (ADR 0007). The asymmetric precaution there is real: shell or
network derogations are not a check-box ceremony; they enlarge the
attack surface. But the ceremony cannot be heavy enough that the user
clicks meccanicamente. The trap is the click reflex — three "OK" in
a row train the brain to "OK" anything that looks like a question.

The decision was needed on 24 April 2026 to give the rewrite of
`approval_ux.html` (one of the 22 UNTRUSTED docs) a concrete pattern
to land on, and to inform the dialog manager module that will be
coded in phase 2.

## Decision

Three principles, applied to every extended-authorization dialog.

**The "three-line card" as default.** Always the same structure:
- one line of *action* in natural language ("I would like to read
  ~/Immagini/");
- one line of *concrete numerical impact* ("1,247 files, ~3 GB; no
  shell, no network");
- one line of *reversibility* ("revocable from DM at any time; I keep
  the 'before' for 24h").

Plus a `[Why?]` button that opens the technical card for whoever
wants depth. The default is never technical.

**Modulation by recurrence.** First time it is verbose; subsequent
times it compresses.
- From the second identical authorization onward: a one-line prompt
  ("as usual?").
- After N consecutive positive authorizations: the permission is
  promoted, Metnos stops asking. Explicit revocation always
  available.
- The "bother budget" of the Architettura chapter 5 governs frequency
  and decay.

**Visual sign for "territory concessions".** Sandbox derogations have
a distinct icon and color from ordinary execution confirmations
(unlocked padlock vs green check). A user must see at a glance
whether they are granting a new perimeter or merely confirming a
routine action. Mixing the two visuals is the fastest path to a
mechanical click on something that deserved attention.

Canonical example (Telegram, first request):

```
photo_organize would like to read ~/Immagini/

   1,247 files, ~3 GB · no shell · no network
   I can revoke from DM at any time
   Reversible for 24 hours (I keep the "before")

   [ Once ]  [ Always ]  [ No ]  [ Why? ]
```

The "Why?" card shows the executor name, the declared profile, the
forbidden paths of the core that remain forbidden anyway, the audit
trail, and the "first time or N-th". That detail level exists; it is
not read by whoever does not want to.

## Alternatives considered

**One uniform confirmation for any action.** Pro: simplest mental
model. Con: trains the click reflex; defeats the second axis of
safety. The whole point of the perimeter axis is that it modulates;
a uniform UX undoes the modulation. Rejected.

**Inline TOML manifest dump in the prompt.** Pro: full transparency.
Con: nobody reads a TOML manifest on Telegram; the prompt becomes
unreadable; the user clicks through anyway, but now without the
illusion of comprehension. Rejected.

**Five-line card with rationale included.** Pro: more context.
Con: scrolls past the Telegram fold; the third and fourth lines get
ignored; the "always" button looks scary because it carries too much
text near it. The three lines (action / impact / reversibility) are
the load-bearing minimum. Rejected.

**No territory-concession sign, color is enough.** Pro: less visual
noise. Con: color-blind users; small screens; long DM thread where
color blends with surrounding messages. The icon + color combination
is what registers in peripheral vision. Rejected.

## Consequences

When `approval_ux.html` is rewritten (one of the 22 UNTRUSTED
microdesigns to be promoted to v1.1), the three-line card becomes
canonical, aligned with `executor.html` v1.1 and Architettura chapter
5 (axis 2). When the dialog manager / interaction module is coded
(phase 2 of the roadmap, chapter 17 of Architettura), the three-line
card is a reusable component parameterized by: action, requested
profile vs current profile, numerical count (files, bytes, estimated
duration), reversibility.

The pattern reaches further than approval. The same three-line
structure is reused for device pairing (ADR 0011) and DM-pairing of
senders (chapter 12 of Architettura): action + impact + reversibility,
same shape.

Three open questions remain for `approval_ux.html`:

1. The exact list of dimensions that activate the verbose card. A path
   outside `workspace/` triggers it; a new network domain triggers it;
   a new shell binary triggers it. A scheduled cron running the same
   thing every day does not. The boundary needs a definitive list.
2. The promotion threshold N (after N "yes" in a row, Metnos stops
   asking). Likely different per risk category.
3. The mechanics of the revoke command from DM, and what happens to
   mnests that depended on the revoked permission.
