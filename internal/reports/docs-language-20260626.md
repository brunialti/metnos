# Riscrittura lingua + fix fattuali doc metnos.com — notte 2026-06-26

Agente notturno autonomo. Solo `docs/` toccato. Nessun commit/merge/push/restart/deploy
(il deploy lo fa lo script dopo questo turno). Fonte mandato:
`project_docs_language_rewrite.md`.

## Doc completati (IT + EN, validati)

### 1. agent_runtime.html (IT + EN) — passata completa
- Tutti i `Qwen 3.6 35B-A3B` di prosa, tabella tier, label SVG e callout → «il tier wise» /
  «modello locale» (tenuti i `model="...gguf"` dentro `<code>` e la lista generica
  «Qwen 3, Llama 3.1, Mistral»).
- SVG aria-label «Engine v2 cascade/cascata» → «il motore» / «Engine cascade».
- Anglicismi: smart truncation→troncamento intelligente; latest-wins→«vince l'ultimo»;
  champion/challenger→campione/sfidante (IT); «tre layer»→«tre livelli».

### 2. praxis_engine.html (IT + EN) — leak modello
- ASCII diagram `(Qwen 35B-A3B)` → `(tier wise)` / `(wise tier)`.
- Bench table `Qwen 3.6 35B-A3B locale` → `modello locale (wise)` / `local model (wise)`.

### 3. scratchpad.html (IT) — passata completa (EN era già accettabile, solo verifica leak)
- Termini flaggati: smart truncation→troncamento intelligente; gibberish→incomprensibile;
  garbage collection→pulizia automatica.
- Passata lingua piena della prosa: observation→osservazione (solo prosa, NON pseudocodice/
  JSON/SQL), context→contesto, history→cronologia, summary→riassunto, mode→modalità,
  offload→scaricamento, storage→archiviazione, lookup→ricerca (SVG), bytes→byte.

### 4. lifecycle.html (IT) — fix fattuale + passata lingua (EN era già corretto, usato come modello)
- 🐞 Fix fattuale: «4 sorgenti» → **6 sorgenti** (heading cap.3 + TOC).
- Tabella sorgenti completata con `multi_tool_paths (L2) → materialize_pipeline` e
  `canonical_query_log (L1) → cache_pattern` (allineata all'EN); aggiunta riga punteggio
  multi_tool/canonical; intro «sorgenti storiche non eliminate».
- Stale: `/admin/proposals` → `/admin/changes`.
- Anglicismi: cross-source→fra sorgenti, user-facing→rivolto all'utente, hub→centro di
  raccolta, escape hatch→via di fuga, short-circuit→scorciatoia, grace period→periodo di
  grazia, rollback(prosa)→ripristino, retry→nuovo tentativo, Fastpath promotion→Promozione
  fastpath, boost→incremento, matchare→far corrispondere, dashboard→cruscotto,
  end-to-end→dall'inizio alla fine.

## Bonifica leak-modello su doc NON ancora language-passati

Fix di policy surgicali «MAI Qwen in prosa» (stessa natura dei fix fattuali sparsi già
applicati nelle notti precedenti), IT + EN salvo nota:
- **skills_backends**: «modello locale medio (Qwen)» → «modello locale, tier middle».
- **vaglio**: cap. tradeoff, «Qwen 3.6 35B-A3B locale, tier middle» → «modello locale, tier middle».
- **multilang**: cap. quality flag, «wise è il default (Qwen…)» → «(modello locale…)».
- **telos**: Giudice teleologico «LLM Qwen 3.6 35B-A3B locale» → «LLM locale, tier wise» +
  bench convergenza «con Qwen…» → «con il modello locale»; boost→incremento.
- **index/architecture**: intro orchestrator, «LLM locale Qwen 3.6 35B-A3B» → «LLM locale, tier wise».
- **mnest**: 2 esempi JSON `"mnest_01HW..." // ULID` → `"mn_a1b2c3d4e5f6..." // mn_ + token esadecimale`
  (fix fattuale: l'id reale è `mn_`+token_hex, non ULID).
- **mnestoma / mnestome**: esempio CLI `history mnest_01HW...` → `mn_a1b2c3d4e5f6...`.

Questi doc RESTANO in DA FARE per la passata LINGUA completa (è fatta solo la parte leak).

## Validazione (numeri reali)

- HTML well-formed (`html.parser`): **ok** su tutti i file toccati —
  agent_runtime IT+EN, praxis_engine IT+EN, scratchpad IT, lifecycle IT, skills_backends
  IT+EN, vaglio IT+EN, multilang IT+EN, telos IT+EN, index IT+EN, mnest IT+EN, mnestoma IT,
  mnestome EN.
- grep `Qwen|Gemma|Engine v[0-9]|mnest_01HW` su `docs/**/architecture/*.html`: i soli match
  residui sono **leciti**:
  - `agent_runtime` IT+EN: `model="...gguf"` dentro `<code>` (esempi config TOML) + lista
    generica «Qwen 3, Llama 3.1, Mistral».
  - `virtualization.html` IT+EN: nomi modello (Qwen / Qwen3-VL) in keywords/SVG/tabella/prosa
    — INTENZIONALI, doc già FATTO/approvato (è il doc del facade modelli). Non toccato.

## Cosa resta (DA FARE)

- **Passata LINGUA completa** sui restanti architecture/ (model-leak già bonificato):
  mnest, mnestoma/mnestome, vaglio, policy, sandbox, channel, pairing, approval_ux,
  http_api, observability, telos, multilang, executor, skill_importer, skills_backends.
  Peggiori per anglicismi: telos(cap14), http_api(cap2/6), vaglio, sandbox, policy.
- **Libretti tecnici**: QuickTour, Architettura_Intro, Glossario, code.html — passata lingua
  (fattuali già a posto).

La lista DA FARE NON è vuota → sentinella `~/.metnos/docs-language.done` **non creata**:
il cron deve riprendere la notte successiva.
