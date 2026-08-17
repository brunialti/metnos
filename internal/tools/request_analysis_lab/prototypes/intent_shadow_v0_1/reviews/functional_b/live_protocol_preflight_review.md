# Revisione funzionale indipendente — preflight protocollo live

Data: 2026-08-13  
Revisore: `reviewer_b_independent`  
Perimetro: sola lettura del candidato e degli artefatti live chiusi; nessun endpoint, POST, GPU, servizio, produzione o banco; nessun marker di autorizzazione/consumo creato.  
Esito: **FAIL — 34/36 PASS, 2/36 FAIL; non `ready_to_arm`.** Un solo difetto di codice (n. 34) e un blocco di stato live (n. 10).

## Matrice finita (36 controlli)

| # | Controllo | Esito | Evidenza sintetica |
|---:|---|:---:|---|
| 1 | Identità braccio A | PASS | Snapshot fissa `runtime.intent_extractor.extract_intent`, workload `intent.extract`, tier fast/micro, scaffold off. |
| 2 | A non è il runtime turno completo | PASS | `live_arm_current.py` importa il solo estrattore e le dipendenze necessarie, usa risposta primaria e adapter di registro; nessuna azione/tool del turno. |
| 3 | Prompt A v4 IT/EN e dipendenze | PASS | Prompt role `intent_extractor_v4`, lingue `it/en`; 14 sorgenti repo, prompt e lessico logico sono hash-pinned. |
| 4 | Identità braccio B | PASS | `live_arm_candidate.py` usa prompt/schema congelati del candidate 0.1 e l'estrattore diretto. |
| 5 | Stesso endpoint/modello/backend | PASS | Entrambi i record sono OpenAI-compatible su `localhost:8080`, model API `local`, Qwen3.6-35B-A3B, llama.cpp build 1422. |
| 6 | Profilo uguale e chiuso | PASS | Ogni request ha temperature 0, seed 42, max_tokens 4000, thinking false, stream false, cache prompt true; timeout runner 120 s. |
| 7 | Zero retry e serialità | PASS | Singolo ciclo sincrono su manifest; nessun thread/process/async/retry nel runner. |
| 8 | Hash sorgenti A correnti | PASS | Snapshot e freeze enumerano e verificano l'insieme chiuso delle sorgenti; `verify_control_environment` confronta ogni byte. |
| 9 | Hash pesi/binario/build/config | PASS | Freeze lega pesi SHA-256 `0b2152…c58b`, binario `14f86e…7c78`, unit/drop-in/env/tier/suprastructure; il verificatore confronta i file correnti. |
| 10 | PID/processo backend corrente | FAIL LIMITATO | Al momento dell'audit non risultava alcun processo llama-server visibile; il protocollo non congela PID/argv e il preflight non li verifica. Il controllo dei file/build passa, ma l'identità del processo vivo non è attestabile. Non è il difetto funzionale del verdetto descritto sotto, ma impedisce comunque di armare finché il processo non è verificato immediatamente prima dell'autorizzazione. |
| 11 | Manifest esatto 316 | PASS | 158 query × 2 bracci; copertura `(sample_index, arm)` completa e unica. |
| 12 | Pannelli 120+4+34 | PASS | 240 record canonical, 8 typed-control, 68 legacy; totale 316. |
| 13 | Coppie adiacenti | PASS | Ogni `sample_index` occupa esattamente gli offset `2i,2i+1`. |
| 14 | AB pari / BA dispari | PASS | `arm_order`: AB su indici pari, BA su dispari; 79 coppie per ordine, quindi 158 richieste A e 158 B. |
| 15 | Query identica nella coppia | PASS | I due record sono ricostruiti dalla stessa query/hash del caso; il loader ne verifica identità e posizione. |
| 16 | Manifest query-only | PASS | `gold_fields_present=false`; user message uguale alla query; static scan vieta oracle/gold nei moduli pre-gold. |
| 17 | Runner unico ramo di rete | PASS | Solo `urllib_transport` effettua `urlopen`; viene passato soltanto da `--execute-once`. |
| 18 | Preflight offline | PASS | Il ramo `--preflight` chiama verificatore/guard e non il trasporto; ricerca statica non trova altre aperture socket. |
| 19 | Nessuna azione reale/produzione | PASS | I bracci generano e analizzano solo payload; nessun dispatch di tool, mutazione produzione o riavvio. |
| 20 | Marker prima del primo socket | PASS | `execute_manifest` usa creazione esclusiva e fsync del consumption marker prima del ciclo/trasporto. |
| 21 | Consumo al primo POST accettato | PASS | Stato/contatore diventano `measurement_consumed_first_post_accepted` solo su prima risposta accepted. |
| 22 | Primo tentativo ambiguo vieta rerun | PASS | Anche un rifiuto/timeout lascia il marker esclusivo; ogni artefatto live preesistente blocca una nuova esecuzione. |
| 23 | Trasporto/timeout ferma | PASS | Risposta non accepted viene journalizzata, interrompe il ciclo e produce batch parziale. |
| 24 | JSON/semantica invalida continua | PASS | Errori wrapper/adapter sono registrati; i POST accettati successivi proseguono fino a 316. |
| 25 | Risposta grezza e hash | PASS | Body, header, SHA-256 e content sono salvati; il valutatore ricostruisce e verifica l'estrazione. |
| 26 | Journal append-only | PASS | Apertura `O_APPEND`, scrittura e fsync per record; seal lega l'hash del journal. |
| 27 | Checkpoint/seal atomici | PASS | File temporaneo, fsync, `os.replace`, fsync directory; seal separato lega batch/journal/marker. |
| 28 | Autorizzazione esterna one-shot | PASS | Schema chiuso lega protocollo/freeze/manifest, audit indipendente, hash report, root authorization e nonce. |
| 29 | Autorizzazione attualmente assente | PASS | Nessun `live_run_authorization_v0_1.json`; verificatore la considera errore se appare prima dell'arming. |
| 30 | Nessun consumo anticipato | PASS | Nessun `live_run_*` o evaluation artefact presente; l'audit non ha eseguito `--execute-once`. |
| 31 | Gold solo post batch completo e seal | PASS | `evaluate` valida batch, seal, journal, marker, 316 record e replay prima di `_verified_gold()`. |
| 32 | Gate oracle/freeze | PASS | `_verified_gold` carica il verificatore canonico e richiede report `ok/0` contro oracle e freeze prima di aprire gold. |
| 33 | Colonne approvate pubblicate | PASS | Aggregati tipizzati espongono tutte le colonne del protocollo, incluso `correct_abstention`. |
| 34 | Regola di verdetto approvata | **FAIL** | Il protocollo congela `correct_abstention` tra le colonne critiche, ma `live_evaluator.py:289-292` la omette dal tuple `critical`; una regressione può quindi produrre `candidate_pass`. |
| 35 | Legacy separato e non compensabile | PASS | 34 casi valutati esclusivamente col pannello Phase-1; output, aggregati e verdetto tipizzato restano separati. |
| 36 | Freeze, suite e comando chiuso | PASS | Protocol/snapshot/manifest/freeze sono hash-pinned; suite core dichiarate passano. Il comando live è unico e distinto dal preflight. |

Nota sul n. 10: viene mantenuto come blocco operativo FAIL perché la richiesta richiede controllo del PID corrente. Il difetto di codice resta uno solo, al n. 34.

## Difetto bloccante

**B-01 — gate no-regression incompleto.** Il protocollo congelato include nove colonne critiche (`live_measurement_protocol_v0_1.json:50-59`); l'evaluator ne controlla otto (`live_evaluator.py:289-293`) e omette `correct_abstention`. Esempio minimo: `semantic_exact` B supera A di 3, B fa 4/4 nei typed controls, le otto colonne implementate non regrediscono, ma `correct_abstention` passa da A=5 a B=4. Il codice restituisce `candidate_pass`, mentre la regola approvata richiede che la regressione blocchi il pass.

Correzione suggerita, non applicata: aggiungere `"correct_abstention"` al tuple `critical` in `live_evaluator.py`; aggiungere un test offline che costruisca aggregati con la sola regressione di questa colonna e attenda `candidate_fail` (o comunque non `candidate_pass`); rigenerare protocol freeze/hash locali e rieseguire verificatore, test positivi/mutazionali e preflight.

## Limiti e suite

- Suite concluse: builder query `ok` (120/4/34), core `7/7` e round-trip `124`, mutazioni core `89/89` negative e `7/7` positive, candidate verifier `ok`, dry-run `158` con `gpu_mode_present=false` e `network_transport_present=false`.
- Verifiche live concluse: verifier `ok` (158 query/316 richieste, zero errori, rete/inferenza false); test positivo `7/7`; preflight `prepared_not_authorized`, authorization assente, guard libera, rete/GPU false. Per l'ordine di consegna immediata, builder live e suite mutazionale live sono stati interrotti e non vengono dichiarati passati; non sono stati rilanciati.
- Warning del test live positivo: impossibilità prevista di aprire il log di stato in filesystem read-only e fallback lessicale multilingua; nessuna chiamata rete/GPU osservata.
- Nessuna autorizzazione, marker di consumo, journal, checkpoint, partial, sealed batch o evaluation è stata creata nel candidato.

## Decisione

**Non `ready_to_arm`.** Prima dell'arming servono la correzione B-01 con rigenerazione del freeze e una verifica immediata del processo backend vivo (PID, `/proc/<pid>/exe` e argv coerenti con endpoint/modello/config congelati). Dopo tali modifiche è necessaria una nuova revisione indipendente; questo report non autorizza alcun POST.

Comando esatto autorizzabile solo dopo nuova revisione PASS e creazione separata dell'autorizzazione one-shot:

```bash
cd /opt/metnos/internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1 && python3 -B live_runner.py --execute-once --authorization live_run_authorization_v0_1.json
```
