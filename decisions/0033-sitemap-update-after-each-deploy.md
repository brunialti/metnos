---
id: 0033
title: Update the sitemap after each deploy that adds or removes pages
date: 2026-04-27
status: accepted
area: deploy
related:
  - 0032
  - 0047
---

## Context

The site at `metnos.com` had a sitemap (`docs/sitemap.xml`)
created when the corpus stabilized at five canonical pages. By the
evening of 27 April 2026 the canonical microdesign count had grown
to ten v1.1 documents (with EN translations entering progressively),
the v1.0 docs were marked UNTRUSTED but still publicly reachable
(ADR 0008), and several new documents had been added. The sitemap
had not kept up.

The cost of stale sitemaps is silent: search engines and crawlers
follow the sitemap to discover what pages exist. A page not in the
sitemap (and not linked from a sitemap-discoverable page) is hard
to index. New canonical microdesigns and new English translations
were therefore quietly invisible to the broader web.

The discipline of doc-alignment (ADR 0032) covered the *content* of
the docs but not the *enumeration* in the sitemap. A separate rule
makes the responsibility explicit.

## Decision

After every deploy via `./deploy.sh` that adds or removes canonical
pages, the sitemap is updated in the same deploy.

**Operationally, three steps:**

1. Verify the existing sitemap includes all v1.1 canonicals (IT)
   plus their EN counterparts as they become available.
2. Long-term, add a `update_sitemap` step inside `deploy.sh` that
   enumerates the HTML files published under `docs/` and writes
   the sitemap entries (loc + lastmod + alternate hreflang).
3. The `lastmod` is the last-modification time of each file
   (`stat`), in W3C format (`YYYY-MM-DD`).

**Hreflang alternates.** Every IT page links its EN counterpart if
one exists, and vice-versa. This appears in both the page-level
meta tag (`<link rel="alternate">`, already present) and in the
sitemap. The two have to agree.

**Robots.txt.** If a `robots.txt` is in place, it points to the
updated sitemap.

**The rule applies whenever** canonical pages enter or leave the
public site. Adding a v1.1 microdesign (`vaglio.html`,
`channel.html`, `pairing.html`, `observability.html`) requires a
sitemap entry. Removing a v1.0 document (rare) requires removing
the entry. Promoting a v1.0 document to v1.1 typically does not
add or remove an entry, but the `lastmod` updates.

This sits alongside ADR 0032 (docs always aligned) and ADR 0047
(autonomous deploy). The three together form the operational
discipline of the public site: when content changes, both the
internal corpus and the external discoverability follow.

## Alternatives considered

**Auto-generate the sitemap on every deploy** without the canonical
check. Pro: zero manual work. Con: the auto-generation today is not
implemented; until it is, an explicit verification step is the
discipline. Adopted as the *direction* — the rule says
"systematize via deploy.sh when possible".

**Update the sitemap only at major releases.** Pro: less per-deploy
overhead. Con: between releases the new pages are quietly invisible
to crawlers; the cost is a slow degradation of search visibility.
Rejected.

**Trust crawlers to discover via internal links.** Pro: zero
sitemap maintenance. Con: discovery is slower and incomplete;
hreflang alternates do not auto-discover; the sitemap is the
canonical place where discovery is asserted, not inferred. Rejected.

## Consequences

For each deploy Claude runs, the diff is checked: did any HTML files
appear or disappear under `docs/`? If yes, sitemap update before
the deploy. The cost is small (a few minutes when manual, zero
once automated).

The `update_sitemap` automation is a queued task. When implemented,
it will:
- enumerate `docs/it/*.html`, `docs/en/*.html`,
  `docs/it/architecture/*.html`, `docs/en/architecture/*.html`;
- attach `lastmod` from `stat`;
- pair each IT page with its EN counterpart for `xhtml:link
  rel="alternate" hreflang="..."`;
- emit the W3C-formatted XML.

After implementation the rule becomes a side effect of `./deploy.sh`,
not a separate step.

A specific subset that needs explicit handling: the UNTRUSTED v1.0
documents (ADR 0008) carry `noindex, follow` and stay reachable
internally. They appear in the sitemap with the correct `lastmod`
but the `noindex` keeps them out of search results. This is the
correct behavior — they exist as traceability, not as content.
