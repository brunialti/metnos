# RM-0006 — certificazione finale C6 del 23 agosto 2026

## Verdetto

RM-0006 soddisfa il criterio di uscita ed e' `implemented`. I 24 flussi di
riferimento, in italiano e inglese, sono stati eseguiti in due cicli completi
consecutivi: 96 casi su 96 conformi, nessuna esclusione, nessun errore e nessun
fallimento.

```text
certification_id=rm0006-c6-two-cycle-v1
evaluated_cases=96
passed=96
failed=0
errors=0
pending=0
objective_achieved=true
matrix_sha256=3623f900e37e8a8fe966eb2030fc7303ac670a7202d208a007b59ea1fbf00062
results_registry_sha256=a7bc2edd8d64b5584a77331f78573a64becb95fa813dbc0e9f88586bcb409c6d
```

## Revisione congelata

Il manifest identifica il commit base
`201342f1269fd366270ee1523f4bca94dd59c393` e congela inoltre le superfici
effettivamente eseguite. Queste impronte rendono esplicito il contenuto della
revisione qualificata:

| Oggetto | SHA-256 |
|---|---|
| matrice casi | `3623f900e37e8a8fe966eb2030fc7303ac670a7202d208a007b59ea1fbf00062` |
| catalogo | `23ae20dbb890042bfdf8778823bb058f3f0c0022bcd13e965964abb76b44153d` |
| corpus | `d164560dc8bace187a321c483dc30dc6d65c784c77da8bc8fa9f499d885a8193` |
| configurazione LLM | `4faed382e5b1102ca3fa599e8009dfe20dd2fff7bff6f200fd1e671818e558f2` |
| suite | `69b5a8a641208a171854e2c778f97519d33d8f87798c5b3b5c9bf368a5dc8943` |
| superficie di prodotto | `4bd49f473fa43b46c0782e5c6ee27be711afc8b0a16198878328edc78e65b6bd` |

Entrambi i cicli usano `oracle_version=rm0006-golden-oracle/4`, le stesse
fixture, gli stessi cataloghi IT/EN e la stessa piattaforma isolata con client
reale e motore durevole.

## Misure

| Misura | Valore |
|---|---:|
| casi | 96/96 |
| latenza p50 | 5 834 ms |
| latenza p95 | 14 719 ms |
| latenza massima | 15 025 ms |
| approvazioni | 20 |
| chiamate modello | 92 |

Ogni caso rispetta il proprio limite preregistrato. Il coordinatore verifica
piano ammesso, collocazione, autorita', consenso, effetti richiesti e vietati,
stato terminale, contenuto della risposta e postcondizioni sulle fixture.

## Controllo dei dodici criteri

1. 24 flussi e 48 formulazioni IT/EN congelati: conforme.
2. Due cicli consecutivi sulla stessa matrice e superficie: conforme.
3. 96 casi obbligatori eseguiti, zero esclusioni: conforme.
4. Ogni successo ha almeno una postcondizione provata: conforme.
5. Zero violazioni di proprietario, autorita', consenso o collocazione:
   conforme.
6. Zero effetti vietati o ripetizioni ambigue: conforme.
7. Zero sessioni, dialoghi, invocazioni o lavori orfani: conforme.
8. Fallimenti attesi localizzati e contabilizzati: conforme.
9. Annullamento, arresto e ripresa superano le postcondizioni: conforme.
10. Tempi, approvazioni e chiamate modello entro i budget: conforme.
11. Cinque sonde reali sicure, minimo richiesto quattro: conforme.
12. Impronte, limiti e riproduzione presenti; questa verifica e' stata svolta
    da Codex leggendo gli artefatti dopo l'esecuzione, fuori dai processi Metnos
    che hanno eseguito i casi: conforme.

## Campione indipendente dei successi

E' stata aperta almeno una traccia verde per ciascuna famiglia: spiegazione
Tutor, lettura/selezione, composizione e passaggio dati, mutazione/annullamento,
dispositivo/proprietario, dialogo/ripresa, lavoro durevole e
fallimento/risultato parziale. Il campione conferma che il verdetto deriva da
postcondizioni e contratti, non dal solo testo finale.

## Artefatti

`internal/reports/rm0006-c6-certification-20260823/` contiene manifest,
cataloghi, casi, osservazioni redatte, registro risultati append-only, eventi,
tracce per caso e ciclo, JUnit, log e riepilogo. Le esecuzioni diagnostiche
interrotte prima del ricongelamento non fanno parte del pacchetto qualificante
e non sono state conteggiate.
