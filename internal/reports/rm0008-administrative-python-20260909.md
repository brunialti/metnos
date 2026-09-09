# RM-0008 — l'interprete amministrativo della Release 2, 9 settembre 2026

Ripresa dopo lo stop delle 15:26. Documento interno.
Prevale su `internal/design/handover_rm0008_stop_9_9_2026_1526.md` per la
parte diagnostica: la direzione di rimedio proposta lì è **rovesciata** da
questo censimento.

## 1. Stato ritrovato, invariato

Quattro servizi attivi dalle 15:23, nessun riavvio automatico, nessun reboot
(boot id invariato). HTTP operativo e non in sola manutenzione, worker pronto,
browser connesso e contratto allineato. Release 2 selezionata, ferma a
`HEAD_REQUIRED` / fase 5. Nessuna scrittura eseguita durante la diagnosi.

Fatti nuovi utili:

- il modulo dei cookie dentro la release selezionata è byte-identico a quello
  provato (`a754e941…`), insieme ai due prompt IT/EN: il percorso browser in
  esercizio esegue davvero il codice nuovo;
- il workflow GitHub `34356573475` sul commit `7132b4db` è **riuscito**;
  la voce aperta al §9 della consegna è chiusa.

## 2. Censimento della clausola, in sola lettura

Confronto di tutte le voci di `_bind_administrative_tcb_core_v1`
(`runtime/executor_birth_admin_preflight.py:8633`) fra il prerequisito di
avvio della Release 2, il suo descrittore firmato e la misura locale dei
quattro eseguibili amministrativi:

| voce | esito |
|---|---|
| percorso `openssl`, `systemctl`, `systemd-analyze` | coincidono |
| impronte `python`, `openssl`, `systemctl`, `systemd-analyze` | coincidono |
| **percorso `python`** | **diverge** |

Una sola divergenza. Il prerequisito, prodotto dalla macchina alle 15:21,
misura già `/usr/bin/python3.12`; è **il descrittore** a dichiarare
l'interprete gestito del prodotto.

## 3. Causa: il costruttore, non il verificatore

`install/executor_birth_distribution_release.py` ricavava l'interprete
amministrativo da `os.path.realpath(sys.executable)` e lo usava per due cose:
il campo `python_executable` del descrittore e il segnaposto
`@administrative_python@` delle unità.

`@administrative_python@` è **l'interprete che esegue come root l'aiutante di
nascita**, cioè il cancello amministrativo stesso. Deve appartenere alla base
fidata del sistema operativo. Il confronto è provato dai due descrittori:

- Release 1, costruita con il Python di sistema → `/usr/bin/python3.12`,
  fase 6 raggiunta;
- Release 2, costruita con il Python gestito → interprete gestito.

Le unità installate oggi avviano quindi il cancello amministrativo con
l'interprete dell'ambiente mutabile del prodotto. **Il verificatore aveva
ragione a rifiutare.** Allentare la clausola — la direzione proposta dalla
consegna precedente — avrebbe reso il descrittore capace di scegliersi da solo
l'interprete del proprio controllo: un indebolimento reale, non una correzione.

L'interprete gestito resta l'interprete **di servizio** ed è già dichiarato
come bersaglio esterno firmato del catalogo, verificato dal ciclo alle righe
8703-8730. La distinzione chiesta dalla consegna esisteva già nel
verificatore; mancava nel costruttore.

## 4. Rimedio applicato

1. `install/executor_birth_distribution_release.py`: nuovo
   `_administrative_python_executable_v1()` che risolve il collegamento fisso
   `/usr/bin/python3`, indipendente dall'interprete che esegue il build;
   rifiuta un bersaglio mancante, non regolare o pendente. L'interprete di
   servizio resta separato.
2. `install/birth_authority_provisioner.py`: controllo preventivo
   `_require_administrative_python_bound_to_tcb_v1`, applicato subito dopo la
   cattura del descrittore firmato, **prima** della prenotazione dell'arco e
   prima di qualunque arresto di servizio. Codice
   `birth_transition_administrative_python_mismatch`.
3. Prove nuove:
   - `tests/portable/test_executor_birth_admin_preflight_tcb.py` — il caso
     concreto del doppio interprete: descrittore con interprete gestito
     rifiutato; con interprete di sistema accettato anche quando il gestito è
     un bersaglio esterno dichiarato;
   - `tests/portable/test_executor_birth_distribution_release.py` — il
     costruttore ignora `sys.executable`; rifiuta collegamento assente,
     directory o pendente;
   - `tests/portable/test_executor_birth_transition_cutover.py` — il controllo
     preventivo accetta l'interprete di sistema e rifiuta gestito, collegamento
     non risolto e campo assente.

Suite `tests/portable`: 2490 passate, 43 saltate, 11 rosse. Attribuzione:
due rosse sono il pin della revisione sorgente, atteso e da riallineare con
`internal/tools/rm0008_repin_source_roots.py` a modifiche finite; una è
l'inventario che differisce da `git ls-files` per il lavoro non committato;
una è il conftest congelato, già segnalata dalla sessione precedente; cinque
richiedono `sudo` senza password; due falliscono per `tomlkit` nel Python di
sistema. Nessuna causata dal rimedio.

## 5. Blocco residuo: la catena non ha un'uscita

La Release 2 non potrà mai essere attestata: il suo descrittore è firmato e
immutabile. Ma la catena non consente nemmeno di superarla:

- `_next_release_edge_v1` (`install/executor_birth_distribution_release.py:235`)
  apre la sequenza successiva **solo** se l'ultima transazione è
  `PREFLIGHT_VERIFIED` alla sequenza 6; la Release 2 è ferma a 5;
- la sola altra via è il ramo di ricostruzione a parità di sorgente, ma la
  rivendicazione di successione è già consumata e
  `_publish_control_no_replace_v2` rifiuta di sostituirla con una diversa;
- gli stati del coordinatore (`OwnershipCoordinatorStateV1`) sono sette e
  vanno in avanti: non esiste un esito «crociera non attestabile».

Quindi: nessun percorso di prodotto porta fuori da questo stato. Serve una
decisione, riportata nel documento di consegna.

## 6bis. Un dettaglio storico, spiegato

L'impronta del Python registrata dalla Release 1 non è riproducibile da alcun
file presente oggi. Non è un'anomalia nuova: è l'incidente descritto da
ADR 0225. Un normale aggiornamento di sicurezza ha cambiato i byte del Python
di sistema dopo l'attestazione della Release 1, ed è proprio questo che aveva
fermato i servizi. Il prerequisito della Release 2, prodotto oggi, misura
correttamente il binario attuale. Nessuna azione richiesta.

## 6. Cosa NON è stato fatto

Nessuna scrittura su release, head, cutover, prerequisito, rivendicazione,
journal o firme. Nessun servizio fermato o riavviato. Nessun reboot. Nessuna
nuova prova di chat con un turno reale. Nessun pin riallineato. Nessuna
pubblicazione. RM-0008 resta aperta.

## 7. Uscita in avanti dal blocco (decisa da Roberto, 9 settembre)

Il blocco del §5 non ha rimedio dentro il prodotto: la scala del giornale ha
uno stato per posizione, quindi «crociera fallita» non è scrivibile fra i
record. L'uscita è quindi un documento di controllo separato, immutabile,
accanto alla transazione, che conserva il suo ultimo record veritiero.

### Forma

`coordinator-v1/abandoned-crossings-v2/<request_id>.json`, uno per crociera,
pubblicato senza sostituzione sotto il lock di deployment. Contiene solo
identità già presenti nel giornale (richiesta, sequenza, build, head,
descrittore, impronta dell'esatto record 5) più un motivo da un **vocabolario
chiuso**, oggi con una sola voce: `administrative_tcb_path_unsatisfiable`.

### Perché non è un modo per aggirare il cancello

- Il motivo non lo dichiara chi chiama: lo **dimostra la macchina** dentro il
  lock, con `_prove_unattestable_crossing_v1`, che riautentica la stessa
  storia firmata dell'attestazione.
- La prova è ammessa solo se i percorsi dichiarati differiscono da quelli
  osservati **e** tutte le impronte del prerequisito coincidono. Percorsi
  diversi con impronte diverse = è la macchina a essere cambiata: non si
  abbandona nulla, il guasto può ancora essere transitorio.
- La prova dichiara anche **quale** crociera ha esaminato; chi scrive rifiuta
  una prova presa su una richiesta diversa. Il dimostratore seleziona tramite
  la head richiesta, chi scrive legge il giornale: devono parlare della stessa.
- Se l'attestazione potrebbe riuscire, il dimostratore solleva: non esiste
  abbandono di una crociera sana.
- Nessuna firma, head, cutover, ricevuta o record viene riscritto.

### I quattro lettori, e la prova che li tiene d'accordo

La stessa regola di ammissione del predecessore è scritta in quattro posti
indipendenti, e la separazione è voluta (il verificatore non si fida degli
altri moduli e ha decoder propri). Sono stati allineati tutti e quattro:

1. `install/executor_birth_distribution_release.py::_next_release_edge_v1`;
2. `runtime/executor_birth_ownership_coordinator.py::_successor_claim_for_transition_v2`;
3. `runtime/executor_birth_ownership_coordinator.py::_resolve_ownership_coordinator_at_v2`;
4. `runtime/executor_birth_admin_preflight.py`, autenticazione dello snapshot fisso.

Quattro implementazioni sono quattro occasioni di divergere. La prova
`test_both_readers_agree_on_every_abandonment_verdict` interroga i due
decisori indipendenti — coordinatore e verificatore — sugli stessi byte e
pretende **lo stesso identico verdetto**, codice ed esatto dettaglio compresi,
su documento assente, valido, non corrispondente al record e duplicato, per
record a sequenza 4, 5 e 6. Una divergenza futura accende quella prova.

### Operazione

`install/birth_authority_provisioner.py::abandon_unattestable_transition_v2`:
prende il lock di deployment, lascia dimostrare al verificatore, scrive.
Non ferma servizi, non riavvia, non pubblica release.

### Prove

`tests/portable/test_executor_birth_ownership_coordinator_v2.py`: documento e
codec, rifiuto del motivo inventato, della prova su un'altra crociera e della
prova assente senza alcuna scrittura, idempotenza, documento estraneo,
accordo builder/coordinatore, accordo fra i due lettori.
`tests/portable/test_executor_birth_transition_cutover.py`: l'operazione tiene
il lock e passa il dimostratore vero, non uno finto.
`tests/portable/test_executor_birth_admin_preflight_tcb.py`: la distinzione
fra release sbagliata (permanente) e macchina cambiata (non permanente).

### Confine dichiarato, non aggirato

Il guardiano del confine ha correttamente rifiutato le quattro funzioni nuove
che scrivono nel deposito e il lettore nuovo: superficie di scrittura nuova nel
nucleo di nascita **deve** essere dichiarata. Sono state aggiunte all'inventario
`internal/reports/rm0007-m4-boundary-inventory.json`, alla politica compilata
`runtime/contract_boundary_birth_authority_policy.py` e alla proiezione
rigenerata con `internal/tools/render_contract_boundary_policy.py --write`.
Le impronte congelate delle prove di caratterizzazione sono state riallineate:
la conta dei rilievi passa da 157 a 161, cioè esattamente i quattro nuovi
proprietari dichiarati. I pin della revisione sorgente sono stati riallineati
due volte con `internal/tools/rm0008_repin_source_roots.py`, come prescritto.

### Stato delle prove

`tests/portable`: **2502 passate**, 43 saltate, 9 rosse. Le nove sono tutte
preesistenti e non attribuibili a questo lavoro: cinque richiedono `sudo` senza
password, due dipendono da `tomlkit` nel Python di sistema, una confronta
l'inventario con `git ls-files` e si chiude al commit, una è il conftest
congelato già segnalato dalla sessione precedente. Le due rosse del pin della
revisione sorgente, presenti a metà lavoro, sono chiuse.

### Cosa resta prima di usarla in esercizio

L'operazione **non è stata eseguita** sulla macchina. Servono, nell'ordine:
riallineamento dei pin della revisione sorgente, commit, esecuzione
dell'abbandono sotto lock, costruzione della Release 3 con il costruttore
corretto — questa volta l'interprete di build non conta più — e crociera
completa fino all'attestazione.
