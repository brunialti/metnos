# Report — qualità/velocità ricerca web (SearXNG) in `find_urls`

> Tipo: report di analisi + change-request (per Metnos)
> Data: 2026-06-04 · Autore: sessione suprastructure (analisi su richiesta utente)
> Scope codice: `executors/find_urls/find_urls.py` — ADR 0115 (seed-discoverer), ADR 0118 (LLM rerank), ADR 0108 (blocked_origins), §7.9 (language=all), §10.3 (SearXNG self-hosted)
> Scope infra: istanza SearXNG `http://localhost:8888` (`/etc/searxng/settings.yml`)

---

## TL;DR

L'utente non è soddisfatto della ricerca web locale. Ho analizzato il codice e la config reali. **Rerank, estrazione e robustezza sono già a posto — non rifarli.** Il problema è a monte: un **funnel troppo stretto** che affama il rerank (`SEARXNG_TOP_N = 5`, `SEARXNG_TIMEOUT_S = 3.0`) e un'**istanza SearXNG non curata e a freddo, per giunta condivisa con giorgio2**. Due interventi: uno solo-Metnos a ritorno immediato (§5.A), uno d'istanza da concordare per via del blast radius voce (§5.B).

## 1. Contesto

In MODO SEARCH `find_urls` interroga SearXNG, prende i top-N candidati, li fa rerankare a un LLM locale e poi espande in BFS. La qualità percepita di "cerca sul web X" dipende da questa catena. Questo report la dissezione e isola dove sta davvero la perdita di qualità/velocità.

## 2. Inquadramento (perché non si cambia motore)

1. **Tetto strutturale.** Web aperto in locale = metasearch (SearXNG, leader della categoria) oppure indice proprio (YaCy/Marginalia, qualità generalista scarsa). Nessun backend alternativo locale batte SearXNG sul grezzo. La leva è *cosa gli chiediamo* e *come filtriamo dopo* — non *quale motore*.
2. **Profilo Metnos = batch.** ~4 call/notte, ~15k tok: **latenza non vincolante, pertinenza sì** (l'opposto di giorgio2 voce). Questo autorizza scelte lente-ma-accurate (più candidati, timeout largo) che sul path voce sarebbero proibite.

## 3. Stato attuale — VERIFICATO, non rifare

La pipeline è già matura:

- **Fetch** `_searxng_search_full` (riga ~931): `format=json`, `language=all` (§7.9 — scelta corretta, evita il bias IT dell'istanza), filtro `blocked_origins` (ADR 0108), `time_range` server-side.
- **Rerank LLM** `_llm_rerank_candidates` (ADR 0118, riga ~1002): `tier=middle` riordina su query+title+snippet; **fallback non-silenzioso** in `meta.error` (§2.8). Budget `_RERANK_TIMEOUT_S = 8.0` (env `METNOS_FINDURLS_RERANK_TIMEOUT_S`) per la contesa GPU col planner.
- **Estrazione contenuto**: MODO `deep_search` legge già full-body top-K con pre-rank ibrido BM25+embedding + content ranking.
- **Robustezza**: `error_class` esplicito (`search_no_results` / `search_backend_unavailable` / `search_backend_invalid`), truncation §2.7, diversity cap `max_per_domain`.

➡️ Conclusione: rerank, estrazione, robustezza **fatti**. La perdita di qualità è prima del rerank.

## 4. Diagnosi — cause reali

### 4.1 Funnel che affama il rerank (`find_urls.py`, righe 98/103)
```python
SEARXNG_TIMEOUT_S = 3.0   # riga 98
SEARXNG_TOP_N     = 5      # riga 103
```
- **`TOP_N = 5`**: il rerank — la parte intelligente — vede solo 5 candidati. Un buon risultato che SearXNG mette in posizione 6–15 è **irrecuperabile**: nessun riordino lo fa risalire. Qui sta il collo di bottiglia di qualità.
- **`TIMEOUT = 3.0s`** mentre l'istanza aggrega fino a `max_request_timeout: 15.0`: **il client molla prima che i motori finiscano**. I motori lenti-ma-buoni non arrivano → set parziale e ballerino fra una run e l'altra. In un batch notturno, 3s è un vincolo autoinflitto.

### 4.2 Vincolo accoppiato: budget rerank (`_RERANK_TIMEOUT_S = 8.0`)
Alzare `TOP_N` allunga il rerank LLM: con ~20 candidati e `max_tokens=900`, sotto contesa GPU col planner si rischia di **sforare gli 8s → fallback all'ordine grezzo SearXNG**, annullando il beneficio (è il bug ARK/people-search già citato a riga 100). Quindi §5.A va calibrato, non applicato alla cieca.

### 4.3 Istanza non curata e a freddo (`/etc/searxng/settings.yml`) — CONDIVISA
- `use_default_settings: true` **senza override `engines:`** → interroga l'intero set default, inclusi motori lenti/spesso-bloccati che sporcano l'aggregato e fanno scattare il timeout di §4.1.
- **Nessuna cache** (`valkey.url` non configurato) → ogni query a freddo.
- ⚠️ `instance_name: "Giorgio Search"` → **l'istanza è condivisa con giorgio2** (voce, latency-sensitive). Ogni modifica a `settings.yml` ha blast radius sulla voce.

## 5. Interventi

### 5.A — `find_urls.py` (PRIORITÀ 1, blast radius zero su giorgio2)
1. **`SEARXNG_TOP_N` 5 → ~20.** Dà al rerank materiale vero. Singolo cambiamento a più alto ritorno.
2. **`SEARXNG_TIMEOUT_S` 3.0 → ~12.0** (≤ `max_request_timeout` istanza). In batch la latenza non conta: lascia completare l'aggregazione. Va **insieme** al punto 1 (più candidati senza più tempo non arrivano).
3. **Calibra il budget rerank** (§4.2): alzando `TOP_N` verifica che `_RERANK_TIMEOUT_S` (env `METNOS_FINDURLS_RERANK_TIMEOUT_S`) e `max_tokens=900` reggano ~20 candidati senza fallback sistematico. Se il rerank sfora, alza il budget o tronca i candidati passati all'LLM (non il TOP_N fetchato).
4. *(Pulizia opzionale)* promuovi `SEARXNG_TOP_N` e `SEARXNG_TIMEOUT_S` a override-via-env come gli altri parametri (`METNOS_SEARXNG_*`): il tuning futuro non richiederebbe più re-sign dell'executor.
5. *(Opzionale, query-type aware)* `categories=general` esplicito nei `params` (riga ~953) per disaccoppiarsi dai default d'istanza; per query tecniche aggiungere `engines=...,github,stackexchange`. Da valutare dopo aver misurato 1–3.

### 5.B — Istanza `/etc/searxng/settings.yml` (PRIORITÀ 2, CONCORDARE con suprastructure)
Aiuta **anche** giorgio2 (meno motori lenti = più veloce):
- **Curare gli engine**: override esplicito con un core veloce/affidabile (`google, brave, duckduckgo, wikipedia`) + tecnici (`github, stackexchange`), disabilitando i default lenti/bloccati. Taglia sia rumore sia latenza d'aggregazione → mitiga anche §4.1.
- **Abilitare cache valkey** (`valkey.url`): query ripetute servite calde.
- 🔴 **NON alzare `outgoing.request_timeout` dell'istanza**: peggiorerebbe la latenza voce di giorgio2. Il "lascia completare i motori lenti" per il batch si ottiene col timeout **client** di Metnos (§5.A.2), non con quello d'istanza.

## 6. Deploy

- **Metnos** (executor firmato → §7.10):
  ```
  python3 runtime/sign.py sign executors/find_urls
  systemctl restart metnos-http.service
  ```
  (se promuovi i parametri a env in §5.A.4, il tuning successivo è solo env + restart, senza re-sign).
- **Istanza** (§5.B): restart servizio SearXNG **in finestra concordata con suprastructure** — interrompe anche giorgio2.
- **ADR in `decisions/`**: registrare diagnosi (funnel + budget + istanza), i nuovi valori `TOP_N`/`TIMEOUT`/budget rerank, l'eventuale curatela engine, con la motivazione del tetto strutturale (§2).

## 7. Criteri di accettazione

| # | Verifica | Atteso |
|---|----------|--------|
| 1 | Query SEARCH (es. "AMD ROCm gfx1151"), confronto pre/post §5.A | top-5 finale più pertinente; risultati prima irraggiungibili (rank 6–15) ora recuperati dal rerank |
| 2 | Stessa query ripetuta N volte | ranking finale **stabile** (no set ballerino da timeout) |
| 3 | `meta` rerank | `candidates` ≈ 20; `used:true` quando l'LLM risponde; **no** fallback sistematico per budget sforato (§4.2) |
| 4 | Istanza post-curatela | latenza aggregazione ≤ baseline **e giorgio2 voce non peggiorata** (timeout istanza invariato) |
| 5 | ADR in `decisions/` | diagnosi + valori + motivazione documentati |

## 8. Coordinamento / blast radius

- **Istanza condivisa** ("Giorgio Search"): §5.B in finestra concordata; la curatela engine va validata anche sul path voce.
- **Reranker dedicato** (es. `bge-reranker-v2-m3` su `llama-server --reranking`) come alternativa al rerank LLM via gateway: allo stato **non serve** — il rerank ADR 0118 è adeguato una volta sfamato (§5.A). Se in futuro lo si volesse, è infra GPU → coordinare slot/porta con suprastructure (`:8080` Qwen, slot 0/1 pinned).

---

*Nota di metodo: questo report nasce da lettura diretta di `find_urls.py` e `settings.yml`. La prima ipotesi (mancano rerank/estrazione) era sbagliata — erano già implementati. La perdita di qualità è il funnel a monte, non il ranking.*
