# V25.2 relation-goal — audit statico pre-live

Data: 2026-08-08. Nessuna inference usata. Il freeze candidato 1.0 non è
stato modificato.

## Verdetto

Il contratto piatto ha copertura sufficiente per discriminare tutti i 34
controls e non contiene esempi del benchmark. La derivazione di
`runtime.current_location` dipende da otto dimensioni indipendenti più role ed
evidenza, non da un binding atomico. Il costo di output dell'oggetto leggibile
è però alto; dopo la prova semantica conviene testare una tuple posizionale che
conserva gli stessi tipi.

## Copertura degli enum sui 34 controls

| Dimensione oracle | Valori nella suite | Copertura V25.2 |
|---|---|---|
| speech act | open, polar, imperative, condition, quote | completa |
| relation | located-at, identity, filesystem, runtime host, proximity, share, move, workflow, document | completa con namespace tecnico |
| focus | location, entity, identity/place identity, truth, host, collection, result, destination, progress, none | completa; place identity = focus identity + subject type place |
| subject | current actor, explicit/unknown person, deictic place, file, process, collection, location operand, quoted actor | completa per composizione ref + type |
| time | current, historical, current context, quoted/relative/explicit contextual | completa per il gate; gli ultimi valori sono intenzionalmente raccolti in contextual |
| evidence | span, morphology, speaker context, clause, none | completa e compatibile con pro-drop/fusione |

La compressione di più varianti temporali in `contextual` non riduce il gate:
solo `current` può unificarsi col capability. Se in futuro servirà routing
temporale autonomo, il registro dovrà distinguere explicit-time, quoted-time e
relative-clause; non è necessario per questo capability.

## Disciplina del projector

La route current-location è derivata soltanto se valgono insieme:

1. head role=request;
2. speech_act=open_question;
3. relation=spatial.located_at;
4. focus=location:spatial_position;
5. subject=current_actor:actor;
6. time=current;
7. focus evidence presente;
8. subject evidence presente;
9. patient e route Phase 2 coerenti con places/get.

Le mutation congelate cambiano una dimensione per volta: 11/11 passano. Il
projector non legge query, lingua, lemma, gloss, gold o case ID.

## Audit anti-contaminazione

Confronto casefold/Unicode contro 34 controls, 109 legacy e 70 adversarial:

| Corpus | query intera nel prompt | 4-gram | 3-gram | 2-gram nella sola sezione nuova |
|---|---:|---:|---:|---:|
| controls 34 | 0 | 0 | 0 | 1 (`in the`) |
| legacy 109 | 0 | 0 | 0 | 4, tutti funzionali generici |
| adversarial 70 | 0 | 0 | 0 | 3, tutti funzionali generici |

I bigrammi della sezione nuova sono frammenti grammaticali inglesi (`in the`,
`to a`, `do not`, `for the`, `from the`), non trigger semantici. L'intero
prompt, che eredita la lunga ontologia V25, ha un solo 3-gram overlap:
`how many files`; non nasce dal delta V25.2. Nessuna frase location dei
controls compare nel prompt.

## Costo stimato

- prompt V25: 26.843 byte;
- prompt V25.2: 28.580 byte, +1.737 (+6,5%); con prefix cache il delta warm è
  secondario;
- schema V25: 22.353 byte;
- schema V25.2: 25.495 byte, +3.142 (+14,1%);
- output V25 mono osservato: mediana 868 caratteri;
- desired-observation leggibile: 308 caratteri minified neutro, 379 positivo,
  circa 432 pretty-printed positivo per head.

Usando il fit warm già misurato (circa 9,9 ms per output token) e una stima
conservativa di 77–108 token aggiuntivi, l'oggetto leggibile costa circa
0,76–1,07 s per head. È accettabile come prova di semantica, non come formato
finale per query XL.

## Tuple compatta proposta, non congelata

Stesso ordine e stessi enum, nessuna perdita semantica:

    [speech_act, relation, focus_role, focus_type,
     subject_ref, subject_type, time_scope,
     focus_evidence_kind, focus_start, focus_end,
     subject_evidence_kind, subject_start, subject_end]

Esempio puramente tecnico (non frase sorgente):

    ["open_question", "spatial.located_at", "location", "spatial_position",
     "current_actor", "actor", "current", "explicit_span", 1, 1,
     "predicate_morphology", 2, 2]

La tuple positiva è 149 caratteri minified / 189 pretty-printed contro
379/432 dell'oggetto: -56%/-61%. La stima warm diventa circa 0,37–0,47 s per
head. JSON Schema Draft 2020-12 può imporre 13 `prefixItems`, `minItems=13`,
`maxItems=13`; il validator deve poi espanderla in una dataclass nominata prima
del type checking. Non abbreviare gli enum nella prima prova: codici opachi
ridurrebbero ancora i token ma peggiorerebbero audit e mode stability.

Rischi tuple da misurare prima dell'adozione:

- shift posizionale silenzioso se il decoder non rispetta `prefixItems`;
- supporto incompleto del grammar compiler del server;
- minore leggibilità dei raw trace;
- possibile bias verso il primo enum, anche se non esiste più un branch
  `oneOf none` che consenta di saltare l'intero contratto.

Gate consigliato: prima dimostrare 34/34 con oggetto leggibile; poi congelare
la tuple separatamente e richiedere parità semantica, zero invalidi e K≥5.

## Outage accounting

Il primo tentativo locale è stato bloccato dal sandbox prima dell'endpoint. Il
vecchio summary diagnostico `25/34` contava erroneamente i 25 negativi come
true negatives. L'evaluator 0.1 separato marca invece:

- evaluable=0;
- NOT_EVALUATED=34;
- binding denominator=0;
- recall/leakage/rate=null;
- transport failures=34.

La mutation outage con una positiva e una negativa passa: nessun errore di
trasporto può contribuire alle metriche semantiche.
