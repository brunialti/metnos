# Benchmark worker `compute_files_loc` — 2026-07-21

## Esito

Sul workload reale locale il valore ottimale e' **un worker**. Aumentare il pool
non produce speedup: con una chiamata la p50 passa da **699,189 ms** a
**790,421 ms** (+13,05%) fra 1 e 16 worker; con due chiamate simultanee il
tempo di gruppo passa da **1.733,217 ms** a **1.868,480 ms** (+7,80%).

Il costo cresce invece in modo monotono: una chiamata passa da 1 a 16 thread
executor e da +9,52 a +19,86 MiB RSS; due chiamate arrivano a 32 thread executor
e +41,50 MiB RSS. La sovrapposizione di due scansioni a un worker impiega gia'
il 23,94% in piu' di due mediane singole seriali, quindi la contesa sullo stesso
filesystem non offre throughput aggiuntivo.

Tutti i casi, comprese le invocazioni simultanee, hanno prodotto lo stesso
digest strutturale, 2.199 file e 531.167 linee, con tasso errori **0**.

## Metodo

Strumento riproducibile:
`tests/benchmarks/tools/benchmark_compute_files_loc.py`.

- host: Linux 7.0.1 x86_64, Python 3.12.3, 32 CPU logiche;
- sorgente: `/opt/metnos`, soli file `.py`, esclusioni canoniche
  dell'executor, cap 50.000;
- configurazioni: 1/2/4/8/16 worker, una e due invocazioni simultanee;
- per configurazione: un warmup e sei campioni, in un processo isolato;
- RSS campionata da `/proc/self/status` ogni 2 ms;
- thread effettivi contati dai thread `ThreadPoolExecutor-*` realmente vivi;
- output completo canonicalizzato e confrontato via SHA-256.

## Chiamata singola

| Worker | p50 | p95 | Delta p50 vs 1 | Thread reali | Delta RSS |
|---:|---:|---:|---:|---:|---:|
| 1 | 699,189 ms | 708,909 ms | — | 1 | 9,52 MiB |
| 2 | 715,833 ms | 732,331 ms | +2,38% | 2 | 11,72 MiB |
| 4 | 759,596 ms | 783,283 ms | +8,64% | 4 | 14,39 MiB |
| 8 | 774,090 ms | 794,580 ms | +10,71% | 8 | 17,15 MiB |
| 16 | 790,421 ms | 797,830 ms | +13,05% | 16 | 19,86 MiB |

## Due chiamate simultanee

| Worker/chiamata | p50 gruppo | p95 gruppo | Delta p50 vs 1 | Thread reali totali | Delta RSS |
|---:|---:|---:|---:|---:|---:|
| 1 | 1.733,217 ms | 1.754,562 ms | — | 2 | 18,90 MiB |
| 2 | 1.774,974 ms | 1.855,957 ms | +2,41% | 4 | 23,23 MiB |
| 4 | 1.787,676 ms | 1.830,753 ms | +3,14% | 8 | 28,79 MiB |
| 8 | 1.881,126 ms | 1.904,748 ms | +8,53% | 16 | 35,64 MiB |
| 16 | 1.868,480 ms | 1.908,899 ms | +7,80% | 32 | 41,50 MiB |

## PC-ROBERTO / Windows reale

Il 2026-07-21 alle 20:25 UTC e' stata eseguita la matrice remota richiesta sul
device fisico `PC-ROBERTO`: Windows x86_64, client 0.2.25, 14 CPU logiche,
sandbox AppContainer. Il corpus e' la libreria standard Python gia' installata
dal client; sono stati letti i primi 512 file `.py` deterministici, pari a
230.896 LOC. Nessuna fixture e' stata creata e nessun file del device e' stato
modificato.

Il probe e' stato eseguito dentro lo stesso processo dell'executor per misurare
thread e working set reali. Ogni configurazione e' partita in un processo
remoto isolato, con un warmup e sei campioni. Le latenze in tabella misurano la
sola scansione, come il benchmark locale; il tempo remoto complessivo include
anche costruzione AppContainer, consegna e raccolta del risultato.

### Chiamata singola remota

| Worker | p50 | p95 | Delta p50 vs 1 | Thread reali | Delta RSS |
|---:|---:|---:|---:|---:|---:|
| 1 | 233,198 ms | 303,639 ms | — | 1 | 1,70 MiB |
| 2 | 188,488 ms | 203,973 ms | -19,17% | 2 | 2,01 MiB |
| 4 | 196,809 ms | 200,730 ms | -15,60% | 4 | 1,67 MiB |
| 8 | 190,986 ms | 208,268 ms | -18,10% | 8 | 2,29 MiB |

Due worker producono il miglior compromesso per una scansione isolata. Quattro
e otto worker restano nel plateau e non migliorano materialmente la p50 di due
worker.

### Due chiamate simultanee remote

| Worker/chiamata | p50 gruppo | p95 gruppo | Delta p50 vs 1 | Thread reali totali | Delta RSS |
|---:|---:|---:|---:|---:|---:|
| 1 | 375,173 ms | 390,402 ms | — | 2 | 3,95 MiB |
| 2 | 407,043 ms | 980,011 ms | +8,49% | 4 | 3,73 MiB |
| 4 | 442,465 ms | 723,588 ms | +17,94% | 8 | 4,00 MiB |
| 8 | 376,217 ms | 384,605 ms | +0,28% | 16 | 4,52 MiB |

Sotto sovrapposizione, un worker per chiamata ha la migliore p50. Otto worker
eguagliano la latenza usando otto volte i thread; due e quattro peggiorano la
p50 e mostrano code lunghe nella p95. I 72 output misurati, singoli e
simultanei, hanno tutti digest
`de64e5e4fde9f92faee75503aa028927c42bf78e833bd56c792ac912572ca8f1`,
512 file, 230.896 LOC ed error rate 0.

Il primo tentativo ha inoltre rilevato uno shim di processo stantio:
`executor_helpers.py` importava `worker_policy.py`, assente dal bundle tenuto in
memoria dal daemon avviato prima della modifica. Il riavvio quiescente del solo
daemon Telegram/agent-server ha riallineato il bundle content-addressed; il
client ha poi aggiornato automaticamente lo shim. Una scansione volutamente
troppo ampia di `C:\\Users` e' stata negata dall'AppContainer, mentre il path
esatto del corpus e' rimasto accessibile in sola lettura.

## Decisione combinata

Non esiste un budget globale statico ottimale: Linux locale preferisce un
worker; Windows/AppContainer beneficia materialmente di due worker soltanto
nella chiamata isolata; sotto carico simultaneo torna preferibile un worker.
Pertanto il profilo remoto **vieta la migrazione globale a classe 0**, ma non
ammette automaticamente una classe positiva universale.

Il comportamento esistente resta invariato. Un'eventuale promozione dovra'
essere separata secondo ADR 0196 e usare una policy consapevole di placement e
carico: candidato prudente `1` di default e `2` solo per una scansione Windows
isolata gia' ammessa dallo scheduler. Prima del cutover servono un test E2E del
budget assegnato centralmente e la prova che due processi reali non aggirino
l'admission/backpressure. Non e' giustificato usare 4 o 8 worker su questo
corpus.

## Riproduzione

```bash
python3 tests/benchmarks/tools/benchmark_compute_files_loc.py \
  --workers 1,2,4,8,16 \
  --concurrent-calls 1,2 \
  --iterations 6 --warmups 1
```
