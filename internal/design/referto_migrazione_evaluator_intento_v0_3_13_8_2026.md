# Referto pre-valutazione — migrazione evaluator intento v0.3

Data: 13 agosto 2026

## Esito pre-valutazione

La versione 0.3 è pronta e congelata nel solo laboratorio. Le 18 prove finite
sono superate con zero errori. L'oracolo non è stato aperto dalle prove e la
valutazione 0.3 non è ancora stata eseguita al momento di questo referto.

Nessun file 0.2 è stato modificato. Sono rimasti invariati anche raw, batch,
journal, sigillo, marker, protocollo, autorizzazione, oracolo, expected,
runtime e risultati storici.

## Correzioni introdotte

1. Il replay tollera una differenza soltanto nel booleano diagnostico
   `adapter_metadata.implicit_actions_ignored`. La regola vale allo stesso modo
   per ogni record, in entrambe le direzioni, e registra ogni tolleranza. Non
   contiene indici, query, identificativi o hash di un caso particolare.
2. La metrica principale `semantic_exact_canonical` applica la stessa
   proiezione a expected e actual. Può eliminare soltanto `data_from: []`, porte
   derivabili in modo univoco e outcome vuoti materializzati.
3. La vecchia uguaglianza byte-semantica resta visibile come
   `semantic_exact_v0_2_raw`, ma non decide il verdetto.
4. Radice prodotta dal modello e radice accettata sono separate. Sono esposte
   anche rotta, composizione e collegamenti dati.
5. Consenso, proprietà dei rami e controllo di sistema riportano sia il totale
   globale sia il denominatore realmente applicabile. I tre casi con barriera
   non possono più essere nascosti dal totale 120.
6. I pannelli 120, 4 e 34 restano separati. I 34 casi legacy restano Phase-1 e
   non compensano gli altri pannelli. I nove controlli critici restano nove;
   soltanto il primo usa la metrica semantica corretta.

## Risultati attesi da verificare una sola volta

L'audit di migrazione prevede:

- 62 falsi negativi di sola rappresentazione corretti;
- pannello canonico: A 75/120 e B 25/120;
- colonna storica preservata: A 29/120 e B 9/120;
- controlli speciali B: 0/4;
- differenza B−A: −50;
- verdetto ancora `candidate_fail`;
- radice, stato e tutte le altre colonne 0.2 invariati;
- consenso e proprietà dei rami: 0/3 sul denominatore applicabile per entrambi
  i bracci.

Questi numeri non sono ancora un nuovo risultato: sono condizioni che il
controllo post-valutazione dovrà confermare leggendo l'unico output 0.3.

## Prove pre-valutazione

Le 18/18 prove coprono:

- tolleranza `false→true` e `true→false` su record diversi;
- più tolleranze visibili separatamente;
- rifiuto di campo assente, tipo non booleano, altro puntatore, modifica
  semantica e identità non chiusa;
- simmetria della proiezione e conservazione di root, route, ordine,
  collegamenti e reason;
- nove controlli critici e uso della metrica canonica nel verdetto;
- scansione anti-hardcoding;
- freeze chiuso e privo di fonti gold;
- arresto prima dell'oracolo se il freeze fallisce;
- replay completo pre-gold di 316/316 record con zero differenze inattese.

## Artefatti e impronte pre-valutazione

- `live_replay_gate_v0_3.py`:
  `50dfcd89e2c99a91d39c3399ba4b36b87c538304dc2a0a695d9964f6b66255d5`;
- `live_evaluator_v0_3.py`:
  `bcec36f2d31156c2851650b675af1a883d28309b3dbbda569961f39cd7fbcd13`;
- `build_live_replay_v0_3_freeze.py`:
  `2867c9f76a846fcb1cb7b3a051560e641d67a5458c45595683bcb8eeffe28ea1`;
- `test_live_evaluator_v0_3.py`:
  `9734fe85fa70c2d6dda9ad7f8927dca23738e7e02773bfd0c0970fa48ba9138d`;
- `audit_live_evaluation_v0_3.py`:
  `26619768de407f924d9054038ec8db72aa3e08a3333b252a7c92ae0237c56697`;
- freeze 0.3:
  `73d156f7a2fde636262dadcacb366dd4ed274e83234be3fb8d89a3d62dbffb19`.

Il freeze contiene 39 fonti pre-gold e 5 file 0.3 auto-improntati.

## Impronte storiche preservate

- evaluator 0.2:
  `be49b085e22a089dda3c82a89dacddda218099d174b934adf4074bc0f71ee85c`;
- replay gate 0.2:
  `cf4b832b3e8eb951f80298dc902bbaf2d1162d95759fc3726f27f2462c4d0bdf`;
- risultato 0.2 riuscito:
  `6eee1f559b20f3145780ae9dca52eaf101df82f442214cdfec0751f50845d25d`;
- batch:
  `eac40d320a8e2d98b0a11ab2ed652cf9ecc64b4893fb2e658a9bbde50143ca0b`;
- sigillo batch:
  `8f58208fd6082b6fafda953882356c3f4d0084cf183aefeeaafb8b08847cde4c`;
- journal:
  `403135c52aff5852e782f6549bc55f0f08d64b6a92e7a8fb4b9c6374c7515c4e`.

Non sono state usate GPU, rete, endpoint o servizi. Nessun commit è stato
creato. Checkpoint e handover non sono stati aggiornati in attesa dell'audit
indipendente.
