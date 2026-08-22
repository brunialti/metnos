# RM-0006 — verifica C3 del 22 agosto 2026

## Esito

C3 e' completata. I 19 flussi logici non durevoli hanno attraversato un
processo HTTP Metnos isolato in italiano e inglese: 38 risultati su 38 sono
conformi, senza errori, esclusioni o intervento manuale.

```text
certification_id=rm0006-c3-nondurable-v1
evaluated_cases=38
passed=38
failed=0
errors=0
objective_achieved=true
matrix_sha256=0825a86b259acd9b56ae33f507845305b57993217af09f877f595c035b880cd0
results_registry_sha256=095bab00d72bd74443c179536510faa87bfef64b9bf30794d0b53770f17d50af
```

Il digest della matrice nel riepilogo identifica il sottoinsieme C3. La
matrice d'oro completa rimane composta da 24 flussi e 48 casi, con
`oracle_version=rm0006-golden-oracle/3` e digest
`5fee975705d3d88b1c161738e08d8708b9929842c7d400519998c32ea1c7ec9b`.

## Superfici attraversate

Il runner usa la route pubblica `POST /agent/turn` su processi isolati per
lingua. Copre Tutor, percorso deterministico, planner, composizione fra
executor, letture, mutazioni, undo, approvazioni, raccolta input, risultato
parziale e credenziale revocata. Le sonde osservano piano, risposta, registri
di test e postcondizione sulla fixture dedicata.

La revoca delle credenziali non e' simulata come generico errore di rete: un
server IMAP TLS locale completa la connessione e rifiuta `LOGIN` con
`AUTHENTICATIONFAILED`. Il caso deve terminare in errore controllato e proporre
la correzione dell'account; il risultato parziale deve invece conservare
l'output riuscito senza ripetere l'effetto fallito.

## Difetti corretti prima del ciclo qualificante

I cicli diagnostici hanno localizzato cause comuni nei confini di prodotto:
propagazione dell'identita' autenticata nei dialoghi, ripresa senza duplicare
approvazioni o input, esecuzione canonica dei rami composti, conservazione del
risultato parziale, classificazione distinta di autenticazione e rete,
normalizzazione del provider e risoluzione dei filtri contro lo schema reale
dei record. Ogni correzione ha una regressione mirata; la risoluzione dei
campi resta inattiva quando lo schema e' ambiguo.

## Verifiche

- ciclo E2E qualificante: 38/38;
- test mirati finali per resolver, posta e contratto C3: 24/24;
- generatore della matrice d'oro in modalita' `--check`: 48 casi coerenti;
- nessuna indisponibilita' del modello o della rete convertita in esclusione.

## Artefatti e limite

`internal/reports/rm0006-c3-nondurable-20260822/` contiene manifest, casi,
cataloghi firmati, osservazioni redatte, risultati append-only, eventi, JUnit,
log e riepilogo. C3 non certifica i cinque flussi con dispositivo posseduto o
lavoro durevole: appartengono a C4. C5 e C6 restano necessari per sonde reali,
ricongelamento e doppio ciclo finale da 96 casi.
