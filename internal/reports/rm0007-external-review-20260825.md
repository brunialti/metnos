# RM-0007 — Controrevisione esterna del 2026-08-25

> Rapporto storico non normativo estratto dalla roadmap il 2026-08-25.
> La specifica vigente è `internal/roadmap/RM-0007-pubblicazione-verificata-contratti.md`;
> in caso di differenza prevale sempre la roadmap.

## 17. Controrevisione esterna — verdetto e risposte al mandato

> **Rapporto storico non normativo.** Questa sezione conserva il testo ricevuto
> per tracciabilità; in caso di differenza prevalgono sempre i §§1-14. Tutti i
> rilievi bloccanti sono risolti: B1 in §§3.4, 5.1, 9 e 10; B2 in §4.4 e M4;
> B3 in §§4.2 e 6; B4 in §§5.3, 8, M4 e 14. Roberto ha autorizzato la modifica
> coordinata di `CLAUDE.md`. H1 è in §§3.3, 6.1 e 10.3; H2 resta un hardening
> non bloccante; H3 è nel gate iniziale di §9.

> Prodotta il 2026-08-25 in sola lettura, in risposta a §15, dallo stesso
> revisore che ha condotto il secondo giro avversariale su RM-0002. Consultati
> senza modificarli: ADR 0223, `internal/design/TODO.md`
> (`EXEC-BIND-001`, `AFF-I18N-001`, `PUB-001`) e il rapporto di audit
> `internal/reports/rm0002-linter-manifest-multilingue-audit-20260824.md`.
> Nessun file di prodotto, manifest, firma o stato è stato modificato.

### 17.1 Verdetto

**La riduzione di perimetro è giusta e la garanzia ristretta di §1 chiude i
difetti che bloccano RM-0002 L5.** Il confronto è verificabile: gli otto difetti
che l'audit del 24 agosto aveva confermato contro il codice — base non
verificata prima della rifirma, digest ricalcolato, tre file scritti
separatamente, byte diversi fra verifica e uso, candidato applicato a una base
cambiata, percorso del registro usato come destinazione, secondo scrittore
legacy, validatore finto nelle prove — hanno tutti un punto corrispondente in
§3.4, §5, §6 o §8. Nessuno resta scoperto.

Il verdetto è **approvabile con quattro rilievi bloccanti**, tutti di
specifica e tutti economici: nessuno richiede di cambiare il disegno, tre
richiedono di scrivere un'invariante che oggi è implicita e uno richiede una
decisione di Roberto su un file invariante.

Classificazione completa: **4 bloccanti, 3 rischi accettabili (quantificati),
3 elementi di irrobustimento futuro, 0 preferenze stilistiche**.

### 17.2 Rilievi bloccanti

#### B1 — Il digest del codice non può essere calcolato dalla directory della generazione

**Dato misurato:** **21 contratti builtin su 21** dichiarano `code.files` con
percorsi che escono dalla directory del manifest — `../../system/admin.py`,
`../../classify_entries.py`, `../../recurring_tasks.py` e così via. Non è
un'eccezione: è la forma normale di quella classe. E
`sign.compute_code_digest()` (`runtime/sign.py:96-104`) fa `manifest_dir / fname`
**senza normalizzare e senza controllo di contenimento**, il che oggi è
intenzionale e funziona.

Dopo il cutover `manifest_dir` non è più la directory sorgente: è la
generazione dentro `PATH_USER_STATE`. Risolvere lì `../../classify_entries.py`
esce dal deposito e cade in una directory arbitraria dello stato utente.
L'implementazione che «sposta il contratto e riusa la funzione di digest»
produce, a seconda di cosa trova, un errore di file assente oppure il digest di
un file sbagliato.

§5.1 punto 6 dice già la cosa giusta — «calcola il digest attraverso le radici
ammesse associate a `ContractId`» — ma è una riga sola dentro un elenco di
otto, e questo è precisamente il dettaglio che si perde in implementazione.

**Richiesta:** promuoverlo a invariante di §3.4 («il digest del codice si
calcola sempre dalla radice sorgente dichiarata del contratto, mai dalla
directory della generazione»), aggiungere la prova corrispondente in §10.1 e
citare esplicitamente il caso `../../` fra le fixture di M0.

#### B2 — Il selettore canonico non è definito, e finisce dentro l'hash della generazione

§4.3 richiede che lo stato conservi provenienza e hash «per selettore
canonico», ma non dice **quale** delle due forme oggi in uso sia quella
canonica:

- `args.<nome>.description` — la forma scritta nei companion su disco,
  verificata su `executors/find_files/manifest.lang_state.json`;
- `args.properties.<nome>.description` — la forma prodotta da
  `i18n_materializer.iter_localized_text_tables()` e usata da
  `i18n_pipeline._update_state()`.

È il rilievo ADV-009 dell'audit, confermato e oggi latente soltanto perché
nessuna terza lingua è mai stata promossa. RM-0007 non lo risolve: lo
**incapsula**. Dal momento in cui `manifest.lang_state.json` entra nella
generazione, il selettore fa parte dell'identità immutabile, e due forme dello
stesso testo producono due generazioni diverse per lo stesso contenuto.

**Richiesta:** scegliere la forma canonica in §4.3, dichiarare la migrazione
una-tantum dei companion esistenti come consegna di M4, e aggiungere a §10.1 la
prova che un selettore nella forma non canonica viene **rifiutato**, non
silenziosamente accettato.

#### B3 — Il retry dopo un arresto fra il passo 18 e il 21 non è idempotente su Windows

§6 dichiara che «un errore dopo il punto 21 non annulla una pubblicazione già
visibile: il retry la riconosce». La finestra scoperta è **prima** del 21.

Il passo 18 rinomina la directory temporanea in `generations/<generation-id>`.
Se il processo termina fra il 18 e il 21, `current` indica ancora la vecchia
generazione — corretto — ma la directory della nuova esiste già. Al retry, con
la stessa base e le stesse patch, il publisher ricostruisce **lo stesso**
identificatore e riprova il passo 18 su una destinazione che esiste. Su Linux
`os.replace` di una directory su una directory non vuota fallisce; su Windows
la sostituzione di una directory esistente non è ammessa affatto.

Il caso non è raro: è esattamente la finestra che §10.3 punto 2 chiede di
provare («dopo il rename della generazione»). La prova è prevista, il
comportamento no.

**Richiesta:** aggiungere a §6 un ramo esplicito fra il 17 e il 18 — se
`generations/<generation-id>` esiste già, verificarla integralmente; se
coincide, saltare al passo 20 e proseguire; se differisce, è la corruzione
descritta in §4.2 e il contratto si blocca. È l'unico punto in cui l'algoritmo,
così come è scritto, non fa quello che il documento promette.

#### B4 — Dopo il cutover, §7.10 di `CLAUDE.md` diventa falso

`CLAUDE.md` §7.10 è una regola **invariante** e dice: modifichi
`<executor>.py` o il solo `manifest.toml`, firmi con
`python3 runtime/sign.py sign executors/<name>`, riavvii, committi manifest e
firma insieme — «senza firma il loader scarta l'executor in silenzio».

Dopo il cutover quella sequenza non rende più viva la modifica: il loader legge
`current`, e la sorgente firmata resta una sorgente di authoring finché non
passa da `publish_signed_source()`. Il modo di sbagliare è **lo stesso** che
quella regola esiste per prevenire, con un nome nuovo: non più «senza firma
sparisce in silenzio», ma «senza pubblicazione la modifica non ha effetto in
silenzio». Chi sviluppa cambierà il codice, firmerà, riavvierà, e vedrà il
comportamento vecchio senza un solo messaggio d'errore.

§8 elenca otto file da modificare e non cita `CLAUDE.md`; M4 elenca sei
consegne e non cita il flusso di lavoro quotidiano.

**Richiesta:** aggiungere a M4 la consegna «aggiornare il flusso di authoring
documentato» e **portare a Roberto la modifica di §7.10**, che è invariante e
non si tocca di iniziativa. Fino ad allora il cutover non è completo, qualunque
prova verde dia il catalogo.

### 17.3 Rischi accettabili, con i numeri

Tre righe della tabella §11 si possono chiudere con una misura invece che con
un giudizio. Misurato sul catalogo reale di 122 contratti, il 25 agosto 2026:

| Voce | Misura | Conseguenza |
|---|---|---|
| peso di una generazione | **7,5 KB** in media (manifest + firma + stato) | tradurre l'intero catalogo in una lingua nuova costa **0,9 MB** di generazioni |
| costo dell'identificatore | **3,1 ms** per calcolare il digest delle generazioni di tutti e 122 i contratti | irrilevante all'avvio, dove la verifica delle firme e dei digest del codice già domina |
| finestra verifica→invocazione | invariata rispetto a oggi | RM-0007 non la peggiora e non la chiude; `EXEC-BIND-001` la possiede |

Conseguenza sulla tabella §11: la riga «crescita dello spazio: media» va
abbassata a **bassa**, e la scelta di non fare raccolta automatica in v1 è
corretta — a 7,5 KB per generazione, la raccolta costerebbe più rischio di
quanto risparmi spazio. La riga «codice cambia dopo la verifica: alta,
residua» resta com'è: è dichiarata onestamente in §3.3 ed è la sola cosa che
rende credibile la qualificazione KISS.

### 17.4 Irrobustimento futuro, non bloccante

**H1 — nominare i due modi di guasto Windows che «prova reale NTFS» non cattura
da sola.** §10.3 chiede la prova su NTFS reale, il che è giusto ma generico. I
due meccanismi che vale la pena provare per nome sono: (a) la sostituzione di
`current` con `os.replace()` fallisce con violazione di condivisione se un
lettore tiene quel file aperto senza condivisione in cancellazione — la
mitigazione è che il lettore apra, legga e chiuda subito, come §7.1 punto 1 già
prescrive, e che il publisher riprovi con attesa finita; (b) la sincronizzazione
di una directory non esiste su Windows, quindi la garanzia dei passi 19 e 22 è
strutturalmente più debole lì. §10.3 chiede già di distinguere crash del
processo da perdita di alimentazione: quella distinzione va scritta anche in
§3.3, dove il limite è dichiarato, non solo fra le prove.

**H2 — dare un tetto d'allarme alla diagnostica degli orfani.** §7.3 fa bene a
segnalare e non cancellare, ma un rapporto che nessuno legge non è un
controllo. Basta una soglia oltre la quale la diagnostica diventa un avviso
operativo.

**H3 — chiudere il ciclo con RM-0002 per iscritto.** Il TODO lo coordina già
(«L2 produce l'inventario neutro che RM-0007 consuma, L5 richiede il confine di
pubblicazione già in servizio») e §8 assegna correttamente la proprietà di
`manifest_inventory.py` a RM-0002. Manca la conseguenza operativa: **M0 di
RM-0007 non può iniziare prima che L2 di RM-0002 sia consegnata**, altrimenti
`ContractId` nasce dentro RM-0007 e la dipendenza si inverte. Una riga in §9.

### 17.5 Risposte alle quattordici domande di §15

1. **Sì**, risolti B1-B4. Gli otto difetti confermati dall'audit hanno tutti un
   punto corrispondente nella specifica.
2. **Nessuno**, per i difetti in perimetro. Copiare il codice servirebbe solo a
   chiudere la finestra verifica→invocazione, e i 21 builtin con `../../`
   dimostrano proprio che i file dichiarati **non sono** la chiusura delle
   dipendenze: copiarli darebbe una falsa garanzia. È `EXEC-BIND-001`.
3. **Deve stare nella generazione.** Ricostruirlo richiederebbe `source_lang` e
   `source_hash`, che non sono derivabili dal manifest pubblicato: si perderebbe
   la provenienza e la riconciliazione post-commit smetterebbe di essere
   idempotente, costringendo a introdurre la ricevuta persistente che §13
   rinvia.
4. **No, non in v1.** La firma del manifest autentica già ciò che decide il
   caricamento; l'hash della generazione impedisce di mescolare i tre file per
   errore. Una seconda firma proteggerebbe l'associazione fra i tre file da un
   avversario **attivo** che ha già i permessi dell'utente Metnos — cioè lo
   scenario che §3.2 esclude dichiaratamente. Aggiungerla senza cambiare quel
   confine sarebbe costo senza garanzia nuova.
5. **Sì, sono due guasti distinti e servono entrambi.** Il lock impedisce a due
   scritture di interlacciarsi. `expected_generation_id` impedisce di applicare
   un candidato preparato **prima** dell'acquisizione e ormai vecchio: il lock
   non può vederlo, perché quando lo si ottiene la base è già cambiata. Toglierne
   uno lascia scoperto l'altro caso.
6. **Sì per gli scrittori cooperanti**, che è il perimetro dichiarato, con due
   avvertenze: il blocco è per handle e per intervallo di byte, quindi va preso
   su `writer.lock` e mai sul puntatore; ed è mandatorio, quindi nessun lettore
   deve mai aprire il file di lock, neanche in diagnostica.
7. **Sì per l'arresto del processo, no per la perdita di alimentazione su
   Windows**, dove la sincronizzazione di directory non esiste. La distinzione
   è già richiesta in §10.3 e va dichiarata anche in §3.3 (vedi H1).
8. **Sì, ed è idempotente proprio grazie a §4.3.** Gli hash dello stato
   linguistico rendono la riconciliazione una funzione dello stato osservato,
   non della memoria di un processo morto. Senza quegli hash servirebbe la
   ricevuta persistente.
9. **Sì, a condizione che B4 diventi una consegna di M4** e che la guardia
   statica di §7.2 vieti la lettura viva dalla sorgente per i contratti
   migrati. Il divieto mirato è la scelta giusta: vietare genericamente le
   scritture bloccherebbe installer e generatori.
10. **Sì per installer, Synt e importatori**, che producono directory nuove e
    possono lavorare fuori dal lock. Il caso scoperto è **chi modifica in posto
    una sorgente già ammessa**: §6.2 impone a costoro di acquisire il lock, ma
    §14 — l'elenco che l'implementatore legge davvero — non lo dice. Va
    aggiunto lì.
11. **Sì, e i dati lo dimostrano.** 21 builtin su 21 usano `../../`: essendo la
    forma universale di quella classe, la regola generale «i file di codice
    risolvono dentro la radice sorgente dichiarata della classe del contratto»
    non ha bisogno di nominare nessun executor. È esattamente B1.
12. **Nulla.** Il perimetro è già al minimo: togliere il lock o la generazione
    attesa scopre un guasto distinto (domanda 5), togliere lo stato dalla
    generazione rompe l'idempotenza (domanda 3), togliere il puntatore riporta
    al problema dei tre file separati.
13. **Nessun elemento rinviato è indispensabile alla v1.** Il più vicino al
    confine è la raccolta delle generazioni, ed è fuori a ragione: 7,5 KB per
    generazione non giustificano il rischio di cancellare la cosa sbagliata.
14. **Non ne vedo una.** SQLite come puntatore sarebbe più rapido da scrivere,
    ma introduce un'autorità nuova su un formato pubblico orientato ai file e
    sposta la durabilità dentro un motore che va comunque sincronizzato. Un file
    di una riga sostituito con `os.replace()` è la primitiva più piccola che dia
    al lettore uno stato completo senza obbligarlo a prendere un lock.

### 17.6 Stima separata, come richiesto da §15

La stima ritirata di 7-12 giorni si riferiva a un perimetro diverso. Questa
vale per il perimetro KISS, a valle di L2 di RM-0002 e con B1-B4 risolti nella
specifica prima di iniziare.

| Blocco | Stima | Dove sta il rischio |
|---|---|---|
| nucleo: M1 + M2 + M3 | **6-9 giorni** | quasi tutto in M3: patch, confronto strutturale e digest conservato |
| migrazione e cutover: M4 | **4-6 giorni** | è il blocco più rischioso, perché tocca il loader **e** il modo di lavorare quotidiano (B4) |
| certificazione Windows: §10.3 su NTFS reale | **2-4 giorni** | dipende dall'accesso alla macchina: `ssh` e `ping` verso il PC sono chiusi e si diagnostica solo attraverso Metnos, il che va messo a preventivo e non scoperto in corsa |
| **totale** | **12-19 giorni** | M0 non è contato: appartiene a RM-0002 L2 |

La riga della certificazione Windows è quella che più facilmente viene
sottostimata: non è tempo di scrittura, è tempo di andata e ritorno su una
macchina che non si interroga direttamente.

### 17.7 Raccomandazione

Promuovere RM-0007 a `ready` **dopo** aver scritto B1, B2 e B3 nella specifica —
sono tre paragrafi, non tre progetti — e **dopo** che Roberto abbia deciso su
B4, che è l'unico punto che tocca un file invariante e il modo di lavorare di
tutti i giorni.

Non vedo motivi per riaprire il disegno. La versione KISS è più piccola della
prima, chiude gli stessi difetti, e la parte che ha rinviato l'ha rinviata
dicendo perché.

### 17.8 Esito dell'integrazione

La raccomandazione storica di §17.7 è soddisfatta. La specifica normativa:

- separa base sorgente, radici ammesse e directory della generazione;
- definisce selettore e byte-state canonici con migrazione validata;
- definisce riuso, corruzione, durability barrier e retry per postcondizione;
- coordina comando operativo, cutover globale e modifica di `CLAUDE.md`;
- conserva traduzioni e provenienza attraverso i publish tecnici successivi;
- rende espliciti lock Windows, pointer replace, limiti power-loss e prove
  NTFS reali.

RM-0007 è pertanto `ready`. Lo sviluppo segue M0-M4 senza riaprire il perimetro
salvo che una prova dimostri falsa una delle invarianti di §3.4.
