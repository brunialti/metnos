---
id: 0032
title: Architettura and microdesign always aligned with code, no doc backlog
date: 2026-04-27
status: accepted
area: process
related:
  - 0022
  - 0033
---

## Context

On 27 April 2026 Roberto noticed that chapter 17 ("Roadmap &
approfondimenti") of the Architettura Intro was two days behind
the actual phase progress. The drift was small individually — a
few numbers, a banner date, a missing card — but the cumulative
effect was that anybody reading the public site got a stale picture
of what existed.

The Architettura documents (`Metnos_Architettura_Intro_v1.html` IT
+ EN) and the canonical microdesigns (`docs/it/architecture/*.html`
+ corresponding EN) are the project's normative reference. They are
linked from the microprogettazione index and from the public site
metnos.com. Each disalignment is visible: numbers stale, banners
dated last week, "to be rewritten" labels on documents that were
already rewritten.

The choice was either to accept doc drift as inevitable (and post
periodic catch-up batches) or to fold doc updates into the same
session that produces the code change. The latter is more
expensive per session but avoids the compounding cost of catch-ups.

## Decision

The Architettura and the canonical microdesigns are kept aligned
with the code in real time. There is no "docs to update later"
backlog.

**Six concrete update points** when a phase closes:

1. Update chapter 17 "Roadmap & approfondimenti" of Architettura
   (IT + EN).
2. Update the "Aggiornamento post-POC" banner (date + what landed).
3. Update the "Continua a leggere" cards if the canonical count
   changed.
4. Update meta description / og: / twitter: of the
   microprogettazione index.
5. Update the "v1.1 — canonici" table of the microprogettazione
   index.
6. Update the phase-bar of the microprogettazione index plus the
   status banners and callouts of the touched canonical
   documents.

**When promoting a doc from v1.0 to v1.1** (one of the 22 UNTRUSTED
docs becoming canonical, see ADR 0008): update the index to link
it, update stale links in other canonicals that said "X.html to be
rewritten".

**When adding a capability to the code** (synt-on-the-fly, Telegram
daemon, real Vaglio): update the banner of the related canonical
doc, add a concrete section, update the session callout in the
index.

**When numerical counts change** (number of canonicals, number of
seeds, number of tests): no stale numbers anywhere; verify with
grep across the docs.

**Bilingual rule.** When modifying the IT version of a doc that has
an EN counterpart, modify EN in the same pass. The IT↔EN
correspondences: chapter 17 IT ↔ ch.17 EN; roadmap tables IT ↔ EN;
canonical index IT only for now. If EN does not exist for a doc,
that's not a problem; if it exists, it is aligned in the same
session.

**Deploy discipline.** After every doc-modification batch, run
`./deploy.sh` (ADR 0047). The work is not done until the change is
live.

**When NOT to apply.** Documents declared "untrusted/v1.0 obsolete"
(red banner, ADR 0008) stay frozen as traceability. They are not
updated; they are *promoted* (rewritten) to v1.1 when the
corresponding code exists.

## Alternatives considered

**Periodic batch catch-up of docs.** Pro: less per-session friction.
Con: by the time the catch-up runs, the batch is large, the cost is
high, and many small drifts have already misled readers. Rejected.

**Auto-generated docs from code.** Pro: by construction aligned.
Con: the canonical microdesigns are not API references; they carry
discursive prose, design rationale, choice histories, philosophical
framing. None of that is generatable from code. Hybrid possible for
parts (manifest tables, executor counts), prose stays human-authored.
Rejected as a sole strategy.

**Live doc-only branches kept apart from code.** Pro: separation
of concerns. Con: the separation creates the drift the rule is meant
to prevent. Rejected.

**Tolerate the drift; readers are sophisticated.** Pro: zero
process. Con: not all readers are; the public site is a public
artifact; the project's voice depends on its corpus being accurate.
Rejected.

## Consequences

Sessions become slightly longer. A POC cycle that closes is followed
by a doc-update batch in the same session, before the deploy. The
overhead is on the order of 30 minutes per phase closure, which is
much less than the same updates would cost when accumulated.

The discipline interacts with ADR 0022 (POC validates microdesign):
the validation point is the natural moment for the doc update. The
two rules together produce a tight loop — code stabilizes,
microdesign promotes to APPROVED, related Architettura sections
update, deploy.

Companion ADR 0033 covers the sitemap as a special case of this
rule: when canonical pages are added or removed, the sitemap is
kept aligned in the same deploy.

The bilingual constraint (ADR 0048) inherits from this rule: an EN
document that exists is aligned with its IT counterpart. The
operational consequence is that translation work happens in batches
*as part of* the same alignment sessions, not as a separate
"translation backlog".

A specific anti-pattern to refuse: marking a doc with "TODO: update
when phase X closes". The right move is to update the doc when
phase X closes, in the same session.
