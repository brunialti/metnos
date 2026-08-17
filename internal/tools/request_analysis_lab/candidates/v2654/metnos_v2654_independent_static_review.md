# Review statica indipendente V26.5.4

Data: 9 agosto 2026.

Verdetto: **STATIC BLOCK**.

I replay dichiarati passano: **24/24**, **106/106** e **15/15**. Il limite
facade da 1.500.000 byte non è però un limite di allocazione: il frame viene
copiato, serializzato integralmente con `json.dumps` e codificato UTF-8 prima
del controllo sulla lunghezza. I test verdi non coprono questo ordine.

## Evidenza

La facade applica soltanto limiti locali a richiesta, profondità, nodi e
singole stringhe (`metnos_v2654_facade.py:47-74`). Dopo lo snapshot completo
costruisce l'envelope e materializza sia la stringa JSON sia i byte UTF-8 alle
righe 99-112; il rifiuto oltre soglia arriva soltanto alle righe 115-116. Le
chiavi degli oggetti non hanno neppure un limite individuale o cumulativo.

Un probe limitato con otto valori ASCII da 200.000 caratteri, tutti ammessi
singolarmente, ha prodotto una request canonica da 1.600.143 byte e solo dopo
ha ricevuto `ValueError`. `tracemalloc`, avviato dopo la costruzione
dell'input, ha osservato un picco di 3.201.064 byte; il worker non è partito.

## Replay freschi

| Controllo | Esito | SHA-256 stdout |
|---|---:|---|
| self-test V26.5.4 | 24/24 | `dbc99bbf020de9de66127144150152803b916dde23bc013af72a2112b5a03d2e` |
| suite compatta | 106/106 | `3306d299b18726da1b903643d9c85763c05ec57680259c2ab05ddf0aa8bdb68f` |
| audit anti-contaminazione | 15/15 | `50e1c5931fe60bfb1007bf5624b1f836214c43fab98194350b66921549487c92` |

Il self-test fresco ha misurato p50 89,230 ms su sette processi. Il suo stdout
include misure dinamiche e non è atteso byte-identico al risultato autore.
Suite 106 e audit 15 sono replay ereditati e non esercitano il confine memoria
della facade.

## Correzione minima

Durante lo snapshot va mantenuto un conteggio esatto e incrementale dei byte
del JSON canonico UTF-8, includendo sintassi, envelope fisso, richiesta
originale, chiavi, valori ed escape. Il conteggio deve usare gli stessi
parametri della serializzazione (`ensure_ascii=False`, separatori compatti),
rifiutare appena supera 1.500.000 byte e gestire UTF-8 strict: `len(str)` non è
sufficiente. Solo dopo il conteggio può essere chiamato `json.dumps`; la
lunghezza prodotta deve restare una postcondizione uguale al conteggio.

Servono regressioni per aggregati e chiavi oltre budget, ASCII/Unicode/escape,
surrogati non validi e soglie esatte 1.500.000/1.500.001, con una canary che
provi che `json.dumps` non viene raggiunto nel caso oltre budget.

Facade, worker, manifest, self-test e risultato autore sono rimasti invariati.
Nessuna rete, modello, freeze, gate o esecuzione live è stata usata o
autorizzata. Il report leggibile a macchina è
`metnos_v2654_independent_static_review.json`.
