# Review avversariale RM-0008 / Release 3

Data: 10 settembre 2026. Revisione: 2 — consolidamento dopo tre revisioni
indipendenti per ambito.

Worktree: [/opt/metnos/.claude/worktrees/rm0008-reboot](/opt/metnos/.claude/worktrees/rm0008-reboot)

Ramo: `codex/rm0008-reboot`. Base esaminata: `8519edb1`.
HEAD esaminata e ricontrollata: `959b275e`.

Stato locale: 13 file già modificati e non committati, inclusi i fix
lettori/scrittori, riconciliazione e Telegram. Non modificati dalla review.

## Verdetto consolidato

**Il NO-GO assoluto della prima versione, motivato da un P0, è ritirato.**
Nel perimetro riesaminato restano **6 P2 e 3 P3**; A-08 è ritirato dai
rilievi e conservato come osservazione contrattuale. Non sono stati dimostrati
un superamento dei privilegi concessi all'operatore, un aggiramento delle firme
o l'ammissione di autorità estranee.

Il giudizio distingue due situazioni:

- **Ciclo manuale attuale:** lo sviluppatore, il programma amministrativo e i
  materiali preparati sono assunti fidati; opera un solo amministratore.
  I rilievi non giustificano un divieto incondizionato del prossimo rilascio.
  Richiedono però materiali identificati e stabili, trattamento esplicito dei
  residui ambigui e una procedura di ripresa verificata.
- **Futuro aggiornamento automatico:** la procedura attuale non dimostra
  l'autenticazione di sorgenti meno fidate né un esito legato alla release
  richiesta in presenza di aggiornamenti concorrenti. Queste proprietà vanno
  provate prima di esporla come capacità autonoma del prodotto.

**Questa review non rilascia un GO operativo:** non è stato verificato di nuovo
lo stato live, né eseguito un attraversamento, un riavvio o una prova completa
con systemd. La Release 3 in esercizio non è stata modificata né fermata.

Secondo la [consegna](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/design/handover_rm0008_release3_9_9_2026.md:19),
Release 3 è completata: il successore ordinario seguirebbe il percorso del
journal completato, non quello abbandonato di A-02. Questo dato proviene dalla
consegna, non da una nuova misura della macchina.

## Cosa cambia rispetto alla prima versione

| ID | Prima | Consolidato | Motivo |
|---|---|---|---|
| A-01 | P0 | P2 | Fiducia manuale omessa; il figlio normale usa una release già verificata |
| A-02 | P1 | P2 | Mancano confronti tra identità, non il decoder canonico né ogni verifica successiva |
| A-03 | P1 | P2 | Archiviazione di un journal ambiguo riprodotta; nessun aggiramento delle firme provato |
| A-04 | P1 | P2 | Timeout confermato anche a 5 s, ma sotto carico artificiale continuo |
| A-05 | P1 | P2 | Finestra confermata; falsa attribuzione possibile, ritorno non autorizzato non provato |
| A-06 | P2 | P2 | Ripresa difettosa riprodotta; impatto sulla disponibilità |
| A-07 | P2 | P3 | Vincolo eccessivo nei test, non rimozione attuale di protezioni da altri servizi |
| A-08 | P2 | Osservazione | Non individuata una promessa di corrispondenza esatta fra digest e disponibilità |
| A-09 | P3 | P3, causa corretta | Non è la required-head: manca l'isolamento del certificato di proprietà |
| A-10 | P3 | P3 | Riferimenti obsoleti nella consegna confermati |

Le sezioni seguenti sostituiscono le precedenti conclusioni e prescrizioni.

## Perimetro e metodo

Dimensione censita del cambiamento: 34 commit fra base e HEAD; diff del ramo di
136 file, 12.798 inserimenti e 576 rimozioni; diff locale di 13 file,
620 inserimenti e 82 rimozioni. Sono state lette la consegna completa e le
istruzioni del progetto; l'analisi del codice e dei diff è stata mirata ai
confini descritti sotto. **Non è una lettura esaustiva di tutti i 136 file**:
la formulazione iniziale sulla copertura era troppo ampia.

La seconda revisione è stata assegnata a tre agenti con contesti separati.
Ognuno ha letto il documento completo e verificato autonomamente il codice
della propria area, cercando anche prove contrarie:

| Revisore | Ambito | Verifiche aggiuntive |
|---|---|---|
| `review_trust` | A-01, A-03, A-06; confine di fiducia | Ordine degli import senza root; rinomina reale di journal con header vuoto |
| `review_lifecycle` | A-02, A-05; recupero e attivazione | 8 casi: base coerente, cinque mutazioni, header invalido, concorrenza simulata |
| `review_concurrency` | A-04, A-07, A-08, A-09, A-10 | 5 casi: scrittore con timeout reale, quattro controfattuali sui test |

Sono tre revisioni indipendenti **per ambito**, non tre certificazioni complete
del repository. Il consolidamento ha confrontato i risultati con codice e
sonde; le conclusioni non derivano dal solo voto dei revisori.
Il testo consolidato è stato riletto dai tre revisori nelle rispettive sezioni;
è stata recepita anche l'ultima precisazione su quale oggetto viene rinominato
in A-03.

Il coordinatore della review ha inoltre riprodotto A-06 su dati temporanei e
rieseguito il controllo privato delle sorgenti. Tutte le sonde sono circoscritte
a directory temporanee, senza privilegi reali, rete, deploy o azioni sui
servizi. Il solo file del repository aggiornato è questo report.

## A-01 — P2 — Il censimento non autentica la provenienza del bootstrap

### Evidenza e controprove

Il [ciclo, da riga 450](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_release_cycle.py:450)
legge percorso e censimento dallo stesso handoff. A riga 462 inserisce quel
percorso negli import e carica il candidato prima delle verifiche che il
candidato stesso contiene. Il confronto con il pin letto dallo stesso materiale
non stabilisce una provenienza indipendente.

Il [censimento](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_release_cycle.py:80)
controlla i metadati e poi rilegge i percorsi: non mantiene aperti gli stessi
file fino all'import né verifica il proprietario del materiale. L'handoff
prevedibile in `/tmp` non identifica da solo un preparatore autorizzato.

La sonda con un modulo-sentinella conferma l'import prima dell'autenticazione.
Il controllo iniziale dell'UID era simulato; **il processo non era root**.
Questa prova dimostra l'ordine delle operazioni, non un'escalation.

Le controprove impongono di ritirare il P0:

- La [consegna](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/design/handover_rm0008_release3_9_9_2026.md:484)
  dichiara un programma di sviluppo eseguito manualmente con `sudo`.
  Alterare quello stesso programma già autorizzato non prova un superamento
  dei privilegi concessi dall'operatore.
- Un `prepare` riuscito produce handoff `0644`, file `0644/0755` e
  directory `0755`
  ([preparazione](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_release_cycle.py:220),
  [copia](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_release_cycle.py:249)).
  La directory condivisa non prova che un altro utente possa sostituire questi
  oggetti: contano proprietari e protezioni dei genitori. Non è stato dimostrato
  un aggiramento dopo una preparazione riuscita.
- Il figlio `_cross` riceve normalmente il percorso restituito dal
  [costruttore](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_release_cycle.py:495).
  Quest'ultimo autentica, verifica e pubblica la distribuzione
  [prima di ritornare](/opt/metnos/.claude/worktrees/rm0008-reboot/install/executor_birth_distribution_release.py:652).
  Assumendo fidato il costruttore, non è un secondo ingresso di codice non
  autenticato. La precedente affermazione in questo senso è ritirata.
- L'esclusione degli strumenti interni dal
  [censimento delle sorgenti esportate](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_public_source_review.py:18)
  è reale. È una lacuna se quel controllo viene presentato come verifica di
  tutta la procedura amministrativa, non una prova autonoma di escalation.

### Impatto e intervento raccomandato

Il flusso manuale assume fidati preparatore, strumento e materiali importati.
Questa assunzione va dichiarata e preservata: un handoff estraneo accettato o
una modifica da parte di chi controlla il materiale importato farebbe eseguire
quel codice con i privilegi dell'operatore. Non è stato provato che tali
prerequisiti siano ottenibili da un terzo nel flusso documentato.

Il requisito è: **i byte eseguiti con privilegi devono essere quelli
autorizzati e non devono poter cambiare tra controllo e uso**. Un ricevitore
installato e una copia protetta verificata prima degli import sono una
soluzione; anche l'autorizzazione esplicita dell'intero bootstrap manuale deve
coprire provenienza e stabilità. Proprietario, creazione esclusiva e accessi
tramite descrittori sono strumenti per realizzare la proprietà, non la
sostituiscono. Non è dimostrato che sia obbligatoria un'unica architettura
«N deve sempre verificare N+1».

## A-02 — P2 — Il journal abbandonato non viene confrontato con tutte le identità del predecessore

### Evidenza e riproduzione

Il [ritiro del journal abbandonato](/opt/metnos/.claude/worktrees/rm0008-reboot/install/birth_authority_provisioner.py:4055)
autentica il legame tra documento di abbandono e record del predecessore,
seleziona il journal tramite `provisioning_transaction_id`, poi lo archivia.
Manca il confronto di cinque campi dell'header con il record:

`request_id`, `closed_build_id`, `previous_set_id`,
`distribution_payload_hash`, `distribution_signature_hash`.

Non è corretto affermare che il percorso controlli soltanto nome e presenza:
[read_state()](/opt/metnos/.claude/worktrees/rm0008-reboot/install/birth_authority_provisioner.py:1379)
verifica già formato canonico, inventario, identificazione della transazione,
sequenza e concatenazione degli hash dei checkpoint.

La nuova sonda parte da una base realmente preparata e pubblicata dalla
[fixture di ciclo di vita](/opt/metnos/.claude/worktrees/rm0008-reboot/tests/portable/test_birth_authority_journal_lifecycle_v2.py:91),
validata prima con il
[controllo del journal completato](/opt/metnos/.claude/worktrees/rm0008-reboot/install/birth_authority_provisioner.py:3982).
Il record viene portato nominalmente a `HEAD_REQUIRED` mantenendo i legami.
Alterando separatamente ciascuno dei cinque campi:

- il journal viene archiviato conservando byte e inode;
- la preparazione del successore prosegue.

Il caso non alterato passa. Come controprova, un header `{}` viene già
rifiutato senza spostamenti. Le fixture del coordinatore e della distribuzione
sono sostituti di prova, non distribuzioni live firmate: preparazione,
archiviazione e filesystem del journal sono reali.

### Impatto e intervento raccomandato

È una lacuna nell'integrità del recupero: una contraddizione non viene
segnalata e il journal esce dalla posizione attiva. Non sono state dimostrate
ammissione di chiavi o release estranee. Il
[chiamante](/opt/metnos/.claude/worktrees/rm0008-reboot/install/birth_authority_provisioner.py:4308)
e il [lettore del contesto precedente](/opt/metnos/.claude/worktrees/rm0008-reboot/runtime/executor_birth_prepared_root.py:527)
controllano già grafo, abbandono e contesto storico; il successore è validato
separatamente.

Confrontare le identità comuni con il record esatto del predecessore e legare
piano, checkpoint e set alle rispettive prove. Non copiare indiscriminatamente
le condizioni del percorso completato:

- `header.previous_set_id` indica il set antecedente a N, mentre il
  `previous_set` della preparazione N+1 è il set prodotto da N;
- l'inventario sorgente va legato alla distribuzione del predecessore, non a
  quella del successore;
- un abbandono lecito resta `HEAD_REQUIRED`: pretendere
  `PREFLIGHT_VERIFIED` ricreerebbe il blocco che l'uscita in avanti risolve;
- incompletezze e checkpoint vanno valutati rispetto alla fase realmente
  raggiunta; conservare la ripetizione lecita dopo una rinomina già completata.

## A-03 — P2 — Il ritiro degli orfani archivia anche journal ambigui

### Evidenza e riproduzione

Il [ritiro degli orfani](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_release_cycle.py:403)
scorre tutti i journal attivi e seleziona quelli la cui richiesta non compare
nel coordinatore. Non li lega al tentativo appena ritirato e non usa il decoder
canonico. Viene chiamato
[anche se nessuna claim è stata ritirata](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_release_cycle.py:486).

Sonda isolata: un journal il cui `transaction-v2.json` contiene `{}` viene
realmente rinominato sotto il prefisso `superseded`; un journal con richiesta presente
nel coordinatore viene conservato. Rinomina senza sostituzione e sincronizzazione
sono reali; il controllo dei genitori è circoscritto alla directory temporanea
e al proprietario corrente, come nella
[prova generale esistente](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_rehearse_withdrawal.py:59).

Il [decoder canonico](/opt/metnos/.claude/worktrees/rm0008-reboot/install/birth_authority_provisioner.py:608)
rifiuterebbe quell'header. Cambiando prefisso, invece, il journal esce
dall'insieme attivo esaminato dal
[recupero ordinario](/opt/metnos/.claude/worktrees/rm0008-reboot/install/birth_authority_provisioner.py:3904).

### Impatto e intervento raccomandato

L'assenza di una richiesta non prova l'identità né lo stato del residuo.
Il difetto riguarda classificazione e recupero, non una cancellazione: i byte
sono conservati. La procedura acquisisce già
[blocchi di deployment, avvio e provisioning](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_release_cycle.py:272);
le verifiche successive di claim, distribuzione e contesto precedente restano
attive. Non è stato dimostrato un loro aggiramento.

Usare forma e stato canonici, collegare l'eventuale ritiro al tentativo esatto
e non trasformare un residuo ambiguo in autorizzazione a proseguire.
Distinguere un tentativo superato da una richiesta valida in ripresa.
**Zero journal è un normale esito senza operazioni**: la precedente
prescrizione di fermarsi anche in quel caso era errata.

## A-04 — P2 — La priorità degli scrittori non vale tra processi distinti

### Evidenza e riproduzione

La [priorità locale](/opt/metnos/.claude/worktrees/rm0008-reboot/runtime/contract_store.py:1826)
impedisce nuovi lettori quando attende uno scrittore nello stesso processo.
Tra processi, il codice usa
[tentativi non bloccanti](/opt/metnos/.claude/worktrees/rm0008-reboot/runtime/contract_store.py:1956)
e [attese ripetute](/opt/metnos/.claude/worktrees/rm0008-reboot/runtime/contract_store.py:2085),
senza un meccanismo condiviso che sospenda l'ammissione dei nuovi lettori.

La prima sonda usava un timeout ridotto a 300 ms. La revisione indipendente
conferma il comportamento anche con il
[default reale di 5 s](/opt/metnos/.claude/worktrees/rm0008-reboot/runtime/contract_store.py:87):

- sei processi lettori, 480 acquisizioni complessive;
- ogni presa dura circa 60 ms al massimo, ma le prese si sovrappongono;
- lo scrittore scade dopo 5,001 s;
- cessate le letture, lo scrittore acquisisce il blocco.

### Impatto e intervento raccomandato

Sotto letture continuamente sovrapposte una pubblicazione può esaurire il
tempo di attesa. Il fix resta corretto nel consentire lettori contemporanei,
ma non garantisce che ogni scrittore venga ammesso tempestivamente.
Riconciliazione e
[guardia di transizione](/opt/metnos/.claude/worktrees/rm0008-reboot/runtime/contract_cutover_guard.py:223)
usano inoltre un'attesa di 2 s.

Il carico della sonda è **artificiale e continuo**: non misura frequenza o
incidenza sul traffico reale e non dimostra corruzione. La consegna descrive un
guardiano periodico, non sei lettori continui; questo finding non blocca da solo
un rilascio manuale a sistema quieto.

Se è richiesta una garanzia di attesa limitata, introdurre un'ammissione degli
scrittori condivisa fra processi, con ordine dei blocchi verificato e prove
sotto carico. Un blocco separato attraversato dai lettori e trattenuto dallo
scrittore in attesa è un'opzione, non una correzione già implementata.

## A-05 — P2 — Il completamento non protegge anche attivazione e risultato

### Evidenza e limiti della prova

Il [completamento](/opt/metnos/.claude/worktrees/rm0008-reboot/install/birth_authority_provisioner.py:5460)
detiene il blocco di deployment fino al
[ritorno](/opt/metnos/.claude/worktrees/rm0008-reboot/install/birth_authority_provisioner.py:5731).
Solo dopo, l'[orchestratore](/opt/metnos/.claude/worktrees/rm0008-reboot/install/executor_birth_transition.py:371)
avvia la topologia e restituisce l'identità della distribuzione precedente
all'attivazione. Anche il
[ciclo di rilascio](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_release_cycle.py:596)
segue questo percorso senza un blocco esterno.

È quindi possibile interporre un avanzamento amministrativo N+1 fra
completamento di N e avvio. La sonda dell'orchestrazione, con blocco reale
temporaneo ma completamento, verificatore e systemd simulati, restituisce N
mentre la selezione simulata è già N+1. Dimostra l'assenza di un controllo
finale d'identità nel raccordo; **non è una prova con due attraversamenti
reali concorrenti**.

I [controlli amministrativi di avvio](/opt/metnos/.claude/worktrees/rm0008-reboot/runtime/executor_birth_admin_preflight.py:14430)
autenticano la selezione corrente e la configurazione del servizio.
`start` usa il nome dell'unità installata, non un comando libero di N.
Non è stato dimostrato un ritorno non autorizzato a una vecchia release.

### Impatto e intervento raccomandato

In presenza di un reale avanzamento concorrente, sono plausibili un errore
d'avvio o l'attribuzione del successo alla release sbagliata. Un semplice retry
della stessa N non implica da solo una release diversa. La gravità attuale è
P2; il rischio cresce se l'aggiornamento diventa concorrente e automatico.

Conservare il blocco di deployment, acquisito una sola volta
dall'orchestratore, fino all'attivazione e alla verifica dell'identità finale.
Passare la sessione già posseduta al nucleo. Liberare invece blocco degli avvii
e catalogo prima dello start: i servizi ne hanno bisogno per partire.
La [specifica dell'ordine dei blocchi](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/roadmap/RM-0008-porta-unica-nascita-executor.md:796)
è un riferimento progettuale, non la prova che questa sequenza sia già
implementata. Serve ancora una prova d'integrazione dell'interleaving.

## A-06 — P2 — Le prove di rilascio incomplete non vengono riparate dal retry

### Evidenza e riproduzione

[save_evidence()](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_release_cycle.py:434)
crea prima la directory e poi scrive separatamente payload e firma.
Il [chiamante](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_release_cycle.py:501)
salta tutto il salvataggio se la directory esiste, senza confrontare i contenuti
con la distribuzione appena costruita.

Sonda del coordinatore, soltanto su dati temporanei:

1. errore simulato nella sincronizzazione dopo la prima scrittura;
2. directory presente con il solo `distribution.json`;
3. stessa condizione di retry del chiamante: salvataggio saltato;
4. lettura di `distribution.sig`: `FileNotFoundError`.

Non è stato eseguito l'intero ciclo root né simulata una reale perdita di
alimentazione. Il nome usa solo 16 cifre del digest, ma una collisione non è
stata riprodotta e non serve a dimostrare il difetto principale.

### Impatto e intervento raccomandato

Una scrittura interrotta può lasciare un retry bloccato sulla stessa directory.
La successiva lettura o autenticazione rifiuta l'evidenza incompleta:
**non è dimostrata l'accettazione di materiale privo di firma valida**.

Pubblicare la coppia completa con una procedura atomica o recuperabile,
sincronizzare anche le directory e verificare gli oggetti già presenti rispetto
ai byte e all'identità attesi. L'intero digest evita un'abbreviazione
superflua, ma non sostituisce il controllo dei contenuti.

## A-07 — P3 — Il test Telegram impone un divieto più ampio del requisito

Il [test aggiunto](/opt/metnos/.claude/worktrees/rm0008-reboot/tests/portable/test_executor_birth_service_catalog.py:1019)
vieta qualsiasi presenza di `RestrictNamespaces`, per ogni servizio e valore.
La [correzione produttiva](/opt/metnos/.claude/worktrees/rm0008-reboot/runtime/executor_birth_service_catalog.py:619)
rimuove invece la direttiva soltanto da Telegram, che esegue davvero la catena
degli executor.

Il catalogo contiene anche servizi amministrativi e di controllo con compiti
diversi, come [traduttore e verificatori](/opt/metnos/.claude/worktrees/rm0008-reboot/runtime/executor_birth_service_catalog.py:548).
Il test impedisce future configurazioni compatibili senza valutarne il bisogno.
Non dimostra una riduzione attuale delle protezioni di altri servizi.

Verificare i servizi interessati e le restrizioni incompatibili con la loro
sandbox, conservando la prova sul frammento Telegram. Una capacità tipata nel
catalogo è una possibile soluzione, **non un nuovo schema obbligatorio**
giustificato da questo solo difetto.

## A-08 — Osservazione contrattuale; rilievo P2 ritirato

Il [restart](/opt/metnos/.claude/worktrees/rm0008-reboot/runtime/stack_reconcile.py:904)
verifica gli executor sotto blocco del catalogo, poi lo libera prima
dell'avvio, conservando il blocco di riconciliazione. È necessario permettere
ai servizi di leggere il catalogo.

La risposta `signed` registra
[digest verificati prima del riavvio](/opt/metnos/.claude/worktrees/rm0008-reboot/runtime/stack_reconcile.py:581);
il controllo di disponibilità confronta
[i nomi correnti](/opt/metnos/.claude/worktrees/rm0008-reboot/runtime/stack_reconcile.py:708).
La verifica preventiva e la disponibilità successiva non attestano, da sole,
che il servizio utilizzi gli stessi digest. Inoltre il
[fornitore del catalogo HTTP](/opt/metnos/.claude/worktrees/rm0008-reboot/runtime/metnos_http_server.py:91)
carica al primo accesso, non necessariamente durante l'avvio.

Non è stata individuata una promessa contrattuale di corrispondenza esatta,
né riprodotto un guasto o un'esecuzione non autenticata dovuti a questa
distinzione. Il nome `sign_first` non basta a dedurre tale promessa:
[il metodo lo descrive come ammissione](/opt/metnos/.claude/worktrees/rm0008-reboot/runtime/stack_reconcile.py:498).

Chiarire nella documentazione cosa attesti la risposta. Se si richiederà una
corrispondenza con una generazione precisa, introdurre identità osservabili e
prove dedicate. Fino ad allora resta un'osservazione, non un bug dimostrato
né un requisito bloccante.

## A-09 — P3 — Due test dipendono dal certificato di proprietà della macchina

La prima review ha ottenuto **481 test passati e 2 falliti** nella selezione
mirata. I due sono:

- [test_transition_materialization_has_no_ownership_authority](/opt/metnos/.claude/worktrees/rm0008-reboot/tests/runtime/contracts/test_contract_store.py:4159);
- [test_transition_owner_binding_rejects_a_linked_authoring_inode](/opt/metnos/.claude/worktrees/rm0008-reboot/tests/runtime/contracts/test_contract_store.py:4185).

La diagnosi iniziale attribuita alla required-head produttiva era inesatta.
La [fixture runtime](/opt/metnos/.claude/worktrees/rm0008-reboot/tests/runtime/conftest.py:81)
isola già `DEFAULT_OWNERSHIP_CHAIN_ROOT_V1`. Restano non isolati i percorsi
del certificato iniziale di proprietà e della sua firma, controllati in
[contract_store](/opt/metnos/.claude/worktrees/rm0008-reboot/runtime/contract_store.py:3446).

Controprova in quattro casi: i due test originali passano con radici temporanee
vuote; aggiungendo soltanto un falso `ownership-cutover-v1.json` alla radice
temporanea, entrambi incontrano anticipatamente
`authoring_seed_transition_closed`. La presenza del file è sufficiente:
la funzione controlla l'esistenza, non ne autentica il contenuto in quel punto.

Isolare anche la radice del certificato nelle fixture pertinenti. Non è un
difetto attribuito al nuovo blocco lettori/scrittori. Le 91 failure runtime
citate dalla consegna **non sono state riesaminate singolarmente**: non si
estende loro questa diagnosi e non si raccomanda di convertirle genericamente
in test saltati. Il requisito di produzione che chiude la transizione dopo il
trasferimento della proprietà va conservato.

## A-10 — P3 — La consegna riporta riferimenti sorgente obsoleti

La [consegna](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/design/handover_rm0008_release3_9_9_2026.md:692)
chiama correnti i prefissi privato `0a27b038…` e pubblico `9180f62d…`.
Il [worktree esaminato](/opt/metnos/.claude/worktrees/rm0008-reboot/scripts/publish-public.sh:38)
dichiara invece:

- privato: `sha256:0fe09130ec866e53083b4453251e913122ac3272853e4182b8ccd555983edbaf`,
  755 sorgenti; **ricalcolato e verificato anche nella seconda review**;
- pubblico configurato: `sha256:e57452380b8d620079bc9baeabb0a4c43a16c1110582dd3f16a5648d4d47517c`,
  743 sorgenti; **non è stata ricostruita una nuova esportazione pubblica**.

Aggiornare la consegna distinguendo candidato ed effettiva release installata.
Il disallineamento è documentale: non è stato dimostrato che faccia accettare
una distribuzione errata. La consegna non è stata modificata, perché l'incarico
di consolidamento riguarda soltanto questa review.

## Prove, provenienza e limiti

### Prima review: risultati storici conservati

| Prova | Esito e limite |
|---|---|
| Controllo delle differenze testuali | PASS |
| Controllo privato sorgenti, 755 file | PASS; rieseguito anche nel consolidamento |
| Controllo dei confini birth-closed | PASS; non rieseguito nel secondo giro |
| Selezione coordinator/journal/catalog/store/reconcile | 481 PASS, 2 FAIL in 53,53 s; causa corretta in A-09 |
| Tre test stretti di riconciliazione | 3 PASS; non tre prove indipendenti del blocco tra processi |
| Journal con request/build discordanti | Archiviato e successore preparato; rafforzato dalle nuove sonde A-02 |
| Scrittore sotto sei lettori, timeout 300 ms | Timeout; sostituito come prova principale da A-04 a 5 s |

La selezione da 483 test comprendeva questi file, non l'intera suite:

- [journal lifecycle](/opt/metnos/.claude/worktrees/rm0008-reboot/tests/portable/test_birth_authority_journal_lifecycle_v2.py);
- [ownership coordinator](/opt/metnos/.claude/worktrees/rm0008-reboot/tests/portable/test_executor_birth_ownership_coordinator_v2.py);
- [transition predecessor](/opt/metnos/.claude/worktrees/rm0008-reboot/tests/portable/test_executor_birth_transition_predecessor.py);
- [service catalog](/opt/metnos/.claude/worktrees/rm0008-reboot/tests/portable/test_executor_birth_service_catalog.py);
- [contract store](/opt/metnos/.claude/worktrees/rm0008-reboot/tests/runtime/contracts/test_contract_store.py);
- [stack reconcile](/opt/metnos/.claude/worktrees/rm0008-reboot/tests/runtime/infra/test_stack_reconcile.py).

### Seconda review: nuove verifiche

| Area | Risultato osservato | Cosa non prova |
|---|---|---|
| A-01 | Import-sentinella eseguito prima della guardia | Nessuna escalation reale, UID soltanto simulato |
| A-02 | Controllo coerente + 5 mutazioni archiviate + header malformato rifiutato | Non è una catena live firmata completa |
| A-03 | Header vuoto archiviato; richiesta nota conservata | Nessun superamento delle successive verifiche |
| A-04 | Scrittore scaduto a 5,001 s; poi ammesso senza lettori | Non misura l'incidenza sul carico reale |
| A-05 | Risposta N con selezione simulata N+1 | Non è un attraversamento concorrente reale con systemd |
| A-06 | Coppia incompleta non riscritta dal retry | Non è un test di spegnimento improvviso dell'intero ciclo |
| A-09 | 4 controfattuali sugli originali: passano a radici vuote, rifiuto con certificato | Non diagnostica tutte le failure della suite |
| A-10 | Controllo privato corrente superato | Non verifica una nuova esportazione pubblica |

Le sonde di ciclo di vita hanno dato **8 PASS in 12,91 s**; quelle di
concorrenza/test **5 PASS in 5,63 s**. Qui PASS significa che l'asserzione
della sonda è soddisfatta, anche quando riproduce un difetto: **non significa
che il codice sia stato corretto**. A-01/A-03 e A-06 sono sonde aggiuntive,
non comprese nei 13 casi pytest.

Gli script diagnostici sono conservati soltanto in directory temporanee,
non aggiunti al codice di prodotto e non garantiti come archivio durevole:

- [sonde di fiducia e orfani](/tmp/metnos-review-trust-probes.py);
- [sonde del ciclo di vita](/tmp/rm0008-lifecycle-review-uuB97S/test_review_lifecycle.py);
- [sonde di concorrenza e isolamento](/tmp/metnos-review-concurrency-Fagh7l/test_review_probe.py).

Per A-06 è stata usata una sonda in memoria: chiamata reale a
`save_evidence` su directory temporanea, `os.fsync` sostituito da un errore
alla prima chiamata, poi ripetizione della condizione di esistenza del
chiamante. Metodo ed esito sono descritti nella sezione del rilievo.

## Priorità e condizioni prima dell'uso

1. **Prima di automatizzare il rilascio:** rendere esplicita e verificabile la
   provenienza dei byte privilegiati (A-01) e serializzare fino al risultato
   osservato l'avanzamento di release (A-05).
2. **Per un recupero affidabile:** correggere i confronti tra prove e la
   classificazione dei journal (A-02, A-03); rendere riprendibile il salvataggio
   delle evidenze (A-06).
3. **Per aggiornare sotto carico:** definire e verificare il limite d'attesa
   degli scrittori fra processi (A-04).
4. **Per rendere attendibili test e consegna:** restringere il test namespace,
   isolare i percorsi mancanti e aggiornare i riferimenti (A-07, A-09, A-10).
   A-08 resta un chiarimento, non una correzione obbligata.

Prima di un eventuale prossimo passaggio manuale vanno accertati materiali
fidati e stabili, assenza di operazioni amministrative concorrenti, identità
dei residui eventualmente presenti e completezza delle evidenze riutilizzate.
In presenza di journal ambigui, fermare il recupero automatico invece di
dedurne che siano superati.

Restano da eseguire, fuori dal perimetro di questa revisione documentale, le
prove integrate del ciclo e della ripresa, la verifica dei servizi e i turni
reali sui canali interessati dopo il rilascio. Non è stata concessa né
esercitata alcuna autorizzazione a modificare la produzione.

---

# Controdeduzioni dell'autore (10 settembre 2026)

Scritte dopo aver letto la revisione 2 per intero. Ordine dei rilievi
invariato. Ogni voce dichiara **accolto**, **accolto e corretto** o
**contestato**, con la misura che la sostiene.

**Avvertenza sui riferimenti di riga.** La review ha esaminato anche i file non
committati. Dopo la review sono state fatte le correzioni descritte qui sotto
(A-03, A-06, A-09, A-10) più il rilascio anticipato del catalogo nella
riparazione di una singola dipendenza: **i numeri di riga citati nella review
si sono spostati**. Il codice citato è comunque identificabile per nome.

**Stato misurato al momento di scrivere** (letto dalla catena, non dedotto):
tre transazioni registrate, tre rivendicazioni, **zero pendenti** — la
sequenza 1, la 2 abbandonata e la 3 in esercizio; suite portable
2509 verdi / 6 rosse, cioè la linea di base (5 chiedono `sudo` senza password,
1 è il sigillo di `conftest.py` volutamente rosso); gruppo contratti
**431 verdi, 0 rosse**.

## A-01 — accolto, non corretto in questo giro

Il rilievo è giusto e non lo contesto: fra il censimento e l'import il
materiale può cambiare, e l'handoff sta in una directory condivisa.

Una precisazione che rafforza il rilievo invece di attenuarlo: lo strumento
**possiede già** il predicato giusto — `open_parent` rifiuta qualunque genitore
con scrittura di gruppo o altri, o con lo sticky bit — e non lo applica
all'albero in scena. Applicato com'è, `/tmp` (0o1777) verrebbe rifiutato: il
predicato dice che quella collocazione è sbagliata, non che serva un
meccanismo nuovo.

La correzione è quindi nota e non è un ritocco: `apply` deve **copiare
l'albero in scena in una directory di proprietà della radice**, rimisurarlo lì
e importare da quella copia; `prepare` deve consegnare l'handoff nello stesso
regime. Non la faccio adesso, e dico perché: cambia il percorso che gira con i
privilegi, il giorno stesso di un passaggio in esercizio, senza prova generale
del ciclo (§6-quater punto 4 della consegna). La review stessa conclude che
con un solo amministratore e materiali fidati il rilievo non vieta il
prossimo rilascio manuale.

**L'assunzione resta quindi dichiarata**: chi prepara, lo strumento e i
materiali importati sono fidati; il rilascio automatico non può ereditarla.
È il primo lavoro della lista, prima di qualunque automazione.

## A-02 — accolto, non corretto in questo giro

I cinque campi dell'header non vengono confrontati con il record del
predecessore. Vero, ed è codice di prodotto
(`_archive_abandoned_authority_journal_v2`), non dello strumento.

Aggiungo un punto a favore della prescrizione della review: le quattro cautele
che elenca (il `previous_set_id` dell'header non è il `previous_set` della
preparazione N+1; l'inventario sorgente si lega alla distribuzione del
predecessore; un abbandono lecito **resta** `HEAD_REQUIRED`; checkpoint
valutati rispetto alla fase raggiunta) sono esattamente il modo in cui questa
correzione può essere sbagliata. Il difetto che il 9/9 ha bloccato la catena
era proprio una condizione del percorso completato copiata su quello
abbandonato. Va scritto con la prova negativa per ciascuno dei cinque campi,
sulla falsariga della sonda della review.

## A-03 — accolto e corretto

Corretto in `retire_orphan_journals`, in tre punti:

- l'header si legge con **`decode_transaction_header_v2`**, il decoder
  canonico: un header malformato è un rifiuto, non un orfano;
- si ritira **soltanto** il journal del tentativo che questa esecuzione ha
  appena ritirato, confrontato sul `request_id` esatto — per questo
  `withdraw_superseded_claim` restituisce ora la richiesta e non la sorgente;
- qualunque altro journal senza transazione **ferma l'esecuzione** con il
  proprio nome nel messaggio, invece di essere dedotto superato.

Un journal che il coordinatore conosce, abbandonati compresi, resta intoccato
come prima. **Zero journal resta un esito normale senza operazioni**: la
prescrizione corretta della review è stata recepita.

## A-04 — accolto in parte, contestato in parte

**Contestato**: il rilievo presenta la fame dello scrittore come effetto del
nuovo blocco lettori/scrittori. Non lo è. `flock` non ha coda: anche **prima**,
con lettori esclusivi, sei prese continue e sovrapposte esaurivano allo stesso
modo i 5 s di uno scrittore, perché a ogni rilascio i pretendenti gareggiano
da capo. Il cambiamento sposta **chi può sovrapporsi**, non introduce la
classe di guasto. Chiedo che questo sia messo a verbale, perché altrimenti la
correzione appare un peggioramento mentre è, sulla disponibilità, un
miglioramento misurato: il caso reale che l'ha motivata era un lettore che
scadeva contro un altro lettore.

**Contestato, secondo punto**: l'esito è un rifiuto esplicito e limitato nel
tempo (`catalog_lock_timeout`), mai una corruzione né un successo dichiarato a
vuoto. Nessuna delle proprietà di integrità dipende da chi vince la gara.

**Accolto**: la garanzia di attesa limitata fra processi non c'è, la priorità
agli scrittori vale solo dentro un processo, e la sonda a 5 s lo dimostra.
La review ha ragione anche nel dire che il carico è artificiale: la
popolazione reale è un guardiano ogni 2 minuti più letture del turno servite
dalla cache in microsecondi. Se la garanzia verrà richiesta, il disegno è
quello indicato — un'ammissione degli scrittori condivisa fra processi — e va
**misurata**, non assunta.

## A-05 — accolto, non corretto in questo giro

Fra il completamento e l'avvio non c'è un blocco che tenga insieme l'identità
fino al risultato osservato. Accolto senza riserve.

Una sola osservazione sulla gravità: il rischio nasce dalla **concorrenza
amministrativa**, che oggi non esiste (un solo amministratore, un comando alla
volta) e che diventa la norma esattamente quando l'aggiornamento diventa una
capacità del prodotto (§9.4 della consegna). Va quindi risolto **insieme** a
quella capacità, non dopo: è un requisito d'ingresso, non un debito.

## A-06 — accolto e corretto

`save_evidence` è diventata `publish_evidence`: se la directory esiste, i due
file vengono **riletti e confrontati byte per byte** con la distribuzione
appena costruita, e una coppia incompleta o diversa ferma l'esecuzione; se non
esiste, i due file vengono scritti e **anche la directory viene
sincronizzata**, cosa che prima non avveniva. La presenza non è più una prova.

Il digest abbreviato nel nome resta: la review stessa nota che la collisione
non è stata riprodotta e non serve a dimostrare il difetto. Ora il contenuto
viene comunque verificato, quindi il nome non è più l'unica identità.

## A-07 — contestato, con una concessione

**Contestato.** Il divieto generale non è una deduzione sbagliata sui singoli
servizi: è una **politica su un catalogo chiuso**. Le alternative sono due, e
sono entrambe peggiori oggi:

1. un elenco dei servizi «che eseguono turni» dentro la prova — è
   esattamente l'hardcoding che §7.3 vieta, e sarebbe la stessa copia che ha
   generato il difetto;
2. una capacità tipata nel catalogo firmato — che la review stessa qualifica
   come opzione, non obbligo, e che cambia lo schema del catalogo: un
   intervento più grande del difetto che lo motiva.

Il costo di tenere il divieto è di una riga: il giorno in cui un servizio avrà
bisogno di restringere gli spazi dei nomi, si modifica la prova **insieme** al
servizio, scrivendo il motivo. È lo stesso regime dei riferimenti rivisti già
in uso in questo sottosistema: si muove il sigillo e si dichiara perché.
Il costo di restringerlo ora è ricreare la condizione in cui una direttiva
viene copiata senza che nessuno debba giustificarla.

**Concessione.** La review ha ragione su un punto di forma: la prova non
dichiarava di essere una politica. Il testo della prova è stato riscritto per
dirlo, e per nominare la via d'uscita.

## A-08 — accolto come chiarimento

Nessuna obiezione. Il campo `signed` della risposta di `restart` registra i
digest **ammessi prima** del riavvio; non attesta quali generazioni il
processo abbia poi caricato, tanto più che il catalogo HTTP si carica al primo
accesso. Va scritto nella documentazione. Non è stata trovata una promessa
contrattuale contraria, e non ne introduco una.

## A-09 — accolto e corretto

Diagnosi della review corretta e più precisa della mia: non la required-head,
ma il **certificato di proprietà** e la sua firma, che la fixture runtime non
isolava. Isolata anche quella radice
(`DEFAULT_OWNERSHIP_ROOT_V1`, accanto a `DEFAULT_OWNERSHIP_CHAIN_ROOT_V1`,
in `tests/runtime/conftest.py`).

Misura: gruppo contratti **431 verdi, 0 rosse** (erano 2 rosse). La regola di
produzione che chiude l'authoring dopo il trasferimento della proprietà **non
è stata toccata**: era giusta, e le prove leggevano la risposta della macchina
invece della propria.

Sulle 91 rosse del runtime citate dalla consegna: d'accordo, non si estende
loro questa diagnosi e non vanno convertite in test saltati.

## A-10 — accolto e corretto

La consegna è stata aggiornata: i riferimenti `0fe09130…` / `e5745238…` sono
dichiarati **del candidato**, e quelli della release in esercizio
(`0a27b038…` / `9180f62d…`) restano indicati come tali. Il punto della review
— distinguere candidato e release installata — è stato scritto come regola,
non solo come correzione dei due valori.

## Sul verdetto operativo

Questa review non rilascia un GO e non l'ho trattata come tale. Le condizioni
che pone per un passaggio manuale sono state **verificate una per una** prima
di proporre l'esercizio: materiali preparati e rimisurati (`prepare`
idempotente, censimento riprodotto), nessuna operazione amministrativa
concorrente, **zero rivendicazioni pendenti** e nessun residuo ambiguo
misurato sulla catena, evidenze ora verificate nel contenuto.

Resta vero, e lo scrivo qui perché non vada perso: **le prove integrate del
ciclo mancano ancora**. La prova generale dell'attraversamento su copia della
catena (§6-quater punto 4) è il lavoro che avrebbe reso questa review meno
necessaria, ed è il prossimo che va fatto.
