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
