---
id: 0120
title: Pass consumer="metnos" al gateway LLM per slot affinity
date: 2026-05-10
status: accepted
area: runtime
related:
  - 0117  # unified image enrichment index (origine carico LLM 15k tok)
---

## Context

Il llama-server condiviso su `beelink:8080` (Gemma 4 27B MoE su gfx1151,
`--parallel 2`) è usato sia da Metnos (image enrichment / RAG, prompt
~15k token) sia da giorgio2 (voice realtime, prompt ~4k token).
Diagnosi suprastructure 2026-05-10: il selettore LCP-similarity
interno di llama-server può mandare la voice nello slot dove Metnos
ha appena scritto la sua KV cache. Quando succede:

- LCP comune voice↔Metnos = 27 token
- 4189 token di prefill ricalcolati da zero
- Tempo: 7.57s per quella sola chiamata (caso reale 09-05 23:21)

Questo butta via tutta la cache della voice e fa lievitare il voice E2E
da ~6s atteso a ~20s misurato sul caso "accendi televizione".

Il gateway suprastructure (`/llm/complete` su :8801) ora supporta
**slot affinity per consumer**: passando `consumer` nel body JSON,
inoltra a llama-server il parametro `id_slot` che bypassa LCP e pinna
la richiesta a uno slot dedicato.

Mappa correntemente cablata in `gateway/server.py::_CONSUMER_TO_SLOT`:

| consumer        | id_slot | uso                       |
|-----------------|---------|---------------------------|
| `giorgio` / `voice` | 0   | voice realtime giorgio2   |
| `metnos` / `myclaw` | 1   | batch image enrichment    |

## Decision

Tutti i client Metnos che parlano al gateway suprastructure devono
includere `consumer: "metnos"` nel body delle chiamate `/llm/complete`.

Esempio Python (httpx):

```python
resp = httpx.post(
    "http://beelink:8801/llm/complete",
    json={
        "prompt": "...",
        "system": "...",
        "max_tokens": 1024,
        "consumer": "metnos",  # ← slot affinity, vedi ADR 0120
    },
    headers={"Authorization": "Bearer <key>"},
    timeout=300,
)
```

Il campo è opzionale: se assente, il gateway ricade su un fallback
IP-based (localhost → metnos, LAN → giorgio). Il fallback funziona
oggi perché Metnos gira sulla stessa beelink del gateway (127.0.0.1)
e giorgio2 chiama da 192.168.1.177. **Comunque va passato esplicito**
perché:

- Se Metnos in futuro girasse su altro host, il fallback IP-based lo
  classificherebbe come voice → bug silenzioso (slot sbagliato).
- Esplicito è auto-documentante nei log e nei diff codice.
- Costo zero: una chiave in più nel JSON.

I client da aggiornare (cercare le chiamate a `/llm/complete`):

- `executors/<image_enrichment>/...` (vedi ADR 0117)
- `runtime/llm_client.py` o equivalente wrapper centrale, se esiste
- Eventuali script `scripts/*.py` che chiamano il gateway direttamente

Se esiste un wrapper unico, basta aggiungere `consumer="metnos"` nel
default lì e tutti i caller ne ereditano.

## Alternatives considered

- **Lasciare il fallback IP-based**: funziona oggi, fragile domani.
  Una migrazione di host o un container in rete diversa rompe tutto
  silenziosamente. Scartato.
- **Header HTTP `X-Consumer`**: equivalente, ma più verboso (`headers={...}`)
  e meno discoverable nei tool di test (curl/Postman). Body field vince
  per ergonomia.
- **Backend dedicato per Metnos** (secondo llama-server o vLLM): risolve
  davvero la contesa GPU (con `id_slot` la voice aspetta comunque se
  Metnos sta facendo prefill 15k tok). Ma costa ~16 GB RAM extra per il
  modello duplicato e settimane di tuning. Tenuto come opzione futura
  se la contesa GPU diventerà bottleneck visibile.

## Consequences

- Code paths Metnos che chiamano `/llm/complete` vanno aggiornati per
  passare `consumer="metnos"`. Cambio una-tantum, ~1 LOC per call site.
- Nessun impatto a runtime se il campo è omesso: fallback IP funziona.
- I log llama-server mostrano lo slot scelto (`task NNNN | slot id N`):
  utile per verificare che il routing è effettivo. Comando:
  ```
  journalctl -u llama-server.service -f | grep -E "launch_slot_:"
  ```
- Limite noto: `id_slot` controlla solo la cache, non la GPU. Se Metnos
  e voice partono insieme, la voice aspetta comunque il prefill di
  Metnos in queue. La frequenza con cui questo succede oggi è bassa
  (4 batch Metnos in 3 ore notturne osservate). Se diventa visibile,
  si valuta backend separato.
