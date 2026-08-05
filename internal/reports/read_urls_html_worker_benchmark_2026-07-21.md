# Benchmark worker `read_urls_html` — 2026-07-21

## Esito

Il parallelismo e' utile, ma il cap ottimale dipende dal numero di host. Su 20
URL dello stesso host la p50 migliora fino a **4 worker** (215,595 ms) e poi
resta piatta: 8/16/32 worker producono 216,296/216,693/216,209 ms. Il pool crea
pero' fino a 20 thread reali, dei quali 16 restano bloccati dal throttle.

Su 20 URL distribuiti su 5 host il beneficio continua fino a tutti i 20 job:
la p50 scende da 828,460 ms con un worker a 57,390 ms col cap 32 (20 thread
reali). Un budget centrale di classe 2, massimo 8 worker, porterebbe la p50 a
133,928 ms: **+133,37%** rispetto al comportamento corrente sul batch
multi-host.

Il limite per-host non e' globale: due invocazioni costruiscono due
`HostThrottle` indipendenti. Con almeno 4 worker ciascuna, la pressione sullo
stesso host passa da 4 a **8 richieste simultanee**; su cinque host il picco
globale arriva a **40** richieste. Il budget dei worker per invocazione non
risolve quindi da solo l'oversubscription fra turni.

Tutti i **216 output completi** hanno digest stabile per profilo e tasso errori
zero; ordine e contenuto sono equivalenti in ogni configurazione.

## Metodo

Strumento riproducibile:
`tests/benchmarks/tools/benchmark_read_urls_html.py`.

- cinque server HTTP loopback controllati, 40 ms di latenza per risposta;
- 20 pagine HTML deterministiche, cache e iframe disabilitati;
- profili `same_host` e `multi_host` (4 URL per ciascuno di 5 host);
- 1/2/4/8/16/32 worker, una e due invocazioni simultanee;
- un warmup e sei campioni per configurazione, processo figlio isolato;
- p50/p95, RSS, thread executor reali e pressione osservata dai server;
- rimozione dei soli campi temporali prima del digest SHA-256.

## Chiamata singola

| Worker | Same-host p50 | Same-host p95 | Pressione host | Multi-host p50 | Multi-host p95 | Pressione globale |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 826,584 ms | 828,862 ms | 1 | 828,460 ms | 830,118 ms | 1 |
| 2 | 419,952 ms | 420,139 ms | 2 | 419,894 ms | 421,053 ms | 2 |
| 4 | 215,595 ms | 216,243 ms | 4 | 216,022 ms | 216,381 ms | 4 |
| 8 | 216,296 ms | 216,441 ms | 4 | 133,928 ms | 134,220 ms | 8 |
| 16 | 216,693 ms | 217,273 ms | 4 | 94,525 ms | 94,642 ms | 16 |
| 32 | 216,209 ms | 216,526 ms | 4 | 57,390 ms | 58,698 ms | 20 |

Nel caso same-host il delta RSS cresce da 8,64 MiB (1 worker) a 9,89 MiB
(20 thread effettivi). Il costo di memoria e' contenuto, ma i thread oltre 4
non svolgono lavoro utile.

### Controllo HTML pesante

Il profilo same-host e' stato ripetuto con 20 pagine da 256 KiB. La p50 resta
equivalente: 222,757 ms con 4 worker e 222,088 ms col cap 32 (20 thread reali),
una differenza dello 0,30% entro il rumore. Il delta RSS passa invece da
19,98 MiB a 35,41 MiB. Anche questo profilo ha un solo digest e zero errori.

## Due invocazioni simultanee

| Worker/chiamata | Same-host p50 gruppo | Picco per host | Thread totali | Multi-host p50 gruppo | Picco globale | Picco per host |
|---:|---:|---:|---:|---:|---:|---:|
| 1 | 842,890 ms | 2 | 2 | 843,699 ms | 2 | 2 |
| 2 | 437,570 ms | 4 | 4 | 433,140 ms | 4 | 2 |
| 4 | 227,970 ms | 8 | 8 | 231,341 ms | 8 | 2 |
| 8 | 230,063 ms | 8 | 16 | 147,975 ms | 16 | 4 |
| 16 | 231,900 ms | 8 | 32 | 110,315 ms | 32 | 8 |
| 32 | 233,001 ms | 8 | 40 | 75,977 ms | 40 | 8 |

## Decisione

Non promuovere ancora `read_urls_html` a una classe positiva e non sostituire
il cap locale con un valore centrale fisso. Classe 2 perde throughput
multi-host; classe 3 conserva il throughput ma non contiene due invocazioni.

Due interventi vanno valutati separatamente:

1. ridurre senza perdita il pool della singola invocazione a
   `min(job, cap_globale, host_distinti * cap_per_host)`, eliminando i thread
   sicuramente bloccati nel profilo same-host;
2. progettare un budget per-host condiviso fra invocazioni/processi, oppure una
   serializzazione per insieme di host. Il registro attuale delle chiavi di
   concorrenza accetta una sola identita' e non rappresenta batch multi-host
   parzialmente sovrapposti.

Il primo e' risultato equivalence-preserving anche su HTML pesante ed e' stato
applicato nel solo calcolo del pool locale. Il secondo cambia la politica
trasversale e richiede decisione separata; non va nascosto in `HostThrottle` o
nel manifest.

## Riproduzione

```bash
python3 tests/benchmarks/tools/benchmark_read_urls_html.py \
  --workers 1,2,4,8,16,32 --concurrent-calls 1,2 \
  --profiles same_host,multi_host --iterations 6 --warmups 1 \
  --url-count 20 --server-count 5 --delay-ms 40
```
