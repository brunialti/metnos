# RM-0008 — RM-VARIAZIONE-03: adozione esplicita delle correnti iniziali

Data: 7 settembre 2026
Stato: implementata sul candidato, verifica produttiva non ancora eseguita
Unita': `RM-VARIAZIONE-03`

## 1. Guasto riprodotto

Il primo passaggio F4 ha riattestato 22 generazioni non revertibili e si e'
fermato sulla prima generazione revertibile con
`birth_ownership_receipt_proof_invalid`. Il negozio e' rimasto nello stato
coordinatore `PREPARED`, prima del punto di non ritorno.

La causa non e' nell'executor fermato. Il runner centrale delle proprieta'
invia agli entrypoint il campo privato `birth_property_action`; nessun
executor reale lo implementa. Inoltre il profilo legge `[output].properties`,
mentre il contratto ammesso usa `[output].schema_inline`. Sul catalogo del
repository sono stati misurati 107 manifest: 106 dichiarano `schema_inline`,
zero dichiarano `output.properties`, 24 sono revertibili e zero fra questi
implementano il protocollo privato.

Correggere soltanto il parser attiverebbe quindi piu' casi del runner
incompatibile. Adattare i 24 executor replicherebbe un involucro estraneo alla
loro funzione e al contratto esistente. Riutilizzare `runtime/test_runner.py`
non e' equivalente: quel runner accetta `setup` e `teardown` shell del
manifest, mentre la prova Birth deve usare fixture e comandi chiusi dal core.

## 2. Regola minima

Le generazioni correnti anteriori a F4 non possono implementare
retroattivamente un protocollo che non appartiene ai loro byte. La prima
transizione le adotta quindi con una regola nominata, senza dichiarare come
eseguita una prova dinamica che non e' stata eseguita:

1. la regola e' selezionabile soltanto dal contesto di riattestazione staged;
2. la distribuzione autenticata deve avere `release_sequence == 1` e nessun
   `previous_closed_build_id`;
3. la dipendenza sigillata porta l'identita' esatta della transizione;
4. ogni richiesta V2 deve appartenere a quella stessa transizione;
5. il controllo `properties` emette `not_applicable` con evidenza legata a
   transizione, candidato e contesto;
6. la ricevuta aggiunge il controllo passato
   `initial_current_generation_adoption_v1`, legato anche a contratto,
   generazione e sorgente;
7. la revisione semantica retrospettiva e' anch'essa `not_applicable`: i 16
   contratti importati correnti non hanno evidenza indipendente nel nuovo
   insieme e inventarla renderebbe falso il controllo;
8. standard del manifest, lint, chiusura, approvazione, identita' dello
   snapshot e persistenza V2 restano invariati.

Una riattestazione richiesta dalla testa corrente, una distribuzione
successiva o una richiesta con transizione differente non ricevono questa
regola e continuano a fallire chiuse se la prova dinamica non e' disponibile.
Non cambia alcun manifest o entrypoint executor e non nasce un adattatore per
executor.

La prova completa sui 122 contratti ha inoltre mostrato che una revisione LLM
retrospettiva non sarebbe soltanto priva dell'evidenza indipendente obbligatoria:
richiederebbe una chiamata per ognuno dei 16 contratti importati e la prima ha
gia' superato la scadenza di 30 secondi. L'adozione esplicita evita quindi sia
un'affermazione di sicurezza falsa sia lavoro remoto inutile; le nascite e le
revisioni successive conservano integralmente la revisione semantica.

## 3. Recupero dei residui PREPARED

La transizione interrotta puo' avere gia' prodotto ricevute e claim per le
generazioni precedenti al primo errore. Questi record non devono essere
cancellati: la ricevuta V2 e' indicizzata da
`(contratto, generazione, admission_context_id)` e la richiesta Producer V2
include transizione, epoca, insieme target, contesto e identita' del sorgente.
Una transizione recuperata e un nuovo sorgente producono quindi identita'
diverse; le ricevute dei due contesti coesistono e nessuna e' un fallback per
l'altra.

La recovery deve quarantinare soltanto il contenitore coordinatore `PREPARED`,
la directory V2 corrispondente e l'insieme di autorita' target autenticato.
Ricevute e claim terminali restano storia append-only. Dopo la recovery il
passaggio riparte con un nuovo sorgente e una nuova transizione.

## 4. Identita' Python della convergenza

L'analisi preventiva del percorso completo ha trovato una regressione separata
introdotta nel passaggio alla convergenza installata. Il descrittore conserva
correttamente il Python amministrativo che costruisce la release, mentre il
catalogo firmato conserva separatamente l'unico Python di servizio e il relativo
hash. La convergenza veniva pero' avviata con il primo: sulla macchina reale
`/usr/bin/python3.12` non contiene `tomlkit`, mentre l'ambiente di servizio
attestato la contiene.

La correzione non cambia il descrittore e non introduce un terzo ambiente. La
convergenza ricattura il catalogo dalla distribuzione verificata, richiede una
sola identita' Python per tutte le entry `python_module` e avvia quel percorso
esatto. Zero, due o un percorso assente/non-file vengono rifiutati prima di creare
il processo. In questo modo il cutover non richiede piu' di proiettare una
libreria del virtual environment nel Python di sistema con un bind mount.
`PY-RUNTIME-IDENTITY-001` resta aperta per l'unificazione complessiva della
prossima release; qui viene rimossa soltanto la regressione che interessava la
convergenza RM-0008.

La stessa analisi ha corretto il budget del processo chiuso: il padre concedeva
10 minuti a un percorso che include una convergenza internamente limitata a 20
minuti. Il limite e' ora 50 minuti e una prova lo mantiene maggiore della somma
dei limiti di convergenza, attivazione e verifica. E' un limite massimo, non un
ritardo aggiunto al percorso normale.

## 5. Lettura storica prima della preparazione del nuovo contesto

La verifica della prima migrazione deve distinguere autenticazione dei byte
storici ed esecuzione sotto autorita'. Ricostruire il contesto V1 dai sorgenti
nuovi prima di leggere i contratti invariati produce correttamente un mismatch,
ma nel punto sbagliato: la lettura non richiede quel runtime.

Una vista immutabile condivisa autentica marker, insieme, materiali e legami
delle chiavi sotto la stessa barriera, restituendo soltanto identita' e chiavi
pubbliche. La convergenza, la verifica iniziale con ricevute deferite alla V2 e
l'enumerazione sul lato storico usano questa vista. Il runtime rigoroso viene
costruito soltanto se serve davvero pubblicare un contratto; in quel caso il
contesto V1 non corrispondente continua a essere rifiutato. Nessun runtime puo'
quindi eseguire una vecchia autorita' su sorgenti diversi. La riattestazione V2
rimane legata al nuovo contesto della distribuzione autenticata.

## 6. Prove richieste

- selezione positiva solo per la prima distribuzione staged;
- rifiuto per contesto richiesto, distribuzione successiva e transizione
  differente;
- esecuzione completa della riattestazione senza invocare il protocollo
  privato e presenza dei due controlli distinti nella ricevuta;
- ripetizione idempotente della stessa richiesta;
- identita' Producer differenti dopo recovery o cambio di sorgente;
- persistenza e rilettura indipendente di due ricevute V2 della stessa
  generazione in due contesti;
- descriptor amministrativo e Python di servizio distinti, con esecuzione
  vincolata al secondo;
- rifiuto prima del subprocess per zero o due identita' Python di servizio;
- budget del processo chiuso maggiore dei sottoprocessi che contiene;
- migrazione con contesto storico A e sorgenti realmente diversi B: lettura
  autenticata e convergenza senza pubblicazione, runtime V1 ancora rifiutato;
- rifiuto di alterazioni a insieme storico, chiavi o binding del contratto;
- suite RM-0008 e prova completa sul clone isolato prima di ogni atto live.

RM0008-Unita: RM-VARIAZIONE-03
RM0008-Ruolo: recovery-f4
RM0008-Stato: IMPLEMENTATA-IN-VERIFICA
RM0008-Ancora: worktree candidato del 7 settembre 2026
RM0008-Percorsi: runtime/executor_birth_bootstrap.py; runtime/executor_birth_reattestation.py; runtime/executor_birth_shadow.py; internal/design/rm0008_variazione_03_adozione_correnti_iniziali_7_9_2026.md
RM0008-Prova: censimento 107/106/24/0; 2049 test portable verdi e 32 skipped (2044 diretti + 5 ownership sotto fakeroot); 122/122 correnti con replay esatto; recovery integrata V2 -> legacy -> reset verde
RM0008-Ambito: prima transizione F4 staged
