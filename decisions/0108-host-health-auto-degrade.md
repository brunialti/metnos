---
id: 0108
title: Auto-degrade T2→T1 su risposta 429/503 ripetute (host_health)
date: 2026-05-07
status: accepted
area: runtime, executor, crawler, robustness
related:
  - 0081  # web crawler multi-tier
  - 0098  # web crawl parallel + strategy
  - 0103  # round 2 perf executor
---

## Context

Policy 7/5/2026 (vedi `find_urls`): tier default T2 (8 conn/host, 200ms
floor). Se un host risponde 429 o 503 ripetuti, il crawler attuale lo
ricicla all'infinito perche' nessun feedback loop converte le risposte
di throttling in degrado di tier. Risultato: log pieni di errori, host
infastidito, utente non aiutato.

## Decision

Tracker per-host degli status 429/503 in sliding-window 60 min. Soglia
3 eventi → host aggiunto automaticamente a `~/.config/metnos/blocked_origins.json`
con TTL 24h. Trascorse le 24h, `is_blocked()` cleanup lazy lo rimuove e
torna a T2.

`find_urls._resolve_tier` consulta gia' `blocked_origins.json` — il
nostro lavoro e' SOLO popolarlo automaticamente quando giusto.

## Implementation

`runtime/host_health.py` (~165 LOC):

  - `record_response(host, code)` — append a sliding-window. 200 fa
    solo prune (no contatore).
  - `_count_errors(host)` — eventi nella window.
  - `maybe_block_host(host)` — se >= 3 eventi → add a
    blocked_origins.json + ttl 24h. Idempotente.
  - `is_blocked(host)` — consulta blocked + cleanup lazy.
  - `cleanup_expired()` — rimuove TTL scaduti.

Storage:

  - `~/.local/share/metnos/host_health.json` — eventi (volatile).
    Schema: `{"hosts": {<host>: {"events": [{"ts":float,"code":int}]}}}`.
  - `~/.config/metnos/blocked_origins.json` — esteso con campo `ttl`:
    `{"hosts": [...], "ttl": {<host>: <expire_epoch>}}`.
    Backwards-compatible: hosts senza voce in `ttl` restano permanenti.

Wire in `executors/read_urls_html/read_urls_html.py::_fetch_one`:

```python
except urllib.error.HTTPError as e:
    if e.code in (429, 503):
        record_response(host, e.code)
        maybe_block_host(host)
    return None, {...}
```

E specularmente in `executors/find_urls/find_urls.py::_fetch_html`.

Determinismo §7.9: solo conteggi, lock thread-safe, atomic write.

## i18n

Notice user-facing nel runtime ("host bloccato per 24h dopo 429
ripetuti") TBD: la chiave i18n verra' aggiunta quando l'orchestratore
HTTP renderer lo mostrera'. Oggi il blocco e' silente lato utente; il
crawler torna semplicemente in T1 (rate piu' polite).

## Consequences

  + Convergenza automatica verso comportamento polite con host stressati.
  + Recupero automatico dopo 24h (TTL expire), zero intervento manuale.
  + Zero leak di credenziali / dati: tutto locale al `.33`.
  - Possibile falso positivo su host che 503-eggia per cause transient
    (deploy in corso): 24h e' lungo; se serve, l'utente cancella manualmente
    blocked_origins.json o chiama `cleanup_expired()`.

## Test plan

`runtime/tests/test_host_health.py` (9 test): under-threshold no block,
threshold triggers, 503 conta, 200 non conta, TTL expire 24h,
cleanup_expired, idempotente, window prune, manual-listed host
permanente. Tutti pass.
