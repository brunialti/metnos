# Self-review — Revisore AI A

Data: 12 agosto 2026.

Stato: proposta indipendente prodotta da un revisore AI non umano; richiede
adjudicazione finale esterna. Nessuna uscita dei bracci modello e nessun loro
accordo sono stati usati come verità.

## Esito

- 120 richieste congelate, 120 indici e 120 SHA-256 unici;
- 94 radici `operation_graph`, 2 `system_control`, 24 `unrepresentable`;
- ragioni di non rappresentabilità: 21 `outside_registry`, 2
  `no_actionable_intent`, 1 `ambiguous_intent`;
- 109 casi ad alta confidenza e 11 a confidenza media; nessuno a bassa
  confidenza;
- 4 controlli nuovi, con testo e SHA-256 unici sia fra loro sia rispetto alle
  120 richieste e ai 34 controlli preesistenti;
- impronta SHA-256 del JSON: `53d8f32679df6fc40f8528aa088ef212f354bee7118ab78394b2731dacf75509`.

## Decisione aggiunta durante la revisione

È registrata la decisione approvata da Roberto: se una clausola indispensabile
di una richiesta composta è fuori dal registro congelato, l'intera richiesta è
`unrepresentable/outside_registry`. Un sottografo eseguibile parziale non è
verità attesa. La regola è applicata, fra gli altri, ai casi 18, 34, 49, 63,
73, 79, 88 e 89 e al nuovo controllo 038.

Non è emersa un'altra scelta di progetto bloccante.

## Controlli avversariali nuovi

1. `reviewer_a.control.035`: mescola undo e ricerca; atteso
   `unrepresentable/mixed_root_kinds`.
2. `reviewer_a.control.036`: assegna effetti diversi ad approvazione e rifiuto;
   verifica ordine degli esiti e possesso dei rami.
3. `reviewer_a.control.037`: nega cancellazione e spostamento ma conserva una
   ricerca; verifica accuratezza read-only e assenza di mutazioni inventate.
4. `reviewer_a.control.038`: combina una ricerca registrata con una
   pubblicazione indispensabile fuori registro; verifica la decisione sul
   composto intero.

## Dubbi non bloccanti

- Casi 9 e 11: «assomiglia» può indicare similarità visiva generica; la
  proposta sceglie `find/persons` per il riferimento esplicito alla persona
  nella foto.
- Caso 38: la sorgente delle fatture non è indicata; sono conservate le
  alternative incompatibili e la radice è `ambiguous_intent`.
- Caso 59: `write_issues` non è una rotta congelata, ma la frase dichiara
  esplicitamente l'aggiornamento dello stato; l'oracolo usa `set/issues` per il
  significato, non come sostituzione automatica di alias.
- Caso 84: l'enumerazione dei corpus è proposta come
  `find/dirs -> get/images`; il registro non offre una rotta dedicata per
  elencare gli indici.
- Casi 39, 85, 100 e 109: il provider GitHub è trattato come argomento della
  rotta `files`/`dirs`, coerentemente con l'astrazione del registro. Questo
  giudica il significato, non garantisce che il backend congelato possa
  eseguirlo.

Questi dubbi sono visibili anche nei campi `ambiguity` e `confidence` del JSON;
non sono stati nascosti per aumentare il conteggio di accuratezza.

## Verifiche deterministiche eseguite

Il controllo locale ha verificato con esito zero errori:

- corrispondenza esatta fra indice, testo e SHA-256 del campione congelato;
- cardinalità e unicità dei 120 casi e dei 4 nuovi controlli;
- assenza di duplicati dei nuovi controlli rispetto ai 34 esistenti;
- radici esclusive e campi ammessi;
- appartenenza di rotte, controllo, barriera, esiti e ragioni al registro;
- ordine e unicità degli esiti della barriera;
- riferimenti `data_from` soltanto a operazioni precedenti e dominanti;
- coerenza dei conteggi riepilogativi;
- nessun riferimento a elaborati di altri revisori.

## Possibili decisioni future da sottoporre a Roberto

Nessuna è necessaria per completare questa proposta. Prima del congelamento
finale può essere utile confermare, con revisione incrociata o umana, soltanto gli
11 casi a confidenza media, in particolare il confine fra significato astratto
della rotta e disponibilità concreta del provider.
