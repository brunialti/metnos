---
id: 0100
title: Executor perf — parallelizzazione thread-pool su loop iterativi I/O-bound
date: 2026-05-07
status: accepted
area: runtime, executor
related:
  - 0098  # find_urls parallel pattern (riferimento)
  - 0099  # runtime perf (catalog cache, seed-step, reasoning budget)
complements:
  - 0098
---


## Context

Dopo ADR 0098 (`find_urls` parallelizzato, 10x speedup) restano executor
con loop iterativi seriali su I/O-bound. Audit (vedi `/tmp/exec_perf_audit.md`):
3 candidati top-ROI con HIGH speedup potenziale e low rischio.

Vincolo CLAUDE.md §7.4: «niente parallelismo se non c'e' speedup reale».
Verificato empiricamente prima di applicare.


## Decision

Parallelizzati 3 executor seguendo il pattern `find_urls` (`_HostThrottle`
per-host + `ThreadPoolExecutor` globale). Pattern essenziale, non estratto
in modulo condiviso (semplicita' §7.2; e' ~30 righe per executor).


### (1) `read_urls_html`

`for url in urls: _fetch_one(url, ...)` → `ThreadPoolExecutor`.

- `_HostThrottle` thread-safe (Semaphore per-host) per non saturare un
  singolo dominio quando l'utente passa molti URL same-host.
- Cap globale `min(32, cpu*4)` env `METNOS_READ_URLS_GLOBAL_MAX`.
- Cap per-host `4` env `METNOS_READ_URLS_PER_HOST`.
- `_fetch_one` accetta `throttle` opzionale (None → sync mode); rilascio
  in `finally` post-fetch network: il parsing HTML (CPU-locale) NON tiene
  lo slot per-host occupato.
- Stage 2 iframe-follow: parallelizzato indipendentemente con il proprio
  pool dopo la fetch primaria. 1 follow max per pagina (no chain).
- Output deterministico: indici originali preservati (`entries` ordinato
  per posizione di input, indipendente da ordine di completamento worker).
- Fast-path sync per N=1 (no overhead pool).

**Benchmark mock HTTP @ 200ms latency:**
- 20 URL same-host: serial 4022ms → parallel 1026ms = **3.92x** (limitato
  da `_PER_HOST_MAX=4`, corretto: protegge l'host).
- 20 URL × 5 hosts: serial 4022ms → parallel 226ms = **17.8x**.

### (2) `read_urls_pdf`

Stesso pattern di read_urls_html. Cap globale `min(16, cpu*2)` (parsing
pypdf piu' costoso del HTML stdlib parser, conservativi).

Output: `entries` riordinato per indice originale; `failed` idem.

### (3) `compute_files_loc`

`for cand in walker: _is_binary(cand) + _count_one(cand)` → walk seriale
(deterministico) seguito da `ThreadPoolExecutor` su processing.

- Helper `_process_one(cand)` combina sniff binarieta' + conteggio righe.
- Workers `min(16, cpu*2)` env `METNOS_COMPUTE_LOC_WORKERS`.
- `by_path` mantiene ordine di walk (mappa `pos → result`).
- Totali commutativi (sum), invarianti rispetto all'ordine.
- Fast-path sync per N=1.

**Benchmark su `/opt/suprastructure` (5064 .py, 157 dopo exclude):**
- workers=1  : 163ms
- workers=8  : 96ms (1.70x)
- workers=16 : 93ms (1.75x)

Speedup modesto su SSD locale + file Python piccoli (CPU overhead Python
domina). Cresce su workload pesanti (NAS/CIFS, repo grandi con file
medi/grossi). Lasciato comunque parallelizzato: rischio nullo, scalabilita'
positiva.


## Consequences

### Positive

- `read_urls_html` / `read_urls_pdf`: 4-18x speedup su tipici batch (utente
  passa lista di N URL al PLANNER, executor li scarica in parallelo).
- `compute_files_loc`: 1.7-2x su workload medi, scala su NAS o repo grandi.
- Pattern coerente con ADR 0098 (`_HostThrottle`).
- Zero LLM (§7.9), zero refactor cross-cutting (§7.2).
- Output deterministico preservato (ordering by input index).

### Open

- Pattern `_HostThrottle` ora duplicato 3 volte (find_urls + read_urls_html
  + read_urls_pdf). Estrazione in `runtime/_url_throttle.py` differita: si
  fa quando il 4° caller arriva (regola del 3, §7.2).
- `read_files_csv`/`xlsx`/`ocr`: candidati Phase 2, speedup MED-LOW. Skip
  per ora (rischio openpyxl thread-safety; OCR Tesseract richiederebbe
  `ProcessPoolExecutor` per uscire dal GIL).
- LLM-bound executor (`describe_entries`, `classify_entries`): NO parallel
  (Ollama serializza GPU; nessuno speedup atteso).
- IMAP/SMTP (`read_messages`, `send_messages`): NO parallel (provider
  Migadu/register.it cap connection per-account).


## Test

- Smoke invariants OK (catalog 55, 0 rejected).
- `tests/runtime/executors/test_read_urls_html.py` 7/7 PASS.
- `tests/runtime/executors/test_read_urls_pdf.py` 7/7 PASS.
- Regression: 656/664 PASS — 8 fail pre-esistenti (gallery×3,
  http_server×3, pipeline_smoke, users_smoke_e2e). **Zero nuove
  regressioni.**
- Re-firma: `runtime/sign.py sign` rieseguito su tutti e 3.

## Files

- `executors/read_urls_html/read_urls_html.py` — `_HostThrottle` + ThreadPool in `invoke()`.
- `executors/read_urls_pdf/read_urls_pdf.py` — idem.
- `executors/compute_files_loc/compute_files_loc.py` — ThreadPool su processing.
- `executors/{read_urls_html,read_urls_pdf,compute_files_loc}/manifest.toml.sig` — re-signed.

## Bench scripts

- `/tmp/bench_compute_loc.py`
- `/tmp/bench_read_urls.py`
- `/tmp/bench_read_urls_multi.py`

## Riesame worker `read_urls_html` (2026-07-21)

La baseline riproducibile corrente e' in
`internal/reports/read_urls_html_worker_benchmark_2026-07-21.md`. Su 20 URL
same-host il throttle satura a 4 e i cap 8/16/32 non riducono la p50; su cinque
host il throughput continua invece a scalare fino ai 20 job disponibili.

Il pool per invocazione e' quindi limitato anche dalla capacita' utile
`host_distinti * cap_per_host`, oltre che dal cap globale e dal numero di job.
Il test con HTML da 256 KiB conferma p50 equivalente fra 4 e 20 thread, ma
riduce il delta RSS da 35,41 a 19,98 MiB. Output e ordine restano equivalenti.

Questa ottimizzazione non risolve il throttle trasversale: due invocazioni
possono ancora raddoppiare la pressione sullo stesso host. Un limite condiviso
fra processi o una chiave per insiemi di host richiede una decisione distinta;
non viene implicato dalla presente modifica locale.
