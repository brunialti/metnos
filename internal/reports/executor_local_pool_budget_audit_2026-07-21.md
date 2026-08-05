# Verifica pool locali e budget centrale — 2026-07-21

## Esito

Il budget `METNOS_EXECUTOR_ASSIGNED_WORKERS` governa correttamente gli executor
generati e i consumer gia' migrati, ma **non governa i cinque pool locali
legacy**. E' il comportamento conservativo dichiarato da ADR 0196 e da
`executor_scheduler.assigned_worker_environment()`: un manifest privo di
`[execution]` non riceve l'iniezione e conserva la semantica storica.

Forzare comunque `METNOS_EXECUTOR_ASSIGNED_WORKERS=1` nel processo non cambia i
cap legacy: sul nodo a 32 CPU logiche la prova dinamica ha restituito
`64 / 32 / 16 / 16` worker. Non si puo' quindi affermare che il budget centrale
impedisca oggi la sovrapposizione dei pool preesistenti.

## Inventario completo

La ricerca su tutti gli executor attivi trova cinque implementazioni con pool
proprio e nessun altro `ThreadPoolExecutor`, `ProcessPoolExecutor`,
`multiprocessing.Pool`, `asyncio.gather` o `threading.Thread`.

| Executor | Pool per invocazione | Cap locale sul nodo | Fonte del cap | Rispetta il budget centrale |
|---|---:|---:|---|---|
| `find_urls` | 1 per batch BFS/prefetch | 64 | `METNOS_FIND_URLS_GLOBAL_MAX`, altrimenti CPU×4 con cap 64 | no |
| `read_urls_html` | 1 fetch + possibile 1 iframe, non simultanei | 32 | `METNOS_READ_URLS_GLOBAL_MAX`, altrimenti CPU×4 con cap 32 | no |
| `read_urls_pdf` | 1 | 16 | `METNOS_READ_URLS_GLOBAL_MAX`, altrimenti CPU×2 con cap 16 | no |
| `compute_files_loc` | 1 | 16 | `METNOS_COMPUTE_LOC_WORKERS`, altrimenti CPU×2 con cap 16 | no |
| `create_images_indices` | 1 | 1 default, 8 massimo | `METNOS_VLM_PARALLEL` | no |

I thread sono creati lazy dal `ThreadPoolExecutor` e il numero effettivo resta
limitato dagli elementi del batch. I throttle per host limitano le operazioni
remote, non il numero complessivo di pool concorrenti fra turni distinti.

## Evidenza

Prova dinamica, con budget centrale forzato a uno:

```text
{"central_budget":"1","compute_files_loc":16,"find_urls":64,
 "read_urls_html":32,"read_urls_pdf":16}
```

La scansione dei cinque manifest non trova alcuna tabella `[execution]`; quindi
`assigned_worker_environment()` restituisce `{}` prima ancora di avviare il
sottoprocesso. La lettura dei call-site conferma inoltre che nessuno dei cinque
importa `executor_workers.assigned_workers()` o
`executor_helpers.assigned_workers()`.

Al contrario:

- `runtime/extract_entries.py` usa `executor_workers.assigned_workers()` e
  `map_ordered()`;
- gli script Google Workspace usano lo stesso adapter, con fallback seriale;
- il contratto degli executor generati prescrive
  `executor_helpers.assigned_workers()` e una `[execution]` seriale.

## Rischio reale

Dentro una singola pipeline i cinque executor legacy restano barriere seriali,
perche' non sono ammessi nelle wave parallele. Turni HTTP distinti possono
pero' sovrapporsi: lo scheduler limita le invocazioni, ma non somma i worker
interni che questi executor scelgono autonomamente. Il caso peggiore teorico e'
quindi il prodotto fra invocazioni contemporanee e cap locale; il numero reale
dipende dalle dimensioni dei batch e dai throttle di dominio.

## Decisione operativa

Nessuna migrazione automatica in questa tranche. Sostituire i cap locali con un
budget che oggi vale `1` eliminerebbe speedup gia' misurati; promuovere subito
le classi positive ammetterebbe invece nuova concorrenza fra invocazioni e
passi. Entrambe sono modifiche semantiche e richiedono il ciclo ADR 0196 per
singolo executor: baseline p50/p95 e memoria, equivalenza ripetuta, errori
parziali/timeout/ordine, carico simultaneo e solo infine modifica firmata del
manifest.

Il candidato piu' semplice per una futura prova isolata e'
`compute_files_loc`: output ordinato e somme commutative, nessuna rete e
benchmark storico disponibile. Non va promosso senza una nuova misura sul
workload reale e senza verificare l'interazione con le wave read-only.
