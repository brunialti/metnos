# Follow-up review Composer/Engine/Remote — 2026-07-04

**Input:** `internal/reports/composer_engine_remote_review_actions_2026-07-04.md`  
**Scopo:** rispondere alle osservazioni dopo i fix, riesaminare codice/test/doc
con effort alto, e produrre un handoff operativo per un agente.

## Sintesi

Le azioni fatte dopo la review sono sostanzialmente corrette. Lo stato corrente
e' adatto all'uso W3.3/read-only: `get_files`, `compute_files_loc` e `list_dirs`
possono essere instradati verso un device Windows quando la chat nomina il PC,
il client ha lock single-instance, spool dei result, heartbeat separato e Job
Object Windows. Non ho trovato regressioni bloccanti nella suite.

Restano pero' alcune correzioni da chiudere prima di considerare il sistema
"manifest-driven puro", multi-utente o pronto per executor remoti mutanti:

1. `DEVICE_ELIGIBLE` e' ancora un fallback attivo: accettabile come compat-shim,
   ma non e' ancora single source of truth.
2. `purge_invocations` e' implementata ma non testata e usa solo
   `delivered_epoch`, con un edge case sui terminali senza claim.
3. L'isolamento owner e' corretto nel caso host attuale, ma si basa ancora su
   `actor == owner_user_id`; prima dei guest serve una semantica owner centrale.
4. `internal/design/remote-executors.html` resta il documento piu' rischioso:
   contiene molte sezioni storiche in mezzo al testo normativo.
5. Remote mutanti: ancora non pronti. Servono policy exactly-once, sandbox tier e
   UX di conferma/audit.

## Risposta alle osservazioni del report azioni

### F1 — `list_dirs` Windows

Confermo fix corretto. `executors/list_dirs/manifest.toml` dichiara ora
`platforms = ["linux", "windows"]` e `[placement] device_ok = true`. Il codice
di `list_dirs.py` e' stdlib puro, quindi la dichiarazione e' ragionevole.

Proposta: aggiungere un E2E Windows reale per `list_dirs` quando il client si
reinstalla/scarica lo shim aggiornato. Il guard manifest basta contro la
regressione di placement, non prova il round-trip completo su Windows.

### F2 — manifest-driven device eligibility

Fix parziale e buono, ma la formulazione "single source of truth = catalogo"
e' prematura. In `runtime/agent_runtime.py`, `_device_ok` e' vero se:

```python
bool(_plc.get("device_ok")) or executor.name in DEVICE_ELIGIBLE
```

Quindi un executor nella whitelist hardcoded continuerebbe a essere eleggibile
anche se perdesse `device_ok`. Il test `DeviceEligibleManifestTests` impedisce
il caso inverso per gli executor gia' noti, ma continua a dipendere dal set.

Proposta per agente:

1. introdurre helper unico, per esempio `target_device.executor_device_ok(ex)`;
2. farlo leggere solo da manifest, con log/deprecation se il vecchio set viene
   usato;
3. aggiungere test "manifest device_ok=true ma nome non in DEVICE_ELIGIBLE ->
   viene instradato";
4. poi rimuovere `DEVICE_ELIGIBLE` o mantenerlo solo come costante di test
   storica non usata dal runtime.

### F3 — owner filter in `invoke_executor`

Confermo fix corretto per lo stato attuale. `invoke_executor` filtra i device
con `owner_user_id == (actor or "host")` prima di `choose_placement`, quindi uno
scope device-only non vede piu' tutti i device globali.

Rischio futuro: nel modello utenti esistono id tecnici e nomi/display name.
Prima del multi-utente reale, non fare affidamento sul fatto che `actor` sia
sempre identico a `owner_user_id`. Serve un resolver centrale
`current_owner_id(actor, channel)` o equivalente, condiviso tra `run_turn`,
admin UI e pairing.

### F5 — purge invocations

Confermo che la tabella non e' piu' append-only: `purge_invocations()` esiste ed
e' chiamata dal `state_reaper` con `METNOS_INVOCATIONS_RETENTION_DAYS`.

Due miglioramenti necessari:

1. manca un test dedicato in `runtime/tests/test_invocations.py`;
2. la delete usa `delivered_epoch IS NOT NULL AND delivered_epoch < cutoff`.
   In flusso normale un result arriva dopo claim, quindi va bene. Ma un record
   terminale senza `delivered_epoch` non verra' mai purgato. Meglio aggiungere
   `completed_epoch` oppure purgare terminali vecchi con fallback su
   `created_at`/`completed_at`.

Test da aggiungere:

- done/failed vecchi con `delivered_epoch` vecchio vengono rimossi;
- queued/delivered vecchi non vengono rimossi;
- done/failed recenti restano;
- terminale con `delivered_epoch NULL` ha comportamento esplicito e testato.

### D1/D6 — documentazione corretta

Confermo:

- `client-rs/README.md` riflette il client reale 0.2.7, HTTP firmato Ed25519 e
  Job Object Windows;
- `internal/reports/chat_driven_placement_R1_assessment.md` e' marcato come
  storico/aggiornato in testa.

### F4/F6/F7/F8/F9/F10 rimandati

La scelta di rimandarli e' ragionevole, con queste precisazioni:

- F4 va chiuso prima di aprire a guest o a pairing da UI multi-utente.
- F6 e F7 sono prerequisiti duri prima di qualunque write/move/delete remoto.
- F8 va risolto per qualita' UX: se l'utente nomina esplicitamente un PC per un
  executor non impacchettabile, oggi l'operazione puo' girare locale/server senza
  messaggio specifico. Va distinto sticky implicito da target esplicito.
- F9 non e' urgente, ma quando si tocca `engine/dispatch.py` servono snapshot
  test dell'ordine dei guard: la semantica e' corretta ma implicita.
- F10 e' correttamente conservativo: synt/composer resta server-side. Non
  abilitarlo al device senza `placement_context`.

## Nuova review codice

### Remote executor path

Flusso verificato:

1. `agent_runtime.run_turn` risolve il target chat una volta prima di fast path
   ed engine.
2. Il target viene passato sia al fast path sia a `_try_engine_v2`.
3. `_try_engine_v2` passa `placement_target` a `invoke_executor`.
4. `invoke_executor` legge `[placement]`, applica `device_ok`/compat-shim,
   filtra owner, chiama `placement.choose_placement`.
5. Se il placement ritorna un device, `remote_exec.invoke_remote` accoda una
   invocazione firmata e attende il result.
6. Il client verifica `server_sig`, scarica executor/shim, esegue in sandbox,
   scrive result nello spool, poi consegna il result firmato.

Questo percorso e' coerente. Non ho trovato fallback locale silenzioso quando il
placement vincola davvero a un device e il device e' offline/incompatibile.

Punto aperto: il target esplicito su executor non `device_ok` non entra nel
blocco remoto e quindi resta locale/server. Per sticky implicito e' corretto; per
richiesta esplicita e' UX debole.

### Placement

`runtime/placement.py::choose_placement` resta una funzione pura e testabile:
scope any/server torna server, scope device richiede un device disponibile, il
nome esplicito vince salvo scope server, e il gate `platforms` blocca device OS
incompatibili. La correzione F1 rende ora `list_dirs` compatibile con Windows.

Miglioramento: centralizzare il predicato "executor puo' girare sul device"
accanto al loader/manifest, non nel blocco di `agent_runtime`.

### Client Rust

Stato robusto per read-only:

- `proclock.rs`: lock esclusivo `client.lock`, rilasciato dal kernel a morte
  processo;
- `runner.rs`: dedup in-process e pending spool inizializzati dai result non
  consegnati;
- result scritto nello spool prima del POST al server;
- heartbeat su task separato;
- Windows: `sandbox_windows.rs` usa Job Object con kill-on-close, cap memoria,
  cap processi, spawn suspended -> assign -> resume;
- Linux: bwrap se disponibile, fallback `sandbox:"none"` onesto, kill via process
  group.

Limite dichiarato: Windows Job Object e' contenimento risorse/process tree, non
ancora isolamento filesystem/AppContainer. Per i tre executor read-only attuali
e' accettabile. Per mutanti remoti no.

### Engine/composer

`runtime/engine/dispatch.py` e' operativo come engine a quattro layer, anche se i
nomi di codice restano "engine v2/v3". Il routing remote viene innestato fuori
dal dispatcher, nel bridge `agent_runtime`, quindi fast path e engine moderno
ricevono lo stesso `placement_target`.

Non ho trovato bug algoritmico nuovo, ma il file resta ad alto rischio di
manutenzione: molte normalizzazioni e guard sono ordinate semanticamente. Se un
agente deve modificarlo, prima deve aggiungere snapshot/contract test sull'ordine
dei passaggi principali.

`prompt_loader.compose()` e' deterministico: sezioni ordinate, fallback lingua
controllato, cache con chiave che include formato sezione. Nessuna incongruenza
nuova trovata.

`synt_multistage.run_stage5(scope=...)` contiene gia' un parametro per prompt
cross-platform, ma nessun caller promuove automaticamente synt a device. Questo
e' il comportamento giusto oggi: ogni executor remoto deve essere promosso
manualmente dopo audit.

## Nuova review test

Verifiche eseguite:

```bash
python3 -m pytest runtime/tests/test_target_device.py runtime/tests/test_placement.py runtime/tests/test_agent_server_remote.py runtime/tests/test_invocations.py runtime/tests/test_admin_device_test_invoke.py -q
# 72 passed

python3 -m pytest runtime/tests/test_engine_v2.py runtime/tests/test_planner_routing_composition.py runtime/tests/test_resume_planner.py runtime/tests/test_engine_seed_uploads.py -q
# 88 passed

cargo test --manifest-path client-rs/Cargo.toml
# 5 passed

python3 -m pytest runtime/tests -q
# 3181 passed, 26 skipped, 360 warnings, 64 subtests passed
```

Warning: i 360 warning sono `aiohttp.NotAppKeyWarning` in test HTTP esistenti.
Non sono legati ai remote executor.

Gap test ancora presenti:

1. manca unit test per `purge_invocations`;
2. manca test runtime "executor manifest `device_ok=true` ma non in
   `DEVICE_ELIGIBLE` viene instradato";
3. manca E2E Windows reale per `list_dirs`;
4. manca test UX/contratto per "target esplicito + executor non device_ok";
5. manca test owner multi-utente end-to-end sul pairing/admin join.

## Nuova review documentazione

Allineata:

- `client-rs/README.md`: descrive 0.2.7, HTTP firmato, moduli reali, Job Object.
- `docs/it/architecture/remote_executors.html` e versione EN: citano 0.2.7 e lo
  stato W3.3.
- `docs/it/architecture/sandbox.html` e versione EN: coerenti con limiti
  dichiarati.
- `chat_driven_placement_R1_assessment.md`: marcato storico.

Da sistemare:

- `internal/design/remote-executors.html` e' ancora una miscela di corrente e
  storico: mTLS, client 0.2.0/0.2.1/0.2.2/0.2.3/0.2.6, W1/W2/W3, sezioni
  "non implementare" e stato corrente. Alcune note storiche sono marcate, ma il
  documento non ha ancora una testa normativa unica e breve.
- Le conte suite nel design interno sono vecchie rispetto alla suite corrente.
- Alcuni commenti runtime sono rimasti storici, per esempio `target_device.py`
  dice ancora TODO manifest-driven anche se `device_ok` e' gia' letto come
  percorso primario con fallback.

Proposta doc:

1. aprire `internal/design/remote-executors.html` con sezione "Stato normativo
   corrente 2026-07-04";
2. dichiarare esplicitamente: client 0.2.7, HTTP firmato, no mTLS corrente,
   Job Object Windows, bwrap/fallback Linux, read-only remote executor set;
3. spostare W1-W3 storico in appendice o marcarlo come storico a blocchi;
4. aggiungere una tabella "gates prima dei mutanti": F4/F6/F7/F8.

## Findings aggiornati per agente

### A1 — Chiudere la migrazione `device_ok` manifest-only

Severita': media, architetturale.  
Stato: non bloccante oggi, ma incoerente con la promessa "catalogo = fonte unica".

Task:

- creare helper unico per eleggibilita' device;
- aggiungere test manifest-only;
- rimuovere o deprecare `DEVICE_ELIGIBLE`;
- aggiornare commenti in `target_device.py` e `agent_runtime.py`.

### A2 — Rendere robusta e testata la purge della coda invocations

Severita': media, resource safety.  
Stato: funzione presente, copertura insufficiente.

Task:

- aggiungere test in `runtime/tests/test_invocations.py`;
- decidere timestamp terminale (`completed_epoch` consigliato);
- preservare sempre queued/delivered;
- documentare retention in public/internal remote docs.

### A3 — Owner identity end-to-end prima del multi-utente

Severita': media ora, alta prima dei guest.  
Stato: host current OK, guest non pronto.

Task:

- definire resolver owner da actor/channel a owner_user_id;
- usarlo in `run_turn`, `invoke_executor`, admin join/token;
- aggiungere test con due owner e due device omonimi.

### A4 — UX target esplicito non packageable

Severita': bassa/media.  
Stato: comportamento conservativo ma poco trasparente.

Task:

- conservare flag `explicit_device_ref` fino a `invoke_executor` o al finalizer;
- se executor non `device_ok` e target era esplicito, mostrare messaggio onesto:
  operazione eseguita sul server perche' non disponibile sul device, oppure
  rifiutare con errore chiaro;
- non cambiare lo sticky implicito: deve continuare a non rompere query come
  `che ore sono`.

### A5 — Bloccare mutanti remoti finche' mancano policy

Severita': alta prima di C7 R3/write remote.  
Stato: non bug per read-only attuale.

Task:

- policy exactly-once/idempotency per mutanti;
- audit/reversibility per side effect;
- sandbox tier minimo richiesto per mutanti;
- su Windows decidere AppContainer/ACL prima di filesystem write remoto;
- su Linux decidere se `sandbox:"none"` e' vietato per mutanti.

### A6 — Storicizzare il design interno

Severita': media documentale.  
Stato: docs pubbliche meglio allineate del design interno.

Task:

- sezione normativa corrente in testa;
- appendice storica W1-W3;
- conte test aggiornate;
- rimuovere ambiguita' mTLS corrente vs futuro;
- descrivere `device_ok` e compat-shim.

## Priorita' consigliata

1. A2: test purge + timestamp terminale. Piccolo, resource-safety reale.
2. A1: manifest-only `device_ok`. Piccolo/medio, pulisce architettura.
3. A4: UX target esplicito. Piccolo, migliora onesta' verso utente.
4. A6: design interno normativo. Medio, riduce errori degli agenti.
5. A3: owner identity. Prima di guest.
6. A5: mutanti remoti. Prima di qualunque executor write/move/delete su device.

## Stato finale

Nessun blocco trovato per continuare con remote executor read-only su Windows in
LAN/overlay. Non consegnerei ancora il design a un agente per abilitare mutanti
remoti: prima chiudere A2, A1, A3/A5 secondo lo scope scelto.
