# Review multidimensionale indipendente — V26.2 native K1

Data: 2026-08-08. Solo analisi offline; nessuna inference aggiuntiva.

## Verdetto

V26.2 non converge, ma il fallimento non smentisce la rappresentazione
relazionale minima. Il difetto dominante è una regressione del contratto:
riducendo lo schema è stato ridotto anche il prompt, fino a definire
tecnicamente soltanto la relazione target, il relativo speech act e il tempo
corrente. Il modello ha quindi usato quei valori come attrattori per quasi
tutto.

Risultato nativo congelato:

- 34 call, zero retry;
- 26/34 evaluable secondo il report originale;
- 20/26 binding exact;
- 5/26 signature exact;
- 3/7 positivi valutabili;
- leakage 2/19;
- evidence complete 17/26;
- p50 3,109 s, p95 4,195 s;
- output SHA-256:
  `ae8020a29ade3de48922d64cc61fe5f5bf9349fa3defda6643d0c4716757b834`.

Contabilità fail-closed corretta: 19 supported, 7 ambiguous, 7 unsupported,
1 invalid. Unsupported non è una classificazione negativa riuscita; deve
essere `NOT_EVALUATED`. La copertura reale è quindi 19/34, non 26/34.

## Diagnosi per dimensione

### Enum e ontologia — causa primaria

Mismatch raw contro oracle:

| Campo | Exact | Mismatch |
|---|---:|---:|
| role | 30/34 | 4 |
| interpretation | 19/34 | 15 |
| speech_act | 30/34 | 4 |
| relation | 17/34 | 17 |
| subject_ref | 27/34 | 7 |
| grammatical_person | 28/34 | 6 |
| time_scope | 29/34 | 5 |

Il prompt V26.2 non contiene il registro con firma/arità delle nove relazioni.
Nomina semanticamente soltanto `spatial.located_at`; analogamente descrive in
modo specifico `open_question`, `current_actor` e `current`, ma non i confini
completi degli altri enum. L'output riflette esattamente questo sbilanciamento:

- relation spatial.located_at: 27/34;
- speech_act open_question: 30/34;
- time_scope current: 34/34;
- identity.same_as: 1/34;
- tutte le altre relazioni tecniche: 0/34.

Questa non è una prova che servano sinonimi o esempi. Servono definizioni
tecniche complete, simmetriche e catalog-derived per ogni valore ammesso.

### Segmentazione — riuscita

UAX #29 ha eliminato il problema ZH one-token: nessun errore di range nei casi
ZH/JA/TR. Il positivo ZH è diventato ambiguous con time evidence none, non è
fallito per segmentazione. Il segmenter va mantenuto invariato.

### Branch prior — secondario

La variante evidence `none` è prima nel tagged union, ma non si osserva un
collasso generalizzato:

- relation evidence non-none 28/34;
- subject evidence non-none 33/34;
- time evidence non-none 26/34.

Il prior contribuisce soprattutto sul tempo implicito, ma l'asimmetria del
prompt spiega meglio i tre attrattori globali. Riordinare branch senza
ripristinare il registro non risolve il problema.

### Evidenza — buona struttura, riferimenti sovravincolati

L'oggetto required `{relation, subject, time}` con valori tagged union è una
buona forma: exact-once, niente free text, niente claim duplicati. Il validator
ha però invalidato un caso quote perché `clause_semantics` duplicava gli estremi
della clausola e pretendeva equality esatta.

Per proof by construction, le varianti contestuali dovrebbero essere simboliche:

- `clause_semantics` implica la clausola corrente, senza ripetere start/end;
- `predicate_morphology`/`tense_morphology` implicano il predicate corrente,
  senza ripetere predicate id;
- solo `explicit_segment` porta uno span;
- context evidence non porta offset.

Questo riduce token, invalidi e possibilità di contraddizione senza leggere
testo o introdurre euristiche linguistiche.

### Selezione clausola e multiquery

Il runner sceglie la prima clausola con relation != none. È sufficiente per il
gate binario current-location, ma non dimostra analisi completa su condition,
quote e relative clause. La fixture 34 contiene una sola signature attesa per
caso, anche quando il modello deve produrre più clausole. Quindi:

- binding exact può essere valutato;
- una signature target può essere valutata;
- "full semantic exact" dell'intero frame non ha ancora un gold completo.

Serve un oracle per-clause/per-relation prima di chiamare 34/34 una prova di
analisi semantica generale.

### Ambiguous e unsupported

Sette ambiguous e sette unsupported sono il costo diretto delle definizioni
mancanti. Il report originale esclude ambiguous ma conta unsupported come
evaluable; questo accredita vere negative a un analyzer che si è astenuto.
Policy corretta:

- supported + frame valido: evaluable;
- ambiguous, unsupported, invalid, transport error: NOT_EVALUATED;
- binding, signature e coverage sempre pubblicati separatamente.

`interpretation` non deve essere un operando della tupla di routing. È uno
stato di copertura.

### Gold e misura

Tre limiti della fixture impediscono di usare signature exact come unico gate:

1. Il controllo etichettato polar è una forma dichiarativa senza marcatore di
   domanda; l'output assertion/description è linguisticamente difendibile.
2. Il quinto elemento della tuple originale è sovraccarico: a volte tempo, a
   volte luogo esplicito, operand, quoted time o relative-clause marker. La
   conversione piatta a `time_scope=contextual` non è una semantica uniforme.
3. Non esiste oracle del tipo esatto di evidence; si può misurare grounding
   presente/assente e validità dello span, non imporre una variante specifica.

Il gate di binding resta legittimo. Il gate semantico richiede un gold tipizzato
per relazione e per clausola, più adjudication trasparente del controllo polar.

## Tre rappresentazioni sensate

### A. Atomo relazionale minimo + registro tecnico completo

Mantiene role, speech_act, relation, subject_ref, time_scope e tre proof tagged.
`grammatical_person` resta diagnostica ma esce dalla firma di routing;
`interpretation` resta stato di coverage. Il prompt definisce simmetricamente
ogni enum e ogni arità, generato in produzione dai semantic contract del
catalogo. Nessun esempio o trigger sorgente.

Pro: delta minimo, sfrutta il segnale già osservato, compatto, facile da
integrare. Contro: una singola relation enum non mostra perché un caso è
ambiguo.

### B. Candidate-set relazionale tipizzato

Sostituisce relation + interpretation con `relation_candidates`:

- zero candidati = unsupported;
- uno = resolved;
- più di uno = ambiguous.

Ogni candidato ha ID tecnico, argomenti tipizzati e proof. La cardinalità
determina lo stato, evitando `ambiguous` auto-dichiarato accanto a una singola
analisi completa.

Pro: ambiguity verificabile, generalizzazione catalog-driven. Contro: output e
schema più grandi; rischio di mode collapse e costo maggiore non ancora testati.

### C. Fact table sintattico-semantica + unificatore catalogo

Il modello non sceglie la relazione. Emette fatti: role/speech, variabile
richiesta, subject ref, entity/context type, temporal anchor e proof. Un solver
deterministico unifica i fatti con firme di catalogo (spatial, filesystem,
runtime, workflow, document, identity, ACL, movement, proximity).

Pro: massima separazione lingua/catalogo, relazione derivata. Contro: richiede
un'ontologia di argomenti e un nuovo gold tipizzato; delta più ampio.

## Raccomandazione unica

Proseguire con **A**, in una nuova versione congelata e nativa.

Motivo: il confronto V25.3→V26.2 isola già la variabile. La forma minima non è
la perdita primaria; è scomparso il registro tecnico. A ripristina soltanto
l'informazione tecnica necessaria, senza reintrodurre campi ridondanti, esempi,
sinonimi, retry o route authority.

Requisiti della nuova freeze:

1. stesso UAX #29;
2. stesso schema semantico minimo, con proof refs simbolici by construction;
3. definizione completa di tutti gli enum retained e firme di tutte le
   relazioni;
4. zero surface/examples e audit n-gram pre-live;
5. una call, zero retry;
6. unsupported/ambiguous NOT_EVALUATED;
7. binding gate e semantic signature gate separati;
8. nessun credito da ablation post-hoc;
9. adjudication/gold v2 prima di pretendere semantic 34/34 full-frame.

B e C restano le alternative successive se A non converge; non vanno lanciate
in parallelo sullo stesso design set.
