# RM-0009 — analisi preparatoria del coordinatore

Data: 15 settembre 2026. Stato: **proposta di lavoro, non baseline congelata**.

La specifica normativa resta
`internal/roadmap/RM-0009-crescita-allineata-delle-capacita.md`.
Questo rapporto non ne cambia implicitamente contratti, gate o stati. Raccoglie
le questioni da risolvere in G0.2–G0.6 e le prove da assegnare soltanto dopo il
congelamento. Nessuna attività di prodotto elencata qui è dichiarata eseguita.

## 1. Vincolo di coordinamento

Roberto ha affidato al coordinatore lo sviluppo, la scelta degli agenti e la
qualità finale, poi ha disposto di attendere conclusione e commit del lavoro
RM-0008. Nell'attesa sono autorizzati analisi, preparatori e ambiente di test.

Non si deve confondere:

1. commit intermedio RM-0008 con conclusione del lavoro;
2. baseline sorgente coerente con certificazione di esercizio;
3. contratto del verifier definito con attestazione già disponibile;
4. test di fixture con certificazione del codice di prodotto.

Il checkout principale osservato è `037840f455c005899dcaf089cc2e52dd614f07de`.
La linea RM-0008 è diversa e ancora in movimento. I rapporti `birth-contract.md`,
`inventory-data.md` e `inventory-security.md` registrano snapshot e limiti;
non è lecito scegliere il checkout principale come scorciatoia.

L'assenza di FS-A, FS-B o EXT-RM0008-F5 non vieta definizione e test isolati dei
lettori/decisori. Le prove restano obbligatorie al relativo punto di esercizio.
Il codice di prodotto rimane inoltre soggetto ai passaggi G0.6–G0.10 della
roadmap. L'incarico generale non sostituisce l'approvazione del payload esatto
prevista da G0.9.

## 2. Questioni tecniche da chiudere prima dell'assegnazione agli esecutori

### A — Nuova proposta dopo rollback e unicità globale

**Evidenza normativa:** §5.4.1 dichiara `rolled_back` terminale e richiede un
nuovo evento causale per una nuova proposta; §5.7 esclude l'evento dalla
fingerprint, mentre A.4/P2 richiede unicità del canonico. Il codice osservato
`runtime/change_intents.py::upsert_intent` conserva lo stato già progredito
quando ritrova la stessa fingerprint.

**Problema:** un nuovo evento sullo stesso bisogno può ritrovare per sempre il
canonico terminale. Inserire l'evento o l'owner nella fingerprint risolverebbe
il blocco violando però la deduplica globale; riaprire il terminale violerebbe
la macchina a stati.

**Direzione proposta:** distinguere la singola istanza di lifecycle dal
bisogno canonico. Il consolidamento deve conservare immutabile il lifecycle
precedente e creare una nuova istanza canonica corrente in una sola
transazione, riusando archivio/alias se sufficiente. Prima di scegliere DDL e
indici occorre specificare: fonti già consumate, eventi arretrati rispetto al
rollback, rifiuti ancora validi e riferimenti delle receipt storiche. Non
aggiungere un arco `rolled_back -> proposed` né un secondo canone concorrente.
Receipt, valutazioni e operation storiche restano legate al ciclo originale:
un alias di lookup non deve riassegnarle automaticamente al ciclo nuovo.

**Prove da congelare:** stesso evento ripetuto non riapre; evento genuinamente
nuovo può proporre di nuovo; due writer creano una sola istanza corrente;
receipt e fonti storiche restano interrogabili e soggette al purger.

### B — Campione insufficiente e precedenza delle domande

**Evidenza:** §5.3 riga 9 dice «resta in ombra, nessuna domanda», ma non nomina
uno dei quattro risultati `auto/ask_user/deny/blocked`. La riga 3, applicata
prima della 9, può chiedere consenso o costo anche senza campione sufficiente.

**Direzione proposta:** rappresentare l'attesa con `blocked` e un reason
tipizzato di evidenza insufficiente, senza regola di rifiuto e con ripresa in
`proposed`. Per `promote_plan` la verifica del campione deve precedere ogni
domanda. Il momento di decisione unico di promote deve avere una matrice di
fatti propria: non può ereditare per errore una receipt Birth obbligatoria da
D2 create/extend. La tabella completa va normalizzata in G0.6; nessun agente
medium deve inventare un quinto risultato o una nuova macchina a stati.
G0.6 deve riallineare anche §5.4.1, `DecisionMoment` e D.8: oggi l'arco
`proposed -> blocked` è descritto per il solo veto D1; la proposta lo estende
esplicitamente al veto di promote, con `resume_state=proposed`. Veto tecnico
e rifiuto attivo mantengono la precedenza; l'attesa del campione precede le
domande. Il rapporto non applica già questa modifica alla roadmap.

**Prove:** campione 0/minimo−1 con costo o consenso necessario → nessuna
domanda; minimo raggiunto → rivalutazione completa; rifiuto attivo resta
efficace; nessuna attivazione durante l'attesa.

### C — Cutoff coerente fra SQLite e JSONL

**Evidenza:** A.4/P0 descrive backup SQLite e filtro temporale dei turni; D-P0.1
richiede invece un cutoff globale coerente e replica identica dello snapshot.

**Problema:** la consistenza del singolo backup non dimostra la coerenza
causale fra archivi separati. Un writer può committare un effetto in un DB e
scriverne l'evento dopo che l'altro archivio è già stato fotografato.

**Direzione proposta:** definire un protocollo di barriera/watermark accettato
da tutti i writer partecipanti, oppure un prefisso causale verificabile che
non dipenda dal solo timestamp. Il manifest deve distinguere digest dei byte
conservati e digest logico dell'esportazione canonica: «stesso cutoff» non
autorizza a confrontare indiscriminatamente due file SQLite fisici. Sorgente
non coordinabile, linea JSONL incompleta o writer fuori inventario rendono la
prova incompleta; non autorizzano una copia dichiarata coerente.

**Prove:** commit su DB A prima del cutoff e append B ritardata; writer che
oltrepassa la barriera; crash a metà riga; confronto di due esportazioni;
snapshot interrotto che non pubblica un manifest di successo. Usare soltanto
archivi sintetici finché non esiste la specifica congelata P0.

### D — Limiti numerici della politica

**Evidenza:** §5.6 ammette `success_drop_pp=0` e usa `delta >= soglia`; con
successo invariato il confronto diventa vero. Sono previsti confronti
percentuali per costo/latenza, ma il caso baseline zero non è ancora chiuso.

**Direzione proposta:** definire peggioramento come delta strettamente positivo
che raggiunge la soglia. Per confronto relativo: baseline=0 e osservato=0 non
è regressione; baseline=0 e osservato>0 è aumento rispetto a zero, rappresentato
con reason tipizzato, non con `Infinity` nel JSON. Baseline/costo mancanti
restano sconosciuti, mai zero. Rifiutare `NaN`, infinities, overflow e tipi
booleani dove è richiesto un numero; validare anche il risultato derivato.

**Prove:** zero/zero, zero/positivo, uguaglianza esatta alla soglia positiva,
calo minimo con soglia zero, valori non finiti e campione insufficiente. Queste
regole non cambiano i default e non introducono soglie dipendenti dall'owner.

### E — Cancellazione, revoca e writer concorrenti

**Evidenza:** `inventory-data.md` documenta purger presenti ma non collegati,
archivi senza owner immutabile, copie derivate e stato in RAM. La sola chiamata
sequenziale ai purger non impedisce una scrittura tardiva da un turno già in
corso. Lo stesso vale per claim remoto e ripresa durable.

**Direzione proposta:** revoca dell'identità operativa prima del purge, con
controllo al confine effettivo di scrittura/claim; passaggi idempotenti e stato
di avanzamento recuperabile. Congelare per ogni store la catena autorevole che
riconduce sender/turn/hash al principal prima di eliminarla. La scansione deve
coprire anche archivi storici e cache, non solo le viste ordinarie.

Il tag HMAC serve esclusivamente alla revoca operativa; il tombstone casuale
serve a scollegare la provenienza. Nessuno dei due può cambiare ranking,
deduplica, soglie o disponibilità dell'artefatto globale. Non generalizzare i
dati personali o biometrici per colmare un owner mancante.

**Prove:** delete intercalato con writer/claim/flush/ripresa; crash a ogni
purger; richiesta arretrata e riuso della stessa identità; stessi effetti
globali per l'altro utente; assenza di ID raw e dati personali nelle copie.
G0.6 deve fissare la serializzazione revoca/commit per ciascun writer, oppure
un protocollo equivalente di arresto e riconciliazione: il solo controllo
prima di scrivere non chiude la race. P2.9 copre gli store e i percorsi
operativi esistenti con prove isolate; grant/completion v2 e il loro purge
appartengono alle rispettive unità F6 e a F6.6, senza diventare antenati di
I1.1. Dopo il CAS irrevocabile di start il purge non dimostra zero effetti:
l'esito resta ignoto finché osservato e non va eliminata l'unica evidenza
necessaria alla riconciliazione. I contributi canonici globali lecitamente
privi di dati personali restano disponibili, scollegandone la provenienza.

### F — Domanda monouso e consegna al canale

**Evidenza:** §5.5 richiede unicità e conservazione delle domande logiche in
coda. La consegna al canale aggiunge un effetto esterno il cui contratto di
recupero va specificato separatamente; il paragrafo non dimostra già una
garanzia di consegna fisica unica.

**Problema:** crash dopo l'invio e prima del salvataggio della receipt può
rendere ignoto l'esito della consegna. Una transazione SQLite non rende
automaticamente monouso l'invio Telegram/HTTP.

**Direzione proposta:** identificatore logico stabile della domanda; creazione
di domanda/token, prenotazione del budget e outbox nella stessa transazione
di emissione. Il consumo del token appartiene alla distinta transazione della
risposta, insieme a stato, eventuale regola di rifiuto ed epoca. G0.6 deve
fissare la sorte della prenotazione su errore certo, consegna ignota, scadenza
e cambio di settimana. Stato di consegna e stato della decisione sono distinti.
Il retry riusa domanda e token, non prenota un altro posto nel budget. Il
contratto di ciascun canale deve esplicitare receipt/idempotenza/rilettura
disponibili. In assenza di prova non dichiarare consegna fisica exactly-once:
normalizzare in G0.6 la gestione di `delivery_unknown` e l'accesso alla domanda
pendente senza creare decisioni concorrenti. I soli doppi clic non collaudano
questo confine.

**Prove:** crash prima/dopo invio e prima/dopo receipt; stesso token su due
messaggi; scadenza e rivalutazione; budget settimanale conteso da due worker;
nessun invio in un test con adattatore inerte.

### G — F6 deve seguire il call graph reale

**Evidenza:** `inventory-security.md` distingue builtin ordinario, verb-unique,
executor locale, remoto, durable e passi interni del Tutor. Il builtin
ordinario non attraversa `loader.invoke_verb_unique`; l'undo remoto nasce
prima dell'enqueue; un executor durable attraversa già `invoke_executor`.

**Direzione proposta:** correggere il work manifest per includere i punti
effettivi prima del primo effetto. D-F6.2b/c/d condividono parti di
`agent_runtime.py`: assegnazione seriale o un unico proprietario del file,
non tre agenti che modificano liberamente lo stesso confine. Il ponte durable
non deve aggiungere una seconda osservazione/gate al percorso comune.

**Prove:** un'osservazione per percorso/tentativo; diniego enforced prima di
undo/subprocess/builtin/enqueue; errore del lettore in shadow conserva l'esito
ordinario e non emette grant o `AuthorityViolationV1`. Un task interno al
scheduler non va trattato come executor solo perché passa dallo scheduler.
Le prove di diniego con zero effetti appartengono alla tranche di enforcement;
I1.1 richiede soltanto conteggio singolo e invarianza degli esiti in ombra,
senza antenati FS/S0/X0/F6.3-F6.6.

### H — Identità Birth, osservazione e perdita delle dipendenze

**Evidenza:** `birth-contract.md` censisce i chiamanti e distingue il risultato
di pubblicazione dalla receipt Admission autenticata. La proposta di façade
non è ancora un contratto approvato RM-0008.

**Direzione proposta:** binding durevole operation→candidato→richiesta→receipt,
rilettura autenticata e CAS sulla generazione target. Ripetere una chiamata
con un `reason` diverso non deve creare una nuova richiesta per la stessa
operation. La riverifica dei prerequisiti deve arrivare al confine effettivo,
non fermarsi al calcolo precedente di D1/D2. Se l'effetto non è osservabile,
`unknown` non equivale ad `absent` e non autorizza un nuovo effetto.

**Prove:** due worker; pubblicazione riuscita seguita da crash; lease scaduta;
drift/revoca tra decisione e invio; effetto presente ma receipt temporaneamente
illeggibile; sostituzione della generazione. L'attesa di dipendenza mantiene
operation ID e tentativi; il recupero riparte da observe. Le prove simulate
non certificano l'idempotenza del publisher reale.

## 3. Ordine di lavoro dopo RM-0008

1. Conferma esplicita della conclusione e commit finale; ricontrollo read-only
   di storia, stato dei file rilevanti e corrispondenza con la release osservata.
2. Ricalcolo degli inventari sul commit finale; attribuzione di ogni modifica
   da riportare, senza importare cumulativamente il worktree principale.
3. Chiusura tecnica A–H, helper G0.4 e contratto con RM-0008; nomi, schemi,
   numeri migrazione, file, test, dipendenze e condizioni di esercizio nel work
   manifest G0.6. Le espansioni FS-A/FS-B devono corrispondere all'inventario.
4. Due dry-run medium indipendenti G0.7 e review architettura/sicurezza G0.8.
   La review dei preparatori non sostituisce questi passaggi.
5. Anteprima normalizzata con digest, approvazione G0.9, conservazione della
   baseline esterna e applicazione del payload esatto G0.10. Solo allora si
   assegnano le unità di prodotto non-FS secondo la roadmap. FS-A/FS-B
   conservano l'avvio già autorizzato dopo G0.6, con assegnazione esclusiva e
   prerequisiti espliciti, una volta terminata l'attesa RM-0008 richiesta.

Nessun problema tecnico A–H viene trasferito a un agente medium perché lo
risolva implicitamente. Il coordinatore deve scegliere la soluzione minima,
farla contestare e congelarla con criteri riproducibili. Una nuova decisione
materiale di prodotto non già autorizzata resta da sottoporre a Roberto.

## 4. Contestazione indipendente

`analysis-review.md` ha confermato il fondamento delle questioni e richiesto
cinque precisazioni (R1–R5), recepite qui: arco/momento di promote, emissione
del budget distinta dalla risposta, serializzazione della revoca e confine
post-start, separazione dei test enforced da I1.1, avvio FS dopo G0.6 e
unicità logica distinta dalla consegna fisica. Le direzioni rimangono proposte
da normalizzare e approvare secondo G0, non nuove regole del runtime.
