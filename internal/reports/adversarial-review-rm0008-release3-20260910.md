# Review avversariale RM-0008 / Release 3

> Revisione 4 — stato verificato il 10 settembre 2026, sera: sistema operativo,
> RM-0008 non chiuso. Il riferimento aggiornato è la sezione finale
> «Stato attuale RM-0008 — revisione 4». Le review e le controdeduzioni
> precedenti sono conservate come storico, anche quando compaiono fuori ordine
> cronologico: i loro conteggi, riferimenti e verdetti non sono lo stato attuale.

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

---

# Seconda tornata di controdeduzioni (10 settembre 2026, dopo il commit 71d173c1)

Le tre precisazioni sono **tutte e tre corrette**. Nessuna contestata.

## A-06 — accolto: rilevare non è riprendere

Aveva ragione: rifiutare un'evidenza incompleta trasformava una scrittura
interrotta in un **tentativo bloccato per sempre**. Avevo corretto il difetto
opposto e creato il suo gemello.

La regola giusta distingue tre casi, e ora è quella implementata in
`publish_evidence`:

- **coppia completa e identica** → si rilegge, si confronta, si prosegue;
- **coppia completa e diversa** → si rifiuta, e resta un rifiuto: la stessa
  build non può avere due contenuti;
- **coppia lacera** → si **sposta di lato** sotto `…​.torn-NN`, mai si
  cancella, e si riscrive. L'evidenza è derivata dalla build appena fatta:
  non c'è niente da perdere e nessuna ragione per fermarsi.

Provato nella prova generale, scenario 5: una coppia identica viene verificata
e non riscritta; una coppia lacera viene messa da parte (`SET_ASIDE_TORN_EVIDENCE`)
e riscritta, con il pezzo superstite conservato per intero; una coppia completa
ma di un'altra build viene rifiutata. Due tentativi consecutivi ora passano.

## A-03 — accolto: la prova era rimasta indietro, e per due motivi

Il primo lo aveva visto lei: la prova confrontava ancora `source_id` e chiamava
`retire_orphan_journals()` senza il nuovo argomento. Difetto mio, e grave nel
modo peggiore — **una correzione consegnata con la sua prova rotta**.

Il secondo è emerso correggendo il primo, e vale più del primo: la prova
generale **dipendeva dal residuo del guasto reale sulla catena viva**. Chiuso
l'attraversamento, di rivendicazioni pendenti non ce n'erano più e la prova non
partiva affatto. *Una prova che funziona solo finché il difetto è presente non
prova nulla.* Ora il tentativo superato viene **costruito sulla copia**
(`seed_pending_attempt`), quindi la prova gira su una catena sana.

Aggiunte anche le due prove negative che mancavano alla regola nuova: un
journal aperto che questa esecuzione **non** ha ritirato ferma il ciclo col
proprio nome nel messaggio, e un header che il decoder canonico rifiuta ferma
il ciclo invece di passare per orfano. Le fixture usano ora `TransactionHeaderV2`,
cioè l'encoder del prodotto: un documento fatto a mano verrebbe scartato per la
forma e non arriverebbe mai alla regola sotto prova.

Esito: `REHEARSAL_OK`, sei scenari, cinque rifiuti attesi su cinque.

## A-10 — accolto: il valore che avevo scritto non è quello installato

Verificato sulla macchina. Nella copia installata della Release 3,
`runtime/contract_boundary_guard.py` dichiara
`BIRTH_CLOSED_SOURCE_REVIEW_SHA256 = "sha256:b430908b…"`, non `9180f62d…`.
Nella Release 4, ora in esercizio, dichiara `sha256:e5745238…`.

L'errore aveva anche una causa che vale la pena scrivere: `0a27b038…` è un
riferimento **privato**, e la copia installata non lo porta affatto — l'export
riscrive nel candidato il solo riferimento pubblico. Confrontare un valore
privato con una release installata è una categoria sbagliata, non un numero
sbagliato. La consegna è stata corretta di conseguenza.

## Una nota sul metodo

Tre rilievi su tre erano difetti reali, e due erano **miei difetti introdotti
correggendo i suoi rilievi precedenti**. Lo registro perché è il dato più utile
di questa tornata: in questo sottosistema una correzione senza la sua prova
rieseguita ha una probabilità alta di essere sbagliata, e la prova va
rieseguita *sulla catena com'è oggi*, non su quella che aveva il guasto.

---

# Terza tornata: O-01, O-02, O-03 chiusi (10 settembre 2026, sera)

Tutti e tre accolti, tutti e tre corretti, ciascuno con la propria prova nella
prova generale — che passa così da sei scenari a **nove**. `REHEARSAL_OK`.

## O-01 — accolto e corretto: troncato non è contraddetto

Aveva ragione due volte. La prima correzione distingueva i **nomi** mancanti,
non le scritture interrotte: con entrambi i nomi presenti e uno dei due file
troncato, il confronto byte per byte lo leggeva come contraddizione e
rifiutava per sempre.

La regola ora distingue tre casi e non due:

- coppia identica → si riusa;
- ogni file **uguale all'atteso oppure più corto** → scrittura interrotta: si
  mette da parte e si riscrive;
- stessa lunghezza con byte diversi, oppure più lungo → **contraddizione**, e
  resta un rifiuto: una build non può avere due contenuti.

*Scenario 6 della prova generale*: firma troncata a 8 byte, due tentativi
consecutivi, entrambi riprendono. È esattamente la sequenza che lei aveva
riprodotto.

## O-02 — accolto e corretto: il legame ora è durevole

Il punto centrale era suo: *«la prova non è persa dal disco, ma il percorso di
ripresa non la usa»*. L'archivio del ritiro conserva `successor-claim.json`,
cioè l'identità esatta del tentativo. La ripresa ora lo legge.

`retire_orphan_journals` accetta un giornale la cui richiesta compare fra i
ritiri **già archiviati su disco**, oltre a quello di questa esecuzione.
`this run` non è più un'identità: lo è il ritiro, e quello sopravvive
all'interruzione.

*Scenario 7*: rivendicazione ritirata, valore in memoria buttato via, ripresa
chiamata con `None` — il giornale viene riconosciuto e ritirato.

## O-03 — accolto e corretto: la copia rifiutata non occupa più il posto

Due cambiamenti, non uno. La copia viene **misurata prima** di diventare
l'identità riutilizzabile, così una che non corrisponde non occupa mai il
percorso che bloccherebbe. E una copia già presente che non misura più uguale
viene **messa da parte** sotto `.rejected-NN` — mai cancellata, mai lasciata in
mezzo.

*Scenario 8*: misura → cambio transitorio → rifiuto → ripristino → due
tentativi, entrambi adottano; e la copia rifiutata è conservata una volta sola.

## Nota su una sua osservazione che ho verificato

Il ramo di rimozione neutralizzato nella sua sonda e i controlli di proprietà
adattati all'utente della prova erano le stesse due deroghe che la prova
generale dichiara in testa. Le ho tenute identiche anche nello scenario nuovo,
sostituendo soltanto il cambio di proprietario privilegiato: copia, rinomina e
censimento restano quelli veri.

## Ciò che resta aperto da questa review

A-01 (provenienza dei byte privilegiati, mitigata ma non chiusa), A-02
(confronto delle cinque identità dell'header) e A-05 (serializzazione fino al
risultato osservato). Nessuno dei tre è stato toccato oggi, e nessuno dei tre
è un rischio di integrità: sono le tre condizioni da soddisfare **prima** che
l'aggiornamento diventi automatico e concorrente.

---

# Riscontro sulle note dell'agente esterno — 10 settembre 2026

## Perimetro e risultato

Riletti integralmente il documento e le due tornate di controdeduzioni,
confrontandoli con il codice attuale. Analisi iniziata a `d44453b3` e
note ricontrollate a `8c13e2060635531048207589fdbd0735802139e4`;
ultimo controllo di coerenza a `4b361ed3314408b134e4fb2823df7b9bb3f996a9`.
Durante il controllo sono arrivati altri commit e una modifica esterna
a [scripts/publish-public.sh](/opt/metnos/.claude/worktrees/rm0008-reboot/scripts/publish-public.sh);
non sono stati alterati. I file del ciclo e della prova generale sono rimasti
identici durante le prove descritte sotto.

**Le correzioni dichiarate hanno effetto, ma tre casi di ripresa restano
bloccati e sono stati riprodotti.** Sono problemi di disponibilità/ripresa
classificati P2, collegati ad A-06, A-03 e A-01: le sigle O-01/O-02/O-03
identificano queste precisazioni, non tre rilievi da sommare nuovamente ai
medesimi ID. Non è stato dimostrato un aggiramento delle firme, né viene
ripristinato il P0 o il NO-GO assoluto della prima review.

Sono stati modificati soltanto questo report e un file diagnostico temporaneo
fuori dal repository. Nessuna modifica al codice del prodotto, nessuna
installazione, nessuna invocazione privilegiata, nessun riavvio o
attraversamento sulla catena viva.

## Esito delle controdeduzioni

| ID | Esito aggiornato | Precisazione |
|---|---|---|
| A-01 | Assunzione di fiducia ancora necessaria; nuova osservazione di ripresa O-03 | La copia amministrativa ricensita è un miglioramento reale, non un'autenticazione di un preparatore meno fidato. Una copia rifiutata può però occupare stabilmente il percorso riutilizzato. |
| A-02 | Aperto, già accolto | Restano da confrontare le identità mancanti del predecessore; non è stata rieseguita in questo giro la suite completa del ciclo di vita. Non introdurre come rimedio requisiti di completamento incompatibili con un tentativo abbandonato. |
| A-03 | Corretto il ritiro indiscriminato; ripresa ancora parziale, O-02 | Il decoder canonico e il confronto esatto con `request_id` correggono il caso originario. Il collegamento al ritiro si perde però fra due esecuzioni. La prova generale aggiornata ora termina correttamente. |
| A-04 | Controdeduzione accolta sull'assenza di regressione dimostrata | Anche il precedente percorso interprocesso usava acquisizioni non bloccanti ripetute. Rimane una condizione P2 di progresso non garantito sotto contesa, non una nuova regressione provata né una corruzione. Nessuna nuova misura di frequenza sul carico reale. |
| A-05 | Aperto, già accolto | La separazione fra completamento, attivazione e risultato resta rilevante con più amministratori/aggiornamenti concorrenti; non è stata rieseguita qui una prova concorrente integrata. |
| A-06 | Riparato il file mancante; ripresa ancora parziale, O-01 | La presenza di entrambi i nomi non distingue una firma completa da una creata ma ancora vuota o parziale. |
| A-07 | Controdeduzione accolta; ritirato come bug | Il divieto sull'intero catalogo è ora una politica intenzionale esplicita. Non si richiede una nuova capacità nello schema soltanto per soddisfare questa review. |
| A-08 | Resta osservazione contrattuale | Nessuna promessa aggiuntiva di snapshot è stata dimostrata; nessun nuovo difetto aperto su questa base. |
| A-09 | Corretto nel perimetro dei due test originari | La fixture isola anche la radice del certificato di proprietà. Entrambi i test prima falliti passano; non viene qui riconfermato il totale storico di 431 test. |
| A-10 | Corretti i riferimenti R3/R4; resta un disallineamento documentale P3 | La consegna contiene ancora una tabella e una decisione operativa anteriori al suo stesso annuncio della Release 5. Valori riscontrati sotto. |

Per A-07, la motivazione è ora esplicita nel
[test del catalogo](/opt/metnos/.claude/worktrees/rm0008-reboot/tests/portable/test_executor_birth_service_catalog.py:1030).
Per A-09, la
[fixture corretta](/opt/metnos/.claude/worktrees/rm0008-reboot/tests/runtime/conftest.py:91)
isola entrambe le radici, senza indebolire la regola del prodotto.

## O-01 / A-06 — P2 — Firma presente ma incompleta: il retry resta bloccato

**Punto:** [publish_evidence](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_release_cycle.py:486)
considera completa la coppia quando l'elenco dei nomi coincide con quello
atteso. La [scrittura](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_release_cycle.py:503)
crea invece ciascun file prima di scriverne i byte.

**Sequenza riprodotta:**

1. Scrittura completa di `distribution.json`.
2. Creazione di `distribution.sig`, poi interruzione prima della scrittura
   oppure dopo i primi 8 byte.
3. Due nuovi tentativi con la stessa coppia attesa: entrambi falliscono con
   `release evidence is not this build: distribution.sig`.
4. Nessuno spostamento in `.torn-NN`: i due nomi sono presenti, quindi il ramo
   di riparazione non viene raggiunto. La firma resta rispettivamente di 0 o
   8 byte.

Il caso con firma **non ancora creata** si ripara invece correttamente:
il JSON superstite è conservato in `.torn-01`, la coppia viene ricostruita
e una seconda chiamata identica passa. Confermati anche il riuso senza
riscrittura della coppia completa identica e il rifiuto di quella diversa.

**Conseguenza:** la correzione distingue i nomi mancanti, non tutte le scritture
interrotte. Un errore o un arresto nel secondo file richiede ancora intervento
sui residui per riprendere la stessa build. È un rifiuto esplicito, non
l'accettazione di una firma falsa.

**Indicazione:** distinguere materiale in corso di scrittura da una coppia
effettivamente pubblicata, mediante preparazione privata e pubblicazione
atomica o un protocollo durevole di ripresa. Non risolvere ammettendo
indiscriminatamente coppie complete diverse. La prova di accettazione deve
interrompere il percorso prima/dopo apertura, scrittura e sincronizzazione
di ciascun file, poi ritentare due volte conservando i residui.

La [prova generale attuale](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_rehearse_withdrawal.py:256)
rimuove la firma: copre il nome mancante, non il nome presente con contenuto
troncato. La sonda di questa review inietta errori nelle vere operazioni di
apertura/scrittura su file temporanei; non è una prova di spegnimento fisico.

## O-02 / A-03 — P2 — Il legame con il tentativo ritirato non sopravvive alla ripresa

**Punto:** [withdraw_superseded_claim](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_release_cycle.py:416)
archivia release e rivendicazione, poi restituisce `request_id`.
[apply_cycle](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_release_cycle.py:596)
passa quel valore alla funzione di ritiro dei journal soltanto in memoria.
Alla successiva esecuzione, se non ci sono rivendicazioni pendenti,
[il valore restituito è None](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_release_cycle.py:393).

**Sequenza riprodotta:**

1. Una rivendicazione superata e la relativa release vengono archiviate
   correttamente; resta aperto il journal della medesima `request_id`.
2. Si simula l'interruzione fra il ritiro della rivendicazione e quello del
   journal, richiamando il percorso di ripresa senza conservare il risultato
   della prima chiamata.
3. Il secondo ritiro della rivendicazione restituisce `None`.
4. [retire_orphan_journals](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_release_cycle.py:460)
   rifiuta il journal per due tentativi consecutivi:
   `open journal of an attempt this run did not withdraw`.

Il file della rivendicazione archiviata conserva ancora l'identità esatta del
tentativo: la prova non è persa dal disco, ma il percorso di ripresa non la
usa. Il controllo senza interruzione, con le due chiamate consecutive e il
valore restituito correttamente passato, riesce.

**Conseguenza:** la restrizione al solo tentativo ritirato è corretta, ma
`this run` non è un'identità durevole. L'interruzione lascia un journal
legittimamente riconducibile al ritiro che la nuova esecuzione tratta come
estraneo. La disponibilità si ferma; non viene dimostrata un'archiviazione
indebita.

**Indicazione:** rendere durevole il collegamento al ritiro e verificarlo in
ripresa, per esempio mediante un record di operazione o la rivendicazione
archiviata verificata e legata esattamente al journal. Non tornare alla sola
euristica «assente dal coordinatore». Provare separatamente gli arresti dopo
ogni spostamento di release, rivendicazione e journal, inclusi due retry e il
rifiuto di residui appartenenti a un altro tentativo.

La sonda usa le funzioni reali, header prodotto dal decoder/encoder canonico
e spostamenti reali su fixture temporanee. Simula il confine fra chiamate:
non termina un processo amministrativo reale e non verifica una catena
crittografica completa.

## O-03 / A-01 — P2 — Una copia candidata rifiutata rimane nella posizione riutilizzabile

**Punto:** [adopt_candidate](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_release_cycle.py:535)
copia soltanto quando il percorso candidato non esiste.
La [rinomina nella posizione definitiva](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_release_cycle.py:548)
precede il censimento della copia e il confronto con il digest atteso.

**Sequenza riprodotta:**

1. Il sorgente viene misurato, poi cambia prima della copia.
2. La copia finisce nel percorso definitivo associato al digest atteso.
3. Il ricensimento rileva correttamente la differenza e rifiuta il candidato.
4. Il sorgente viene ripristinato ai byte originali e torna a misurare
   esattamente il digest atteso.
5. Due retry falliscono ugualmente: trovano il percorso già esistente e
   ricensiscono sempre la copia rifiutata. La copia è stata eseguita una sola
   volta e contiene ancora i byte non conformi.

**Conseguenza:** un cambiamento transitorio durante la preparazione lascia una
copia che impedisce stabilmente il riuso della stessa identità candidata.
La verifica evita correttamente di eseguire quei byte; manca la gestione del
residuo rifiutato.

**Indicazione:** verificare la copia privata prima di renderla una candidata
riutilizzabile; definire anche la ripresa di copie incomplete o rifiutate,
con conservazione verificabile e senza cancellazione indiscriminata di
percorsi in conflitto. Continuare comunque a verificare le copie riutilizzate.
La prova di accettazione deve ripetere misura → modifica → rifiuto →
ripristino → due retry, più un arresto durante la copia.

La sonda ha eseguito copia, rinomina e censimento reali in una directory
temporanea. Ha sostituito soltanto il cambio di proprietario privilegiato e
adattato i controlli di proprietà all'utente della prova; il ramo di rimozione
era neutralizzato e verificava l'assenza del percorso temporaneo. Non è quindi
una prova di isolamento root, né un nuovo argomento per il P0 già ritirato.

## A-10 — Riferimenti riletti e stato storico da non confondere

La seconda controdeduzione è corretta per R3 e R4. Questi sono i valori letti
al checkpoint, distinti per origine:

| Oggetto | Riferimento osservato | Fonte |
|---|---|---|
| Release 3 installata, pubblico | `sha256:b430908b7ffb7cdeabef0ccbae40e5e5dd05775f212f51143568a56e90ca097c` | [guard R3](/var/lib/metnos/executor-birth/releases-v1/00000000000000000003/runtime/contract_boundary_guard.py:55) |
| Release 4 installata, pubblico | `sha256:e57452380b8d620079bc9baeabb0a4c43a16c1110582dd3f16a5648d4d47517c` | [guard R4](/var/lib/metnos/executor-birth/releases-v1/00000000000000000004/runtime/contract_boundary_guard.py:55) |
| Release 5 installata, pubblico | `sha256:ab5c31a12b882a2028345045a24d968e223a479be3e7919d93638ce2b889a9d8` | [guard R5](/var/lib/metnos/executor-birth/releases-v1/00000000000000000005/runtime/contract_boundary_guard.py:55) |
| Candidato privato, 755 file | `sha256:14109404492a955b32e90553b1d614b74a8a81949928f22f90cdac4811c91b26` | [pin privato](/opt/metnos/.claude/worktrees/rm0008-reboot/scripts/publish-public.sh:44) |
| Proiezione pubblica configurata, 743 file | `sha256:ab5c31a12b882a2028345045a24d968e223a479be3e7919d93638ce2b889a9d8` | [pin pubblico](/opt/metnos/.claude/worktrees/rm0008-reboot/scripts/publish-public.sh:46) |

Il gate del sorgente privato è passato con il riferimento e il numero di file
indicati. La proiezione pubblica non è stata ricostruita in questa review:
per quella sono stati letti il pin configurato e il corrispondente file R5.

La [consegna iniziale](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/design/handover_rm0008_release3_9_9_2026.md:20)
annuncia ora R5 in esercizio, mentre la
[tabella finale](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/design/handover_rm0008_release3_9_9_2026.md:764)
dice ancora R4 «in esercizio» e candidato privato `0fe09130…`/pubblico
`e5745238…`. Anche la
[decisione aperta n. 1](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/design/handover_rm0008_release3_9_9_2026.md:776)
chiede ancora di distribuire correzioni che l'apertura dichiara distribuite.

**Indicazione:** aggiornare o etichettare come storico quel riepilogo e quella
decisione; non dedurre il valore pubblico dal privato. La consegna non viene
modificata da questa review. «R5 in esercizio» è un'affermazione della consegna:
la lettura della copia installata non equivale a una nuova verifica di
attivazione, servizi o intera catena firmata.

## Precisazione del modello di autorità amministrativa

È ora presente anche
[install_release_authority.sh](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/install_release_authority.sh:13).
Il suo commento dichiara correttamente che il launcher, pur amministrativo,
esegue un programma dal worktree scrivibile dallo sviluppatore e concede in
pratica root senza password attraverso quel codice e il candidato.

Il [launcher](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/install_release_authority.sh:73)
e la [regola prevista](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/install_release_authority.sh:82)
limitano le forme di invocazione, non l'autorità del codice modificabile che
viene eseguito. L'assunzione va quindi formulata come **fiducia amministrativa
persistente nell'account e nei suoi strumenti**, non come approvazione umana
garantita a ogni tentativo.

È una precisazione del perimetro, non un superamento dei privilegi concessi
dimostrato dalla review. Non prova ancora un aggiornamento autonomo del prodotto
capace di ricevere input meno fidati. Lo script non è stato installato né
invocato **da questa review**; non è stata verificata qui la policy sudo attiva.

## Prove eseguite e limite della conclusione

- Prova generale fornita dal progetto, su copia temporanea della catena:
  `REHEARSAL_OK`, sei gruppi di scenari. Il sesto contiene cinque rifiuti
  attesi; il quinto verifica anche il rifiuto della coppia completa diversa.
  La nuova API è utilizzabile. La preparazione sintetica prevista quando non
  esiste un tentativo pendente è stata letta nel codice, ma questa esecuzione
  non ne dimostra separatamente la copertura su una catena sana.
- Cinque test del prodotto: **5 passed**. Sono i due casi originari di A-09,
  il test della politica `RestrictNamespaces` e i due test che verificano il
  rilascio del catalogo prima di affidare riavvio/riparazione a systemd.
  Non è stata rieseguita l'intera suite.
- Sette casi diagnostici temporanei: **7 passed**. In questo contesto
  «passed» significa che le asserzioni hanno confermato sia i controlli
  positivi sia i tre difetti descritti, non che tali difetti siano corretti.
  Il file riproducibile è
  [/tmp/metnos-notes-review.arKTwa/test_counterarguments.py](/tmp/metnos-notes-review.arKTwa/test_counterarguments.py);
  è un artefatto temporaneo, non un test aggiunto al prodotto.
- Gate del sorgente privato: superato, 755 file, digest riportato sopra.
- Prove senza bytecode/cache pytest nel repository, con dati e stato
  instradati nella directory temporanea. Nessuna invocazione di
  `apply --cross` o del launcher amministrativo.

Identità dei due file principali effettivamente sottoposti alle prove:

| File | SHA-256 |
|---|---|
| [rm0008_release_cycle.py](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_release_cycle.py) | `d33565837f63f77eff7f86aaa37afe32225a4536c5ca93445e66b3a67f7fe023` |
| [rm0008_rehearse_withdrawal.py](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_rehearse_withdrawal.py) | `b326442227215b076b2140707d11ca687ca0a499468fcd2086c6e27385c841d0` |

**Conclusione:** accogliere le correzioni provate, senza equiparare la prova
generale riuscita alla riprendibilità di ogni interruzione. Prima di dichiarare
il ciclo automaticamente riprendibile, chiudere O-01/O-02/O-03 con prove dei
punti di interruzione e mantenere il rifiuto dei materiali realmente estranei.
Restano le condizioni architetturali A-02/A-04/A-05 già esplicitate e il modello
di fiducia di A-01. Nessun GO operativo viene rilasciato da questa verifica.

---

# Stato attuale RM-0008 — revisione 4

Verifica del **10 settembre 2026, sera**, conclusioni riferite al controllo
delle **22:50 CEST**. Worktree
[/opt/metnos/.claude/worktrees/rm0008-reboot](/opt/metnos/.claude/worktrees/rm0008-reboot),
HEAD `2a583cdb0af3912981d19f62a9e3670945d27334`.
Confronto con il precedente controllo a `4b361ed3`, con la terza tornata
dell'autore e con le aggiunte serali alla consegna.

## Esito sintetico

**Metnos risponde come operativo, ma RM-0008 non è chiuso.**
I tre casi esatti O-01/O-02/O-03 sono ora corretti e verificati. Restano:

- **A-11 — P1:** il percorso documentato di pubblicazione del singolo executor
  non dispone della redazione versionata necessaria nel contesto
  dell'operatore; anche predisponendola, il comando non acquisisce
  automaticamente l'edit nel worktree.
- **O-04 / A-03 — P2:** un'interruzione un passo prima di quella corretta da
  O-02 lascia ancora il ritiro bloccato.
- **A-12 — P2:** codice e digest del manifest di `login_sites` nella Release
  19 non coincidono. L'assenza di una chiamata a `verify_executor` non prova
  che il codice modificato sia quello ammesso ed eseguito in `STORE_ONLY`.
- Le condizioni precedenti A-01/A-02/A-04/A-05 e il disallineamento
  documentale A-10, con le qualificazioni riportate sotto.

Il controllo del sorgente privato è **rosso sul worktree già modificato
all'inizio della review**. Non è una regressione introdotta da questa verifica
e non è stato aggirato riallineando le impronte.

## Stato osservato: selezione, servizi e sorgenti non sono la stessa prova

| Oggetto | Riscontro di questa verifica |
|---|---|
| Selezione durevole | Il [puntatore richiesto](/var/lib/metnos/executor-birth/chain-v1/required-head-v1.bin) seleziona **Release 19**. Firma verificata con il [registro pubblico della head](/var/lib/metnos/executor-birth/authorities-v1/head-registry-v1.json), tramite i decoder del prodotto. |
| Identità selezionata | Head `sha256:d919d597b3c56ad2a67673b2548283c179d501ede54b53f1e85235ab046dce55`; build `sha256:4dc2d93890b52b32d0565a8d18b6516055a8fbe1c6590f58aa3002a0fec39f5a`. |
| Servizi | HTTP, Telegram, browser e lavoratore durevole attivi; `metnos.target` attivo; timer traduttore e sorvegliante attivi/in attesa. Osservazione systemd in sola lettura. |
| Risposta del prodotto | `/agent/health`: `operational=true`, `maintenance_only=false`; lavoratore durevole `ready`, disponibile e senza causa di errore. Nessun turno utente eseguito. |
| Sorgenti Python di R19 | Censiti **743 file**: `sha256:2f84944a91183ccb273f56d18ce1c23732cf6e667fa3b186783e3eadc49e2ea4`, coincidente con il [riferimento pubblico installato](/var/lib/metnos/executor-birth/releases-v1/00000000000000000019/runtime/contract_boundary_guard.py:55). |
| Worktree privato | Riferimento approvato `sha256:924f27ffd719ce8ac4abb424fe07b9c74b9cc9b4794aa51bbf9bae8a382fcd07`, 755 file; censimento effettivo `sha256:8ee399d346a1ae8592de28060d4e2e4d29bd292b2f546463902b3ad1a916ba56`: controllo rifiutato. |
| Lavoro locale preesistente | Cinque percorsi già modificati, fra cui codice e test di `login_sites` già nell'indice, un test eliminato e due risorse i18n modificate. Conservati byte per byte, senza modificare l'indice. |

Il controllo della firma del puntatore non è una nuova certificazione
dell'intera catena, delle protezioni del filesystem o dell'identità di ciascun
processo vivo. Non si deduce dalla sola salute HTTP che ogni executor sia
disponibile o che ogni correzione serale sia stata eseguita.

La [roadmap](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/roadmap/RM-0008-porta-unica-nascita-executor.md:3)
resta `in_progress`: F4 non chiude F5-F6, né sostituisce le cinque ammissioni
reali da almeno due produttori. Questa review non produce quelle prove.

## Riconciliazione dei rilievi precedenti

| Rilievo | Stato alla revisione 4 |
|---|---|
| O-01 / A-06 | **Chiuso il caso riprodotto:** firma appena creata vuota e firma troncata a 8 byte vengono conservate e ricostruite; due ripetizioni riescono. Restano fuori dalla prova spegnimento fisico e verifica esaustiva di tutte le scritture. |
| O-02 / A-03 | **Chiuso il caso riprodotto:** dopo entrambi gli spostamenti, il journal viene riconosciuto dal ritiro su disco senza il valore in memoria. La famiglia di ripresa A-03 resta aperta per O-04. |
| O-03 / A-01 | **Chiuso il blocco di identità:** nuova copia misurata prima della pubblicazione; vecchia copia definitiva difforme spostata di lato; due ripetizioni riescono. La conservazione di ogni copia rifiutata, però, non è quanto il codice fa: precisazione sotto. |
| A-01 | Resta il modello di fiducia amministrativa nell'account, nello strumento e nella preparazione. Non è stata dimostrata l'autenticazione di un preparatore meno fidato; nessun P0 ripristinato. |
| A-02 | Aperto: il confronto delle cinque identità manca ancora. Il codice non è cambiato dal controllo precedente. La suite ordinaria del journal passa, ma non rende superflue le controprove sui campi discordanti. |
| A-04 | Resta condizione di disponibilità sotto contesa fra processi, non regressione dimostrata. Non è corretto eliminarla dal riepilogo soltanto perché gli altri tre rilievi sono stati corretti. |
| A-05 | Aperto sotto concorrenza amministrativa: completamento, attivazione e identità restituita non sono ancora un'unica operazione protetta. Codice invariato. |
| A-07 / A-08 | Restano rispettivamente politica esplicita accolta e osservazione contrattuale; non riaperti. |
| A-09 | Correzione confermata: i due test originari passano anche in questo giro. |
| A-10 | Aperto come P3 documentale: la consegna annuncia R5 in testa, R4 nella tabella, mentre la selezione osservata è R19. |

La frase della terza controdeduzione «nessuno dei tre è un rischio di
integrità» è troppo generale: A-02 riguarda proprio la coerenza del recupero
con il predecessore. Resta vero che non sono state provate ammissioni di
autorità estranee o alterazioni della catena valida. I due giudizi non vanno
confusi.

## A-11 — P1 — Il comando documentato non chiude il percorso edit → pubblicazione

La [consegna](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/design/handover_rm0008_release3_9_9_2026.md:849)
segnala `birth_unavailable / authoring_version_invalid: missing`. Il codice
spiega il risultato:

1. [verify_named_executors](/opt/metnos/.claude/worktrees/rm0008-reboot/runtime/stack_reconcile.py:522),
   in `STORE_ONLY`, prende il riferimento dal catalogo e mette a `None` la
   directory dell'executor nel worktree.
2. [La preparazione](/opt/metnos/.claude/worktrees/rm0008-reboot/runtime/executor_birth_snapshot.py:412)
   legge l'albero di redazione versionato dietro quel riferimento.
3. [Il lettore](/opt/metnos/.claude/worktrees/rm0008-reboot/runtime/executor_birth_authoring.py:644)
   rifiuta l'assenza di `version.json` prima di acquisire il candidato.
4. [La radice delle origini del repository](/opt/metnos/.claude/worktrees/rm0008-reboot/runtime/manifest_inventory.py:258)
   è sotto lo stato dell'account, non dentro la release installata.

Nel contesto di Roberto, la lettura della directory
[/home/roberto/.local/state/metnos/contract-authoring](/home/roberto/.local/state/metnos/contract-authoring)
ha trovato soltanto il lucchetto del tentativo di `login_sites`, nessun albero
canonico e nessuna versione. La directory omologa dell'account di servizio
non è leggibile da questo contesto: **non si estende l'osservazione a tutti
gli account della macchina**.

**Due sonde isolate confermano il difetto di raccordo:**

- Senza versione, la vera funzione di verifica del comando fallisce con
  `birth_unavailable` prima di consegnare qualsiasi richiesta a Birth.
- Con una versione valida contenente byte precedenti e un edit distinto nel
  worktree, consegna i byte precedenti della redazione, non quelli modificati
  nel worktree. Sono simulate soltanto la selezione dell'inventario,
  l'ammissione finale e la lettura finale del catalogo: non si è pubblicato
  nulla in produzione.

Il [test ordinario](/opt/metnos/.claude/worktrees/rm0008-reboot/tests/runtime/infra/test_stack_reconcile.py:885)
crea esplicitamente la versione prima di chiamare il comando: dimostra che la
preparazione funziona quando il prerequisito esiste, non che il percorso
operativo lo produca.

**Impatto e chiusura richiesta:** P1 per il percorso di manutenzione prescritto,
non per la disponibilità generale del servizio. Non basta creare cartelle
vuote o ripopolare una vecchia sorgente: occorre legare esplicitamente
l'edit candidato, l'account produttore, la versione di redazione e la
generazione ammessa. Provare il comando documentato dall'edit fino a una
generazione nuova riletta, con assenza iniziale della redazione e con
redazione già esistente ma diversa dal candidato.

Esiste già una
[materializzazione prima della transizione](/opt/metnos/.claude/worktrees/rm0008-reboot/runtime/contract_store.py:3421),
ma si chiude dopo la comparsa della proprietà/head
([rifiuto previsto](/opt/metnos/.claude/worktrees/rm0008-reboot/runtime/contract_store.py:3451)).
Non è un rimedio da invocare forzatamente su R19; il recupero post-transizione
richiede un percorso autorizzato che conservi quel confine.

## O-04 / A-03 — P2 — Arresto fra i due spostamenti: ritiro ancora non riprendibile

La [sequenza di ritiro](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_release_cycle.py:416)
sposta prima la release, poi la rivendicazione. La nuova lettura dei ritiri
archiviati risolve il caso **successivo a entrambi gli spostamenti**, non
l'interruzione fra i due.

Sonda: il primo spostamento reale riesce; si inietta un errore prima del
secondo. Restano release nell'archivio, rivendicazione nella posizione
originaria e journal aperto. Due nuove chiamate falliscono entrambe alla
[verifica della release originaria](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_release_cycle.py:400)
con `the pending claim reserved no release directory`.
Il lettore dei ritiri non può ricostruire il legame: il
[file della rivendicazione archiviata](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_release_cycle.py:444)
non è ancora stato scritto/spostato.

**Impatto:** disponibilità del ciclo interrotta fino a trattamento esplicito
del residuo; nessuna cancellazione o ammissione indebita dimostrata.
O-02 resta chiuso nel suo caso esatto: non si cambia retroattivamente ciò
che quella prova dimostra.

**Chiusura richiesta:** rendere riconoscibile e riprendibile lo stato parziale
release archiviata/rivendicazione ancora pendente, verificandone identità e
destinazione senza sovrascrivere conflitti. Provare separatamente arresti dopo
ogni spostamento e relativa sincronizzazione, due ripetizioni e il rifiuto
di archivi appartenenti ad altri tentativi.

## A-12 — P2 — R19 contiene codice e manifest discordanti; la consegna non prova l'esecuzione del fix

Rilettura e calcolo con la funzione del prodotto sui file dichiarati dal
[manifest installato](/var/lib/metnos/executor-birth/releases-v1/00000000000000000019/executors/login_sites/manifest.toml:24):

| Profilo di `login_sites` | Digest dichiarato | Digest calcolato |
|---|---|---|
| R19 installata e HEAD `2a583cdb` | `sha256:373df2e979a4bf9f82cc5d9251c52cffe8df77b097b33550ba9ec23620fe2286` | `sha256:342c6fd4f4871f4c85fd79d11ffba16abe33c85e836a5f1500a6b59fe82ab543` |
| Worktree con modifica locale preesistente | `sha256:373df2e979a4bf9f82cc5d9251c52cffe8df77b097b33550ba9ec23620fe2286` | Uguale al dichiarato |

La modifica locale ha quindi una situazione diversa dal commit e dalla
release selezionata. Non prova di per sé un aggiornamento del processo vivo.

La [spiegazione della consegna](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/design/handover_rm0008_release3_9_9_2026.md:868)
salta un controllo: in `STORE_ONLY` il caricatore non usa
`sign.verify_executor`, ma passa da
[current_manifest](/opt/metnos/.claude/worktrees/rm0008-reboot/runtime/loader.py:2259)
alla [verifica della generazione](/opt/metnos/.claude/worktrees/rm0008-reboot/runtime/contract_store.py:1581).
Questa [ricalcola e confronta il digest del codice](/opt/metnos/.claude/worktrees/rm0008-reboot/runtime/contract_store.py:1261).
Non è corretto dedurre «non verifica il codice» dall'assenza della chiamata
storica, né dire per questo che la discordanza sia innocua in produzione.

**Impatto provato:** incoerenza del contratto presente nell'artefatto e
insufficienza della prova di consegna del fix. **Non provato:** quale
generazione di `login_sites` sia effettivamente ammessa dal catalogo vivo,
un'esecuzione di codice non autorizzato o un blocco effettivo dell'executor
in produzione. Se la generazione ammessa lega altre copie dei byte, occorre
mostrarlo: la firma dell'intera release non sostituisce quel legame.

**Chiusura richiesta:** riconciliare candidato, manifest, generazione
pubblicata e ricevuta di ammissione nel percorso Birth; verificare il digest
della generazione realmente caricata e una postcondizione sul comportamento
corretto. Non alterare la release immutabile o saltare il controllo del digest.

## Precisazioni sul report e sulle prove

- **A-10:** riferimenti R4/R5 e decisione di «rilasciare le due correzioni»
  sono rimasti nella consegna
  ([tabella](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/design/handover_rm0008_release3_9_9_2026.md:947),
  [decisioni](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/design/handover_rm0008_release3_9_9_2026.md:961)).
  Vanno dichiarati storici o aggiornati; lo stato attuale non si ricava
  dall'ultima sezione aggiunta né dal solo numero di release nel titolo.
- **Conservazione O-03:** una vecchia copia difforme già definitiva viene
  conservata in `.rejected-NN`. Una copia nuova rifiutata prima della
  rinomina resta invece in `.rm0008-cycle-candidate-incoming` ed è eliminata
  dalla preparazione successiva
  ([codice](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_release_cycle.py:594)).
  Riprodotti entrambi i casi: rispettivamente uno e zero archivi.
  L'[asserzione `len(rifiutate) <= 1`](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_rehearse_withdrawal.py:336)
  ammette zero e non dimostra «conservata una volta sola». Correggere la
  promessa o provare effettivamente la conservazione; questo non riapre il
  blocco di identità già risolto.
- **Limiti della prova generale:** il
  [sostituto di open_parent](/opt/metnos/.claude/worktrees/rm0008-reboot/internal/tools/rm0008_rehearse_withdrawal.py:133)
  controlla tipo, proprietario e identità, ma non ripete tutti i controlli dei
  permessi della funzione reale. Nello scenario candidato neutralizza
  `chown`, non il comando di rimozione, che opera sulla copia temporanea.
  Non chiamare quindi queste deroghe identiche alle precedenti sonde della
  review, né presentarle come una prova completa delle protezioni root.

## Prove, riproducibilità e limiti

- Prova generale del progetto: **9 scenari, REHEARSAL_OK**, su copia temporanea
  della catena; 27 oggetti preservati identici nel controllo di ritiro.
  Stavolta la catena copiata non aveva una rivendicazione pendente: è stato
  esercitato anche il ramo che la prepara sinteticamente.
- **98 test del prodotto superati** in due esecuzioni: 66 su redazione,
  riconciliazione e regressioni A-07/A-09; 32 sul ciclo di vita del journal
  e sull'autenticazione delle generazioni. Non è la suite completa e non
  riconferma i conteggi storici di migliaia di test.
- **8 sonde isolate superate**, nel senso che le asserzioni confermano
  correzioni e difetti descritti. File:
  [/tmp/metnos-rm008-status.7IiRMU/test_review_state.py](/tmp/metnos-rm008-status.7IiRMU/test_review_state.py).
  Le prove di ritiro usano header canonici, rinomine e file reali, con
  proprietà adattata all'utente e radici temporanee; non sono arresti fisici.
  Nessuna richiesta raggiunge l'ammissione produttiva nei test del comando.
- Firma del puntatore, censimento R19 e digest dei tre profili:
  [/tmp/metnos-rm008-status.7IiRMU/collect_state.py](/tmp/metnos-rm008-status.7IiRMU/collect_state.py).
  Usa soltanto registro pubblico e dati leggibili; non legge chiavi private.
- Controllo del sorgente privato: **rifiutato** per la divergenza già descritta.
  Nessuna modifica a riferimenti approvati, firme o sorgenti per farlo passare.
- Stato del servizio letto, senza riavvio, attraversamento, pubblicazione o
  turno reale. Nessuna nuova verifica Windows o di aggiornamenti concorrenti.

Le differenze locali preesistenti, incluse quelle nell'indice Git, sono
rimaste identiche durante la review. Le sole scritture di questa attività
sono questo report e gli artefatti diagnostici nella directory temporanea.

Identità dei file amministrativi provati: ciclo
`65beb1f30c9665a4047053e5df09362ba7f5e2ebff4abac9718ba8718d3ecfeb`;
prova generale
`c4df05b3b7b8e5e144a1d518db90bed00f7090bb8d989030e789c517d94afecd`.

**Ordine di chiusura consigliato:** rendere utilizzabile e verificabile la
pubblicazione puntuale (A-11/A-12), completare la ripresa del ritiro (O-04),
poi soddisfare le condizioni di fiducia/concorrenza e le prove F5-F6 prima
di dichiarare l'aggiornamento una capacità autonoma del prodotto.
Nessun GO incondizionato al prossimo rilascio viene emesso da questo report.

# Quarta tornata: risposta alla revisione 4 (11 settembre 2026, notte)

Risposta alle conclusioni del controllo delle 22:50. Rispetto a quel HEAD
(`2a583cdb`) i commit nuovi sono `bbc83784` (questo report, conservato così
com'è) e da `7fb40857` a `baeaae5b`.

## A-12 — accolto, e avevo torto

Avevo scritto che in `STORE_ONLY` il digest del codice non viene controllato e
che la discordanza era perciò innocua in produzione. **Era sbagliato**, per la
ragione indicata: `current_manifest` passa dalla verifica della generazione,
e `_verify_payloads` ricalcola il digest del codice e rifiuta con
`code_digest_mismatch`. Dall'assenza della vecchia chiamata a
`verify_executor` non si poteva dedurre niente.

Contenimento, non prova retroattiva:

- la modifica a `login_sites` è stata tolta dal repository (`7fb40857`);
- la **Release 20** l'ha tolta dall'esercizio (cutover
  `sha256:980e5998…`); sulla release installata il digest dichiarato e quello
  calcolato con `manifest_code_digest.compute_code_digest` coincidono:
  `sha256:373df2e979a4bf9f82cc5d9251c52cffe8df77b097b33550ba9ec23620fe2286`.

Resta **non provato**, come scrive la review, quale generazione il catalogo
vivo abbia ammesso durante la Release 19. Con la 20 il quesito non ha più
effetto, ma non diventa per questo dimostrato.

## A-11 — accolto; correzione scritta, applicazione in attesa di decisione

La causa è quella descritta. Tre fatti in più, verificati sul codice e sulla
macchina:

1. `commit_birth_snapshot` accetta un albero canonico assente anche quando
   esiste una generazione predecessore: `old_tree_id` diventa `None`.
2. `AuthoringInstallJournalV1` non lo vieta: `old_tree_id` e predecessore
   sono annullabili **indipendentemente**. Il §7.1 della roadmap dice invece
   «nullable soltanto alla prima nascita». È una **divergenza fra norma e
   codice**, e la correzione proposta sotto ci si appoggia: la prima consegna
   dopo la transizione di un contratto esistente è proprio «albero assente,
   predecessore presente». Va decisa in norma, non sfruttata in silenzio.
3. Questo account ha l'autorità Birth predisposta
   (`birth_authority_is_prepared_v1() → True`): il contesto dell'operatore
   può avviare il runtime Birth.

Correzione proposta, una sola funzione (`verify_named_executors`): in
`STORE_ONLY` il candidato è la cartella dell'operatore `executors/<nome>`
quando esiste — con `materialize_birth_candidate_from_authoring`, che la
stessa funzione usa già in modalità di redazione — altrimenti si riammette
l'albero redatto, e con nessuno dei due si chiude con `birth_unavailable`.
Nessuna autorità nuova: la cartella è una proposta, Birth ammette o rifiuta,
la generazione lega quei byte.

**Stato:** la modifica a `runtime/stack_reconcile.py` è stata **bloccata dal
classificatore di sicurezza** della sessione — è il punto che sceglie quali
byte entrano nel negozio di produzione. Non l'ho aggirata; la decisione è di
Roberto. Le due prove dei due casi della review (redazione assente; redazione
presente ma diversa dal candidato) sono scritte e messe da parte. Nella suite
ci sono le due che descrivono il comando com'è: senza cartella locale
riammette l'albero redatto; senza niente da ammettere rifiuta. **Non è
provato** il percorso dalla modifica a una generazione nuova riletta: richiede
la modifica.

## O-04 — accolto e corretto

`withdraw_superseded_claim` riconosce ora lo stato «release già nell'archivio
di **questo** tentativo, rivendicazione ancora pendente» e sposta soltanto la
rivendicazione, dopo aver verificato che il descrittore della release
parcheggiata nomini la sequenza della rivendicazione. L'archivio porta il nome
del proprio tentativo, quindi quello di un altro non è mai lo stesso percorso;
una release assente da entrambi i posti resta un rifiuto.

Prova generale, scenario 9: primo spostamento reale, poi due chiamate — la
prima riprende e restituisce la richiesta, la seconda non trova niente da fare;
l'archivio contiene i due oggetti. Un archivio intestato a un altro tentativo
è rifiutato con l'errore originale. Lo scenario 10 conserva tutti i rifiuti
precedenti, compreso «a claim with no release directory». **REHEARSAL_OK, 10
scenari.** Limite invariato: non è un arresto fisico.

## Precisazione O-03 — corretto il codice, non la promessa

Una copia in arrivo rifiutata viene ora messa da parte (`.rejected-NN`)
**prima** del rifiuto: prima restava nello slot che la preparazione successiva
svuota. «Niente viene cancellato» diventa vero anche in questo caso. La prova
generale verifica **esattamente una** copia conservata e che contenga i byte
rifiutati, non più `<= 1`.

## A-10 — corretto

Nella consegna le affermazioni su Release 4 e 5 sono marcate storiche, la
decisione «rilasciare le due correzioni» è segnata come fatta, e lo stato
corrente rimanda alla Release 20.

## Sui limiti della prova generale

Accolti come scritti: il sostituto di `open_parent` non ripete tutti i
controlli dei permessi, e lo scenario candidato neutralizza `chown`. Non li
presento come prova delle protezioni di root.

## Ciò che resta aperto dopo questa tornata

- **A-11**: applicazione (decisione di Roberto), poi la prova dalla modifica
  alla generazione riletta; e la divergenza norma/codice su `old_tree_id`.
- **A-02** e **A-05**: codice di prodotto, non ancora toccato.
- **A-01**: il modello di fiducia amministrativa resta un'assunzione
  dichiarata; **A-04** resta condizione di disponibilità.
- **F5-F6**: nessuna nuova ammissione reale in questa tornata.

Nessun GO incondizionato viene rivendicato da questa risposta.
