# LRE non avviato: turno 896a6841a7684507

Data: 2026-09-16. Stato: diagnosi; nessuna correzione o pubblicazione in produzione.

Aggiornamento successivo: su autorizzazione di Roberto, la correzione è stata
implementata. La prova diagnostica temporanea citata sotto è stata sostituita
da regressioni integrate in `test_executor_prerequisites.py` e
`test_image_indexing_boundaries.py`; l'esito storico rimane documentato qui.
Il rilascio è tracciato in `lre-placement-release-20260916.md`.

## Esito

Il turno «cerca localmente foto con il mare» non ha creato un lavoro LRE.
La catena contiene un conflitto riproducibile tra destinazione conversazionale
e destinazione effettiva del servizio indice: `PC-ROBERTO` viene inoltrato al
costruttore dell'indice, il cui contratto accetta solo il server.
Non è un lavoro bloccato o in loop: il rifiuto precede la creazione del job.

## Evidenze

- Turno iniziato alle 09:30:58.590574 UTC e terminato alle 09:31:02.182156 UTC.
- Contesto di destinazione della stessa conversazione aggiornato alle
  09:30:58.654340 UTC: destinazione `PC-ROBERTO`. Il dato cade dentro il turno,
  non è una preferenza precedente dedotta da altri turni.
- Il campo pubblico `target_device` del turno è invece nullo: non espone la
  destinazione richiesta quando l'executor non è effettivamente eseguito sul PC.
- `find_images_indices` è server-only (`device_ok=false`). Esegue quindi la
  ricerca sul server anche se la destinazione conversazionale è un dispositivo.
- In `agent_runtime.py`, il percorso dei prerequisiti inoltra senza normalizzarlo
  il `target_device` originale. `executor_prerequisites.py` lo passa alla
  sottomissione automatica.
- `durable_workloads/image_indexing.py::normalize_request` rifiuta ogni target
  diverso da `None`, stringa vuota o `server`, prima di leggere il percorso e
  prima di accedere alla coda.
- Esito osservato: `ERR_LRE_EXECUTOR_CONTRACT_UNSUPPORTED`;
  la risposta finale lo presenta come errore generico e suggerisce dettagli
  di percorso/dispositivo, senza spiegare il conflitto.
- Controllo delle 09:32:37 UTC: motore pronto, zero lavori, nessun riavvio del
  lavoratore. I due executor foto risultano attivi nel catalogo di esercizio.

Evidenze amministrative private, non da pubblicare:

- `/var/lib/metnos-admin/agent-runs/run-upezu9x3/full.json`: turno e stato LRE.
- `/var/lib/metnos-admin/agent-runs/run-yt46s7u6`: catalogo dei due executor.
- `/var/lib/metnos-admin/agent-runs/run-5qsauvpc`: destinazione e timestamp,
  letti con SQLite in sola lettura e filtro sul contesto esatto del turno.

## Riproduzione e limiti

`tests/runtime/executors/test_prerequisite_diagnosis.py` usa i manifest spediti,
schema e guard reali, un'autorità simulata e percorsi temporanei. La prova
differenziale attraversa la sottomissione e la normalizzazione reali, fermandosi
esplicitamente al controllo di disponibilità, prima di registro e database:

- target `PC-ROBERTO`: stesso `contract_unsupported`, senza raggiungere readiness;
- target `None` o `server`: normalizzazione superata, arresto diagnostico previsto;
- controlli preliminari del prerequisito: superati nella fixture.

Il test non certifica l'intera ammissione o l'indicizzazione. Il turno storico
non registra l'eccezione interna specifica: non si può escludere un ulteriore
errore precedente nascosto dal medesimo messaggio. Il conflitto di destinazione
è comunque documentato e sufficiente a rifiutare quella richiesta.

Esecuzione aggregata: 97 test superati, 1 fallito, 5 subtest superati. Il fallimento
è `DeviceEligibleManifestTests::test_device_ok_executors_declare_windows`:
il caricatore locale rifiuta il manifest di `list_dirs` per digest incoerente,
quindi il test non trova l'executor. Non è una prova di guasto del catalogo
produttivo. Non sono stati alterati firme, digest o trust per farlo passare.

## Correzione proposta, non implementata

1. Separare destinazione richiesta e destinazione effettiva dell'operazione.
   I prerequisiti devono ereditare un contesto di esecuzione attendibile, coerente
   con i contratti firmati e con la provenienza del percorso restituito.
2. Per una ricerca server-only e un costruttore server-only, preservare tale
   collocazione anche nel prerequisito. Non cancellare indiscriminatamente il
   target dei lavori remoti e non convertire richieste device-only in server.
3. Chiarire semanticamente «archivio locale» rispetto a «questo PC», senza
   introdurre eccezioni per parole nel codice degli executor. Una destinazione
   remota non supportata deve essere esplicita, non cambiata silenziosamente.
4. Registrare un codice causale strutturato del rifiuto: target incompatibile,
   argomenti invalidi, autorizzazione richiesta, percorso indisponibile,
   contratto assente. Non registrare credenziali o contenuti dell'archivio.
5. Coprire con test: server con contesto PC precedente, richiesta esplicita di
   PC, executor device-only, target ambiguo/offline, owner diverso, percorso
   non autorizzato, deduplicazione e creazione effettiva di un unico job.
6. Solo dopo la correzione verificata, collaudare l'ammissione completa e poi
   pubblicare con la pipeline canonica; nessun avvio forzato per aggirare i gate.

## English summary

The turn selected PC-ROBERTO while the index search ran on the server. The
prerequisite forwarded the original device target to a server-only indexing
adapter, which rejects it before job creation. Isolated differential tests
reproduce the rejection; server targets pass normalization. Production was
only inspected, not modified. The historical log suppresses the internal
reason, so an additional earlier failure cannot be ruled out. The proposed
fix must preserve trusted effective placement without weakening remote or
owner boundaries, and expose distinct rejection reasons.
