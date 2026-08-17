# V26.5 — revisione statica indipendente e offline

Data: 2026-08-09  
Verdetto: **BLOCK prima di freeze, gate o run**

## Perimetro e attestazione

La revisione ha letto soltanto artefatti durevoli del laboratorio e dipendenze
locali. Non ha chiamato server, non ha usato la rete, non ha letto output live,
non ha creato freeze o gate e non ha modificato gli artefatti di design V26.5.
Gli unici cambiamenti autorizzati sono questo rapporto, il gemello JSON e la
sezione di verdetto nel README V26.5.

Il blocco non deriva da un errore semantico osservato nell'adattatore. Deriva
da tre lacune nella riproducibilità e nella catena di evidenza che impediscono
di congelare in sicurezza i byte correnti.

## Blocker

### B1 — il bundle non è riproducibile senza lo stato corrente di `/tmp`

Il runner V26.5 usa direttamente:

- `/tmp/metnos_v2641_typed_phase1_runner.py`;
- `/tmp/metnos_v264_independent_graph_probe.py`.

Il primo importa a sua volta registry, schema, prompt, fixture, mutazioni,
oracolo e altri artefatti da `/tmp`. Il secondo importa runner e registry
V26.4 da `/tmp`. Anche il probe di normal form e l'audit schema usano il probe
temporaneo; l'audit prompt usa `/tmp/metnos_prompt_contamination_audit.py`, il
quale carica i dataset 109/34/70 da `/tmp`. I file grezzi dei dataset 109 e 70
non sono presenti nel bundle durevole.

Nell'ambiente corrente le tre copie temporanee principali coincidono con gli
archivi durevoli:

| Dipendenza | SHA-256 |
|---|---|
| runner V26.4.1 | `6f215b04e6dd543b6de5193199957cb653599164f107177f7465fbef7dc95459` |
| probe graph V26.4 | `9116a851241c5d1c03385a81a2eff23bb830599ea57e46abb2f6ddd1fd15e29a` |
| auditor contaminazione | `1cedfa7115744da79764c6b2fd31c2be67eada3fcc18d23bb46a5108eb190169` |

Questa coincidenza spiega il verde locale, ma non costituisce un replay da
checkout pulito. Senza quei file temporanei il self-test termina prima della
validazione. Prima del freeze le dipendenze devono essere risolte da percorsi
durevoli e relativi al bundle, oppure il validator deve essere realmente
self-contained.

### B2 — gli hash sono controllati dopo l'esecuzione e la catena transitiva è incompleta

`metnos_v265_offline_runner.py` importa ed esegue adattatore, runner parent e
probe prima di chiamare `_hash_checks()`. Un file alterato viene quindi
eseguito prima che il mismatch diventi un test fallito. L'audit hook blocca gli
eventi `socket`, `http.client` e `urllib`, ma non è una sandbox di capacità e
non rende sicura l'esecuzione pre-hash.

Inoltre sono vincolati gli hash dei due moduli temporanei di primo livello, ma
non gli artefatti che essi importano. In particolare, la semantica effettiva
di `parent.validate_frame` viene dal registry V26.4.1 temporaneo; il probe
storico usa un runner e un registry V26.4 temporanei non controllati dal runner
V26.5. Il probe riporta i loro hash nel proprio output, ma V26.5 non li
confronta con valori attesi.

Prima del freeze tutti i byte eseguibili e semantici devono essere verificati
prima dell'import, con chiusura transitiva della catena. Il runner stesso dovrà
poi essere legato da un freeze esterno; questa revisione non lo crea.

### B3 — il verde storico delle mutazioni non attraversa la pipeline compatta

Il runner e i due probe sostituiscono soltanto `probe.valid`. Le chiamate
`probe.invalid` delle 68 mutazioni V26.4 continuano a esercitare direttamente
il vecchio validator, senza passare da:

`schema compatto -> adattatore canonico -> validator V26.4.1`.

Di conseguenza `existing_probe_125` dimostra che il vecchio probe è ancora
verde, non che le mutazioni restino fail-closed nel nuovo formato. Inoltre il
probe di normal form contiene una seconda implementazione completa di
`compact_frame`/`expand_frame`; i suoi cinque controlli negativi non importano
`metnos_v265_compact_adapter.py`. Il runner canonico usa l'adattatore reale per
i 19 positivi, ma soltanto per due negativi.

La revisione ha eseguito una matrice indipendente in memoria sui byte correnti:

- 19/19 controlli positivi attraversano schema, adattatore reale e validator;
- 65/68 mutazioni storiche vengono respinte durante la proiezione o dal
  validator espanso;
- `M011_duplicate_clause_id`, `M012_clause_id_reverse` e
  `X003_supported_unsupported_clause_collision` diventano valide perché i soli
  ID artificiali vengono rimossi e ricostruiti dagli span: è la
  canonicalizzazione prevista, non un fail-open;
- i cinque nuovi casi del probe vengono respinti anche dall'adattatore
  canonico reale;
- `source_ordinal` uguale a zero, negativo, stringa, booleano, self-reference
  o riferimento futuro viene respinto dalla combinazione schema/adattatore.

Questi risultati non sostituiscono una suite durevole. Prima del freeze serve
una sola suite compact-native che importi l'adattatore canonico, classifichi
esplicitamente le mutazioni obsolete e verifichi le sostitutive.

## Controlli superati sui byte correnti

| Area | Esito indipendente |
|---|---|
| Generatore schema | output oggetto e byte identici allo schema archiviato |
| Generatore prompt | output byte identico al prompt archiviato |
| Schema | Draft 2020-12 valido; trasformazione limitata ai tre ID rimossi e al nuovo edge |
| Prompt/i18n | 3 righe cambiate, nessuna lista di termini della lingua sorgente, soli nomi tecnici |
| Adapter | 19/19 round-trip positivi; 5/5 controlli strutturali reali fail-closed |
| Multi-azione | fan-out e riuso di output PASS |
| Multi-dominio | due domini indipendenti PASS |
| Ambiguità | alternative tipizzate con azione preservata PASS |
| Richiesta mista | supported più unsupported preservati PASS |
| Separazione sintetico/frozen | 17 casi registry frozen e 2 casi DAG sintetici etichettati separatamente |
| Risultati archiviati | normal form 24/24, schema 30/30, prompt 7/7, runner 34/34 riprodotti esattamente nell'ambiente corrente |

Non è stato osservato un frame invalido accettato dal percorso compatto
completo nei controlli eseguiti. Questo PASS locale non supera B1-B3.

## Censimento mutazioni per la prossima versione

Mutazioni di superficie rese obsolete dalla derivazione deterministica:

- collisione, inversione o non consecutività di `clause_id`;
- duplicazione o disaccordo array/`atom_id`;
- `output_index` fuori range o rivolto a uno slot non-output;
- cicli costruiti tramite ID espliciti arbitrari, impossibili quando ogni edge
  deve puntare strettamente a un ordinal precedente.

Mutazioni compact-native obbligatorie:

- `source_ordinal` assente, zero, negativo, booleano, non intero, self, futuro
  o fuori range;
- sorgente senza output derivabile o con più output derivabili;
- output derivato incompatibile con tipo/coercion del consumer;
- dipendenza senza projection proprietaria nello stesso span;
- collisioni di span supported/supported, supported/unsupported e
  unsupported/unsupported;
- ordinal locale all'alternativa, copertura e distinzione delle alternative,
  common unsupported coerente;
- ordine sorgente, fan-out, riuso dell'unknown di una projection e limiti
  globali di atomi, clausole, prove e profondità.

Restano necessarie senza cambiamento sostanziale le famiglie V26.4 su branch di
status, arità/registry, reference type, role/speech, cardinalità unknown,
compatibilità proof, consumo output, scope, safety, coverage mista e
classificatori. Devono però essere esercitate dopo l'espansione del formato
compatto, non soltanto sul frame pieno storico.

## Condizione per una nuova review

Una V26.5 revisionabile deve essere riproducibile dal solo archivio durevole,
verificare tutte le dipendenze prima di importarle, usare una sola
implementazione dell'adattatore e includere la matrice compact-native appena
descritta. Fino ad allora: nessun freeze, nessun gate e nessun run.
