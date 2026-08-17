# Audit indipendente del fallimento V26.2 K1

Stato: audit esclusivamente offline e redatto; zero chiamate al server.

## Esito separato correttamente

- Run pubblicato: 26/34 evaluable, 20/26 binding corretti, 5/26 tuple exact,
  3/7 positivi valutabili, 2/19 leakage, 8 NOT_EVALUATED.
- Risoluzione reale: 19 supported,
  7 ambiguous, 7 unsupported,
  1 invalid.
- Il report V26.2 contava `unsupported` come evaluable. In una contabilità
  fail-closed anche quei casi sono NOT_EVALUATED: una mancata analisi non è un
  vero negativo.
- Categorie pubblicate: `{"false_binding": 6, "not_evaluated": 8, "tuple_exact_binding_correct": 5, "tuple_mismatch_binding_correct": 15}`.
- Stesso criterio V26.2 ma contabilità fail-closed:
  `{"false_binding": 3, "not_evaluated_ambiguous": 7, "not_evaluated_invalid": 1, "not_evaluated_unsupported": 7, "tuple_exact_binding_correct": 5, "tuple_mismatch_binding_correct": 11}`.
- Criterio più piccolo, ancora soltanto diagnostico:
  `{"false_binding": 4, "not_evaluated_ambiguous": 7, "not_evaluated_invalid": 1, "not_evaluated_unsupported": 7, "tuple_exact_binding_correct": 5, "tuple_mismatch_binding_correct": 10}`.

## Confusione per campo sul frame nativo

| Campo | Exact | Mismatch |
|---|---:|---:|
| `role` | 30/34 | 4 |
| `interpretation` | 19/34 | 15 |
| `speech_act` | 30/34 | 4 |
| `relation` | 17/34 | 17 |
| `subject_ref` | 27/34 | 7 |
| `grammatical_person` | 28/34 | 6 |
| `time_scope` | 29/34 | 5 |

La perdita dominante non è nello schema minimo: è nel contratto descrittivo.
V26.2 definiva con arità 0 relazioni contro
9 in V25.3 e nominava soltanto
una parte degli enum. Questo spiega l'aumento di `ambiguous`/`unsupported` e i
mismatch di relazione, soggetto e tempo.
In particolare, 11 relazioni non-target sono collassate su
`spatial.located_at`; tre tempi contestuali e un tempo storico sono collassati
su `current`. È il profilo atteso quando il prompt definisce soltanto il target
e lascia gli altri enum senza registro tecnico.

## Evidenza

La fixture non contiene un oracle del tipo esatto di prova. È quindi scorretto
valutare `explicit_segment` contro `predicate_morphology` come exact match. Si
può verificare soltanto se una claim che esiste semanticamente è groundata.

| Claim | Confusione required/non-required → grounded/non-grounded |
|---|---|
| `relation` | `{"True": {"False": 6, "True": 28}}` |
| `subject` | `{"True": {"False": 1, "True": 33}}` |
| `time` | `{"True": {"False": 8, "True": 26}}` |

## Binding, tupla e oracle

Un binding sbagliato, una tupla non esatta e un caso non analizzato sono tre
esiti diversi. Per il solo binding `get_location`, una tupla completa diversa
su un controllo già correttamente respinto non cambia la route. Sono presenti
10 reiezioni corrette con tupla non exact.
Perciò il 34/34 sui sette campi era sovraspecificato come gate della singola
route; resta un indicatore separato e legittimo soltanto se Phase 1 dichiara di
essere un analizzatore semantico generico.

## Causa primaria e proposta

Conservare lo schema piccolo, senza reintrodurre `answer_mode`, focus/type,
`subject_type` o deissi. Rimuovere `grammatical_person` dalla tupla di routing:
`subject_ref` più la prova tipizzata del soggetto ne coprono il contributo utile.
Tenere `interpretation` come stato di copertura, non come operando della tupla.
Associare poi ogni valore alla propria prova, invece di mantenere due strutture
parallele che possono divergere:

```text
relation_claim = { value, evidence }
subject_claim  = { ref, evidence }
time_claim     = { scope, evidence }
```

Il prossimo prompt deve invece ripristinare, in forma esclusivamente tecnica e
senza esempi surface, il registro completo con arità di tutte le relazioni e i
confini completi di ruolo, atto linguistico, soggetto, tempo, evidenza,
condizione, descrizione e quote. In produzione questo testo va generato dai
metadata del catalogo, non da liste della lingua sorgente.

Il criterio proposto è: `supported` + `role=request` +
`speech_act=open_question` + `relation=spatial.located_at` +
`subject_ref=current_actor` + `time_scope=current` + tre claim di evidenza
groundate. È un criterio derivato dall'arità tecnica della relazione, non dai
34 gold. Richiede un nuovo freeze e un nuovo run nativo; gli ablation del report
sono diagnostici e non ricevono credito di accettazione.
