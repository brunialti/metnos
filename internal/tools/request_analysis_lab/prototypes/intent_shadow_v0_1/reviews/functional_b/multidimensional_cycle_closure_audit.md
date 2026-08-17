# Audit finale di chiusura del ciclo multidimensionale

Data: 2026-08-13  
Perimetro: controllo indipendente e in sola lettura dei documenti terminali,
dei sigilli e degli artefatti storici dopo `candidate_v0_2`. Nessuna GPU,
rete, chiamata a servizi, modifica a runtime/banco/artefatti o commit. L'unica
scrittura è questo referto.

## Esito

**PASS.** I documenti terminali, i due audit indipendenti, i sigilli correnti
e gli artefatti storici sono coerenti. Non risultano difetti bloccanti o non
bloccanti nel perimetro finito di questa chiusura.

La sola decisione ancora pendente nell'ordine corrente è effettivamente se
**autorizzare oppure non autorizzare una nuova misura GPU affiancata per
v0.2**. Il punto 5 resta incompleto e nessuno dei documenti controllati decide
o autorizza tale misura.

## Coerenza del referto multidimensionale

Il referto multidimensionale riproduce senza compensazioni o fusione dei
pannelli i risultati dell'audit live v0.3:

| Misura | A | B | Esito |
|---|---:|---:|---|
| canonico, 120 casi | 75/120 | 25/120 | PASS |
| raw storico v0.2 | 29/120 | 9/120 | PASS |
| controlli tipizzati | 1/4 | 0/4 | PASS |
| barriere applicabili | 0/3 | 0/3 | PASS |
| controlli di sistema applicabili | 2/2 | 2/2 | PASS |
| legacy Phase-1, separato | 25/34 | 29/34 | PASS |

Il delta canonico B-A è -50 e il verdetto resta `candidate_fail`. Sono
riportati correttamente 62 falsi negativi rappresentazionali corretti, 46 per
A e 16 per B, e zero falsi positivi. Radice, route, ordine, archi effettivi,
barriere, esiti non vuoti e `reason` restano invarianti. Il censimento completo
degli edge resta distinto dal conteggio operativo iniziale: 156 issue/archi in
66 record B contro la soglia storica 139.

La sezione sul candidato coincide con l'audit ciclo 2: **30/30** prove
ufficiali, **124/124** round-trip, **36/36** controlli complessivi, zero difetti
aperti nel perimetro offline e circa 4,007 ms medi per la sola prova locale del
compilatore. Il dato prestazionale non viene presentato come prestazione del
modello.

## Stato dell'ordine nei documenti terminali

Il checkpoint qualità e l'handover dichiarano entrambi che:

- le sezioni precedenti sono stati storici intermedi;
- il ciclo multidimensionale/offline è chiuso;
- il primo punto incompleto resta il punto 5;
- v0.2 non ha ancora una misura semantica live;
- la scelta pendente è autorizzare o non autorizzare una nuova misura GPU;
- la chiusura non costituisce autorizzazione.

Non emergono altre decisioni aperte nello stesso perimetro. I limiti dichiarati
del campione, delle fonti bilingui e dei significati multi-ramo restano limiti
espliciti, non scelte concorrenti né risultati semantici impliciti.

## README e assenza di claim semantici impropri

Il README di `candidate_v0_2` registra l'audit ciclo 2 come PASS, con 30/30
prove ufficiali, 36/36 controlli e round-trip 124/124. Distingue correttamente
il freeze osservato dal revisore (`7b68f9f...`) dal successivo risigillo dovuto
al solo aggiornamento documentale.

README, referto multidimensionale, checkpoint e handover affermano
esplicitamente che l'evidenza offline riguarda struttura, determinismo,
sicurezza e riproducibilità. Nessuno dichiara accuratezza semantica live o
universalità per v0.2; i 120 casi sono qualificati come campione aperto di
regressione.

## Freeze e verifiche riprodotte

Il controllo di rigenerazione degli artefatti termina senza differenze e la
suite package-qualified termina **30/30 PASS**; la prova aggregata interna
conferma **124/124** round-trip.

- freeze corrente candidato:
  `f00d7ed185cdceab16eaf753b2990c93797ecb9aac66cd401302edefd4b38d5f`;
- payload freeze candidato:
  `c4070b3ae2486c001af55519ace18ea48988174beac2a5c038a75e302eff5330`;
- freeze corrente oracolo:
  `9d1cdec28fa7fad2685a0a76a4d4ef62c73d8624e114716b3d43c3a372bc6a54`;
- payload lock oracolo:
  `09cb00bd71926baae677a246ab3b9273853f900e8fd42a2903d3b40f5ca2e230`;
- handover sigillato:
  `6ab25778637a57605fc1ba757b9b05d7cf036e11b1b3ce45fe49a896e59939b6`.

Il verificatore canonico dell'oracolo termina con `error_count=0` e **23/23**
fonti congelate. La suite mutazionale respinge **107/107** mutazioni negative
e accetta **6/6** riordini positivi semanticamente neutri. Il nuovo hash del
freeze oracolo riallinea la fonte handover; non cambia i byte dell'oracolo né
gli attesi.

## Artefatti storici invariati

Le impronte correnti coincidono con quelle registrate dagli audit precedenti:

| Artefatto | SHA-256 | Esito |
|---|---|---|
| oracolo/attesi v0.1 | `3e0d1155fad7b2920b3b04bb6e617747849c8fe4a1abfea5920f1b51dba6a123` | PASS |
| payload semantico oracolo | `2cdec2776fa48594db8729ec1aa17a635956a61f64b2ce7ad00aa32f8cc0427f` | PASS |
| batch sigillato | `eac40d320a8e2d98b0a11ab2ed652cf9ecc64b4893fb2e658a9bbde50143ca0b` | PASS |
| sigillo batch | `8f58208fd6082b6fafda953882356c3f4d0084cf183aefeeaafb8b08847cde4c` | PASS |
| journal con raw | `403135c52aff5852e782f6549bc55f0f08d64b6a92e7a8fb4b9c6374c7515c4e` | PASS |
| output v0.2 fallito, storico | `96fc99ddd2d8518737da0bb51da9e88ce09adea5917397d7d4a1d4432cc83fbc` | PASS |
| output v0.2 riuscito | `6eee1f559b20f3145780ae9dca52eaf101df82f442214cdfec0751f50845d25d` | PASS |
| output v0.3 | `f7d85c38474c477a95414de83fe563c8b1f81719318c357a83284cbcfa85582e` | PASS |
| self-check v0.3 | `a7a9f36b20d1d70ce75b2b565654f7abb223ff1f9b51b41ed08e7a4c2a72283a` | PASS |

Il batch e il journal byte-identici conservano le 316 risposte raw già
verificate dal replay; il risigillo documentale non li modifica. Non emerge
alcuna sovrascrittura di oracolo, attesi, raw, batch, sigillo o output storici.

## Conclusione

**PASS di chiusura.** La diagnosi A 75/120 contro B 25/120 e l'audit offline
36/36 appartengono a piani distinti e sono riportati correttamente. v0.2 è una
base offline auditata, non un risultato di accuratezza. Nel perimetro
dell'ordine corrente la sola scelta residua è la nuova misura GPU affiancata:
autorizzarla o non autorizzarla, tramite un gate successivo e separato.
