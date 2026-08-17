# V24.1 frozen delta — sink ownership, argument relation, question binding

V24.1 è un freeze separato: non modifica retroattivamente V24. Regole e codice
sono stati congelati prima della cross-validation su V23lite 109, V23 full 109 e
adversarial V21 50.

## Risultato

| Dataset | V24 | V24.1 | Delta | Regressioni |
|---|---:|---:|---:|---:|
| V23lite full | 105/109 | **107/109** | +2 | 0 |
| V23 full | 100/109 | **102/109** | +2 | 0 |
| V21 adversarial, projection-only | 44/50 | **44/50** | 0 | 0 |

V23lite è valido 109/109. V23 full conserva i due frame sink-span0 come
fail-closed, quindi 107/109 validi. V21 conserva 45/50 adapter-valid; patient e
carrier grounding restano N/A perché lo schema V21 non li rappresenta.

I due miglioramenti V23lite sono:

1. `extract/entries` con sink esplicito nello store diventa
   `extract/entries + write/entries` tramite ownership/durability del manifest;
2. il file che supporta un precedente `create/events` viene acquisito come
   `get/files`, tramite relation binding tipizzata, invece di essere trattato
   come un nuovo `write/files`.

## Perché non ho usato “non write/create/move” come lista

La scorciatoia “primary sink su azione non-owner = secondary” avrebbe introdotto
errori:

- `compress/files` possiede un artifact persistente pur non essendo write/create;
- `read/files` con destination è rappresentato dal catalogo/gold come una sola
  route `read/files` (mono 80), non come `read + write`;
- `read/urls` può avere un consumer write esplicito successivo, che deve
  sopprimere il duplicato.

V24.1 usa invece `primary_sink_contracts` dichiarativi per route, con
`primary_artifact_durability`, `explicit_sink_policy`, sink domain e
materialization route. Il projector non contiene una lista di verbi owner.

## Nuovi claim strutturati

### `argument_relations`

Un nodo può dichiarare un supporto a un argomento di un predicato precedente:

```json
{
  "kind": "support_argument",
  "binding_id": "calendar_event_attachment",
  "target_predicate_id": 4,
  "support_object": "files",
  "start_token_id": 22,
  "end_token_id": 23
}
```

Il manifest binding valida target route, support domain e acquisition route. Il
projector non legge il lemma/gloss “attach”. Per comparare un frame legacy
V23lite, l'adapter shadow può inferire il claim solo con la congiunzione completa:

- target source edge presente e precedente;
- target route ammessa dal binding;
- patient file esplicito;
- scope `single_known`;
- candidate route con destination-artifact object basis;
- primary sink con stesso dominio e identico span del patient.

Questa inferenza è esplicitamente marcata `comparison-only`. In produzione il
claim deve essere emesso dall'analyzer e source-grounded; non va mantenuto come
repair silenzioso.

### `question_binding`

V24.1 aggiunge un claim catalogato per il tipo di domanda:

```json
{
  "kind": "catalog_query",
  "binding_id": "runtime.current_location",
  "start_token_id": 1,
  "end_token_id": 1
}
```

Il binding `runtime.current_location` proietta `get/places` ed è verificato da
mutation. Il frame V23lite catturato per “dove mi trovo” non contiene alcun
equivalente strutturale e propone `get/persons`; V24.1 non legge il testo e non
inventa il claim. È quindi uno dei due residui reali.

## Due residui V23lite

1. `create/files_spreadsheet` contro gold `create/files`: la stessa suite usa
   `create/files_spreadsheet` per una formulazione quasi equivalente. È un gold
   incoerente/stale da adjudicare, non una regola da aggiungere.
2. `get/persons` contro `get/places`: serve che il nuovo analyzer emetta il
   `question_binding` current-location. Senza evidenza, correggere sarebbe un
   override lessicale o gold-specific.

Quindi 107/109 è il massimo onesto ottenibile dai frame V23lite catturati con le
regole frozen. Lo schema proposto rappresenta il secondo residuo, ma serve una
nuova inference LLM per popolarlo.

## Mutation e stabilità

Il delta V24.1 passa **10/10** mutation, sopra le **31/31** della base V24:

- extract ephemeral sink → secondary persistence;
- compress persistent artifact resta primary;
- read-file destination resta una sola route;
- question current-location → places;
- relation attachment esplicita e legacy structural inference;
- guard senza source edge;
- evidence coverage question;
- forward argument edge rejection;
- runtime catalog gate fail-closed.

Venti round sui 109 frame V23lite producono un solo hash output
`83275fa19a32da2ddcb198d16f239947db66b78ee85ad12de9b9ffc73618dfda`.
Costo CPU validator+projector: mediana 2.50 ms, p95 4.02 ms, p99 5.00 ms, max
9.16 ms; inference LLM e trasporto esclusi.

## Artifact e hash freeze

- `/tmp/metnos_v241_contract.schema.json`
- `/tmp/metnos_v241_rules_frozen.json`
- `/tmp/metnos_v241_offline.py`
- `/tmp/metnos_v241_freeze.lock.json`
- `/tmp/metnos_v241_results.json`
- `/tmp/metnos_v241_mutation_results.json`
- `/tmp/metnos_v241_stability_perf.json`

Hash V24.1:

| Artifact | SHA-256 |
|---|---|
| rules | `254056cf4e7d959812f0d6a8a24c931b84191ea685f0e955d0b14cce57c7ff82` |
| schema | `82b24c50dfcf10eed7736231c7640e75f773025864dd933889a474d531b375b4` |
| projector | `964ab447f730a0a145178d23c05404e876e06be0b56688d9372dc5e890c8d68e` |

Il lock include anche hash della base V24, dei tre dataset e dei due baseline
result file. `cross_validation_started_at` è registrato nel lock.

## Gate runtime

V24.1 resta shadow-only. I nuovi metadata sono
`prototype_manual_review_required`; non correggono il fatto che il catalog audit
ha 0/96 contract production-reviewed. La modalità runtime continua a fallire
closed. Prima del cutover servono:

1. ownership/durability e sink policy nei manifest reali;
2. argument binding realmente supportati dal planner/executor contract;
3. question binding nell'output LLM con span sorgente;
4. K>=3 live su full+adversarial;
5. rimozione dell'inferenza legacy della relation, mantenendo solo il claim
   esplicito.

Conclusione: V24.1 porta il miglior frame disponibile a 107/109 senza liste
linguistiche, query/gold override o regressioni. I due residui non giustificano
altro codice di repair: uno è gold da correggere, l'altro richiede una nuova
evidenza strutturata dall'LLM.
