# V26.2 minimal Phase 1 — esito K1 redatto

Questo report descrive l'unico run nativo autorizzato sui 34 controlli. Non
contiene query, risposte raw o segmenti linguistici: gli identificatori sotto
sono prefissi di 12 caratteri degli `opaque_case_id` congelati.

## Provenienza

- versione: `metnos.v26.2-minimal-phase1/1.0`;
- output: `metnos_v262_minimal_phase1_controls_k1.json`;
- SHA-256 output: `ae8020a29ade3de48922d64cc61fe5f5bf9349fa3defda6643d0c4716757b834`;
- SHA-256 freeze: `757f461e26cb39083c039d4a4f3871e56e94a3bc30e5c838ff8a1401d5e8ff9d`;
- run nativo: sì; credito post-hoc: no;
- chiamate modello: 34; retry: 0; errori di trasporto: 0;
- latenza per chiamata: p50 `3108.54 ms`, p95 `4194.95 ms`.

## Esito del gate

Il candidato **non passa** il gate: 26/34 casi sono valutabili, 5/26 hanno la
tupla esatta, 20/26 hanno il binding esatto e 17/26 hanno tutte e tre le prove
richieste. Sui casi valutabili i positivi sono 3/7 e il leakage negativo è
2/19. Rispetto all'intera fixture, 2 dei 9 positivi e 6 dei 25 negativi non
sono valutabili; pertanto i rapporti sui soli casi valutabili non vanno letti
come 3/9 o 23/25 successi.

## Pattern di failure

Le categorie `schema/validator invalid` e `ambiguous` sono mutuamente
esclusive e non entrano nei denominatori delle categorie successive. `Tuple
mismatch` ed `evidence mismatch` sono invece assi ortogonali e possono
sovrapporsi.

### Schema/validator invalid — 1/34

- `5940e59955c4`: il JSON è arrivato e non c'è stato errore di trasporto, ma
  il validator ha rilevato due errori `evidence_clause`; gli intervalli della
  prova `clause_semantics` non identificavano la clausola dichiarata.

### Ambiguous — 7/34

I frame sono strutturalmente validi, ma almeno una clausola ha
`interpretation=ambiguous`; il protocollo fail-closed li esclude dalla
valutazione. Due appartengono ai nove positivi e cinque ai venticinque
negativi.

`543baf5a0477`, `c07a4e4a2b76`, `ea7817740f76`, `82bf368c4405`,
`8d20d29e5761`, `999152661d3a`, `315eb70d2e87`.

### Tuple mismatch — 21/26 valutabili

Solo 5/26 tuple coincidono integralmente con l'oracolo. Gli exact per singolo
campo, che non sono additivi, sono: `role` 23/26, `interpretation` 19/26,
`speech_act` 23/26, `relation` 12/26, `subject_ref` 20/26,
`grammatical_person` 21/26 e `time_scope` 22/26. Il collo di bottiglia è quindi
la relazione, seguito dalla scelta fail-soft `unsupported` in 7 casi
valutabili; non emerge una singola correzione locale sufficiente.

Sei mismatch di tupla producono anche un errore di binding: quattro falsi
negativi e due leakage negativi. Gli altri quindici conservano il binding
binario corretto pur sbagliando almeno una dimensione della tupla.

`f94859539aaa`, `962e2eb3e35a`, `3f5bd2980e69`, `b5626529f15b`,
`9963512e338b`, `3e66ef71beea`, `9afa61063dd2`, `6d2c032c34de`,
`a7074afa19ef`, `d85300f0a5e8`, `085f1a9bbd8a`, `66997e917143`,
`14a4d01b2942`, `a7fe1472e45e`, `f67ee9ac145a`, `402060ae7e74`,
`ae25856617b3`, `6234733a0b72`, `298fa0b79312`, `703f61679ee0`,
`f65e576916a3`.

### Evidence mismatch — 9/26 valutabili

Nove frame valutabili usano `kind=none` per almeno una delle tre prove
obbligatorie. Manca la prova temporale in 6 casi e quella della relazione in
5; in 2 casi mancano entrambe. La prova del soggetto è sempre presente. Otto
dei nove casi hanno anche una tupla errata; uno ha tupla esatta ma evidence
incompleta, quindi soltanto 4/26 casi soddisfano insieme tupla, binding ed
evidence.

`9963512e338b`, `3e66ef71beea`, `9afa61063dd2`, `6d2c032c34de`,
`8230c30a0a28`, `085f1a9bbd8a`, `a7fe1472e45e`, `6234733a0b72`,
`298fa0b79312`.

## Decisione

V26.2 resta un artifact negativo utile: dimostra che uno schema minimale,
Unicode e privo di liste linguistiche non basta, con questo decoder e questo
contratto, a rendere affidabili relazione, ambiguità ed evidence in una sola
chiamata. Nessuna route integration, full 109 o cutover runtime è autorizzato
da questo risultato; il freeze e l'output non vanno modificati o rilanciati.
