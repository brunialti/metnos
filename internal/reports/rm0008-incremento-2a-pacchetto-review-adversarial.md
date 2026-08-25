# RM-0008 — Pacchetto per la revisione adversarial del piano di ripresa 2A

**Data della fotografia:** 25 agosto 2026

**Stato dichiarato:** sviluppo congelato; piano non ancora approvato

**Base Git pubblica:** `83345c18406c19e47009218d1e7e4f0f584672ac`

**Ramo pubblico:** `codex/rm0008-recovery`
**Oggetto della revisione:** piano diagnostico e criteri di ripresa dell'incremento
2A; non approvazione del codice e non chiusura di RM-0008

## 1. Scopo dell'incarico al revisore

Si richiede una revisione adversarial indipendente del piano con cui riprendere lo
sviluppo dell'incremento 2A di RM-0008. Il precedente ciclo di lavoro ha prodotto
più correzioni successive senza una convergenza sufficiente su Windows. Per
questo motivo il codice è stato congelato e non deve essere corretto prima che
le cause, le prove e l'ordine di intervento siano stati approvati.

Il revisore non deve presumere che il documento sia corretto perché dettagliato,
né che un test verde dimostri il requisito indicato dal suo nome. Deve
confrontare ogni affermazione con il codice, con i test effettivamente raccolti
e con i vincoli normativi di RM-0008.

Il verdetto richiesto è uno dei seguenti:

- `APPROVABILE PER LA SOLA RIPRESA DIAGNOSTICA`, se il piano conserva tutti i
  requisiti 2A, separa correttamente diagnosi e certificazione e non anticipa il
  2B o fasi successive;
- `NON APPROVABILE`, con rilievi P0-P3 e correzioni documentali minime, se resta
  una lacuna che può produrre altro codice a tentativi, un falso verde o una
  perdita di requisiti.

Un'approvazione non autorizza a dichiarare 2A completato. Autorizza soltanto a
costruire i riproduttori diagnostici e le prove di accettazione nell'ordine
concordato.

## 2. Materiale autorevole da leggere

Il revisore deve leggere nell'ordine:

1. `internal/roadmap/RM-0008-porta-unica-nascita-executor.md`, in particolare i
   §§23.6-23.8 e lo stato della roadmap;
2. `internal/reports/rm0008-gruppo2-analisi-implementazione.md`, in particolare
   i §§7, 10, 12, 13, 15 e 16;
3. il prototipo locale nei file:
   - `runtime/executor_birth_secure_fs.py`;
   - `runtime/executor_birth_keystore.py`;
   - `runtime/executor_birth_semantic_authority.py`;
   - `runtime/executor_birth_approval_authority.py`;
4. le prove correnti:
   - `tests/runtime/contracts/test_executor_birth_secure_fs.py`;
   - `tests/runtime/contracts/test_executor_birth_keystore.py`;
   - `tests/runtime/contracts/test_executor_birth_semantic_authority.py`;
   - `tests/runtime/contracts/test_executor_birth_approval_authority.py`;
   - `tests/portable/test_executor_birth_secure_fs_native.py`;
   - `tests/portable/test_executor_birth_semantic_authority_windows.py`;
5. `.github/workflows/portable-contract-store.yml`.

Il presente pacchetto è un indice ragionato, non sostituisce le fonti sopra.

## 3. Stato verificabile, senza interpretazioni estensive

La registrazione pubblica `83345c18` contiene soltanto la specifica di
implementazione. L'esecuzione pubblica GitHub Actions `32887571156` è verde su
quella registrazione documentale e non contiene il prototipo 2A.

Il prototipo è locale e non registrato in Git. La fotografia riproducibile usa:

- directory di lavoro `/tmp/metnos-rm0008-recovery`;
- base Git `83345c18406c19e47009218d1e7e4f0f584672ac`;
- Python `3.12.3`;
- impronta SHA-256 aggregata dei quattro moduli e dei sei file di prova elencati
  nel comando, calcolata con `sha256sum <file...> | sha256sum` nello stesso
  ordine del comando:
  `8069ebdd5f6bb6c7b110277c00a6a302d4d241c6d7a605dc50c8f8083c8c113b`;
- 75 `node-id` raccolti, con impronta SHA-256 dell'elenco nell'ordine di
  raccolta pytest
  `73c821eda82ee6908a3b20c8c06076bf2edc0d96d0a548d9bd4cfe83b204826c`.

Il comando esatto è:

```bash
python3 -m pytest \
  tests/runtime/contracts/test_executor_birth_secure_fs.py \
  tests/runtime/contracts/test_executor_birth_keystore.py \
  tests/runtime/contracts/test_executor_birth_semantic_authority.py \
  tests/runtime/contracts/test_executor_birth_approval_authority.py \
  tests/portable/test_executor_birth_secure_fs_native.py \
  tests/portable/test_executor_birth_semantic_authority_windows.py -q
```

La raccolta mirata eseguita su Linux ha prodotto:

```text
63 passed, 12 skipped
```

I dodici casi saltati sono specifici per Windows. Il risultato dimostra
importabilità e una parte delle compatibilità Linux; non dimostra ACL, blocchi,
rinomina, percorsi o arresti Windows. La matrice pubblica raccoglie
`tests/portable`, ma non le prove causali che oggi risiedono in
`tests/runtime/contracts`, e non dispone ancora dell'attività Windows dedicata
alle ACL reali prevista dalla specifica.

Non esiste alcuna registrazione Git di codice 2A da approvare. Il revisore deve giudicare il
piano di ripresa e segnalare se il prototipo contraddice una causa dichiarata,
ma non deve trasformare la revisione in un'approvazione implicita
dell'implementazione.

## 4. Confine esatto dell'incremento 2A

L'incremento 2A deve:

- introdurre una primitiva comune di accesso relativo a handle;
- autenticare la radice già aperta e adottare un descrittore consumabile una
  volta, mantenendo identità e profilo vincolati alla sessione;
- validare volume e funzionalità del filesystem prima di ogni creazione;
- su Windows, costruire, applicare e verificare il descrittore di sicurezza
  esatto e abilitare e ripristinare il privilegio minimo necessario;
- produrre un inventario relativo a handle con identità, tipo e metadati di
  collegamento sufficienti a riconoscere oggetti estranei;
- migrare le letture dell'archivio chiavi, dell'autorità semantica e del registro
  approvazioni;
- introdurre blocchi condivisi ed esclusivi comuni;
- introdurre la rinomina Windows per handle senza sostituzione;
- introdurre la disposizione sicura di un oggetto di transazione come
  operazione di basso livello vincolata alla sessione;
- normalizzare gli errori di sistema in codici Birth stabili e privi di percorsi
  o dettagli ACL.

Queste capacità restano operazioni di basso livello. Non scelgono radici,
identità, profili, dati, politica transazionale o stato del predispositore.

L'incremento 2A non deve:

- generare, copiare o installare chiavi e autorità;
- predisporre archivi produttivi;
- creare `transaction-v1.json`, journal o checkpoint;
- attivare bootstrap, Phase 3, pubblicazione o migrazione dei chiamanti;
- scrivere `set.json`, marker o stato di recupero del predispositore;
- rendere vincolante la guardia F4 o anticipare F5-F6.

`dispose_transaction_object` può eliminare e riconciliare un oggetto mediante
handle e restituire un esito chiuso. Non può conoscere o registrare checkpoint:
quella politica appartiene al chiamante del 2B.

## 5. Modello probatorio proposto

Ogni criterio usa due artefatti distinti:

- **D — riproduttore diagnostico.** Dimostra la causa mediante analisi statica,
  barriera deterministica, sonda dell'ordine o iniezione di errore. Può terminare
  con successo perché ha osservato il difetto. Non è mai un controllo verde di
  conformità.
- **A — prova di accettazione.** Attraversa il simbolo produttivo e afferma
  l'invariante desiderata. Per un difetto già dimostrato deve essere rossa prima
  e verde dopo la correzione. Per una copertura ancora mancante può risultare
  verde alla prima esecuzione; in tal caso non si modifica il prodotto.

Soltanto gli A entrano fra i controlli obbligatori della matrice. Prima della
modifica del flusso di lavoro deve esistere un manifesto canonico contenente
identificativo del criterio, `node-id` pytest, piattaforma proprietaria, simbolo
produttivo e divieto di salto o successo inatteso. Il conteggio totale dei test
non è un criterio di raccolta.
Il nome di ogni prova, inclusa una prova preesistente, deve descrivere soltanto
la garanzia realmente esercitata; una prova nello stesso processo non può essere
nominata come certificazione multiprocesso.

Gli arresti reali mediante `SIGKILL` o `TerminateProcess` provano
l'integrazione, non la causa. Una finestra di durabilità deve prima essere
localizzata deterministicamente.

## 6. Cause di prodotto dichiarate

Il piano corrente identifica otto criteri rossi.

### R1 — Proprietà strutturale della capacità di scrittura

Il prototipo usa simboli Python raggiungibili come sigillo e consente di
modificare il descrittore prima dell'adozione. Il risultato richiesto non è una
non falsificabilità assoluta contro codice arbitrario nello stesso interprete.
Occorrono una sola entrata supportata nel modulo installatore, radice e identità
derivate internamente, descrittore consumabile una volta e non modificabile,
assenza transitiva di fabbriche di scrittura dalle facciate pubbliche e guardia
statica del grafo della distribuzione. Il limite dello stesso UID o SID resta
esplicito.

### R2 — Blocco globale esclusivo prima di ogni mutazione

Lo stato autorevole consente mutazioni senza conservare la modalità del blocco
globale. Ogni mutazione, inclusa la disposizione quando verrà introdotta, deve
fallire prima dell'I/O senza globale esclusivo. I caricatori interni di sola
lettura possono usare il globale condiviso.

### R3 — Disposizione autenticata dell'oggetto di transazione

L'operazione comune manca. Deve essere relativa a handle, richiedere globale
esclusivo e identità attesa, rifiutare link e oggetti estranei e riconciliare
l'esito. POSIX sincronizza la directory padre dopo `unlinkat`; Windows usa la disposition
sul medesimo handle e la rilettura. Nessun checkpoint appartiene a questa
operazione 2A.

### R4 — Recupero durevole del file di blocco POSIX vuoto

Il ramo che inizializza un file vuoto già esistente non sincronizza sempre la
directory. La causa deve essere provata con la sequenza osservabile scrittura
completa → `fsync` del file → `fsync` della directory, poi validata con arresto
reale.

### R5 — Profili Windows autenticati

La radice viene verificata per tipo, reparse point e percorso finale, ma non
rispetto al profilo proprietario/DACL. I profili dei discendenti vengono
inferiti e non provengono da un catalogo chiuso del proprietario installatore.
L'oracolo di prova deve essere indipendente dal generatore SDDL e dal
verificatore produttivo e deve usare token reali del servizio, un secondo utente
e un processo non elevato. L'indisponibilità dei token non può diventare uno
salto verde.

### R6 — Rinomina Windows dopo ispezione

Il ramo con directory memorizzata riusa un handle privo del diritto necessario;
il ramo non memorizzato può invece raggiungere la rinomina senza confronto ACL
completo. Le due cause richiedono riproduttori distinti. L'accettazione confronta
volume, FileID a 128 bit, tipo, profilo e inventario, incluse destinazione già
presente e destinazione creata alla barriera prima della chiamata.

### R7 — Inventario comune incompleto

Il record comune non distingue un collegamento simbolico da un file regolare e
non contiene il tag di reparse. Su POSIX un collegamento simbolico entra con
`directory=False`; su Windows il prototipo conserva nome, FileID e tipo di
directory, ma forza il numero di link a uno e perde il reparse tag. Servono due
riproduttori distinti, uno per piattaforma. La correzione modifica il record
condiviso, riapre gli ingressi mediante handle e deve riconoscere tipo, identità,
numero reale di link e tag specifico della piattaforma.

### R8 — Tre caricatori interni vincolati alla stessa radice

Mancano le entrate interne complete e la contesa reale. Processi distinti non
condividono una sessione: ciascuno adotta la propria sessione sulla stessa
radice autenticata e sullo stesso identificativo di oggetto. Per tutti e tre i
caricatori si prova la contesa globale esclusivo/condiviso. Il blocco locale
`birth-keystore.lock` si prova soltanto per l'archivio chiavi; non si inventa un
blocco locale per approvazioni o autorità semantica.

## 7. Coperture bloccanti non ancora provate

Questi criteri possono risultare verdi alla prima esecuzione; se accade, non
autorizzano una correzione del prodotto.

### C1 — Blocco Windows multiprocesso

Servono processi `spawn`, contese condiviso/condiviso,
condiviso/esclusivo ed esclusivo/condiviso e scadenza misurata. La variante di
recupero deve avere una barriera successiva a `CREATE_NEW`, ACL e `LockFileEx`,
ma precedente alla prima `WriteFile`; soltanto lì si usa `TerminateProcess`.

### C2 — Sostituzione Windows dopo l'apertura

Un processo vincola radice, intermedi e finale; un secondo processo tenta
rinomina o sostituzione dopo ogni segnale. Usa una junction soltanto per radice
e intermedi di tipo directory e un reparse point o collegamento simbolico per
il file finale. Il primo processo deve restare sull'identità originaria o
fallire chiuso e non deve mai leggere i byte sostitutivi.

### C3 — Sostituzione POSIX fra due `openat`

Un processo apre la radice o un intermedio e segnala una barriera; l'avversario
sostituisce il componente successivo. L'operazione resta sullo stesso
`st_dev`/`st_ino` o fallisce chiusa, senza usare il sostituto. La prova si ripete
per radice, ogni intermedio e oggetto finale.

### C4 — Blocco POSIX multiprocesso

Processi distinti devono provare condiviso/condiviso, condiviso/esclusivo ed
esclusivo/condiviso, con barriera, scadenza monotona e rilascio dopo la
terminazione del detentore. Il caricatore conserva il blocco condiviso mentre il
predispositore attende quello esclusivo; nessuna mutazione precede
l'acquisizione esclusiva.

Le barriere C1-C4 appartengono al banco di prova e sono realizzate mediante
orchestrazione o intercettazione confinata delle chiamate di sistema. Nessun
callback, percorso, parametro di politica, variabile d'ambiente o punto di
arresto selezionabile dal chiamante entra nella capacità distribuita.

## 8. Requisiti gialli ancora obbligatori

Oltre alle cause e coperture precedenti, la registrazione del codice richiede:

1. inventario e istantanea comuni contro aggiunta, rimozione, rinomina,
   sostituzione dello stesso nome e ingresso non JSON, con limite massimo;
2. compatibilità storica dei tre punti d'ingresso basati su `Path`, incluso archivio
   chiavi esterno senza blocco globale e materiale pubblico POSIX posseduto da
   UID differente ma non modificabile dal servizio;
3. durabilità POSIX di creazione e rinomina, incluse scritture corte, `EINTR`,
   errori di sincronizzazione, directory padre distinte, `EXDEV`, `ENOSYS` e assenza di
   ripiego non atomico;
4. ciclo di vita ed errori stabili su chiusura, sblocco e adozione, con ogni
   handle chiuso una volta e nessuna fuga di percorso, SID o DACL;
5. ripristino di `SeRestorePrivilege`, incluso fallimento del secondo
   `AdjustTokenPrivileges` e confronto reale dello stato prima e dopo;
6. raccolta pubblica tracciata dal manifesto dei `node-id`;
7. rifiuto Windows di volumi non NTFS o privi di `FILE_PERSISTENT_ACLS` prima di
   ogni creazione, con inventario invariato e oracolo del volume indipendente;
8. rifiuto POSIX di radice, directory o file autorevoli con UID diverso da
   quello autenticato, separato dalla compatibilità pubblica storica;
9. percorsi Windows lunghi, prefisso `\\?\`, differenze di
   maiuscole/minuscole e UNC secondo la matrice positiva o il rifiuto chiuso del
   §12.2; l'assenza di un server UNC certificabile non diventa uno skip verde;
10. dimensioni e offset Windows x64 di `FILE_RENAME_INFO`, `FILE_ID_INFO`,
    `FILE_DISPOSITION_INFO_EX` e `OVERLAPPED`, più il seriale volume
    `8000000000000001` conservato unsigned con sedici cifre esadecimali;
11. scrittura e rilettura Windows di tutti i byte da `0x00` a `0xff` senza
    trasformazioni di testo;
12. fallimento di `SetSecurityInfo` normalizzato, senza destinazione marcata
    completa e con riconciliazione di inventario e residui.

## 9. Ordine proposto per la ripresa

1. approvare questo piano senza modificare test, flusso di lavoro o prodotto;
2. produrre e riesaminare tutti i D;
3. scrivere tutti gli A e il manifesto di raccolta;
4. chiudere R1 e R2;
5. eseguire C1, C2, C3 e C4, perché blocco multiprocesso e vincolo degli handle sono
   prerequisiti del recupero;
6. su POSIX, chiudere R4, durabilità completa, proprietario autorevole, R3 e
   ciclo di vita;
7. predisporre nella matrice pubblica l'attività Windows bloccante con seconda
   identità locale, processo non elevato e verifica indipendente dei diritti
   effettivi. Nessuna correzione di prodotto R5-R7 è ammessa prima che questa
   attività esista e dimostri di poter fallire sui controlli negativi;
8. su Windows, chiudere prima supporto del volume e ABI, poi R5, ripristino del
   privilegio, fallimento di `SetSecurityInfo`, R7, R6, R3, percorsi e byte
   binari, in questo ordine;
9. chiudere R8, inventario comune e compatibilità storica sui tre caricatori;
10. eseguire arresti reali, sostituzioni sincronizzate e matrice completa;
11. richiedere una nuova revisione adversarial del codice e delle evidenze.

Al secondo fallimento consecutivo della stessa classe dopo una correzione, il
lavoro torna alla diagnosi e non introduce una terza variante.

## 10. Registrazioni candidate e criterio conclusivo

GitHub Actions può eseguire il prototipo esatto soltanto dopo una registrazione
pubblicata. Sono quindi ammessi commit candidati incrementali sul solo ramo di
revisione. Ogni candidato:

- è approvato in sola lettura prima della pubblicazione;
- supera tutte le prove locali applicabili e non regredisce controlli già
  certificati;
- contiene il marcatore `RM-0008-Status: candidate-not-certified`;
- non viene unito in `main`, etichettato, rilasciato o dichiarato completamento
  2A;
- associa la matrice pubblica al proprio SHA esatto;
- al secondo fallimento della stessa classe riattiva l'arresto diagnostico.

L'unione in `main`, il tag, il rilascio e la registrazione conclusiva 2A restano
vietati finché:

- R1-R8, C1-C4 e i dodici requisiti gialli non sono verdi sulla piattaforma
  proprietaria;
- il manifesto dimostra la raccolta esatta, senza salti o successi inattesi;
- non restano P0 o P1, né P2 che violino requisiti normativi;
- ogni P3 ha una disposizione esplicita;
- ogni limite o requisito non provato resta dichiarato e non colorato verde;
- una revisione indipendente approva codice ed evidenze.

Il criterio non chiude RM-0008 e non autorizza l'avvio dei gruppi successivi.

## 11. Domande obbligatorie per la revisione adversarial

Il revisore deve rispondere esplicitamente a tutte le domande seguenti.

1. Il confine 2A è completo e non anticipa dati, checkpoint o politica del 2B?
2. La sicurezza dichiarata per la capacità Python è verificabile oppure promette
   più di quanto il confine stesso UID/SID consenta?
3. Ogni mutazione è coperta dal requisito del blocco globale esclusivo prima
   dell'I/O?
4. La separazione D/A impedisce che un riproduttore del difetto venga scambiato
   per un verde di conformità?
5. Le prove dei tre caricatori usano sessioni distinte e soltanto i blocchi
   definiti normativamente?
6. Le prove Windows ACL sono indipendenti dal codice produttivo e usano identità
   reali sufficienti a dimostrare i diritti effettivi?
7. C1 localizza realmente la finestra fra creazione del file di blocco e prima scrittura?
8. C2 e C3 coprono ogni sostituzione dopo apertura su Windows e POSIX, e C4
   prova la contesa POSIX multiprocesso richiesta dal §12.3?
9. R6 separa correttamente handle memorizzato e sorgente non memorizzata, e
   riconcilia entrambe le parti dopo conflitto o errore?
10. Inventario, durabilità, volume, percorsi, ABI, byte binari, fallimento
    `SetSecurityInfo` e proprietario errato dispongono di una piattaforma, una
    barriera e un oracolo sufficienti?
11. L'ordine proposto evita di implementare disposizione o rinomina Windows
    prima di avere chiuso ACL, privilegi e identità degli oggetti?
12. La distinzione fra candidato non certificato e registrazione conclusiva
    consente la prova GitHub sullo SHA esatto senza permettere merge, tag o
    completamento con test essenziali saltati o P2 normativi ancora aperti?
13. Quale requisito essenziale precedentemente concordato è assente o è stato
    indebolito?
14. Esiste una causa alternativa più semplice che spiega i fallimenti ripetuti
    e che il piano non considera?

## 12. Formato richiesto della risposta

La risposta deve contenere:

1. verdetto `APPROVABILE PER LA SOLA RIPRESA DIAGNOSTICA` oppure
   `NON APPROVABILE`;
2. tabella dei rilievi P0-P3 con requisito, file e riga, causa, rischio e
   correzione documentale minima;
3. elenco dei requisiti essenziali eventualmente persi;
4. elenco degli sconfinamenti eventuali dal 2A;
5. giudizio separato su modello D/A, matrice Linux, matrice Windows, caricatori e
   ordine di intervento;
6. risposta alle quattordici domande del §11;
7. dichiarazione finale esplicita: `LA CODIFICA PUÒ RIPRENDERE` oppure
   `LA CODIFICA DEVE RESTARE CONGELATA`.

Il revisore non deve modificare il codice durante questa revisione. Una proposta
di patch può essere descritta soltanto dopo il verdetto e non sostituisce la
dimostrazione della causa.

---

# Revisione adversarial indipendente — deduzioni (25 agosto 2026)

Revisione svolta sul codice, non sul solo documento. Fotografia riprodotta:
comando del §3 rieseguito → `63 passed, 12 skipped`; impronta aggregata dei dieci
file riprodotta esattamente (`75bb104d…`) con
`sha256sum <file…> | sha256sum` nell'ordine del comando; 75 `node-id` raccolti.
Tutti i dodici salti sono limitati a Windows e risiedono in `tests/portable`.

## 1. Verdetto

**NON APPROVABILE**, per un solo rilievo P1 la cui correzione è documentale e
lunga due paragrafi. Con quella correzione il verdetto diventa
`APPROVABILE PER LA SOLA RIPRESA DIAGNOSTICA`.

Va detto in premessa, perché cambia il peso del verdetto: **tutte e otto le
cause R1-R8 sono state verificate vere nel codice**, alla lettera. La diagnosi
non è in discussione; il rilievo riguarda l'ordine di lavoro, non l'analisi.

Verifiche puntuali delle cause dichiarate:

| Causa | Riscontro nel codice |
|---|---|
| R1 | `_AuthenticatedRootDescriptor` (`runtime/executor_birth_secure_fs.py:70-85`) ha attributi scrivibili (`handles`, `root_path`, `identity`, `_adopted`); il sigillo è il globale di modulo `_DESCRIPTOR_TOKEN` (`:2059`). Confermata. |
| R2 | `create_file_exclusive` (`:1707…`) verifica solo `self._authoritative` e non il blocco; `_holds_global_lock()` (`:1069-1071`) non distingue condiviso ed esclusivo ed è invocato **soltanto** da `executor_birth_keystore.py:247`. Confermata e più netta di come è scritta. |
| R3 | nessun metodo di disposizione nella sessione (elenco metodi `:1001-1993`). Confermata. |
| R4 | `_posix_lock` sincronizza la directory solo se `created` è vero (`:1489-1492`); il ramo di recupero del file vuoto preesistente non lo fa. Confermata. |
| R5 | radice verificata per tipo, reparse e percorso finale (`_verify_win_object`, `:655`), senza confronto col profilo del proprietario. Confermata. |
| R6 | rami distinti in `_rename_no_replace_windows`; nessun confronto di profilo prima della rinomina. Confermata. |
| R7 | `_win_inventory` (`:2249-2294`) costruisce `_InventoryEntry(..., 1)` e non legge il tag reparse. Confermata. |
| R8 | solo `executor_birth_keystore.py` possiede un'entrata interna in sessione; `executor_birth_semantic_authority.py:113-160` usa doppio inventario senza blocco; `executor_birth_approval_authority.py:63` legge tramite `_read_path_once`. Confermata. |

## 2. Rilievi

| ID | Requisito | File e riga | Causa | Rischio | Correzione documentale minima |
|---|---|---|---|---|---|
| P1-a | §9 passo 7; §6 R5-R7 | `.github/workflows/portable-contract-store.yml:23-57` | L'oracolo preteso da R5 richiede token reali, un secondo utente e un processo non elevato; la matrice pubblica non crea alcuna identità aggiuntiva e non prevede un'attività elevata/non elevata. Il §9 avvia il lavoro Windows senza pretendere prima l'esistenza di quell'attività. | È esattamente la condizione che ha già prodotto correzioni a tentativi: si modifica codice Windows senza un giudice indipendente che possa dichiararlo sbagliato. | Inserire nel §9 un passo 6-bis: «predisporre nella matrice pubblica l'attività Windows con seconda identità locale, processo non elevato e verifica dei diritti effettivi; l'attività è bloccante». Vincolare il passo 7 alla sua esistenza e dichiarare che nessuna correzione R5-R7 è ammessa prima. |
| P2-a | §4 confine 2A | `runtime/executor_birth_secure_fs.py:690, 754, 821-958, 2064-2114, 2151, 2249` | Il §4 elenca sei capacità; il prototipo ne implementa altre cinque già pretese dal §8: validazione del volume, ripristino di privilegio, generazione e verifica del descrittore, autenticazione e adozione della radice, inventario. | Un confine che non nomina la superficie consegnata non può rilevare uno sconfinamento, né in questa direzione né nella prossima. | Allineare l'elenco «l'incremento 2A deve» alla superficie effettiva, aggiungendo le cinque capacità e dichiarando che restano di basso livello e prive di politica. |
| P2-b | §6 R7; §8.1 | `runtime/executor_birth_secure_fs.py:43-49, 2151-2180, 2249-2294` | Il difetto d'inventario non è solo Windows: il record comune `_InventoryEntry` non ha campo per il tag reparse né per la distinzione del collegamento simbolico, e su POSIX un collegamento simbolico entra con `directory=False`, indistinguibile da un file regolare. | Correggere il solo lato Windows lascia il difetto sull'altro e obbliga a un secondo cambio del record comune. | Riscrivere R7 come «inventario comune incompleto», con due riproduttori, uno per piattaforma, e dichiarare che la correzione cambia il record condiviso. |
| P3-a | §3; §5 manifesto | documento, §3 | L'impronta dichiarata «dell'elenco ordinato» corrisponde in realtà all'ordine di raccolta: l'elenco ordinato produce `6fbb168d…`, la raccolta produce il valore pubblicato `02f8a7ed…`. Il metodo dell'impronta aggregata non è dichiarato. | Un manifesto canonico con una regola d'ordine sbagliata non è riproducibile e fallisce alla prima verifica indipendente. | Sostituire «elenco ordinato» con «elenco nell'ordine di raccolta pytest» e dichiarare il metodo dell'impronta aggregata. |
| P3-b | §5 modello D/A | `tests/runtime/contracts/test_executor_birth_secure_fs.py:120-129` | `test_shared_lock_blocks_exclusive_with_a_bounded_deadline` usa due sessioni nello stesso processo: prova il conflitto fra descrizioni di file aperte, non la contesa multiprocesso di C4. | Il nome promette più della garanzia; è la forma di falso verde che il §5 vuole impedire. | Estendere la disciplina del §5 ai nomi delle prove già esistenti e rinominare o qualificare questo caso prima di scrivere C4. |

## 3. Requisiti essenziali persi

Nessuno. Il confronto con `internal/reports/rm0008-gruppo2-analisi-implementazione.md`
(§§7.5, 16.3-16.4) e con i §§23.6-23.8 della roadmap non mostra requisiti
normativi assenti dal pacchetto. Il §4 li circoscrive in modo più stretto del
dovuto (P2-a), ma non li elimina.

## 4. Sconfinamenti dal 2A

Nessuno riscontrato. I quattro moduli non contengono `transaction-v1.json`,
checkpoint, `set.json`, marcatori né stato di recupero del predispositore; la
sessione non espone alcuna operazione di disposizione. La lettura del registro
approvazioni, che sembra basata su percorso, passa in realtà per la facciata
storica a handle (`_read_path_once` → `_open_legacy_root_session`,
`runtime/executor_birth_secure_fs.py:2116-2126`): è compatibilità censita, non
uno sconfinamento.

## 5. Giudizi separati

- **Modello D/A:** corretto e sufficiente. La regola «un D può essere verde
  perché ha osservato il difetto» è l'unica che evita la confusione ricorrente
  fra riproduttore e conformità. Unico difetto: non è applicata ai nomi delle
  prove già scritte (P3-b).
- **Matrice Linux:** adeguata e onesta. I quattro file di prova causali sono
  interamente eseguibili su POSIX e non producono salti; il tempo di esecuzione
  ridotto non è un indizio di prove finte, perché le prove esercitano davvero
  blocchi, rinomine, sostituzioni e scritture parziali.
- **Matrice Windows:** insufficiente per le cause dichiarate. I dodici casi
  Windows risiedono in `tests/portable` e vengono quindi eseguiti dall'attività
  pubblica su `windows-2022`; manca però qualunque identità aggiuntiva, e senza
  di essa R5, R6 e R7 non possono essere giudicati. È il P1.
- **Caricatori:** la disciplina è oggi eterogenea in modo non dichiarato:
  entrata interna con controllo del globale per il solo archivio chiavi,
  rilevazione per doppio inventario per l'autorità semantica, lettura semplice
  per le approvazioni. R8 lo cattura; il §4 no.
- **Ordine di intervento:** corretto nella sostanza — blocco multiprocesso e
  vincolo degli handle prima del recupero, ACL e identità prima di rinomina e
  disposizione — e sbagliato in un punto solo: il passo 7 comincia senza
  pretendere l'attività che deve giudicarlo.

## 6. Risposte alle quattordici domande

1. **Sì**, con la riserva del P2-a: il confine è corretto ma dichiarato più
   stretto della superficie consegnata. Nessun dato, checkpoint o politica del
   2B è presente nel codice.
2. **Promette più di quanto il confine consenta**, ed è giusto che il §6 R1 lo
   dica: sigillo e descrittore sono raggiungibili e modificabili nello stesso
   interprete. La proprietà ottenibile è strutturale (entrata unica, descrittore
   consumabile una volta, assenza transitiva di fabbriche di scrittura, guardia
   statica), non una garanzia contro codice arbitrario nello stesso UID o SID.
   Il limite va tenuto scritto dov'è.
3. **No, oggi.** Nessuna mutazione controlla il blocco; l'unico controllo
   esistente sta fuori dalla primitiva e non distingue la modalità. R2 lo
   richiede correttamente per il futuro.
4. **Sì**, la separazione è sufficiente per le prove nuove. Non lo è per quelle
   già scritte (P3-b).
5. **No, non ancora**, ed è dichiarato: due caricatori su tre non hanno entrata
   interna e non acquisiscono il globale. R8 chiede la contesa per tutti e tre e
   il blocco locale per il solo archivio chiavi: è la lettura corretta del §7.5
   della specifica di gruppo.
6. **No.** Sono indipendenti dal generatore SDDL solo se l'oracolo usa identità
   reali, e quelle identità oggi non esistono in nessuna attività. Vedi P1-a.
7. **Sì, sulla carta.** La finestra fra `CREATE_NEW` più ACL più `LockFileEx` e
   la prima `WriteFile` è quella giusta, e corrisponde al ramo POSIX osservato
   in `_posix_lock`. Resta da dimostrare che la barriera sia collocabile senza
   punti d'arresto scelti dal chiamante, come il §7 stesso impone.
8. **Sì per C2 e C3** (radice, ogni intermedio e oggetto finale, su entrambe le
   piattaforme). **Sì per C4**, che è l'unico modo di provare ciò che la prova
   in-processo odierna non prova.
9. **Sì.** La distinzione fra ramo con directory memorizzata e sorgente non
   memorizzata è quella osservata nel codice, e la riconciliazione dopo
   conflitto è richiesta per entrambi.
10. **No, non tutti**: volume, ABI, byte binari e percorsi hanno piattaforma e
    oracolo; ACL, privilegi e proprietario errato hanno l'oracolo descritto ma
    non la piattaforma su cui eseguirlo (P1-a); inventario ha oracolo e
    piattaforma ma un record comune inadeguato (P2-b).
11. **Sì**, l'ordine è corretto: ACL, privilegi e identità precedono rinomina e
    disposizione. È l'unico punto in cui il piano protegge davvero dal ciclo di
    correzioni a tentativi, e va conservato.
12. **Sì.** Candidato non certificato con marcatore, nessun merge, nessun tag e
    matrice legata allo SHA esatto è il minimo corretto per far girare la prova
    pubblica senza dichiarare completato il 2A.
13. **Nessuno perso.** Vedi §3.
14. **Sì, e il piano non la nomina.** La spiegazione più semplice dei
    fallimenti Windows ripetuti non è la complessità delle singole cause: è che
    nessuna correzione Windows è mai stata giudicata da un'esecuzione capace di
    smentirla. Le prove ACL, privilegi e identità non hanno mai avuto una
    seconda identità né un processo non elevato su cui girare. Finché quella
    condizione resta, ogni correzione è una congettura verificata dal suo stesso
    autore. È il motivo per cui il P1-a precede tutto il resto.

## 7. Dichiarazione finale

**LA CODIFICA DEVE RESTARE CONGELATA** fino all'applicazione delle correzioni
documentali P1-a, P2-a, P2-b, P3-a e P3-b. Sono correzioni di testo: non
richiedono altra analisi, non toccano il prodotto e non riaprono la diagnosi.
Applicate quelle, la ripresa diagnostica è approvabile nell'ordine proposto dal
§9 con il passo 6-bis inserito.

## 8. Riscontro alle condizioni della revisione

Le cinque condizioni sono state applicate senza modificare il prodotto:

| Rilievo | Riscontro |
|---|---|
| P1-a | Il §9 richiede ora, prima di R5-R7, un'attività Windows pubblica e bloccante con seconda identità locale, processo non elevato e oracolo indipendente dei diritti effettivi. |
| P2-a | Il §4 e il §10.1 della specifica elencano autenticazione e adozione della radice, controllo del volume, sicurezza e privilegio Windows e inventario comune come capacità 2A di basso livello e prive di politica. |
| P2-b | R7 è ora «inventario comune incompleto», con riproduttori distinti POSIX e Windows e correzione del record condiviso. |
| P3-a | L'impronta usa esplicitamente l'ordine di raccolta pytest; il metodo dell'impronta aggregata dei file è dichiarato. |
| P3-b | La prova preesistente è stata rinominata `test_in_process_file_descriptions_enforce_shared_exclusive_conflict`, senza rivendicare contesa multiprocesso. |

Dopo la sola rinomina, la fotografia è stata riprodotta con Python 3.12.3:
`63 passed, 12 skipped`; 75 `node-id`; impronta della raccolta
`73c821eda82ee6908a3b20c8c06076bf2edc0d96d0a548d9bd4cfe83b204826c`;
impronta aggregata dei dieci file
`8069ebdd5f6bb6c7b110277c00a6a302d4d241c6d7a605dc50c8f8083c8c113b`.

Si applica pertanto la condizione espressa dal verdetto: il piano è approvato
per la sola ripresa diagnostica. La prima attività consentita è predisporre
l'oracolo Windows bloccante. Nessuna correzione di prodotto R5-R7 è autorizzata
prima che tale attività esista e dimostri di poter respingere configurazioni
negative.

## 9. Stato della prima attività autorizzata

La barriera richiesta dal P1-a è stata predisposta senza modificare il prodotto.
È inserita come step obbligatorio del controllo storico
`Python 3.12 / windows-2022`, così una configurazione esterna che richieda quel
controllo non può ignorare la calibrazione. Il codice isolato in
`tests/windows_identity` non importa il runtime Metnos.

La calibrazione crea un'identità di servizio e un secondo utente locale reali.
Ogni processo nasce sospeso e il suo token viene autenticato prima
dell'esecuzione; l'oracolo strutturale usa API Win32 indipendenti, mentre
l'oracolo effettivo usa aperture, letture ed enumerazioni reali. Tre sentinelle
negative coprono proprietario errato, lettore estraneo su `confidential` e
diritto di scrittura del servizio. I casi complessivi sono undici, privi di
salti e di esiti attesi invertiti. Password, processi, handle, privilegi,
directory e account hanno pulizia esplicita e il fallimento della pulizia rende
rosso il controllo.

La revisione statica indipendente dell'implementazione non ha rilevato P0-P3.
Non essendo disponibile localmente Windows, questa non è ancora evidenza
dinamica: R5-R7 restano congelati fino al verde pubblico sullo SHA esatto della
candidata.

## 10. Prima esecuzione pubblica e causa osservata

La candidata `d677347c50a78c1d5c3f9a75df865407ab0aa460` corrisponde
all'esecuzione `32902651884`. Linux è verde. Windows Server 2022 ha confermato
NTFS, poi la calibrazione si è arrestata prima di account e ACL con
`GetTokenInformation(size): WinError 24` sulla classe `TokenElevation`.

La causa è delimitata nel wrapper: la richiesta preliminare con buffer nullo e
lunghezza zero era applicata indiscriminatamente sia alle informazioni con SID
variabile sia alle strutture di quattro byte. La correzione passa buffer e
lunghezza esatti per `TokenElevation` e `TokenElevationType`, verifica
`ReturnLength` e lascia invariato il doppio passaggio per `TokenUser` e
`TokenIntegrityLevel`. Nessun codice di prodotto è stato modificato. Il nuovo
esito pubblico resta necessario prima di autorizzare R5-R7.
