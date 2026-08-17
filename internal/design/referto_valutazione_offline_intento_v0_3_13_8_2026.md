# Referto della valutazione offline intento v0.3

Data: 13 agosto 2026

## Esito semplice

La correzione del valutatore è riuscita con **zero errori di controllo**. Sono
stati rimossi 62 falsi errori dovuti soltanto a differenze di forma.

Il risultato sostanziale, però, resta negativo per il candidato B:

- A: 75/120 corretti;
- B: 25/120 corretti;
- differenza B−A: −50;
- controlli speciali B: 0/4;
- verdetto: `candidate_fail`.

È stata eseguita una sola valutazione offline 0.3 sui risultati GPU già
salvati. Non è stata effettuata alcuna nuova chiamata al modello e non è stata
usata la GPU.

## Correzione della metrica

La metrica principale è ora `semantic_exact_canonical`. Expected e actual
ricevono la stessa proiezione. La proiezione elimina soltanto:

- `data_from: []`;
- porte univocamente derivabili;
- outcome vuoti materializzati.

Non può cambiare root, route, ordine, collegamento, barriera, outcome non vuoto
o reason. La vecchia metrica resta come `semantic_exact_v0_2_raw`:

| Pannello canonico | Canonica v0.3 | Storica v0.2 |
|---|---:|---:|
| A | 75/120 | 29/120 |
| B | 25/120 | 9/120 |

La differenza è 46 casi per A e 16 per B: in totale 62 falsi negativi corretti.

## Dimensioni separate — pannello 120

| Dimensione | A | B | Denominatore |
|---|---:|---:|---:|
| root prodotta dal modello | 94 | 73 | 120 |
| root accettata | 91 | 37 | 120 |
| route esatta | 51 | 17 | 84 applicabili |
| composizione esatta | 58 | 23 | 84 applicabili |
| collegamenti dati esatti | 53 | 22 | 84 applicabili |
| consenso esatto | 0 | 0 | 3 applicabili |
| proprietà dei rami esatta | 0 | 0 | 3 applicabili |
| controllo di sistema esatto | 2 | 2 | 2 applicabili |

La vecchia lettura globale del consenso rimane 117/120 per entrambi, ma il
nuovo denominatore mostra chiaramente il problema reale: **0/3** barriere.

## Stato delle estrazioni — pannello 120

| Stato | A | B |
|---|---:|---:|
| rappresentabile valido | 70 | 27 |
| non rappresentabile valido | 46 | 33 |
| documento non valido | 4 | 57 |
| errore tecnico | 0 | 3 |

Root, stati e tutte le altre colonne storiche coincidono con l'output 0.2. La
modifica riguarda soltanto la misura dell'uguaglianza semantica.

## Pannelli separati e verdetto

- Canonico: 120 casi per braccio, senza compensazione.
- Speciale tipizzato: 4 casi per braccio; B resta 0/4.
- Legacy: 34 casi per braccio, invariato come Phase-1; A 25/34 e B 29/34.

I nove controlli critici restano nove. Le regressioni di B sono:
`semantic_exact_canonical`, `root_exact`, `correct_abstention` e
`technical_valid`. Il risultato legacy non compensa questi fallimenti.

## Replay e integrità

- replay pre-gold: 316/316 record;
- differenze diagnostiche tollerate e registrate: 1;
- differenze inattese: 0;
- prove finite pre-valutazione: 18/18;
- controllo post-valutazione: `error_count=0`;
- freeze 0.3 nuovamente verificato dopo la valutazione.

La tolleranza replay è generale: vale su qualsiasi record, in entrambe le
direzioni, soltanto per il booleano
`adapter_metadata.implicit_actions_ignored`. Campo assente, tipo diverso,
altro puntatore o modifica semantica bloccano il replay. La scansione
anti-hardcoding non trova indici, query, identificativi o hash del vecchio caso
speciale nel codice 0.3.

## Artefatti e impronte

- output 0.3:
  `internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1/live_evaluation_v0_3.json`
  — `f7d85c38474c477a95414de83fe563c8b1f81719318c357a83284cbcfa85582e`;
- controllo finale:
  `internal/tools/request_analysis_lab/prototypes/intent_shadow_v0_1/candidate_v0_1/live_evaluation_v0_3_selfcheck.json`
  — `a7a9f36b20d1d70ce75b2b565654f7abb223ff1f9b51b41ed08e7a4c2a72283a`;
- freeze 0.3:
  `73d156f7a2fde636262dadcacb366dd4ed274e83234be3fb8d89a3d62dbffb19`;
- evaluator 0.3:
  `bcec36f2d31156c2851650b675af1a883d28309b3dbbda569961f39cd7fbcd13`;
- replay gate 0.3:
  `50dfcd89e2c99a91d39c3399ba4b36b87c538304dc2a0a695d9964f6b66255d5`.

Impronte storiche confermate invariate:

- evaluator 0.2:
  `be49b085e22a089dda3c82a89dacddda218099d174b934adf4074bc0f71ee85c`;
- replay gate 0.2:
  `cf4b832b3e8eb951f80298dc902bbaf2d1162d95759fc3726f27f2462c4d0bdf`;
- output 0.2 riuscito:
  `6eee1f559b20f3145780ae9dca52eaf101df82f442214cdfec0751f50845d25d`;
- batch:
  `eac40d320a8e2d98b0a11ab2ed652cf9ecc64b4893fb2e658a9bbde50143ca0b`;
- sigillo batch:
  `8f58208fd6082b6fafda953882356c3f4d0084cf183aefeeaafb8b08847cde4c`;
- journal:
  `403135c52aff5852e782f6549bc55f0f08d64b6a92e7a8fb4b9c6374c7515c4e`;
- oracolo:
  `3e0d1155fad7b2920b3b04bb6e617747849c8fe4a1abfea5920f1b51dba6a123`;
- freeze oracolo:
  `eb9d051af8cee705536348ac602f7888595e5046937ec2ef09a632fddba63a03`.

## Revisione avversariale

La revisione ha cercato quattro modi di falsare il miglioramento:

1. normalizzare solo actual: escluso, la stessa funzione riceve entrambi;
2. cancellare differenze semantiche: escluso dai mutanti di root, route,
   ordine, edge e reason;
3. nascondere le barriere nel totale: escluso dal denominatore 0/3;
4. usare un'eccezione del vecchio record: escluso dai test su record diversi e
   dalla scansione anti-hardcoding.

Il controllo finale ricalcola dal solo output le 248 righe tipizzate, confronta
le colonne non modificate con la versione 0.2, verifica il pannello legacy e
conferma zero errori. Resta necessaria la revisione indipendente prima di
aggiornare checkpoint e handover.

Produzione, runtime e banco congelato sono intatti. Nessuna rete, servizio,
GPU o commit è stata usata in questa fase.
