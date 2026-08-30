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
