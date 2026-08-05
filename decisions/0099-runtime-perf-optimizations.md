---
id: 0099
title: Runtime perf — seed-step injection, catalog cache, dynamic reasoning budget
date: 2026-05-07
status: accepted
area: runtime, planner, performance
related:
  - 0094  # fast path
  - 0098  # web crawl parallel
complements:
  - 0094
  - 0098
---


## Context

Turn live federvolley 7/5/2026 dopo deploy ADR 0098: 8+ minuti per
trovare 58 URL. La parallelizzazione (b) funziona (8 conn TCP confermate
via `ss`), ma il PLANNER hint (Z) ha **fallito**: il PLANNER ha comunque
scelto `find_urls` come step 1 invece di `read_urls_html(urls=[seed])`.
Roberto: «non capisco se ha trovato il risultato».

Diagnosi: il segnale «esplorando in profondita'» nel prompt utente
attiva il pattern discovery del PLANNER, e l'hint testuale (Z) non
sovrascrive il prior. Il PLANNER probabilistico non e' affidabile per
il routing strutturale.

Inoltre: profilando il turn precedente, ogni step ha ~25-40s di latenza
PLANNER (Gemma 4 26B think=true reasoning_budget=512). Su 6 step =
~3 min puro thinking. Riduzione possibile per gli step >= 2 dove il
contesto e' gia' vincolato.

Infine: `loader.load_catalog()` legge 55+ manifest TOML ad ogni turn.
Profilato: ~80ms cold, chiamato 5-6 volte per turn = ~400-500ms
overhead non necessario quando il catalog non cambia.

## Decision

Tre ottimizzazioni complementari, indipendenti, totalmente deterministiche.

### 1. Seed-step injection — `runtime/fast_path.py::try_seed_step`

Quando la query contiene un URL completo (regex `https?://[^\s<>'"]+`
con netloc valido), il runtime DETERMINISTICAMENTE inietta come step 1
`read_urls_html(urls=[<URL>])`. PLANNER prende il controllo dallo step 2
in poi, vedendo il risultato del read in history.

API:
```python
try_seed_step(query: str) -> Optional[dict]
# returns {"executor": "read_urls_html", "args": {"urls": [URL]}, "url": URL}
# o None se nessun URL valido nella query.
```

Integrazione in `agent_runtime.py::run_turn`:
- DOPO `try_fast_path` (che terminerebbe il turno) e PRIMA del PLANNER loop.
- Skip se `reference_images` allegati (semantica diversa).
- Step appended con `step_num=1`, marker `seed_step=True`.
- PLANNER loop parte da `step_num=2`.
- cap_steps NON cambiato: il seed_step CONSUMA budget (niente
  pollution della contabilita').

Razionale: ADR 0098 (Z) era un hint testuale al PLANNER, fragile su
prompt ambigui. Il runtime puo' garantire l'invariante deterministicamente
quando il segnale (URL nella query) e' inequivoco.

### 2. Catalog load caching — `runtime/loader.py`

Cache modulo-level `_CATALOG_CACHE: dict[str, (Catalog, signature)]`.
Cache key: `(executors_dir, verify, include_synth, DEFAULT_LANG)`.
Signature: max(mtime) di manifest.toml + .py + .sig + mtime del DB
`executor_aging` (lifecycle override).

Hit O(1) (~1ms) vs cold load (~80ms). Su un turn da 6 step ognuno
chiamante `load_catalog`: **~480ms freed**.

API supporto: `invalidate_catalog_cache()` per test che modificano stato
on-disk. In normale uso runtime, l'invalidazione e' automatica via
mtime watching.

Sicurezza: la firma copre TUTTE le sorgenti che possono cambiare il
catalog visibile (manifest, code, signature, aging DB). `apply_ager`
e `sign.py sign` invalidano automaticamente.

### 3. Reasoning budget dinamico — `runtime/agent_runtime.py`

Default LlamaCppProvider `reasoning_budget=512`. Modifica:
- **Step 1 PLANNER call** (primo dopo eventuale seed_step): 768.
  Decision iniziale, contesto poco vincolato → think piu' generoso.
- **Step 2+**: 256. History gia' presente, ranker applicato, pool tool
  ristretto → decisione vincolata, meno reasoning serve.

Pass-through condizionale: solo a `provider.name == "llamacpp"` (gli
altri provider non accettano il kwarg). Non rompe i test stub.

Riduzione attesa: ~30-50% latency PLANNER per step 2+ (~10-15s
risparmiati per step), ~50-100s per turn da 6 step.

## Consequences

Positive:
- **Caso federvolley risolto strutturalmente**: il PLANNER non puo'
  PIU' scegliere find_urls quando l'utente fornisce URL specifico. Lo
  step 1 e' garantito dal runtime.
- ~480ms/turn risparmiati su catalog cache.
- ~50-100s/turn risparmiati su reasoning_budget dinamico.
- Sommando: turn tipico da 30s di pure overhead → 5-10s.
- Cache key include `DEFAULT_LANG` → multilingua compatibile.
- Invalidate automatica via aging DB mtime → no stale catalog.

Open / future:
- Validation live del seed-step su prompt federvolley (richiede daemon
  restart).
- Multi-URL nel query: oggi `try_seed_step` prende il primo. Possibile
  estensione per `urls=[url1, url2]`.
- Riduzione reasoning_budget anche su step 1 quando `intent.verb` e'
  ben classificato + 1-2 candidates (low-ambiguity).

## Test

- `tests/runtime/engine/test_fast_path.py`: +7 test su `try_seed_step` (URL+path,
  bare domain, no URL, trailing punct, empty, http/https, first URL wins).
  Suite totale 36/36 PASS.
- `tests/runtime/i18n/test_loader_description_lang.py` + `test_introvertive_loop_stress.py`:
  18/18 PASS (cache key + signature aging DB).
- `tests/runtime/infra/test_run_turn_reference_images.py`: 14/14 PASS
  (nessuna interferenza seed_step con reference_images).
- Full regression: 656 pass / 8 fail (le 8 pre-esistenti).
- Smoke invariants: 55/55 catalog OK.
- Benchmark cache: cold 80ms, warm 1ms, **54× speedup**.

## File toccati
- `runtime/fast_path.py`: nuova `try_seed_step()` + costanti `_URL_RE`,
  `_URL_TRAILING_STRIP`.
- `runtime/agent_runtime.py`: import `try_seed_step`, blocco seed-step
  injection (~80 LOC), loop start dinamico `_loop_start_step`,
  reasoning_budget dinamico nel `provider.chat_with_tools`.
- `runtime/loader.py`: `_CATALOG_CACHE` + `_catalog_cache_signature` +
  `invalidate_catalog_cache()`. ~50 LOC.
- `tests/runtime/engine/test_fast_path.py`: +7 test seed_step.
