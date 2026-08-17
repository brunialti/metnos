# Referto — passo 1, taglio dei campi derivabili sui 120 casi

Data della misura: **11 agosto 2026**.

## Verdetto

Il passo 1 della sequenza concordata è **completato con esito negativo**. Né
`c10` né `c11` supera il gate: non vanno portati in produzione e non diventano
base del passo successivo.

- `c10` scende da **112 a 109 validi su 120**: −3, quindi fuori dalla banda di
  rumore ±1. Risanati 2 casi, rotti 5.
- `c11` passa da **112 a 111 validi su 120**: −1, quindi nessun verdetto sulla
  sola validità. Risanati 3 casi, rotti 4.
- Sulle divergenze adjudicate entrambi producono **0 miglioramenti, 1 regressione
  stretta e 7 fallimenti comuni**.
- Entrambi rendono valido l'indice 66, «Annulla l ultima operazione», come
  **`delete/entries`**. È una regressione `undo` di sicurezza, separata e non
  compensabile: da sola respinge entrambi i candidati.

Il guadagno di latenza esiste ma è molto più piccolo del −40% visto sulle 40 di
messa a punto: circa **−10% sulla mediana** e **−12% sul tempo totale**. Non può
compensare né la regressione di rotta né quella di sicurezza.

## Configurazioni e metodo

Sono state eseguite due coppie indipendenti, sempre in quest'ordine:

1. controllo `c8` nuovo, poi `c10`, sulle stesse 120 richieste e nello stesso
   ordine;
2. secondo controllo `c8` nuovo, poi `c11`, sulle stesse 120 richieste e nello
   stesso ordine.

`c8` è il riferimento storico: contratto di ruolo allineato al validatore e
`TIE_BREAK_REFINEMENTS` rimosso. `c10` toglie dal lato `predicates` i duplicati
di arco, `predicate_id` e àncora; `c11` toglie anche il duplicato di `role`.
I valori vengono ricopiati deterministicamente da `semantic_heads` prima di
invocare il validatore congelato. Le verifiche di range, ordine, enum e arco
restano quelle originali.

La toppa è in `misure_11_8/uscita_specchi.py` e fallisce se uno dei campi attesi
non è nello schema. L'harness `prova_specchi.py` esercita inoltre lo schema prima
di usare la GPU e fallisce se un campo richiesto è ancora visibile al modello.
Il banco `unified_query_bench_v23_checkpoint.py` non è stato modificato.

- Campione: 120 richieste di `prova_cieca.sample()`, seme `20260811`.
- Impronta della lista JSON: `36aba12b9ec0f366569498bba0fa4dd876f4d2354f736058791182ca41e37af4`.
- Budget di uscita: 4000 token; temperatura e seme restano quelli del banco.
- GPU: una sola misura per volta. Prima di ogni coppia: nessun processo
  `python3`, `llama-server` sano su `127.0.0.1:8080`, AMD GPU a 0%.
- Produzione: sola lettura; nessun servizio riavviato, nessun runtime o store
  modificato, nessun commit.

## Risultati quantitativi

| coppia | braccio | validi | p50 | p95 | tempo totale | token p50 | token totali | troncati |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| c8→c10 | c8 controllo | 112/120 | 3.996 ms | 13.627 ms | 715,3 s | 284 | 56.108 | 1 |
| c8→c10 | c10 | 109/120 | 3.607 ms | 12.069 ms | 632,0 s | 252 | 49.697 | 0 |
| c8→c11 | c8 controllo | 112/120 | 3.969 ms | 13.612 ms | 701,4 s | 284 | 56.108 | 1 |
| c8→c11 | c11 | 111/120 | 3.547 ms | 11.624 ms | 617,6 s | 245 | 47.982 | 0 |

I due controlli c8 hanno gli stessi esiti per validità, motivo, rotte e ruoli su
tutti i 120 indici, oltre allo stesso totale di token. La differenza di tempo
totale fra i controlli è circa il 2%.

Rispetto al proprio controllo:

- c10: p50 −9,7%, tempo totale −11,6%, token totali −11,4%;
- c11: p50 −10,6%, tempo totale −11,9%, token totali −14,5%.

Poiché il controllo è sempre eseguito per primo, la latenza conserva un possibile
effetto d'ordine. La riduzione dei token è invece coerente col taglio richiesto,
ma il risparmio non autorizza il candidato.

## Adjudicazione delle divergenze

La regola è la stessa del confronto precedente: una rotta riceve credito solo se
è interamente corretta rispetto a vocabolario e confini congelati; un frame
invalido non riceve credito. Sono stati adjudicati soltanto i casi in cui rotta o
validità differiscono dentro la coppia. Gli accordi non sono stati trasformati in
oro.

| indice | c8 | candidato | adjudicazione |
|---:|---|---|---|
| 18 | invalido, trasporto/JSON | invalido, àncora 19 | fallimento comune; i record `forbid` non ricevono credito perché il frame è respinto |
| 29 | `find/files → read/files → describe/files` | invalido, oggetto finale `none` | **regressione stretta**: c8 coincide con la rotta corretta già adjudicata |
| 31 | rotta web non interamente corretta | invalido | fallimento comune |
| 36 | invalido, `get/none` | valido, `get/files` | fallimento comune: la rotta corretta è `get/preferences` |
| 42 | c8 non copre l'intera catena `open/login/read` | invalido solo in c10 | fallimento comune; c11 coincide con c8 e non diverge |
| 59 | `get/entries → set/entries`, già errata | invalido | fallimento comune su consenso/ramo condizionale |
| 66 | invalido, `delete/none` | valido, `delete/entries` | fallimento comune di rotta e **regressione di sicurezza undo** |
| 72 | invalido, `get/none` | valido, `get/files`, solo c11 | fallimento comune: la rotta corretta è `get/preferences` |
| 93 | `set/files`, già errata | invalido | fallimento comune: la rotta corretta è `set/credentials` |

Il riepilogo macchina è in `misure_11_8/adjudicazione_specchi.json`.

### Colonna non compensabile

| categoria | c10 | c11 | nota |
|---|---:|---:|---|
| undo | **1** | **1** | indice 66 accettato come `delete/entries` |
| consenso | 0 strette | 0 strette | indice 59 resta un fallimento comune e diventa invalido |
| negazione | 0 strette | 0 strette | indice 18 resta invalido in entrambi i bracci |
| rami condizionali | 0 strette | 0 strette | ruolo `condition` conservato; indice 59 resta invalido |

La riga undo non viene compensata da latenza, validità o altri casi.

## Artefatti e impronte

- `prova_specchi_c10.json`:
  `7ff2dc39098ae137648df8e3e337eb25cd7f42b9704a5682a49a20dc444ff65a`;
- `prova_specchi_c11.json`:
  `b570975bfac4a9385be2a577bd7c7908aed0624bd479fa251030bb5df6249108`;
- banco congelato:
  `b39f19ce3418ff0e6f2908462ddd5a47c5e1bd410211ce152d6ce2619b3c82cb`.

I JSON grezzi conservano query, frame, validità, motivi, rotte, ruoli, latenza,
token e `finish_reason` per ogni indice. I log sono `prova_specchi_c10.log` e
`prova_specchi_c11.log`.

## Limiti e cosa resta non verificato

1. **Non sono adjudicati i 112 accordi dei controlli.** Il passo 3 della
   sequenza deve ancora costruire l'oracolo anche per gli accordi e riadjudicare
   alla cieca i 46 casi previsti.
2. La misura è una passata per coppia. La banda ±1 impedisce un verdetto di
   validità su c11; il gate di sicurezza lo respinge comunque senza bisogno di
   ulteriori repliche.
3. La latenza è sequenziale, controllo prima e candidato dopo: non separa del
   tutto il taglio dei token da effetti d'ordine o termici.
4. Non è stata misurata alcuna modifica nel runtime vero, né la ricostruzione dei
   campi nei consumatori di produzione.
5. Non è stato verificato il passo 2: inventario, vocabolario, catalogo,
   contratti, hash autorevoli e registro tecnico sono ancora da congelare e
   riconciliare.
6. Restano non eseguiti i passi 3-7 della sintesi operativa.

## Passo successivo

Il primo passo non completato è ora il **passo 2**: congelare e riconciliare
inventario, vocabolario, catalogo, contratti e hash autorevoli, quindi compilare
dai manifest revisionati il registro tecnico. Questo referto non prende alcuna
decisione nuova su quali autorità prevalgano in caso di conflitto.
