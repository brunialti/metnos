# Benchmark pool di `services_registry.snapshots()` — 2026-07-21

## Esito

Il riuso del pool riduce la mediana dell'overhead puro da **0,3551 ms** a
**0,0849 ms** (-76,09%, 4,18x) e la scansione systemd da **6,5326 ms** a
**6,2422 ms** (-4,45%). Sul percorso amministrativo completo il costo dei
probe domina: **80,9775 ms** contro **79,9135 ms** (-1,31%), con p95
rispettivamente **106,6103 ms** e **105,5682 ms**.

Il riuso e' quindi corretto come eliminazione di churn per processo, ma non va
presentato come ottimizzazione materialmente percepibile della pagina completa.
Non emerge alcun motivo per un altro refactor del registro.

## Metodo

Strumento riproducibile:
`tests/benchmarks/tools/benchmark_services_registry.py`.

Le due condizioni usano lo stesso catalogo di 10 servizi, lo stesso limite di
8 thread e la stessa `_safe_snapshot()`. Cambia soltanto il ciclo di vita:

- `fresh_pool`: crea, usa e chiude un `ThreadPoolExecutor` a ogni chiamata,
  ricostruendo il comportamento precedente;
- `reused_pool`: chiama l'implementazione corrente `snapshots()` con pool
  persistente lazy.

L'ordine delle condizioni e' alternato a ogni iterazione. Ogni profilo ha tre o
cinque warmup per condizione. Host di riferimento: Linux 7.0.1 x86_64, Python
3.12.3, 32 CPU logiche. Il benchmark e' stato eseguito fuori sandbox per
raggiungere il bus systemd utente e gli endpoint locali reali.

## Risultati

| Profilo | Campioni/condizione | Fresh p50 | Reused p50 | Delta p50 | Fresh p95 | Reused p95 |
|---|---:|---:|---:|---:|---:|---:|
| overhead sintetico | 500 | 0,3551 ms | 0,0849 ms | -76,09% | 0,3912 ms | 0,0933 ms |
| systemd, no HTTP | 60 | 6,5326 ms | 6,2422 ms | -4,45% | 7,3678 ms | 7,2762 ms |
| completo, systemd + HTTP | 60 | 80,9775 ms | 79,9135 ms | -1,31% | 106,6103 ms | 105,5682 ms |

Nel profilo completo i massimi sono rumorosi (564,2511 ms fresh e 412,5860 ms
reused), ma p50 e p95 appaiati restano coerenti. Il primo campione esplorativo
da 12 iterazioni e' stato escluso dalla tabella perche' insufficiente per un
p95 stabile.

## Riproduzione

```bash
python3 tests/benchmarks/tools/benchmark_services_registry.py --mode all --warmups 3
python3 tests/benchmarks/tools/benchmark_services_registry.py \
  --mode full --iterations 60 --warmups 5
```

I profili `manager` e `full` richiedono accesso reale al bus systemd; un run in
sandbox misura soltanto errori immediati di connessione e non rappresenta il
nodo in esercizio.
