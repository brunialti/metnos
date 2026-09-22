# RM-0008 — proposta G8: autorita e prove d'ingresso a F5

Data: 15 settembre 2026.
Stato: **BOZZA PRECEDENTE SUPERATA DALL'HANDOVER; non approvata e non implementata**.
Base di prodotto: `5c1220ac25a1899b0d88aac5540fb0ab913db5ea`.
Checkpoint delle verifiche: `d408d202`, copia `codex/rm0009-development`.

L'ultima istruzione di Roberto richiede solo il passaggio di consegne a un
agente specializzato, non l'implementazione in questa task. Riferimento
corrente: [handover F5/F6](handover_rm0008_f5_f6_15_9_2026.md).
Questa bozza e conservata come proposta antecedente: non e esaustiva
(in particolare N3/N7/N8 dell'handover), non autorizza codice o nuove
autorita e non sostituisce le decisioni richieste da RM-0008 §17.

## 1. Decisione richiesta

Roberto aveva affidato al coordinatore anche il completamento F5/F6,
chiedendo di fermarsi alla conclusione per una revisione esterna; ha poi
sostituito l'incarico con il solo handover. Questa proposta antecedente
affrontava alcuni dei punti aperti di RM-0008 §17; non li ha risolti e
non chiede di ridurre la soglia, ripetere F4 o anticipare la conservazione F6.

Si propone di approvare insieme tre principi:

1. Un certificatore separato da Birth, con propria identita crittografica
   amministrativa e custodia protetta, puo attestare soltanto la prova
   d'ingresso a F5. Non puo pubblicare executor o cancellare oggetti.
2. I due cicli consecutivi sono ricostruiti da un registro autenticato di
   prove reali complete, con profilo e criteri fissati prima dell'esecuzione;
   non sono due turni scelti a posteriori.
3. Lo zero difetti deriva da un registro completo e autenticato dei difetti
   di integrita/aggiramento nel perimetro RM-0008 operativo, con chiusure
   sostenute da prove e revisione indipendente; non da un numero dichiarato.

Il coordinatore congela i dettagli tecnici e le prove del solo gruppo 8
prima di assegnare codice. La decisione non approva automaticamente il
contratto futuro `EXT-RM0008-F5`, i gruppi 9/10 o un nuovo schema non descritto.
Ogni ulteriore ampliamento di autorita resta soggetto al §17.

## 2. Fatti verificati e motivo dell'arresto

F4 e gia verificata in esercizio: `CLAUDE.mutabile.md:88-92` e
`internal/design/handover_rm0008_verifica_8_9_2026.md` concordano.
Il commit storico `b715a765` e antenato della base corrente e il diniego
compilato e vero. Quel valore e una prova di questa installazione, non una
costante da inserire nel nuovo protocollo. Non si ripete la transizione.

`runtime/executor_birth_lifecycle.py:97` autentica il contenitore
`F4Certification` e ne confronta liste e conteggi con la soglia. Non
ricostruisce le ammissioni e non conosce l'emittente o lo schema dei due
registri mancanti. Il parametro `authorities` arriva dal chiamante, non
da una radice produttiva dedicata gia installata.

I revisori indipendenti di integrazione e sicurezza confermano il medesimo
arresto. La conferma di autenticita del contenitore non rende veri gli
identificatori di prova che contiene.

## 3. Confini proposti dell'autorita

- Scopo unico della nuova chiave: `f5_threshold_certification_v1`.
  Separazione obbligatoria da autore, Admission, Producer, distribuzione,
  transizione, testa richiesta e chiave HTTP amministrativa.
- Custodia: account amministrativo del sistema, fuori dalle radici accessibili
  al servizio e agli executor. Su Linux chiave privata posseduta da
  `root`, modo `0600`, catena di directory controllata; su Windows
  protezione equivalente con ACL e prova nativa prima dell'uso.
- Il certificatore usa lettori pubblici/autenticati dei dati posseduti dai
  rispettivi componenti; non riceve le loro chiavi private. I nuovi accessi
  sono censiti e limitati nel controllo dei confini.
- Solo il percorso amministrativo previsto puo predisporre o ruotare
  l'identita. Nessuna creazione automatica da parte di un candidato o
  rimpiazzo silenzioso di un registro assente/corrotto.
- Rotazione: registri e atti precedenti restano conservati; attivazione della
  nuova identita tramite autorizzazione amministrativa esplicita, monotona e
  riletta. Una firma prodotta da una chiave revocata non consente una nuova
  attivazione, anche se il suo file e integro.
- Il processo ordinario riceve soltanto la prova e le chiavi pubbliche. Non
  accetta percorsi, registri o chiavi scelti dal chiamante della crescita.
- Un futuro incarico esplicito dovra assegnare progettazione e codice;
  questa bozza non li autorizza.
  Installazione della nuova autorita, prove sul servizio e pubblicazione
  conservano i controlli di rilascio e richiedono il perimetro operativo
  previsto; nessuna modifica reale viene effettuata con questa proposta.

## 4. Evidenze e soglia, senza nuovi conteggi discrezionali

La soglia resta cinque ammissioni tecniche reali, almeno due produttori,
ricevute rilette, due cicli consecutivi e zero difetti aperti.

Il certificatore parte dall'ancora F4 autenticata della singola installazione
e dalla catena dei contesti successivi. Per ogni atto eleggibile verifica:

1. autenticita e coerenza fra AdmissionReceipt, ProducerReceipt e risultato
   terminale della transazione; una riga SQLite che dice `committed` non basta;
2. identita esatte di contratto, generazione, candidato, richiesta, contesto,
   sorgente e predecessore, rileggendo i byte dal proprietario;
3. esecuzione del percorso produttivo, non fabbriche di prova o dati copiati
   con firme effimere; gli esiti di test restano in un dominio distinto;
4. deduplicazione per identita dell'atto e della richiesta: retry, rigioco,
   traduzione equivalente e riattestazione non aumentano le nascite tecniche;
5. produttore dal campo autenticato `issuer_id` e dalla registrazione della
   capacita: due operazioni o due chiavi ruotate dello stesso produttore
   non diventano due produttori.

I lettori delle ricevute attuali non sono lettori storici generici:
`read_current_birth_receipt_v2` verifica la corrente e il contesto sigillato;
`inspect_birth_receipts` dichiara esplicitamente una proiezione informativa.
Il nuovo enumeratore storico deve restare nel proprietario dello store e
autenticare ogni contesto tramite la sua catena; non deve aggirare quei
controlli o scegliere una vecchia chiave da un dizionario del chiamante.

Il dossier di soglia lega almeno: versione di politica, installazione,
ancora F4, punto finale della catena osservata, identita sorgente/build,
inventario delle ricevute ammesse ed escluse con motivo, profilo dei cicli,
teste dei registri, frontiere di lettura e impronta del certificatore.
Una frontiera incoerente, una pagina omessa o un riferimento senza prove
produce rifiuto, non un conteggio parziale positivo.

La prova d'ingresso e un fatto sul passato autenticato. Non autorizza
automaticamente una release discendente o un nuovo contesto: il controllo
dell'attivazione deve verificare separatamente compatibilita, revoche e
legame alla release che usa F5. Non si costruisce un riferimento circolare
fra certificato e manifest che ne includa gia l'impronta.

## 5. Due cicli consecutivi

Un ciclo e un'esecuzione completa del profilo di routing versionato e
registrato prima di partire, non una singola risposta riuscita.

- Stesso profilo e medesima selezione autenticata per la coppia; ogni caso
  previsto attraversa il percorso reale e ha un esito osservabile.
- Il profilo usa dati predisposti non personali e operazioni limitate; non
  autorizza messaggi esterni, credenziali reali o modifiche all'archivio utente.
- Ordine, identita dei casi e criteri degli esiti sono fissati in anticipo.
  Per il testo non deterministico si verifica la postcondizione prescritta,
  non si modifica la domanda o la soglia dopo un fallimento.
- Il registro conserva avvio, conclusione, interruzione e fallimento. Due
  successi separati da un ciclo fallito/interrotto non sono consecutivi.
- Identita selezionata prima e dopo, ricevute dei turni, inventario dei casi,
  esiti e differenze sono legati all'atto; un semplice `cycles=2` e rifiutato.
- Una differenza inspiegata impedisce il successo. Una spiegazione accettata
  richiede evidenza registrata e revisione, non un interruttore `ignore`.

Il profilo esatto dei casi, il formato dei record e la matrice produttore/
consumatore vanno congelati e revisionati dal coordinatore prima del codice.

## 6. Registro dei difetti

Il perimetro comprende integrita, autenticazione e aggiramenti dei percorsi
RM-0008 gia operativi e dei nuovi confini introdotti dal gruppo esaminato,
dall'ancora F4 alla frontiera di certificazione. Nessun problema noto viene
escluso solo perche trovato da RM-0009 o da un altro agente.

- La prima fotografia include tutti i rilievi noti delle fonti censite;
  ciascuno riceve classificazione motivata, responsabile e riferimenti.
- Le funzionalita future non ancora iniziate restano lavori aperti, non
  falsi difetti di una funzione dichiarata esistente. Ogni classificazione
  che incide sulla soglia viene revisionata indipendentemente.
- Apertura, chiusura e riapertura sono eventi append-only autenticati,
  numerati e collegati alla testa precedente. Non esiste cancellazione del
  rilievo per portare il contatore a zero.
- Una chiusura identifica codice correttivo, prova discriminante e revisione
  indipendente; l'autore del fix non puo produrre da solo l'intera chiusura.
- Il certificatore ricostruisce lo stato dell'intero registro alla frontiera
  riletta. Fonte mancante, intervallo non coperto, biforcazione, salto o
  rigioco di una vecchia testa negano la certificazione.
- Il registro non conserva segreti o testo personale: solo identita,
  riferimenti protetti e impronte delle prove necessarie.

Lo zero attesta il registro completo e revisionato nel perimetro dichiarato;
non pretende di provare l'assenza matematica di ogni possibile difetto ignoto.

## 7. Migrazione e distinzione da RM-0009

La migrazione rimane senza perdita: statistiche, storia e stato di promozione
per nome si conservano integralmente come `unresolved` quando manca
l'associazione autenticata alla generazione. Il nome corrente non prova
a quale generazione si riferisca un evento storico. Nuove epoche hanno
contatori zero; nessun override o feedback per nome passa al successore.

Prima di ritirare i lettori precedenti si riconciliano sorgenti, writer e
frontiera della copia. Rigiochi e arresti devono conservare stessi conteggi,
impronte e destinazioni. Non e ammessa cancellazione di tabelle legacy per
far apparire completata la riconciliazione. Lo schema fisico e la barriera
dei writer del gruppo 8 restano da congelare, non da inventare dall'agente.

La nuova prova di soglia **non** si chiama `EXT-RM0008-F5`. Quell'artefatto
appartiene alla futura attestazione di F5 realmente integrata, dopo il gruppo
9 e le prove necessarie. Un'eventuale attestazione della release con la sua
autorita esistente puo includere l'impronta della prova di soglia separata;
non autorizza il riuso della chiave di distribuzione per il nuovo scopo.
Il suo contratto comune con RM-0009 rimane il lavoro G0.3, non questa proposta.

## 8. Uscita da questa decisione

Approvazione richiesta soltanto sui principi e sui limiti dei §§3-7.
Dopo l'approvazione il coordinatore consegna il piano tecnico ottimizzato
del gruppo 8: schemi chiusi, file/API, proprietari, ordine dei blocchi,
punti di commit, errori, prove avversariali e criteri d'uscita. Le omissioni
tecniche non sono delegate agli agenti medium. Il piano non allarga i tre
scopi autorizzati senza una nuova decisione.

Non si aggiungono nuovi emittenti, registri autorevoli o collegamenti
produttivi F5 sulla base di questa bozza. L'elenco aggiornato delle decisioni,
il subentro e la sequenza dei gruppi sono nell'handover; l'eventuale
approvazione dei soli tre principi qui proposti non li esaurirebbe.
Questa task si ferma all'handover, senza implementare F5/F6 o riprendere RM-0009.

RM0008-Unita: RM-G8-DEC-01
RM0008-Ruolo: coordinatore
RM0008-Stato: BOZZA_SUPERATA_NON_APPROVATA
RM0008-Ambito: principi autorita e prove G8; nessun effetto di esercizio
