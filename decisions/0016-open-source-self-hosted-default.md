---
id: 0016
title: Open-source self-hosted as the default for recurring capabilities
date: 2026-04-26
status: accepted
area: architecture
---

## Context

Several seed executors (ADR 0015) need backend services for their
operation: search, geocoding, OCR, TTS, STT, vector store, mail.
Each backend is a fork in the architecture: SaaS service, locally
hosted open-source stack, or in-process library. Without a default
preference, every executor would re-decide and the system would
accumulate diverse dependencies, varied billing models, and
inconsistent privacy postures.

Two telos pulled the decision toward self-hosting. The "lives in the
user's home" framing of Architettura chapter 1 says Metnos should not
need a permanent umbilical cord to a third-party SaaS. The
`t.parsimonia` telos asks for zero recurring cost where possible. A
counterargument exists for cases where reputation or scale make
self-hosting senseless (frontier LLMs, mail outbound deliverability),
and the rule has to acknowledge those.

The decision was needed on 26 April 2026 before any seed executor
backend was wired, and was informed by the inheritance of giorgio2
(`/opt/giorgio2/plugins/`), which already has the OSS pipeline for
most of these services and can be cannibalized rather than reinvented.

## Decision

For every recurring capability, prefer an open-source stack that
runs on `.33`. SaaS is reserved for fallback or for cases where
self-hosting does not make sense today.

Concrete defaults at the time of writing:

- **Search** → SearXNG self-hosted (running on `metnos-server:8888`,
  smoke-tested 26/4).
- **Maps and geo** → Nominatim + Overpass (OSM), self-hostable.
  Public instances allowed as fallback with "be polite" rate limit.
- **OCR** → Tesseract local.
- **TTS** → Piper local.
- **STT** → whisper.cpp local.
- **Vector store and embeddings** → in-process (SQLite + sqlite-vec,
  or FAISS/Chroma in-process).
- **Database** → SQLite local.
- **Local LLMs** → Ollama or llama.cpp for the `local-fast` and
  `local-middle` tiers (judge, classify, extract simple).

Accepted exceptions, kept explicit:

- **Frontier LLMs** → SaaS (Anthropic / OpenAI / Google / Mistral).
  Self-hosting frontier-class models requires hardware that is not
  reasonable for ordinary users today.
- **Mail outbound** → SaaS smarthost (Resend / Postmark / Migadu).
  Deliverability requires reputation; residential IPs fail.
- **Live third-party feeds** (news, weather, financial data) when no
  self-hostable alternative exists. Kept ad-hoc.

The operational shortcut: when picking a backend for a new executor,
look at giorgio2 first. If giorgio2 already has the OSS pipeline for
that capability (search, voice, OCR, satellite arbitration, IoT), the
default is to port it, not to reinvent.

## Alternatives considered

**SaaS-first by default.** Pro: zero ops, faster to wire. Con:
recurring cost, external dependency, third-party data exposure,
weakens the "lives-in-your-house" framing. The privacy posture of
Metnos depends on the user controlling the path of every byte;
SaaS-first is incompatible. Rejected.

**Library-only (in-process for everything).** Pro: simplest deploy.
Con: some backends (search, geocoding) need a curated index that is
unwieldy in-process; running a SearXNG container on `.33` is one
order of magnitude simpler than embedding an index. Rejected for
search and geo; partially adopted for vector store and database.

**Mix per executor with no preference.** Pro: maximum flexibility.
Con: drift in privacy, billing, and operational complexity; every
new executor re-asks the question; the corpus has to document
exceptions everywhere. Rejected.

## Consequences

A small number of containers run on `.33` (SearXNG already; Nominatim
+ Overpass + Whisper + Piper + Tesseract + Ollama queued for
implementation). The single Linux home server inherits a coherent
operational footprint.

Cost: zero recurring spend on the default path. Frontier-LLM and
mail-outbound costs are explicit and bounded; the user can audit
them in one place (the SaaS account dashboards) instead of in many.

The principle interacts with executor design. A self-hosted backend
typically returns slightly noisier results than a polished SaaS
(e.g. SearXNG vs Google Search API); the corresponding executor is
allowed to invest a little more in input/output normalization than a
SaaS-backed one would. The auto-enrichment built into `web_search`
(ADR 0015) is an example: SearXNG returns less context than Google,
so the executor compensates.

The decision is also the place where giorgio2 enters Metnos as a
pattern source. The reuse is documented in seed-executor manifests
(`web_search`, `voice_say`, satellite arbiter); the principle that
"compatible OSS pattern existing in giorgio2 is preferred to a fresh
write" is operational.
