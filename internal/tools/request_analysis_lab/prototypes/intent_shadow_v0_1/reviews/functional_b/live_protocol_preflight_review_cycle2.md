# Revisione funzionale indipendente — preflight live, cycle 2

Data: 2026-08-13  
Revisore: `reviewer_b_independent`  
Perimetro: sola lettura; nessun POST, endpoint, inferenza, GPU, restart, servizio modificato, autorizzazione o marker creato.  
Esito: **FAIL operativo — 35/36 PASS, 1/36 FAIL; non `ready_to_arm`.** Il difetto funzionale B-01 del cycle 1 è corretto; resta esclusivamente il controllo host n. 10, fuori dalla namespace del revisore e demandato a root.

## Matrice dei 36 controlli

| # | Controllo | Esito | Evidenza cycle 2 |
|---:|---|:---:|---|
| 1 | Identità braccio A | PASS | Snapshot chiuso: `runtime.intent_extractor.extract_intent`, workload `intent.extract`, fast/micro, scaffold off. |
| 2 | A non è il turno runtime completo | PASS | Adapter A usa estrattore, prompt/lessico e conversione al registro; nessun dispatch di tool o azione. |
| 3 | Prompt A v4 IT/EN e dipendenze | PASS | Prompt role v4, lingue IT/EN e insieme chiuso delle sorgenti necessarie restano nel snapshot. |
| 4 | Identità braccio B | PASS | Candidate 0.1 diretto su prompt/schema congelati. |
| 5 | Endpoint/modello/backend comuni | PASS | Protocollo chiude `localhost:8080`, API model `local`, Qwen3.6-35B-A3B e llama.cpp build 1422 per entrambi. Nessuna connessione eseguita. |
| 6 | Profilo comune | PASS | Temperature 0, seed 42, max 4000, timeout 120, thinking off, stream off e cache prompt on. |
| 7 | Serialità e zero retry | PASS | Un solo ciclo sincrono sul manifest; nessun thread/process/async e nessun retry implicito. |
| 8 | Sorgenti A hash-pinned | PASS | Snapshot e freeze mantengono membership chiusa e verifica byte-per-byte delle sorgenti più hash logico del lessico. |
| 9 | Pesi, binario, build e config | PASS CON LIMITE | Pin invariati: pesi `0b2152…c58b`, binario `14f86e…7c78`, build 1422 e unit/drop-in/env/tier/suprastructure. Non è stato ricalcolato più volte il peso; si usa l'attestazione read-only già verificata dal primo revisore, come ordinato. |
| 10 | Processo backend host attivo e coerente | **FAIL NON VERIFICATO** | Dal sandbox `ps` non vede il namespace host e systemd risponde `Failed to connect to bus: Operation not permitted`. La richiesta di sola introspezione host di PID/cgroup/argv non è stata autorizzata prima dello stop. Nessuna evidenza contraria e nessun restart necessario, ma questo revisore non può attestare processo attivo, exe/modello/porta/argv. |
| 11 | Manifest 316 | PASS | Struttura chiusa: `(120+4+34)×2=316`, 158 query e due bracci. |
| 12 | Pannelli 120+4+34 | PASS | 240 record canonical, 8 typed, 68 legacy; pannelli distinti. |
| 13 | Coppie adiacenti | PASS | Ogni caso occupa gli offset contigui `2i` e `2i+1`. |
| 14 | AB pari / BA dispari | PASS | Ordine deterministico alternato; 158 richieste per braccio. |
| 15 | Query identica per coppia | PASS | Entrambi i bracci derivano dallo stesso caso, query e query hash. |
| 16 | Manifest query-only | PASS | `gold_fields_present=false`; nessun expected/gold nei record o moduli pre-gold. |
| 17 | Unico ramo di rete | PASS | Solo `urllib_transport` contiene `urlopen`, raggiungibile da `--execute-once`. |
| 18 | Preflight non apre socket | PASS | Il ramo `--preflight` verifica artefatti/guard; non invoca il trasporto. |
| 19 | Nessuna azione reale/produzione | PASS | Bracci e runner manipolano solo richieste e risultati; nessun executor, tool action o restart. |
| 20 | Marker prima del socket | PASS | Creazione esclusiva, write e fsync precedono il primo trasporto. |
| 21 | Consumo al primo POST accettato | PASS | Stato e contatore di consumo cambiano solo dopo la prima risposta HTTP accepted. |
| 22 | Tentativo ambiguo impedisce rerun | PASS | Il marker esclusivo resta anche su errore iniziale; qualsiasi artefatto live preesistente chiude il guard. |
| 23 | Trasporto/timeout ferma e salva | PASS | Record dell'errore, journal e batch parziale; nessun retry. |
| 24 | JSON/semantica invalida conta e continua | PASS | Un POST accepted con wrapper/output invalido registra errore tecnico e passa al record successivo. |
| 25 | Evidenza grezza e replay | PASS | Body/header/hash/content sono conservati e l'evaluator riproduce l'estrazione. |
| 26 | Journal append-only | PASS | `O_APPEND`, fsync per record e hash journal nel seal. |
| 27 | Checkpoint e seal atomici | PASS | Temporaneo + fsync + replace + fsync directory; seal separato lega batch, journal, marker e auth. |
| 28 | Autorizzazione esterna one-shot | PASS | Schema chiuso lega protocollo/freeze/manifest, audit B, hash report, root authorization e nonce. |
| 29 | Autorizzazione assente | PASS | `live_run_authorization_v0_1.json` assente durante il controllo. |
| 30 | Nessun consumo anticipato | PASS | Nessun artefatto `live_run_*` o evaluation osservato; `--execute-once` non eseguito. |
| 31 | Gold solo dopo batch completo e seal | PASS | Batch, seal, journal, marker, 316 record e replay sono validati prima di `_verified_gold`. |
| 32 | Oracle freeze gate | PASS | Verificatore canonico deve restituire `ok/0` contro oracle e freeze prima dell'apertura del gold. |
| 33 | Nove colonne pubblicate | PASS | Aggregato e report includono le nove critiche, oltre a `incorrect_abstention` come colonna informativa. |
| 34 | Gate canonico e due test nuovi | PASS | `CRITICAL_NO_REGRESSION_COLUMNS` contiene esattamente nove voci incluso `correct_abstention`; protocol loader, evaluator e report usano la stessa costante. I test nuovi coprono regressione isolata (non-pass) e assenza regressioni (pass), verificando identità degli insiemi. B-01 chiuso. |
| 35 | Legacy separato | PASS | Phase-1 34 ha righe/aggregati propri, nessuna conversione o compensazione col verdetto tipizzato. |
| 36 | Sigilli e suite live aggiornate | PASS | Esecuzione offline unica con cache hash condivisa: builder/sigilli `ok`; verifier `ok`, zero errori, 158 query/316 richieste e rete/inferenza false; test live 9/9; mutazioni 41/41 negative respinte e 5/5 positive accettate; preflight `prepared_not_authorized`, authorization assente, guard libera, 316, rete/GPU false. |

## Chiusura del difetto cycle 1

**B-01 chiuso.** `correct_abstention` è la terza voce dell'unica costante canonica di nove colonne. `load_protocol` richiede membership esatta e senza duplicati; `typed_verdict` rifiuta un set diverso, calcola le regressioni su quella costante e la riporta nell'output. Il test negativo minimo imposta A=5 e B=4 soltanto su `correct_abstention` e vieta `candidate_pass`; il test positivo richiede il pass quando tutte e nove non regrediscono.

Non sono emersi nuovi difetti funzionali nel codice ispezionato.

## Blocchi residui

1. Attestare read-only, dal namespace host, che `llama-server.service` è attivo e che MainPID/cgroup, `/proc/<pid>/exe` e argv coincidono con binario, modello, porta 8080 e configurazione congelati. Processo attivo coerente è sufficiente: non serve né è autorizzato un restart.

## Decisione e comando

**Non `ready_to_arm` in questa revisione.** Nessuna authorization o consumption marker deve essere creato finché i due blocchi non sono chiusi da evidenza read-only e nuova attestazione indipendente.

Comando unico previsto, autorizzabile soltanto dopo tale chiusura e una authorization one-shot separata:

```bash
cd /opt/metnos/internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1 && python3 -B live_runner.py --execute-once --authorization live_run_authorization_v0_1.json
```
