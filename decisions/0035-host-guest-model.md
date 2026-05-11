---
id: 0035
title: Host + guest model — Metnos as personal assistant with trusted invitees
date: 2026-04-27
status: accepted
area: architecture
related:
  - 0036
---

## Context

Until 27 April 2026 Metnos had been a single-user system: Roberto
talks to it on Telegram, all data and decisions are his. The
question of whether other people in the household (family members,
trusted invitees) might also interact with the system was deferred
under "multi-user, phase 7". On the evening of 27 April the
question re-surfaced with a different framing: not multi-tenant
(equal-status users in a shared system) but *host plus guests*
(one personal assistant with a small number of trusted invitees).

The framing change is load-bearing. A multi-tenant design needs
data isolation, per-user mnestoma, per-user workspace, per-user
TELOS, sometimes encryption-by-actor. A host-plus-guests design is
much smaller: one mnestoma, one workspace, one TELOS, one host,
plus a handful of guests with their own channels and a per-actor
field where it matters.

Roberto's correction: actors are not equal-rank in this system.
Metnos is *Roberto's* personal assistant. Guests are invited; the
host is the gatekeeper of host-owned resources, but is not admin
over the guests on guests' personal matters. Each actor approves
on their own channel.

## Decision

A minimal layered model with one host (Roberto) and N guests.

**Layers:**

| Concept | Where it lives | Owner |
|---|---|---|
| SOUL, IDENTITY, Constitution, TELOS, USER, mnestoma, workspace | server, single | host |
| Audit log with `actor` field | server | system |
| Telegram pairing (channel + sender + autonomy) | server, one entry per actor | each actor for their own |
| `guest_profile` (rare, opt-in) | server | the guest |
| OAuth/API secrets | server table `(actor, key, ciphertext)`, default `actor='host'` | actor |
| Devices | server table, owner=host (for now) | host |

**Not present**: per-guest mnestoma, per-user workspace, duplicated
TELOS/USER, massive table-per-user layers.

**Approval rules** (who approves what):
- Guest action, in the guest's perimeter → the guest approves
  (within autonomy).
- Guest action, on a shared family resource → the guest approves
  (it is theirs too).
- Guest action, on a host resource (e.g. Roberto's mail) → the
  host approves (gatekeeper of the resource).
- Guest action, autonomy insufficient for the action → escalation
  to host.

The host is *not* admin over guests' personal traffic. Each actor
has their own channel, their own approvals, their own history. The
host sees aggregate audit, not the details of others' traffic
(except shared things).

**MVP — what is actually coded now:**
1. `actor: str` field everywhere relevant (internal requests,
   audit, approval, secrets). Default `"host"`.
2. Approval router — routes the message to the actor's own channel
   (if not host) or to host (if the resource is host-owned or
   escalation needed).
3. `secrets` table with `actor` column, default `host`.
4. `devices` table with `owner_user_id` always `host` for now
   (future: `accessible_to`).
5. Audit log schema extended with `actor` (default `host`).

Cost over single-user pure: ~30 minutes of scaffolding plus ~1 hour
for multi-channel approval router.

**Deferred** to "real multi-user" needs:
- Formalized `guest_profile` (created at first real need, e.g.
  guest's own mailbox).
- Mnestoma with restricted visibility ("private host entries"). For
  now the entire mnestoma is the host's.
- Elaborate per-user autonomy (today the pairing level suffices).
- Per-actor encryption of secrets (today single server-side
  encryption).
- Workspace split.
- Per-user namespaced paths.

**Director principle.** Start wide, narrow if needed. Today: soft
boundaries between host and guest, single mnestoma, single workspace,
trivial per-actor secrets. If a real need for stricter privacy /
formal isolation / stronger sandboxing emerges, narrow that piece —
do not re-architect the whole. Narrowing is targeted work; widening
after the fact is painful refactor. Therefore: KIS today + implicit
optionality for tomorrow (the `actor` field, the default-host
tables, the multi-channel approval router).

The pairing with ADR 0036 (no third-party real names in docs) is
direct: in code, dialogues, and examples the only proper noun is
"Roberto" plus generic terms (guest, ospite, actor, sender).

## Alternatives considered

**Full multi-tenant from day one.** Pro: clean foundation. Con:
massive over-engineering for a household of trusted people; per-
user mnestoma alone is weeks of work; the actual asymmetry between
host and guest is not respected by symmetry. Rejected.

**Single-user only.** Pro: simplest. Con: a real and recurring
question ("can my partner use Metnos?") gets answered "no" with no
extension path; future widening is the painful refactor the
director principle warns against. Rejected.

**Host as admin over guests.** Pro: traditional. Con: contradicts
"each actor approves on their own channel"; turns Metnos into a
surveillance instrument over the household; ethically off. Rejected.

## Consequences

The MVP scaffolding (~1.5 hours of work) gives Metnos the *shape*
of host-plus-guest while staying nearly as simple as single-user.
The shape is what enables all later widening to be local, not
global.

The decision interacts with ADR 0011 (client/server architecture):
remote executors are signed by the server and run on devices; the
device-owner relationship is host-only today, but the schema
(`owner_user_id`, future `accessible_to`) accommodates per-actor
device ownership when it becomes relevant.

Approval UX (ADR 0012, three-line card) extends naturally: the
card carries the requesting `actor` and is presented on that
actor's channel, not on the host's. The host sees aggregate audit;
the actor sees the action card.

Open: when a guest is invited for the first time (the first real
guest event), the formal `guest_profile` is created. The shape of
that profile (autonomy levels, default sandbox restrictions,
visibility into which mnestoma) is to be decided then, against a
real case rather than a speculative one.
