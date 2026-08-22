# RM-0006 — verifica C0 del 22 agosto 2026

## Esito

C0 e' completata. Il coordinatore indipendente vive in
`tests/e2e/certification/`, non importa il runtime Metnos e nel lotto sintetico
non contatta server o modelli. C1, la selezione dei 24 flussi d'oro, non e'
stata avviata.

## Contratto prodotto

- `schemas.json` definisce con JSON Schema 2020-12 `CaseSpec`, `CaseResult` e
  `CertificationManifest`; gli oggetti sono chiusi a campi imprevisti.
- Ogni `CaseSpec` richiede almeno una sonda di postcondizione.
- La validazione semantica rifiuta un verdetto `pass` senza sonde, con sonde
  fallite o con motivi di fallimento.
- `results.jsonl` ed `events.redacted.jsonl` ricevono soltanto aggiunte. La
  chiave di ripresa e' `(case_id, cycle)` e un duplicato arresta il lotto.
- `manifest.json` e `cases.jsonl` sono immutabili: una ripresa con contratto
  differente fallisce chiusa.
- `summary.json` e `junit.xml` sono ricostruiti deterministicamente dal
  registro. Le richieste non compaiono negli eventi o nel riepilogo.

## Fotografia iniziale

La fotografia integrale e le impronte SHA-256 sono in
`rm0006-c0-snapshot-20260822.json`. Valori principali:

- revisione di partenza: `2678ac823479198b7f0df7c9c8b18c0688f68380`;
- suite E2E: 168 test raccolti, 25 file di scenario dopo l'aggiunta di C0;
- corpus locale: 1.113 record, 942 capostipiti deduplicati;
- catalogo, configurazione dei carichi, criteri qualita' e superfici HTTP
  congelati con impronte distinte;
- nessuna richiesta privata conservata nella fixture sintetica.

La revisione Git identifica il punto di partenza; le impronte identificano
anche i file C0 non ancora committati al momento della fotografia.

## Prove eseguite

```text
python3 -m pytest tests/e2e/scenarios/test_certification_c0.py -q
5 passed in 0.19s

python3 -m pytest --collect-only -q
168 tests collected in 0.09s
```

Il primo lotto e' stato interrotto volontariamente dopo 2 risultati su 6 e
poi ripreso. Un secondo lotto e' partito pulito. `diff -qr` fra le due
directory non ha prodotto differenze: manifest, matrice, registro, eventi,
riepilogo e JUnit sono identici byte per byte.

Riepilogo comune dei due lotti:

```json
{
  "complete": true,
  "errors": 0,
  "evaluated_cases": 6,
  "failed": 0,
  "objective_achieved": true,
  "passed": 6,
  "pending": 0,
  "results_registry_sha256": "8d2c02aa45062baeb304fd34f74579698b968a03d038208f7e433c10d5d2bbff"
}
```

## Limiti dichiarati

Questa prova certifica soltanto il meccanismo C0. Le tre osservazioni sono
sintetiche e non dimostrano interpretazione, instradamento o azioni reali di
Metnos. Non sostituiscono i 24 flussi, i 96 casi finali, le sonde reali o la
revisione indipendente richiesti da C1-C6.
