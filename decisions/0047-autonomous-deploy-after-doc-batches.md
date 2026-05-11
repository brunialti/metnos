---
id: 0047
title: Autonomous deploy after each batch of doc modifications
date: 2026-04-23
status: accepted
area: deploy
related:
  - 0032
  - 0033
---

## Context

The public site at `metnos.com` is built from `/opt/myclaw/docs/`
and deployed to Cloudflare Pages via `/opt/myclaw/deploy.sh`. The
script reads credentials from `~/.config/mykleos/deploy.env` (mode
600). On 23 April 2026 Roberto configured the credentials and said
*"in future you can do it directly"*. On 24 April the rule was
reinforced: *"always publish the modified text on Cloudflare"*.

Without an explicit rule the default failure mode is to leave
modifications local. The user does not see them; the work is
unfinished by their reading. Asking confirmation for each deploy is
friction that defeats the autonomy.

The decision was needed because the deploy is not a "may or may
not" step; it is the final stage of the doc-modification work. ADR
0032 (docs always aligned) expects code-level changes to be
followed by doc updates; ADR 0033 (sitemap) expects new pages to be
discoverable. Both depend on the deploy actually happening.

## Decision

Whenever a batch of modifications under `/opt/myclaw/docs/` is
complete, run `/opt/myclaw/deploy.sh` without asking for
confirmation. The work is not finished until the change is live.

**Operational rules:**
- After writing or modifying files in `/opt/myclaw/docs/`, when
  the batch is complete (content consolidated, links coherent,
  sitemap updated if pages were added/removed), run `./deploy.sh`.
- One deploy per batch, not one per file.
- Report the deploy URL in the closing message so Roberto can see
  immediately if anything went wrong.
- If the deploy fails for non-trivial reasons (permissions, missing
  env var, an unusual wrangler error), stop and report rather than
  retry blindly.
- The autonomy applies to `deploy.sh` of Metnos. Other deploy
  contexts in the future require separate authorization.

**The pairing with ADR 0032 (docs aligned) and ADR 0033 (sitemap
updated)** is direct: those rules describe what to update in the
docs; this rule describes that, when the updates are done, the
deploy follows automatically. The three together form the
end-to-end discipline of the public corpus.

## Alternatives considered

**Ask for confirmation before each deploy.** Pro: explicit human
checkpoint before publishing. Con: friction; the user has already
asked for the doc modification and expects the result to be visible;
the question becomes a ritual that the user answers "yes" without
attention. Rejected.

**Deploy on every file save (continuous).** Pro: maximum freshness.
Con: too many deploys for a single logical change; intermediate
states are visible publicly; deploy has cost (Cloudflare bandwidth,
build time). Rejected.

**Deploy only on explicit user request.** Pro: maximum control.
Con: forgetting to ask is the failure mode; modifications stack up
unpublished; the corpus drifts away from what the user thinks is
live. Rejected.

## Consequences

Doc-modification sessions close with a deploy and a URL in the
report. The discipline is operationally invisible — Roberto does
not have to remember to ask — and structurally important: the
canonical corpus and the live site stay in sync.

The credentials live in `~/.config/mykleos/deploy.env` (mode 600)
with `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID`. Token
rotation is non-urgent (home network, single user, low exposure)
and when it happens the script supports the rotation without any
in-chat token paste.

The boundary of the autonomy is precise. Modifications under
`docs/` trigger deploy. Modifications elsewhere (`runtime/`,
`executors/`, etc.) do *not* trigger deploy — they are code, the
deploy applies to the public-facing corpus only. If a code change
also requires a doc update (per ADR 0032), the doc update is part
of the same batch and the deploy follows.

When a deploy genuinely fails, the report points to the cause; the
discipline does not retry blindly. A few real-world failures —
missing env var, expired token, wrangler internal error — were
observed early and are now in the diagnostics path.
