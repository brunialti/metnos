# Specifica degli annullamenti che richiedono riprogettazione

Stato: **contratto comune e quattro executor implementati secondo ADR 0217;
`delete_dirs` resta in progettazione**.

Data: 23 agosto 2026.

## Confine comune

Un executor non diventa annullabile perche' esiste un'azione dal nome
contrario. L'esecuzione deve produrre una ricevuta non ricostruibile dal testo
utente, legata all'istanza esatta creata o allo stato precedente esatto. Il
reverse deve verificare che il bersaglio non sia stato sostituito nel frattempo
e deve dichiarare separatamente successo, fallimento, parzialita' e assenza di
effetto.

Il broker di undo non deve contenere nomi di executor, provider, pacchetti,
domini, label o percorsi speciali. Per i rami misti serve un contratto firmato
generale di ricevuta per-esecuzione: un ramo mutante e reversibile consegna la
ricevuta; un ramo riuscito senza effetto viene chiuso come `no_effect`; un ramo
intenzionalmente irreversibile non viene presentato come annullabile. Questo
contratto e' ora parte dello standard firmato: il runtime interpreta
esclusivamente l'esito generico e non conosce gli executor che lo usano.

## `open_sites`

### Stato attuale

`open_sites` puo' creare un nuovo contesto browser oppure riusare una sessione
autenticata compatibile. Il risultato espone `session_id` e `reused`. Chiudere
un ID appena creato e' un inverso esatto; chiudere una sessione riusata
distruggerebbe invece stato precedente al turno. Il riuso si limita a
verificare e toccare la sessione, non naviga verso una nuova pagina.

### Contratto proposto

Per ogni entry riuscita il forward deve emettere `created=true|false`. La
ricevuta deve contenere soltanto gli ID con `created=true`, owner implicito
autenticato e pattern standard di cancellazione per ID. Un risultato composto
da soli riusi deve terminare come `no_effect`, senza apparire nella pila di
undo. Un batch misto chiude soltanto i nuovi ID. Il reverse fallisce se il
broker non conferma ownership e chiusura dell'ID esatto; non cerca per host,
URL, titolo o label.

### Gate

Test nuovi/riusati/misti, owner differente, sessione gia' scaduta, ricevuta
vuota o alterata, doppio undo e prova reale con apertura e chiusura dello stesso
ID.

### Implementazione

`open_sites` emette `created` per ogni successo e registra soltanto gli ID
creati. Un batch di soli riusi chiude il turno come `no_effect`; il reverse
chiude gli ID esatti con l'owner autenticato. I test automatici coprono nuovo,
riuso e batch misto.

## `delete_dirs`

### Stato attuale e prova reale

Il backend Drive usa gia' cestino e ID esatti, quindi quel ramo ha un inverso.
Il backend locale rimuove invece directory vuote o alberi ricorsivi. Un
prototipo ha tentato di spostare atomicamente l'albero nello store centrale di
undo: i test diretti erano verdi, ma due turni reali hanno restituito `EXDEV`.
Nel sandbox il target e lo store di history sono mount distinti anche quando i
path risiedono sullo stesso disco. Copiare l'albero e poi eseguire `rmtree` non
ha un punto di commit atomico e puo' lasciare, dopo un crash, backup incompleto
e sorgente parzialmente cancellata.

### Contratto proposto

Il primo requisito e' il contratto generale per rami misti: Drive puo' emettere
la ricevuta `restore_trashed_files`, mentre il ramo locale resta non
annullabile finche' non esiste uno store corretto. Per il locale vanno
confrontate e approvate almeno due architetture:

1. cestino per-filesystem, collocato nello stesso mount del target, con indice
   autenticato nello store centrale, retention e garbage collection anche sui
   device offline;
2. archivio verificato a due fasi, con stato `prepared/committed`, ripresa dopo
   crash ed executor tipizzato di estrazione disponibile anche sul device.

La prima conserva il rename atomico ma introduce indici e cleanup distribuiti;
la seconda funziona fra filesystem ma richiede una macchina a stati e non puo'
essere trattata come un semplice blob file. Nessun path laterale nascosto puo'
essere lasciato senza ownership, indice e retention.

### Gate

Directory vuota e ricorsiva, `/tmp`, home e volume esterno, mount separati del
sandbox, symlink, metadati e contenuti binari, crash in ogni fase, device
offline durante la retention, occupazione concorrente del path originale,
doppio undo e prova reale Linux/Windows. Nessun nuovo codice prima della scelta
dell'architettura di storage e dell'approvazione del contratto per rami misti.

## `set_signatures`

### Stato attuale

Lo stesso executor esegue upsert di whitelist/blacklist, rimozione verso
`unknown` e creazione possibile di una severity `forbidden`. La Legge 1 vieta
di cancellare una signature `forbidden`; quindi un semplice
`revertible=true` sarebbe falso almeno per quel ramo. Inoltre l'upsert corrente
non usa compare-and-swap contro modifiche successive.

### Contratto proposto

Per i soli rami ammessi all'undo, il forward deve registrare la riga completa
prima e dopo, compresi source, contatori, timestamp, peso e versione seed. Il
reverse deve procedere solo se la riga corrente coincide con lo snapshot
`after`: se prima era assente elimina la riga creata, altrimenti ripristina lo
snapshot `before` in una transazione. Una postcondizione `forbidden` non emette
mai una ricevuta reversibile. Anche questo richiede il contratto generale per
rami misti; in alternativa l'intero executor resta non annullabile.

### Gate

Round-trip insert/update/delete, conflitto concorrente, riga seed, contatori e
timestamp, `forbidden` mai cancellato, ricevuta incompleta, doppio undo e
transazione interrotta.

### Implementazione

E' stata scelta l'annullabilita' condizionale. `set_signatures` acquisisce una
transazione immediata, registra la riga completa prima/dopo e ripristina con
compare-and-swap. Creare `forbidden` produce `irreversible`; una riga
`forbidden` esistente non viene modificata ne' cancellata.

## `run_processes` (nome canonico da ADR 0218)

### Perche' oggi non basta

Il client restituisce `package_id`, `lifetime` e un booleano derivato dalla
stringa diagnostica `already_running`. Non restituisce l'identita' del processo
avviato. Terminare per `package_id` potrebbe uccidere un'istanza preesistente,
una seconda istanza avviata dopo il turno o un processo figlio non creato da
Metnos. Con `lifetime=persistent` esiste inoltre una seconda mutazione: la
registrazione di avvio automatico, distinta dal processo corrente.

### Contratto proposto

L'helper autenticato deve esporre un'operazione tipizzata `start` che restituisca
una ricevuta per pacchetto con almeno:

- `created_process=false` per un'istanza gia' attiva, oppure PID e
  creation-time del kernel legati al pacchetto risolto; la richiesta inversa
  firma questa identita' completa;
- `created_startup_registration=false` oppure l'ID opaco della registrazione
  persistente appena creata;
- versione del protocollo helper e identita' del dispositivo.

Il gemello tipizzato `stop` deve accettare esclusivamente tali handle, verificare
PID **e** creation-time per impedire il riuso del PID, rimuovere soltanto la
registrazione creata dal forward e riportare le due postcondizioni. Se il
processo e' gia' terminato, quella componente e' `no_effect`; se l'ID ora
designa un'altra istanza, il reverse fallisce senza terminarla. Non sono
ammessi nomi processo, path, comandi o ricerca per package nel reverse.

### Gate

Gia' attivo, nuova istanza, PID riusato, uscita spontanea, sessione e
persistent, registrazione preesistente, batch parziale, restart client,
ownership dispositivo, doppio undo e test reale Windows.

### Implementazione

Il protocollo helper 4 e le build client/helper 0.2.56 aggiungono lo stop
tipizzato e firmato. L'helper riapre l'oggetto processo, confronta percorso
canonico e creation-time prima di terminarlo. Sessione nuova e' reversibile,
istanza gia' attiva e' `no_effect`; `persistent` resta `irreversible` perche'
la registrazione di startup non ha ancora un'identita' verificabile analoga.
Test Rust, contratto wire e cross-build Windows sono verdi; la prova su una
macchina Windows reale resta un gate di rilascio.

## `login_urls`

### Perche' oggi non basta

Con `force=false` un cookie jar valido viene riusato e non c'e' effetto. Negli
altri casi viene costruito un jar nuovo e salvato sul path 0600 del dominio,
sovrascrivendo eventualmente un file precedente. Per fare undo bisogna
distinguere file assente, file precedente e riuso; il contenuto del jar e' un
segreto di sessione e non puo' essere duplicato nel JSONL di undo.

### Contratto proposto

Immediatamente prima della sostituzione il backend deve creare, nello store
protetto dell'attore, un
backup opaco cifrato o una sostituzione atomica del file precedente. Il journal
conserva soltanto un handle non predicibile, il digest del file `after`, il path
canonico gia' autorizzato e l'esito `created|replaced|no_effect`. Il reverse
opera con compare-and-swap: procede solo se il cookie jar corrente ha ancora il
digest `after`; `created` elimina quel file, `replaced` ripristina atomicamente
il backup. Il backup usa mode 0600, cifratura e chiave dello stesso vault,
owner binding, TTL, purge e cancellazione dopo undo. Nessun cookie o campo
credenziale entra nel journal, nei log o nell'output.

### Gate

Cache hit, creazione, sostituzione, jar modificato dopo il login, crash prima e
dopo rename, permessi 0600, isolamento utenti, scadenza/purge, ricevuta forgiata,
doppio undo e login reale controllato. La retention coincide con
`METNOS_UNDO_RETENTION_DAYS` (30 giorni di default) e non introduce una seconda
politica temporale.

### Implementazione

`protected_undo` cifra backup generici con chiave derivata e separata per
dominio, handle casuale, binding attore/namespace, mode privati, scadenza e
purge. `login_urls` salva atomicamente, registra soltanto handle e digest e
ripristina soltanto se lo stato corrente coincide con `after`. I test coprono
cache hit, creazione, sostituzione, ripristino esatto, conflitto concorrente,
binding e purge.

## Casi esclusi per decisione di prodotto

`set_credentials` e `set_persons` restano intenzionalmente non annullabili.
Credenziali e dati biometrici vengono rimossi soltanto tramite una richiesta
esplicita dell'utente agli executor di cancellazione. Il comando generico
"annulla" non deve eliminare automaticamente questi dati ne' conservare copie
aggiuntive per rendere possibile un ripristino.
