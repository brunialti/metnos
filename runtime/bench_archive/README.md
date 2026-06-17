# bench_archive — esperimenti una-tantum conclusi

Benchmark empirici già usati per prendere una decisione passata (modello
embedding, tuning prefilter, formulazione testo, latency, ecc.), con i loro
`.result.json`. NON sono test né gate di regressione, NON girano in CI/cron,
nessun codice di produzione li importa (solo si importano fra loro). Spostati
qui il 17/6/2026 per de-cluttering di `runtime/`.

Gate di regressione VIVI (restano altrove): `bench/intent_accuracy_bench.py`
(gold intent), `bench/routing_subset_bench.py` (routing first_tool).

Per rieseguirne uno: `cd runtime/bench_archive && python3 <nome>.py`.
