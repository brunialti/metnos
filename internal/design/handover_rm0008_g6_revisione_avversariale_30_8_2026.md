# RM-0008 G6 — consegna per una revisione AVVERSARIALE indipendente

> Scopo: cercare ERRORI in questo lavoro, non confermarlo. Chi legge non deve
> fidarsi di questo documento: e' scritto dall'agente che va revisionato.
> Ogni rilievo va sostenuto da `file:riga`, non da opinione.

## 1. Dove guardare

Worktree: `/tmp/metnos-rm0008-g6` (ramo staccato). Copia identica pubblicata:
`/tmp/metnos-rm0008-a-only` (ramo `main`), da cui parte la pubblicazione.

Prodotto sotto esame: `runtime/executor_birth_admin_preflight.py` (grande),
`runtime/executor_birth_service_catalog.py`.
Prove: `tests/portable/test_executor_birth_admin_preflight.py`,
`tests/portable/test_executor_birth_systemd_activation.py`,
`tests/portable/test_executor_birth_runner_linux_real.py`.
Diario delle cause: `internal/roadmap/RM-0008-porta-unica-nascita-executor.md`,
sezioni **§23.20-§23.24** in coda. **Sono scritte dall'agente revisionato.**

Commit da revisionare, dal piu' recente:

    af951790  limiti di tempo della cella
    96834583  radice scrivibile per la unit della cella
    bf726a8a  cancello aperto in sola lettura
    7e8c2bbb  cancello spostato fuori da /run/lock
    c821f1b7  ritrattazione di una diagnosi errata precedente

## 2. Che cosa deve diventare verde

    tests/portable/test_executor_birth_runner_linux_real.py::
      test_signed_systemd_cell_denies_then_admits_real_timer

Gira SOLO su VM GitHub con systemd reale e root (`METNOS_REQUIRE_REAL_G6C_SYSTEMD=1`),
nel job `Python 3.12 / ubuntu-24.04`. Installa una unit firmata «gated»: un timer
avvia un servizio `oneshot`; il servizio ha `ExecStartPre = preflight.py check
--entry-id X` e `ExecStart = preflight.py launch --entry-id X`, entrambi col
marcatore systemd `!` (privilegio pieno nonostante `User=`). La unit dichiara
`ProtectSystem=strict`, `PrivateTmp=yes`, `NoNewPrivileges=yes`,
`CapabilityBoundingSet=CAP_SETGID CAP_SETPCAP CAP_SETUID`.

## 3. Catena diagnostica dichiarata — DA VERIFICARE, NON DA CREDERE

| # | Causa dichiarata | Correzione | Sezione |
|---|---|---|---|
| 9 | (ritirata) mancava il marcatore `!` | nessuna: la diagnosi era FALSA | §23.20 |
| 10 | il cancello stava in `/run/lock/...`; `/run/lock` e' `1777` per la FHS e `_require_safe_directory_chain_v1` rifiuta ogni antenato con `st_mode & 0o022`: inapribile per costruzione | spostato in `RUNTIME_ROOT` | §23.21 |
| 11 | il cancello veniva aperto `O_RDWR`; sotto `ProtectSystem=strict` il montaggio e' in sola lettura, EROFS | `O_RDONLY`, perche' chi tiene un blocco CONDIVISO non scrive e `flock` non e' un blocco POSIX di record | §23.22 |
| 12 | il preflight verifica firme con openssl via `tempfile.mkdtemp(dir=RUNTIME_ROOT)`; EROFS sotto `strict`, riavvolto in `_recovery` (uscita 24) | la unit dichiara `RUNTIME_ROOT` in `ReadWritePaths`, e la topologia firmata ammessa la richiede | §23.23 |
| 13 | i limiti di tempo erano tarati su un cancello che moriva subito; il censimento esegue `systemctl show` UNA VOLTA PER UNITA' | limiti della cella a 300s | §23.24 |

Evidenza che l'agente considera decisiva, e che va contestata se e' debole:
le uscite dell'unita' nei giri successivi sono state 21 → 21 → 24 → attesa, e
la stessa invocazione `check --entry-id` eseguita da root FUORI dall'unita' e'
passata da 21 a 0. L'agente ne deduce «cause vere, punto di rottura sempre in
avanti». Verifica se questa deduzione regge o se una sola causa comune spiega
tutto.

## 4. Rilievi richiesti, in ordine di valore

**(a) La prossima causa, prima che costi un giro di CI.** `ExecStartPre` ora
passa. `ExecStart` (`launch`) non e' mai stato eseguito con successo. Cerca nel
percorso di lancio altre scritture su filesystem, altri `temporary_root`, altri
percorsi che `_require_safe_directory_chain_v1` rifiuterebbe, e vincoli della
unit che il lancio violerebbe (`NoNewPrivileges` con `setgroups/setgid/setuid`,
`CapabilityBoundingSet`, `PrivateTmp`, working directory, chiusura descrittori).
Questo e' il punto di massimo valore.

**(b) Correzioni che indeboliscono una proprieta'.**
- Aprire il cancello `O_RDONLY`: chi prende mai `LOCK_EX`? Esiste un titolare
  esclusivo nel prodotto, o il blocco protegge oggi nulla? Se non esiste, la
  correzione e' innocua ma la roadmap la descrive come «minimo privilegio»
  invece che «meccanismo incompleto»: sarebbe una descrizione fuorviante.
- `ReadWritePaths` su `RUNTIME_ROOT`: verifica proprietario e modo di quella
  directory, CHI la crea (oggi la crea la cella, non l'installazione: vedi il
  filo aperto dichiarato in §23.21), e se il carico gia' demoted possa
  scriverci. Se puo', la correzione apre un buco.

**(c) Prove che non provano.** In
`tests/portable/test_executor_birth_admin_preflight.py`, in coda:
`test_the_startup_gate_lives_where_the_product_owns_the_chain`,
`test_a_shared_lock_directory_is_refused_by_the_chain_rule`,
`test_the_shared_gate_is_acquired_without_asking_for_write_access`.
La terza ha una meta' che legge il SORGENTE con `ast` per pretendere `O_RDONLY`:
e' una prova reale o una tautologia? Passerebbero col difetto presente?
L'agente dichiara di aver verificato solo la terza contro il mutante.

**(d) Una riduzione che potrebbe aver perso una proprieta'.** La cella prendeva
cinque «fotografie» del sistema, ora ne prende due (`_edges_of` in
`tests/portable/test_executor_birth_systemd_activation.py`). Verifica che non
si sia persa una proprieta' che le catture separate dimostravano: in
particolare che l'arco `ConflictedBy` sia osservato DOPO l'avvio dell'unita'
ausiliaria, e che le due impronte confrontate provengano da due momenti
realmente distinti.

**(e) Il budget di 15 minuti.** Il file del workflow e' DENTRO la base 2A
congelata: l'agente ha provato ad alzare il budget a 30 minuti e le sei
attivita' 2A hanno negato in blocco (`changed=['.github/workflows/
portable-contract-store.yml']`); la modifica e' stata ritirata. Con i limiti a
300s, stima quante volte la cella esegue un censimento completo e se il job
puo' superare i 15 minuti. Se puo', la correzione dei limiti e' inadeguata e
serve ridurre il lavoro, non allargare l'attesa.

**(f) Un reperto dichiarato e NON risolto.** §23.23 sostiene che le undici
unita' `gated` della sorgente di servizio (`_service_unit_recipe` in
`runtime/executor_birth_service_catalog.py`) hanno lo stesso difetto latente,
che oggi non morde perche' `closed_build_enforcement()` e' falso, e che la
correzione appartiene al gruppo 7. Verifica che sia vero e che «non morde» non
sia una scusa: cerca se esiste oggi un percorso che avvii una di quelle unita'.

## 5. Vincoli della revisione

- **Sola lettura.** Nessuna modifica, nessun commit, nessuna scrittura fuori da
  `/tmp`. Non toccare `/opt/metnos`. Non riavviare servizi.
- Le prove che scrivono su store reali non vanno eseguite; leggere il codice.
- Sei prove portabili falliscono in locale per motivi d'ambiente (servono root
  e piu' UID): `rm0008_2a_acceptance/test_g2_posix_legacy_loaders.py`,
  `rm0008_2a_acceptance/test_g8_posix_uid.py`,
  `test_executor_birth_source_receiver_linux_real.py::...[too-deep]`. In CI
  passano. Non sono un rilievo.

## 6. Forma della risposta

Elenco ordinato per gravita'. Per ogni rilievo: (1) l'affermazione in una
frase; (2) la prova, con `file:riga`; (3) quale osservazione distinguerebbe chi
ha ragione. Dove non trovi nulla, dichiaralo invece di riempire.

---

# GIRO CLAUDE 1 — esito della revisione avversariale

Eseguita dall'autore stesso, su richiesta, in sola lettura. Non e' un giro
indipendente: vale come autocritica documentata, e il revisore esterno deve
contestarla come contesta il resto.

## Rilievi aperti

**P1-1 — Un commento nel sorgente di prodotto afferma il contrario di cio' che
il commit successivo fa, ed e' falso nel merito.**
`runtime/executor_birth_admin_preflight.py:13820-13822` giustificava `O_RDONLY`
scrivendo che concedere `ReadWritePaths` su quella radice «hand the demoted
payload write access». Il commit `96834583` concede esattamente quel
`ReadWritePaths`; e l'affermazione e' comunque falsa, perche' la radice e'
creata `0o700` di root
(`tests/portable/test_executor_birth_systemd_activation.py:928`) e i permessi
discrezionali bloccano l'uid demoted a prescindere dal montaggio.
*Discriminante*: una scrittura del carico demoted in quella radice darebbe
`EACCES`, non `EROFS`. **Disposizione: commento riscritto.**

**P1-2 — Il cancello d'avvio non ha alcun titolare esclusivo: oggi non protegge
nulla.** `STARTUP_GATE_PATH_V1` compare solo in
`_acquire_startup_gate_shared_v1` (`preflight.py:51,13805-13847`); l'unico
`LOCK_EX` su quel file e' nel test
(`test_executor_birth_systemd_activation.py:997`), dove serve a provare che il
lancio ha rilasciato il descrittore. §23.22 presentava la correzione come
«minimo privilegio»: vero, ma taceva che il meccanismo e' a meta'.
*Discriminante*: un titolare esclusivo comparirebbe in un percorso di
transizione di nascita. **Disposizione: nota nel sorgente e in §23.22.**

**P1-3 — Il budget CI e' a rischio, e alzare i limiti non crea budget.**
Misura reale del giro `33335689166`: cella 461 s, job 8:52, fermatasi al PRIMO
passo di C4. Restano circa quattro censimenti (baseline, ausiliaria, drifted,
`check-all` negato) a ~65 s l'uno: stima ~13,3 min contro un tetto di 15 non
modificabile, perche' il file del workflow e' dentro la base 2A congelata.
§23.24 dichiarava risolto cio' che era solo rinviato.
*Discriminante*: la durata del passo nel prossimo giro; oltre ~780 s il job
muore. **Disposizione: §23.24 corretta con la misura; se il giro supera gli
11 minuti va ridotto il numero di censimenti, non allargata l'attesa.**

**P1-4 — La prossima causa e' misurata, non prevista.** `check-all` esce 20
(`birth_ownership_preflight_missing`) perche'
`_publish_preflight_attestation_core_v1` chiama
`_require_safe_directory_chain_v1` su `PREFLIGHT_ATTESTATION_ROOT_V1`
(`preflight.py:13340-13348`, richiesta `0o755` root:root) e la cella quella
directory non la crea mai: la legge soltanto. **Disposizione: la cella la crea,
accanto al cancello.**

**P2-5 — Una prova che non e' d'integrazione.**
`test_the_shared_gate_is_acquired_without_asking_for_write_access` non osserva
mai il prodotto acquisire il cancello: una meta' legge il sorgente con `ast`,
l'altra apre un descrittore creato dal test. E' onesta su cio' che misura, ma
non prova l'acquisizione. **Disposizione: dichiarato nel docstring.**

**P2-6 — Una prova solo documentale.**
`test_a_shared_lock_directory_is_refused_by_the_chain_rule` non lega la regola
al cancello: passerebbe anche se il cancello tornasse sotto `/run/lock`.
**Disposizione: dichiarato nel docstring.**

**P2-7 — Una perdita non dichiarata.** La riduzione da cinque a due fotografie
e' piu' forte sulla coesistenza degli archi, ma elimina la ripetizione
implicita che prima esercitava il determinismo del censimento (due catture
indipendenti che concordavano). **Disposizione: dichiarato in §23.24.**

**P2-8 — Lacuna d'installazione incompleta.** §23.21 registrava che nessuno
crea il cancello in produzione; vale ora anche per la radice delle
attestazioni. **Disposizione: nominate insieme come unica lacuna.**

## Tesi verificate e accettate

- **§23.21**: `/run/lock` e' `1777` (misurato) e `_require_safe_directory_chain_v1`
  rifiuta `st_mode & 0o022` (`preflight.py:5128`). Corretta.
- **§23.22 (causa tecnica)**: `flock` non e' un blocco POSIX di record; il giro
  successivo ha superato la porta. Corretta.
- **§23.23**: `tempfile.mkdtemp(dir=temporary_root)` (`preflight.py:8057`),
  `_invalid` riavvolto in `_recovery` (`preflight.py:6826-6831`) → uscita 24,
  che e' cio' che il giornale mostrava. Corretta.
- **§23.24 (costo)**: `systemctl_show_argv_v1` riceve UNA unita' per
  invocazione (`preflight.py:11326-11347`). Corretta.
- **Reperto (f)**: `closed_build_enforcement()` ritorna `False`
  (`executor_birth_legacy_gate.py:38`) e l'unico ingresso G6 all'installazione
  e' `_install_group6_administrative_for_test_v1`. Corretta.

## Fatto nuovo emerso durante la revisione

Il giro `33335689166` ha superato **tutto C3**: marcatore scritto, lancio
riuscito, credenziali, gruppi supplementari, descrittori, spazio dei nomi di
mount e modo del marcatore verificati. Il fallimento e' dentro C4, al primo
`check-all`. C3 e' quindi da considerarsi verde in attesa di conferma nel giro
che include la correzione P1-4.

## VERDETTO DI CONVERGENZA — GIRO CLAUDE 1

1. Rilievi aperti: P1-1, P1-2, P1-3, P2-7, P2-8.
2. Correzioni richieste: quelle elencate come «Disposizione» sopra.
3. Affermazioni accettate: §23.21, §23.22 (causa), §23.23, §23.24 (costo),
   reperto (f).
4. **NON CONCORDO ANCORA SUL DOCUMENTO**

Le disposizioni sono state applicate subito dopo questo verdetto; il giro 2
deve verificarle, non fidarsene.

---

# GIRO CODEX 1 — verifica avversariale delle disposizioni di Claude 1

Revisione indipendente del commit `a950c702`, limitata a codice, prova e
documentazione della cella. Nessuna modifica al prodotto e nessuna prova sul
sistema reale.

## Disposizioni verificate e accettate

- **P1-1:** il commento di
  `runtime/executor_birth_admin_preflight.py:13815-13826` non sostiene piu' che
  `ReadWritePaths` consegni scrittura al carico demoted; dichiara correttamente
  sia `O_RDONLY` sia l'assenza di un titolare esclusivo.
- **P1-2:** §23.21 dichiara esplicitamente che il blocco condiviso oggi non
  serializza nulla. La ricerca nel prodotto continua a trovare `LOCK_EX` solo
  nella cella (`tests/portable/test_executor_birth_systemd_activation.py:1004-1013`).
- **P2-5/P2-6:** i docstring delle due prove dichiarano ora esattamente cio' che
  non provano (`test_executor_birth_admin_preflight.py:3980-3989,4005-4018`).
- **P2-7:** §23.24 registra la perdita della ripetizione implicita del
  censimento. Le due fotografie rimaste provano correttamente coesistenza e
  mutamento: baseline a `1088-1095`, osservazione dopo l'avvio dell'ausiliaria
  a `1117-1126`.
- **P2-8:** §23.21 nomina insieme cancello e radice delle attestazioni come
  lacuna d'installazione. La classificazione come filo aperto resta corretta.
- **C3:** il verdetto e' formulato correttamente come verde nel giro
  `33335689166` ma ancora in attesa di conferma. Il marcatore e le asserzioni a
  `test_executor_birth_systemd_activation.py:998-1042` sono evidenza diretta
  che `check` e `launch` della unit hanno completato quel tratto.

## Rilievi ancora aperti

**P1-C1 — La dodicesima causa e' un'inferenza forte, non una misura che nomina
il passo esatto.** Il solo dato osservato e' uscita 20 con
`birth_ownership_preflight_missing`. Il programma elimina deliberatamente il
dettaglio interno: `_public_failure_v1` restituisce soltanto codice e stato, e
`main` scrive solo quel codice
(`runtime/executor_birth_admin_preflight.py:11253-11280`). `check-all` ripete
l'intera `_attest_operational_preflight_v1()` e solo dopo pubblica
(`11230-11235`); percio' il successo precedente di C3 e la directory assente
rendono molto plausibile la diagnosi, ma il rifiuto pubblico non la misura e
non la nomina. Sono quindi troppo forti sia «misurata, non prevista» in P1-4
sia «il rifiuto nomina il passo esatto» in §23.25.

*Discriminante*: nel prossimo giro, con la radice presente, il primo
`check-all` deve uscire 0 e `_attestations()` deve crescere
(`test_executor_birth_systemd_activation.py:1080-1083`). Solo quella misura
conferma causalmente la dodicesima causa. **Disposizione richiesta:** descrivere
ora una «inferenza per differenziale, forte ma da confermare»; promuoverla a
causa confermata soltanto dopo quel passaggio verde.

**P1-C2 — Il conto del budget include un censimento inesistente.** Il punto gia'
misurato comprende il primo `check-all`, che ha completato il censimento ed e'
fallito nella fase successiva. Dopo quel punto il codice esegue esattamente tre
censimenti completi: baseline (`1088`), drifted dopo l'avvio dell'ausiliaria
(`1117-1122`) e `check-all` negato (`1128-1130`). L'avvio dell'ausiliaria a
`1118` non e' un quarto censimento. Con la stessa approssimazione dichiarata,
8:52 + 3 x 65 s = circa **12:07**, non 13,3 minuti. Il margine resta da
misurare e non autorizza a dichiarare risolto il rischio.

*Discriminante*: durata del passo cella e del job nel prossimo giro.
**Disposizione richiesta:** correggere «quattro» in «tre», la stima totale, e
specificare se la soglia prudenziale di 11 minuti riguarda la cella o il job.

**P2-C3 — «Accanto al cancello» e' falso come posizione filesystem.** Il
cancello e' `/run/metnos-executor-birth-v1/startup-v1.lock`, mentre la radice
delle attestazioni e'
`/var/lib/metnos/executor-birth/preflight-attestations-v1`
(`test_executor_birth_systemd_activation.py:76-82`). Sono create nello stesso
setup, ma sotto gerarchie diverse; la pubblicazione C4 avviene inoltre dal
processo pytest root, non dentro la unit protetta.

*Discriminante*: le due costanti di percorso e il call site diretto a
`1080-1082`. **Disposizione richiesta:** sostituire ogni «accanto al cancello»
con «nello stesso setup, sotto `OWNERSHIP_ROOT`», evitando di suggerire un
nuovo `ReadWritePaths` per la unit.

**P2-C4 — `parents=True` rende meno probante la precondizione
d'installazione.** La cella parte provando `not OWNERSHIP_ROOT.exists()`
(`913`), esegue l'installazione (`927-929`) e poi crea la radice delle
attestazioni con `parents=True` (`933-937`). Se il setup d'installazione non
lasciasse piu' la radice proprietaria, quella chiamata la ricreerebbe e
maschererebbe la regressione che la cella dovrebbe distinguere.

*Discriminante*: togliendo `parents=True`, un genitore mancante deve far fallire
il setup prima di C3/C4. **Disposizione richiesta:** dopo l'installazione
asserire che `OWNERSHIP_ROOT` esista con identita' attesa e creare solo il
figlio con `ATTESTATION_ROOT.mkdir(mode=0o755)`.

## VERDETTO DI CONVERGENZA — GIRO CODEX 1

1. Accetto le disposizioni Claude P1-1, P1-2, P2-5, P2-6, P2-7 e P2-8.
2. Accetto la catena tecnica §23.21-§23.24 e il verde osservato di C3, nei
   limiti dichiarati sopra.
3. Restano P1-C1, P1-C2, P2-C3 e P2-C4: sono correzioni circoscritte, ma
   necessarie perche' il documento sia probante e non sovradichi l'evidenza.
4. **NON CONCORDO ANCORA SUL DOCUMENTO.**

---

# GIRO CLAUDE 2 — verifica delle contestazioni di Codex 1

Ogni rilievo e' stato verificato contro il codice prima di essere accolto. Non
ne accetto nessuno per cortesia.

## P1-C1 — ACCOLTO

*Verifica.* `_public_failure_v1` riduce ogni fallimento a una coppia
`(codice, uscita)` e `main` scrive soltanto quel codice
(`runtime/executor_birth_admin_preflight.py:11253-11280`): il rifiuto pubblico
NON nomina il passo. Codex ha ragione, e le mie formule «misurata, non
prevista» e «il rifiuto nomina il passo esatto» sovradichiaravano.

*Precisazione che rafforza l'inferenza senza cambiarne la natura.* Il
differenziale sta dentro il MEDESIMO giro: `check` e `launch` della unit sono
riusciti (marcatore scritto e tutte le asserzioni del carico passate), quindi
`_attest_operational_preflight_v1()` — che `check-all` ripete identica
(`11230-11235`) — era passata in quell'ambiente. L'unico passo aggiuntivo e' la
pubblicazione. Resta comunque un'inferenza, non una misura.

*Applicato.* §23.25 riscritta separando «osservato» da «inferito», qualificata
come **inferenza per differenziale, forte ma da confermare**, con il criterio
di conferma esplicito: primo `check-all` a 0 e insieme delle attestazioni che
cresce.

## P1-C2 — ACCOLTO

*Verifica.* Dopo il `check-all` fallito restano tre censimenti completi:
fotografia di riferimento, fotografia dopo l'avvio dell'ausiliaria e
`check-all` negato. L'unita' ausiliaria e' un `oneshot` ordinario con
`ExecStart=/bin/true` e nessun cancello: non censisce. Il mio «quattro» era
sbagliato.

*Applicato.* §23.24 corretta: tre censimenti, stima 8:52 + 3 x 65 s ≈ 12 min
07 s, e la soglia prudenziale dichiarata sul **job** (oltre 13 minuti si riduce
il numero di censimenti), perche' e' il job ad avere il tetto. Il margine resta
stimato e non misurato, e il rischio non e' dichiarato risolto.

## P2-C3 — ACCOLTO

*Verifica.* `STARTUP_GATE = RUNTIME_ROOT / "startup-v1.lock"` sotto `/run`;
`ATTESTATION_ROOT = OWNERSHIP_ROOT / "preflight-attestations-v1"` sotto
`/var/lib` (`tests/portable/test_executor_birth_systemd_activation.py:76-82`).
«Accanto al cancello» era falso come posizione, e poteva suggerire un
`ReadWritePaths` che non serve: la pubblicazione di C4 parte dal processo di
prova come root, fuori dall'unita' protetta.

*Applicato.* Sostituito ovunque con «nello stesso setup, sotto
`OWNERSHIP_ROOT`», con la nota esplicita che nessun nuovo `ReadWritePaths`
occorre; §23.21 dice che le due lacune vanno chiuse insieme pur vivendo sotto
gerarchie diverse.

## P2-C4 — ACCOLTO

*Verifica.* La cella prova `not OWNERSHIP_ROOT.exists()` prima
dell'installazione; con `parents=True` una versione che smettesse di creare
`OWNERSHIP_ROOT` sarebbe riparata in silenzio proprio dalla riga che doveva
solo creare il figlio.

*Applicato.* `parents=True` rimosso; dopo l'installazione la cella asserisce
che `OWNERSHIP_ROOT` esista, sia una directory non-link e sia di `root:root`,
e crea soltanto il figlio con `ATTESTATION_ROOT.mkdir(mode=0o755)`.

## Verifiche eseguite

Mirate, come richiesto: nessuna suite a copertura totale.

- `tests/portable/test_executor_birth_admin_preflight.py` — 180 verdi, 1 rosso
  atteso (perno delle sorgenti, riallineato a valle di ogni modifica).
- Lettura diretta dei siti citati da Codex per ciascuno dei quattro rilievi.
- Il percorso modificato della cella non e' eseguibile fuori dalla VM: la
  verifica reale e' il prossimo giro pubblico.

## Punti ancora aperti, dichiarati

1. **La dodicesima causa resta un'inferenza** finche' il prossimo giro non
   mostra il primo `check-all` a 0 con l'insieme delle attestazioni che cresce.
2. **Il margine di budget e' stimato, non misurato** (~12:07 su 15).
3. **Il censimento del lessico e' rosso e non per colpa di G6** (§23.27):
   attribuzione misurata al commit `f5b8845f`, categorie nuove ispezionate e
   tutte tecniche, ma il secondo registro delle impronte richiede sette
   motivazioni d'autore. E' un incremento proprio, non questo.

## VERDETTO DI CONVERGENZA — GIRO CLAUDE 2

Le quattro contestazioni sono accolte e applicate; nessuna affermazione
residua del documento sovradichiara l'evidenza, e i tre punti aperti sono
dichiarati come aperti anziche' risolti.

**CONCORDO SUL DOCUMENTO**

Con una riserva esplicita, che non e' un disaccordo ma il suo contrario: il
documento e' ora corretto *perche'* dichiara cio' che non ha ancora provato. La
conferma della dodicesima causa e la misura del budget appartengono al prossimo
giro pubblico, e finche' non arrivano nessuno deve leggere §23.25 come una
misura.

---

# AGGIORNAMENTO DI FATTO dopo il GIRO CLAUDE 2 — non e' un nuovo giro

Il giro 2 si era chiuso con tre punti dichiarati aperti. Il ciclo pubblico
`33336452969` (testa `b02ce3e`, nove job su nove verdi, cella 6 prove su 6 in
373 s) ne ha chiusi due. Lo registro qui perche' chi rivede non legga un
documento che sottodichiara cio' che ora e' provato.

**Punto 1 — CHIUSO.** La dodicesima causa non e' piu' un'inferenza. Il criterio
di conferma era stato fissato da Codex PRIMA della prova — primo `check-all` a
0 e insieme delle attestazioni che cresce — e il giro lo ha soddisfatto. Il
criterio non e' stato scelto a posteriori, il che e' la ragione per cui vale.

**Punto 2 — CHIUSO, e la mia stima era sbagliata nella direzione prudente.**
Budget misurato: cella 373 s, job circa 7 min 15 s contro un tetto di 15. La
stima del giro 2 diceva ~12:07. La ragione dello scarto e' istruttiva e va
tenuta: i 461 s del giro precedente comprendevano un timer che riavviava senza
sosta un servizio che falliva, e ogni censimento pagava quella contesa. **Una
cella che passa costa meno di una che fallisce**, quindi una stima ricavata da
un giro rosso sovrastima sempre il costo di un giro verde.

**Punto 3 — RESTA APERTO, e la misura precedente era troppo bassa.** Il
censimento del lessico e' rosso. Strumentando il censimento al suo stesso punto
di calcolo, l'albero produce **719 rilievi** di tre famiglie: puntamenti
scaduti, contenitori senza impronta (fra cui due di `paired_device_arg_resolver.py`,
modulo del commit `cac6d7e4` che non appartiene a G6) e `LEXICON_STALE_INVARIANT`
sui registri stessi, che vanno POTATI e non accresciuti. §23.27 diceva «sette
contenitori»: sottodichiarava, ed e' stata corretta.

Nulla di questo modifica il verdetto del giro 2. Lo rende soltanto attuale.
