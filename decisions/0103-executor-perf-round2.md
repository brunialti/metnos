---
id: 0103
title: Executor perf round 2 — HostThrottle estratto + audit negativo §7.4
date: 2026-05-07
status: accepted
area: runtime, executor
related:
  - 0098  # find_urls parallel pattern
  - 0100  # round 1: read_urls_html, read_urls_pdf, compute_files_loc
complements:
  - 0100
---


## Context

Dopo ADR 0100 (round 1, 3 executor parallelizzati con speedup 1.7-17.8x) il
brief richiedeva di estendere il pattern a TUTTI gli executor handcrafted
con loop iterativi/ricorsivi che potessero beneficiare.

Audit completo dei 21 executor handcrafted core in `/opt/myclaw/executors/`
identifica 3 candidati top-ROI (find_dirs, get_files_metadata, move_files);
benchmark pre/post-patch su SSD locale + tmpfs + JPEG reali con EXIF.

Il risultato e' negativo per tutti e 3 — overhead pool ThreadPool dominante,
nessun speedup misurabile, alcuni casi con regressione 0.5-0.7x.

CLAUDE.md §7.4 «niente parallelismo se non c'e' speedup reale» richiede di
NON applicare patch che non producono accelerazione misurabile. Il risultato
positivo di Round 2 e' solo l'estrazione di `_HostThrottle` in modulo
condiviso (regola del 3, §7.2): tre caller (find_urls, read_urls_html,
read_urls_pdf) → un modulo `runtime/host_throttle.py`.


## Decision

### (1) Estrazione `HostThrottle` in modulo condiviso

Nuovo `runtime/host_throttle.py`. API:

    HostThrottle(per_host_limit: int, rate_limit_ms: int = 0)
    .acquire(host) / .release(host)

`rate_limit_ms=0` (default) = solo Semaphore concurrency, niente delay.
`rate_limit_ms>0` = aggiunge throttle per request consecutive sullo stesso
host (usato da find_urls dove ADR 0098 vincola rate per tier polite).

Refactor dei tre caller:

- `executors/find_urls/find_urls.py` — rimossa `class _HostThrottle`
  (45 righe), import via `sys.path` + `from host_throttle import HostThrottle`.
  `_throttle = HostThrottle(per_host_limit=N, rate_limit_ms=M)` invariato.
- `executors/read_urls_html/read_urls_html.py` — rimossa classe locale
  (28 righe) + rimosso `import threading` non piu' necessario al top-level.
  Riferimenti type-hint `_HostThrottle | None` → `HostThrottle | None`.
- `executors/read_urls_pdf/read_urls_pdf.py` — idem.

Nessun cambio di comportamento: la classe estratta accetta entrambi i casi
(rate=0 default per html/pdf, rate=M per find_urls). Test 29/29 PASS sui
tre executor.

### (2) Patch parallelizzazione 3 executor — REVERTED per §7.4

Audit identifica 3 candidati promettenti:

- **find_dirs**: `_scan_dir` per dir fa `iterdir()` + `stat()` per child →
  pattern multi-IO FS apparentemente parallelizzabile.
- **get_files_metadata**: per ogni entry: `_exif()` (Pillow open+parse) +
  `stat()` + opzionali `_read_image_dimensions()`.
- **move_files**: `_move_one()` per entry; `shutil.move` cross-FS = full
  copy I/O.

Patch applicate seguendo il pattern ADR 0100 (ThreadPoolExecutor + cap
globale env-overridable, ordering preservato per indice). Re-firma manifest.

Benchmark realistici (vedi `Test` sotto): tutti e 3 mostrano speedup <1
(0.4-0.7x). Cause:

1. **find_dirs**: su SSD locale warm cache, `_scan_dir` costa ~1ms; pool
   submit overhead ~0.5-2ms per task. Su 600 dirs: 100-130ms seriale, ~270ms
   parallelo. Bench su /opt/myclaw reale (611 dirs, 7000 file): 593ms
   serial vs 610-630ms parallelo. Su NAS cold-cache potrebbe vincere ma
   non e' osservabile sul setup attuale.
2. **get_files_metadata**: 98 JPEG reali con EXIF da fotocamera (Canon EOS
   5D Mark III, Apple iPhone, ecc.) → 36ms serial vs 51ms con 8 worker.
   Pillow rilascia poco GIL su EXIF; pool overhead domina.
3. **move_files**: 50 file 256KB su tmpfs (same-FS) → 2ms serial vs 4.5ms
   parallel. shutil.move = renameat() syscall, ~us-level.

Patch rollback completo: nessun import multiprocessing/ThreadPoolExecutor,
nessun env METNOS_*_WORKERS aggiunto in produzione. Re-firmati i 3 manifest.

L'unica patch tenuta nei 3 file e' il refactor `_HostThrottle → HostThrottle`
condiviso (passo 1). Round 2 e' quindi un refactor neutro + audit
documentato.


## Consequences

### Positive

- **Codice piu' pulito**: ~100 righe di duplicazione `_HostThrottle` rimosse
  (regola del 3 §7.2). Future evoluzioni del throttle (es. backpressure,
  jitter, metriche) si fanno in un unico punto.
- **Audit deterministico §7.4**: prima di ogni futura proposta «parallelizziamo
  X» abbiamo benchmark riproducibili in `/tmp/bench_round2*.py` che mostrano
  cosa NON funziona su SSD locale.
- **Costanza signature**: `HostThrottle(per_host_limit, rate_limit_ms=0)` e'
  il superset delle 3 firme precedenti; un 4° caller importa senza scrivere
  classi.

### Negative / open

- Risultato di Round 2 = «no win». Roberto si aspettava speedup; il brief
  ha ROI negativo. Documentato.
- Su NAS o storage di rete potrebbe esserci speedup teorico in find_dirs e
  get_files_metadata. Non disponibile per bench. Riapertura possibile se
  Metnos passa a indicizzare CIFS/NFS pesantemente.
- `compute_files_loc` (ADR 0100) ha mostrato 1.75x su SSD: il workload
  CPU-Python piu' grande (binarieta' sniff + line count) contribuisce.
  Confronta: `_scan_dir` + EXIF + move sono troppo brevi.

## Test

- Smoke invariants: catalog 55, 0 rejected.
- `tests/runtime/executors/test_read_urls_html.py` 17/17 PASS.
- `tests/runtime/executors/test_read_urls_pdf.py` 3/3 PASS.
- `tests/runtime/executors/test_find_urls.py` 8/8 PASS.
- Regression: 691/699 PASS — 8 fail pre-esistenti (gallery×3, http_server×3,
  pipeline_smoke, users_smoke_e2e). **Zero nuove regressioni.**
- Re-firma manifest: 6 `manifest.toml.sig` rinnovati durante il ciclo
  patch+revert (find_urls, read_urls_html, read_urls_pdf, find_dirs,
  get_files_metadata, move_files).

## Files

### Modificati (refactor neutro)

- `runtime/host_throttle.py` — NUOVO modulo (~50 LOC).
- `executors/find_urls/find_urls.py` — `_HostThrottle` class rimossa,
  import condiviso.
- `executors/read_urls_html/read_urls_html.py` — idem.
- `executors/read_urls_pdf/read_urls_pdf.py` — idem.
- `executors/{find_urls,read_urls_html,read_urls_pdf}/manifest.toml.sig`
  — re-signed.

### Reverted (no speedup §7.4)

- `executors/find_dirs/find_dirs.py` — patch ThreadPool reverted.
- `executors/get_files_metadata/get_files_metadata.py` — patch ThreadPool
  reverted.
- `executors/move_files/move_files.py` — patch ThreadPool reverted.

## Bench scripts

- `/tmp/bench_round2.py` — bench iniziale 3 candidati.
- `/tmp/bench_round2_v2.py` — bench heavy (file-heavy dir, payload 256KB).
- `/tmp/bench_round2_v3.py` — bench su /opt/myclaw e /opt/suprastructure
  (repo reali, 7000+ dir).
- `/tmp/bench_gfm_jpeg.py` / `bench_gfm_jpeg2.py` / `bench_gfm_real.py` —
  bench get_files_metadata su JPEG generati e reali.
- `/tmp/exec_perf_round2_audit.md` — audit dei 21 executor handcrafted.
