# Audit indipendente dei risultati live v0.3

Data: 2026-08-13  
Ambito: sola lettura della misura già conclusa e dei valutatori offline; nessuna
nuova inferenza, GPU, rete, chiamata a servizi o commit. L'unica scrittura è
questo referto.

## Esito

**PASS per evaluator v0.3, integrità, conteggi e verdetto.**

La canonicalizzazione non introduce falsi positivi nei dati misurati: **0/62**
nuovi positivi sospetti. Il candidato B resta `candidate_fail`.

Esiste un solo rilievo documentale non funzionale: il referto descrittivo
`internal/design/referto_valutazione_offline_intento_v0_3_13_8_2026.md`
riporta un'impronta errata del freeze v0.3. Il freeze reale, ricostruito e
verificato, è
`73d156f7a2fde636262dadcacb366dd4ed274e83234be3fb8d89a3d62dbffb19`,
non
`73d156f7a2fde636262dadcacb366dd4ed274b5979c5925ab8955a8f8190bd79`.
Il referto pre-valutazione, l'output v0.3 e il controllo del freeze usano
l'impronta reale; il difetto è quindi limitato a quella riga descrittiva.

## Replay, tolleranza e anti-hardcoding

- replay completo: **316/316** estrazioni, risposte HTTP grezze e record del
  journal verificati;
- differenze consentite: **1**; differenze inattese: **0**;
- l'unica tolleranza è generale, simmetrica `false/true` e `true/false`, per
  qualsiasi record e soltanto per il booleano esatto
  `adapter_metadata.implicit_actions_ignored` presente su entrambi i lati;
- campo assente, tipo non booleano, altro puntatore, altra modifica o identità
  non chiusa vengono respinti;
- ogni tolleranza conserva identità, valori e motivo nel report;
- la scansione del codice v0.3 non trova indice, ordinale, query, identificativo
  o hash del vecchio caso speciale. Il mismatch osservato resta il record 80,
  ma la regola non lo nomina né lo privilegia.

## Canonicalizzazione e ricerca di falsi positivi

La proiezione è applicata dalla stessa funzione a expected e actual. Può
eliminare soltanto:

- `data_from: []`;
- porte esplicite quando origine o destinazione ne ammettono una sola;
- outcome materializzati con corpo vuoto.

Ho ricostruito indipendentemente i 248 confronti tipizzati dal batch e
dall'oracolo. I **62** casi che passano da raw falso a canonico vero sono
esattamente 46 per A e 16 per B. Il confronto strutturale caso per caso trova
soltanto **67 occorrenze** di `data_from: []` materializzato; alcuni casi ne
contengono più di una. In questo batch porte e outcome non aggiungono alcun
nuovo positivo.

Per tutti i 62 casi restano identici radice, sequenza e collocazione delle
route, ordine dei nodi, origine e porte effettive degli archi, barriere,
outcome non vuoti e `reason`. Le prove negative confermano inoltre che
mutazioni di root, route, order, edge e reason rimangono diverse dopo la
proiezione. **Falsi positivi trovati: 0.**

## Conteggi ricalcolati

### Pannello canonico 120

| Misura | A | B |
|---|---:|---:|
| `semantic_exact_canonical` | **75/120** | **25/120** |
| `semantic_exact_v0_2_raw` | **29/120** | **9/120** |
| falsi negativi di rappresentazione corretti | 46 | 16 |
| root prodotta dal modello | 94/120 | 73/120 |
| root accettata / `root_exact` | 91/120 | 37/120 |
| route esatta | 51/84 | 17/84 |
| composizione esatta | 58/84 | 23/84 |
| archi dati esatti | 53/84 | 22/84 |
| consenso esatto, applicabile | **0/3** | **0/3** |
| proprietà dei rami esatta, applicabile | **0/3** | **0/3** |
| controllo di sistema esatto, applicabile | **2/2** | **2/2** |

I falsi negativi corretti sono **46 + 16 = 62**. Il delta canonico B−A è
**25−75 = −50**. Le regressioni restano
`semantic_exact_canonical`, `root_exact`, `correct_abstention` e
`technical_valid`; il verdetto resta **`candidate_fail`**.

La vecchia lettura globale di consenso e proprietà dei rami resta 117/120 per
entrambi i bracci; il denominatore applicabile rende visibile il risultato
reale 0/3. Il controllo di sistema globale resta 120/120 e quello applicabile
è 2/2.

### Controlli tipizzati e Phase-1

- controlli tipizzati: A **1/4**, B **0/4** sulla metrica canonica; il vincolo
  speciale B 4/4 fallisce;
- Phase-1 separato: A **25/34**, B **29/34** `direct_binding_exact`;
- pannelli conservati senza conversione o compensazione: 120 canonici, 4
  tipizzati e 34 legacy per braccio.

## Colonne e stati preservati

Il confronto riga per riga con l'output v0.2 riuscito trova zero differenze
nelle 248 righe tipizzate per `root_exact`, astensione corretta e scorretta,
validità tecnica, falsa azione evitata, undo, consenso, negazione e proprietà
dei rami. Anche `semantic_exact_v0_2_raw` coincide riga per riga con la vecchia
`semantic_exact`.

| Stato canonico | A | B |
|---|---:|---:|
| rappresentabile valido | 70 | 27 |
| non rappresentabile valido | 46 | 33 |
| documento non valido | 4 | 57 |
| errore tecnico | 0 | 3 |

Gli stati, Phase-1 e tutte le altre colonne storiche sono invariati.

## Test, output e sigilli

- suite finita v0.3: **18/18 PASS**;
- verifica freeze v0.3: **0 errori**, 39 sorgenti pre-gold e 5 file v0.3
  auto-improntati;
- replay pre-gold: **PASS**, oracolo non aperto e valutazione non eseguita in
  quella fase;
- verifica oracolo: **0 errori**, 23/23 fonti; 107/107 mutazioni negative
  respinte e 6/6 riordini neutri accettati;
- controllo finale dell'output: **0 errori**, 248 righe, 9 colonne critiche;
- output v0.3:
  `f7d85c38474c477a95414de83fe563c8b1f81719318c357a83284cbcfa85582e`;
- self-check v0.3:
  `a7a9f36b20d1d70ce75b2b565654f7abb223ff1f9b51b41ed08e7a4c2a72283a`;
- freeze v0.3 reale:
  `73d156f7a2fde636262dadcacb366dd4ed274e83234be3fb8d89a3d62dbffb19`.

Le impronte storiche restano quelle già registrate: evaluator v0.2
`be49b085e22a089dda3c82a89dacddda218099d174b934adf4074bc0f71ee85c`,
replay gate v0.2
`cf4b832b3e8eb951f80298dc902bbaf2d1162d95759fc3726f27f2462c4d0bdf`,
output v0.2 riuscito
`6eee1f559b20f3145780ae9dca52eaf101df82f442214cdfec0751f50845d25d`,
batch
`eac40d320a8e2d98b0a11ab2ed652cf9ecc64b4893fb2e658a9bbde50143ca0b`,
sigillo batch
`8f58208fd6082b6fafda953882356c3f4d0084cf183aefeeaafb8b08847cde4c`,
journal
`403135c52aff5852e782f6549bc55f0f08d64b6a92e7a8fb4b9c6374c7515c4e`,
oracolo
`3e0d1155fad7b2920b3b04bb6e617747849c8fe4a1abfea5920f1b51dba6a123`
e freeze oracolo
`eb9d051af8cee705536348ac602f7888595e5046937ec2ef09a632fddba63a03`.
Non emerge alcuna sovrascrittura degli artefatti storici.

## Conclusione

L'evaluator v0.3 corregge esclusivamente i 62 falsi negativi di
rappresentazione, senza creare falsi positivi o modificare gli altri risultati.
Integrità e separazione dei pannelli sono confermate. Il risultato sostanziale
resta **A 75/120, B 25/120, delta −50, typed B 0/4, `candidate_fail`**. Resta
soltanto da trattare come refuso l'impronta del freeze nel referto descrittivo
v0.3; il freeze e gli artefatti valutati sono coerenti.
