# RM-0006 — verifica C2 del 22 agosto 2026

## Esito

C2 e' completata. Gli 8 casi rapidi italiani hanno attraversato due processi
HTTP Metnos isolati consecutivi: 16 risultati su 16 sono verdi, senza errori,
intervento manuale, frontier o modifica delle fixture.

```text
certification_id=rm0006-c2-quick-v1
evaluated_cases=16
passed=16
failed=0
errors=0
objective_achieved=true
results_registry_sha256=f24051d15c1ecd1dc2f9822ae9c21f434687c0846f4d2a6dd250a99860bd0efb
```

## Superfici attraversate

Il coordinatore usa `E2EServer` e `E2EClient` sulla sola route pubblica
`POST /agent/turn`. Le sonde leggono soltanto il TurnLog del server isolato e
la fixture dedicata `/tmp/metnos-certification-fixture`. Ogni caso ricrea la
fixture e ne confronta l'impronta prima/dopo; ogni ciclo avvia un processo con
storage separato.

I piani osservati coprono Tutor, fast path deterministico, selezione file,
lettura scalare, passaggio find→read, ordinamento e read→group→sort. Durata
osservata: minimo 61 ms, massimo 12 651 ms, media 6 516 ms; ogni caso resta
entro il proprio limite congelato.

## Difetti corretti prima dei cicli qualificanti

I cicli diagnostici hanno trovato quattro cause generali: autenticazione
mancante nel client E2E, catalogo Tutor firmato non seminato nel processo
isolato, variante «fuso configurato» esclusa dal fast path e presentazione dei
contenuti file sostituita da metadati o da una sintesi non richiesta. Sono
stati corretti i confini comuni e aggiunte regressioni mirate. Il Tutor rende
anche esplicita la separazione fra spiegazione e azione per le guide operative.

## Artefatti

`internal/reports/rm0006-c2-quick-20260822/` contiene manifest, casi,
osservazioni redatte, registro risultati append-only, eventi, JUnit e
riepilogo. Le osservazioni sono distinte per ciclo; la ripresa non sovrascrive
un risultato gia' registrato.

## Limite

C2 certifica soltanto gli 8 casi rapidi italiani. C3 deve estendere il runner
ai 19 flussi non durevoli e a entrambe le lingue; C4-C6 restano necessari per
dispositivo, LRE, condizioni avverse, sonde reali e certificazione 96-casi.
