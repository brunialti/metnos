---
id: 0105
title: HTTP cache disk-based per read_urls_html
date: 2026-05-07
status: accepted
area: runtime, executor, performance
related:
  - 0081  # web crawler multi-tier
  - 0082  # credenziali Fernet (ispira il pattern atomic write)
  - 0103  # round 2 perf executor
---

## Context

`read_urls_html` viene invocato spesso piu' volte sullo stesso URL durante
un turno (iframe-follow, fallback per pagine rotte) e attraverso turni
consecutivi (utente che riformula la stessa richiesta entro qualche minuto).
Ogni invocazione ricicla l'opener ma rifà l'intero round trip HTTP, anche
se il body sarebbe identico. Per pagine institutionali stabili
(scuola, news regionali, sito comunale) il costo di rete e il rischio di
saturare host esterni e' eccessivo.

## Decision

Aggiungere una cache disk-based locale, isolata per processo, riusabile
fra turni. **Il livello giusto e' il fetch del body**: cattura tutti i
casi di re-fetch (sia parallel intra-turn sia cross-turn).

Storage:

```
~/.cache/metnos/http/<sha256(url)[:2]>/<sha256(url)>.json
```

Sharding sui primi 2 hex char ~ 256 sotto-directory: evita una singola
directory gigante quando la cache cresce.

Cache key = `sha256(canonical_url)` dove canonical normalizza:

  - lowercase netloc;
  - strip default port (80/443);
  - strip fragment.

(Query string preservata: pagine listing/filter dipendono da `?page=N`.)

Cache value JSON:

```json
{
  "url": "<canonical>",
  "ts": <epoch_seconds>,
  "ctype": "text/html; charset=utf-8",
  "body_b64": "<base64 of decompressed body>",
  "headers": { ... }
}
```

TTL default 900s (15 min), configurabile via:

  - env `METNOS_HTTP_CACHE_TTL_S` (process-level);
  - arg `cache_ttl_s` in `invoke()` (per-call).

`cache_ttl_s=0` disabilita: get/put diventano no-op. Utile per debug e
per scenari dove la freschezza e' critica (es. monitoring real-time).

## Implementation

`runtime/http_cache.py` (~110 LOC):

  - `HttpCache(ttl_s=DEFAULT_TTL_S)` — istanza per-call.
  - `.get(url) -> dict | None` — None se miss/scaduto/disabled.
  - `.put(url, ctype, body, headers)` — atomic write via tmp+rename.
  - `.clear_older_than(seconds)` — cleanup batch.
  - `cleanup_weekly()` — entry point per scheduler.

In `executors/read_urls_html/read_urls_html.py`:

  1. `_fetch_one()` accetta `cache=Optional[HttpCache]`.
  2. Pre-fetch lookup: cache hit valido salta intero blocco HTTP/throttle.
  3. Post-success: scrive body decompresso (text-decoded e' meno fragile
     fra varianti gzip/deflate/brotli). Errori di scrittura no-op (cache
     fallback-safe per costruzione).
  4. `invoke()` legge `cache_ttl_s` da args, costruisce cache.

Cache cleanup: weekly via `runtime/recurring_tasks.py` (TBD: hook al
`cleanup_weekly()`). Soglia hardcoded 7 giorni.

Determinismo §7.9: zero LLM, hashing + I/O atomico.

## Consequences

  + Pagine ripetute zero costo di rete (latency 1-2 ms vs 200-2000 ms).
  + Riduce 429/503 dai server esterni nei dialoghi multi-turn.
  + Consente offline semi-graceful (read recente in cache risponde anche
    se la connessione cade).
  - Spazio disco: stima 50 MB per 1000 pagine medie (pre-cleanup).
  - Cache stale: 15 min e' compromesso, non e' giusto per breaking news.

## Test plan

`tests/runtime/http/test_http_cache.py` (10 test): canonical url, sharding,
TTL=0 disable, expired, atomic write, cleanup_older_than, key
consistency. Tutti passano deterministicamente.

`tests/runtime/executors/test_read_urls_html.py` esistenti: 11/11 passano (no
regressioni — il path cache e' opt-in, default ttl_s=900 ma vuoto al
primo run).
