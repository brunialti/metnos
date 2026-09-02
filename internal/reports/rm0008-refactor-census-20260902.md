# RM-0008 — censimento per la rifattorizzazione strutturale

Data: 2 settembre 2026
Worktree: `/tmp/metnos-rm0008-f4-transizione`
Commit di partenza: `bef2c77f40a92a67daf0acc4c055b486478a6491`

## Regole operative

- Nessuna modifica all'esercizio durante censimento, pulizia e
  rifattorizzazione.
- Il worktree `/opt/metnos` e lo stato produttivo non fanno parte del perimetro
  di scrittura.
- L'handover locale già modificato viene preservato e non viene incluso nei
  commit della rifattorizzazione.
- Prima di rimuovere un file si provano assenza di processi, riproducibilità,
  stato Git e assenza di un ruolo di recovery.
- La rifattorizzazione privilegia deduplicazione, helper condivisi con un solo
  proprietario e API pubbliche minime. Non verrà introdotto un generico
  `utils.py`.

## Baseline della pulizia temporanea

Il primo inventario di `/tmp` rileva 186 oggetti con prefisso
`metnos-rm0008-`, per 11.164.454.912 byte (10,398 GiB): 37 file e 149
directory. Nessun processo attivo fa riferimento a questi percorsi.

Classificazione iniziale:

- **preservare**: worktree di rifattorizzazione, baseline comparativa e
  worktree con modifiche non registrate;
- **rimuovere**: export derivati, copie identiche di commit, probe conclusi,
  directory XDG di test, archivi duplicati e script one-off di attivazione o
  diagnosi;
- **verificare prima di rimuovere**: worktree Git registrati e cloni che
  contengono modifiche locali.

Gli export `activation-export-v7`…`v22` sono copie derivate e riproducibili.
Gli script `/tmp/metnos-rm0008-activate*.sh`, `diagnose-*`, `inspect-*`,
`preflight-*`, `traced-*` e `verify-*` sono one-off conclusi e non sono
entrypoint di prodotto.

### Esito della pulizia

- Gli script e gli export one-off sono stati rimossi da `/tmp`.
- Dodici worktree Git puliti e raggiungibili sono stati rimossi tramite Git e
  i relativi metadati sono stati potati.
- Quarantuno copie, probe ed export che contenevano anche residui non
  registrati sono stati spostati, non cancellati, in
  `/tmp/metnos-rm0008-quarantine.YniLGQ`. La quarantena occupa 5.142.069.248
  byte ed è integralmente recuperabile fino alla sua cancellazione esplicita.
- Nel worktree attivo sono stati eliminati soltanto artefatti ignorati da Git:
  `tests/e2e/tmp`, bytecode, cache pytest e `dist/metnos-public`. La sola
  directory `tests/e2e/tmp` occupava 1.415.766.016 byte.
- Restano preservati `f4-baseline`, il worktree attivo, `recovery` con otto
  modifiche sorgente/test reali e due worktree detached ancora da confrontare.
- `dist/.public-repo` è un repository annidato ignorato, pulito e
  riproducibile; viene conservato finché non termina il confronto dei
  certificati pubblici.

La pulizia ha quindi separato il materiale operativo dal materiale storico
senza distruggere contenuti ambigui. La quarantena non è una dipendenza della
rifattorizzazione.

## Evidenza dimensionale iniziale del codice

I maggiori moduli direttamente coinvolti o raggiunti dalla transizione sono:

| Modulo | Righe | Funzioni | Funzione massima |
|---|---:|---:|---:|
| `runtime/executor_birth_admin_preflight.py` | 14.826 | 369 | 610 |
| `runtime/contract_store.py` | 6.467 | 156 | 295 |
| `runtime/executor_birth_ownership_coordinator.py` | 5.862 | 182 | 193 |
| `install/birth_authority_provisioner.py` | 5.288 | 201 | 239 |
| `runtime/executor_birth_secure_fs.py` | 5.166 | 155 | 234 |
| `runtime/contract_boundary_guard.py` | 2.943 | 77 | 486 |
| `runtime/executor_birth_service_catalog.py` | 2.536 | 65 | 196 |
| `install/executor_birth_source_receiver.py` | 1.973 | 62 | 223 |
| `runtime/executor_birth_bootstrap.py` | 1.425 | 57 | 120 |
| `runtime/stack_reconcile.py` | 1.115 | 45 | 126 |

Questi numeri sono indicatori di perimetro, non ancora decisioni di taglio.
Il censimento successivo mapperà chiamanti, autorità, effetti I/O, identità,
lock e stato persistente funzione per funzione.

## Perimetro esatto

### File toccati dalla sequenza operativa

Il range `07469c36^..bef2c77f` ha modificato esattamente questi 36 file:

**Prodotto e installazione**

- `install/birth_authority_provisioner.py`
- `install/executor_birth_contract_convergence.py`
- `install/executor_birth_distribution_release.py`
- `install/executor_birth_transition.py`
- `runtime/contract_boundary_guard.py`
- `runtime/contract_cutover_guard.py`
- `runtime/contract_store.py`
- `runtime/executor_birth_admin_preflight.py`
- `runtime/executor_birth_bootstrap.py`
- `runtime/executor_birth_ownership_chain.py`
- `runtime/executor_birth_prepared_root.py`
- `runtime/executor_birth_secure_fs.py`
- `runtime/executor_birth_service_catalog.py`
- `runtime/manifest_inventory.py`
- `runtime/skill_registry.py`
- `runtime/stack_reconcile.py`
- `scripts/publish-public.sh`

**Test**

- `tests/portable/rm0008_2a_acceptance/test_r4_posix_lock_durability.py`
- `tests/portable/rm0008_2b/test_author.py`
- `tests/portable/rm0008_2b/test_group3_authority_consumption.py`
- `tests/portable/test_contract_cutover_guard_session.py`
- `tests/portable/test_executor_birth_admin_preflight_launch.py`
- `tests/portable/test_executor_birth_admin_preflight_materials.py`
- `tests/portable/test_executor_birth_distribution_release.py`
- `tests/portable/test_executor_birth_service_catalog.py`
- `tests/portable/test_executor_birth_transition_cutover.py`
- `tests/portable/test_executor_birth_transition_entry.py`
- `tests/runtime/contracts/test_contract_store.py`
- `tests/runtime/contracts/test_executor_birth_contract_convergence.py`
- `tests/runtime/executors/test_executor_birth_bootstrap.py`
- `tests/runtime/infra/test_stack_reconcile.py`
- `tests/runtime/skills/test_skill_enablement.py`

**Evidenze e strumenti**

- `internal/design/handover_rm0008_chiusura_transizione_2_9_2026.md`
- `internal/reports/rm0007-m4-boundary-inventory.json`
- `internal/tools/prova_b3_server_post_transizione.py`
- `internal/tools/sonda_sovrapposizione_unita_rm0008.py`

### Chiusura strutturale

Il solo diff non è un perimetro sufficiente. La chiusura statica comprende 91
moduli produttivi: i dieci moduli `install/*birth*.py`, tutti i moduli
`runtime/executor_birth*.py` e i seguenti owner trasversali:

- `runtime/config.py`
- `runtime/contract_boundary_guard.py`
- `runtime/contract_cutover_guard.py`
- `runtime/contract_store.py`
- `runtime/manifest_inventory.py`
- `runtime/skill_registry.py`
- `runtime/stack_reconcile.py`

Questa definizione è deterministica sul commit censito e non include moduli per
semplice somiglianza nominale. Nel perimetro risultano 80 moduli direttamente
coinvolti, 67.643 righe di produzione e 135 file Python di test correlati. Il
corpus contiene 1.172 test ma anche 1.308 usi di `monkeypatch`: molte boundary
operative sono simulate invece di essere provate con NSS, filesystem e
systemd reali.

### Perimetro di intervento prioritario

P0, da modificare o spezzare:

- `install/birth_authority_provisioner.py`
- `install/birth_authority_provisioning.py`
- `install/executor_birth_contract_convergence.py`
- `install/executor_birth_distribution_release.py`
- `install/executor_birth_source_receiver.py`
- `install/executor_birth_systemd.py`
- `install/executor_birth_transition.py`
- `runtime/config.py`
- `runtime/contract_boundary_guard.py`
- `runtime/contract_cutover_guard.py`
- `runtime/contract_store.py`
- `runtime/executor_birth_admin_preflight.py`
- `runtime/executor_birth_distribution_assembler.py`
- `runtime/executor_birth_distribution_manifest.py`
- `runtime/executor_birth_ownership_coordinator.py`
- `runtime/executor_birth_secure_fs.py`
- `runtime/executor_birth_service_catalog.py`
- `runtime/manifest_inventory.py`
- `runtime/skill_registry.py`
- `runtime/stack_reconcile.py`

P1, da disaccoppiare attraverso API pubbliche:

- `runtime/executor_birth_bootstrap.py`
- `runtime/executor_birth_context_selection.py`
- `runtime/executor_birth_dominant_startup.py`
- `runtime/executor_birth_dominant_topology.py`
- `runtime/executor_birth_intent.py`
- `runtime/executor_birth_legacy_gate.py`
- `runtime/executor_birth_legacy_neutralizer.py`
- `runtime/executor_birth_legacy_retirement.py`
- `runtime/executor_birth_maintenance_units.py`
- `runtime/executor_birth_operational.py`
- `runtime/executor_birth_ownership_authorities.py`
- `runtime/executor_birth_ownership_chain.py`
- `runtime/executor_birth_ownership_preflight.py`
- `runtime/executor_birth_postcondition.py`
- `runtime/executor_birth_prepared_root.py`
- `runtime/executor_birth_reattestation.py`
- `runtime/executor_birth_startup_gate.py`

## Analisi multidimensionale

### Architettura e dipendenze

- Il grafo degli import contiene tre componenti fortemente connesse: otto
  moduli catalogo/ownership, cinque bootstrap/intent/operational e quattro
  preflight/coordinator/startup. Gli import locali nascondono, ma non
  eliminano, le dipendenze circolari.
- Sono importati 337 simboli privati in 177 siti distribuiti su 31 file.
  `birth_authority_provisioner.py` da solo espone 78 dipendenze private.
- `runtime/config.py` scrive e ripara permessi all'import. La transizione
  modifica `os.environ` e `sys.path` prima di importare dinamicamente il
  runtime. Il risultato dipende dall'ordine degli import e dall'identità del
  processo.

### Stato, provenance e recovery

- Non esiste un journal operativo end-to-end. Account, XDG, ACL, sorgente e
  release possono essere mutati prima del primo record `PREPARED` del
  coordinator.
- Il coordinator è una valida autorità crittografica, ma non può sostituire il
  workflow operativo. Oggi convivono il suo stato, `ProvisioningStateV1`, una
  transazione locale di 505 righe e stato effimero sotto `/run`.
- La release può essere pubblicata prima che il `source_id` venga legato alla
  transition edge. Esiste quindi una finestra durevole priva di provenance
  completa.
- L'idempotenza è buona in alcuni record locali, ma non è dimostrata per la
  transizione completa. Alcuni callback mutanti vengono richiamati una seconda
  volta come “second reading”, confidando in idempotenza implicita.

### Concorrenza e lock

- `complete_transition_cutover_v2` rilascia il maintenance/catalog guard prima
  della convergenza di 122 contratti e lo riacquisisce dopo. Il catalogo live
  può quindi essere osservato parzialmente aggiornato.
- Il test di orchestrazione verifica questo ordine usando mock; i tre test
  della convergenza non coprono crash, lock reali o concorrenza.
- La soluzione target è staging completo e attivazione atomica di una catalog
  generation sotto un guard breve e continuo.

### Identità, account e permessi

La stessa identità è ricostruita in almeno cinque forme incompatibili:

- `_ServiceAccountV1` in `executor_birth_source_receiver.py`;
- `DeploymentDescriptorV1` in `executor_birth_distribution_assembler.py`;
- `_LegacyServiceIdentityV2` in `birth_authority_provisioner.py`;
- `_service_environment_v1` in `executor_birth_transition.py`;
- `_SourceCompileContextV1` in `executor_birth_service_catalog.py`.

Il provisioning dichiarato come unico non crea account, layout XDG o ACL; lo
faceva lo script. Root, servizio candidato e legacy devono essere identità
distinte, tipizzate e fotografate una volta. Il cambio euid/egid nel processo
root va sostituito da un figlio con privilegi abbassati irreversibilmente.

### Preflight ed errori

- `check-all` pubblica oggi un'attestazione: lock, create, chown, chmod, write,
  fsync, link e unlink. Acquisizione, valutazione pura, attestazione,
  pubblicazione e launcher sono responsabilità differenti.
- I CLI catturano `BaseException`; la convergenza comprime cause diverse nel
  solo `birth_transition_contract_convergence_failed`. Occorrono errori
  tipizzati con fase, codice, dettaglio redatto e retry disposition.

### Duplicazione misurata

- 20 implementazioni `_canonical`, 19 `_digest`, 12 `_pairs`, nove `_invalid`
  e sette `_fail`.
- `executor_birth_admin_preflight.py` e `contract_boundary_guard.py`
  contengono 61 funzioni con corpo AST identico: 1.727 righe replicate per
  copia. Le tre maggiori sono `_analyse_scope` (486 righe), `check` (207) e
  `birth_closed_findings` (114).
- Snapshot del distribution tree, verifica della import closure, decoder di
  successor claim/disposition e parser del catalogo sono duplicati in owner
  differenti.
- Le implementazioni di serializzazione ASCII e UTF-8 non sono sempre
  semanticamente equivalenti. La deduplicazione sarà guidata da profili e
  golden vector, non da sostituzioni nominali.

## Architettura target

Dipendenze ammesse:

`primitives -> domain -> ports -> adapters -> application -> composition/CLI`

Bounded context:

1. Host Identity & Provisioning;
2. Service Topology;
3. Release & Distribution;
4. Contract Catalog Migration;
5. Transition Workflow;
6. Ownership & Crypto;
7. Preflight & Startup;
8. Legacy Retirement;
9. Secure Persistence e Contract Boundary Analysis.

La macchina a stati persistente minima è:

`NEW -> PLANNED -> HOST_PROVISIONED -> STATE_SNAPSHOT_PREPARED ->`
`LEGACY_QUIESCED -> STATE_SYNCED -> SOURCE_RECEIVED -> RELEASE_INSTALLED ->`
`AUTHORITIES_READY -> CONTRACTS_CONVERGING -> CONTRACTS_CONVERGED ->`
`OWNERSHIP_PREPARED -> TOPOLOGY_RETIRING -> TOPOLOGY_INSTALLED ->`
`PREFLIGHT_EVALUATED -> PREFLIGHT_PUBLISHED -> TARGET_ACTIVATED ->`
`HEALTH_VERIFIED -> COMPLETED`.

Ogni effetto segue `intent+fsync -> mutate -> observe -> result+fsync`, usa una
idempotency key e registra digest di input/output. Prima del cutover è ammesso
`ABORTED_PRE_CUTOVER`; dopo il confine si entra in `RECOVERY_REQUIRED` e si
riprende in avanti. `/run` non è mai autorità.

## Helper condivisi e proprietari

Non viene introdotto alcun `utils.py`. Ogni helper ha un owner semantico,
un'API minima e contract test propri:

| Owner | Responsabilità condivisa |
|---|---|
| `executor_birth_account_identity` | account snapshot, confronto identità, layout XDG |
| `executor_birth_host_provisioning` | ensure account/layout/owner/mode/ACL |
| `executor_birth_topology_*` | source, modello, codec, compiler e query unità |
| `executor_birth_canonical` | profili canonici ASCII/UTF-8 e decode limitato |
| `executor_birth_crypto_framing` | SHA-256 ID e hash con domain framing |
| `contract_boundary_analysis` | analyzer AST e policy boundary pure |
| `executor_birth_posix_store` | no-follow, identity, fsync e publish-no-replace |
| `executor_birth_systemd_client` | osservazione e mutazione systemd tipizzata |
| `executor_birth_transition_journal` | append/CAS/read/resume del workflow |
| `executor_birth_errors` | errori pubblici tipizzati e traduzione ai CLI |

Il preflight amministrativo deve restare eseguibile con `python -I -S`. Il suo
file standalone diventa quindi un artefatto generato e verificato byte per
byte dagli stessi sorgenti modulari, non un secondo owner copiato a mano.

## Migrazione area per area, funzione per funzione

1. **Identity**: `_service_environment_v1`,
   `_service_account_snapshot_v1` e
   `_resolve_legacy_service_identity_v2` confluiscono nel modello account e
   layout condiviso. Primo passo senza modifica di comportamento.
2. **Provisioning**: `open_birth_provisioning_layout_v1` viene sostituito da
   `inspect/plan/apply/verify`; solo `apply` richiede root.
3. **Boundary analysis**: estrazione delle 61 copie esatte, poi generazione del
   preflight standalone e differential test sui byte/risultati.
4. **Preflight**: `_attest_operational_preflight_v1` viene diviso in capture ed
   evaluator puro; `_publish_preflight_attestation_core_v1` resta adapter
   esplicito; launcher in CLI separato.
5. **Contract convergence**: `_source_generation_has_historical_receipt`,
   `_candidate_for_transition` e `converge` diventano planner puro, staging per
   item, verifier e activation atomica.
6. **Topology/systemd**: `_install_bound_topology_v2` viene diviso in plan,
   install, reload e verify; nessuna unità è elencata fuori dal catalogo.
7. **Workflow**: `_TransactionJournalV1` diventa repository CAS; le private
   `_reserve_transition_edge_*` e `_cross_*` diventano eventi pubblici.
8. **Orchestrazione**: `complete_transition_cutover_v2` viene sostituita da
   `TransitionService.resume(transition_id)`; `executor_birth_transition.py`
   resta temporaneamente facade CLI.
9. **Secure filesystem**: `_SecureRootSession` viene separata in policy,
   adapter POSIX, adapter Windows e sessione; eliminazione delle primitive
   duplicate solo dopo parity.
10. **Rimozione**: facade, maintenance projection, probe e sonde vengono
    cancellati solo dopo due cicli verdi e sostituzione con test mantenuti.

### Valutazione del worktree `recovery`

Il worktree preservato contiene un tentativo utile di centralizzare letture e
lock di approval authority, keystore e semantic authority. Sul suo commit i
test mirati sono verdi, ma il port diretto è stato rifiutato:

- usa una versione precedente di `executor_birth_secure_fs.py` (2.294 righe
  contro 5.166) e il vecchio booleano `exact_private` invece del role catalog;
- rimuove loader autorevoli già consumati da provisioner, prepared root e
  prepared set;
- riapre alcuni path dopo l'autenticazione, perdendo la capability binding;
- i nuovi test costruiscono descriptor e token privati incompatibili col
  modello attivo.

La riduzione netta di 429 righe resta un obiettivo, non una patch da copiare.
Prima del port vanno introdotte porte pubbliche `load_*_from_birth_session`,
un `SecureReadPort`, una capability `LockedBirthSession` e snapshot di
inventario immutabili. Il ruolo pubblico/confidenziale proviene sempre dal
catalogo firmato; non sarà selezionato dal consumer con un booleano.

## Guardrail e criteri di accettazione

- modulo produttivo nuovo o rifatto: massimo 400 righe;
- classe massimo 250 righe;
- funzione ordinaria massimo 40 righe, orchestration massimo 60;
- complessità ciclomatica massima 10;
- zero import cross-module di simboli privati nel nuovo package;
- zero scritture, subprocess, `os.environ`, `sys.path` o cwd all'import;
- zero SCC nel nuovo package;
- type checking strict sul nuovo package;
- test di purezza import, golden byte, dependency layering e preflight no-write;
- replica Linux reale con UID root/service/legacy distinti, ACL, xattr e
  systemd; crash injection dopo ogni write/rename/fsync/append e ripresa;
- convergenza sul catalogo reale, senza monkeypatch dello store;
- seconda esecuzione completamente no-op e digest finale identico.

## Prima tranche implementata: identità POSIX e layout XDG

È stato introdotto `runtime/executor_birth_account_identity.py` come owner
semantico unico, senza I/O all'import e senza dipendenze dal dominio di
attivazione. Il modulo contiene 176 righe; la funzione più lunga ne contiene
17. Espone:

- grammatica canonica del nome account;
- record e snapshot immutabili di account e gruppi supplementari;
- errori di risoluzione tipizzati per piattaforma, account e gruppi;
- confronto esplicito `assert_unchanged` fra due osservazioni NSS;
- layout XDG Metnos legato per costruzione a un solo record account.

Sono stati migrati, conservando i codici d'errore pubblici:

- ambiente e verifica dell'identità nel child di
  `install/executor_birth_transition.py`;
- snapshot account, gruppi, home canonica e target della shell in
  `install/executor_birth_source_receiver.py`;
- identità legacy e ambiente della convergenza in
  `install/birth_authority_provisioner.py`;
- validazione del nome account in distribution assembler e service catalog.

Il preflight standalone mantiene per ora la propria regex perché deve
funzionare con `python -I -S`; un contract test ne prova la parità con l'owner.
La copia verrà sostituita dall'artefatto generato nella tranche boundary, non
da un import che indebolisca l'isolamento.

I test nuovi coprono purezza dell'import, limiti dimensionali, happy path,
API POSIX assente o non callable, snapshot modificato, binding del layout,
cambio del target della shell e la matrice completa dei tre adapter per le tre
classi di errore. Le suite mirate hanno prodotto, progressivamente, 214, 293 e
134 test superati senza regressioni.

### Confronto A/B della suite RM-0008/Birth

La prima esecuzione ampia sul worktree rifatto ha prodotto `2187 passed`,
`34 skipped`, `1 deselected` e 13 errori. La classificazione non è stata fatta
per supposizione:

- cinque prove richiedono `sudo chown` e sono impedite dal profilo di sandbox
  `no new privileges`;
- gli altri otto identici test sono stati eseguiti nel worktree detached e
  immutato del commit di partenza `bef2c77f`: falliscono tutti anche lì;
- nessuno dei tredici errori è quindi una regressione della tranche identity.

Uno degli otto errori ha rivelato un inventario Python già obsoleto rispetto
all'indice Git: mancavano il modulo di convergenza e il suo test. Dopo aver
registrato nell'indice anche i tre nuovi file della tranche, l'inventario è
stato rigenerato dal generatore autorevole e contiene 1.943 path; il relativo
gate ora è verde. Gli altri sette errori preesistenti restano visibili e non
sono stati mascherati con correzioni estranee alla tranche.

## Seconda tranche: dominio puro del layout host

È stato aggiunto `runtime/executor_birth_host_layout.py`, senza lookup account,
filesystem, subprocess, systemd o effetti all'import. Il modulo descrive:

- l'account di sistema `metnos`, home `/var/lib/metnos-service` e shell
  `/usr/sbin/nologin`;
- il gruppo primario `metnos` e l'assenza esplicita di gruppi supplementari;
- i parent strutturali `root:root 0755`;
- la capsula bootstrap permanente `/var/lib/metnos-host-provisioning-v1`,
  `root:root 0700`, separata dalla radice che deve essere provisionata;
- le foglie XDG e il workspace `service:service 0700`;
- `/var/lib/metnos/executor-birth` come `root:root 0755`;
- assenza di ACL POSIX access/default, osservazioni complete, conflitti e
  piano di convergenza parent-first;
- verifica tipizzata e secondo piano interamente no-op.

I path data/state/config/cache/workspace non sono duplicati: vengono derivati
dall'owner `executor_birth_account_identity.metnos_xdg_layout_v1`. Il modulo
contiene 395 righe e la funzione più lunga 24; 44 test mirati sono verdi.
Questa tranche è intenzionalmente solo dominio: non crea ancora account o
directory e non viene invocata dalla transizione. Il prossimo adapter mutante
dovrà consumare questo piano sotto journal, non ricostruire policy proprie.

## Terza tranche: owner condiviso dei metadati POSIX

È stato aggiunto `runtime/executor_birth_posix_metadata.py`, 98 righe, come
owner read-only di:

- `PosixObjectKeyV1` per device/inode;
- `PosixStatSnapshotV1` per la fotografia completa a nove campi;
- `PosixStableMetadataV1` per il confronto che esclude solo i timestamp;
- `snapshot_stat_v1` e `snapshot_fd_v1`.

Sono stati migrati source receiver, distribution release, systemd, startup
gate e startup prerequisite. Nessun consumer esterno importa più le private
`_identity` e `_stable_identity` dal receiver; le facade restano soltanto per
una rimozione compatibile successiva. Scrittura, apertura delle catene e
rename non sono stati impropriamente accorpati in questa tranche read-only.
La suite mirata della tranche ha prodotto 78 test superati, tre saltati e uno
deselezionato soltanto perché richiedeva il successivo repin.

## Baseline per la deduplicazione boundary

Prima di estrarre le 53 definizioni top-level e le 32 tabelle duplicate fra
guard e preflight standalone è stato aggiunto un characterization contract.
Congela modelli serializzati, 19 gruppi di policy, otto limiti, facts e
findings ordinati su un corpus rappresentativo, oltre a normalizzazione e hash
della source review. Sono verdi 30 test nuovi e 63 prove storiche di parità.

La decisione resta: `contract_boundary_policy` e
`contract_boundary_analysis` saranno gli owner autoriali; il preflight
`python -I -S` conterrà una proiezione generata deterministicamente e
verificata, non un import del runtime non ancora fidato.

## Quarta tranche: protocollo puro di provisioning host

Il workflow iniziale ha ora una grammatica persistente pura, ma non ancora un
adapter di storage o un mutatore. Il journal non vive sotto la radice che deve
creare: la sua sede prevista è la capsula bootstrap indipendente descritta dal
layout host. La FSM chiusa è:

`PLANNED/ENSURE_ACCOUNT -> ACCOUNT_READY/ENSURE_LAYOUT ->`
`LAYOUT_READY/VERIFY_HOST -> HOST_VERIFIED`.

Ogni record contiene sequence, hash del predecessore e digest immutabili di
request, policy, account, layout e osservazione. Le funzioni pubbliche creano
soltanto la transizione successiva ammessa; non esiste un `append` generico.
JSON non canonico, chiavi duplicate, campi extra, booleani usati come interi,
record oltre 64 KiB, salti di stato, tampering e drift delle evidenze vengono
rifiutati fail-closed.

Per non introdurre nuove copie tecniche, il journal usa tre owner piccoli:

- `executor_birth_canonical.py`: profilo JSON ASCII canonico e decoder chiuso,
  57 righe;
- `executor_birth_crypto_framing.py`: framing length-delimited e SHA-256,
  37 righe;
- `executor_birth_host_provisioning_evidence.py`: digest derivati soltanto dai
  tipi canonici identity/layout, 160 righe.

`executor_birth_host_provisioning_journal.py` contiene 362 righe; la funzione
massima dei quattro moduli è 26 righe. I 22 test dedicati includono golden
bytes/digest e prove negative della catena. Nessun modulo esegue I/O, lookup o
mutazioni.

## Quinta tranche: capability POSIX read-only condivisa

È stata estratta una porta pubblica e policy-neutral per osservare directory
reali senza riusare private di `secure_fs` o del source receiver:

- `executor_birth_posix_directory.py`, 300 righe: walk assoluto componente per
  componente con descriptor, `O_NOFOLLOW`, binding a PID/device/inode e
  verifica della catena prima e dopo l'osservazione;
- `executor_birth_posix_acl.py`, 118 righe: decoder fail-closed degli xattr ACL
  access/default;
- `executor_birth_posix_directory_model.py`, 68 righe: errori e osservazioni
  tipizzate che rifiutano stati impossibili.

La capability non è copiabile o serializzabile, chiude i descriptor anche sui
fallimenti, rifiuta symlink e sostituzioni inode e traduce in una tassonomia
chiusa le syscall assenti. È rigorosamente read-only: nessun mkdir, owner,
mode, ACL mutation, subprocess o lock. I 41 test del sottosistema, inclusi
quelli del precedente owner metadata, sono verdi su descriptor reali; le prove
ACL saltano esplicitamente solo quando il filesystem non le supporta.

## Piano esatto della deduplicazione boundary

La seconda analisi AST distingue 61 funzioni/method body corrispondenti tra
guard e preflight; le 53 definizioni top-level identiche valgono 1.729 righe
per copia. Sono inoltre presenti 32 assegnazioni AST identiche (505 righe) e
48 policy equivalenti considerando alias e rappresentazioni.

L'ordine di estrazione stabilito è:

1. `contract_boundary_policy` come unica autorità di tabelle, limiti e regex;
2. proiezione ASCII deterministica e verificata nel preflight `python -I -S`;
3. owner puro di normalizzazione/source-review;
4. modelli e primitive AST;
5. risoluzione import/dynamic boundary;
6. literal, path e taint;
7. collector e alias;
8. scomposizione di `_analyse_scope` (486 righe) per costrutto;
9. scanner puro `relative path + bytes` con port I/O distinti;
10. scomposizione di `check` (207) e `birth_closed_findings` (114).

La proiezione standalone resterà fisicamente tracciata perché fa parte del
TCB isolato, ma non sarà più una seconda sorgente autoriale. Il gate confronterà
i byte generati e gli output differenziali, non l'identità accidentale dei
corpi AST.

## Verifica integrata dopo le tre tranche

I pin sono stati rigenerati due volte con output e byte identici:

- private: 706 file,
  `sha256:f1900556f31560c425c3a14fb49d34bf03f9093444b368b26e57e7c5e83a3354`;
- public: 694 file,
  `sha256:bef30a1caa9d12ee89dc79df69049553a03a9be9671be5db0b97c2a86ae67de2`.

L'inventario Python rigenerato contiene ora 1.948 path. La suite integrata
mirata ha prodotto 620 test superati, tre saltati, uno deselezionato e un solo
errore già riprodotto sul commit iniziale. La suite completa del perimetro
RM-0008/Birth ha prodotto:

- `2255 passed`;
- `34 skipped`;
- 13 errori, tutti classificati;
- la controprova con i soli 13 node-id nominativi esclusi ha prodotto
  `2255 passed, 34 skipped, 13 deselected`.

Cinque errori sono prove UID/ownership che il sandbox non può eseguire perché
`sudo` è bloccato da `no new privileges`. Gli altri otto falliscono anche sul
commit immutato `bef2c77f`:

1. bootstrap iniziale eseguito con closed enforcement già attivo;
2. entrypoint `executor_birth_contract_convergence.py` assente dal catalogo;
3. snapshot boundary fermo a 126 owner contro i 127 compilati;
4. fixture V2 priva dell'identità servizio ora richiesta dal prodotto;
5. tre aspettative legacy-retirement non allineate alla topologia corrente;
6. cutover guard che non rifiuta un'unità richiesta `not-found`.

La classificazione è una baseline esplicita, non una deroga: questi otto
difetti devono essere trattati nelle rispettive aree e i cinque test privilegi
devono essere eseguiti nella replica Linux reale.

## Verifica integrata dopo cinque tranche

Il nuovo checkpoint ha rigenerato due volte i pin con output e byte identici:

- private: 713 file,
  `sha256:bf9831ae8fc0a821e83cd94e14fb9806bd2bfc87f03fa079085adfabc4deaeca`;
- public: 701 file,
  `sha256:9fcc63edb8caabc4d46d321ae182588a9b7bff86d9a1469ee0ff0a904980aa13`.

L'inventario autorevole contiene 1.957 path. I test integrati dei nuovi owner
hanno prodotto `107 passed`; la suite mirata dei consumer e dei gate ha
prodotto `487 passed, 3 skipped` più il solo snapshot 126/127 già presente
nella baseline.

La corsa ampia è stata deliberatamente estesa a 130 file. Il risultato è
`2435 passed, 34 skipped, 15 failed`. Ottantaquattro pass e due failure
appartengono al test aggregatore dei connettori, fuori dal perimetro RM-0008:
`consult_frontier` e `find_places` falliscono per il comportamento delle loro
fixture/provider e non importano i moduli modificati. Tolto quel file, la
stessa corsa contiene quindi `2351 passed, 34 skipped, 13 failed`.

I tredici failure del perimetro coincidono per node-id con la baseline: cinque
richiedono `sudo chown` e sono bloccati da `no new privileges`; gli altri otto
sono i difetti già riprodotti sul commit iniziale e descritti sopra. Non emerge
alcuna regressione della quarta o quinta tranche.

## Decisione

Le revisioni indipendenti di architettura, software engineering e Python
convergono sulla stessa decisione:

1. nessun'altra patch allo script operativo e nessun nuovo tentativo in
   produzione;
2. characterization test prima di ogni estrazione;
3. migrazione consumer per consumer, con dual-read comparativo soltanto e mai
   dual-write;
4. replica reale completa prima di qualsiasi nuova proposta di esercizio;
5. identità, layout, journal puro e porta POSIX read-only sono completati; la
   prossima area è policy/proiezione boundary, poi analyzer puro.
