# RM-0008 — Diagnostica causale dell'incremento 2A

## 1. Scopo e vincolo d'uso

Questo rapporto congela i riproduttori D richiesti dal §16 della specifica di
implementazione prima di modificare il comportamento del prodotto. Un D verde
significa che il difetto o la lacuna sono stati osservati; non è una prova di
conformità e non entra nella matrice obbligatoria. Le future prove A devono
affermare l'invariante opposta attraverso il simbolo produttivo.

La base storica del prototipo è `38368d3d`. Il comportamento osservato non è
identificato soltanto dal commit: ciascun riproduttore verifica prima
dell'importazione le impronte SHA-256 dei file che costituiscono l'ingresso.
In questo modo le modifiche successive al solo apparato diagnostico non possono
cambiare silenziosamente il prodotto sottoposto alla prova.

| File | SHA-256 |
|---|---|
| `runtime/executor_birth_secure_fs.py` | `fd194b9c89a57ef94ddd2de0fb79717f488cffeafee3f26d1d0f4f4d3930a7d8` |
| `runtime/executor_birth_keystore.py` | `5e52dbe2508d4a379c08bc89f37e602279c51167c095b7cf6284abcc5f3b19a3` |
| `runtime/executor_birth_approval_authority.py` | `65fefed1891bdcbb7601859c6085402b109ebef58a25ca15cf97b9bb2d224974` |
| `runtime/executor_birth_semantic_authority.py` | `d130d8e4b2207faaad8341c8dec6658a99c5ed2f8231f47a3b91dc8dc8148c6f` |
| `tests/runtime/contracts/test_executor_birth_secure_fs.py` | `790b62c6854ded6c2205bf08100a3b00cddb29569859ce2cf3e9be0a8a92b25b` |
| `tests/portable/test_executor_birth_secure_fs_native.py` | `d70d8a26dadb73c0751559e90eaa955d3da3b8cfdd068ce0f10b094a4aa10d8f` |
| `tests/runtime/contracts/test_executor_birth_keystore.py` | `71f4316eabe65ef4dc2b80887344bc6e5889a03b192934e129e1dee2c28233a8` |
| `tests/runtime/contracts/test_executor_birth_approval_authority.py` | `1131b3bc6a616588bedbbaa765f00d6b0b929afc8c2e99a85f9219658a9de963` |
| `tests/runtime/contracts/test_executor_birth_semantic_authority.py` | `58793cc9b73854cdaaa90cc84600069e84516e06861878a32fa20aeaf11cdd19` |

Il riproduttore eseguibile è
`internal/tools/rm0008_increment_2a_diagnostics.py`. Non è raccolto da pytest e
non deve essere trasformato in un controllo obbligatorio: conserva la fotografia
del difetto, mentre gli A sopravvivono alla correzione.
Il riproduttore verifica autonomamente tutte le impronte d'ingresso prima di
eseguire. L'esecuzione con Python 3.12 osserva: inventario statico completo dei
siti R1-R3; R1 costruibile e modificabile; tutte le mutazioni R2 presenti senza
globale e sotto globale condiviso; R4 senza sincronizzazione della directory padre;
R7-POSIX con tipo perso; R3 e le due entrate R8 assenti; assenza delle contese e
barriere C1-C4 nei file di prova congelati.

### 1.1 Evidenze di esecuzione consolidate

La raccolta D ha raggiunto zero errori il 26 agosto 2026. Il risultato indica
che tutti i difetti storici descritti dai riproduttori sono stati osservati in
modo deterministico; non indica che il prodotto sia stato corretto.

- Il riproduttore locale
  `internal/tools/rm0008_increment_2a_diagnostics.py` ha restituito codice zero,
  ha verificato tutte le nove impronte della tabella e ha prodotto le
  osservazioni D-R1, D-R2, D-R3, D-R4, D-R7-POSIX, D-R8 e D-C1-D-C4.
- La suite portabile locale ha concluso con 99 prove superate e 25 esclusioni
  previste.
- Il commit pubblico diagnostico è `24578c8`; deriva dal commit interno
  `887790e1`. Il controllo di esportazione ha rilevato zero dati personali,
  zero segreti e zero file sensibili.
- Il workflow ordinario pubblico
  [32911521703](https://github.com/brunialti/metnos/actions/runs/32911521703)
  è verde su `ubuntu-24.04` e `windows-2022`.
- Il workflow diagnostico esplicito
  [32911765834](https://github.com/brunialti/metnos/actions/runs/32911765834)
  è verde su entrambi i sistemi. Il job Windows ha superato 11 prove di
  identità e diagnostica in 6,23 secondi, quindi 111 prove portabili con sei
  esclusioni previste.

Il job Windows ha prodotto sei osservazioni dinamiche, senza sostituire il
codice di prodotto con una simulazione:

| Criterio | Evidenza prodotta sul prototipo congelato |
|---|---|
| D-R5-root | Sei mutazioni indipendenti — proprietario, protezione, ordine ACE, tipo ACE, SID e maschera — sono state rifiutate dall'oracolo e accettate dall'uso produttivo della radice. |
| D-R5-product-self-check | Il prodotto ha rifiutato il lock vuoto appena creato; l'oracolo indipendente ha accettato esattamente il suo profilo ACL. |
| D-R5-catalog | Una directory creata `integrity_only` è stata riaperta come `confidential`; un file creato `confidential` è stato riaperto come `integrity_only`. |
| D-R6-memorizzata | L'handle di ispezione privo di `DELETE` ha causato `ERROR_ACCESS_DENIED` prima della creazione della destinazione. |
| D-R6-nuova | Una ACL sorgente alterata e rifiutata dall'oracolo ha raggiunto `SetFileInformationByHandle` senza verifica del profilo; la chiamata ha restituito `ERROR_INVALID_PARAMETER` e ha lasciato invariati sorgente e destinazione. |
| D-R7-Windows | Fra due enumerazioni il numero di collegamenti è passato da due a uno ed è stato accettato; la junction aveva un tag indipendente non nullo, ma il record produttivo non possedeva `reparse_tag`. |

La sonda D-R6-nuova dimostra soltanto l'assenza della verifica ACL prima
dell'I/O. Non attribuisce `ERROR_INVALID_PARAMETER` alla dimensione del buffer
di rinomina: tale ipotesi non è stata dimostrata dal controllo indipendente ed è
stata ritirata. G10 resta quindi una copertura ABI da rendere obbligatoria nel
futuro manifesto A, non una causa già provata da D.

## 2. Cause rosse R1-R8

| D | Osservazione deterministica sul prototipo | Causa delimitata | A necessario prima della correzione |
|---|---|---|---|
| D-R1 | Il riproduttore importa `_DESCRIPTOR_TOKEN`, costruisce `_AuthenticatedRootDescriptor` e ne sostituisce `root_path`; `handles` è inoltre una lista mutabile. | Il nome privato Python è trattato come sigillo e lo stato autorevole risiede in attributi modificabili. | Entrata installatore unica, descrittore consumabile una volta e immutabile, guardia AST del grafo di raggiungibilità. |
| D-R2 | Il riproduttore crea directory e file e rinomina il file sia senza blocco globale sia sotto un globale condiviso, confrontando l'inventario prima e dopo. Le mutazioni controllano soltanto `_authoritative`. | La sessione memorizza rango e nome del blocco, ma non la modalità; nessuna mutazione richiede `global/exclusive`. | Rifiuto prima di qualunque I/O per creazione, rinomina e disposizione senza globale esclusivo. |
| D-R3 | `_SecureRootSession` non contiene `dispose_transaction_object`; i rami di errore Windows chiamano direttamente `_win_dispose_created` e sopprimono il suo errore. | Manca una capacità autenticata e riconciliata di disposizione. | Operazione relativa a handle, identità attesa, tipo, link, inventario, globale esclusivo e rilettura post-disposizione su entrambe le piattaforme. |
| D-R4 | Recuperando un `provisioning-v1.lock` vuoto già esistente, la sonda registra `fsync(file)` ma non `fsync(directory)`. | Il secondo `fsync` è condizionato a `created`, non alla transizione durevole vuoto → byte canonico. | Ordine scrittura completa → sincronizzazione file → sincronizzazione directory per ogni transizione da vuoto. |
| D-R5 | Il profilo Windows viene scelto dal booleano o valore letterale del chiamante e memorizzato con `setdefault`; il confronto produttivo converte descrittore atteso e reale in SDDL e confronta le stringhe. `create_directory_exclusive()` non registra il profilo creato, perciò una directory `integrity_only` viene riaperta come `confidential`; la lettura di un file ricava invece il profilo dall'argomento `exact_private`, perciò il test nativo richiede `integrity_only` anche per il file creato `confidential`. | Il verificatore non analizza indipendentemente owner, controllo DACL, tipo, ordine e flag delle ACE, SID e maschera; il profilo storico non è chiuso e il profilo nuovo non viene conservato in modo coerente. | Catalogo chiuso del profilo legato all'identità dell'oggetto; oracolo indipendente già calibrato più A di prodotto per ogni alterazione singola e per gli accessi effettivi delle due identità reali. |
| D-R6-memorizzata | La rinomina riusa `_directories[source]` quando presente; quell'handle è stato aperto da `_directory_chain()` senza `DELETE`. | L'handle di ispezione non possiede il diritto richiesto dalla rinomina. | Handle sorgente distinto con diritti minimi, medesima identità e profilo verificato. |
| D-R6-nuova | Se la sorgente non è memorizzata, la rinomina la apre con `delete=True` e ne verifica forma/percorso, ma non richiama `_verify_windows_profile`. Dopo un errore consulta la destinazione per nome e non riconcilia entrambi gli oggetti per handle. | Il ramo nuovo salta autenticazione ACL; il ramo d'errore non ha una riconciliazione completa. | DACL alterata rossa, barriera sulla destinazione e A che conserva sorgente/destinazione o restituisce conflitto riconciliato. |
| D-R7-POSIX | Il record di un link simbolico e quello di un file regolare hanno entrambi `directory=False`; `_InventoryEntry` non ha tipo esplicito né tag. | Il record comune perde il tipo di oggetto. | Record condiviso con `kind`, identità, link e metadati specifici; riapertura di ogni figlio. |
| D-R7-Windows | `_win_inventory()` forza `links=1` e non trasferisce `ReparsePointTag`; non riapre i figli per confrontare `FileStandardInfo` e `FileAttributeTagInfo`. | L'enumerazione è usata come attestazione completa pur non contenendo i metadati richiesti. | Hard link, junction e mutazione fra enumerazioni devono fallire chiusi. |
| D-R8 | Esiste soltanto `_load_birth_keystore_in_session`; approvazioni e semantica non hanno un'entrata equivalente. La prova del keystore non esercita contesa fra processi. | Due caricatori riaprono ancora radici basate su `Path`; manca la contesa reale globale e locale. | Entrate a sessione per tutti e tre, globale condiviso per tutti e locale condiviso soltanto per keystore, con due processi. |

## 3. Coperture bloccanti C1-C4

| D | Evidenza di assenza | A richiesto |
|---|---|---|
| D-C1 | I test Windows del blocco eseguono creazione e lettura in sequenza; non usano `spawn`, barriera o `TerminateProcess`. | Tre combinazioni condiviso/esclusivo e recupero del file vuoto fra processi Windows reali. |
| D-C2 | Il solo test di junction prepara l'oggetto prima dell'apertura; nessun avversario sostituisce un componente dopo un segnale del processo lettore. | Sostituzione sincronizzata di radice, intermedi e finale con identità/byte indipendenti. |
| D-C3 | `test_root_and_cached_child_replacement_are_detected` muta nello stesso processo dopo la memorizzazione; non inserisce una barriera fra due `openat`. | Avversario POSIX separato per radice, intermedi e finale. |
| D-C4 | `test_in_process_file_descriptions_enforce_shared_exclusive_conflict` dichiara correttamente il solo conflitto fra descrizioni nello stesso processo. | Contesa multiprocesso, scadenza monotona e rilascio dopo terminazione del detentore. |

## 4. Coperture G1-G12

| Criterio | Stato diagnostico e causa della lacuna |
|---|---|
| G1 | Due enumerazioni rilevano alcune mutazioni, ma non esiste limite dell'inventario né riapertura completa dei figli; R7 impedisce un confronto tipizzato. |
| G2 | Il caricatore storico del keystore è coperto su POSIX, ma non sono provati i tre entrypoint reali su Linux e Windows né il proprietario pubblico POSIX differente. |
| G3 | Scrittura corta e un errore `fsync` sono parzialmente coperti; mancano `EINTR`, stato dopo ogni errore, directory padre distinte e rifiuto esplicito di ogni indisponibilità `renameat2`. |
| G4 | Gli errori di sblocco/chiusura non hanno contatori di handle; un errore in `finally` può mascherare l'errore primario e la pulizia Windows sopprime errori di disposizione. |
| G5 | Nessuna prova inietta fallimento o `ERROR_NOT_ALL_ASSIGNED` nel secondo `AdjustTokenPrivileges`; manca il confronto reale dello stato prima/dopo. |
| G6 | Il workflow non contiene ancora un manifesto canonico dei `node-id` A né attività separate per portabilità e concorrenza. |
| G7 | Il volume è controllato nei rami di creazione Windows e del lock creato; manca una A indipendente che inietti un volume non NTFS/privo di ACL persistenti e provi inventario invariato prima di ciascuna creazione. |
| G8 | Il codice confronta `st_uid` quando riceve `expected_uid`; manca una A distinta che alteri proprietario di radice, directory e file autorevoli prima dell'uso. |
| G9 | Nessuna A attraversa nomi lunghi, prefisso `\\?\`, UNC e differenze di maiuscole/minuscole secondo la matrice normativa. |
| G10 | Esiste una prova ABI x64 e seriale high-bit, ma diventerà obbligatoria soltanto nel manifesto A Windows. |
| G11 | La prova portabile usa già `bytes(range(256))` nella primitiva produttiva, ma sul prototipo Windows il ramo è bloccato prima della lettura dal difetto di profilo R5; non costituisce ancora una A verde. |
| G12 | I fallimenti di `SetSecurityInfo` non hanno una A con inventario prima/dopo; la pulizia fallita è soppressa e può lasciare un oggetto parziale. |

## 5. Ordine delle correzioni

La diagnosi conferma l'ordine del §16.8 e non autorizza una correzione cumulativa.
Gli A vengono scritti prima e conservano gli identificativi R/C/G. L'ordine di
chiusura resta: R1-R2; C1-C4; R4-G3-G8-R3-G4 su POSIX; G7-G10-R5-G5-G12-R7-R6-
R3-G9-G11 su Windows; infine R8-G1-G2, arresti reali e matrice completa.

L'oracolo pubblico Windows `096284975579cb7fc21e0acb3b7f4e40eb605dd7` ha
superato undici casi reali e rende ora eseguibili gli A Windows; non cambia
alcuna delle conclusioni di prodotto sopra riportate.
