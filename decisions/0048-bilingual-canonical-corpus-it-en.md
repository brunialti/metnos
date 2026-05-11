---
id: 0048
title: Canonical corpus in IT and EN, with separate roots and hreflang
date: 2026-04-22
status: accepted
area: documentation
related:
  - 0008
  - 0033
---

## Context

The Metnos corpus had grown through April 2026 in Italian. By 22
April the primary documents (10/10) were complete in IT —
`Dialogo`, `Glossario`, `QuickTour`, `Survival_Kit`,
`Architettura_Intro`, `Prospettive_Estese`, `Neuroni_Memoria`,
`Prospettive_Giudizio`, `Letteratura_Adattamenti`, plus the
landing — and a translation push had produced their EN
counterparts in the same window. The question of whether the
corpus would be IT-only, EN-only, or bilingual had to be decided
before the v1.1 microdesigns started landing.

Three factors aligned on bilingual. First, the *audience* of
Metnos is realistically not Italian-only — the site at
`metnos.com` is a public artifact, the project's vocabulary
borrows heavily from Greek and from network-medicine English,
and the technical reader of the future is mostly anglophone.
Second, the *cost* of translation is dramatically lower than it
used to be: ADR 0019's framing (single author plus increasingly
capable AI assistants) makes a bilingual corpus feasible where it
would not have been five years earlier. Third, the *authoring*
language remains Italian — the dialogues and the Architettura are
Italian-first, the rhythm of the prose lives in Italian — and
Italian-as-source plus EN-translation is healthier than
EN-as-source-with-IT-fallback because the source language is
where the meaning is decided.

## Decision

The canonical corpus is bilingual. Italian is the primary
authoring language; English is a translated mirror.

**Site structure.**
- Root `metnos.com/` → 302 redirect to `/en/` (English is the
  default language for first-time visitors).
- `/en/*.html` — English version.
- `/it/*.html` — Italian version (original, complete).
- Every page carries an `IT|EN` switcher at top right.
- `_redirects` in `/opt/myclaw/docs/_redirects` handles legacy
  URLs.
- `sitemap.xml` and `robots.txt` declare hreflang alternates (ADR
  0033 covers the sitemap discipline).

**Translation conventions.**

Not translated (kept for evocative value):
- `Metnos` (public name) and `myclaw` (technical name).
- `Vaglio` (gloss: "the sieve").
- `Telos` / `telos`.
- `Giornata` / `Giornate` (gloss: "Day/Days", Galilean).
- `kleos` / κλέος (kept as Greek lexicon, no longer the project
  root after rebrand of ADR 0006).
- `hybris` / ὕβρις.
- `L'Altro` → `The Other` (capitalized in both languages).

Translated:
- `assistente domotico` → `home agent`.
- `Costituzione` → `Constitution`.
- `Leggi` (the four Laws) → `Laws`.
- `contraddizione / discrepanza / divergenza` → `contradiction /
  discrepancy / divergence`.
- *"La Costituzione non giudica, la teleologia sì"* → *"The
  Constitution does not judge; teleology does"*.
- *"Metnos si giudica, non giudica"* → *"Metnos judges itself; it
  does not judge"*.

**Tone per document.**
- *Dialogue*: literary register, The Other as a robust
  interlocutor (no Simplicio strawman), Roberto crisp. Mythological
  prologue (Prometheus, Icarus, Golem of Prague) preserved intact.
- *Glossary*: concise, reference style, with an "(in Italian)"
  note on links to documents not yet translated.
- *Quick Tour*: "for dummies" tone, conversational. SVG mock content
  also translated (`Maggio` → `May`, etc.).
- *Architecture Intro*: technical precision preserved,
  language slightly softened from the original where the technical
  density would otherwise dominate the prose.

**Microdesigns.** Level 2 microdesigns
(`docs/it/architecture/*.html`) are kept in Italian until
translation is requested. The reasoning is that the technical
naming will use English names anyway (executor, mnest, mnestome),
so the prose around them carries less language-specific weight,
and on-demand translation matches actual reader need.

**Workflow per translation:**
1. Create the EN file under `/en/` with English-friendly anchors
   (`constitution` vs `costituzione`).
2. Update the EN landing (`/en/index.html`): drop the `<span
   class="lang-badge">IT</span>` and switch the link from
   `/it/Foo.html` to `Foo.html` (relative inside `/en/`).
3. Update the EN landing's `.secondary` box if relevant.
4. Update `sitemap.xml` with hreflang pairing for the new doc (ADR
   0033).
5. Cloudflare Pages deploy via `./deploy.sh` (ADR 0047).

## Alternatives considered

**English-only.** Pro: maximum reach. Con: the dialogues and the
Architettura are *authored* in Italian; the rhythm of the prose
lives there; translating the source language out kills the voice.
Rejected.

**Italian-only.** Pro: zero translation cost. Con: real audience
constraint; the public site at `metnos.com` becomes invisible to
the largest part of the technical web. Rejected.

**Bilingual but EN-as-primary.** Pro: aligns with the broader
technical web. Con: the authoring is Italian; forcing the source
to a translation-target language slows the original work and
removes the Italian voice from the canon. Rejected.

**Mixed (English landing, Italian deep content).** Pro: fast
onboarding, deep content in source language. Con: confusing UX
(you arrive in English, then the depth is in another language);
the language switch breaks the reading. Rejected.

## Consequences

Translation work happens in batches alongside doc-alignment
batches (ADR 0032). The 22 April push produced 10/10 primary
documents in EN; subsequent v1.1 microdesigns are translated as
they are written, with the EN version following the IT in the
same session whenever feasible.

The bilingual rule has secondary effects:
- Style guidelines (ADR 0036 on no real names, ADR 0038 on no
  greek letters) apply uniformly across languages.
- Provider-specific prompt hints (ADR 0027) are kept in their
  authoring language because the LLM handles both; the rule
  there is "do not duplicate maintenance".
- The corpus's own bilingual structure is a soft proof-of-concept
  for the multilingual UX of Metnos itself: when the system talks
  to the user in their language while preserving its core
  vocabulary (telos, mnest, vaglio), it follows the same pattern
  the corpus does.

A specific implication for new authoring sessions: the IT
document is written first; an EN draft follows in the same
session when content is stable. Translation is not a separate
backlog, it is the second half of the writing.

Open: the boundary between "deep microdesign that needs EN" and
"deep microdesign that stays IT-only" is empirical. As of late
April 2026, microdesigns under `/it/architecture/` translate on
demand; the v1.1 canonicals (executor, mnest, mnestome) are
bilingual from origin.
