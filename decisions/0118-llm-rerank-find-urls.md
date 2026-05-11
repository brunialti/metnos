---
id: 0118
title: LLM re-rank generale per find_urls (multilingua, topic-agnostic, no hardcoded)
date: 2026-05-09
status: accepted
area: web-crawl, planner, llm
related:
  - 0098  # web crawl parallel + strategy
  - 0101  # crawler error_class + soft-fail
  - 0108  # auto-degrade T2→T1 host_health
  - 0115  # SearXNG seed-discoverer
---

## Context

Turn live 9/5/2026 16:24 (`8421f108`): query «cerca su web il file
sull'organico di diritto della scuola nella provincia di Roma» →
crawler 274s con 3 `find_urls` + `describe_entries`, **risposta finale
onesta ma vuota**: «Non sono riuscito a trovare il file specifico».

Diagnosi: SearXNG (top-5 da Bing/Google/DDG via meta-search) e BM25
sui titoli/snippet hanno bassa probabilita' di centrare PDF
istituzionali profondi quando il caller non fornisce URL seed. Il
problema e' generale: vale per qualsiasi query con jargon di settore
(istituzionale italiano, scientifico inglese, normativo francese,
finanziario tedesco). Le contromisure ad-hoc (liste di domini per
topic, query rewrite con `site:` filter, dizionari di sinonimi)
funzionano ma sono per-lingua/per-paese/per-dominio: violano §7.3
(soluzioni generali, mai hardcoded) e moltiplicano la manutenzione.

## Decision

**LLM re-rank su candidate set ampio.** Pipeline (lingua/dominio/topic-agnostic):

1. SearXNG fetcha **wide-N** candidati (default 30) — mantiene il
   pattern §7.9 (HTTP GET + JSON, niente LLM nel motore stesso).
2. Per ogni candidato: `(url, title, snippet)` — gia' presenti nella
   risposta SearXNG, no fetch aggiuntivo.
3. LLM tier middle (Gemma 4 26B locale via :8080) riceve `{user_query,
   candidates: [...]}` + system prompt `runtime/prompts/<lang>/web_rerank.j2`
   → output JSON `{"top": [{"url", "score"}]}` con score 0-1.
4. `find_urls` tronca a top_n (default 5) e usa quegli URL come seed
   per il BFS.

Lingua determinata da `config.DEFAULT_LANG` (i18n.sqlite latest-wins,
ADR 0092). Prompt template MiniJinja, due varianti `it`/`en` di
~30 righe ciascuna, scala di score chiarita (1.0 match perfetto, 0.6
buono, 0.3 cutoff, 0.0 irrilevante) + 5 considerazioni implicite
(authority TLD .gov/.edu/.gov.<cc>, formato file da URL extension,
data esplicita, lingua del sito vs query, news vs wiki).

**General-purpose**: niente regex per-paese, niente liste hardcoded di
domini, niente dizionari di sinonimi. Il LLM ha multilingua nativo e
sa riconoscere autorita' istituzionale dal contesto (`.gov.it ≈ .gov`,
`bund.de`, `gov.uk`, `gouv.fr`, ente_<cc>.<tld>).

## Implementation

`executors/find_urls/find_urls.py`:
- `_searxng_search_full(query, top_n, ...)`: ritorna `[{url, title, snippet}]`
  invece di soli URL. Wrapper `_searxng_search` retro-compatibile per i
  test legacy.
- `_llm_rerank_candidates(user_query, candidates, top_k)`: nucleo del
  re-rank. Lazy import `prompt_loader` + `llm_helpers.call_llm` +
  `config.DEFAULT_LANG`. Robusto a code-fences ```json```, JSON
  malformato, top vuoto, URL allucinati. Fallback graceful: in qualsiasi
  errore ritorna i candidati nell'ordine originale + `meta.used=False
  + reason`. Nessun silent failure (§2.8) — il chiamante vede `meta`
  e puo' loggare.
- `invoke()`: nuovo flusso con env var `METNOS_FIND_URLS_RERANK`
  (default ON, set "0" per disable) + `METNOS_FIND_URLS_RERANK_WIDE`
  (default 30). Quando attivo + SearXNG ritorna >top_n candidati,
  invoca `_llm_rerank_candidates` e usa l'ordine LLM. `search_meta.rerank`
  esposto in output per audit.

`runtime/prompts/{it,en}/web_rerank.j2`: nuovi 2 file, ~30 righe ciascuno.
Pattern §6 (DEVI/NON DEVI/OK/ERRORE) + scala score esplicita.

`runtime/tests/test_find_urls_llm_rerank.py`: 8 test (empty, single,
LLM failure, JSON invalid, valid reorder, unknown URL skipped, empty
top, code fence stripped).

## Consequences

Pro:
- Quality jump misurato su query istituzionale Roma reale: top-5
  risale `usrlazio.it/organico-roma-2024.pdf` + `istruzione.it/...pdf`
  + `gazzettaufficiale.it/...` invece di blog/forum/news periferici.
- Lingua-agnostic: una sola implementazione copre IT/EN/FR/DE/ES/...
- Topic-agnostic: nessuna ontologia da mantenere; il LLM giudica in
  context dalla terna `(query, title, snippet)`.
- Domain-agnostic: niente liste di siti "trusted" hardcoded.
- Testabile: 8 test mock-LLM verdi, fallback graceful coperto.

Contro:
- Latency +5-8s su query con search_query (singola call LLM
  ~900 token in / ~220 token out a Gemma 4 26B locale). Trascurabile
  vs il crawl BFS che gia' costava 30s+ per query non triviale.
- 30 candidati invece di 5 → ~6x carico SearXNG (ma SearXNG locale e
  meta-engine pubblici sono economici).
- Costo aggiuntivo zero in API esterne (Gemma locale, §10.3).

Trade-off accettato: +5s latency in cambio di top-5 utilizzabili
invece di top-5 spazzatura.

## Disabled

`METNOS_FIND_URLS_RERANK=0` ripristina il comportamento pre-ADR0118
(BM25-only su titoli/snippet). Utile per test isolati e
benchmark comparative.

## Status

`accepted` 2026-05-09. Suite 1108 PASS / 0 FAIL / 1 skip dopo merge.
Wired di default in produzione.
