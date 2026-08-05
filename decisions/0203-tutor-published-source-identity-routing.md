# ADR 0203 — Tutor: instradamento per identità della fonte pubblicata

**Stato:** accepted  
**Data:** 2026-07-28  
**Roadmap:** RM-0003

## Contesto

Una domanda come «cosa contiene il file
`Metnos_Prospettive_Estese_v1.html`?» può sembrare una normale operazione sui
file. Se arriva dopo un turno collocato su un dispositivo remoto, il normale
motore può anche ereditare quel dispositivo e cercarvi il percorso. Il nome,
però, identifica una pagina ufficiale già ammessa nel catalogo del Tutor: la
domanda chiede della documentazione di Metnos, non del filesystem dell'utente.

Una regola basata sulla parola «file», su una frase o su una cartella presunta
sarebbe fragile. Anche inoltrare ogni nome con estensione HTML al Tutor
sottrarrebbe vere letture, modifiche e cancellazioni al motore operativo.

## Decisione

Prima del classificatore di modo, il runtime confronta il testo con l'inventario
canonico prodotto da `runtime/published_docs.py`. Sono identità ammesse:

- il nome completo del file;
- il percorso relativo alla pubblicazione, con o senza il prefisso `docs/`;
- l'indirizzo canonico completo e il suo percorso URL.

Il confronto è esatto, senza distinzione fra maiuscole e minuscole, e rispetta i
confini del token. Un percorso o un URL prevalgono su un nome condiviso; la
lingua corrente risolve le traduzioni con lo stesso nome. Ogni ambiguità residua
fallisce chiusa.

Il risultato è un'attestazione del runtime, non una deduzione del modello. Il
classificatore usa l'attestazione per distinguere:

- lettura, descrizione o riassunto della fonte: `EXPLAIN`;
- modifica, spostamento, cancellazione, condivisione o altro uso operativo:
  `ACT`;
- presenza di entrambe le parti: `MIXED`.

Per `EXPLAIN`, il recupero viene vincolato al `source_ref` esatto del documento
nel catalogo Tutor firmato. La somiglianza semantica ordina soltanto le sezioni
di quella fonte e non può deviare verso un executor, un'altra pagina o un
dispositivo. Se il catalogo firmato non contiene più la fonte, il Tutor dichiara
una lacuna e non ripiega su una ricerca di file.

L'inventario riconoscibile comprende esclusivamente pagine pubbliche,
indicizzabili e validate. File personali, note interne, roadmap e report non
acquisiscono questo trattamento. Il normale collocamento server/dispositivo
rimane invariato per ogni nome non riconosciuto e per le richieste operative.

## Cache e aggiornamento

Il riconoscimento precede il planner e non modifica le chiavi L0/L1. Per non
rileggere ogni pagina a ogni turno, il processo conserva l'inventario delle
identità e lo invalida quando cambiano percorso, dimensione, tempo di modifica o
tempo di variazione del file. Il catalogo semantico conserva il proprio ciclo
separato di compilazione, firma e sostituzione atomica.

## Conseguenze

- L'utente non deve imparare una formula speciale: basta citare correttamente
  nome, percorso o collegamento della documentazione.
- Un nome sconosciuto o ambiguo non viene promosso a documentazione per
  somiglianza.
- Riconoscere una fonte non concede autorità su di essa e non trasforma una
  cancellazione in una spiegazione.
- Il dispositivo usato nell'ultimo turno non influenza una domanda su una fonte
  pubblicata.
- La soluzione vale automaticamente per ogni nuova pagina ammessa, in ogni
  lingua, senza aggiungere nomi o frasi al codice di instradamento.

## Prove

- `tests/runtime/test_published_docs.py`: identità per nome, percorso e URL,
  scelta della lingua, ambiguità e confini del token;
- `tests/runtime/tutor/test_tutor_f1.py`: vincolo alla fonte prima della
  similarità;
- `tests/runtime/tutor/test_tutor_f3_f4.py`: lettura servita dal Tutor e
  cancellazione lasciata al motore;
- `scripts/certify_tutor_f3.py`: turno reale sul documento pubblico, fonti
  tutte appartenenti allo stesso `source_ref`, collegamento canonico e nessuna
  azione.
